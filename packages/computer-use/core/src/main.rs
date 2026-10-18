mod global_keyboard;
mod image_reference;
mod input_policy;
mod launch;
mod mcp_cancel_registry;
mod mcp_response;
mod release_recovery;
mod takeover;
use base64::{engine::general_purpose::STANDARD, Engine};
use image_reference::{Cache as ReferenceCache, Key as ReferenceKey, Pixels};
use keyboard::Keyboard;
use pointer::{Button, Pointer};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    cell::RefCell,
    collections::{HashMap, HashSet},
    env, fs,
    io::{self, BufRead, BufReader, Read, Write},
    os::{
        fd::AsRawFd,
        unix::{
            fs::{FileTypeExt, MetadataExt, OpenOptionsExt, PermissionsExt},
            net::{UnixListener, UnixStream},
            process::CommandExt,
        },
    },
    path::{Path, PathBuf},
    process::{Command, Stdio},
    sync::{
        atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering},
        Arc, Mutex,
    },
    thread,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

type R<T> = Result<T, String>;
const MAX_REQUEST: u64 = 262_144;
const MAX_AGE: Duration = Duration::from_secs(60);
const MAX_POINTER_VIEW_DIMENSION: u32 = 1200;
const RESUME_BINDING_REVISION: u32 = 1;
thread_local! {static CURRENT_CANCEL_FLAG: RefCell<Option<Arc<AtomicBool>>> = const { RefCell::new(None) };}
thread_local! {static CURRENT_DEADLINE: RefCell<Option<Instant>> = const { RefCell::new(None) };}
fn own_canceled() -> bool {
    CURRENT_CANCEL_FLAG.with(|f| {
        f.borrow()
            .as_ref()
            .is_some_and(|v| v.load(Ordering::SeqCst))
    })
}
struct DeadlineGuard(Option<Instant>);
impl DeadlineGuard {
    fn set(deadline: Option<Instant>) -> Self {
        Self(CURRENT_DEADLINE.with(|d| d.replace(deadline)))
    }
}
impl Drop for DeadlineGuard {
    fn drop(&mut self) {
        CURRENT_DEADLINE.with(|d| *d.borrow_mut() = self.0);
    }
}
fn deadline_elapsed() -> bool {
    CURRENT_DEADLINE.with(|d| d.borrow().is_some_and(|until| Instant::now() >= until))
}
fn remaining_timeout(max: Duration) -> Duration {
    CURRENT_DEADLINE.with(|d| {
        d.borrow()
            .map(|until| max.min(until.saturating_duration_since(Instant::now())))
            .unwrap_or(max)
    })
}

#[derive(Clone)]
struct Observation {
    id: String,
    at: Instant,
    epoch: u64,
    input_generation: u64,
    output: String,
    output_geometry: Value,
    focused_window: Option<Value>,
    windows: Value,
    focus_output: Option<String>,
    image_width: u32,
    image_height: u32,
    image: PathBuf,
    view: Crop,
    view_image: PathBuf,
}
#[derive(Clone, Deserialize, Serialize)]
struct Crop {
    x: u32,
    y: u32,
    width: u32,
    height: u32,
}
#[derive(Clone)]
struct SemanticTarget {
    background: bool,
    at: Instant,
    epoch: u64,
    input_generation: u64,
    window: Value,
    snapshot: Arc<atspi::Snapshot>,
    object: atspi::Object,
}
#[derive(Clone)]
struct TaskScope {
    // Compatibility deadline for callers that have not attached a live helper.
    expires: Instant,
    owner_processes: Option<[ScopeProcess; 2]>,
    epoch: u64,
    scope_token: Option<String>,
}

#[derive(Clone, Debug, PartialEq, Eq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct ScopeProcess {
    pid: u32,
    start_ticks: u64,
}
impl ScopeProcess {
    fn is_live(&self) -> bool {
        let process = PathBuf::from(format!("/proc/{}", self.pid));
        if self.pid == 0 || !fs::metadata(&process).is_ok_and(|meta| meta.uid() == uid()) {
            return false;
        }
        let Ok(raw) = fs::read_to_string(process.join("stat")) else { return false };
        let Some((_, fields)) = raw.rsplit_once(") ") else { return false };
        let fields: Vec<_> = fields.split_whitespace().collect();
        !matches!(fields.first().copied(), None | Some("Z" | "X" | "x"))
            && fields.get(19).and_then(|ticks| ticks.parse::<u64>().ok()) == Some(self.start_ticks)
    }
}
impl TaskScope {
    fn is_live(&self) -> bool {
        match &self.owner_processes {
            Some(processes) => processes.iter().all(ScopeProcess::is_live),
            None => self.expires > Instant::now(),
        }
    }
}

struct State {
    started: Instant,
    session_id: String,
    epoch: AtomicU64,
    serial: AtomicU64,
    queued: AtomicUsize,
    takeover: AtomicBool,
    takeover_marker: Mutex<takeover::Latch>,
    last_human_ms: AtomicU64,
    input_policy: input_policy::Policy,
    human_monitor: Mutex<Value>,
    release_confirmed: AtomicBool,
    capture_available: AtomicBool,
    capture_attempts: AtomicU64,
    capture_failure: Mutex<Option<String>>,
    active: Mutex<Value>,
    // One explicit Computer Use task owns this scope across model-thinking gaps.
    // The task indicator registers it and removes it on every normal shutdown;
    // a fixed lifetime bound only expires stale ownership and never resumes it.
    task_scopes: Mutex<HashMap<String, TaskScope>>,
    task_scope_ids: Mutex<HashSet<String>>,
    task_scope_gate: Mutex<()>,
    last_result: Mutex<Value>,
    observations: Mutex<HashMap<String, Observation>>,
    reference_cache: ReferenceCache,
    semantic_targets: Mutex<HashMap<String, SemanticTarget>>,
    actor: Mutex<Option<Pointer>>,
    keyboard: Mutex<Option<Keyboard>>,
    global_keyboard: Mutex<Option<global_keyboard::GlobalKeyboard>>,
    image_dir: PathBuf,
    log: Mutex<fs::File>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "snake_case")]
enum TextMethod {
    Auto,
    Keyboard,
    Clipboard,
}
fn default_text_method() -> TextMethod {
    TextMethod::Auto
}
fn text_uses_clipboard(app: &str, text: &str, method: &TextMethod) -> bool {
    match method {
        TextMethod::Clipboard => true,
        TextMethod::Keyboard => false,
        TextMethod::Auto => matches!(app,
            "code" | "Code" | "com.visualstudio.code" | "com.t3tools.T3Code"
            | "com.t3tools.T3CodeNightly" | "com.t3tools.T3Code.Development"
        ) || text.chars().count() > 1000,
    }
}
#[derive(Debug, Deserialize, Default, Clone, Copy, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
enum KeyScope {
    #[default]
    App,
    Compositor,
}
#[derive(Debug, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
enum Action {
    LaunchApp { app: launch::App },
    EditorOpenProject { project: launch::Project },
    EditorOpenFile { project: launch::Project, relative_path: String },
    Focus {
        window_id: u64,
    },
    CloseWindow {
        window_id: u64,
    },
    Move {
        x: f64,
        y: f64,
    },
    Click {
        x: f64,
        y: f64,
        #[serde(default)]
        button: Option<String>,
        #[serde(default)]
        count: Option<u8>,
    },
    Scroll {
        x: f64,
        y: f64,
        #[serde(default)]
        dx: i32,
        #[serde(default)]
        dy: i32,
    },
    Drag {
        x: f64,
        y: f64,
        to_x: f64,
        to_y: f64,
        #[serde(default)]
        duration_ms: Option<u64>,
    },
    Type {
        text: String,
        #[serde(default = "default_text_method")]
        text_method: TextMethod,
    },
    Paste {
        text: String,
        #[serde(default = "default_restore_clipboard")]
        restore_clipboard: bool,
    },
    Key {
        keys: Vec<String>,
        #[serde(default)]
        key_scope: KeyScope,
    },
    SemanticSetValue {
        handle_id: String,
        text: String,
        #[serde(default)]
        expected_text: Option<String>,
    },
    SemanticClick {
        handle_id: String,
        action_name: String,
    },
    SemanticFocus {
        handle_id: String,
    },
    Wait {
        ms: u64,
    },
}
fn default_restore_clipboard() -> bool {
    true
}

impl Action {
    fn name(&self) -> &'static str {
        match self {
            Self::LaunchApp { .. } => "launch_app",
            Self::EditorOpenProject { .. } => "editor_open_project",
            Self::EditorOpenFile { .. } => "editor_open_file",
            Self::Focus { .. } => "focus",
            Self::CloseWindow { .. } => "close_window",
            Self::Move { .. } => "move",
            Self::Click { .. } => "click",
            Self::Scroll { .. } => "scroll",
            Self::Drag { .. } => "drag",
            Self::Type { .. } => "type",
            Self::Paste { .. } => "paste",
            Self::Key { .. } => "key",
            Self::SemanticSetValue { .. } => "semantic_set_value",
            Self::SemanticClick { .. } => "semantic_click",
            Self::SemanticFocus { .. } => "semantic_focus",
            Self::Wait { .. } => "wait",
        }
    }
    fn launch(&self) -> Option<launch::Request<'_>> {
        match self {
            Self::LaunchApp { app } => Some(launch::Request::App(*app)),
            Self::EditorOpenProject { project } => Some(launch::Request::Project(*project)),
            Self::EditorOpenFile { project, relative_path } => Some(launch::Request::File(*project, relative_path)),
            _ => None,
        }
    }
    fn pointer(&self) -> bool {
        matches!(
            self,
            Self::Move { .. } | Self::Click { .. } | Self::Scroll { .. } | Self::Drag { .. }
        )
    }
}

const GLOBAL_ROUTING_REVISION: u32 = 2;
fn close_window_capability() -> Value {
    json!({"request_path_revision":1,"implemented":true,"focused_only":true,"standalone":true,"explicit_window_id":true,"caller_must_prove_task_ownership_and_saved_state":true,"live_compositor_support_attested":false,"request_ack_is_window_closed":false,"fresh_inventory_and_modal_verification_required":true})
}
fn global_keyboard_capability() -> Value {
    json!({"routing_revision":GLOBAL_ROUTING_REVISION,"key_scope":["app","compositor"],"super_implies_compositor":true,"transport":"owned_uinput","device_creation":"only during guarded action preparation","readiness":"authenticated Niri peer has own exact event node open; not proof of configured/UI acceptance","ui_success_requires_verification":true,"fallback":false})
}
#[derive(Deserialize)]
struct GlobalBackendGuard {
    routing_revision: u32,
    session_id: String,
    epoch: u64,
}
#[derive(Deserialize)]
struct ActArgs {
    observation_id: String,
    #[serde(default)]
    window_id: Option<u64>,
    #[serde(default)]
    task_id: Option<String>,
    actions: Vec<Action>,
    // Added by new proxies after a fresh capability check. An old proxy can
    // still send Super safely: this core always routes it to owned uinput.
    #[serde(default)]
    expected_global_backend: Option<GlobalBackendGuard>,
    #[serde(default)]
    timeout_ms: Option<u64>,
    #[serde(default = "default_observe_after")]
    observe_after: bool,
    #[serde(default = "default_observe_after")]
    include_image: bool,
    #[serde(default = "default_settle_ms")]
    settle_ms: u64,
}
fn default_observe_after() -> bool {
    true
}
fn default_settle_ms() -> u64 {
    80
}

fn uid() -> u32 {
    fs::read_to_string("/proc/self/status")
        .ok()
        .and_then(|s| {
            s.lines()
                .find(|l| l.starts_with("Uid:"))
                .and_then(|l| l.split_whitespace().nth(2))
                .and_then(|n| n.parse().ok())
        })
        .unwrap_or(1000)
}

fn private_dir(path: &Path) -> R<()> {
    if path.exists() {
        let m = fs::symlink_metadata(path).map_err(|e| e.to_string())?;
        if !m.is_dir() || m.file_type().is_symlink() || m.uid() != uid() {
            return Err("Private state directory must be a nonsymlink owned directory".into());
        }
    } else {
        fs::create_dir_all(path).map_err(|e| e.to_string())?;
    }
    fs::set_permissions(path, fs::Permissions::from_mode(0o700)).map_err(|e| e.to_string())
}

fn check_epoch(state: &State, epoch: Option<u64>) -> R<()> {
    if own_canceled() {
        return Err(
            "This client request was canceled or disconnected; its pending actions stopped".into(),
        );
    }
    if deadline_elapsed() {
        return Err("Action batch deadline elapsed; pending dispatch/capture stopped".into());
    }
    if epoch.is_some_and(|e| state.epoch.load(Ordering::SeqCst) != e) {
        Err("Canceled or desktop taken over; queued actions discarded".into())
    } else {
        state.input_policy.check_bound()
    }
}

// Called only while task_scope_gate is held. Indicator-owned task IDs may
// submit multiple batches for the same live scope. Action-only IDs (legacy or
// direct single-batch calls) are tombstoned on first admission so they cannot
// be replayed after a stop or admission of a later task.
fn authorize_action_task(
    state: &State,
    requested: Option<&str>,
    generated: String,
) -> R<(String, bool)> {
    let mut scopes = state.task_scopes.lock().map_err(|_| "Task scopes poisoned")?;
    scopes.retain(|_, scope| scope.is_live());
    if scopes.len() > 1 {
        return Err("Action admission requires one unambiguous live Computer Use task scope".into());
    }
    if let Some((scope_id, scope)) = scopes.iter().next() {
        let task_id = requested.ok_or("desktop_act requires the current indicator task_id while a task scope is active")?;
        if task_id != scope_id || scope.epoch != state.epoch.load(Ordering::SeqCst) {
            return Err("Action task_id does not match the current authorized task scope; canceled or older tasks cannot dispatch".into());
        }
        return Ok((scope_id.clone(), true));
    }

    let task_id = requested.unwrap_or(&generated);
    if task_id.is_empty() || task_id.len() > 128 || task_id.chars().any(char::is_control) {
        return Err("Action task_id must be a nonempty printable identifier of at most 128 bytes".into());
    }
    let mut used = state.task_scope_ids.lock().map_err(|_| "Task scope IDs poisoned")?;
    if !used.insert(task_id.to_string()) {
        return Err("Action task_id was already used or canceled in this backend session; start a new task identity".into());
    }
    Ok((task_id.to_string(), false))
}

fn action_scope_still_live(state: &State, task_id: &str) -> bool {
    state.task_scopes.lock().is_ok_and(|mut scopes| {
        scopes.retain(|_, scope| scope.is_live());
        scopes.get(task_id).is_some_and(|scope| {
            scope.epoch == state.epoch.load(Ordering::SeqCst)
        })
    })
}

fn physical_activity(state: &State, escape: bool, simulation: bool) {
    if !escape {
        let now = state.started.elapsed().as_millis() as u64;
        state.last_human_ms.store(now.max(1), Ordering::SeqCst);
        // Ordinary activity invalidates targets without canceling task ownership.
        state.input_policy.activity();
        state.reference_cache.invalidate();
        return;
    }
    // Serialize Escape with scope admission and queued-to-active handoff. It
    // cancels the current request, never changes the explicit takeover latch.
    let scope_gate = state.task_scope_gate.lock();
    let now = state.started.elapsed().as_millis() as u64;
    state.last_human_ms.store(now.max(1), Ordering::SeqCst);
    state.input_policy.activity();
    state.reference_cache.invalidate();
    if scope_gate.is_err() {
        // Unknown scope state must stop every existing writer. Future admission
        // also refuses this poisoned gate; no lasting Escape marker is needed.
        let epoch = state.epoch.fetch_add(1, Ordering::SeqCst) + 1;
        record(state, "physical_escape_scope_unknown", json!({"epoch":epoch,"task_canceled":true,"takeover_latched":state.takeover.load(Ordering::SeqCst),"error":"Task scope gate poisoned"}));
        return;
    }
    let active_live = state.active.lock().map_or(true, |active| !active.is_null());
    let scope_live = state.task_scopes.lock().map_or(true, |mut scopes| {
        scopes.retain(|_, scope| scope.is_live());
        !scopes.is_empty()
    });
    let task_live = active_live || state.queued.load(Ordering::SeqCst) != 0 || scope_live;
    if !task_live {
        record(state, "physical_escape_idle", json!({"source":if simulation{"controlled_evdev_simulation"}else{"physical_escape"},"takeover_latched":state.takeover.load(Ordering::SeqCst),"task_canceled":false}));
        return;
    }
    // Advance the epoch before cleanup so all running/preparing/queued batches
    // stop at their next boundary and perform their normal owned-input release.
    // Admitted IDs remain tombstoned; an old indicator's end is cleanup only.
    let epoch = state.epoch.fetch_add(1, Ordering::SeqCst) + 1;
    if let Ok(mut scopes) = state.task_scopes.lock() {
        if let Ok(mut used) = state.task_scope_ids.lock() {
            used.extend(scopes.keys().cloned());
        }
        scopes.clear();
    }
    record(state, "physical_escape_cancel", json!({"epoch":epoch,"source":if simulation{"controlled_evdev_simulation"}else{"physical_escape"},"task_canceled":true,"takeover_latched":state.takeover.load(Ordering::SeqCst),"takeover_persisted":false,"fresh_observation_required":true,"old_task_or_queue_resumed":false,"controlled_simulation":simulation}));
}

fn physical_held_controls(file: &fs::File, key_capable: bool) -> Option<usize> {
    if !key_capable {
        return Some(0);
    }
    // Linux EVIOCGKEY(96), bounded KEY_MAX=0x2ff bitset. Kernel snapshot is
    // authoritative after queued events; do not add queued presses to it.
    // Actual key/button identities are discarded immediately after counting.
    let mut mask = [0u8; 96];
    let request = (2u64 << 30) | ((mask.len() as u64) << 16) | (0x45u64 << 8) | 0x18;
    let result = unsafe {
        libc::ioctl(
            file.as_raw_fd(),
            request as libc::c_ulong,
            mask.as_mut_ptr(),
        )
    };
    if result < 0 {
        None
    } else {
        Some(mask.iter().map(|byte| byte.count_ones() as usize).sum())
    }
}

fn physical_event_chunk(
    bytes: &[u8],
    source: input_policy::Source,
    sync_lost: &mut bool,
) -> (bool, bool) {
    let mut activity = false;
    let mut escape = false;
    // Linux x86_64 input_event: timeval16 + type2 + code2 + value4.
    // Classify in bounded memory; no raw user input is retained.
    for ev in bytes.chunks_exact(24) {
        let kind = u16::from_ne_bytes([ev[16], ev[17]]);
        let code = u16::from_ne_bytes([ev[18], ev[19]]);
        let value = i32::from_ne_bytes(ev[20..24].try_into().unwrap());
        if *sync_lost {
            // Ignore queued events after SYN_DROPPED until SYN_REPORT, then
            // recompute current state via EVIOCGKEY rather than event replay.
            if kind == 0 && code == 0 {
                *sync_lost = false;
            }
            continue;
        }
        match input_policy::classify(kind, code, value, source) {
            input_policy::Event::Ignore => {}
            input_policy::Event::Activity => activity = true,
            input_policy::Event::Escape => {
                activity = true;
                escape = true;
            }
            input_policy::Event::Resync => {
                activity = true;
                *sync_lost = true;
            }
        }
    }
    (activity, escape)
}

fn human_monitor(state: Arc<State>) {
    struct Device {
        path: PathBuf,
        file: fs::File,
        simulation: bool,
        key_capable: bool,
        held_controls: Option<usize>,
        sync_lost: bool,
    }
    // Private integration-test hook: only a deliberately created, uniquely named
    // uinput device may exercise the real evdev monitor. Never configured in the
    // production service, and always reported as controlled simulation.
    let test_device = env::var("WEASEL_COMPUTER_USE_TEST_INPUT_DEVICE")
        .ok()
        .filter(|name| {
            name.starts_with("weasel-cua-takeover-test-")
                && name.len() <= 80
                && !name.chars().any(|c| c.is_control())
        });
    let mut devices: Vec<Device> = Vec::new();
    let mut scan_at = Instant::now() - Duration::from_secs(3);
    let mut coverage_known = false;
    let mut denied = 0usize;
    loop {
        if scan_at.elapsed() > Duration::from_secs(2) {
            scan_at = Instant::now();
            denied = 0;
            coverage_known = false;
            if let Ok(entries) = fs::read_dir("/sys/class/input") {
                coverage_known = true;
                for entry in entries.flatten() {
                    let name = entry.file_name().to_string_lossy().into_owned();
                    if !name.starts_with("event") {
                        continue;
                    }
                    let syspath = match (entry.path().join("device")).canonicalize() {
                        Ok(p) => p,
                        Err(_) => {
                            coverage_known = false;
                            continue;
                        }
                    };
                    // Bluetooth UHID devices under virtual/misc are physical user input;
                    // only direct kernel virtual/input devices are synthetic uinput.
                    let virtual_input = syspath.starts_with("/sys/devices/virtual/input");
                    let simulation = virtual_input
                        && test_device.as_ref().is_some_and(|expected| {
                            fs::read_to_string(syspath.join("name"))
                                .is_ok_and(|name| name.trim() == expected)
                        });
                    let source = if simulation {
                        input_policy::Source::ControlledSimulation
                    } else if virtual_input {
                        input_policy::Source::Synthetic
                    } else {
                        input_policy::Source::Physical
                    };
                    if source == input_policy::Source::Synthetic {
                        continue;
                    }
                    let mask = fs::read_to_string(syspath.join("capabilities/ev"))
                        .ok()
                        .and_then(|s| {
                            u64::from_str_radix(
                                s.trim().split_whitespace().last().unwrap_or("0"),
                                16,
                            )
                            .ok()
                        });
                    let Some(mask) = mask else {
                        coverage_known = false;
                        continue;
                    };
                    if mask & ((1 << 1) | (1 << 2) | (1 << 3)) == 0 {
                        continue;
                    }
                    let path = PathBuf::from("/dev/input").join(name);
                    if devices.iter().any(|d| d.path == path) {
                        continue;
                    }
                    match fs::OpenOptions::new()
                        .read(true)
                        .custom_flags(libc::O_NONBLOCK | libc::O_CLOEXEC)
                        .open(&path)
                    {
                        Ok(file) => {
                            let key_capable = mask & (1 << 1) != 0;
                            let held_controls = physical_held_controls(&file, key_capable);
                            devices.push(Device {
                                path,
                                file,
                                simulation,
                                key_capable,
                                held_controls,
                                sync_lost: false,
                            });
                        }
                        Err(_) => denied += 1,
                    }
                }
            }
        }
        let mut remove = Vec::new();
        for (idx, device) in devices.iter_mut().enumerate() {
            let mut buf = [0u8; 24 * 32];
            match device.file.read(&mut buf) {
                Ok(0) => remove.push(idx),
                Ok(n) => {
                    let source = if device.simulation {
                        input_policy::Source::ControlledSimulation
                    } else {
                        input_policy::Source::Physical
                    };
                    let (activity, escape) =
                        physical_event_chunk(&buf[..n], source, &mut device.sync_lost);
                    if activity {
                        physical_activity(&state, escape, device.simulation);
                    }
                }
                Err(e) if e.kind() == io::ErrorKind::WouldBlock => {}
                Err(_) => remove.push(idx),
            }
            // Refresh every bounded poll, including quiet held modifiers/buttons.
            // An ioctl failure is uncertain capability, never evidence of release.
            device.held_controls = if device.sync_lost {
                None
            } else {
                physical_held_controls(&device.file, device.key_capable)
            };
        }
        if !remove.is_empty() {
            coverage_known = false;
            scan_at = Instant::now() - Duration::from_secs(3);
        }
        for idx in remove.into_iter().rev() {
            devices.remove(idx);
        }
        let held_controls = devices.iter().filter_map(|d| d.held_controls).sum();
        let held_state_known = coverage_known
            && denied == 0
            && !devices.is_empty()
            && devices.iter().all(|d| d.held_controls.is_some());
        state
            .input_policy
            .update_holds(held_controls, held_state_known);
        if let Ok(mut status) = state.human_monitor.lock() {
            *status = json!({"available":!devices.is_empty(),"watched_devices":devices.len(),"permission_denied_devices":denied,"held_controls":held_controls,"held_state_known":held_state_known,"held_state_source":"bounded authoritative EVIOCGKEY snapshots per key-capable device; actual bitmasks discarded","event_data_retained":false,"policy":"cooperative_escape_only","escape_abort":"physical EV_KEY KEY_ESC press only; owned synthetic input excluded","ordinary_activity":"invalidates observations; current conflicting batch releases without takeover","source":"physical evdev activity; excludes direct uinput; includes Bluetooth UHID","controlled_simulation_device_enabled":test_device.is_some(),"watched_simulation_devices":devices.iter().filter(|d|d.simulation).count(),"poll_interval_ms":5,"inventory_rescan_ms":2000});
        }
        thread::sleep(Duration::from_millis(5));
    }
}

fn bounded_command(
    state: &State,
    program: &str,
    args: &[String],
    input: Option<&[u8]>,
    timeout: Duration,
    epoch: Option<u64>,
) -> R<Vec<u8>> {
    bounded_command_with_launch(state, program, args, input, timeout, epoch, None)
}

fn bounded_command_with_launch(
    state: &State,
    program: &str,
    args: &[String],
    input: Option<&[u8]>,
    timeout: Duration,
    epoch: Option<u64>,
    mut launch: Option<(&mut dyn FnMut() -> R<()>, &mut launch::Receipt)>,
) -> R<Vec<u8>> {
    check_epoch(state, epoch)?;
    let mut cmd = Command::new(program);
    // wl-copy intentionally forks a persistent selection owner. Its child keeps
    // inherited output FDs, so pipe EOF is not an acknowledgement of the setter.
    // This command has no consumed output; ordinary commands retain drained pipes.
    let background_output = program == "wl-copy" || launch.is_some();
    cmd.args(args)
        .env_remove("NO_AT_BRIDGE")
        .env_remove("ELECTRON_RUN_AS_NODE")
        .process_group(0)
        .stdin(if input.is_some() {
            Stdio::piped()
        } else {
            Stdio::null()
        })
        .stdout(if background_output {
            Stdio::null()
        } else {
            Stdio::piped()
        })
        .stderr(if background_output {
            Stdio::null()
        } else {
            Stdio::piped()
        });
    if launch.is_some() {
        // Fixed removals avoid host/CLI mode injection into the isolated app.
        cmd.env_remove("LD_LIBRARY_PATH").env_remove("ZED_ASKPASS_SOCKET");
    }
    if let Some((gate, _)) = launch.as_mut() {
        gate()?;
    }
    // Command construction or capability checks can wait. Fence the actual
    // spawn, not just entry into this helper.
    check_epoch(state, epoch)?;
    let mut child = cmd
        .spawn()
        .map_err(|e| format!("{program} unavailable: {e}"))?;
    let pid = child.id();
    if let Some((_, receipt)) = launch {
        receipt.commit(pid);
        // Once spawn succeeded, any cancellation/failure is uncertain. Kill
        // only this owned group; never find or kill a shared application PID.
        if let Err(error) = check_epoch(state, epoch) {
            unsafe { libc::kill(-(pid as i32), libc::SIGKILL); }
            let _ = child.kill();
            let _ = child.wait();
            return Err(error);
        }
        // A GUI process has no bounded exit ACK. Return its OS spawn receipt
        // and reap it independently; continued GUI success needs fresh proof.
        thread::spawn(move || { let _ = child.wait(); });
        return Ok(Vec::new());
    }
    let writer = if let Some(bytes) = input {
        let mut p = child.stdin.take().ok_or("Missing child stdin")?;
        let bytes = bytes.to_vec();
        Some(thread::spawn(move || p.write_all(&bytes)))
    } else {
        None
    };
    let stdout = child.stdout.take();
    let stderr = child.stderr.take();
    let out = thread::spawn(move || {
        let mut bytes = Vec::new();
        if let Some(stdout) = stdout {
            stdout.take(32 * 1024 * 1024).read_to_end(&mut bytes)?;
        }
        Ok::<Vec<u8>, io::Error>(bytes)
    });
    let err = thread::spawn(move || {
        let mut bytes = Vec::new();
        if let Some(stderr) = stderr {
            stderr.take(1024 * 1024).read_to_end(&mut bytes)?;
        }
        Ok::<Vec<u8>, io::Error>(bytes)
    });
    let start = Instant::now();
    let mut exit = None;
    let status = loop {
        if let Err(e) = check_epoch(state, epoch) {
            unsafe {
                libc::kill(-(pid as i32), libc::SIGKILL);
            }
            let _ = child.kill();
            let _ = child.wait();
            return Err(e);
        }
        if start.elapsed() > timeout {
            unsafe {
                libc::kill(-(pid as i32), libc::SIGKILL);
            }
            let _ = child.kill();
            let _ = child.wait();
            return Err(format!("{program} timed out"));
        }
        if exit.is_none() {
            exit = child.try_wait().map_err(|e| e.to_string())?;
        }
        if exit.is_some()
            && out.is_finished()
            && err.is_finished()
            && writer.as_ref().is_none_or(|w| w.is_finished())
        {
            break exit.unwrap();
        }
        thread::sleep(Duration::from_millis(5));
    };
    if let Some(writer) = writer {
        writer
            .join()
            .map_err(|_| "Input writer failed")?
            .map_err(|e| format!("{program} stdin failed: {e}"))?;
    }
    let output = out
        .join()
        .map_err(|_| "Output reader failed")?
        .map_err(|e| e.to_string())?;
    let errors = err
        .join()
        .map_err(|_| "Error reader failed")?
        .map_err(|e| e.to_string())?;
    if !status.success() {
        return Err(format!(
            "{program} failed ({status}): {}",
            String::from_utf8_lossy(&errors)
                .chars()
                .take(400)
                .collect::<String>()
        ));
    }
    Ok(output)
}

fn niri(state: &State, kind: &str) -> R<Value> {
    niri_epoch(state, kind, None)
}
fn niri_epoch(state: &State, kind: &str, epoch: Option<u64>) -> R<Value> {
    let out = bounded_command(
        state,
        "niri",
        &["msg".into(), "-j".into(), kind.into()],
        None,
        Duration::from_secs(3),
        epoch,
    )?;
    serde_json::from_slice(&out).map_err(|e| format!("Niri {kind} response invalid: {e}"))
}

fn focused(windows: &Value) -> Option<Value> {
    windows
        .as_array()
        .and_then(|ws| ws.iter().find(|w| w["is_focused"].as_bool() == Some(true)))
        .cloned()
}
fn identity(window: &Value) -> Value {
    json!({"id":window["id"],"pid":window["pid"],"app_id":window["app_id"],"workspace_id":window["workspace_id"],"layout":window["layout"],"is_floating":window["is_floating"]})
}

fn system_monotonic_ms() -> Option<f64> {
    let mut t = libc::timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    if unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut t) } == 0 {
        Some(t.tv_sec as f64 * 1000.0 + t.tv_nsec as f64 / 1_000_000.0)
    } else {
        None
    }
}
fn record(state: &State, event: &str, data: Value) {
    let v = json!({"schema":1,"monotonic_ms":state.started.elapsed().as_secs_f64()*1000.0,"monotonic_system_ms":system_monotonic_ms(),"event":event,"data":data});
    if let Ok(mut file) = state.log.lock() {
        let _ = writeln!(file, "{v}");
        let _ = file.flush();
    }
}

fn capture_unavailable(state: &State, epoch: u64, category: &str) {
    // Scoped cancellation, deadline and cooperative conflict do not demonstrate
    // global capture failure and must not cancel independent pending requests.
    if own_canceled()
        || deadline_elapsed()
        || state.epoch.load(Ordering::SeqCst) != epoch
        || state.input_policy.check_bound().is_err()
    {
        return;
    }
    if let Ok(mut failure) = state.capture_failure.lock() {
        *failure = Some(category.into());
        state.capture_available.store(false, Ordering::SeqCst);
    } else {
        state.capture_available.store(false, Ordering::SeqCst);
    }
    let next = state.epoch.fetch_add(1, Ordering::SeqCst) + 1;
    if let Ok(mut saved) = state.observations.lock() {
        for obs in saved.values() {
            let _ = fs::remove_file(&obs.image);
            let _ = fs::remove_file(&obs.view_image);
        }
        saved.clear();
        state.reference_cache.invalidate();
    }
    if let Ok(mut targets) = state.semantic_targets.lock() {
        targets.clear();
    }
    record(
        state,
        "capture_unavailable",
        json!({"epoch":next,"category":category,"old_observations_invalidated":true,"new_successful_capture_required":true}),
    );
}
fn capture(
    state: &State,
    output: &str,
    path: &Path,
    id: &str,
    timeout: Duration,
    epoch: u64,
) -> R<()> {
    {
        let _lifecycle = state
            .capture_failure
            .lock()
            .map_err(|_| "Capture lifecycle state poisoned")?;
        state.capture_attempts.fetch_add(1, Ordering::SeqCst);
    }
    record(
        state,
        "capture_request",
        json!({"observation_id":id,"output":output}),
    );
    let started = Instant::now();
    if bounded_command(
        state,
        "grim",
        &[
            "-l".into(),
            "1".into(),
            "-s".into(),
            "1".into(),
            "-o".into(),
            output.into(),
            path.to_string_lossy().into(),
        ],
        None,
        timeout,
        Some(epoch),
    )
    .is_err()
    {
        if own_canceled()
            || deadline_elapsed()
            || state.epoch.load(Ordering::SeqCst) != epoch
            || state.input_policy.check_bound().is_err()
        {
            return check_epoch(state, Some(epoch));
        }
        capture_unavailable(state, epoch, "capture_process_failed");
        return Err("Capture backend failed/unavailable; old observations and semantic handles invalidated. No further input allowed until desktop_observe succeeds.".into());
    }
    record(
        state,
        "capture_complete",
        json!({"observation_id":id,"capture_ms":started.elapsed().as_secs_f64()*1000.0}),
    );
    Ok(())
}

fn png_size(path: &Path) -> R<(u32, u32)> {
    let mut f = fs::File::open(path).map_err(|e| e.to_string())?;
    let mut b = [0u8; 24];
    f.read_exact(&mut b).map_err(|e| e.to_string())?;
    if &b[..8] != b"\x89PNG\r\n\x1a\n" {
        return Err("Capture is not PNG".into());
    }
    Ok((
        u32::from_be_bytes(b[16..20].try_into().unwrap()),
        u32::from_be_bytes(b[20..24].try_into().unwrap()),
    ))
}

fn view_point(obs: &Observation, x: f64, y: f64) -> R<()> {
    if !x.is_finite()
        || !y.is_finite()
        || x < 0.0
        || y < 0.0
        || x >= obs.view.width as f64
        || y >= obs.view.height as f64
    {
        return Err("Pointer coordinate outside displayed observation view; no input sent".into());
    }
    Ok(())
}
fn preflight_pointer_view(obs: &Observation) -> R<()> {
    if obs.view.width > MAX_POINTER_VIEW_DIMENSION || obs.view.height > MAX_POINTER_VIEW_DIMENSION {
        return Err(format!(
            "Pointer view is {}x{}; capture a fresh target crop at most {} pixels in each dimension, then use its crop-local coordinates. No input from this batch sent.",
            obs.view.width, obs.view.height, MAX_POINTER_VIEW_DIMENSION
        ));
    }
    Ok(())
}
fn preflight(obs: &Observation, action: &Action) -> R<()> {
    if action.pointer() {
        preflight_pointer_view(obs)?;
    }
    match action {
        Action::Move { x, y } | Action::Click { x, y, .. } | Action::Scroll { x, y, .. } => {
            view_point(obs, *x, *y)?
        }
        Action::Drag {
            x, y, to_x, to_y, ..
        } => {
            view_point(obs, *x, *y)?;
            view_point(obs, *to_x, *to_y)?;
        }
        _ => {}
    }
    if action.launch().is_some() {
        if obs.focus_output.as_deref() != Some(obs.output.as_str()) {
            return Err("Launch requires the observed focused output; no spawn".into());
        }
        if let Action::EditorOpenFile { relative_path, .. } = action {
            launch::relative_path(relative_path)?;
        }
    }
    match action {
        Action::Click{button:b,count,..}=>{button(b)?;if count.is_some_and(|n|n==0||n>3){return Err("Click count must be 1..3; no input sent".into());}},
        Action::Scroll{dx,dy,..} if (*dx==0&&*dy==0)||dx.unsigned_abs()>100||dy.unsigned_abs()>100=>return Err("Scroll needs a nonzero dx/dy within -100..100 wheel steps; no input sent".into()),
        Action::Drag{duration_ms:Some(ms),..} if !(50..=3000).contains(ms)=>return Err("Drag duration_ms must be 50..3000; no input sent".into()),
        Action::Key{keys,key_scope}=>preflight_keys(keys,*key_scope)?,
        Action::SemanticSetValue{handle_id,text,expected_text}=>{if handle_id.is_empty()||handle_id.len()>128||text.len()>65536||expected_text.as_ref().is_some_and(|t|t.len()>65536){return Err("Semantic handle/text exceeds bounded schema; no input sent".into());}},
        Action::SemanticClick{handle_id,action_name}=>{if handle_id.is_empty()||handle_id.len()>128||action_name.is_empty()||action_name.len()>128{return Err("Semantic handle/action name invalid; no input sent".into());}},
        Action::SemanticFocus{handle_id} if handle_id.is_empty()||handle_id.len()>128=>return Err("Semantic focus handle invalid; no input sent".into()),
        Action::Type{text,..}|Action::Paste{text,..} if text.len()>65536=>return Err("Text exceeds 64 KiB; no input sent".into()),
        Action::Type{text,text_method:TextMethod::Keyboard} if text.chars().count()>1000=>return Err("Explicit keyboard typing is limited to 1000 characters for the calibrated 8s budget; use text_method=clipboard/auto for longer input".into()),
        Action::Wait{ms} if *ms>10000=>return Err("Wait exceeds 10 seconds; reobserve instead".into()),
        _=>{},
    }
    Ok(())
}
fn png_pixels(path: &Path) -> R<Pixels> {
    image_reference::read_pixels(path)
}
fn reference_pixels(
    state: &State,
    id: &str,
    image: &Path,
    epoch: u64,
    generation: u64,
) -> R<Arc<Pixels>> {
    state.reference_cache.pixels(
        ReferenceKey {
            session: state.session_id.clone(),
            epoch,
            input_generation: generation,
            observation: id.into(),
            path: image.into(),
        },
        || check_epoch(state, Some(epoch)),
    )
}

fn visual_guard(state: &State, obs: &Observation, action: &Action, epoch: u64) -> R<Value> {
    visual_guard_with_capture(state, obs, action, epoch, |path| {
        capture(
            state,
            &obs.output,
            path,
            &obs.id,
            Duration::from_secs(5),
            epoch,
        )
    })
}
fn visual_guard_with_capture(
    state: &State,
    obs: &Observation,
    action: &Action,
    epoch: u64,
    mut capture_current: impl FnMut(&Path) -> R<()>,
) -> R<Value> {
    preflight(obs, action)?;
    let points = match action {
        Action::Click { x, y, .. } | Action::Scroll { x, y, .. } => vec![(*x, *y)],
        Action::Drag {
            x, y, to_x, to_y, ..
        } => vec![(*x, *y), (*to_x, *to_y)],
        _ => return Ok(json!({"applied":false})),
    };
    let start = Instant::now();
    check_epoch(state, Some(epoch))?;
    let prior = reference_pixels(state, &obs.id, &obs.image, epoch, obs.input_generation).map_err(
        |_| {
            capture_unavailable(state, epoch, "prior_image_unavailable");
            "Prior capture artifact unavailable; fresh observation required".to_string()
        },
    )?;
    let path = state.image_dir.join(format!(
        "guard-{}.png",
        state.serial.fetch_add(1, Ordering::SeqCst)
    ));
    let capture_start = Instant::now();
    capture_current(&path)?;
    let capture_ms = capture_start.elapsed().as_secs_f64() * 1000.0;
    let current = png_pixels(&path);
    let _ = fs::remove_file(&path);
    let current = current.map_err(|_| {
        capture_unavailable(state, epoch, "capture_decode_failed");
        "Capture PNG decode failed; fresh observation required".to_string()
    })?;
    if (prior.width, prior.height, prior.channels)
        != (current.width, current.height, current.channels)
    {
        return Err("Screenshot dimensions/color frame changed; no pointer input sent".into());
    }
    check_epoch(state, Some(epoch))?;
    let mut regions = Vec::new();
    for (x, y) in points {
        let x = x + obs.view.x as f64;
        let y = y + obs.view.y as f64;
        if !x.is_finite()
            || !y.is_finite()
            || x < 0.0
            || y < 0.0
            || x >= prior.width as f64
            || y >= prior.height as f64
        {
            return Err("Pointer target outside observed screenshot; no input sent".into());
        }
        let left = (x.floor() as u32).saturating_sub(16);
        let top = (y.floor() as u32).saturating_sub(16);
        let right = ((x.floor() as u32) + 17).min(prior.width);
        let bottom = ((y.floor() as u32) + 17).min(prior.height);
        let mut changed = 0usize;
        let mut count = 0usize;
        for yy in top..bottom {
            for xx in left..right {
                let idx = ((yy as usize) * (prior.width as usize) + (xx as usize)) * prior.channels;
                // Ignore alpha; tolerate tiny compositor/antialias noise only.
                let rgb = if prior.channels < 3 { 1 } else { 3 };
                if (0..rgb).any(|c| prior.bytes[idx + c].abs_diff(current.bytes[idx + c]) > 12) {
                    changed += 1;
                }
                count += 1;
            }
        }
        let fraction = changed as f64 / (count.max(1) as f64);
        regions.push(json!({"x":left,"y":top,"width":right-left,"height":bottom-top,"changed_fraction":fraction}));
        if fraction > 0.02 {
            record(
                state,
                "stale_visual_target",
                json!({"observation_id":obs.id,"changed_fraction":fraction,"guard_capture_ms":capture_ms,"guard_total_ms":start.elapsed().as_secs_f64()*1000.0}),
            );
            return Err(format!("Visual target changed in fresh 33px region ({:.1}% pixels); no pointer input sent. Observe again and ground the new target.",fraction*100.0));
        }
    }
    let result = json!({"applied":true,"guard_capture_ms":capture_ms,"guard_total_ms":start.elapsed().as_secs_f64()*1000.0,"regions":regions});
    record(
        state,
        "visual_guard",
        json!({"observation_id":obs.id,"capture_ms":capture_ms,"total_ms":start.elapsed().as_secs_f64()*1000.0}),
    );
    Ok(result)
}

fn crop_png(pixels: &Pixels, destination: &Path, crop: &Crop) -> R<()> {
    if crop.width == 0
        || crop.height == 0
        || crop
            .x
            .checked_add(crop.width)
            .is_none_or(|v| v > pixels.width)
        || crop
            .y
            .checked_add(crop.height)
            .is_none_or(|v| v > pixels.height)
    {
        return Err("Crop is outside actual screenshot dimensions".into());
    }
    let mut bytes =
        Vec::with_capacity(crop.width as usize * crop.height as usize * pixels.channels);
    for y in crop.y..crop.y + crop.height {
        let start = (y as usize * pixels.width as usize + crop.x as usize) * pixels.channels;
        bytes
            .extend_from_slice(&pixels.bytes[start..start + crop.width as usize * pixels.channels]);
    }
    let file = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(destination)
        .map_err(|e| e.to_string())?;
    let mut encoder = png::Encoder::new(io::BufWriter::new(file), crop.width, crop.height);
    encoder.set_color(match pixels.channels {
        1 => png::ColorType::Grayscale,
        2 => png::ColorType::GrayscaleAlpha,
        3 => png::ColorType::Rgb,
        _ => png::ColorType::Rgba,
    });
    encoder.set_depth(png::BitDepth::Eight);
    encoder.set_compression(png::Compression::Fast);
    encoder
        .write_header()
        .map_err(|e| e.to_string())?
        .write_image_data(&bytes)
        .map_err(|e| e.to_string())
}

fn text_result(data: Value) -> Value {
    json!({"content":[{"type":"text","text":data.to_string()}],"isError":false})
}
fn error_result(message: String) -> Value {
    json!({"content":[{"type":"text","text":json!({"schema":1,"status":"failed","error":message}).to_string()}],"isError":true})
}

fn observe(state: &State, args: &Value) -> R<Value> {
    observe_inner(state, args, false)
}
fn observation_input_readiness(state: &State, input_generation: u64) -> Value {
    // An ordinary-input collision does not make a captured image unavailable.
    // Readiness is advisory; the saved observation retains its capture-start
    // generation and every action still rechecks generation/focus/input state.
    let ready_at_check = state.input_policy.check(input_generation).is_ok();
    let current_generation = state.input_policy.generation();
    let input_ready = ready_at_check
        && current_generation == input_generation
        && state.input_policy.controls_released();
    let takeover = state.takeover.load(Ordering::SeqCst);
    let action_ready = input_ready && !takeover && state.release_confirmed.load(Ordering::SeqCst);
    json!({"input_ready":input_ready,"action_ready":action_ready,"current_input_generation":current_generation,"input_activity_during_capture":current_generation!=input_generation,"fresh_observation_required_for_input":!action_ready,"takeover_latched":takeover,"note":"Image remains available for reasoning during ordinary activity or held controls. Input readiness is a snapshot, not input permission; desktop_act always freshly revalidates the original generation, released controls, focus and target."})
}
fn observe_inner(state: &State, args: &Value, actor_held: bool) -> R<Value> {
    observe_inner_guarded(state, args, actor_held, false)
}
fn observe_inner_guarded(state: &State, args: &Value, actor_held: bool, allowed_output_only: bool) -> R<Value> {
    let start = Instant::now();
    let epoch = state.epoch.load(Ordering::SeqCst);
    record(state, "observe_request", json!({"epoch":epoch}));
    // Observe shares the actuator lock so images never race a running batch.
    let _actor = if actor_held {
        None
    } else {
        Some(state.actor.lock().map_err(|_| "Actuator lock poisoned")?)
    };
    let input_generation = state.input_policy.generation();
    let windows = niri_epoch(state, "windows", Some(epoch))?;
    let outputs = niri_epoch(state, "outputs", Some(epoch))?;
    let workspaces = niri_epoch(state, "workspaces", Some(epoch))?;
    let focus = focused(&windows);
    let name = args["output"]
        .as_str()
        .map(str::to_owned)
        .or_else(|| {
            workspaces
                .as_array()
                .and_then(|w| w.iter().find(|v| v["is_focused"] == true))
                .and_then(|w| w["output"].as_str())
                .map(str::to_owned)
        })
        .or_else(|| {
            outputs.as_object().and_then(|o| {
                o.iter()
                    .find(|(_, v)| v["logical"].is_object())
                    .map(|(n, _)| n.clone())
            })
        })
        .ok_or("No enabled Niri output")?;
    if allowed_output_only {
        let focused_output = workspaces.as_array().and_then(|ws| ws.iter().find(|w| w["is_focused"] == true))
            .and_then(|w| w["output"].as_str());
        if focused_output != Some(name.as_str()) {
            return Err("Post-launch/focus output changed; no capture, verify the selected output explicitly".into());
        }
    }
    let geometry = outputs[&name]["logical"].clone();
    if !geometry.is_object() {
        return Err("Requested output unavailable or disabled".into());
    }
    let id = format!(
        "obs-{}-{}",
        state.session_id,
        state.serial.fetch_add(1, Ordering::SeqCst)
    );
    let image = state.image_dir.join(format!("{id}.png"));
    let capture_start = Instant::now();
    capture(state, &name, &image, &id, Duration::from_secs(8), epoch)?;
    let capture_ms = capture_start.elapsed().as_secs_f64() * 1000.0;
    let capture_at = Instant::now();
    fs::set_permissions(&image, fs::Permissions::from_mode(0o600)).map_err(|e| e.to_string())?;
    let (width, height) = png_size(&image).map_err(|_| {
        capture_unavailable(state, epoch, "capture_header_failed");
        "Capture PNG header invalid; fresh observation required".to_string()
    })?;
    let view = if args["crop"].is_object() {
        serde_json::from_value::<Crop>(args["crop"].clone())
            .map_err(|e| format!("Invalid crop: {e}"))?
    } else {
        Crop {
            x: 0,
            y: 0,
            width,
            height,
        }
    };
    let view_image = if args["crop"].is_object() {
        let path = state.image_dir.join(format!("{id}-crop.png"));
        let pixels = reference_pixels(state, &id, &image, epoch, input_generation)?;
        crop_png(&pixels, &path, &view)?;
        path
    } else {
        image.clone()
    };
    let data = json!({"schema":1,"observation_id":id,"epoch":epoch,"input_generation":input_generation,"input_policy":state.input_policy.status(state.last_human_ms.load(Ordering::SeqCst)),"global_keyboard":global_keyboard_capability(),"observed_unix_ms":SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_millis(),"monotonic_ms":state.started.elapsed().as_secs_f64()*1000.0,"monotonic_system_ms":system_monotonic_ms(),"expires_after_ms":60000,"desktop":"niri-wayland","focused_window":focus,"windows":windows,"workspaces":workspaces,"outputs":outputs,"capture":{"output":name,"path":image,"mime_type":"image/png","image_width":width,"image_height":height,"scale":1,"coordinate_frame":"output-local screenshot pixels; origin top-left; dimensions are actual PNG dimensions and may differ by rounding from Niri logical size","output_logical":geometry},"capabilities":{"capture":true,"window_focus":true,"window_close_request":close_window_capability(),"desktop_launch":launch::capability(),"pointer":"wlr_virtual_pointer_v2","keyboard":{"shortcuts":"app shortcuts: persistent canonical German evdev Wayland keyboard; keymap refreshed before every chord. key_scope=compositor or chords containing Super/meta/logo use an owned Linux uinput device so Niri compositor bindings can process them; permission/monitor/device-open failures refuse before any batch input. Device creation is a capability side effect; open/write acknowledgement is not UI success","unicode_text":{"auto":"plain clipboard for known Electron app IDs or >1000characters; wtype for shorter text in other apps","keyboard_limit_characters":1000,"max_text_utf8_bytes":65536,"electron_keyboard":"known unreliable due physical DomCode and supplementary Unicode; explicit override requires app-specific proof"}},"clipboard":{"backend":"Rust wlr-data-control helper with an owned user scope","plain_text_restore":"best effort; source ownership checked, no atomic selection CAS; skipped after cancel/takeover","rich_or_nontext_restore":"supported bounded MIME payloads including HTML, COMPOUND_TEXT and original Chromium metadata; no portal handles/password hints","rich_preserve_request":"snapshot same offer before replacement; unknown/oversized/sensitive formats refuse","ignored_transport_mimes":["SAVE_TARGETS","GTK_TEXT_BUFFER_CONTENTS"],"holder_lifetime":"separate user scope; source replacement or graphical session shutdown","potential_change_reported_on_failure":true},"semantic_tree":"desktop_semantic: read-only Cua application/PID tree; unique window inventory mapping does not attest node window scope; Cua bounds are not screenshot coordinates","takeover":"physical Escape aborts only a live queued/preparing/running action or indicator-owned task scope; idle Escape only invalidates observations. Active Escape cancels the task and drops queued work without latching control; a new task with fresh observations can operate after owned input releases. Stopped task IDs cannot reopen. Explicit takeover remains a separate persistent stop."},"timing_ms":{"capture":capture_start.elapsed().as_secs_f64()*1000.0,"observe_total":start.elapsed().as_secs_f64()*1000.0}});
    check_epoch(state, Some(epoch))?;
    if focused(&niri_epoch(state, "windows", Some(epoch))?)
        .as_ref()
        .map(identity)
        != focus.as_ref().map(identity)
        || niri_epoch(state, "outputs", Some(epoch))?[&name]["logical"] != geometry
    {
        return Err("Desktop changed during capture; observe again".into());
    }
    let focus_output = focus
        .as_ref()
        .and_then(|w| w["workspace_id"].as_u64())
        .and_then(|id| {
            workspaces
                .as_array()
                .and_then(|ws| ws.iter().find(|w| w["id"].as_u64() == Some(id)))
        })
        .and_then(|w| w["output"].as_str())
        .map(str::to_owned);
    let obs = Observation {
        id: id.clone(),
        at: capture_at,
        epoch,
        input_generation,
        output: name,
        output_geometry: geometry,
        focused_window: focus,
        windows: windows.clone(),
        focus_output,
        image_width: width,
        image_height: height,
        image: image.clone(),
        view: view.clone(),
        view_image: view_image.clone(),
    };
    let mut saved = state
        .observations
        .lock()
        .map_err(|_| "Observation storage poisoned")?;
    if saved.len() >= 64 {
        if let Some(old) = saved.values().min_by_key(|o| o.at).map(|o| o.id.clone()) {
            if let Some(o) = saved.remove(&old) {
                state.reference_cache.evict(&o.id);
                let _ = fs::remove_file(o.image);
                let _ = fs::remove_file(o.view_image);
            }
        }
    }
    saved.insert(id.clone(), obs);
    drop(saved);
    let image_block = if args["include_image"].as_bool() != Some(false) {
        let bytes = fs::read(&view_image).map_err(|e| e.to_string())?;
        Some(json!({"type":"image","mimeType":"image/png","data":STANDARD.encode(bytes)}))
    } else {
        None
    };
    let total_ms = start.elapsed().as_secs_f64() * 1000.0;
    let mut data = data;
    let readiness = observation_input_readiness(state, input_generation);
    for key in [
        "input_ready",
        "action_ready",
        "current_input_generation",
        "input_activity_during_capture",
        "fresh_observation_required_for_input",
        "takeover_latched",
    ] {
        data[key] = readiness[key].clone();
    }
    data["input_readiness"] = readiness;
    data["input_policy"] = state
        .input_policy
        .status(state.last_human_ms.load(Ordering::SeqCst));
    data["timing_ms"]["capture"] = json!(capture_ms);
    data["timing_ms"]["observe_total"] = json!(total_ms);
    data["capture"]["path"] = json!(view_image);
    data["capture"]["reference_path"] = json!(image);
    data["capture"]["full_output_width"] = json!(width);
    data["capture"]["full_output_height"] = json!(height);
    data["capture"]["view"] = json!(view);
    data["capture"]["image_width"] = json!(view.width);
    data["capture"]["image_height"] = json!(view.height);
    data["capture"]["coordinate_frame"]=json!("x/y actions are local to displayed view image. Core adds view.x/y to map to full output PNG; full output dimensions normalize virtual pointer. Never add desktop output origin.");
    accept_capture(state)?;
    record(
        state,
        "observed",
        json!({"observation_id":id,"capture_ms":capture_ms,"total_ms":total_ms}),
    );
    let mut result = text_result(data);
    if let Some(block) = image_block {
        result["content"].as_array_mut().unwrap().push(block);
    }
    Ok(result)
}

fn validate(
    state: &State,
    obs: &Observation,
    window_id: Option<u64>,
    pointer_action: bool,
) -> R<()> {
    if !state.capture_available.load(Ordering::SeqCst) {
        return Err(
            "Capture unavailable; no input allowed until a successful fresh desktop_observe".into(),
        );
    }
    check_epoch(state, Some(obs.epoch))?;
    state.input_policy.check(obs.input_generation)?;
    if obs.at.elapsed() > MAX_AGE {
        return Err("Observation is older than 60 seconds; call desktop_observe again".into());
    }
    let windows = niri_epoch(state, "windows", Some(obs.epoch))?;
    let current = focused(&windows);
    let expected = window_id.or_else(|| obs.focused_window.as_ref().and_then(|w| w["id"].as_u64()));
    if expected.is_none() {
        return Err("Observed desktop has no focused window identity; no input sent. Focus an actual window, then observe again.".into());
    }
    if expected != obs.focused_window.as_ref().and_then(|w| w["id"].as_u64()) {
        return Err(
            "Requested window was not focused in the observation; focus separately then observe"
                .into(),
        );
    }
    if current.as_ref().and_then(|w| w["id"].as_u64()) != expected {
        return Err("Focused window changed; observe and select the window again".into());
    }
    if current.as_ref().map(identity) != obs.focused_window.as_ref().map(identity) {
        return Err("Window identity, workspace or geometry changed; observe again".into());
    }
    if pointer_action {
        if obs.focus_output.as_deref() != Some(obs.output.as_str()) {
            return Err("Captured output does not contain focused target workspace; focus desired window then observe its output".into());
        }
        let workspace_id = current
            .as_ref()
            .and_then(|w| w["workspace_id"].as_u64())
            .ok_or("Focused target has no workspace identity")?;
        let workspaces = niri_epoch(state, "workspaces", Some(obs.epoch))?;
        let workspace = workspaces
            .as_array()
            .and_then(|ws| ws.iter().find(|w| w["id"].as_u64() == Some(workspace_id)))
            .ok_or("Target workspace disappeared")?;
        if workspace["output"].as_str() != Some(obs.output.as_str())
            || workspace["is_focused"] != true
            || workspace["is_active"] != true
            || workspace["active_window_id"].as_u64() != expected
        {
            return Err("Fresh target workspace/output/active-window mapping changed; no pointer input sent. Observe again.".into());
        }
        if niri_epoch(state, "outputs", Some(obs.epoch))?[&obs.output]["logical"]
            != obs.output_geometry
        {
            return Err("Output geometry or scaling changed; stale pointer target rejected".into());
        }
    }
    Ok(())
}

fn validate_focus_state(state: &State, obs: &Observation) -> R<()> {
    if !state.capture_available.load(Ordering::SeqCst) {
        return Err(
            "Capture unavailable; focus requires a successful fresh desktop_observe".into(),
        );
    }
    check_epoch(state, Some(obs.epoch))?;
    state.input_policy.check(obs.input_generation)?;
    if state.takeover.load(Ordering::SeqCst) {
        return Err("Desktop takeover is latched; focus refused".into());
    }
    if obs.at.elapsed() > MAX_AGE {
        return Err(
            "Observation is older than 60 seconds; focus requires desktop_observe again".into(),
        );
    }
    Ok(())
}
fn validate_focus_inventory(obs: &Observation, window_id: u64, current: &Value) -> R<()> {
    let exact = |windows: &Value| -> R<Value> {
        let matches = windows
            .as_array()
            .ok_or("Window inventory unavailable; observe again before focus")?
            .iter()
            .filter(|window| window["id"].as_u64() == Some(window_id))
            .collect::<Vec<_>>();
        if matches.len() != 1 {
            return Err("Focus target is missing or ambiguous in observed/current window inventory; observe again".into());
        }
        let window = matches[0];
        if window["pid"].as_u64().filter(|pid| *pid > 0).is_none()
            || window["app_id"]
                .as_str()
                .filter(|app| !app.is_empty())
                .is_none()
        {
            return Err(
                "Focus target lacks PID/app identity; no instance identity can be asserted".into(),
            );
        }
        Ok(identity(window))
    };
    if exact(&obs.windows)? != exact(current)? {
        return Err(
            "Focus target identity, workspace or layout changed; observe again before focus".into(),
        );
    }
    Ok(())
}
fn validate_focus_target(state: &State, obs: &Observation, window_id: u64) -> R<()> {
    validate_focus_state(state, obs)?;
    let current = niri_epoch(state, "windows", Some(obs.epoch))?;
    validate_focus_inventory(obs, window_id, &current)?;
    validate_focus_state(state, obs)
}

// Close is deliberately focused-only at first. The explicit IPC ID prevents
// a focus change after validation from redirecting the close to another window.
// This validates a UI target, not ownership or the document's saved state.
fn validate_close_target(state: &State, obs: &Observation, window_id: u64) -> R<()> {
    validate(state, obs, Some(window_id), false)?;
    validate_focus_state(state, obs)?;
    let windows = niri_epoch(state, "windows", Some(obs.epoch))?;
    validate_focus_inventory(obs, window_id, &windows)?;
    let actual = windows
        .as_array()
        .and_then(|items| {
            items
                .iter()
                .find(|item| item["id"].as_u64() == Some(window_id))
        })
        .ok_or("Close target disappeared; no close sent")?;
    let original = obs
        .focused_window
        .as_ref()
        .ok_or("Close requires the exact observed focused window")?;
    if actual["title"].as_str().is_none() || actual["title"] != original["title"] {
        return Err("Close target title/document context changed; reobserve before closing".into());
    }
    let workspace_id = actual["workspace_id"]
        .as_u64()
        .ok_or("Close target has no workspace identity")?;
    let workspaces = niri_epoch(state, "workspaces", Some(obs.epoch))?;
    let workspace = workspaces
        .as_array()
        .and_then(|items| {
            items
                .iter()
                .find(|item| item["id"].as_u64() == Some(workspace_id))
        })
        .ok_or("Close target workspace disappeared")?;
    if workspace["output"].as_str() != Some(obs.output.as_str())
        || workspace["is_active"] != true
        || workspace["is_focused"] != true
        || workspace["active_window_id"].as_u64() != Some(window_id)
    {
        return Err(
            "Close target is not the exact active captured-output window; reobserve".into(),
        );
    }
    if niri_epoch(state, "outputs", Some(obs.epoch))?[&obs.output]["logical"] != obs.output_geometry
    {
        return Err("Close target output geometry/scaling changed; reobserve".into());
    }
    validate(state, obs, Some(window_id), false)
}

fn delay(state: &State, epoch: u64, ms: u64) -> R<()> {
    let until = Instant::now() + Duration::from_millis(ms);
    while Instant::now() < until {
        check_epoch(state, Some(epoch))?;
        thread::sleep(
            Duration::from_millis(5).min(until.saturating_duration_since(Instant::now())),
        );
    }
    check_epoch(state, Some(epoch))
}

fn uses_global_keyboard(keys: &[String], scope: KeyScope) -> R<bool> {
    let codes = keys
        .iter()
        .map(|key| keyboard::named_evdev(key).map_err(|e| e.to_string()))
        .collect::<R<Vec<_>>>()?;
    Ok(scope == KeyScope::Compositor || global_keyboard::requires_global(&codes))
}
fn validate_german_layout(state: &State, epoch: u64) -> R<()> {
    let layouts = niri_epoch(state, "keyboard-layouts", Some(epoch))?;
    let idx = layouts["current_idx"]
        .as_u64()
        .ok_or("Keyboard layout identity unavailable")? as usize;
    if layouts["names"][idx].as_str() != Some("German") {
        return Err("Canonical shortcut backend is calibrated for default German layout; current layout differs. No key input sent.".into());
    }
    Ok(())
}
fn canonical_keys(
    state: &State,
    obs: &Observation,
    epoch: u64,
    keys: &[String],
    scope: KeyScope,
) -> R<()> {
    canonical_keys_with_pre_dispatch(state, obs, epoch, keys, scope, || Ok(()))
}

fn canonical_keys_with_pre_dispatch(
    state: &State,
    obs: &Observation,
    epoch: u64,
    keys: &[String],
    scope: KeyScope,
    mut pre_dispatch: impl FnMut() -> R<()>,
) -> R<()> {
    preflight_keys(keys, scope)?;
    check_epoch(state, Some(epoch))?;
    validate_german_layout(state, epoch)?;
    let codes: Vec<u32> = keys
        .iter()
        .map(|key| keyboard::named_evdev(key).map_err(|e| e.to_string()))
        .collect::<R<_>>()?;
    if uses_global_keyboard(keys, scope)? {
        let mut saved = state
            .global_keyboard
            .lock()
            .map_err(|_| "Global keyboard lock poisoned")?;
        let kb = saved
            .as_mut()
            .ok_or("Global keyboard was not prepared before batch; no fallback/input sent")?;
        let opened_deadline = Instant::now() + remaining_timeout(Duration::from_secs(3));
        kb.ensure_opened(opened_deadline, || check_epoch(state, Some(epoch)))?;
        // Readiness can wait. The earlier layout read is not sufficient.
        validate_german_layout(state, epoch)?;
        let monitor = state
            .human_monitor
            .lock()
            .map_err(|_| "Human monitor status poisoned")?;
        if monitor["available"].as_bool() != Some(true)
            || monitor["watched_devices"].as_u64().unwrap_or(0) == 0
            || monitor["status"].as_str() == Some("starting")
        {
            return Err(
                "Physical takeover monitor unavailable before global shortcut; no key sent".into(),
            );
        }
        drop(monitor);
        check_epoch(state, Some(epoch))?;
        validate(state, obs, None, false)?;
        pre_dispatch()?;
        let result = (|| {
            for code in &codes {
                check_epoch(state, Some(epoch))?;
                kb.key(
                    *code,
                    true,
                    Instant::now() + remaining_timeout(Duration::from_millis(100)),
                    || check_epoch(state, Some(epoch)),
                )?;
                delay(state, epoch, 8)?;
            }
            delay(state, epoch, 20)?;
            for code in codes.iter().rev() {
                check_epoch(state, Some(epoch))?;
                kb.key(
                    *code,
                    false,
                    Instant::now() + remaining_timeout(Duration::from_millis(100)),
                    || check_epoch(state, Some(epoch)),
                )?;
            }
            check_epoch(state, Some(epoch))
        })();
        let release = kb.release_all();
        return result.and(release);
    }
    let mut saved = state
        .keyboard
        .lock()
        .map_err(|_| "Keyboard lock poisoned")?;
    if saved.is_none() {
        *saved = Some(
            Keyboard::new_timeout(remaining_timeout(Duration::from_secs(1)))
                .map_err(|e| e.to_string())?,
        );
    }
    check_epoch(state, Some(epoch))?;
    let kb = saved.as_mut().unwrap();
    // wtype installs its own temporary virtual keymap. Reassert canonical evdev
    // before every shortcut, not only at initial connection creation.
    kb.refresh_keymap_timeout(remaining_timeout(Duration::from_millis(100)))
        .map_err(|e| e.to_string())?;
    check_epoch(state, Some(epoch))?;
    validate(state, obs, None, false)?;
    // Run contextual checks after keymap refresh and target validation, before
    // the first key press. Epoch/cancellation are checked again for every key.
    pre_dispatch()?;
    let result = (|| {
        for key in keys {
            check_epoch(state, Some(epoch))?;
            kb.key_named(key, true).map_err(|e| e.to_string())?;
            delay(state, epoch, 8)?;
        }
        delay(state, epoch, 20)?;
        for key in keys.iter().rev() {
            check_epoch(state, Some(epoch))?;
            kb.key_named(key, false).map_err(|e| e.to_string())?;
        }
        check_epoch(state, Some(epoch))?;
        kb.sync_timeout(remaining_timeout(Duration::from_millis(100)))
            .map_err(|e| e.to_string())
    })(); // Always release modifiers on cancellation/error.
    let release = kb.release_all().map_err(|e| e.to_string());
    result.and(release)
}
fn preflight_keys(keys: &[String], scope: KeyScope) -> R<()> {
    if keys.is_empty() || keys.len() > 6 {
        return Err("Key needs 1..6 key names, modifiers first".into());
    }
    for k in &keys[..keys.len() - 1] {
        match k.to_ascii_lowercase().as_str() {
            "ctrl" | "control" | "shift" | "alt" | "super" | "meta" | "logo" | "altgr" => {}
            _ => return Err("Supported modifiers: ctrl, shift, alt, super, altgr".into()),
        };
    }
    let mut seen = std::collections::HashSet::new();
    for k in keys {
        let code = keyboard::named_evdev(k).map_err(|e| e.to_string())?;
        if !seen.insert(code) {
            return Err("Duplicate key/modifier in chord; no input sent".into());
        }
    }
    if uses_global_keyboard(keys, scope)? {
        global_keyboard::permission_available()?;
    }
    Ok(())
}

fn button(name: &Option<String>) -> R<Button> {
    match name.as_deref().unwrap_or("left") {
        "left" => Ok(Button::Left),
        "right" => Ok(Button::Right),
        "middle" => Ok(Button::Middle),
        _ => Err("Button must be left, right, or middle".into()),
    }
}
fn atspi_helper(state: &State, request: Value, epoch: u64) -> R<Value> {
    let bytes = serde_json::to_vec(&request).map_err(|e| e.to_string())?;
    if bytes.len() > 2 * 1024 * 1024 {
        return Err("Direct accessibility helper request exceeds private2MiB budget".into());
    }
    let executable = env::current_exe()
        .map_err(|e| e.to_string())?
        .to_string_lossy()
        .into_owned();
    let out = bounded_command(
        state,
        &executable,
        &["__atspi_helper".into()],
        Some(&bytes),
        Duration::from_secs(10),
        Some(epoch),
    )?;
    serde_json::from_slice(&out)
        .map_err(|e| format!("Direct AT-SPI helper returned invalid JSON: {e}"))
}
fn semantic_target(state: &State, obs: &Observation, handle: &str) -> R<SemanticTarget> {
    let target=state.semantic_targets.lock().map_err(|_|"Semantic handle cache poisoned")?.get(handle).cloned().ok_or("Semantic handle unknown; call desktop_semantic_direct first. Foreign Cua/index/object tokens are not accepted.")?;
    check_epoch(state, Some(target.epoch))?;
    if !target.background {
        state.input_policy.check(target.input_generation)?;
    }
    if target.at.elapsed() > MAX_AGE {
        return Err("Semantic handle expired; call desktop_semantic_direct again".into());
    }
    if target.background {
        background_semantic_ready(state)?;
        validate_background_semantic_inventory(obs, &target.window,
            &niri_epoch(state, "windows", Some(target.epoch))?,
            &niri_epoch(state, "workspaces", Some(target.epoch))?,
            &niri_epoch(state, "outputs", Some(target.epoch))?,
            !state.input_policy.matches(target.input_generation) || !state.input_policy.controls_released())?;
    } else if obs.focused_window.as_ref().map(identity) != Some(identity(&target.window)) {
        return Err("Semantic handle belongs to another window identity/layout; focus and observe actual target, then read semantics again".into());
    }
    Ok(target)
}

fn background_semantic_ready(state: &State) -> R<()> {
    let monitor = state.human_monitor.lock().map_err(|_| "Physical Escape monitor state poisoned")?;
    if monitor["available"] != true || monitor["watched_devices"].as_u64().unwrap_or(0) == 0
        || monitor["status"] == "starting" || state.takeover.load(Ordering::SeqCst) {
        return Err("Background accessibility requires the live physical Escape monitor and no takeover".into());
    }
    Ok(())
}

fn validate_background_semantic_inventory(obs: &Observation, target: &Value,
    windows: &Value, workspaces: &Value, outputs: &Value, physical_input_changed: bool) -> R<()> {
    if obs.at.elapsed() > MAX_AGE { return Err("Background observation expired; observe again".into()); }
    let id = target["id"].as_u64().ok_or("Background target has no window identity")?;
    let exact = |inventory: &Value| -> R<Value> {
        let matches = inventory.as_array().ok_or("Window inventory unavailable")?.iter()
            .filter(|w| w["id"].as_u64() == Some(id)).collect::<Vec<_>>();
        if matches.len() != 1 || identity(matches[0]) != identity(target)
            || matches[0]["title"] != target["title"] {
            return Err("Background window identity, title or layout changed; observe again".into());
        }
        Ok(matches[0].clone())
    };
    exact(&obs.windows)?;
    let current = exact(windows)?;
    if current["is_focused"] == true && physical_input_changed {
        return Err("Physical input overlaps this exact target window; refresh its state before another action".into());
    }
    let workspace_id = current["workspace_id"].as_u64().ok_or("Background target workspace unavailable")?;
    let matches = workspaces.as_array().ok_or("Workspace inventory unavailable")?.iter()
        .filter(|w| w["id"].as_u64() == Some(workspace_id)).collect::<Vec<_>>();
    if matches.len() != 1 || matches[0]["is_active"] != true
        || matches[0]["output"].as_str() != Some(obs.output.as_str())
        || !obs.output_geometry.is_object() || outputs[&obs.output]["logical"] != obs.output_geometry {
        return Err("Background target's selected output, visible workspace or geometry changed; observe again".into());
    }
    Ok(())
}

fn validate_launch_inventory(obs: &Observation, windows: &Value, workspaces: &Value, outputs: &Value) -> R<()> {
    if obs.focus_output.as_deref() != Some(obs.output.as_str())
        || focused(windows).as_ref().map(identity) != obs.focused_window.as_ref().map(identity) {
        return Err("Launch focus identity/output changed; no spawn, reobserve".into());
    }
    let focused_workspaces = workspaces.as_array().ok_or("Launch workspace inventory unavailable")?
        .iter().filter(|w| w["is_focused"] == true).collect::<Vec<_>>();
    if focused_workspaces.len() != 1 { return Err("Launch active workspace is ambiguous; no spawn".into()); }
    let workspace = focused_workspaces[0];
    let expected_window = obs.focused_window.as_ref().and_then(|w| w["id"].as_u64());
    if workspace["output"].as_str() != Some(obs.output.as_str()) || workspace["is_active"] != true
        || workspace["active_window_id"].as_u64() != expected_window
        || obs.focused_window.as_ref().is_some_and(|w| w["workspace_id"] != workspace["id"])
        || outputs[&obs.output]["logical"] != obs.output_geometry || !obs.output_geometry.is_object() {
        return Err("Launch observed active workspace/output geometry changed; no spawn".into());
    }
    Ok(())
}
fn validate_launch_state(state: &State, obs: &Observation, epoch: u64) -> R<()> {
    validate_launch_state_with_inventory(state, obs, epoch, || Ok((
        niri_epoch(state, "windows", Some(epoch))?,
        niri_epoch(state, "workspaces", Some(epoch))?,
        niri_epoch(state, "outputs", Some(epoch))?,
    )))
}
fn validate_launch_state_with_inventory(state: &State, obs: &Observation, epoch: u64,
    inventory: impl FnOnce() -> R<(Value, Value, Value)>) -> R<()> {
    check_epoch(state, Some(epoch))?;
    validate_retained_observation(state, obs, true)?;
    validate_focus_state(state, obs)?;
    let monitor = state.human_monitor.lock().map_err(|_| "Human monitor status poisoned")?;
    if monitor["available"] != true || monitor["watched_devices"].as_u64().unwrap_or(0) == 0 || monitor["status"] == "starting" {
        return Err("Launch capability unavailable: physical Escape monitor is not ready; no spawn".into());
    }
    drop(monitor);
    let (windows, workspaces, outputs) = inventory()?;
    validate_launch_inventory(obs, &windows, &workspaces, &outputs)?;
    check_epoch(state, Some(epoch))
}
fn execute_launch(state: &State, obs: &Observation, request: launch::Request<'_>, epoch: u64, details: &mut Value) -> R<()> {
    let mut receipt = launch::Receipt::default();
    let mut profile_path = None;
    let result = launch::dispatch(&mut receipt, || validate_launch_state(state, obs, epoch), |gate, receipt| {
        let path = state.image_dir.join(format!("launch-{}", state.serial.fetch_add(1, Ordering::SeqCst)));
        // Validate trusted binary/project/path before creating even the profile.
        let plan = launch::plan(request, &path)?;
        gate()?;
        let mut profile = launch::Profile::create(path)?;
        profile_path = Some(profile.path.clone());
        let result = bounded_command_with_launch(state, plan.program, &plan.args, None,
            remaining_timeout(Duration::from_secs(3)), Some(epoch), Some((&mut || {
                // Recheck filesystem containment immediately before spawn too.
                gate()?;
                let current = launch::plan(request, &profile.path)?;
                plan.require_same_target(&current)
            }, receipt)));
        profile.committed = receipt.spawn_committed;
        result.map(|_| ())
    });
    *details = json!({"launch_revision":launch::REVISION,"spawn":receipt,"profile":profile_path,
        "ui_success":null,"automatic_retry":false,"verification":"OS spawn receipt only; verify fresh owned PID/profile/window/output. Any failure after spawn is uncertain and must not be automatically retried."});
    result
}

fn execute(
    state: &State,
    obs: &Observation,
    pointer: &mut Option<Pointer>,
    action: &Action,
    epoch: u64,
    details: &mut Value,
) -> R<()> {
    check_epoch(state, Some(epoch))?;
    if action.pointer() && pointer.is_none() {
        *pointer = Some(
            Pointer::new_timeout(remaining_timeout(Duration::from_secs(1)))
                .map_err(|e| e.to_string())?,
        );
    }
    check_epoch(state, Some(epoch))?;
    if action.pointer() {
        let refresh = Instant::now();
        pointer
            .as_mut()
            .unwrap()
            .refresh_capabilities_timeout(remaining_timeout(Duration::from_millis(100)))
            .map_err(|e| e.to_string())?;
        check_epoch(state, Some(epoch))?;
        validate(state, obs, None, true)?;
        *details = json!({"pointer_backend_refresh_ms":refresh.elapsed().as_secs_f64()*1000.0,"capability_scope":"fresh Wayland globals and Niri focused workspace/output identity"});
    }
    let move_to = |p: &mut Pointer, x: f64, y: f64| {
        check_epoch(state, Some(epoch))?;
        view_point(obs, x, y)?;
        p.move_to(
            &obs.output,
            x + obs.view.x as f64,
            y + obs.view.y as f64,
            obs.image_width,
            obs.image_height,
        )
        .map_err(|e| e.to_string())
    };
    match action {
        Action::LaunchApp { .. } | Action::EditorOpenProject { .. } | Action::EditorOpenFile { .. } => {
            execute_launch(state, obs, action.launch().unwrap(), epoch, details)?;
        }
        Action::Focus { window_id } => {
            bounded_command(
                state,
                "niri",
                &[
                    "msg".into(),
                    "action".into(),
                    "focus-window".into(),
                    "--id".into(),
                    window_id.to_string(),
                ],
                None,
                Duration::from_secs(3),
                Some(epoch),
            )?;
        }
        Action::CloseWindow { window_id } => {
            // Revalidate immediately before the bounded explicit-ID request.
            validate_close_target(state, obs, *window_id)?;
            bounded_command(
                state,
                "niri",
                &[
                    "msg".into(),
                    "action".into(),
                    "close-window".into(),
                    "--id".into(),
                    window_id.to_string(),
                ],
                None,
                Duration::from_secs(3),
                Some(epoch),
            )?;
            *details = json!({"window_id":window_id,"request":"close-window explicit ID","request_acknowledged":true,"window_closed":null,"saved_document_or_ownership_attested":false,"fresh_inventory_and_modal_verification_required":true});
        }
        Action::Move { x, y } => {
            move_to(pointer.as_mut().unwrap(), *x, *y)?;
        }
        Action::Click {
            x,
            y,
            button: b,
            count,
        } => {
            let p = pointer.as_mut().unwrap();
            move_to(p, *x, *y)?;
            for _ in 0..count.unwrap_or(1) {
                check_epoch(state, Some(epoch))?;
                p.button(button(b)?, true).map_err(|e| e.to_string())?;
                delay(state, epoch, 30)?;
                p.button(button(b)?, false).map_err(|e| e.to_string())?;
                delay(state, epoch, 40)?;
            }
        }
        Action::Scroll { x, y, dx, dy } => {
            let p = pointer.as_mut().unwrap();
            move_to(p, *x, *y)?;
            p.scroll_steps(*dx, *dy).map_err(|e| e.to_string())?;
        }
        Action::Drag {
            x,
            y,
            to_x,
            to_y,
            duration_ms,
        } => {
            let p = pointer.as_mut().unwrap();
            move_to(p, *x, *y)?;
            p.button(Button::Left, true).map_err(|e| e.to_string())?;
            let ms = duration_ms.unwrap_or(300).clamp(50, 3000);
            for i in 1..=20 {
                delay(state, epoch, ms / 20)?;
                move_to(
                    p,
                    x + (to_x - x) * i as f64 / 20.0,
                    y + (to_y - y) * i as f64 / 20.0,
                )?;
            }
            p.button(Button::Left, false).map_err(|e| e.to_string())?;
        }
        Action::Type { text, text_method } => {
            if text.len() > 65536 {
                return Err("Text exceeds 64 KiB".into());
            }
            let app = obs
                .focused_window
                .as_ref()
                .and_then(|w| w["app_id"].as_str())
                .unwrap_or("");
            let clipboard = text_uses_clipboard(app, text, text_method);
            if clipboard {
                let result = execute(
                    state,
                    obs,
                    pointer,
                    &Action::Paste {
                        text: text.clone(),
                        restore_clipboard: true,
                    },
                    epoch,
                    details,
                );
                details["text_backend"] = json!("owned RAM-only clipboard paste; bounded payload MIME restore; SAVE_TARGETS and GTK process-local pointer marker omitted");
                details["text_method_requested"] = json!(match text_method {
                    TextMethod::Auto => "auto",
                    TextMethod::Keyboard => "keyboard",
                    TextMethod::Clipboard => "clipboard",
                });
                result?;
            } else {
                *details = json!({"text_backend":"wtype Unicode keyboard","text_method_requested":match text_method{TextMethod::Auto=>"auto",TextMethod::Keyboard=>"keyboard",TextMethod::Clipboard=>"clipboard"}});
                bounded_command(
                    state,
                    "wtype",
                    &["--".into(), text.clone()],
                    None,
                    Duration::from_secs(8),
                    Some(epoch),
                )?;
            }
        }
        Action::Paste {
            text,
            restore_clipboard,
        } => {
            if text.len() > 65536 {
                return Err("Text exceeds 64 KiB".into());
            }
            check_epoch(state, Some(epoch))?;
            let cancelled = || {
                own_canceled()
                    || state.epoch.load(Ordering::SeqCst) != epoch
                    || !state.input_policy.matches(obs.input_generation)
                    || state.takeover.load(Ordering::SeqCst)
                    || deadline_elapsed()
            };
            // Publication may precede acknowledgement. Keep this possible
            // effect even when the helper is refused, lost, or cancelled.
            *details = json!({"clipboard_changed_possible":true,"clipboard_changed":false,"restore_requested":restore_clipboard,"restored":false,"clipboard_backend":"owned RAM-only multi-MIME data-control helper in a separate user scope"});
            let executable = env::current_exe().map_err(|e| e.to_string())?;
            let mut lease = clipboard::Lease::begin_in_user_scope(
                Path::new("/run/current-system/sw/bin/systemd-run"),
                &executable,
                &["__clipboard_helper"],
                text,
                *restore_clipboard,
                remaining_timeout(Duration::from_secs(2)),
                cancelled,
            )?;
            details["clipboard_changed"] = json!(true);
            details["clipboard_holder"] = lease.ready.clone();
            let operation = (|| {
                canonical_keys_with_pre_dispatch(
                    state,
                    obs,
                    epoch,
                    &["ctrl".into(), "v".into()],
                    KeyScope::App,
                    || {
                        if !lease
                            .check_owned(remaining_timeout(Duration::from_millis(300)), cancelled)?
                        {
                            return Err(
                                "Clipboard source changed before paste; no Ctrl+V dispatched"
                                    .into(),
                            );
                        }
                        // No keymap/layout/window roundtrip follows this check.
                        // Wayland still has no atomic selection-check-and-paste.
                        Ok(())
                    },
                )?;
                details["paste_dispatched"] = json!(true);
                delay(state, epoch, 250)
            })();
            let restore_allowed = *restore_clipboard && !cancelled();
            // Cleanup IPC consumes the same action deadline. No hidden deadline
            // reset. Abandoning never replaces a new human/foreign selection.
            let restoration = lease.finish(
                restore_allowed,
                remaining_timeout(Duration::from_millis(300)),
                cancelled,
            );
            match restoration {
                Ok(result) => {
                    details["restored"] = json!(result["state"] == "restored");
                    details["clipboard_restoration"] = result;
                }
                Err(error) => {
                    details["restore_error"] = json!(error.clone());
                    if operation.is_ok() {
                        return Err(format!(
                            "Clipboard restoration uncertain after paste: {error}"
                        ));
                    }
                }
            }
            operation?;
        }
        Action::Key { keys, key_scope } => {
            details["key_scope"] = json!(if uses_global_keyboard(keys, *key_scope)? {
                "compositor"
            } else {
                "app"
            });
            details["key_transport"] = json!(if uses_global_keyboard(keys, *key_scope)? {
                "owned_uinput"
            } else {
                "wayland_virtual_keyboard"
            });
            canonical_keys(state, obs, epoch, keys, *key_scope)?;
        }
        Action::SemanticSetValue {
            handle_id,
            text,
            expected_text,
        } => {
            let target = semantic_target(state, obs, handle_id)?;
            if !target.background { validate(state, obs, None, false)?; }
            check_epoch(state, Some(epoch))?;
            *details = atspi_helper(
                state,
                json!({"operation":"set_value","snapshot":target.snapshot.as_ref(),"object":target.object,"text":text,"expected_text":expected_text}),
                epoch,
            )?;
            if details["status"] != "buffer_verified" {
                return Err("Direct semantic edit returned uncertain buffer result; inspect new observation and artifact, no retry without new evidence".into());
            }
        }
        Action::SemanticClick {
            handle_id,
            action_name,
        } => {
            let target = semantic_target(state, obs, handle_id)?;
            if !target.background { validate(state, obs, None, false)?; }
            check_epoch(state, Some(epoch))?;
            *details = atspi_helper(
                state,
                json!({"operation":"click","snapshot":target.snapshot.as_ref(),"object":target.object,"action_name":action_name}),
                epoch,
            )?;
            if details["accepted"] != true {
                return Err("Direct semantic action refused; no pointer fallback attempted".into());
            }
        }
        Action::SemanticFocus { handle_id } => {
            let target = semantic_target(state, obs, handle_id)?;
            if target.background { return Err("Background handles never request focus; obtain foreground semantics explicitly".into()); }
            validate(state, obs, None, false)?;
            check_epoch(state, Some(epoch))?;
            *details = atspi_helper(
                state,
                json!({"operation":"focus","snapshot":target.snapshot.as_ref(),"object":target.object}),
                epoch,
            )?;
            if details["status"] != "focus_verified" || details["focus_verified"] != true {
                return Err("Direct semantic focus was not freshly verified on the exact object; obtain fresh observation/semantics. No keyboard or pointer fallback attempted".into());
            }
            // Component acceptance alone does not attest the current Niri
            // focus. A changed window or input generation still stops here.
            validate(state, obs, None, false)?;
        }
        Action::Wait { ms } => {
            if *ms > 10000 {
                return Err("Wait exceeds 10 seconds; reobserve instead".into());
            }
            delay(state, epoch, *ms)?;
        }
    }
    if action.pointer() {
        check_epoch(state, Some(epoch))?;
        pointer
            .as_mut()
            .unwrap()
            .sync_timeout(remaining_timeout(Duration::from_millis(100)))
            .map_err(|e| e.to_string())?;
    }
    check_epoch(state, Some(epoch))
}

fn act(state: &State, args: &Value, epoch: u64) -> R<Value> {
    let result = match act_inner(state, args, epoch) {
        // Generation failures that happen before the active writer is installed
        // have no dispatched effects. In-flight conflicts are reported by the
        // inner batch together with its actual effects and release receipt.
        Err(error) if error.starts_with("Ordinary physical input changed the desktop;") => {
            let data = json!({"schema":1,"task_id":args.get("task_id"),"status":"input_conflict","input_conflict":true,"input_generation":state.input_policy.generation(),"completed_actions":0,"requested_actions":args["actions"].as_array().map(Vec::len),"effects":[],"error":error,"actor_release_confirmed":state.release_confirmed.load(Ordering::SeqCst),"takeover_latched":state.takeover.load(Ordering::SeqCst),"fresh_observation_required":true,"automatic_replay":false,"verification":"No action from this batch dispatched. Wait for any other writer to release, obtain a fresh observation and deliberately continue."});
            record(state, "input_conflict", data.clone());
            *state
                .last_result
                .lock()
                .map_err(|_| "Last result state poisoned")? = data.clone();
            let mut result = text_result(data);
            result["isError"] = json!(true);
            Ok(result)
        }
        result => result,
    };
    if own_canceled() || deadline_elapsed() {
        state.reference_cache.invalidate();
    }
    result
}

fn act_inner(state: &State, args: &Value, epoch: u64) -> R<Value> {
    let start = Instant::now();
    if !state.release_confirmed.load(Ordering::SeqCst) {
        return Err("Previous actuator release/receipt is unconfirmed; call desktop_recover_release explicitly while idle. No new input allowed.".into());
    }
    if !state.capture_available.load(Ordering::SeqCst) {
        return Err(
            "No successful current capture available; call desktop_observe before input".into(),
        );
    }
    if state.takeover.load(Ordering::SeqCst) {
        return Err(
            "Desktop takeover is latched; call desktop_resume, then observe again before acting"
                .into(),
        );
    }
    let parsed: ActArgs = serde_json::from_value(args.clone())
        .map_err(|e| format!("Invalid action arguments: {e}"))?;
    if let Some(guard) = &parsed.expected_global_backend {
        validate_global_backend_guard(guard, &state.session_id, epoch, &parsed.observation_id)?;
    }
    let timeout = Duration::from_millis(parsed.timeout_ms.unwrap_or(30000).clamp(100, 120000));
    let _deadline = DeadlineGuard::set(Some(start + timeout));
    if parsed.settle_ms > 1000 {
        return Err(
            "settle_ms exceeds 1000ms; use bounded fresh observations for slower states".into(),
        );
    }
    if parsed.actions.is_empty() || parsed.actions.len() > 32 {
        return Err("Batch needs 1..32 typed actions".into());
    }
    if parsed.actions.len() > 1
        && parsed
            .actions
            .iter()
            .any(|a| matches!(a, Action::Focus { .. } | Action::SemanticFocus { .. }))
    {
        return Err(
            "Window or semantic focus must be a standalone action; observe again and freshly verify the exact semantic object is focused before keyboard input"
                .into(),
        );
    }
    if parsed.actions.iter().any(|a| a.launch().is_some()) && parsed.actions.len() != 1 {
        return Err("Launch actions must be standalone; obtain a fresh observation before further actions".into());
    }
    if let Some(window_id) = parsed.actions.iter().find_map(|action| match action {
        Action::CloseWindow { window_id } => Some(*window_id),
        _ => None,
    }) {
        if parsed.actions.len() != 1 || parsed.window_id != Some(window_id) {
            return Err("CloseWindow must be standalone with the same explicit top-level window_id; no input sent".into());
        }
    }
    let obs = state
        .observations
        .lock()
        .map_err(|_| "Observation storage poisoned")?
        .get(&parsed.observation_id)
        .cloned()
        .ok_or("Observation missing; call desktop_observe first")?;
    let background_semantic = parsed.actions.iter().all(|action| match action {
        Action::SemanticSetValue { handle_id, .. } | Action::SemanticClick { handle_id, .. } =>
            state.semantic_targets.lock().is_ok_and(|targets| targets.get(handle_id).is_some_and(|t| t.background)),
        _ => false,
    });
    let _input_guard = if background_semantic { input_policy::Guard::background() }
        else { input_policy::Guard::bind(obs.input_generation) };
    if !background_semantic { state.input_policy.check(obs.input_generation)?; }
    // Check the common pointer view before action-specific capability reads.
    // A later oversized click must return the actionable crop error even when
    // an earlier global shortcut cannot access a device on this host.
    if parsed.actions.iter().any(Action::pointer) {
        preflight_pointer_view(&obs)?;
    }
    // Reject malformed later targets before any earlier batch effects occur.
    for action in &parsed.actions {
        preflight(&obs, action)?;
        match action {
            Action::SemanticSetValue { handle_id, .. }
            | Action::SemanticClick { handle_id, .. }
            | Action::SemanticFocus { handle_id } => {
                let target = semantic_target(state, &obs, handle_id)?;
                if target.background && parsed.window_id.is_some_and(|id| Some(id) != target.window["id"].as_u64()) {
                    return Err("Background top-level window_id differs from the exact semantic target".into());
                }
                if target.background && matches!(action, Action::SemanticFocus { .. }) {
                    return Err("Background handles never request focus; obtain foreground semantics explicitly".into());
                }
            }
            _ => {}
        }
    }
    let generated_task = format!("task-{}", state.serial.fetch_add(1, Ordering::SeqCst));
    let admission_gate = state
        .task_scope_gate
        .lock()
        .map_err(|_| "Task scope gate poisoned")?;
    check_epoch(state, Some(epoch))?;
    if state.takeover.load(Ordering::SeqCst) {
        return Err("Desktop takeover is latched; pending batch rejected".into());
    }
    let (task, indicator_scope_owned) =
        authorize_action_task(state, parsed.task_id.as_deref(), generated_task)?;
    record(
        state,
        "action_batch_request",
        json!({"task_id":task,"epoch":epoch,"action_count":parsed.actions.len()}),
    );
    state.queued.fetch_add(1, Ordering::SeqCst);
    drop(admission_gate);
    let mut ptr = loop {
        if let Err(e) = check_epoch(state, Some(epoch)) {
            state.queued.fetch_sub(1, Ordering::SeqCst);
            return Err(e);
        }
        match state.actor.try_lock() {
            Ok(lock) => break lock,
            Err(std::sync::TryLockError::WouldBlock) => thread::sleep(Duration::from_millis(5)),
            Err(_) => {
                state.queued.fetch_sub(1, Ordering::SeqCst);
                return Err("Actuator lock poisoned".into());
            }
        }
    };
    // Serialize the queued-to-active handoff with physical Escape. Until the
    // active record is published this request remains counted as live work.
    let _scope_gate = state
        .task_scope_gate
        .lock()
        .map_err(|_| "Task scope gate poisoned")?;
    state.queued.fetch_sub(1, Ordering::SeqCst);
    check_epoch(state, Some(epoch))?;
    if indicator_scope_owned && !action_scope_still_live(state, &task) {
        return Err("Computer Use task scope ended before actor admission; queued action discarded".into());
    }
    check_epoch(state, Some(obs.epoch))?;
    // Recovery clears cached targets without clearing/incrementing the epoch.
    // A request that cloned an observation before recovery must not reuse it.
    validate_retained_observation(state, &obs, !background_semantic)?;
    if state.takeover.load(Ordering::SeqCst) {
        return Err("Desktop takeover is latched; pending batch rejected".into());
    }
    if !state.release_confirmed.load(Ordering::SeqCst) {
        return Err("Previous actuator release/receipt is unconfirmed; no new input allowed. Call desktop_recover_release explicitly while idle, then observe again.".into());
    }
    *state
        .active
        .lock()
        .map_err(|_| "Active task lock poisoned")? = json!({"task_id":task,"completed_actions":0,"total_actions":parsed.actions.len(),"epoch":epoch,"output":obs.output,"input_generation":obs.input_generation,"phase":"preparing_global_keyboard"});
    state.release_confirmed.store(false, Ordering::SeqCst);
    drop(_scope_gate);
    let mut completed = 0usize;
    let mut effects = Vec::new();
    // Prepare a required global device before ANY earlier batch input. Creation
    // is a documented capability side effect, never an observation side effect.
    // Own event-node open is only a readiness gate, not proof of a UI result.
    let needs_global = parsed.actions.iter().any(|action| match action {
        Action::Key { keys, key_scope } => uses_global_keyboard(keys, *key_scope).unwrap_or(false),
        _ => false,
    });
    let preparation = (|| -> R<()> {
        if needs_global {
            let monitor = state
                .human_monitor
                .lock()
                .map_err(|_| "Human monitor status poisoned")?;
            if monitor["available"].as_bool() != Some(true)
                || monitor["watched_devices"].as_u64().unwrap_or(0) == 0
                || monitor["status"].as_str() == Some("starting")
            {
                return Err(
                "Global keyboard requires an available physical takeover monitor; no input sent"
                    .into(),
            );
            }
            drop(monitor);
            validate(state, &obs, parsed.window_id, false)?;
            let mut saved = state
                .global_keyboard
                .lock()
                .map_err(|_| "Global keyboard lock poisoned")?;
            let deadline = Instant::now() + remaining_timeout(Duration::from_secs(3));
            if saved.is_none() {
                *saved = Some(global_keyboard::GlobalKeyboard::prepare(deadline, || {
                    check_epoch(state, Some(epoch))
                })?);
            }
            saved
                .as_mut()
                .unwrap()
                .ensure_opened(deadline, || check_epoch(state, Some(epoch)))?;
            check_epoch(state, Some(epoch))?;
            validate(state, &obs, parsed.window_id, false)?;
        }
        Ok(())
    })();
    let mut failure = preparation.err();
    if failure.is_none() {
        *state
            .active
            .lock()
            .map_err(|_| "Active task lock poisoned")? = json!({"task_id":task,"completed_actions":0,"total_actions":parsed.actions.len(),"epoch":epoch,"output":obs.output,"input_generation":obs.input_generation,"phase":"executing"});
    }
    for action in &parsed.actions {
        if failure.is_some() {
            break;
        }
        let step = Instant::now();
        let mut dispatched = false;
        let mut guard = Value::Null;
        let mut details = Value::Null;
        let result = (|| {
            check_epoch(state, Some(epoch))?;
            if state.takeover.load(Ordering::SeqCst) {
                return Err("Human desktop takeover; pending actions stopped".into());
            }
            if start.elapsed() > timeout {
                return Err("Action batch timed out".into());
            }
            if let Action::Focus { window_id } = action {
                validate_focus_target(state, &obs, *window_id)?;
            }
            if let Action::CloseWindow { window_id } = action {
                validate_close_target(state, &obs, *window_id)?;
            }
            if action.launch().is_some() {
                if parsed.window_id.is_some() && parsed.window_id != obs.focused_window.as_ref().and_then(|w| w["id"].as_u64()) {
                    return Err("Launch top-level window_id does not match retained focus; no spawn".into());
                }
                validate_launch_state(state, &obs, epoch)?;
            }
            if !background_semantic && action.launch().is_none() && !matches!(action, Action::Focus { .. } | Action::Wait { .. }) {
                validate(state, &obs, parsed.window_id, action.pointer())?;
            }
            if action.pointer() {
                guard = visual_guard(state, &obs, action, epoch)?;
                validate(state, &obs, parsed.window_id, true)?;
            }
            if indicator_scope_owned && !action_scope_still_live(state, &task) {
                return Err("Computer Use task owner ended before dispatch; remaining actions discarded".into());
            }
            dispatched = true;
            record(
                state,
                "action_dispatch",
                json!({"task_id":task,"kind":action.name()}),
            );
            execute(state, &obs, &mut ptr, action, epoch, &mut details)
        })();
        let ok = result.is_ok();
        effects.push(json!({"kind":action.name(),"dispatch_started":dispatched,"acknowledged":ok,"may_have_partial_effects":!ok && if action.launch().is_some() { details["spawn"]["spawn_committed"] == true } else { dispatched && !matches!(action,Action::Wait{..}) },"details":details,"visual_guard":guard,"dispatch_ack_ms":step.elapsed().as_secs_f64()*1000.0}));
        record(
            state,
            "action",
            json!({"task_id":task,"kind":action.name(),"acknowledged":ok,"latency_ms":step.elapsed().as_secs_f64()*1000.0}),
        );
        if let Err(error) = result {
            failure = Some(error);
            break;
        }
        completed += 1;
        *state
            .active
            .lock()
            .map_err(|_| "Active task lock poisoned")? = json!({"task_id":task,"completed_actions":completed,"total_actions":parsed.actions.len(),"epoch":epoch,"output":obs.output,"input_generation":obs.input_generation});
    }
    // A canceled drag/click always releases held buttons before the actor is free.
    let mut cleanup_ok = true;
    if let Some(p) = ptr.as_mut() {
        if let Err(e) = p
            .release_all()
            .and_then(|_| p.sync_timeout(Duration::from_millis(100)))
        {
            cleanup_ok = false;
            if failure.is_none() {
                failure = Some(format!("Button release/receipt failed: {e}"));
            }
        }
    }
    if let Ok(mut kb) = state.keyboard.lock() {
        if let Some(k) = kb.as_mut() {
            if let Err(e) = k
                .release_all()
                .and_then(|_| k.sync_timeout(Duration::from_millis(100)))
            {
                cleanup_ok = false;
                if failure.is_none() {
                    failure = Some(format!("Keyboard release/receipt failed: {e}"));
                }
            }
        }
    } else {
        cleanup_ok = false;
    }
    if let Ok(mut keyboard) = state.global_keyboard.lock() {
        if let Some(keyboard) = keyboard.as_mut() {
            if let Err(error) = keyboard.release_all() {
                cleanup_ok = false;
                if failure.is_none() {
                    failure = Some(format!("Global keyboard release failed: {error}"));
                }
            }
        }
    } else {
        cleanup_ok = false;
    }
    state.release_confirmed.store(cleanup_ok, Ordering::SeqCst);
    // Input dispatch is over and owned controls have been released. Read-only
    // result capture must remain usable during ordinary human activity; each
    // new observation retains its own capture-start input generation.
    drop(_input_guard);
    *state
        .active
        .lock()
        .map_err(|_| "Active task lock poisoned")? = Value::Null;
    let dispatch_ms = start.elapsed().as_secs_f64() * 1000.0;
    let mut after = None;
    let mut after_error = None;
    let after_start = Instant::now();
    let mut settle_wait_ms = 0.0;
    if parsed.observe_after
        && failure.is_none()
        && !own_canceled()
        && state.epoch.load(Ordering::SeqCst) == epoch
    {
        let mut observe_args = json!({"include_image":parsed.include_image});
        if !parsed
            .actions
            .iter()
            .any(|a| matches!(a, Action::Focus { .. }))
        {
            observe_args["output"] = json!(obs.output);
            if !parsed.actions.iter().any(|a| a.launch().is_some()) && (obs.view.x != 0
                || obs.view.y != 0
                || obs.view.width != obs.image_width
                || obs.view.height != obs.image_height)
            {
                observe_args["crop"] = json!(obs.view);
            }
        }
        // Buttons/keys have been released, but retain writer ownership through
        // this capture so another queued action cannot change its result first.
        let settle_started = Instant::now();
        let settled = delay(state, epoch, parsed.settle_ms);
        settle_wait_ms = settle_started.elapsed().as_secs_f64() * 1000.0;
        let allowed_output_only = parsed.actions.iter().any(|a| a.launch().is_some() || matches!(a, Action::Focus { .. }));
        match settled.and_then(|_| observe_inner_guarded(state, &observe_args, true, allowed_output_only)) {
            Ok(v) => after = Some(v),
            Err(e) => after_error = Some(e),
        }
    }
    let canceled = state.epoch.load(Ordering::SeqCst) != epoch || own_canceled();
    let input_conflict = !background_semantic && (!state.input_policy.matches(obs.input_generation)
        || !state.input_policy.controls_released());
    if input_conflict && !canceled {
        record(
            state,
            "input_conflict",
            json!({"task_id":task,"completed_actions":completed,"expected_generation":obs.input_generation,"current_generation":state.input_policy.generation(),"actor_release_confirmed":cleanup_ok,"automatic_replay":false}),
        );
    }
    let mut data = json!({"schema":1,"task_id":task,"observation_id":obs.id,"accepted_actions":args["actions"],"background_semantic":background_semantic,"status":if canceled{"canceled"}else if input_conflict{"input_conflict"}else if failure.is_some(){"failed"}else{"dispatched"},"input_conflict":input_conflict,"expected_input_generation":obs.input_generation,"input_generation":state.input_policy.generation(),"fresh_observation_required":input_conflict,"takeover_latched":state.takeover.load(Ordering::SeqCst),"automatic_replay":false,"completed_actions":completed,"requested_actions":parsed.actions.len(),"effects":effects,"error":failure,"actor_release_confirmed":cleanup_ok,"timing_ms":{"dispatch_and_release":dispatch_ms,"post_action_settle_wait":settle_wait_ms,"post_observe":after_start.elapsed().as_secs_f64()*1000.0},"total_ms":start.elapsed().as_secs_f64()*1000.0,"after_observation_error":after_error,"verification":"Input acknowledgement only. after_observation is fresh evidence to inspect, not inferred UI success. Verify the expected UI state or independent artifact before reporting success."});
    let mut image_blocks = Vec::new();
    if let Some(after) = after {
        if let Some(content) = after["content"].as_array() {
            for block in content {
                if block["type"] == "text" {
                    if let Some(text) = block["text"].as_str() {
                        data["after_observation"] = serde_json::from_str(text)
                            .map_err(|e| format!("Internal post-observation invalid: {e}"))?;
                    }
                } else {
                    image_blocks.push(block.clone());
                }
            }
        }
    }
    *state
        .last_result
        .lock()
        .map_err(|_| "Last result state poisoned")? = data.clone();
    let failed = data["status"] == "failed"
        || data["status"] == "canceled"
        || data["status"] == "input_conflict";
    let mut result = text_result(data);
    result["isError"] = json!(failed);
    result["content"]
        .as_array_mut()
        .unwrap()
        .extend(image_blocks);
    Ok(result)
}

fn semantic(state: &State, args: &Value) -> R<Value> {
    let start = Instant::now();
    let id = args["window_id"]
        .as_u64()
        .ok_or("desktop_semantic requires Niri window_id")?;
    let windows = niri(state, "windows")?;
    let win = windows
        .as_array()
        .and_then(|ws| ws.iter().find(|w| w["id"].as_u64() == Some(id)))
        .ok_or("Requested Niri window is gone")?;
    let pid = win["pid"]
        .as_u64()
        .ok_or("Niri window has no PID; refusing guessed Cua mapping")?;
    let title = win["title"].as_str().unwrap_or("");
    let raw = bounded_command(
        state,
        "cua-driver",
        &["call".into(), "list_windows".into(), "{}".into()],
        None,
        Duration::from_secs(4),
        None,
    )?;
    let listed: Value =
        serde_json::from_slice(&raw).map_err(|e| format!("Cua list_windows invalid: {e}"))?;
    let matches: Vec<&Value> = listed["windows"]
        .as_array()
        .ok_or("Cua windows unavailable")?
        .iter()
        .filter(|w| {
            if w["pid"].as_u64() != Some(pid) {
                return false;
            }
            let cua_title = w["title"].as_str().unwrap_or("");
            let app = w["app_name"].as_str().unwrap_or("");
            cua_title == title
                || (!app.is_empty()
                    && cua_title
                        .strip_suffix(&format!(" [{app}]"))
                        .is_some_and(|s| s == title))
        })
        .collect();
    if matches.len() != 1 {
        return Err(format!("Cua mapping needs exactly one matching PID+title; found {}. Use visual observation rather than guessed synthetic window IDs.",matches.len()));
    }
    let cua = matches[0];
    let cua_id = cua["window_id"]
        .as_u64()
        .ok_or("Cua window lacks numeric synthetic ID")?;
    let max_elements = args["max_elements"].as_u64().unwrap_or(500).clamp(1, 1000);
    let max_depth = args["max_depth"].as_u64().unwrap_or(24).clamp(1, 40);
    let params = json!({"pid":pid,"window_id":cua_id,"include_screenshot":false,"max_elements":max_elements,"max_depth":max_depth});
    let raw = bounded_command(
        state,
        "cua-driver",
        &["call".into(), "get_window_state".into(), params.to_string()],
        None,
        Duration::from_secs(8),
        None,
    )?;
    let tree: Value =
        serde_json::from_slice(&raw).map_err(|e| format!("Cua get_window_state invalid: {e}"))?;
    let fresh = niri(state, "windows")?;
    if fresh
        .as_array()
        .and_then(|ws| ws.iter().find(|w| w["id"].as_u64() == Some(id)))
        .map(identity)
        != Some(identity(win))
    {
        return Err("Window identity/layout changed during semantic capture; reobserve".into());
    }
    let query = args["query"].as_str().map(|s| s.to_lowercase());
    let mut elements = tree["elements"].as_array().cloned().unwrap_or_default();
    let available = elements.len() > 1 || tree["total_element_count"].as_u64().unwrap_or(0) > 1;
    for elem in &mut elements {
        if elem["role"].as_str().is_some_and(|r| {
            r.to_lowercase().contains("password") || r.to_lowercase().contains("securetext")
        }) || elem["is_password"] == true
            || elem["protected"] == true
        {
            *elem = json!({"role":elem["role"],"bounds":elem["bounds"],"element_token":elem["element_token"],"label":"[protected field]","protected_content_redacted":true});
        }
    }
    if let Some(q) = query {
        elements.retain(|e| e.to_string().to_lowercase().contains(&q));
    }
    Ok(text_result(
        json!({"schema":1,"status":if available{"available"}else{"limited"},"niri_window_id":id,"pid":pid,"title":title,"cua_synthetic_window_id":cua_id,"snapshot_id":tree["snapshot_id"],"elements_complete":tree["elements_complete"],"total_element_count":tree.get("total_element_count").filter(|v|v.is_number()).unwrap_or(&tree["element_count"]),"returned_element_count":elements.len(),"elements":elements,"semantic_scope":"application/PID; exact window scoping is not attested by Cua0.23.2. Unique inventory mapping identifies the requested window but does not prove every returned node belongs to it","coordinate_frame":"Cua accessibility bounds are app/window-local and may be inaccurate. They are NOT Niri output screenshot coordinates; do not directly click them. Match semantic intent to fresh visual screenshot until a mapping is explicitly calibrated.","latency_ms":start.elapsed().as_secs_f64()*1000.0,"note":tree["_note"]}),
    ))
}

fn semantic_direct(state: &State, args: &Value) -> R<Value> {
    let started = Instant::now();
    let epoch = state.epoch.load(Ordering::SeqCst);
    let id = args["window_id"]
        .as_u64()
        .ok_or("Niri window_id required")?;
    let _actor = loop {
        check_epoch(state, Some(epoch))?;
        match state.actor.try_lock() {
            Ok(lock) => break lock,
            Err(std::sync::TryLockError::WouldBlock) => thread::sleep(Duration::from_millis(5)),
            Err(_) => return Err("Actuator lock poisoned".into()),
        }
    };
    let input_generation = state.input_policy.generation();
    let windows = niri_epoch(state, "windows", Some(epoch))?;
    let window = windows
        .as_array()
        .and_then(|ws| ws.iter().find(|w| w["id"].as_u64() == Some(id)))
        .cloned()
        .ok_or("Requested Niri window disappeared")?;
    let pid = window["pid"].as_u64().ok_or("Niri target has no PID")?;
    let title = window["title"].as_str().unwrap_or("");
    let sole_window = windows
        .as_array()
        .is_some_and(|ws| ws.iter().filter(|w| w["pid"].as_u64() == Some(pid)).count() == 1);
    let raw = atspi_helper(
        state,
        json!({"operation":"observe","pid":pid,"title":title,"single_window":sole_window,"max_nodes":args["max_elements"].as_u64().unwrap_or(1000).clamp(1,1000),"max_depth":args["max_depth"].as_u64().unwrap_or(40).clamp(1,40),"include_text_selection":args["include_text_selection"].as_bool()==Some(true)}),
        epoch,
    )?;
    let snapshot: atspi::Snapshot = serde_json::from_value(raw["snapshot"].clone())
        .map_err(|e| format!("Direct semantic snapshot invalid: {e}"))?;
    if niri_epoch(state, "windows", Some(epoch))?
        .as_array()
        .and_then(|ws| ws.iter().find(|w| w["id"].as_u64() == Some(id)))
        .map(identity)
        != Some(identity(&window))
    {
        return Err("Niri target changed during semantic capture; reobserve".into());
    }
    check_epoch(state, Some(epoch))?;
    semantic_snapshot_result(
        state,
        args,
        window,
        snapshot,
        epoch,
        input_generation,
        started,
    )
}

fn semantic_input_readiness(state: &State, input_generation: u64, complete: bool, background: bool) -> Value {
    let mut readiness = observation_input_readiness(state, input_generation);
    let activity = readiness
        .as_object_mut()
        .unwrap()
        .remove("input_activity_during_capture")
        .unwrap();
    readiness["input_activity_during_snapshot"] = activity;
    let actionable = complete && if background { background_semantic_ready(state).is_ok() && state.release_confirmed.load(Ordering::SeqCst) }
        else { readiness["action_ready"] == true };
    readiness["background"] = json!(background);
    if background {
        readiness["shared_input_ready"] = readiness["input_ready"].clone();
        readiness["input_ready"] = json!(actionable);
        readiness["fresh_observation_required_for_input"] = json!(!actionable);
    }
    readiness["action_ready"] = json!(actionable);
    readiness["fresh_semantic_snapshot_required_for_input"] = json!(!actionable);
    readiness["note"] = if background {
        json!("Object-targeted background click/edit uses fresh window/output and AT-SPI object/context validation instead of the shared input generation. It never requests focus, keyboard, pointer or clipboard input. Physical input overlapping the exact target, cancellation, incomplete context or an unavailable physical Escape monitor refuse dispatch.")
    } else {
        json!("Semantic labels remain readable during ordinary input/holds. Readiness is advisory, not input permission. Non-actionable or incomplete snapshots issue no handles; semantic input requires a new complete snapshot with released controls and unchanged original generation.")
    };
    readiness
}

fn semantic_snapshot_result(
    state: &State,
    args: &Value,
    window: Value,
    snapshot: atspi::Snapshot,
    epoch: u64,
    input_generation: u64,
    started: Instant,
) -> R<Value> {
    let id = window["id"]
        .as_u64()
        .ok_or("Semantic target has no Niri ID")?;
    let pid = window["pid"].as_u64().ok_or("Semantic target has no PID")?;
    let title = window["title"].as_str().unwrap_or("");
    check_epoch(state, Some(epoch))?;
    let background = args["background"].as_bool() == Some(true);
    let initial_readiness = semantic_input_readiness(state, input_generation,
        snapshot.complete && (!background || window["is_focused"] != true
            || state.input_policy.check(input_generation).is_ok()), background);
    let snapshot = Arc::new(snapshot);
    let snapshot_id = format!(
        "ax-snapshot-{}-{}",
        state.session_id,
        state.serial.fetch_add(1, Ordering::SeqCst)
    );
    let handles: HashMap<atspi::Object, String> = snapshot
        .nodes
        .iter()
        .filter(|n| !n.protected && initial_readiness["action_ready"] == true)
        .map(|n| {
            (
                n.object.clone(),
                format!(
                    "ax-{}-{}",
                    state.session_id,
                    state.serial.fetch_add(1, Ordering::SeqCst)
                ),
            )
        })
        .collect();
    let mut cache = state
        .semantic_targets
        .lock()
        .map_err(|_| "Semantic handle cache poisoned")?;
    if !handles.is_empty() {
        cache.retain(|_, t| {
            t.epoch == epoch && (t.background || t.input_generation == input_generation) && t.at.elapsed() < MAX_AGE
        });
        if cache.len() + handles.len() > 2048 {
            cache.clear();
        }
    }
    let mut elements = Vec::new();
    for node in &snapshot.nodes {
        let handle = handles.get(&node.object);
        if let Some(handle) = handle {
            cache.insert(
                handle.clone(),
                SemanticTarget {
                    background,
                    at: Instant::now(),
                    epoch,
                    input_generation,
                    window: window.clone(),
                    snapshot: snapshot.clone(),
                    object: node.object.clone(),
                },
            );
        }
        let has = |interface: &str| node.interfaces.iter().any(|s| s == interface);
        let has_state = |bit: usize| {
            node.states
                .get(bit / 32)
                .is_some_and(|word| word & (1 << (bit % 32)) != 0)
        };
        let editable = has_state(7);
        let enabled = has_state(8) && has_state(24) && !has_state(3) && !has_state(6);
        let showing = has_state(25);
        let focusable = has_state(11);
        let focused = has_state(12);
        let mut element = json!({"handle_id":handle,"parent_handle_id":node.parent.as_ref().and_then(|p|handles.get(p)),"depth":node.depth,"role":node.role,"label":node.name,"description":node.description,"interfaces":node.interfaces,"states":node.states,"protected":node.protected,"text_excerpt":node.text_excerpt,"action_names":node.action_names,"editable":editable,"enabled":enabled,"showing":showing,"focusable":focusable,"focused":focused,"capabilities":{"focus":!background&&handle.is_some()&&has("org.a11y.atspi.Component")&&focusable&&enabled&&showing,"set_value":handle.is_some()&&has("org.a11y.atspi.EditableText")&&editable&&enabled&&showing,"click":handle.is_some()&&!node.action_names.is_empty()&&enabled&&showing},"coordinates":"none; semantic object targeting only"});
        if let Some(selection) = &node.text_selection {
            element["text_selection"] = json!(selection);
        }
        elements.push(element);
    }
    if let Some(query) = args["query"].as_str() {
        let query = query.to_lowercase();
        elements.retain(|n| n.to_string().to_lowercase().contains(&query));
    }
    let readiness = finalize_semantic_handles(
        state,
        epoch,
        input_generation,
        snapshot.complete,
        initial_readiness["action_ready"] == true,
        background,
        &handles,
        &mut cache,
        &mut elements,
    )?;
    Ok(text_result(
        json!({"schema":1,"status":if snapshot.complete{"available"}else{"limited"},"niri_window_id":id,"epoch":epoch,"input_generation":input_generation,"current_input_generation":readiness["current_input_generation"],"input_ready":readiness["input_ready"],"action_ready":readiness["action_ready"],"input_activity_during_snapshot":readiness["input_activity_during_snapshot"],"fresh_observation_required_for_input":readiness["fresh_observation_required_for_input"],"fresh_semantic_snapshot_required_for_input":readiness["fresh_semantic_snapshot_required_for_input"],"takeover_latched":readiness["takeover_latched"],"input_readiness":readiness,"pid":pid,"title":title,"semantic_snapshot_id":snapshot_id,"background":background,"elements_complete":snapshot.complete,"returned_element_count":elements.len(),"total_element_count":snapshot.nodes.len(),"elements":elements,"semantic_scope":snapshot.semantic_scope,"visibility_traversal":snapshot.visibility_traversal,"traversal_diagnostics":snapshot.traversal_diagnostics,"coordinate_frame":"No pixel bounds exported; direct typed actions use daemon-owned opaque handles only. Cua tokens and raw D-Bus paths are not accepted.","expires_after_ms":60000,"note":if background { "Background handles permit only object-targeted click/edit on the exact visible target window. No focus/keyboard/pointer/clipboard fallback. Fresh unique-owner/window/object/modal/context and expected text checks remain; unrelated physical input does not invalidate this route. A named app action may itself change focus; verify its result." } else { "Labels remain readable during ordinary input or held controls. Non-actionable or incomplete snapshots issue no handles; obtain a fresh complete semantic snapshot after controls are released. Mutations freshly validate original input generation, immutable bus generation/unique owner, object role/name/description/interfaces, observed ancestry, the defined SHOWING-ancestor-chain semantic context, enabled/showing state and modal set. set_value requires expected complete prior text or a complete matching512char excerpt; exact saved artifact remains independently verified." },"latency_ms":started.elapsed().as_secs_f64()*1000.0}),
    ))
}

// A collision while rendering must not publish newly actionable handles.
// This boundary removes only this snapshot's entries; later input still has
// every original action generation/epoch/window/AT-SPI check.
fn finalize_semantic_handles(
    state: &State,
    epoch: u64,
    input_generation: u64,
    complete: bool,
    initial_action_ready: bool,
    background: bool,
    handles: &HashMap<atspi::Object, String>,
    cache: &mut HashMap<String, SemanticTarget>,
    elements: &mut [Value],
) -> R<Value> {
    let publication = (|| {
        check_epoch(state, Some(epoch))?;
        let mut readiness = semantic_input_readiness(state, input_generation, complete, background);
        if !initial_action_ready {
            readiness["action_ready"] = json!(false);
            readiness["fresh_semantic_snapshot_required_for_input"] = json!(true);
        }
        if readiness["action_ready"] != true {
            for handle in handles.values() {
                cache.remove(handle);
            }
            for element in elements.iter_mut() {
                element["handle_id"] = Value::Null;
                element["parent_handle_id"] = Value::Null;
                element["capabilities"]["set_value"] = json!(false);
                element["capabilities"]["click"] = json!(false);
                element["capabilities"]["focus"] = json!(false);
            }
        }
        check_epoch(state, Some(epoch))?;
        Ok(readiness)
    })();
    if publication.is_err() {
        for handle in handles.values() {
            cache.remove(handle);
        }
    }
    publication
}

fn validate_retained_observation(state: &State, obs: &Observation, shared_input: bool) -> R<()> {
    if shared_input { state.input_policy.check(obs.input_generation)?; }
    if !state
        .observations
        .lock()
        .map_err(|_| "Observation storage poisoned")?
        .contains_key(&obs.id)
    {
        return Err(
            "Observation invalidated while waiting for actor; observe again before input".into(),
        );
    }
    Ok(())
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ReleaseRecoveryArgs {
    timeout_ms: Option<u64>,
}

fn recover_release(state: &State, args: &Value) -> R<Value> {
    let start = Instant::now();
    let parsed: ReleaseRecoveryArgs = serde_json::from_value(args.clone())
        .map_err(|e| format!("Invalid release recovery arguments: {e}"))?;
    let budget = Duration::from_millis(parsed.timeout_ms.unwrap_or(1500));
    if !(Duration::from_millis(200)..=Duration::from_millis(2000)).contains(&budget) {
        return Err("Release recovery timeout_ms must be 200..2000".into());
    }
    // No waiting for another writer, no construction/reconnect, no hidden replay.
    let mut ptr = state
        .actor
        .try_lock()
        .map_err(|_| "Actor busy/poisoned; release recovery refused")?;
    let mut active = state
        .active
        .try_lock()
        .map_err(|_| "Active state busy/poisoned; release recovery refused")?;
    if !active.is_null() || state.queued.load(Ordering::SeqCst) != 0 {
        return Err(
            "Release recovery requires actor idle and queue empty; no release attempt made".into(),
        );
    }
    if state.release_confirmed.load(Ordering::SeqCst) {
        return Ok(text_result(
            json!({"schema":1,"status":"already_confirmed","actor_release_confirmed":true,"release_attempted":false,"epoch":state.epoch.load(Ordering::SeqCst),"takeover_latched":state.takeover.load(Ordering::SeqCst),"takeover_cleared":false,"action_replayed":false}),
        ));
    }
    let mut kb = state
        .keyboard
        .try_lock()
        .map_err(|_| "Keyboard busy/poisoned; release recovery refused")?;
    let mut global = state
        .global_keyboard
        .try_lock()
        .map_err(|_| "Global keyboard busy/poisoned; release recovery refused")?;
    let mut observations = state
        .observations
        .try_lock()
        .map_err(|_| "Observations busy/poisoned; release recovery refused")?;
    let mut semantic = state
        .semantic_targets
        .try_lock()
        .map_err(|_| "Semantic storage busy/poisoned; release recovery refused")?;
    let epoch_before = state.epoch.load(Ordering::SeqCst);
    *active =
        json!({"phase":"release_recovery","epoch":epoch_before,"timeout_ms":budget.as_millis()});
    drop(active); // Cancel/takeover/status do not wait for this receipt retry.
                  // Cancellation/takeover must not skip releases. Each call below can ONLY
                  // release owned held inputs or confirm receipt; no new device/keymap/input.
    let report = release_recovery::retry(budget, Instant::now, |backend, until| {
        let remaining = || {
            let left = until.saturating_duration_since(Instant::now());
            if left.is_zero() {
                Err("Release recovery stage deadline elapsed".to_string())
            } else {
                Ok(left)
            }
        };
        match backend {
            release_recovery::Backend::Pointer => match ptr.as_mut() {
                Some(p) => {
                    p.release_all().map_err(|e| e.to_string())?;
                    p.sync_timeout(remaining()?).map_err(|e| e.to_string())?;
                    Ok(true)
                }
                None => Ok(false),
            },
            release_recovery::Backend::Keyboard => match kb.as_mut() {
                Some(k) => {
                    k.release_all().map_err(|e| e.to_string())?;
                    k.sync_timeout(remaining()?).map_err(|e| e.to_string())?;
                    Ok(true)
                }
                None => Ok(false),
            },
            release_recovery::Backend::GlobalKeyboard => match global.as_mut() {
                Some(k) => k.release_all_until(until).map(|_| true),
                None => Ok(false),
            },
        }
    })
    .expect("Release recovery budget was validated before locking");
    if report.confirmed {
        observations.clear();
        state.reference_cache.invalidate();
        semantic.clear();
        let _lifecycle = state
            .capture_failure
            .lock()
            .map_err(|_| "Capture lifecycle state poisoned")?;
        state.capture_available.store(false, Ordering::SeqCst);
    }
    state
        .release_confirmed
        .store(report.confirmed, Ordering::SeqCst);
    *state
        .active
        .lock()
        .map_err(|_| "Active state poisoned after release recovery")? = Value::Null;
    let stages: Vec<Value> = report
        .stages
        .iter()
        .map(|s| json!({"backend":s.backend.name(),"status":s.status,"error":s.error}))
        .collect();
    let data = json!({"schema":1,"status":if report.confirmed {"release_recovered"} else {"release_unconfirmed"},"actor_release_confirmed":report.confirmed,"release_attempted":true,"epoch_before":epoch_before,"epoch":state.epoch.load(Ordering::SeqCst),"takeover_latched":state.takeover.load(Ordering::SeqCst),"takeover_cleared":false,"action_replayed":false,"fresh_observation_required":report.confirmed,"queued_batches":state.queued.load(Ordering::SeqCst),"stages":stages,"elapsed_ms":start.elapsed().as_secs_f64()*1000.0,"note":"Owned release/receipt only. May finalize already-dispatched held input effects; never replays actions or resumes automation. Original last_result preserved. Inspect status and obtain a fresh observation before any continuation. A persistent transport failure remains unconfirmed and requires explicit owned-backend repair."});
    record(state, "desktop_recover_release", data.clone());
    let mut result = text_result(data);
    result["isError"] = json!(!report.confirmed);
    Ok(result)
}

fn accept_capture(state: &State) -> R<()> {
    let mut failure = state
        .capture_failure
        .lock()
        .map_err(|_| "Capture failure state poisoned")?;
    *failure = None;
    state.capture_available.store(true, Ordering::SeqCst);
    Ok(())
}
fn capture_status(state: &State) -> R<Value> {
    // The same short lifecycle lock protects attempt, failure and availability
    // updates. Do not hold it across capture, filesystem work or other locks.
    let failure = state
        .capture_failure
        .lock()
        .map_err(|_| "Capture failure state poisoned")?;
    let attempts = state.capture_attempts.load(Ordering::SeqCst);
    let available = state.capture_available.load(Ordering::SeqCst);
    Ok(
        json!({"capture_available":available,"capture_attempts":attempts,"capture_not_attempted":attempts==0,"capture_failed":failure.is_some(),"capture_failure_category":*failure,"capture_state":if available{"ready"}else if failure.is_some(){"failed"}else if attempts==0{"not_attempted"}else{"no_current_observation"},"note":"No attempt at startup is expected, not capture failure. Resume requires input/release gates and current user authorization, not prior capture. Input always requires a subsequent accepted fresh observation."}),
    )
}
fn resume_input_readiness(state: &State) -> R<Value> {
    let now = state.started.elapsed().as_millis() as u64;
    let last = state.last_human_ms.load(Ordering::SeqCst);
    let monitor = state
        .human_monitor
        .lock()
        .map_err(|_| "Human input monitor state poisoned")?;
    let monitor_ready = monitor["available"] == true
        && monitor["status"] != "starting"
        && monitor["watched_devices"].as_u64().unwrap_or(0) > 0;
    drop(monitor);
    let startup_quiet = now >= 300;
    let activity_age = (last != 0).then(|| now.saturating_sub(last));
    let activity_quiet = activity_age.is_none_or(|age| age >= 300);
    let controls_released = state.input_policy.controls_released();
    let wait_remaining = 300u64.saturating_sub(now).max(
        activity_age
            .map(|age| 300u64.saturating_sub(age))
            .unwrap_or(0),
    );
    Ok(
        json!({"ready":startup_quiet&&monitor_ready&&controls_released&&activity_quiet,"monitor_ready":monitor_ready,"controls_released":controls_released,"startup_quiet_interval_complete":startup_quiet,"recent_activity_quiet_interval_complete":activity_quiet,"required_quiet_ms":300,"wait_remaining_ms":wait_remaining,"last_activity_monotonic_ms":last,"last_activity_age_ms":activity_age,"input_generation":state.input_policy.generation(),"snapshot_only":true,"note":"This shared input-readiness snapshot neither authorizes resume nor confirms actor/queue release. desktop_resume rechecks all gates and the exact observed backend session/epoch."}),
    )
}
fn validate_resume_arguments(args: &Value) -> R<()> {
    let fields = args
        .as_object()
        .ok_or("Desktop resume arguments must be an object")?;
    if fields
        .keys()
        .any(|k| k != "expected_epoch" && k != "expected_session_id")
    {
        return Err(
            "Unknown desktop resume field; only expected_epoch and expected_session_id are allowed"
                .into(),
        );
    }
    if fields
        .get("expected_epoch")
        .is_some_and(|v| v.as_u64().is_none_or(|e| e == 0))
        || fields
            .get("expected_session_id")
            .is_some_and(|v| v.as_str().is_none_or(|v| v.is_empty()))
    {
        return Err("Invalid desktop resume binding field types; expected positive integer epoch and nonempty session string".into());
    }
    Ok(())
}
fn resume_proxy_preflight(args: &Value, response: &Value) -> R<Option<Value>> {
    validate_resume_arguments(args)?;
    if response["isError"] == true {
        return Err("Resume backend capability query failed; no transition forwarded".into());
    }
    let status: Value = serde_json::from_str(
        response["content"][0]["text"]
            .as_str()
            .ok_or("Resume status metadata unavailable")?,
    )
    .map_err(|_| "Resume status metadata invalid")?;
    if status["resume_binding_revision"].as_u64() == Some(RESUME_BINDING_REVISION as u64) {
        return Ok(None);
    }
    if status["takeover_latched"] == false
        && status.get("active").is_some_and(Value::is_null)
        && status["queued_batches"].as_u64() == Some(0)
        && status["actor_release_confirmed"] == true
        && status["epoch"].as_u64().is_some_and(|e| e > 0)
        && status["session_id"].as_str().is_some_and(|v| !v.is_empty())
    {
        // Never send a mutation to an old backend that could ignore fence
        // fields. An already-unlatched idle status needs no transition.
        return Ok(Some(text_result(
            json!({"schema":1,"status":"already_resumed","session_id":status["session_id"],"epoch":status["epoch"],"takeover_latched":false,"actor_release_confirmed":true,"input_permission_changed":false,"fresh_observation_required":false,"resume_request_forwarded":false,"snapshot_only":true,"note":"Read-only idle/unlatched status from an older backend; no resume mutation forwarded. This snapshot does not return control or authorize input. Reinspect status if a new stop occurs; fresh observation and actor gates still apply."}),
        )));
    }
    Err("Backend does not implement resume binding revision1; active/busy/unknown-state resume refused before forwarding. Restart/reconnect the declared backend while released; never fall back to an unbound resume.".into())
}

fn validate_resume_binding(state: &State, args: &Value, request_epoch: u64) -> R<u64> {
    check_epoch(state, Some(request_epoch))?;
    let expected = args["expected_epoch"].as_u64().ok_or("Active takeover resume requires observed expected_epoch and expected_session_id; inspect status and honor the user's return of control")?;
    let session = args["expected_session_id"].as_str().ok_or("Active takeover resume requires observed expected_session_id; inspect status and honor the user's return of control")?;
    if session != state.session_id
        || expected != request_epoch
        || expected != state.epoch.load(Ordering::SeqCst)
    {
        return Err("Desktop resume binding is stale or belongs to another backend; a newer stop/cancellation remains latched. Inspect its current cause and obtain the required return of control; do not refresh and retry blindly.".into());
    }
    Ok(expected)
}

// Internal read-only indicator contract. Do not copy full status, app content,
// capture/readiness state or input device capability locks into this small reply.
fn indicator_lifecycle(state: &State, args: &Value) -> R<Value> {
    let event = args["event"].as_str().unwrap_or("status");
    let task_id = args["task_id"].as_str();
    let scope_token = args["scope_token"].as_str();
    let binding_session = args["session_id"].as_str();
    let binding_epoch = args["expected_epoch"].as_u64();
    if scope_token.is_some_and(|token| token.is_empty() || token.len() > 128 || token.chars().any(char::is_control)) {
        return Err("Task scope ownership token must be a nonempty printable identifier of at most 128 bytes".into());
    }
    if event == "begin" {
        let task_id = task_id.ok_or("Task scope ID is required")?;
        let scope_token = scope_token.ok_or("Task scope ownership token is required")?;
        if task_id.is_empty() || task_id.len() > 128 || task_id.chars().any(char::is_control) {
            return Err("Task scope ID must be a nonempty printable identifier of at most 128 bytes".into());
        }
        let _gate = state.task_scope_gate.lock().map_err(|_| "Task scope gate poisoned")?;
        if binding_session != Some(state.session_id.as_str())
            || binding_epoch != Some(state.epoch.load(Ordering::SeqCst))
        {
            return Err("Task indicator binding is stale; canceled or older tasks cannot reopen their scope".into());
        }
        if state.takeover.load(Ordering::SeqCst)
            || !state.release_confirmed.load(Ordering::SeqCst)
            || !state.active.lock().map_err(|_| "Active task poisoned")?.is_null()
            || state.queued.load(Ordering::SeqCst) != 0
        {
            return Err("Task scope requires an unlatched, idle, released actor; it cannot resume a stopped task".into());
        }
        let owner_processes = if let Some(value) = args.get("owner_processes") {
            let processes: [ScopeProcess; 2] = serde_json::from_value(value.clone())
                .map_err(|_| "Task scope requires exact helper and owner process identities")?;
            if !processes.iter().all(ScopeProcess::is_live) {
                return Err("Task scope helper or owner process is no longer live".into());
            }
            Some(processes)
        } else {
            None
        };
        let mut scopes = state.task_scopes.lock().map_err(|_| "Task scopes poisoned")?;
        scopes.retain(|_, scope| scope.is_live());
        if !scopes.is_empty() && !scopes.contains_key(task_id) {
            return Err("Another Computer Use task scope is already live; end or release it before starting a new task".into());
        }
        let mut used = state.task_scope_ids.lock().map_err(|_| "Task scope IDs poisoned")?;
        if used.contains(task_id) {
            if let Some(scope) = scopes.get_mut(task_id) {
                if scope.epoch != binding_epoch.unwrap_or_default() {
                    return Err("Task indicator scope epoch does not match its original authorization".into());
                }
                match scope.scope_token.as_deref() {
                    Some(existing) if existing == scope_token => {
                        if let Some(processes) = owner_processes {
                            if scope.owner_processes.as_ref().is_some_and(|bound| bound != &processes) {
                                return Err("Task scope process ownership cannot be replaced".into());
                            }
                            scope.owner_processes = Some(processes);
                        }
                        return Ok(text_result(json!({"schema":1,"lifecycle_revision":3,"complete":true,"session_id":state.session_id,"epoch":state.epoch.load(Ordering::SeqCst),"takeover_latched":false,"actor_active":false,"task_scope_active":true,"task_scope_lifetime":if scope.owner_processes.is_some(){"owner_process"}else{"legacy_deadline"},"queued_batches":state.queued.load(Ordering::SeqCst),"actor_release_confirmed":true})))
                    }
                    None => {
                        scope.scope_token = Some(scope_token.to_string());
                        scope.owner_processes = owner_processes;
                    }
                    Some(_) => return Err("Task scope ownership token differs from the current owner".into()),
                }
            } else {
                return Err("Task scope ID was already used in this backend session; choose a fresh ID".into());
            }
        } else {
            used.insert(task_id.to_string());
            scopes.insert(task_id.to_string(), TaskScope {
                expires: Instant::now() + Duration::from_secs(20 * 60),
                owner_processes,
                epoch: binding_epoch.unwrap_or_default(),
                scope_token: Some(scope_token.to_string()),
            });
        }
    } else if event == "end" {
        let _gate = state.task_scope_gate.lock().map_err(|_| "Task scope gate poisoned")?;
        let task_id = task_id.ok_or("Task scope ID is required")?;
        let scope_token = scope_token.ok_or("Task scope ownership token is required")?;
        if binding_session != Some(state.session_id.as_str()) {
            return Err("Task scope end binding belongs to a different actor session".into());
        }
        let mut scopes = state.task_scopes.lock().map_err(|_| "Task scopes poisoned")?;
        if let Some(scope) = scopes.get(task_id) {
            if Some(scope.epoch) != binding_epoch || scope.scope_token.as_deref() != Some(scope_token) {
                return Err("Task scope end ownership binding is stale; current scope was left intact".into());
            }
            scopes.remove(task_id);
        }
    } else if event != "status" {
        return Err("Indicator lifecycle event must be status, begin, or end".into());
    }
    let active = state.active.lock().map_err(|_| "Active state poisoned")?;
    let actor_active = !active.is_null();
    let release_confirmed = state.release_confirmed.load(Ordering::SeqCst);
    let queued = state.queued.load(Ordering::SeqCst);
    let task_scope_active = task_id.is_some_and(|task_id| {
        state.task_scopes.lock().is_ok_and(|mut scopes| {
            scopes.retain(|_, scope| scope.is_live());
            scopes.get(task_id).is_some_and(|scope| {
                scope.epoch == binding_epoch.unwrap_or_default()
                    && scope_token.is_some_and(|token| scope.scope_token.as_deref() == Some(token))
                    && binding_session == Some(state.session_id.as_str())
            })
        })
    });
    drop(active);
    let owner_bound = task_id.is_some_and(|id| state.task_scopes.lock().is_ok_and(|scopes| {
        scopes.get(id).is_some_and(|scope| scope.owner_processes.is_some())
    }));
    let mut reply = json!({
        "schema":1,"lifecycle_revision":3,"complete":true,
        "session_id":state.session_id,
        "task_scope_active":task_scope_active,
        "epoch":state.epoch.load(Ordering::SeqCst),
        "takeover_latched":state.takeover.load(Ordering::SeqCst),
        "actor_active":actor_active,"queued_batches":queued,
        "actor_release_confirmed":release_confirmed
    });
    if task_id.is_some() {
        reply["task_scope_lifetime"] = json!(if owner_bound { "owner_process" } else { "legacy_deadline" });
    }
    Ok(text_result(reply))
}

fn handle(state: &State, tool: &str, args: &Value, epoch: u64) -> R<Value> {
    match tool {
        "desktop_indicator_lifecycle" => indicator_lifecycle(state, args),
        "desktop_status" => {
            let capture = capture_status(state)?;
            let readiness = resume_input_readiness(state)?;
            let mut data = json!({"schema":1,"version":"0.1.0","session_id":state.session_id,"resume_binding_revision":RESUME_BINDING_REVISION,"interface_revision":GLOBAL_ROUTING_REVISION,"global_keyboard":global_keyboard_capability(),"desktop":"niri-wayland","epoch":state.epoch.load(Ordering::SeqCst),"queued_batches":state.queued.load(Ordering::SeqCst),"active":*state.active.lock().map_err(|_|"Active state poisoned")?,"last_result":*state.last_result.lock().map_err(|_|"Last result state poisoned")?,"actor_release_confirmed":state.release_confirmed.load(Ordering::SeqCst),"capture_available":state.capture_available.load(Ordering::SeqCst),"fresh_observation_required_after_capture_failure":true,"uptime_ms":state.started.elapsed().as_millis(),"takeover_latched":state.takeover.load(Ordering::SeqCst),"takeover_persistence":state.takeover_marker.lock().map_err(|_|"Takeover marker state poisoned")?.status(),"input_policy":state.input_policy.status(state.last_human_ms.load(Ordering::SeqCst)),"human_input_monitor":*state.human_monitor.lock().map_err(|_|"Human input monitor state poisoned")?});
            for (key, value) in capture.as_object().unwrap() {
                data[key] = value.clone();
            }
            data["resume_input_readiness"] = readiness;
            data["escape_policy"] = json!("cancel_current_task");
            data["idle_escape_latches"] = json!(false);
            data["persistent_escape_stop"] = json!(false);
            data["window_close_request"] = close_window_capability();
            data["desktop_launch"] = launch::capability();
            Ok(text_result(data))
        }
        "desktop_windows" => Ok(text_result(
            json!({"schema":1,"windows":niri(state,"windows")?,"outputs":niri(state,"outputs")?,"workspaces":niri(state,"workspaces")?}),
        )),
        "desktop_observe" => observe(state, args),
        "desktop_semantic" => semantic(state, args),
        "desktop_semantic_direct" => semantic_direct(state, args),
        "desktop_act" => act(state, args, epoch),
        "desktop_recover_release" => recover_release(state, args),
        "desktop_cancel" | "desktop_takeover" => {
            let _scope_gate = state.task_scope_gate.lock().map_err(|_| "Task scope gate poisoned")?;
            if tool == "desktop_takeover" {
                state.takeover.store(true, Ordering::SeqCst);
            }
            let start = Instant::now();
            let next = state.epoch.fetch_add(1, Ordering::SeqCst) + 1;
            state.task_scopes.lock().map_err(|_| "Task scopes poisoned")?.clear();
            state.reference_cache.invalidate();
            if tool == "desktop_takeover" {
                if let Err(error) = state
                    .takeover_marker
                    .lock()
                    .map_err(|_| "Takeover marker state poisoned")?
                    .set("explicit_desktop_takeover", &state.session_id)
                {
                    record(
                        state,
                        "takeover_persistence_failed",
                        json!({"error":error,"input_remains_latched":true,"backend_restart_requires_resume":true}),
                    );
                    return Err(format!("Desktop takeover latched and pending work canceled, but its marker could not be persisted: {error}. Every backend startup still requires explicit resume."));
                }
            }
            let active = state
                .active
                .lock()
                .map_err(|_| "Active task poisoned")?
                .clone();
            record(
                state,
                tool,
                json!({"epoch":next,"ack_ms":start.elapsed().as_secs_f64()*1000.0}),
            );
            Ok(text_result(
                json!({"schema":1,"status":"cancel_acknowledged","epoch":next,"active_at_ack":active,"ack_ms":start.elapsed().as_secs_f64()*1000.0,"actor_fully_released":false,"note":"Epoch changed immediately. Active actor releases buttons and returns canceled; inspect desktop_status until active=null and actor_release_confirmed=true before assuming desktop is free. Already-dispatched effects cannot be rolled back."}),
            ))
        }
        "desktop_resume" | "desktop_resume_bound" => {
            validate_resume_arguments(args)?;
            if !state
                .active
                .lock()
                .map_err(|_| "Active task poisoned")?
                .is_null()
            {
                return Err(
                    "Actor still releasing canceled task; wait desktop_status active=null".into(),
                );
            }
            if !state.release_confirmed.load(Ordering::SeqCst) {
                return Err("Previous actor release was not confirmed by compositor; call desktop_recover_release explicitly while idle; takeover remains latched".into());
            }
            if state.queued.load(Ordering::SeqCst) != 0 {
                return Err("Desktop resume requires an empty actor queue; wait for pending batches to finish or stop".into());
            }
            // Do not wait for a writer. Holding the idle writer gate prevents
            // a new batch from entering between the checks and the transition.
            let _actor = state
                .actor
                .try_lock()
                .map_err(|_| "Actor busy/poisoned; desktop resume refused")?;
            if state.queued.load(Ordering::SeqCst) != 0 {
                return Err("Desktop resume requires an empty actor queue; pending batch arrived while acquiring the idle writer gate".into());
            }
            if !state.takeover.load(Ordering::SeqCst) {
                // No latch transition is needed. Recent normal input is allowed
                // in cooperative mode; existing targets still obey generation,
                // focus and held-control guards at observation/dispatch time.
                return Ok(text_result(
                    json!({"schema":1,"status":"already_resumed","epoch":state.epoch.load(Ordering::SeqCst),"input_generation":state.input_policy.generation(),"takeover_latched":false,"actor_release_confirmed":true,"input_permission_changed":false,"fresh_observation_required":false,"note":"No transition or cache invalidation performed. last_reason is history. Normal physical input still invalidates stale targets; obtain a fresh observation before dispatching."}),
                ));
            }
            let bound_epoch = if args.get("expected_epoch").is_some() || args.get("expected_session_id").is_some() {
                Some(validate_resume_binding(state, args, epoch)?)
            } else {
                None
            };
            let readiness = resume_input_readiness(state)?;
            if readiness["startup_quiet_interval_complete"] != true {
                return Err("Backend startup quiet interval is not complete; wait at least300ms and inspect desktop_status before explicit resume".into());
            }
            if readiness["monitor_ready"] != true {
                return Err("Physical input monitor is not ready/available; takeover remains latched. Repair input observation before resuming desktop automation.".into());
            }
            if readiness["controls_released"] != true {
                return Err("Physical controls are still held or their state is uncertain; wait for release/monitor recovery before explicit resume".into());
            }
            if readiness["recent_activity_quiet_interval_complete"] != true {
                return Err("Physical human input is still recent; wait until desktop is idle before resuming".into());
            }
            let expected_epoch = match bound_epoch {
                Some(expected_epoch) => expected_epoch,
                None => validate_resume_binding(state, args, epoch)?,
            };
            record(
                state,
                "resume_preparing",
                json!({"expected_epoch":expected_epoch,"session_id":state.session_id}),
            );
            // Serialize the bound startup/explicit takeover transition with cancel.
            // A cancellation that wins this gate changes the epoch first.
            let _scope_gate = state.task_scope_gate.lock().map_err(|_| "Task scope gate poisoned")?;
            validate_resume_binding(state, args, epoch)?;
            if own_canceled() || deadline_elapsed() {
                return Err("Canceled or expired resume request; stop remains latched".into());
            }
            let last = readiness["last_activity_monotonic_ms"]
                .as_u64()
                .ok_or("Input readiness timestamp missing")?;
            let generation = readiness["input_generation"]
                .as_u64()
                .ok_or("Input readiness generation missing")?;
            // Serialize marker deletion with explicit takeover/cancellation. The
            // tool call must follow the user's return-of-control policy.
            let mut marker = state
                .takeover_marker
                .lock()
                .map_err(|_| "Takeover marker state poisoned")?;
            validate_resume_binding(state, args, epoch)?;
            if state.last_human_ms.load(Ordering::SeqCst) != last
                || !state.input_policy.matches(generation)
                || !state.input_policy.controls_released()
            {
                return Err(
                    "Physical input changed while preparing resume; takeover remains latched"
                        .into(),
                );
            }
            let next = expected_epoch
                .checked_add(1)
                .ok_or("Resume epoch exhausted; refusal retained")?;
            state
                .epoch
                .compare_exchange(expected_epoch, next, Ordering::SeqCst, Ordering::SeqCst)
                .map_err(|_| "New stop/cancellation arrived while resuming; refusal retained")?;
            state.reference_cache.invalidate();
            marker.clear()?;
            state.takeover.store(false, Ordering::SeqCst);
            if state.last_human_ms.load(Ordering::SeqCst) != last
                || !state.input_policy.matches(generation)
                || !state.input_policy.controls_released()
                || state.epoch.load(Ordering::SeqCst) != next
                || own_canceled()
                || deadline_elapsed()
            {
                state.takeover.store(true, Ordering::SeqCst);
                if let Err(error) = marker.restore() {
                    record(
                        state,
                        "takeover_persistence_failed",
                        json!({"error":error,"input_remains_latched":true,"backend_restart_requires_resume":true}),
                    );
                }
                return Err(
                    "Human input or cancellation arrived while resuming; takeover remains latched"
                        .into(),
                );
            }
            record(state, "desktop_resume", json!({"epoch":next}));
            Ok(text_result(
                json!({"schema":1,"status":"resumed","epoch":next,"fresh_observation_required":true,"backend_restart_requires_explicit_resume":true,"old_task_or_queue_resumed":false}),
            ))
        }
        _ => Err(format!("Unknown tool {tool}")),
    }
}

// Keep each tagged object aligned with Action's deny_unknown_fields contract.
// The union stays small enough for the client's ordinary MCP schema budget.
fn action_schema() -> Value {
    fn variant(kind: &str, mut properties: Value, fields: &[&str]) -> Value {
        properties["kind"] = json!({"type":"string","enum":[kind]});
        let mut required = vec!["kind"];
        required.extend_from_slice(fields);
        json!({"type":"object","properties":properties,"required":required,"additionalProperties":false})
    }
    let number = json!({"type":"number"});
    let string = json!({"type":"string"});
    json!({"anyOf":[
        variant("launch_app", json!({"app":{"type":"string","enum":["zed","chrome"]}}), &["app"]),
        variant("editor_open_project", json!({"project":{"type":"string","enum":["jarvis"]}}), &["project"]),
        variant("editor_open_file", json!({"project":{"type":"string","enum":["jarvis"]},"relative_path":{"type":"string","minLength":1,"maxLength":4096}}), &["project","relative_path"]),        variant("focus", json!({"window_id":{"type":"integer","minimum":0,"description":"Destination ID inside standalone action."}}), &["window_id"]),
        variant("close_window", json!({"window_id":{"type":"integer","minimum":0,"description":"Standalone; same top-level window_id."}}), &["window_id"]),
        variant("move", json!({"x":number,"y":number}), &["x","y"]),
        variant("click", json!({"x":number,"y":number,"button":{"type":["string","null"],"enum":["left","right","middle",null],"default":"left"},"count":{"type":["integer","null"],"minimum":1,"maximum":3,"default":1}}), &["x","y"]),
        variant("scroll", json!({"x":number,"y":number,"dx":{"type":"integer","minimum":-100,"maximum":100,"default":0},"dy":{"type":"integer","minimum":-100,"maximum":100,"default":0}}), &["x","y"]),
        variant("drag", json!({"x":number,"y":number,"to_x":number,"to_y":number,"duration_ms":{"type":["integer","null"],"minimum":50,"maximum":3000,"default":300}}), &["x","y","to_x","to_y"]),
        variant("type", json!({"text":string,"text_method":{"type":"string","enum":["auto","keyboard","clipboard"],"default":"auto"}}), &["text"]),
        variant("paste", json!({"text":string,"restore_clipboard":{"type":"boolean","default":true}}), &["text"]),
        variant("key", json!({"keys":{"type":"array","items":{"type":"string"},"minItems":1,"maxItems":6,"description":"Modifiers first; no window_id here."},"key_scope":{"type":"string","enum":["app","compositor"],"default":"app"}}), &["keys"]),
        variant("semantic_set_value", json!({"handle_id":string,"text":string,"expected_text":{"type":["string","null"]}}), &["handle_id","text"]),
        variant("semantic_click", json!({"handle_id":string,"action_name":string}), &["handle_id","action_name"]),
        variant("semantic_focus", json!({"handle_id":{"type":"string","description":"Fresh semantic handle; standalone. Reobserve/requery before keyboard input."}}), &["handle_id"]),
        variant("wait", json!({"ms":{"type":"integer","minimum":0,"maximum":10000}}), &["ms"])
    ]})
}

#[cfg(test)]
mod action_schema_contract_tests {
    use super::*;

    fn variant(schema: &Value, kind: &str) -> Value {
        schema["anyOf"]
            .as_array()
            .unwrap()
            .iter()
            .find(|v| v["properties"]["kind"]["enum"] == json!([kind]))
            .unwrap()
            .clone()
    }

    #[test]
    fn actual_t3_development_text_uses_preserving_clipboard_without_changing_overrides() {
        let app = "com.t3tools.T3Code.Development";
        assert!(text_uses_clipboard(app, "Hello, Jarvis", &TextMethod::Auto));
        assert!(!text_uses_clipboard(app, "Hello, Jarvis", &TextMethod::Keyboard));
        assert!(text_uses_clipboard("other.app", "Hello, Jarvis", &TextMethod::Clipboard));
        assert!(!text_uses_clipboard("com.t3tools.T3Code.Development.other", "short", &TextMethod::Auto));
        assert!(!text_uses_clipboard("other.app", &"🦦".repeat(1000), &TextMethod::Auto));
        assert!(text_uses_clipboard("other.app", &"🦦".repeat(1001), &TextMethod::Auto));
        for existing in ["code", "Code", "com.visualstudio.code", "com.t3tools.T3Code", "com.t3tools.T3CodeNightly"] {
            assert!(text_uses_clipboard(existing, "short", &TextMethod::Auto));
        }
    }
    #[test]
    fn variant_fields_and_required_members_match_actual_serde() {
        let samples = [
            (json!({"kind":"launch_app","app":"zed"}), vec!["app"]),
            (json!({"kind":"editor_open_project","project":"jarvis"}), vec!["project"]),
            (json!({"kind":"editor_open_file","project":"jarvis","relative_path":"README.md"}), vec!["project", "relative_path"]),            (json!({"kind":"focus","window_id":261}), vec!["window_id"]),
            (
                json!({"kind":"close_window","window_id":261}),
                vec!["window_id"],
            ),
            (json!({"kind":"move","x":2.5,"y":3.0}), vec!["x", "y"]),
            (
                json!({"kind":"click","x":2.0,"y":3.0,"button":"right","count":2}),
                vec!["x", "y"],
            ),
            (
                json!({"kind":"scroll","x":2.0,"y":3.0,"dx":1,"dy":-1}),
                vec!["x", "y"],
            ),
            (
                json!({"kind":"drag","x":2.0,"y":3.0,"to_x":4.0,"to_y":5.0,"duration_ms":500}),
                vec!["x", "y", "to_x", "to_y"],
            ),
            (
                json!({"kind":"type","text":"äöüß 🦦\n","text_method":"clipboard"}),
                vec!["text"],
            ),
            (
                json!({"kind":"paste","text":"äöüß 🦦\n","restore_clipboard":false}),
                vec!["text"],
            ),
            (
                json!({"kind":"key","keys":["ctrl","l"],"key_scope":"app"}),
                vec!["keys"],
            ),
            (
                json!({"kind":"semantic_set_value","handle_id":"handle","text":"new","expected_text":"old"}),
                vec!["handle_id", "text"],
            ),
            (
                json!({"kind":"semantic_click","handle_id":"handle","action_name":"click"}),
                vec!["handle_id", "action_name"],
            ),
            (
                json!({"kind":"semantic_focus","handle_id":"ax-fixture"}),
                vec!["handle_id"],
            ),
            (json!({"kind":"wait","ms":80}), vec!["ms"]),
        ];
        let schema = action_schema();
        assert_eq!(schema["anyOf"].as_array().unwrap().len(), samples.len());
        for (sample, required) in samples {
            let parsed: Action = serde_json::from_value(sample.clone()).unwrap();
            let branch = variant(&schema, parsed.name());
            assert_eq!(branch["additionalProperties"], false);
            assert_eq!(
                branch["properties"]
                    .as_object()
                    .unwrap()
                    .keys()
                    .collect::<Vec<_>>(),
                sample.as_object().unwrap().keys().collect::<Vec<_>>()
            );
            let mut all_required = vec!["kind"];
            all_required.extend(required);
            assert_eq!(branch["required"], json!(all_required));
            for field in &all_required {
                let mut missing = sample.clone();
                missing.as_object_mut().unwrap().remove(*field);
                assert!(serde_json::from_value::<Action>(missing).is_err());
            }
            let mut unknown = sample;
            unknown["not_an_action_field"] = json!(true);
            assert!(serde_json::from_value::<Action>(unknown).is_err());
        }
    }

    #[test]
    fn omitted_defaults_and_nullable_options_preserve_serde_contract() {
        assert!(matches!(
            serde_json::from_value::<Action>(json!({"kind":"type","text":""})).unwrap(),
            Action::Type {
                text_method: TextMethod::Auto,
                ..
            }
        ));
        assert!(matches!(
            serde_json::from_value::<Action>(json!({"kind":"paste","text":""})).unwrap(),
            Action::Paste {
                restore_clipboard: true,
                ..
            }
        ));
        assert!(matches!(
            serde_json::from_value::<Action>(json!({"kind":"key","keys":["Return"]})).unwrap(),
            Action::Key {
                key_scope: KeyScope::App,
                ..
            }
        ));
        assert!(matches!(
            serde_json::from_value::<Action>(json!({"kind":"scroll","x":0,"y":0})).unwrap(),
            Action::Scroll { dx: 0, dy: 0, .. }
        ));
        let schema = action_schema();
        for (kind, field, sample, nullable_type) in [
            (
                "click",
                "button",
                json!({"kind":"click","x":0,"y":0,"button":null}),
                "string",
            ),
            (
                "click",
                "count",
                json!({"kind":"click","x":0,"y":0,"count":null}),
                "integer",
            ),
            (
                "drag",
                "duration_ms",
                json!({"kind":"drag","x":0,"y":0,"to_x":1,"to_y":1,"duration_ms":null}),
                "integer",
            ),
            (
                "semantic_set_value",
                "expected_text",
                json!({"kind":"semantic_set_value","handle_id":"h","text":"","expected_text":null}),
                "string",
            ),
        ] {
            assert!(serde_json::from_value::<Action>(sample).is_ok());
            assert_eq!(
                variant(&schema, kind)["properties"][field]["type"],
                json!([nullable_type, "null"])
            );
        }
        for sample in [
            json!({"kind":"type","text":"","text_method":null}),
            json!({"kind":"paste","text":"","restore_clipboard":null}),
            json!({"kind":"key","keys":["Return"],"key_scope":null}),
            json!({"kind":"scroll","x":0,"y":0,"dx":null}),
        ] {
            assert!(serde_json::from_value::<Action>(sample).is_err());
        }
        for key in ["Return", "ESC", "Control", "Page_Up", "ä"] {
            assert!(keyboard::named_evdev(key).is_ok());
        }
        assert!(variant(&schema, "key")["properties"]["keys"]["items"]
            .get("enum")
            .is_none());
    }

    #[test]
    fn original_r3_rejections_cannot_be_mistaken_for_valid_action_shapes() {
        // Saved original positions47/66: top-level window_id never supplies a
        // Focus action member and must not be copied into a Key action.
        let mut focus =
            json!({"observation_id":"obs-r3","window_id":261,"actions":[{"kind":"focus"}]});
        let error = serde_json::from_value::<ActArgs>(focus.clone())
            .err()
            .expect("The saved malformed action must be rejected")
            .to_string();
        assert!(error.contains("missing field `window_id`"));
        focus["actions"][0]["window_id"] = json!(261);
        assert!(serde_json::from_value::<ActArgs>(focus).is_ok());
        let mut key = json!({"observation_id":"obs-r3","window_id":262,"actions":[{"kind":"key","window_id":262,"keys":["ctrl","l"],"key_scope":"app"}]});
        let error = serde_json::from_value::<ActArgs>(key.clone())
            .err()
            .expect("The saved malformed action must be rejected")
            .to_string();
        assert!(error.contains("unknown field `window_id`"));
        key["actions"][0]
            .as_object_mut()
            .unwrap()
            .remove("window_id");
        assert!(serde_json::from_value::<ActArgs>(key).is_ok());
        let schema = action_schema();
        assert_eq!(
            variant(&schema, "focus")["required"],
            json!(["kind", "window_id"])
        );
        assert!(variant(&schema, "key")["properties"]
            .get("window_id")
            .is_none());
    }

    #[test]
    fn published_union_stays_below_pinned_client_compaction_budget() {
        let catalog = tools();
        let tool = catalog
            .as_array()
            .unwrap()
            .iter()
            .find(|v| v["name"] == "desktop_act")
            .unwrap();
        assert_eq!(
            tool["inputSchema"]["properties"]["actions"]["items"],
            action_schema()
        );
        let size = serde_json::to_vec(&tool["inputSchema"]).unwrap().len();
        assert!(size < 5000, "ordinary MCP schema is {size} bytes; pinned budget is 5000");
    }
}

fn tools() -> Value {
    let action = action_schema();
    let crop = json!({"type":"object","properties":{"x":{"type":"integer","minimum":0},"y":{"type":"integer","minimum":0},"width":{"type":"integer","minimum":1},"height":{"type":"integer","minimum":1}},"required":["x","y","width","height"]});
    let mut catalog = json!([
      {"name":"desktop_status","description":"Persistent Niri desktop actor status, active/preparing task, queued batches, cancel epoch and escape_policy=cancel_current_task. Physical Escape cancels current work without a persistent latch or unlock ceremony; idle Escape only invalidates observations. Stopped task IDs and stale observations cannot resume. Reports independent capture attempt/failure evidence and shared resume_input_readiness; capture_available=false alone is not capture failure. Startup capture_not_attempted=true is expected. Readiness is only a snapshot, not user permission or actor release. Advertises global_keyboard routing_revision=2 only when this software implements scoped owned-uinput routing; this is not a live device/UI-success attestation. Does not capture or act.","inputSchema":{"type":"object","properties":{}}},
      {"name":"desktop_windows","description":"Read actual Niri windows, outputs and workspaces. Window layout may lack global app bounds; never invent bounds.","inputSchema":{"type":"object","properties":{}}},
      {"name":"desktop_observe","description":"Capture one actual laptop output via grim at scale1, plus Niri identities. Startup capture_not_attempted=true is expected and permits this first read-only observation. Input always requires accepted fresh observation and action_ready; prior successful capture is not a prerequisite for authorized startup resume. Optional crop uses full-output screenshot pixels x/y/width/height; action x/y then use local pixels of the displayed crop. Core translates crop origin; NEVER add compositor output origin. Actual PNG size can differ from Niri logical size by rounding. Observation expires in60seconds; identity/geometry and fresh target-region guards still run. Observe after focus/workspace/layout changes. include_image=false returns private PNG reference only.","inputSchema":{"type":"object","properties":{"output":{"type":"string"},"include_image":{"type":"boolean"},"crop":crop}}},
      {"name":"desktop_semantic","description":"Read fresh Cua AT-SPI elements for a Niri window. Maps only unique actual PID+title; synthetic Cua IDs are never guessed. Query filters returned elements. Accessibility bounds are app-local and NOT screenshot coordinates; do not directly click them without calibrated mapping. Limited/root-only trees require visual fallback.","inputSchema":{"type":"object","properties":{"window_id":{"type":"integer"},"query":{"type":"string"},"max_elements":{"type":"integer"},"max_depth":{"type":"integer"}},"required":["window_id"]}},
      {"name":"desktop_semantic_direct","description":"Read exact-window AT-SPI labels/tree even during ordinary physical activity or held controls. Set background=true for direct click/set_value on a visible target without moving the pointer or requesting focus; unrelated physical input does not invalidate those handles. Original object/context, target output and Escape are still checked; an app may itself change focus after an action. Returns original/current input generation and explicit input/action readiness. Non-actionable or incomplete snapshots issue no handles (focus/click/set_value=false); obtain a fresh complete snapshot after controls release. Only daemon-owned handles from actionable complete snapshots may be used with desktop_act semantic_set_value, semantic_click or standalone semantic_focus. focusable/FOCUSED flags attest element keyboard focus separately from Niri window focus; semantic_focus requires fresh complete semantics and verifies FOCUSED on the same exact object before returning focus_verified. Reobserve and requery semantics before later keyboard input. No raw object/index/Cua tokens and no pixel fallback. Native GTK candidates need live acceptance; missing/incomplete bridges use visual typed actions.","inputSchema":{"type":"object","properties":{"window_id":{"type":"integer"},"query":{"type":"string"},"max_elements":{"type":"integer"},"max_depth":{"type":"integer"},"background":{"type":"boolean","description":"Object-targeted click/edit on a visible target window without requesting focus or moving the pointer; no pointer, clipboard, keyboard or focus fallback."},"include_text_selection":{"type":"boolean","description":"Opt-in bounded selection readback for at most one showing enabled FOCUSED unprotected Text object; unicode codepoint [start,end). Explicit available/unsupported/error, no input authority; check fresh generation, complete tree and text identity separately."}},"required":["window_id"]}},
      {"name":"desktop_act","description":"Standalone launch_app(app:zed|chrome), editor_open_project(project:jarvis) and editor_open_file(project:jarvis,relative_path) spawn pinned isolated apps on the observed active output. OS spawn ACK is not UI success; uncertain spawn requires verification, no auto retry. Single-writer typed desktop actions against fresh observation. With a live task indicator, every call must include that exact task_id; old, ended or mismatched task identities are refused, while multiple batches in the same live scope are allowed. Rejects changed focus/geometry/scaling or changed pixels near pointer targets. x/y are local to displayed screenshot/crop. Every move/click/scroll/drag requires the displayed view to be at most1200 pixels on each axis; an oversized view rejects the whole batch before any input, queue admission or device preparation. Capture a fresh target crop and use its crop-local coordinates. Full images remain usable for overview, focus, keys and semantics. Focus must be standalone. close_window(window_id) is standalone with the same top-level window_id, exact fresh focused target/title/output; caller proves task ownership and safe saved state. Explicit close request acknowledgement is not window disappearance; inspect fresh inventory and any save modal. No PID kill or blind cleanup. Unknown or misplaced fields for an action kind reject the whole batch before input; restore_clipboard belongs only to paste, while type always preserves the prior selection. By default observe_after=true returns a new after_observation ID and image in this same response after dispatch/release and a bounded80ms defaultsettle wait; settle_ms=0..1000 can adjust. Use it for the next act and inspect expected result. A slow/unchanged frame needs another observation/semantic check, not repetition of toggle input. include_image=false omits its image. Acknowledgement is not UI success. Type text_method=auto uses clipboard for known Electron IDs (code/T3) and >1000-character text, keyboard for shorter text in other apps; explicit keyboard/clipboard are available. Electron wtype Unicode is unreliable on this laptop. Clipboard preserves supported text/rich app payloads and original Chromium provenance in bounded RAM from one offer, normalizes duplicate MIME names, omits SAVE_TARGETS and SAME_APP GTK_TEXT_BUFFER_CONTENTS transport markers, preserves serialized GTK rich text, and keeps a separate source holder across actor restarts. Unsupported/sensitive/oversized formats refuse before replacement. Own-source check runs after layout/keymap/window validation and before the first paste modifier press; ownership can still change between reply and input because Wayland has no atomic selection-check-and-paste. No restore after takeover/cancel/ownership loss. Scroll dx/dy are discrete wheel steps (integer -100..100), not pixels; positive dx moves right and positive dy moves down. Smooth-scroll animation needs another fresh observation/settle check before reusing visual targets. Keys are modifiers first e.g.[ctrl,l],[Return]. Explicit key_scope=compositor and Super/meta/logo chords use an owned persistent direct-uinput device because this Niri25.11 Wayland virtual keyboard bypasses compositor bindings; Ctrl/app chords retain the Wayland transport. A fresh proxy check requires backend routing_revision=2 and binds session/epoch/observation before forwarding a global batch; an older backend is refused. Missing permission/takeover monitor/compositor device-open evidence refuses the complete batch before input. Creating the own device is a capability side effect. A kernel input acknowledgement does not verify Niri/UI acceptance; inspect the fresh result. No automatic input fallback. Direct semantic_set_value(handle_id,text,expected_text optional) replaces exact editable contents; semantic_click(handle_id,action_name from direct tree) invokes only AT-SPI named action. Both freshly revalidate context and have no input fallback. Standalone semantic_focus(handle_id) uses Component.GrabFocus, then repeats exact object/window/context validation and freshly queries FOCUSED; acceptance alone is not focus_verified. It sends no keyboard input or fallback. Reobserve and requery the exact focused object before keyboard input. Batch stable edits/shortcuts when intermediate states cannot invalidate later targets; changed-target actions need fresh observation.","inputSchema":{"type":"object","properties":{"observation_id":{"type":"string"},"window_id":{"type":"integer"},"task_id":{"type":"string"},"timeout_ms":{"type":"integer","minimum":100,"maximum":120000,"description":"Whole batch deadline; release cleanup follows timeout."},"observe_after":{"type":"boolean","default":true},"include_image":{"type":"boolean","default":true},"settle_ms":{"type":"integer","default":80,"minimum":0,"maximum":1000,"description":"Post-action wait before capture; reobserve slow states, never replay blindly."},"actions":{"type":"array","items":action,"minItems":1,"maxItems":32}},"required":["observation_id","actions","task_id"]}},
      {"name":"desktop_cancel","description":"Priority epoch cancellation independent of actor lock. Pending batches stop; held buttons release. Already-dispatched effects remain. Wait for desktop_status active=null and actor_release_confirmed=true for full release.","inputSchema":{"type":"object","properties":{}}},
      {"name":"desktop_takeover","description":"Explicit human desktop takeover, persists its cause across backend restarts and latches refusal of future actions and cancels queued/active automation. Physical Escape cancels only queued/preparing/running work or a live task-indicator scope without latching future tasks; idle Escape only invalidates stale observations. Ordinary input invalidates stale observations and releases a conflicting batch without latching takeover. Wait active=null and actor_release_confirmed=true for full release. desktop_resume then fresh observation is required.","inputSchema":{"type":"object","properties":{}}},
      {"name":"desktop_recover_release","description":"Explicit bounded recovery of unconfirmed release/receipt on existing owned actuators only. Refuses if writer busy, active task or queue nonempty. Sends only releases of owned held buttons/keys and receipt sync, no press/move/device creation/action replay, no epoch/latch/cause reset, no automatic resume. May finalize effects of already-held input. Default1500ms, maximum2000ms shared budget. Failure remains unconfirmed; original last_result preserved. Success invalidates old observations/semantic handles and requires fresh capture; inspect status queue and follow human return-of-control policy before resume.","inputSchema":{"type":"object","properties":{"timeout_ms":{"type":"integer","minimum":200,"maximum":2000,"default":1500}},"additionalProperties":false}},
      {"name":"desktop_resume","description":"Backend startup is always latched and preserves prior explicit/legacy takeover causes. An active transition requires expected_epoch=status.epoch and expected_session_id=status.session_id for the stop the user has authorized resuming (not its historical reason.session_id); the proxy requires backend resume_binding_revision=1 and uses a new internal bound wire method so an old replacement backend cannot ignore the fence fields. An idle/unlatched older backend may return only a read-only no-op snapshot, never receive an unbound resume; stale binding refuses, never blindly refresh/retry after a new stop. Physical Escape cancels only current task scopes and batches, without requiring resume for a new task. Canceled task IDs and stale observations remain unusable. Legacy persisted physical_escape migrates to the ordinary backend_startup gate on restart. The old task and queue never resume. Explicit takeover still requires the user's return of control. No successful capture is needed before authorized startup resume; inspect resume_input_readiness, then obtain a fresh observation before input. If already unlatched with idle/released actor and empty queue, empty arguments return already_resumed without epoch/generation/cache changes or quiet checks. last_reason is historical; reason is null when marker_active=false.","inputSchema":{"type":"object","properties":{"expected_epoch":{"type":"integer","minimum":1},"expected_session_id":{"type":"string","minLength":1}},"additionalProperties":false}}
    ]);
    // These hints describe desktop/app effects; private read caches do not
    // grant input. The managed client policy authorizes only this server.
    for tool in catalog
        .as_array_mut()
        .expect("Static MCP tool catalog is an array")
    {
        if matches!(
            tool["name"].as_str(),
            Some("desktop_observe" | "desktop_act")
        ) {
            tool["inputSchema"]["properties"]["compact"] = json!({"type":"boolean","default":false,"description":"Inventory subset with explicit omissions; desktop_windows or detailed=true for full data."});
            tool["inputSchema"]["properties"]["detailed"] = json!({"type":"boolean","default":false,"description":"Full metadata if true; default abbreviates static prose/modes only."});
            let description = tool["description"].as_str().unwrap_or("");
            tool["description"] = json!(format!("{description} MCP metadata is compact by default; detailed=true returns full original metadata. desktop_windows retains full display mode catalogs."));
        }
        let (read_only, destructive, open_world) = match tool["name"].as_str() {
            Some("desktop_status") => (true, false, false),
            Some(
                "desktop_windows"
                | "desktop_observe"
                | "desktop_semantic"
                | "desktop_semantic_direct",
            ) => (true, false, true),
            Some("desktop_recover_release") => (false, true, true),
            Some("desktop_cancel" | "desktop_takeover" | "desktop_resume") => (false, false, false),
            _ => (false, true, true),
        };
        tool["annotations"] = json!({
            "readOnlyHint": read_only,
            "destructiveHint": destructive,
            // Cancel/takeover advance the epoch; resume may be an explicit no-op.
            "idempotentHint": read_only,
            "openWorldHint": open_world,
        });
    }
    catalog
}

fn socket_path() -> PathBuf {
    env::var_os("WEASEL_COMPUTER_USE_SOCKET")
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            PathBuf::from(format!(
                "/run/user/{}/weasel-computer-use/desktop.sock",
                uid()
            ))
        })
}

fn daemon_call(socket: &Path, tool: &str, args: &Value) -> R<Value> {
    daemon_call_owned(socket, tool, args, None)
}

fn proxy_needs_global_guard(tool: &str, args: &Value) -> R<bool> {
    if tool != "desktop_act" {
        return Ok(false);
    }
    let actions = args["actions"]
        .as_array()
        .ok_or("desktop_act actions must be an array")?;
    let mut global = false;
    for item in actions {
        // Validate every action locally, including against an older actor that
        // could otherwise silently ignore a misplaced policy field.
        let action: Action = serde_json::from_value(item.clone())
            .map_err(|e| format!("Invalid action before proxy dispatch: {e}"))?;
        if let Action::Key { keys, key_scope } = action {
            global |= uses_global_keyboard(&keys, key_scope)?;
        }
    }
    Ok(global)
}
fn validate_global_backend_guard(
    guard: &GlobalBackendGuard,
    session: &str,
    epoch: u64,
    observation_id: &str,
) -> R<()> {
    if guard.routing_revision != GLOBAL_ROUTING_REVISION
        || guard.session_id != session
        || guard.epoch != epoch
        || !observation_id.starts_with(&format!("obs-{session}-"))
    {
        return Err("Global keyboard backend changed or observation is from another session/epoch; no batch input sent. Reconnect and obtain a fresh observation.".into());
    }
    Ok(())
}
fn guarded_global_arguments(args: &Value, status_response: &Value) -> R<Value> {
    if status_response["isError"] == true {
        return Err(
            "Global keyboard capability query failed; complete batch refused before dispatch"
                .into(),
        );
    }
    let status: Value = serde_json::from_str(
        status_response["content"][0]["text"]
            .as_str()
            .ok_or("Global keyboard status response has no JSON metadata")?,
    )
    .map_err(|_| "Global keyboard status metadata invalid")?;
    if status["interface_revision"].as_u64() != Some(GLOBAL_ROUTING_REVISION as u64)
        || status["global_keyboard"]["routing_revision"].as_u64()
            != Some(GLOBAL_ROUTING_REVISION as u64)
        || status["global_keyboard"]["transport"] != "owned_uinput"
        || status["global_keyboard"]["super_implies_compositor"] != true
    {
        return Err("Backend does not advertise global keyboard routing revision2. Complete batch refused; restart/reconnect the declared backend before compositor shortcuts. No Wayland fallback.".into());
    }
    let guard = GlobalBackendGuard {
        routing_revision: GLOBAL_ROUTING_REVISION,
        session_id: status["session_id"]
            .as_str()
            .ok_or("Global keyboard status session missing")?
            .into(),
        epoch: status["epoch"]
            .as_u64()
            .ok_or("Global keyboard status epoch missing")?,
    };
    let observation_id = args["observation_id"]
        .as_str()
        .ok_or("Global action observation ID missing")?;
    validate_global_backend_guard(&guard, &guard.session_id, guard.epoch, observation_id)?;
    let mut guarded = args.clone();
    guarded.as_object_mut().ok_or("Global action arguments must be an object")?.insert("expected_global_backend".into(), json!({"routing_revision":guard.routing_revision,"session_id":guard.session_id,"epoch":guard.epoch}));
    Ok(guarded)
}
fn daemon_call_owned(
    socket: &Path,
    tool: &str,
    args: &Value,
    cancel: Option<Arc<AtomicBool>>,
) -> R<Value> {
    if tool == "desktop_resume" {
        let status = daemon_call_transport(
            socket,
            "desktop_status",
            &json!({}),
            cancel.clone(),
            Duration::from_secs(2),
        )?;
        if let Some(noop) = resume_proxy_preflight(args, &status)? {
            return Ok(noop);
        }
        // A replacement old backend cannot ignore binding fields: it does not
        // implement this new wire method and must refuse Unknown tool.
        return daemon_call_transport(
            socket,
            "desktop_resume_bound",
            args,
            cancel,
            Duration::from_secs(135),
        );
    }
    if proxy_needs_global_guard(tool, args)? {
        let started = Instant::now();
        let budget = Duration::from_millis(
            args["timeout_ms"]
                .as_u64()
                .unwrap_or(30000)
                .clamp(100, 120000),
        );
        let status = daemon_call_transport(
            socket,
            "desktop_status",
            &json!({}),
            cancel.clone(),
            budget.min(Duration::from_secs(2)),
        )?;
        let mut guarded = guarded_global_arguments(args, &status)?;
        let remaining = budget.saturating_sub(started.elapsed());
        if remaining < Duration::from_millis(100) {
            return Err(
                "Global capability preflight consumed the batch deadline; no action request sent"
                    .into(),
            );
        }
        guarded["timeout_ms"] = json!(remaining.as_millis() as u64);
        return daemon_call_transport(
            socket,
            tool,
            &guarded,
            cancel,
            remaining + Duration::from_secs(15),
        );
    }
    daemon_call_transport(
        socket,
        tool,
        args,
        cancel,
        if matches!(tool, "desktop_cancel" | "desktop_takeover") {
            Duration::from_secs(2)
        } else if tool == "desktop_recover_release" {
            Duration::from_secs(3)
        } else {
            Duration::from_secs(135)
        },
    )
}
fn daemon_call_transport(
    socket: &Path,
    tool: &str,
    args: &Value,
    cancel: Option<Arc<AtomicBool>>,
    read_timeout: Duration,
) -> R<Value> {
    if cancel.as_ref().is_some_and(|c| c.load(Ordering::SeqCst)) {
        return Err("MCP request canceled before dispatch".into());
    }
    let mut stream=UnixStream::connect(socket).map_err(|e|format!("Desktop daemon unavailable at {}: {e}. Start serve once; do not fall back to blind actions.",socket.display()))?;
    stream
        .set_read_timeout(Some(read_timeout))
        .map_err(|e| e.to_string())?;
    let done = Arc::new(AtomicBool::new(false));
    let monitor = if let Some(cancel) = cancel {
        let watch = stream.try_clone().map_err(|e| e.to_string())?;
        let done = done.clone();
        Some(thread::spawn(move || {
            while !done.load(Ordering::SeqCst) {
                if cancel.load(Ordering::SeqCst) {
                    let _ = watch.shutdown(std::net::Shutdown::Both);
                    break;
                }
                thread::sleep(Duration::from_millis(5));
            }
        }))
    } else {
        None
    };
    let result = (|| {
        writeln!(stream, "{}", json!({"tool":tool,"arguments":args})).map_err(|e| e.to_string())?;
        let mut line = String::new();
        BufReader::new(stream)
            .read_line(&mut line)
            .map_err(|e| e.to_string())?;
        if line.is_empty() {
            return Err("MCP request canceled/disconnected while backend was executing; inspect desktop_status last_result and reobserve before continuing".into());
        }
        serde_json::from_str(&line).map_err(|e| e.to_string())
    })();
    done.store(true, Ordering::SeqCst);
    if let Some(monitor) = monitor {
        let _ = monitor.join();
    }
    result
}

fn serve(socket: &Path) -> R<()> {
    // Different client socket names must not create competing physical actors.
    // flock remains held for the entire daemon lifetime and releases on exit.
    let global_dir = PathBuf::from(format!("/run/user/{}/weasel-computer-use", uid()));
    private_dir(&global_dir)?;
    let global_lock = fs::OpenOptions::new()
        .create(true)
        .write(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .mode(0o600)
        .open(global_dir.join("actuator.lock"))
        .map_err(|e| format!("Global actuator lock unavailable: {e}"))?;
    if global_lock.metadata().map_err(|e| e.to_string())?.uid() != uid() {
        return Err("Global actuator lock must be owned by current user".into());
    }
    fs::set_permissions(
        global_dir.join("actuator.lock"),
        fs::Permissions::from_mode(0o600),
    )
    .map_err(|e| e.to_string())?;
    if unsafe { libc::flock(global_lock.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0 {
        return Err("A desktop actor is already running for this user, possibly through another socket. Do not create a second physical writer.".into());
    }
    let dir = socket.parent().ok_or("Socket needs parent directory")?;
    private_dir(dir)?;
    if socket.exists() {
        let m = fs::symlink_metadata(socket).map_err(|e| e.to_string())?;
        if !m.file_type().is_socket() || m.uid() != uid() {
            return Err("Refusing to replace non-owned socket path".into());
        }
        if UnixStream::connect(socket).is_ok() {
            return Err("Desktop daemon already listening".into());
        }
        fs::remove_file(socket).map_err(|e| e.to_string())?;
    }
    let listener = UnixListener::bind(socket).map_err(|e| e.to_string())?;
    fs::set_permissions(socket, fs::Permissions::from_mode(0o600)).map_err(|e| e.to_string())?;
    let images = dir.join("images");
    private_dir(&images)?;
    let log_path = dir.join("events.jsonl");
    let file = fs::OpenOptions::new()
        .create(true)
        .append(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .mode(0o600)
        .open(&log_path)
        .map_err(|e| e.to_string())?;
    fs::set_permissions(log_path, fs::Permissions::from_mode(0o600)).map_err(|e| e.to_string())?;
    let session_id = format!(
        "{}-{}",
        std::process::id(),
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos()
    );
    let takeover_marker = takeover::Latch::startup(global_dir.join("takeover.json"), &session_id)?;
    let state = Arc::new(State {
        started: Instant::now(),
        session_id,
        epoch: AtomicU64::new(1),
        serial: AtomicU64::new(1),
        queued: AtomicUsize::new(0),
        takeover: AtomicBool::new(true),
        takeover_marker: Mutex::new(takeover_marker),
        last_human_ms: AtomicU64::new(0),
        input_policy: input_policy::Policy::new(),
        human_monitor: Mutex::new(json!({"available":false,"status":"starting"})),
        release_confirmed: AtomicBool::new(true),
        capture_available: AtomicBool::new(false),
        capture_attempts: AtomicU64::new(0),
        capture_failure: Mutex::new(None),
        active: Mutex::new(Value::Null),
        task_scopes: Mutex::new(HashMap::new()),
        task_scope_ids: Mutex::new(HashSet::new()),
        task_scope_gate: Mutex::new(()),
        last_result: Mutex::new(Value::Null),
        observations: Mutex::new(HashMap::new()),
        reference_cache: ReferenceCache::default(),
        semantic_targets: Mutex::new(HashMap::new()),
        actor: Mutex::new(None),
        keyboard: Mutex::new(None),
        global_keyboard: Mutex::new(None),
        image_dir: images,
        log: Mutex::new(file),
    });
    let monitor = state.clone();
    thread::spawn(move || human_monitor(monitor));
    eprintln!("weasel computer use daemon listening {}", socket.display());
    for stream in listener.incoming() {
        let mut stream = match stream {
            Ok(s) => s,
            Err(e) => {
                eprintln!("Socket accept failed: {e}");
                continue;
            }
        };
        let state = state.clone();
        thread::spawn(move || {
            let epoch = state.epoch.load(Ordering::SeqCst);
            let clone = match stream.try_clone() {
                Ok(s) => s,
                Err(_) => return,
            };
            let mut line = String::new();
            if BufReader::new(clone.take(MAX_REQUEST))
                .read_line(&mut line)
                .is_err()
            {
                return;
            }
            let cancel = Arc::new(AtomicBool::new(false));
            CURRENT_CANCEL_FLAG.with(|f| *f.borrow_mut() = Some(cancel.clone()));
            let done = Arc::new(AtomicBool::new(false));
            let watch = match stream.try_clone() {
                Ok(s) => s,
                Err(_) => return,
            };
            let monitor_cancel = cancel.clone();
            let monitor_done = done.clone();
            let monitor = thread::spawn(move || {
                while !monitor_done.load(Ordering::SeqCst) {
                    let mut fd = libc::pollfd {
                        fd: watch.as_raw_fd(),
                        events: libc::POLLRDHUP,
                        revents: 0,
                    };
                    let result = unsafe { libc::poll(&mut fd, 1, 5) };
                    if result > 0
                        && fd.revents
                            & (libc::POLLRDHUP | libc::POLLHUP | libc::POLLERR | libc::POLLNVAL)
                            != 0
                    {
                        monitor_cancel.store(true, Ordering::SeqCst);
                        break;
                    }
                    if result < 0 {
                        monitor_cancel.store(true, Ordering::SeqCst);
                        break;
                    }
                }
            });
            let result = (|| {
                let req: Value = serde_json::from_str(&line).map_err(|e| e.to_string())?;
                handle(
                    &state,
                    req["tool"].as_str().ok_or("Missing tool name")?,
                    &req["arguments"],
                    epoch,
                )
            })();
            done.store(true, Ordering::SeqCst);
            let _ = monitor.join();
            CURRENT_CANCEL_FLAG.with(|f| *f.borrow_mut() = None);
            let value = result.unwrap_or_else(error_result);
            let _ = writeln!(stream, "{value}");
        });
    }
    Ok(())
}

fn mcp(socket: &Path) -> R<()> {
    use mcp_cancel_registry::{Registry, RequestKey, RequestKind};
    let output = Arc::new(Mutex::new(io::stdout()));
    let stdin = io::stdin();
    let mut running = Vec::new();
    // One registry and lock per stdio connection. Nothing persists across a
    // client reconnect and no registry cancellation changes the actor epoch.
    let registry = Arc::new(Mutex::new(Registry::new()));
    for line in stdin.lock().lines() {
        let line = line.map_err(|e| e.to_string())?;
        if line.trim().is_empty() {
            continue;
        }
        let req: Value = serde_json::from_str(&line).map_err(|e| e.to_string())?;
        let Some(id) = req.get("id").cloned() else {
            if req["method"] == "notifications/cancelled" {
                // Missing, malformed or oversized IDs cannot alias a real ID.
                if let Ok(key) = RequestKey::parse(&req["params"]["requestId"]) {
                    registry
                        .lock()
                        .map_err(|_| "MCP request registry lock poisoned")?
                        .cancel(key);
                }
            }
            continue;
        };
        let request_key = match RequestKey::parse(&id) {
            Ok(key) => key,
            Err(refusal) => {
                let response = json!({"jsonrpc":"2.0","id":null,"error":{"code":refusal.code(),"message":refusal.message()}});
                let mut stdout = output.lock().map_err(|_| "MCP output lock poisoned")?;
                writeln!(*stdout, "{response}").map_err(|e| e.to_string())?;
                stdout.flush().map_err(|e| e.to_string())?;
                continue;
            }
        };
        let kind = RequestKind::from_request(&req);
        let worker_id = id.clone();
        let worker_registry = registry.clone();
        let worker_key = request_key.clone();
        let out = output.clone();
        let path = socket.to_owned();
        let admission = registry
            .lock()
            .map_err(|_| "MCP request registry lock poisoned")?
            .admit_then(request_key, kind, move |cancel| {
                // This closure is NEVER invoked for pre-canceled, duplicate or
                // exhausted action IDs. Refusal is before thread/daemon contact.
                thread::spawn(move || {
                    let method = req["method"].as_str().unwrap_or("");
                    let result = match method {
                        "initialize" => Ok(json!({"protocolVersion":req["params"]["protocolVersion"].as_str().unwrap_or("2024-11-05"),"capabilities":{"tools":{"listChanged":false}},"serverInfo":{"name":"weasel-computer-use","version":"0.1.0"}})),
                        "ping" => Ok(json!({})),
                        "tools/list" => Ok(json!({"tools":tools()})),
                        "tools/call" => {
                            let tool = req["params"]["name"].as_str().unwrap_or("");
                            let arguments = req["params"].get("arguments").cloned().unwrap_or_else(|| json!({}));
                            daemon_call_owned(&path, tool, &arguments, cancel)
                                .map(|result| mcp_response::present(tool, &arguments, result))
                        },
                        _ => Err(format!("Unsupported MCP method {method}")),
                    };
                    // Completion and cancellation lookup share ONE lock. Keep
                    // the terminal ID so a late Cancel cannot poison ID reuse.
                    if let Ok(mut calls) = worker_registry.lock() {
                        calls.finish(&worker_key);
                    }
                    let response = match result {
                        Ok(result) => json!({"jsonrpc":"2.0","id":worker_id,"result":result}),
                        Err(message) => json!({"jsonrpc":"2.0","id":worker_id,"error":{"code":-32603,"message":message}}),
                    };
                    if let Ok(mut stdout) = out.lock() {
                        let _ = writeln!(*stdout, "{response}");
                        let _ = stdout.flush();
                    }
                })
            });
        match admission {
            Ok(worker) => running.push(worker),
            Err(refusal) => {
                let response = json!({"jsonrpc":"2.0","id":id,"error":{"code":refusal.code(),"message":refusal.message()}});
                let mut stdout = output.lock().map_err(|_| "MCP output lock poisoned")?;
                writeln!(*stdout, "{response}").map_err(|e| e.to_string())?;
                stdout.flush().map_err(|e| e.to_string())?;
            }
        }
        running.retain(|h| !h.is_finished());
    }
    registry
        .lock()
        .map_err(|_| "MCP request registry lock poisoned")?
        .cancel_owned_pending();
    for h in running {
        let _ = h.join();
    }
    Ok(())
}

fn main() {
    let mut args = env::args().skip(1);
    let command = args.next().unwrap_or_else(|| "--help".into());
    let socket = socket_path();
    let result = match command.as_str() {
        "__clipboard_helper" => clipboard::helper_main(),
        "__atspi_helper" => {
            let mut bytes = Vec::new();
            io::stdin()
                .take(2 * 1024 * 1024)
                .read_to_end(&mut bytes)
                .map_err(|e| e.to_string())
                .and_then(|_| serde_json::from_slice(&bytes).map_err(|e| e.to_string()))
                .and_then(atspi::run)
                .map(|v| println!("{v}"))
        }
        "serve" => serve(&socket),
        "mcp" => mcp(&socket),
        "call" => {
            let tool = args.next().unwrap_or_default();
            let raw = args.next().unwrap_or_else(|| "{}".into());
            serde_json::from_str(&raw)
                .map_err(|e| e.to_string())
                .and_then(|value| daemon_call(&socket, &tool, &value))
                .map(|v| println!("{v}"))
        }
        "--help" | "help" => {
            println!("weasel-computer-use-core serve | mcp | call TOOL JSON\nSocket: WEASEL_COMPUTER_USE_SOCKET or /run/user/UID/weasel-computer-use/desktop.sock\nDaemon has no model/API/credential access. One persistent physical desktop writer; explicit cancel/takeover.");
            Ok(())
        }
        _ => Err("Use --help".into()),
    };
    if let Err(e) = result {
        eprintln!("{e}");
        std::process::exit(1);
    }
}

#[cfg(test)]
mod regression {
    use super::*;
    #[test]
    fn restart_latch_and_resume_guards_without_desktop() {
        let dir = env::temp_dir().join(format!("weasel-resume-regression-{}", std::process::id()));
        private_dir(&dir).unwrap();
        let marker_path = dir.join("takeover.json");
        let session = "offline-session-1";
        let file = fs::OpenOptions::new()
            .create(true)
            .write(true)
            .mode(0o600)
            .open(dir.join("events.jsonl"))
            .unwrap();
        let mut state = State {
            started: Instant::now(),
            session_id: session.into(),
            epoch: AtomicU64::new(1),
            serial: AtomicU64::new(1),
            queued: AtomicUsize::new(0),
            takeover: AtomicBool::new(true),
            takeover_marker: Mutex::new(
                takeover::Latch::startup(marker_path.clone(), session).unwrap(),
            ),
            last_human_ms: AtomicU64::new(0),
            input_policy: input_policy::Policy::test_ready(),
            human_monitor: Mutex::new(Value::Null),
            release_confirmed: AtomicBool::new(true),
            capture_available: AtomicBool::new(false),
            capture_attempts: AtomicU64::new(0),
            capture_failure: Mutex::new(None),
            active: Mutex::new(Value::Null),
            task_scopes: Mutex::new(HashMap::new()),
            task_scope_ids: Mutex::new(HashSet::new()),
            task_scope_gate: Mutex::new(()),
            last_result: Mutex::new(Value::Null),
            observations: Mutex::new(HashMap::new()),
            reference_cache: ReferenceCache::default(),
            semantic_targets: Mutex::new(HashMap::new()),
            actor: Mutex::new(None),
            keyboard: Mutex::new(None),
            global_keyboard: Mutex::new(None),
            image_dir: dir.clone(),
            log: Mutex::new(file),
        };
        assert!(handle(&state, "desktop_resume", &json!({}), 1)
            .unwrap_err()
            .contains("startup quiet interval"));
        state.started = Instant::now() - Duration::from_millis(350);
        assert!(handle(&state, "desktop_resume", &json!({}), 1)
            .unwrap_err()
            .contains("not ready/available"));
        *state.human_monitor.lock().unwrap() = json!({"available":true,"watched_devices":1});
        state
            .last_human_ms
            .store(state.started.elapsed().as_millis() as u64, Ordering::SeqCst);
        assert!(handle(&state, "desktop_resume", &json!({}), 1)
            .unwrap_err()
            .contains("recent"));
        assert!(marker_path.exists());
        assert!(state.takeover.load(Ordering::SeqCst));
        state.last_human_ms.store(0, Ordering::SeqCst);
        *state.active.lock().unwrap() = json!({"task_id":"owned offline task"});
        assert!(handle(&state, "desktop_resume", &json!({}), 1)
            .unwrap_err()
            .contains("still releasing"));
        *state.active.lock().unwrap() = Value::Null;
        state.release_confirmed.store(false, Ordering::SeqCst);
        assert!(handle(&state, "desktop_resume", &json!({}), 1)
            .unwrap_err()
            .contains("not confirmed"));
        state.release_confirmed.store(true, Ordering::SeqCst);
        let resumed = handle(
            &state,
            "desktop_resume",
            &json!({"expected_epoch":1,"expected_session_id":state.session_id}),
            1,
        )
        .unwrap();
        let data: Value =
            serde_json::from_str(resumed["content"][0]["text"].as_str().unwrap()).unwrap();
        assert_eq!(data["status"], "resumed");
        assert_eq!(data["fresh_observation_required"], true);
        assert!(!state.takeover.load(Ordering::SeqCst));
        assert!(!marker_path.exists());
        let takeover = handle(&state, "desktop_takeover", &json!({}), 2).unwrap();
        assert_eq!(takeover["isError"], false);
        assert!(state.takeover.load(Ordering::SeqCst));
        assert!(marker_path.exists());
        let restarted = takeover::Latch::startup(marker_path, "offline-session-2").unwrap();
        assert_eq!(restarted.reason.source, "explicit_desktop_takeover");
        assert_eq!(restarted.reason.session_id, session);
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn bounded_background_and_cancel_and_crop_targets() {
        // These are disposable subprocess/data tests. They never connect to
        // Wayland, Niri, Cua, input devices, real clipboard, or the daemon.
        let dir = env::temp_dir().join(format!("weasel-core-regression-{}", std::process::id()));
        private_dir(&dir).unwrap();
        let script = dir.join("wl-copy");
        fs::write(&script, b"#!/bin/sh\nsleep 0.4 &\nexit 0\n").unwrap();
        fs::set_permissions(&script, fs::Permissions::from_mode(0o700)).unwrap();
        let old_path = env::var_os("PATH").unwrap_or_default();
        env::set_var(
            "PATH",
            format!("{}:{}", dir.display(), old_path.to_string_lossy()),
        );
        let log = fs::OpenOptions::new()
            .create(true)
            .write(true)
            .mode(0o600)
            .open(dir.join("events.jsonl"))
            .unwrap();
        let state = Arc::new(State {
            started: Instant::now(),
            session_id: "regression".into(),
            epoch: AtomicU64::new(1),
            serial: AtomicU64::new(1),
            queued: AtomicUsize::new(0),
            takeover: AtomicBool::new(false),
            takeover_marker: Mutex::new(
                takeover::Latch::startup(dir.join("takeover.json"), "offline-regression").unwrap(),
            ),
            last_human_ms: AtomicU64::new(0),
            input_policy: input_policy::Policy::test_ready(),
            human_monitor: Mutex::new(Value::Null),
            release_confirmed: AtomicBool::new(true),
            capture_available: AtomicBool::new(false),
            capture_attempts: AtomicU64::new(0),
            capture_failure: Mutex::new(None),
            active: Mutex::new(Value::Null),
            task_scopes: Mutex::new(HashMap::new()),
            task_scope_ids: Mutex::new(HashSet::new()),
            task_scope_gate: Mutex::new(()),
            last_result: Mutex::new(Value::Null),
            observations: Mutex::new(HashMap::new()),
            reference_cache: ReferenceCache::default(),
            semantic_targets: Mutex::new(HashMap::new()),
            actor: Mutex::new(None),
            keyboard: Mutex::new(None),
            global_keyboard: Mutex::new(None),
            image_dir: dir.clone(),
            log: Mutex::new(log),
        });
        let start = Instant::now();
        bounded_command(
            &state,
            "wl-copy",
            &[],
            Some(b"fixture"),
            Duration::from_millis(200),
            Some(1),
        )
        .unwrap();
        assert!(
            start.elapsed() < Duration::from_millis(200),
            "background output owner must not retain acknowledgement pipes"
        );
        env::set_var("PATH", &old_path);
        let canceller = state.clone();
        let timer = thread::spawn(move || {
            thread::sleep(Duration::from_millis(30));
            canceller.epoch.fetch_add(1, Ordering::SeqCst);
        });
        let start = Instant::now();
        let error = bounded_command(
            &state,
            "sh",
            &["-c".into(), "sleep 5".into()],
            Some(&vec![b'x'; 65536]),
            Duration::from_secs(2),
            Some(1),
        )
        .unwrap_err();
        timer.join().unwrap();
        assert!(error.contains("Canceled"));
        assert!(
            start.elapsed() < Duration::from_millis(400),
            "stalled stdin must remain cancelable"
        );
        let obs = Observation {
            id: "fixture".into(),
            at: Instant::now(),
            epoch: 2,
            input_generation: 0,
            output: "none".into(),
            output_geometry: Value::Null,
            focused_window: None,
            windows: json!([]),
            focus_output: None,
            image_width: 100,
            image_height: 100,
            image: dir.join("unused.png"),
            view: Crop {
                x: 40,
                y: 40,
                width: 20,
                height: 20,
            },
            view_image: dir.join("unused-crop.png"),
        };
        assert!(preflight(&obs,&Action::Drag{x:5.0,y:5.0,to_x:-1.0,to_y:5.0,duration_ms:None}).is_err(),"negative crop-local endpoint must reject before button down even if full-output position exists");
        assert!(preflight(
            &obs,
            &Action::Click {
                x: 5.0,
                y: 5.0,
                button: None,
                count: Some(0)
            }
        )
        .is_err());
        assert!(preflight(
            &obs,
            &Action::Click {
                x: 19.0,
                y: 19.0,
                button: None,
                count: Some(1)
            }
        )
        .is_ok());
        assert!(preflight(
            &obs,
            &Action::Key {
                keys: vec!["ctrl".into(), "invalid-key".into()],
                key_scope: KeyScope::App
            }
        )
        .is_err());
        assert!(preflight(
            &obs,
            &Action::Scroll {
                x: 5.0,
                y: 5.0,
                dx: 0,
                dy: 101
            }
        )
        .is_err());
        state
            .observations
            .lock()
            .unwrap()
            .insert(obs.id.clone(), obs.clone());
        state.capture_available.store(true, Ordering::SeqCst);
        let started = Instant::now();
        let response=act(&state,&json!({"observation_id":obs.id,"timeout_ms":100,"observe_after":false,"actions":[{"kind":"wait","ms":10000}]}),2).unwrap();
        let data: Value =
            serde_json::from_str(response["content"][0]["text"].as_str().unwrap()).unwrap();
        assert_eq!(data["status"], "failed");
        assert_eq!(response["isError"], true);
        assert_eq!(data["completed_actions"], 0);
        assert_eq!(data["actor_release_confirmed"], true);
        assert!(data["error"].as_str().unwrap().contains("deadline"));
        assert!(
            started.elapsed() < Duration::from_millis(300),
            "100ms batch deadline must interrupt 10-second wait"
        );
        let held = state.actor.lock().unwrap();
        let waiting = state.clone();
        let started = Instant::now();
        let queued = thread::spawn(move || {
            act(
                &waiting,
                &json!({"observation_id":"fixture","timeout_ms":100,"observe_after":false,"actions":[{"kind":"wait","ms":1000}]}),
                2,
            )
        });
        thread::sleep(Duration::from_millis(150));
        drop(held);
        assert!(queued.join().unwrap().unwrap_err().contains("deadline"));
        assert!(started.elapsed() < Duration::from_millis(300));
        assert_eq!(state.queued.load(Ordering::SeqCst), 0);
        // Simulated capture failure and recovery use only owned fake binaries.
        // This tests capability gating, not a real screenshot or desktop input.
        let niri_script = dir.join("niri");
        fs::write(&niri_script, b"#!/bin/sh\ncase \"$3\" in\nwindows) echo '[]';;\noutputs) echo '{\"mock\":{\"logical\":{\"x\":0,\"y\":0,\"width\":2,\"height\":2,\"scale\":1,\"transform\":\"Normal\"}}}';;\nworkspaces) echo '[{\"id\":1,\"idx\":1,\"output\":\"mock\",\"is_focused\":true,\"is_active\":true,\"active_window_id\":null}]';;\n*) exit 1;;\nesac\n").unwrap();
        fs::set_permissions(&niri_script, fs::Permissions::from_mode(0o700)).unwrap();
        let grim_script = dir.join("grim");
        fs::write(&grim_script, b"#!/bin/sh\nexit 1\n").unwrap();
        fs::set_permissions(&grim_script, fs::Permissions::from_mode(0o700)).unwrap();
        env::set_var(
            "PATH",
            format!("{}:{}", dir.display(), old_path.to_string_lossy()),
        );
        assert!(
            observe(&state, &json!({"output":"mock","include_image":false}))
                .unwrap_err()
                .contains("Capture backend failed")
        );
        assert!(!state.capture_available.load(Ordering::SeqCst));
        assert_eq!(state.epoch.load(Ordering::SeqCst), 3);
        assert!(state.observations.lock().unwrap().is_empty());
        assert!(act(&state,&json!({"observation_id":"fixture","observe_after":false,"actions":[{"kind":"wait","ms":1}]}),3).unwrap_err().contains("No successful current capture"));
        let sample = dir.join("synthetic.png");
        let image_file = fs::File::create(&sample).unwrap();
        let mut encoder = png::Encoder::new(image_file, 2, 2);
        encoder.set_color(png::ColorType::Rgba);
        encoder.set_depth(png::BitDepth::Eight);
        encoder
            .write_header()
            .unwrap()
            .write_image_data(&[0u8; 16])
            .unwrap();
        fs::write(
            &grim_script,
            format!(
                "#!/bin/sh\nfor arg do destination=\"$arg\"; done\ncp '{}' \"$destination\"\n",
                sample.display()
            ),
        )
        .unwrap();
        let recovered = observe(&state, &json!({"output":"mock","include_image":false})).unwrap();
        let recovered_data: Value =
            serde_json::from_str(recovered["content"][0]["text"].as_str().unwrap()).unwrap();
        assert!(state.capture_available.load(Ordering::SeqCst));
        assert_eq!(recovered_data["epoch"], 3);
        assert_eq!(state.observations.lock().unwrap().len(), 1);
        let waited=act(&state,&json!({"observation_id":recovered_data["observation_id"],"observe_after":false,"actions":[{"kind":"wait","ms":1}]}),3).unwrap();
        assert_eq!(waited["isError"], false);
        env::set_var("PATH", &old_path);
        let _ = fs::remove_dir_all(dir);
    }
}

#[cfg(test)]
mod global_routing_regression {
    use super::*;
    fn keys(names: &[&str]) -> Vec<String> {
        names.iter().map(|n| n.to_string()).collect()
    }
    fn global_args(action: Value) -> Value {
        json!({"observation_id":"obs-offline-session-1","timeout_ms":1000,"actions":[{"kind":"type","text":"owned fixture"},action]})
    }
    #[test]
    fn scoped_route_defaults_and_aliases_are_typed() {
        for alias in ["super", "meta", "logo"] {
            assert!(uses_global_keyboard(&keys(&[alias, "f"]), KeyScope::App).unwrap());
        }
        assert!(!uses_global_keyboard(&keys(&["ctrl", "alt", "Escape"]), KeyScope::App).unwrap());
        assert!(
            uses_global_keyboard(&keys(&["ctrl", "alt", "Escape"]), KeyScope::Compositor).unwrap()
        );
        assert!(!uses_global_keyboard(&keys(&["ctrl", "s"]), KeyScope::App).unwrap());
        let action: Action =
            serde_json::from_value(json!({"kind":"key","keys":["ctrl","s"]})).unwrap();
        assert!(matches!(
            action,
            Action::Key {
                key_scope: KeyScope::App,
                ..
            }
        ));
        assert!(serde_json::from_value::<Action>(
            json!({"kind":"key","keys":["ctrl","s"],"key_scope":"global-guess"})
        )
        .is_err());
        assert!(proxy_needs_global_guard(
            "desktop_act",
            &global_args(json!({"kind":"key","keys":["super","shift","f"]}))
        )
        .unwrap());
        assert!(proxy_needs_global_guard(
            "desktop_act",
            &global_args(
                json!({"kind":"key","keys":["ctrl","alt","Escape"],"key_scope":"compositor"})
            )
        )
        .unwrap());
    }
    #[test]
    fn action_kind_rejects_ignored_policy_and_foreign_fields() {
        let invalid = [
            json!({"kind":"type","text":"own canary","restore_clipboard":false}),
            json!({"kind":"paste","text":"own canary","text_method":"keyboard"}),
            json!({"kind":"wait","ms":1,"keys":["ctrl","s"]}),
            json!({"kind":"key","keys":["ctrl","s"],"key_scpoe":"compositor"}),
        ];
        for later in invalid {
            // Decode the complete batch before observation lookup/queue/dispatch.
            // An earlier effectful action must never hide a later policy typo.
            let batch = json!({"observation_id":"not-looked-up","actions":[
                {"kind":"type","text":"earlier own canary"}, later
            ]});
            let proxy_error = proxy_needs_global_guard("desktop_act", &batch).unwrap_err();
            assert!(proxy_error.contains("unknown field"), "{proxy_error}");
            let error = serde_json::from_value::<ActArgs>(batch)
                .err()
                .unwrap()
                .to_string();
            assert!(error.contains("unknown field"), "{error}");
        }
        let paste: Action = serde_json::from_value(json!({
            "kind":"paste","text":"own canary","restore_clipboard":false
        }))
        .unwrap();
        assert!(matches!(
            paste,
            Action::Paste {
                restore_clipboard: false,
                ..
            }
        ));
    }
    #[test]
    fn backend_guard_rejects_wrong_session_epoch_and_revision() {
        let guard = GlobalBackendGuard {
            routing_revision: GLOBAL_ROUTING_REVISION,
            session_id: "offline-session".into(),
            epoch: 7,
        };
        assert!(validate_global_backend_guard(
            &guard,
            "offline-session",
            7,
            "obs-offline-session-1"
        )
        .is_ok());
        assert!(
            validate_global_backend_guard(&guard, "other-session", 7, "obs-offline-session-1")
                .is_err()
        );
        assert!(validate_global_backend_guard(
            &guard,
            "offline-session",
            8,
            "obs-offline-session-1"
        )
        .is_err());
        assert!(
            validate_global_backend_guard(&guard, "offline-session", 7, "obs-other-session-1")
                .is_err()
        );
        let old = GlobalBackendGuard {
            routing_revision: 1,
            ..guard
        };
        assert!(
            validate_global_backend_guard(&old, "offline-session", 7, "obs-offline-session-1")
                .is_err()
        );
    }
    fn fake_backend(status: Value, expects_act: bool) -> (PathBuf, thread::JoinHandle<Vec<Value>>) {
        static SERIAL: AtomicU64 = AtomicU64::new(1);
        let dir = env::temp_dir().join(format!(
            "weasel-global-proxy-test-{}-{}",
            std::process::id(),
            SERIAL.fetch_add(1, Ordering::SeqCst)
        ));
        fs::create_dir(&dir).unwrap();
        fs::set_permissions(&dir, fs::Permissions::from_mode(0o700)).unwrap();
        let socket = dir.join("fixture.sock");
        let listener = std::os::unix::net::UnixListener::bind(&socket).unwrap();
        listener.set_nonblocking(true).unwrap();
        let cleanup_socket = socket.clone();
        let handle = thread::spawn(move || {
            let mut requests = Vec::new();
            let mut last = Instant::now();
            loop {
                match listener.accept() {
                    Ok((mut stream, _)) => {
                        stream
                            .set_read_timeout(Some(Duration::from_millis(200)))
                            .unwrap();
                        let mut line = String::new();
                        BufReader::new(stream.try_clone().unwrap())
                            .read_line(&mut line)
                            .unwrap();
                        let req: Value = serde_json::from_str(&line).unwrap();
                        let response = if req["tool"] == "desktop_status" {
                            text_result(status.clone())
                        } else if status["private_old_replacement"] == true
                            && req["tool"] == "desktop_resume_bound"
                        {
                            error_result("Unknown tool desktop_resume_bound; old replacement fixture refused before transition".into())
                        } else {
                            text_result(json!({"status":"offline_fixture_ack"}))
                        };
                        requests.push(req);
                        writeln!(stream, "{response}").unwrap();
                        last = Instant::now();
                        if expects_act && requests.len() == 2 {
                            break;
                        }
                    }
                    Err(e) if e.kind() == io::ErrorKind::WouldBlock => {
                        if (!requests.is_empty()
                            && !expects_act
                            && last.elapsed() > Duration::from_millis(120))
                            || last.elapsed() > Duration::from_secs(2)
                        {
                            break;
                        }
                        thread::sleep(Duration::from_millis(2));
                    }
                    Err(e) => panic!("private fixture socket: {e}"),
                }
            }
            fs::remove_file(&cleanup_socket).unwrap();
            fs::remove_dir(&dir).unwrap();
            requests
        });
        (socket, handle)
    }

    #[test]
    fn r8_new_proxy_never_sends_true_resume_to_old_backend() {
        let (socket, server) = fake_backend(
            json!({"schema":1,"session_id":"old-backend","epoch":7,"takeover_latched":true,"active":null,"queued_batches":0,"actor_release_confirmed":true}),
            false,
        );
        let error = daemon_call_owned(
            &socket,
            "desktop_resume",
            &json!({"expected_epoch":7,"expected_session_id":"old-backend"}),
            None,
        )
        .unwrap_err();
        assert!(error.contains("resume binding revision1"));
        let requests = server.join().unwrap();
        assert_eq!(requests.len(), 1);
        assert_eq!(requests[0]["tool"], "desktop_status");
    }
    #[test]
    fn r8_old_unlatched_backend_resume_is_only_a_status_noop() {
        let (socket, server) = fake_backend(
            json!({"schema":1,"session_id":"old-idle-backend","epoch":7,"takeover_latched":false,"active":null,"queued_batches":0,"actor_release_confirmed":true}),
            false,
        );
        let result = daemon_call_owned(&socket, "desktop_resume", &json!({}), None).unwrap();
        let data: Value =
            serde_json::from_str(result["content"][0]["text"].as_str().unwrap()).unwrap();
        assert_eq!(data["status"], "already_resumed");
        assert_eq!(data["resume_request_forwarded"], false);
        assert_eq!(data["input_permission_changed"], false);
        let requests = server.join().unwrap();
        assert_eq!(requests.len(), 1);
        assert_eq!(requests[0]["tool"], "desktop_status");
    }
    #[test]
    fn r8_new_proxy_forwards_exact_binding_only_to_guarded_backend() {
        let (socket, server) = fake_backend(
            json!({"resume_binding_revision":1,"session_id":"new-backend","epoch":7,"takeover_latched":true}),
            true,
        );
        let args = json!({"expected_epoch":7,"expected_session_id":"new-backend"});
        daemon_call_owned(&socket, "desktop_resume", &args, None).unwrap();
        let requests = server.join().unwrap();
        assert_eq!(requests.len(), 2);
        assert_eq!(requests[0]["tool"], "desktop_status");
        assert_eq!(requests[1]["tool"], "desktop_resume_bound");
        assert_eq!(
            requests[1]["arguments"], args,
            "never manufacture or refresh authorization binding"
        );
    }

    #[test]
    fn r8_wire_method_refuses_old_replacement_even_after_new_revision_preflight() {
        let (socket, server) = fake_backend(
            json!({"resume_binding_revision":1,"session_id":"new-at-preflight","epoch":7,"takeover_latched":true,"private_old_replacement":true}),
            true,
        );
        let args = json!({"expected_epoch":7,"expected_session_id":"new-at-preflight"});
        let result = daemon_call_owned(&socket, "desktop_resume", &args, None).unwrap();
        assert_eq!(result["isError"], true);
        assert!(result["content"][0]["text"]
            .as_str()
            .unwrap()
            .contains("Unknown tool desktop_resume_bound"));
        let requests = server.join().unwrap();
        assert_eq!(requests.len(), 2);
        assert_eq!(requests[1]["tool"], "desktop_resume_bound");
        assert!(
            requests.iter().all(|r| r["tool"] != "desktop_resume"),
            "old replacement must never receive the unbound effective method"
        );
    }
    #[test]
    fn new_proxy_old_actor_refuses_entire_batch_before_any_act_request() {
        for action in [
            json!({"kind":"key","keys":["super","shift","f"]}),
            json!({"kind":"key","keys":["ctrl","alt","Escape"],"key_scope":"compositor"}),
        ] {
            let (socket, server) = fake_backend(
                json!({"schema":1,"version":"0.1.0","session_id":"offline-session","epoch":7}),
                false,
            );
            let error =
                daemon_call_owned(&socket, "desktop_act", &global_args(action), None).unwrap_err();
            assert!(error.contains("routing revision2"));
            let requests = server.join().unwrap();
            assert_eq!(requests.len(), 1);
            assert_eq!(requests[0]["tool"], "desktop_status");
        }
    }
    #[test]
    fn new_proxy_new_actor_binds_guard_and_preserves_scoped_chord() {
        let (socket, server) = fake_backend(
            json!({"interface_revision":2,"global_keyboard":global_keyboard_capability(),"session_id":"offline-session","epoch":7}),
            true,
        );
        daemon_call_owned(
            &socket,
            "desktop_act",
            &global_args(
                json!({"kind":"key","keys":["ctrl","alt","Escape"],"key_scope":"compositor"}),
            ),
            None,
        )
        .unwrap();
        let requests = server.join().unwrap();
        assert_eq!(requests.len(), 2);
        assert_eq!(requests[1]["tool"], "desktop_act");
        let args = &requests[1]["arguments"];
        assert_eq!(
            args["expected_global_backend"],
            json!({"routing_revision":2,"session_id":"offline-session","epoch":7})
        );
        assert_eq!(args["actions"][1]["key_scope"], "compositor");
        assert!(args["timeout_ms"].as_u64().unwrap() <= 1000);
    }
}

#[cfg(test)]
mod indicator_lifecycle_tests {
    use super::*;
    fn data(result: &Value) -> Value {
        serde_json::from_str(result["content"][0]["text"].as_str().unwrap()).unwrap()
    }
    #[test]
    fn lightweight_reply_does_not_read_heavy_status_and_is_read_only() {
        let (state, dir) = release_recovery_tests::cooperative_fixture("lifecycle-lightweight");
        let generation = state.input_policy.generation();
        let serial = state.serial.load(Ordering::SeqCst);
        let _result_lock = state.last_result.lock().unwrap();
        let _capture_lock = state.capture_failure.lock().unwrap();
        let _monitor_lock = state.human_monitor.lock().unwrap();
        let _marker_lock = state.takeover_marker.lock().unwrap();
        let _keyboard_lock = state.global_keyboard.lock().unwrap();
        let result = data(&handle(&state, "desktop_indicator_lifecycle", &json!({}), 77).unwrap());
        assert_eq!(
            result,
            json!({"schema":1,"lifecycle_revision":3,"complete":true,
            "session_id":state.session_id,"epoch":77,"takeover_latched":false,
            "actor_active":false,"task_scope_active":false,"queued_batches":0,"actor_release_confirmed":true})
        );
        assert_eq!(state.input_policy.generation(), generation);
        assert_eq!(state.serial.load(Ordering::SeqCst), serial);
        assert!(state.active.lock().unwrap().is_null());
        fs::remove_dir_all(dir).unwrap();
    }
    #[test]
    fn lifecycle_reports_active_release_queue_and_current_stop_without_content() {
        let (state, dir) = release_recovery_tests::cooperative_fixture("lifecycle-active-stop");
        *state.active.lock().unwrap() =
            json!({"task_id":"private", "phase":"wait", "private_text":"not exported"});
        state.release_confirmed.store(false, Ordering::SeqCst);
        state.queued.store(2, Ordering::SeqCst);
        state.epoch.store(78, Ordering::SeqCst);
        state.takeover.store(true, Ordering::SeqCst);
        let result = data(&indicator_lifecycle(&state, &json!({})).unwrap());
        assert_eq!(result["actor_active"], true);
        assert_eq!(result["actor_release_confirmed"], false);
        assert_eq!(result["queued_batches"], 2);
        assert_eq!(result["epoch"], 78);
        assert_eq!(result["takeover_latched"], true);
        assert!(result.get("active").is_none());
        assert!(result.get("last_result").is_none());
        assert!(!result.to_string().contains("private_text"));
        assert!(!result.to_string().contains("not exported"));
        assert!(result.get("task_id").is_none());
        assert!(result.get("phase").is_none());
        *state.active.lock().unwrap() = Value::Null;
        let idle = data(&indicator_lifecycle(&state, &json!({})).unwrap());
        assert_eq!(idle["actor_active"], false);
        assert_eq!(idle["actor_release_confirmed"], false);
        fs::remove_dir_all(dir).unwrap();
    }
    fn process_binding(pid: u32) -> ScopeProcess {
        let raw = fs::read_to_string(format!("/proc/{pid}/stat")).unwrap();
        let (_, fields) = raw.rsplit_once(") ").unwrap();
        ScopeProcess { pid, start_ticks: fields.split_whitespace().nth(19).unwrap().parse().unwrap() }
    }
    #[test]
    fn helper_upgrade_outlives_old_deadline_but_owner_death_is_terminal() {
        let (state, dir) = release_recovery_tests::cooperative_fixture("owner-process-lifetime");
        let mut child = Command::new("sleep").arg("60").spawn().unwrap();
        let helper = process_binding(std::process::id());
        let owner = process_binding(child.id());
        let base = json!({"event":"begin","task_id":"long-job","session_id":state.session_id,"expected_epoch":77,"scope_token":"own-token"});
        indicator_lifecycle(&state, &base).unwrap();
        let mut bound = base.clone();
        bound["owner_processes"] = json!([helper, owner]);
        let registered = data(&indicator_lifecycle(&state, &bound).unwrap());
        state.task_scopes.lock().unwrap().get_mut("long-job").unwrap().expires = Instant::now() - Duration::from_secs(1);
        let live_past_deadline = action_scope_still_live(&state, "long-job");
        let admission = authorize_action_task(&state, Some("long-job"), "unused".into());
        let mut replacement = bound.clone();
        replacement["owner_processes"] = json!([helper, helper]);
        let replacement_refused = indicator_lifecycle(&state, &replacement).is_err();
        child.kill().unwrap();
        child.wait().unwrap();
        let live_after_death = action_scope_still_live(&state, "long-job");
        assert_eq!(registered["task_scope_lifetime"], "owner_process");
        assert!(live_past_deadline);
        assert_eq!(admission.unwrap(), ("long-job".into(), true));
        assert!(replacement_refused);
        assert!(!live_after_death);
        assert!(indicator_lifecycle(&state, &base).unwrap_err().contains("already used"));
        assert!(authorize_action_task(&state, Some("long-job"), "unused".into()).is_err());
        fs::remove_dir_all(dir).unwrap();
    }
    #[test]
    fn process_binding_rejects_wrong_generation_and_dead_owner() {
        let current = process_binding(std::process::id());
        assert!(current.is_live());
        assert!(!ScopeProcess { start_ticks: current.start_ticks + 1, ..current.clone() }.is_live());
        assert!(!ScopeProcess { pid: 0, start_ticks: 0 }.is_live());
        let (state, dir) = release_recovery_tests::cooperative_fixture("bad-owner-process");
        let bad = json!({"event":"begin","task_id":"bad-owner","session_id":state.session_id,"expected_epoch":77,"scope_token":"own-token","owner_processes":[{"pid":0,"start_ticks":0},{"pid":current.pid,"start_ticks":current.start_ticks}]});
        assert!(indicator_lifecycle(&state, &bad).is_err());
        assert!(state.task_scopes.lock().unwrap().is_empty());
        assert!(state.task_scope_ids.lock().unwrap().is_empty());
        fs::remove_dir_all(dir).unwrap();
    }
    #[test]
    fn stale_scope_cleanup_cannot_end_a_different_current_binding() {
        let (state, dir) = release_recovery_tests::cooperative_fixture("lifecycle-owner-token");
        let begin = |task_id: &str, epoch: u64, token: &str| {
            handle(
                &state,
                "desktop_indicator_lifecycle",
                &json!({"event":"begin","task_id":task_id,"session_id":state.session_id,"expected_epoch":epoch,"scope_token":token}),
                epoch,
            )
        };
        let end = |task_id: &str, epoch: u64, token: &str| {
            handle(
                &state,
                "desktop_indicator_lifecycle",
                &json!({"event":"end","task_id":task_id,"session_id":state.session_id,"expected_epoch":epoch,"scope_token":token}),
                epoch,
            )
        };
        begin("owner-bound-task", 77, "old-owner-token").unwrap();
        end("owner-bound-task", 77, "delayed-wrong-owner").unwrap_err();
        assert!(state.task_scopes.lock().unwrap().contains_key("owner-bound-task"));
        assert!(end("owner-bound-task", 78, "old-owner-token").is_err());
        assert!(state.task_scopes.lock().unwrap().contains_key("owner-bound-task"));
        end("owner-bound-task", 77, "old-owner-token").unwrap();
        assert!(!state.task_scopes.lock().unwrap().contains_key("owner-bound-task"));
        begin("replacement-task", 77, "replacement-owner-token").unwrap();
        end("owner-bound-task", 77, "old-owner-token").unwrap();
        assert!(state.task_scopes.lock().unwrap().contains_key("replacement-task"));
        fs::remove_dir_all(dir).unwrap();
    }
}

#[cfg(test)]
mod release_recovery_tests {
    use super::*;
    pub(super) fn fixture(label: &str) -> (State, PathBuf) {
        let dir = env::temp_dir().join(format!(
            "weasel-private-release-recovery-{}-{label}",
            std::process::id()
        ));
        private_dir(&dir).unwrap();
        let session = "private-release-recovery-session";
        let mut latch = takeover::Latch::startup(dir.join("takeover.json"), session).unwrap();
        latch.set("explicit_desktop_takeover", session).unwrap();
        let file = fs::OpenOptions::new()
            .create(true)
            .write(true)
            .mode(0o600)
            .open(dir.join("events.jsonl"))
            .unwrap();
        (
            State {
                started: Instant::now(),
                session_id: session.into(),
                epoch: AtomicU64::new(77),
                serial: AtomicU64::new(1),
                queued: AtomicUsize::new(0),
                takeover: AtomicBool::new(true),
                takeover_marker: Mutex::new(latch),
                last_human_ms: AtomicU64::new(0),
                input_policy: input_policy::Policy::test_ready(),
                human_monitor: Mutex::new(Value::Null),
                release_confirmed: AtomicBool::new(false),
                capture_available: AtomicBool::new(true),
                capture_attempts: AtomicU64::new(0),
                capture_failure: Mutex::new(None),
                active: Mutex::new(Value::Null),
                task_scopes: Mutex::new(HashMap::new()),
                task_scope_ids: Mutex::new(HashSet::new()),
                task_scope_gate: Mutex::new(()),
                last_result: Mutex::new(
                    json!({"status":"failed","error":"original timeout preserved"}),
                ),
                observations: Mutex::new(HashMap::new()),
                reference_cache: ReferenceCache::default(),
                semantic_targets: Mutex::new(HashMap::new()),
                actor: Mutex::new(None),
                keyboard: Mutex::new(None),
                global_keyboard: Mutex::new(None),
                image_dir: dir.clone(),
                log: Mutex::new(file),
            },
            dir,
        )
    }
    pub(super) fn obs(dir: &Path) -> Observation {
        Observation {
            id: "cloned-before-recovery".into(),
            at: Instant::now(),
            epoch: 77,
            input_generation: 0,
            output: "private-fixture".into(),
            output_geometry: Value::Null,
            focused_window: None,
            windows: json!([]),
            focus_output: None,
            image_width: 1,
            image_height: 1,
            image: dir.join("never-read.png"),
            view: Crop {
                x: 0,
                y: 0,
                width: 1,
                height: 1,
            },
            view_image: dir.join("never-read.png"),
        }
    }
    pub(super) fn cooperative_fixture(label: &str) -> (Arc<State>, PathBuf) {
        let (state, dir) = fixture(label);
        state.takeover_marker.lock().unwrap().clear().unwrap();
        state.takeover.store(false, Ordering::SeqCst);
        state.release_confirmed.store(true, Ordering::SeqCst);
        let o = obs(&dir);
        state.observations.lock().unwrap().insert(o.id.clone(), o);
        (Arc::new(state), dir)
    }
    fn test_live_scope(state: &State, task_id: &str) {
        state.task_scopes.lock().unwrap().insert(
            task_id.to_string(),
            TaskScope {
                expires: Instant::now() + Duration::from_secs(60),
                owner_processes: None,
                epoch: state.epoch.load(Ordering::SeqCst),
                scope_token: Some(format!("test-owner-{task_id}")),
            },
        );
    }
    fn result_data(result: &Value) -> Value {
        serde_json::from_str(result["content"][0]["text"].as_str().unwrap()).unwrap()
    }
    #[test]
    fn oversized_pointer_views_refuse_every_pointer_kind_on_each_axis() {
        let (_state, dir) = cooperative_fixture("pointer-view-boundaries");
        let mut captured = obs(&dir);
        let pointers = [
            Action::Move { x: 3.0, y: 4.0 },
            Action::Click {
                x: 3.0,
                y: 4.0,
                button: None,
                count: None,
            },
            Action::Scroll {
                x: 3.0,
                y: 4.0,
                dx: 0,
                dy: 1,
            },
            Action::Drag {
                x: 3.0,
                y: 4.0,
                to_x: 20.0,
                to_y: 20.0,
                duration_ms: None,
            },
        ];
        for (width, height) in [(2752, 1152), (1201, 1200), (1200, 1201)] {
            captured.view.width = width;
            captured.view.height = height;
            for action in &pointers {
                assert!(preflight(&captured, action)
                    .unwrap_err()
                    .contains("fresh target crop"));
            }
            assert!(preflight(&captured, &Action::Focus { window_id: 77 }).is_ok());
            assert!(preflight(
                &captured,
                &Action::Type {
                    text: "bounded edit".into(),
                    text_method: TextMethod::Auto
                }
            )
            .is_ok());
            assert!(preflight(
                &captured,
                &Action::SemanticClick {
                    handle_id: "fresh-handle".into(),
                    action_name: "activate".into()
                }
            )
            .is_ok());
        }
        captured.view.width = 1200;
        captured.view.height = 1200;
        for action in &pointers {
            assert!(preflight(&captured, action).is_ok());
        }
        captured.image_width = 2752;
        captured.image_height = 1152;
        captured.view = Crop {
            x: 1500,
            y: 200,
            width: 1000,
            height: 400,
        };
        for action in &pointers {
            assert!(preflight(&captured, action).is_ok());
        }
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn oversized_later_pointer_refuses_batch_before_earlier_edit_or_actor_admission() {
        let (state, dir) = cooperative_fixture("pointer-view-batch-refusal");
        let mut captured = obs(&dir);
        captured.image_width = 2752;
        captured.image_height = 1152;
        captured.view.width = 2752;
        captured.view.height = 1152;
        state
            .observations
            .lock()
            .unwrap()
            .insert(captured.id.clone(), captured.clone());
        let before_result = state.last_result.lock().unwrap().clone();
        for earlier in [
            json!({"kind":"type","text":"must-not-be-typed"}),
            json!({"kind":"key","keys":["Super","Right"],"key_scope":"compositor"}),
        ] {
            let error = act(&state, &json!({"observation_id":captured.id,"task_id":"must-not-enter-actor","observe_after":false,"actions":[earlier,{"kind":"click","x":2100,"y":300}]}), 77).unwrap_err();
            assert!(error.contains("fresh target crop"));
            assert!(state.active.lock().unwrap().is_null());
            assert_eq!(state.queued.load(Ordering::SeqCst), 0);
            assert!(state.release_confirmed.load(Ordering::SeqCst));
            assert!(state.actor.lock().unwrap().is_none());
            assert!(state.keyboard.lock().unwrap().is_none());
            assert!(state.global_keyboard.lock().unwrap().is_none());
            assert_eq!(*state.last_result.lock().unwrap(), before_result);
            assert_eq!(fs::metadata(dir.join("events.jsonl")).unwrap().len(), 0);
        }
        fs::remove_dir_all(dir).unwrap();
    }
    fn activity_after_writer_active(
        state: Arc<State>,
        escape: bool,
        min_completed: u64,
    ) -> thread::JoinHandle<()> {
        thread::spawn(move || {
            let until = Instant::now() + Duration::from_secs(1);
            loop {
                let active = state.active.lock().unwrap().clone();
                if !active.is_null()
                    && active["completed_actions"].as_u64().unwrap_or(0) >= min_completed
                {
                    physical_activity(&state, escape, false);
                    return;
                }
                assert!(Instant::now() < until, "fixture writer did not start");
                thread::sleep(Duration::from_millis(1));
            }
        })
    }

    #[test]
    fn independent_focus_inventory_requires_same_exact_instance_not_shared_title() {
        let (state, dir) = cooperative_fixture("independent-focus-identity");
        let mut captured = obs(&dir);
        let target = json!({"id":77,"pid":42,"app_id":"original-app","workspace_id":1,"layout":{"tile_size":[400,300]},"title":"same title"});
        let other =
            json!({"id":78,"pid":43,"app_id":"original-app","workspace_id":1,"title":"same title"});
        captured.windows = json!([target.clone(), other]);
        assert!(validate_focus_inventory(&captured, 77, &json!([target.clone()])).is_ok());
        for (field, value) in [
            ("pid", json!(999)),
            ("app_id", json!("replacement-app")),
            ("workspace_id", json!(2)),
            ("layout", json!({"tile_size":[500,400]})),
        ] {
            let mut changed = target.clone();
            changed[field] = value;
            assert!(
                validate_focus_inventory(&captured, 77, &json!([changed])).is_err(),
                "field {field}"
            );
        }
        assert!(validate_focus_inventory(&captured, 77, &json!([])).is_err());
        assert!(
            validate_focus_inventory(&captured, 77, &json!([target.clone(), target.clone()]))
                .is_err()
        );
        assert!(validate_focus_inventory(&captured, 999, &captured.windows).is_err());
        let mut unknown = target.clone();
        unknown["pid"] = Value::Null;
        assert!(validate_focus_inventory(&captured, 77, &json!([unknown])).is_err());
        let mut renamed = target;
        renamed["title"] = json!("new document title");
        assert!(
            validate_focus_inventory(&captured, 77, &json!([renamed])).is_ok(),
            "title may change in same exact instance"
        );
        assert!(state.release_confirmed.load(Ordering::SeqCst));
        fs::remove_dir_all(dir).unwrap();
    }
    #[test]
    fn independent_focus_state_refuses_old_generation_held_cancel_and_escape() {
        let (state, dir) = cooperative_fixture("independent-focus-state");
        let mut captured = obs(&dir);
        assert!(validate_focus_state(&state, &captured).is_ok());
        captured.at = Instant::now() - Duration::from_secs(61);
        assert!(validate_focus_state(&state, &captured)
            .unwrap_err()
            .contains("older than 60"));
        captured.at = Instant::now();
        state.input_policy.update_holds(1, true);
        captured.input_generation = state.input_policy.generation();
        assert!(validate_focus_state(&state, &captured).is_err());
        assert!(!state.takeover.load(Ordering::SeqCst));
        state.input_policy.update_holds(0, true);
        assert!(
            validate_focus_state(&state, &captured).is_err(),
            "release must not validate old generation"
        );
        captured.input_generation = state.input_policy.generation();
        assert!(validate_focus_state(&state, &captured).is_ok());
        let flag = Arc::new(AtomicBool::new(true));
        CURRENT_CANCEL_FLAG.with(|f| *f.borrow_mut() = Some(flag));
        assert!(validate_focus_state(&state, &captured)
            .unwrap_err()
            .contains("client request was canceled"));
        CURRENT_CANCEL_FLAG.with(|f| *f.borrow_mut() = None);
        assert!(validate_focus_state(&state, &captured).is_ok());
        test_live_scope(&state, "focus-escape");
        physical_activity(&state, true, false);
        assert!(validate_focus_state(&state, &captured).is_err());
        assert!(!state.takeover.load(Ordering::SeqCst));
        assert!(!dir.join("takeover.json").exists());
        let marker = takeover::Latch::startup(dir.join("takeover.json"), "new-review-session").unwrap();
        assert_eq!(marker.reason.source, "backend_startup");
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn independent_focus_age_and_reused_id_refuse_before_dispatch_with_command_shim() {
        // Only a filtered child test process mutates PATH for the command shim.
        // Default parallel tests in the parent retain their original environment.
        const CHILD_SENTINEL: &str = "WEASEL_CU_PRIVATE_FOCUS_PROBE_CHILD";
        const TEST_NAME: &str = "release_recovery_tests::independent_focus_age_and_reused_id_refuse_before_dispatch_with_command_shim";
        if env::var(CHILD_SENTINEL).as_deref() != Ok("1") {
            let child = Command::new(env::current_exe().unwrap())
                .args(["--exact", TEST_NAME, "--test-threads=1"])
                .env(CHILD_SENTINEL, "1")
                .output()
                .unwrap();
            assert!(
                child.status.success(),
                "isolated focus probe failed: stdout={} stderr={}",
                String::from_utf8_lossy(&child.stdout),
                String::from_utf8_lossy(&child.stderr)
            );
            assert!(
                String::from_utf8_lossy(&child.stdout).contains("1 passed; 0 failed"),
                "isolated focus probe did not execute exactly one test"
            );
            return;
        }
        // Command shim and Focus only. No real Niri/Wayland/actuator.
        let (state, dir) = cooperative_fixture("independent-focus-dispatch");
        let shimdir = dir.join("fake-bin");
        private_dir(&shimdir).unwrap();
        let shim = shimdir.join("niri");
        fs::write(&shim, b"#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$WEASEL_PRIVATE_FOCUS_CALLS\"\ncase \"$*\" in\n 'msg -j windows') printf '%s\\n' '[{\"id\":77,\"pid\":999,\"app_id\":\"replacement-app\",\"workspace_id\":1,\"is_focused\":true}]' ;;\n *) printf '%s\\n' '{}' ;;\nesac\n").unwrap();
        fs::set_permissions(&shim, fs::Permissions::from_mode(0o700)).unwrap();
        let calls = dir.join("fake-calls.txt");
        let previous_path = env::var_os("PATH");
        env::set_var("PATH", &shimdir);
        env::set_var("WEASEL_PRIVATE_FOCUS_CALLS", &calls);
        let args = json!({"observation_id":"cloned-before-recovery","window_id":77,"actions":[{"kind":"focus","window_id":77}],"observe_after":false});
        let mut saved = obs(&dir);
        saved.at = Instant::now() - Duration::from_secs(61);
        saved.windows =
            json!([{"id":77,"pid":42,"app_id":"original-app","workspace_id":1,"is_focused":true}]);
        state
            .observations
            .lock()
            .unwrap()
            .insert(saved.id.clone(), saved.clone());
        let old = act(&state, &args, 77).unwrap();
        saved.at = Instant::now();
        state
            .observations
            .lock()
            .unwrap()
            .insert(saved.id.clone(), saved);
        let changed = act(&state, &args, 77).unwrap();
        if let Some(path) = previous_path {
            env::set_var("PATH", path);
        } else {
            env::remove_var("PATH");
        }
        env::remove_var("WEASEL_PRIVATE_FOCUS_CALLS");
        for result in [old, changed] {
            let data = result_data(&result);
            assert_eq!(data["status"], "failed");
            assert_eq!(data["completed_actions"], 0);
            assert_eq!(data["effects"][0]["dispatch_started"], false);
            assert_eq!(data["effects"][0]["may_have_partial_effects"], false);
            assert_eq!(data["actor_release_confirmed"], true);
        }
        // Old age performs no command; fresh-but-changed identity only reads.
        assert_eq!(fs::read_to_string(&calls).unwrap(), "msg -j windows\n");
        assert!(state.active.lock().unwrap().is_null());
        assert_eq!(state.queued.load(Ordering::SeqCst), 0);
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn escape_canceled_batch_stays_canceled_after_noop_resume() {
        let (state, dir) = cooperative_fixture("escape-stale-noop-resume");
        let expected = state.epoch.load(Ordering::SeqCst);
        test_live_scope(&state, "stopped-task");
        physical_activity(&state, true, false);
        let actual = state.epoch.load(Ordering::SeqCst);
        let resume = handle(&state, "desktop_resume", &json!({}), actual).unwrap();
        assert_eq!(result_data(&resume)["status"], "already_resumed");
        assert_eq!(state.epoch.load(Ordering::SeqCst), actual);
        assert!(check_epoch(&state, Some(expected)).is_err());
        let _gate = state.task_scope_gate.lock().unwrap();
        assert!(authorize_action_task(&state, Some("stopped-task"), "unused".into()).is_err());
        assert!(!state.takeover.load(Ordering::SeqCst));
        assert!(!dir.join("takeover.json").exists());
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn r8_true_resume_requires_observed_binding_and_respects_scoped_cancel() {
        let (mut state, dir) = fixture("r8-bound-resume");
        state.started = Instant::now() - Duration::from_secs(2);
        state.release_confirmed.store(true, Ordering::SeqCst);
        *state.human_monitor.lock().unwrap() = json!({"available":true,"watched_devices":1});
        let epoch = state.epoch.load(Ordering::SeqCst);
        assert!(handle(&state, "desktop_resume", &json!({}), epoch).is_err());
        assert!(handle(
            &state,
            "desktop_resume",
            &json!({"expected_epoch":epoch,"expected_session_id":"other-backend"}),
            epoch
        )
        .is_err());
        let args = json!({"expected_epoch":epoch,"expected_session_id":state.session_id});
        let flag = Arc::new(AtomicBool::new(true));
        CURRENT_CANCEL_FLAG.with(|f| *f.borrow_mut() = Some(flag));
        let canceled = handle(&state, "desktop_resume", &args, epoch);
        CURRENT_CANCEL_FLAG.with(|f| *f.borrow_mut() = None);
        assert!(canceled.unwrap_err().contains("canceled"));
        assert!(state.takeover.load(Ordering::SeqCst));
        assert_eq!(state.epoch.load(Ordering::SeqCst), epoch);
        assert!(dir.join("takeover.json").exists());
        let resumed = handle(&state, "desktop_resume", &args, epoch).unwrap();
        assert_eq!(result_data(&resumed)["status"], "resumed");
        assert!(!state.takeover.load(Ordering::SeqCst));
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn r8_resume_waiting_on_marker_refuses_concurrent_input_without_erasing_explicit_takeover() {
        let (mut raw, dir) = fixture("r8-marker-race");
        raw.started = Instant::now() - Duration::from_secs(2);
        raw.release_confirmed.store(true, Ordering::SeqCst);
        *raw.human_monitor.lock().unwrap() = json!({"available":true,"watched_devices":1});
        let state = Arc::new(raw);
        let epoch = state.epoch.load(Ordering::SeqCst);
        let marker_guard = state.takeover_marker.lock().unwrap();
        let worker = state.clone();
        let resume = thread::spawn(move || {
            handle(
                &worker,
                "desktop_resume",
                &json!({"expected_epoch":epoch,"expected_session_id":worker.session_id}),
                epoch,
            )
        });
        let until = Instant::now() + Duration::from_secs(2);
        while !fs::read_to_string(dir.join("events.jsonl"))
            .unwrap()
            .contains("resume_preparing")
        {
            assert!(
                Instant::now() < until,
                "resume did not reach the marker gate"
            );
            thread::sleep(Duration::from_millis(1));
        }
        let worker = state.clone();
        let activity = thread::spawn(move || physical_activity(&worker, false, false));
        activity.join().unwrap();
        drop(marker_guard);
        assert!(resume.join().unwrap().is_err());
        assert!(state.takeover.load(Ordering::SeqCst));
        assert_eq!(
            state.epoch.load(Ordering::SeqCst),
            epoch,
            "concurrent ordinary activity does not create a stop epoch"
        );
        let marker = state.takeover_marker.lock().unwrap();
        assert_eq!(marker.reason.source, "explicit_desktop_takeover");
        assert_eq!(marker.reason.session_id, state.session_id);
        assert!(!marker.reason.cause_id.is_empty());
        assert_eq!(
            serde_json::from_slice::<Value>(&fs::read(dir.join("takeover.json")).unwrap()).unwrap()
                ["cause_id"],
            marker.reason.cause_id
        );
        drop(marker);
        fs::remove_dir_all(dir).unwrap();
    }
    #[test]
    fn explicit_takeover_after_escape_stays_persisted_through_more_escape() {
        let (state, dir) = cooperative_fixture("escape-explicit-precedence");
        test_live_scope(&state, "real-escape-task");
        physical_activity(&state, true, false);
        let epoch = state.epoch.load(Ordering::SeqCst);
        assert!(!dir.join("takeover.json").exists());
        physical_activity(&state, true, true);
        assert_eq!(state.epoch.load(Ordering::SeqCst), epoch, "an idle simulation cannot revive the canceled scope");
        handle(&state, "desktop_takeover", &json!({}), epoch).unwrap();
        let explicit = fs::read(dir.join("takeover.json")).unwrap();
        *state.active.lock().unwrap() = json!({"task_id":"release-window"});
        physical_activity(&state, true, false);
        assert!(state.takeover.load(Ordering::SeqCst));
        assert_eq!(state.takeover_marker.lock().unwrap().reason.source, "explicit_desktop_takeover");
        assert_eq!(fs::read(dir.join("takeover.json")).unwrap(), explicit);
        let restarted = takeover::Latch::startup(dir.join("takeover.json"), "new-backend").unwrap();
        assert_eq!(restarted.reason.source, "explicit_desktop_takeover");
        assert_eq!(fs::read(dir.join("takeover.json")).unwrap(), explicit);
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn r8_capture_status_distinguishes_unattempted_failure_and_invalidation_preserving_original_error(
    ) {
        let (state, dir) = fixture("r8-capture-state");
        state.capture_available.store(false, Ordering::SeqCst);
        let original = state.last_result.lock().unwrap().clone();
        let initial = capture_status(&state).unwrap();
        assert_eq!(initial["capture_state"], "not_attempted");
        assert_eq!(initial["capture_not_attempted"], true);
        assert_eq!(initial["capture_failed"], false);
        state.capture_attempts.fetch_add(1, Ordering::SeqCst);
        let epoch = state.epoch.load(Ordering::SeqCst);
        let flag = Arc::new(AtomicBool::new(true));
        CURRENT_CANCEL_FLAG.with(|f| *f.borrow_mut() = Some(flag));
        capture_unavailable(&state, epoch, "scoped-cancel-is-not-capture-loss");
        CURRENT_CANCEL_FLAG.with(|f| *f.borrow_mut() = None);
        assert_eq!(capture_status(&state).unwrap()["capture_failed"], false);
        assert_eq!(state.epoch.load(Ordering::SeqCst), epoch);
        capture_unavailable(&state, epoch, "private-capture-failure");
        let failed = capture_status(&state).unwrap();
        assert_eq!(failed["capture_state"], "failed");
        assert_eq!(failed["capture_not_attempted"], false);
        assert_eq!(
            failed["capture_failure_category"],
            "private-capture-failure"
        );
        accept_capture(&state).unwrap();
        assert_eq!(capture_status(&state).unwrap()["capture_state"], "ready");
        assert_eq!(capture_status(&state).unwrap()["capture_failed"], false);
        assert_eq!(
            capture_status(&state).unwrap()["capture_failure_category"],
            Value::Null
        );
        state.capture_available.store(false, Ordering::SeqCst); // existing recovery invalidation gate
        let invalidated = capture_status(&state).unwrap();
        assert_eq!(invalidated["capture_state"], "no_current_observation");
        assert_eq!(invalidated["capture_failed"], false);
        assert_eq!(invalidated["capture_not_attempted"], false);
        assert_eq!(*state.last_result.lock().unwrap(), original);
        fs::remove_dir_all(dir).unwrap();
    }
    #[test]
    fn r8_resume_readiness_uses_actual_quiet_monitor_and_known_released_controls_without_capture() {
        let (mut state, dir) = fixture("r8-readiness");
        state.capture_available.store(false, Ordering::SeqCst);
        assert_eq!(resume_input_readiness(&state).unwrap()["ready"], false);
        state.started = Instant::now() - Duration::from_secs(2);
        *state.human_monitor.lock().unwrap() = json!({"available":true,"watched_devices":1});
        let ready = resume_input_readiness(&state).unwrap();
        assert_eq!(ready["ready"], true);
        assert_eq!(ready["required_quiet_ms"], 300);
        assert_eq!(ready["last_activity_age_ms"], Value::Null);
        assert_eq!(ready["wait_remaining_ms"], 0);
        state.input_policy.update_holds(1, true);
        assert_eq!(resume_input_readiness(&state).unwrap()["ready"], false);
        state.input_policy.update_holds(0, false);
        assert_eq!(resume_input_readiness(&state).unwrap()["ready"], false);
        state.input_policy.update_holds(0, true);
        state
            .last_human_ms
            .store(state.started.elapsed().as_millis() as u64, Ordering::SeqCst);
        let recent = resume_input_readiness(&state).unwrap();
        assert_eq!(recent["recent_activity_quiet_interval_complete"], false);
        assert!(recent["wait_remaining_ms"].as_u64().unwrap() > 0);
        state.last_human_ms.store(
            state.started.elapsed().as_millis() as u64 - 350,
            Ordering::SeqCst,
        );
        assert_eq!(resume_input_readiness(&state).unwrap()["ready"], true);
        state.release_confirmed.store(true, Ordering::SeqCst);
        let epoch = state.epoch.load(Ordering::SeqCst);
        let resumed = handle(
            &state,
            "desktop_resume",
            &json!({"expected_epoch":epoch,"expected_session_id":state.session_id}),
            epoch,
        )
        .unwrap();
        assert_eq!(result_data(&resumed)["status"], "resumed");
        assert!(
            !state.capture_available.load(Ordering::SeqCst),
            "resume must not create or attest capture"
        );
        assert!(handle(
            &state,
            "desktop_resume",
            &json!({"expected_epoch":"bad"}),
            epoch
        )
        .is_err());
        assert!(handle(
            &state,
            "desktop_resume",
            &json!({"unrecognized":true}),
            epoch
        )
        .is_err());
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn r8_capture_lifecycle_snapshots_cannot_mix_failure_with_success() {
        let (state, dir) = cooperative_fixture("r8-capture-snapshot-race");
        state.capture_attempts.store(1, Ordering::SeqCst);
        let worker = state.clone();
        let writer = thread::spawn(move || {
            for _ in 0..128 {
                let epoch = worker.epoch.load(Ordering::SeqCst);
                capture_unavailable(&worker, epoch, "private-snapshot-failure");
                accept_capture(&worker).unwrap();
            }
        });
        for _ in 0..1024 {
            let status = capture_status(&state).unwrap();
            assert!(!(status["capture_available"] == true && status["capture_failed"] == true));
            assert_eq!(status["capture_not_attempted"], false);
        }
        writer.join().unwrap();
        assert_eq!(capture_status(&state).unwrap()["capture_state"], "ready");
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn r8_bound_wire_method_is_internal_only_and_enforces_the_same_actor_binding() {
        let (mut state, dir) = fixture("r8-wire-method");
        state.started = Instant::now() - Duration::from_secs(2);
        state.release_confirmed.store(true, Ordering::SeqCst);
        *state.human_monitor.lock().unwrap() = json!({"available":true,"watched_devices":1});
        assert!(tools()
            .as_array()
            .unwrap()
            .iter()
            .all(|t| t["name"] != "desktop_resume_bound"));
        let epoch = state.epoch.load(Ordering::SeqCst);
        assert!(handle(&state, "desktop_resume_bound", &json!({}), epoch).is_err());
        assert!(state.takeover.load(Ordering::SeqCst));
        let result = handle(
            &state,
            "desktop_resume_bound",
            &json!({"expected_epoch":epoch,"expected_session_id":state.session_id}),
            epoch,
        )
        .unwrap();
        assert_eq!(result_data(&result)["status"], "resumed");
        fs::remove_dir_all(dir).unwrap();
    }
    #[test]
    fn cooperative_idle_activity_preserves_epoch_and_refuses_stale_batch_without_input() {
        let (state, dir) = cooperative_fixture("cooperative-stale");
        physical_activity(&state, false, false);
        assert_eq!(state.epoch.load(Ordering::SeqCst), 77);
        assert!(!state.takeover.load(Ordering::SeqCst));
        assert!(!dir.join("takeover.json").exists());
        assert!(
            check_epoch(&state, Some(77)).is_ok(),
            "idle request remains usable"
        );
        let result = act(&state, &json!({"observation_id":"cloned-before-recovery","actions":[{"kind":"wait","ms":1}],"observe_after":false}), 77).unwrap();
        let data = result_data(&result);
        assert_eq!(data["status"], "input_conflict");
        assert_eq!(data["completed_actions"], 0);
        assert_eq!(data["effects"], json!([]));
        assert_eq!(data["takeover_latched"], false);
        assert_eq!(data["fresh_observation_required"], true);
        assert!(state.active.lock().unwrap().is_null());
        assert_eq!(state.queued.load(Ordering::SeqCst), 0);
        fs::remove_dir_all(dir).unwrap();
    }
    #[test]
    fn cooperative_active_conflict_releases_stops_pending_actions_and_fresh_work_needs_no_resume() {
        let (state, dir) = cooperative_fixture("cooperative-active");
        let timer = activity_after_writer_active(state.clone(), false, 1);
        let start = Instant::now();
        let result = act(&state, &json!({"observation_id":"cloned-before-recovery","actions":[{"kind":"wait","ms":1},{"kind":"wait","ms":2000},{"kind":"wait","ms":1}],"observe_after":false}), 77).unwrap();
        timer.join().unwrap();
        let data = result_data(&result);
        assert_eq!(data["status"], "input_conflict");
        assert_eq!(data["completed_actions"], 1);
        assert!(
            data["effects"].as_array().unwrap().len() <= 2,
            "remaining action must not dispatch"
        );
        assert!(start.elapsed() < Duration::from_millis(500));
        assert_eq!(data["actor_release_confirmed"], true);
        assert_eq!(data["automatic_replay"], false);
        assert_eq!(state.epoch.load(Ordering::SeqCst), 77);
        assert!(!state.takeover.load(Ordering::SeqCst));
        assert!(state.active.lock().unwrap().is_null());
        assert_eq!(state.queued.load(Ordering::SeqCst), 0);
        assert!(!dir.join("takeover.json").exists());
        // A newly observed target is enough; no desktop_resume or replay of the
        // interrupted operation. This fixture deliberately dispatches Wait only.
        let mut fresh = obs(&dir);
        fresh.id = "fresh-after-ordinary-input".into();
        fresh.input_generation = state.input_policy.generation();
        state
            .observations
            .lock()
            .unwrap()
            .insert(fresh.id.clone(), fresh);
        let recovered = act(&state, &json!({"observation_id":"fresh-after-ordinary-input","actions":[{"kind":"wait","ms":1}],"observe_after":false}), 77).unwrap();
        assert_eq!(result_data(&recovered)["status"], "dispatched");
        assert_eq!(result_data(&recovered)["completed_actions"], 1);
        fs::remove_dir_all(dir).unwrap();
    }
    #[test]
    fn physical_escape_cancels_active_work_releases_and_allows_only_fresh_work() {
        let (state, dir) = cooperative_fixture("cooperative-escape");
        let timer = activity_after_writer_active(state.clone(), true, 0);
        let result = act(&state, &json!({"task_id":"stopped-active","observation_id":"cloned-before-recovery","actions":[{"kind":"wait","ms":2000},{"kind":"wait","ms":1}],"observe_after":false}), 77).unwrap();
        timer.join().unwrap();
        let data = result_data(&result);
        assert_eq!(data["status"], "canceled");
        assert_eq!(data["actor_release_confirmed"], true);
        assert_eq!(data["takeover_latched"], false);
        assert!(data["completed_actions"].as_u64().unwrap() < 2);
        let stored = state.last_result.lock().unwrap().clone();
        for field in ["status", "actor_release_confirmed", "task_id", "completed_actions", "error"] {
            assert_eq!(stored[field], data[field], "stored cancellation field {field}");
        }
        assert!(state.active.lock().unwrap().is_null());
        assert_eq!(state.queued.load(Ordering::SeqCst), 0);
        assert!(!dir.join("takeover.json").exists());
        let epoch = state.epoch.load(Ordering::SeqCst);
        assert_eq!(epoch, 78);
        let stale_call = json!({"task_id":"fresh-active","observation_id":"cloned-before-recovery","actions":[{"kind":"wait","ms":0}],"observe_after":false});
        let stale = act(&state, &stale_call, epoch).unwrap();
        assert_eq!(stale["isError"], true);
        assert_eq!(result_data(&stale)["status"], "input_conflict", "stale observations refuse before admission or dispatch");
        let mut fresh = obs(&dir);
        fresh.id = "after-escape".into();
        fresh.epoch = epoch;
        fresh.input_generation = state.input_policy.generation();
        state.observations.lock().unwrap().insert(fresh.id.clone(), fresh);
        let mut fresh_call = stale_call;
        fresh_call["observation_id"] = json!("after-escape");
        fresh_call["task_id"] = json!("stopped-active");
        assert!(act(&state, &fresh_call, epoch).unwrap_err().contains("already used or canceled"));
        fresh_call["task_id"] = json!("fresh-active");
        let result = act(&state, &fresh_call, epoch).unwrap();
        assert_eq!(result_data(&result)["status"], "dispatched");
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn physical_escape_while_idle_only_invalidates_targets_and_does_not_persist() {
        let (state, dir) = cooperative_fixture("escape-idle-is-not-a-takeover");
        let epoch = state.epoch.load(Ordering::SeqCst);
        let generation = state.input_policy.generation();
        physical_activity(&state, true, false);
        assert!(!state.takeover.load(Ordering::SeqCst));
        assert_eq!(state.epoch.load(Ordering::SeqCst), epoch);
        assert_eq!(state.input_policy.generation(), generation + 1);
        assert!(!dir.join("takeover.json").exists());
        assert!(state.active.lock().unwrap().is_null());
        assert_eq!(state.queued.load(Ordering::SeqCst), 0);
        let status = result_data(&handle(&state, "desktop_status", &json!({}), epoch).unwrap());
        assert_eq!(status["escape_policy"], "cancel_current_task");
        assert_eq!(status["persistent_escape_stop"], false);
        assert_eq!(status["idle_escape_latches"], false);
        assert!(status.get("rearm_warning").is_none());
        fs::remove_dir_all(dir).unwrap();
    }
    #[test]
    fn task_indicator_escape_consumes_old_scope_and_accepts_new_task_without_resume() {
        let (state, dir) = cooperative_fixture("escape-task-scope");
        let old_binding = json!({"event":"begin","task_id":"owned-task-gap","session_id":state.session_id,"expected_epoch":77,"scope_token":"owner-gap"});
        let initial = result_data(&handle(&state, "desktop_indicator_lifecycle", &old_binding, 77).unwrap());
        assert_eq!(initial["task_scope_active"], true);
        // The indicator owns the workflow across model-thinking gaps, even
        // without an executing action batch.
        physical_activity(&state, true, false);
        assert!(!state.takeover.load(Ordering::SeqCst));
        assert_eq!(state.epoch.load(Ordering::SeqCst), 78);
        assert!(!dir.join("takeover.json").exists());
        assert_eq!(*state.last_result.lock().unwrap(), json!({"status":"failed","error":"original timeout preserved"}));
        let mut ended = old_binding.clone();
        ended["event"] = json!("end");
        let ended = result_data(&handle(&state, "desktop_indicator_lifecycle", &ended, 78).unwrap());
        assert_eq!(ended["task_scope_active"], false);
        assert!(!state.takeover.load(Ordering::SeqCst));
        assert!(handle(&state, "desktop_indicator_lifecycle", &old_binding, 78).is_err());
        let mut rebound_old = old_binding;
        rebound_old["expected_epoch"] = json!(78);
        assert!(handle(&state, "desktop_indicator_lifecycle", &rebound_old, 78).unwrap_err().contains("already used"));
        let fresh = json!({"event":"begin","task_id":"new-user-request","session_id":state.session_id,"expected_epoch":78,"scope_token":"new-owner"});
        let accepted = result_data(&handle(&state, "desktop_indicator_lifecycle", &fresh, 78).unwrap());
        assert_eq!(accepted["task_scope_active"], true);
        // Late cleanup by the stopped indicator cannot end the new scope.
        let mut late_end = rebound_old;
        late_end["event"] = json!("end");
        handle(&state, "desktop_indicator_lifecycle", &late_end, 78).unwrap();
        assert!(state.task_scopes.lock().unwrap().contains_key("new-user-request"));
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn physical_escape_cancels_all_queued_batches_before_actor_admission() {
        let (state, dir) = cooperative_fixture("escape-queued");
        let actor = state.actor.lock().unwrap();
        let spawn = |task_id: &'static str| {
            let state = state.clone();
            thread::spawn(move || act(&state, &json!({"task_id":task_id,"observation_id":"cloned-before-recovery","observe_after":false,"actions":[{"kind":"wait","ms":0}]}), 77))
        };
        let first = spawn("queued-first");
        let second = spawn("queued-second");
        let until = Instant::now() + Duration::from_secs(1);
        while state.queued.load(Ordering::SeqCst) != 2 {
            assert!(Instant::now() < until, "both batches must reach the queue");
            thread::sleep(Duration::from_millis(1));
        }
        assert!(state.active.lock().unwrap().is_null());
        physical_activity(&state, true, false);
        drop(actor);
        assert!(first.join().unwrap().unwrap_err().contains("Canceled"));
        assert!(second.join().unwrap().unwrap_err().contains("Canceled"));
        assert_eq!(state.queued.load(Ordering::SeqCst), 0);
        assert_eq!(state.epoch.load(Ordering::SeqCst), 78);
        assert!(state.active.lock().unwrap().is_null());
        assert!(state.release_confirmed.load(Ordering::SeqCst));
        assert!(!state.takeover.load(Ordering::SeqCst));
        assert!(!dir.join("takeover.json").exists());
        let _gate = state.task_scope_gate.lock().unwrap();
        for task in ["queued-first", "queued-second"] {
            assert!(authorize_action_task(&state, Some(task), "unused".into()).is_err());
        }
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn physical_escape_during_preparation_refuses_new_scope_until_release_confirmed() {
        let (state, dir) = cooperative_fixture("escape-preparing-release");
        test_live_scope(&state, "preparing-old");
        *state.active.lock().unwrap() = json!({"task_id":"preparing-old","phase":"preparing_global_keyboard"});
        state.release_confirmed.store(false, Ordering::SeqCst);
        physical_activity(&state, true, false);
        let binding = json!({"event":"begin","task_id":"after-preparation","session_id":state.session_id,"expected_epoch":78,"scope_token":"new-owner"});
        assert!(!state.takeover.load(Ordering::SeqCst));
        assert!(handle(&state, "desktop_indicator_lifecycle", &binding, 78).is_err());
        *state.active.lock().unwrap() = Value::Null;
        assert!(handle(&state, "desktop_indicator_lifecycle", &binding, 78).is_err(), "idle alone does not prove release");
        state.release_confirmed.store(true, Ordering::SeqCst);
        assert_eq!(result_data(&handle(&state, "desktop_indicator_lifecycle", &binding, 78).unwrap())["task_scope_active"], true);
        assert!(!dir.join("takeover.json").exists());
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn stale_task_action_after_escape_is_rejected_while_new_task_keeps_multi_batch_access() {
        let (mut state, dir) = cooperative_fixture("task-scope-action-admission");
        Arc::get_mut(&mut state).unwrap().started = Instant::now() - Duration::from_secs(4);
        *state.human_monitor.lock().unwrap() = json!({"available":true,"watched_devices":1});
        state.release_confirmed.store(true, Ordering::SeqCst);
        handle(
            &state,
            "desktop_indicator_lifecycle",
            &json!({"event":"begin","task_id":"old-A","session_id":state.session_id,"expected_epoch":77,"scope_token":"owner-A"}),
            77,
        ).unwrap();
        physical_activity(&state, true, false);
        let stopped_epoch = state.epoch.load(Ordering::SeqCst);
        *state.active.lock().unwrap() = Value::Null;
        state.last_human_ms.store(0, Ordering::SeqCst);
        let args = json!({"event":"begin","task_id":"new-B","session_id":state.session_id,"expected_epoch":stopped_epoch,"scope_token":"owner-B"});
        handle(&state, "desktop_indicator_lifecycle", &args, stopped_epoch).unwrap();
        let mut fresh = state.observations.lock().unwrap().values().next().unwrap().clone();
        fresh.epoch = state.epoch.load(Ordering::SeqCst);
        fresh.input_generation = state.input_policy.generation();
        fresh.at = Instant::now();
        let observation_id = fresh.id.clone();
        state.observations.lock().unwrap().insert(observation_id.clone(), fresh);
        let action_call = |task_id: &str| json!({
            "observation_id":observation_id,
            "task_id":task_id,
            "observe_after":false,
            "include_image":false,
            "actions":[{"kind":"wait","ms":0}]
        });
        assert!(handle(&state, "desktop_act", &action_call("old-A"), state.epoch.load(Ordering::SeqCst))
            .unwrap_err().contains("current authorized task scope"));
        handle(&state, "desktop_act", &action_call("new-B"), state.epoch.load(Ordering::SeqCst)).unwrap();
        handle(&state, "desktop_act", &action_call("new-B"), state.epoch.load(Ordering::SeqCst)).unwrap();
        let admission = |task_id: &str| {
            let _gate = state.task_scope_gate.lock().unwrap();
            authorize_action_task(&state, Some(task_id), "unused-generated".into())
        };
        assert!(admission("old-A").unwrap_err().contains("current authorized task scope"));
        assert_eq!(admission("new-B").unwrap(), ("new-B".into(), true));
        assert_eq!(admission("new-B").unwrap(), ("new-B".into(), true), "multiple batches in the same live indicator task remain valid");
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn action_only_identity_is_tombstoned_on_first_admission() {
        let (state, dir) = cooperative_fixture("action-only-id-tombstone");
        {
            let _gate = state.task_scope_gate.lock().unwrap();
            assert_eq!(authorize_action_task(&state, Some("single-action-task"), "unused".into()).unwrap(), ("single-action-task".into(), false));
        }
        assert!(state.task_scope_ids.lock().unwrap().contains("single-action-task"));
        let _gate = state.task_scope_gate.lock().unwrap();
        assert!(authorize_action_task(&state, Some("single-action-task"), "unused".into()).is_err());
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn explicit_takeover_remains_persisted_and_rejects_retired_rearm_binding() {
        let (mut state, dir) = cooperative_fixture("explicit-escape-precedence");
        Arc::get_mut(&mut state).unwrap().started = Instant::now() - Duration::from_secs(4);
        *state.human_monitor.lock().unwrap() = json!({"available":true,"watched_devices":1});
        state.release_confirmed.store(true, Ordering::SeqCst);
        handle(&state, "desktop_takeover", &json!({}), 77).unwrap();
        let explicit = fs::read(dir.join("takeover.json")).unwrap();
        *state.active.lock().unwrap() = json!({"task_id":"release-window"});
        physical_activity(&state, true, false);
        assert_eq!(state.takeover_marker.lock().unwrap().reason.source, "explicit_desktop_takeover");
        assert_eq!(fs::read(dir.join("takeover.json")).unwrap(), explicit);
        *state.active.lock().unwrap() = Value::Null;
        state.last_human_ms.store(0, Ordering::SeqCst);
        let epoch = state.epoch.load(Ordering::SeqCst);
        state.last_human_ms.store(0, Ordering::SeqCst);
        let rearm = json!({"expected_epoch":epoch,"expected_session_id":state.session_id,"task_id":"must-not-bypass","scope_token":"owner"});
        assert!(handle(&state, "desktop_resume", &rearm, epoch).unwrap_err().contains("Unknown desktop resume field"));
        assert!(!state.task_scope_ids.lock().unwrap().contains("must-not-bypass"));
        assert_eq!(state.takeover_marker.lock().unwrap().reason.source, "explicit_desktop_takeover");
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn stale_indicator_begin_after_escape_or_cancel_cannot_reserve_task_id() {
        let (state, dir) = cooperative_fixture("cancel-before-indicator-begin");
        test_live_scope(&state, "old-scope");
        physical_activity(&state, true, false);
        let canceled_epoch = state.epoch.load(Ordering::SeqCst);
        handle(&state, "desktop_cancel", &json!({}), canceled_epoch).unwrap();
        let stale = json!({"event":"begin","expected_epoch":canceled_epoch,"session_id":state.session_id,"task_id":"new-task","scope_token":"new-owner"});
        let current_epoch = state.epoch.load(Ordering::SeqCst);
        assert!(handle(&state, "desktop_indicator_lifecycle", &stale, current_epoch).is_err());
        assert!(!state.task_scope_ids.lock().unwrap().contains("new-task"));
        let mut fresh = stale;
        fresh["expected_epoch"] = json!(current_epoch);
        assert_eq!(result_data(&handle(&state, "desktop_indicator_lifecycle", &fresh, current_epoch).unwrap())["task_scope_active"], true);
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn cooperative_controlled_escape_preserves_prior_explicit_marker() {
        let (state, dir) = fixture("cooperative-simulation");
        let legacy = fs::read(dir.join("takeover.json")).unwrap();
        physical_activity(&state, false, false);
        assert_eq!(fs::read(dir.join("takeover.json")).unwrap(), legacy);
        assert_eq!(state.epoch.load(Ordering::SeqCst), 77);
        physical_activity(&state, true, true);
        assert_eq!(
            state.takeover_marker.lock().unwrap().reason.source,
            "explicit_desktop_takeover"
        );
        assert_eq!(fs::read(dir.join("takeover.json")).unwrap(), legacy);
        fs::remove_dir_all(dir).unwrap();
    }
    #[test]
    fn cooperative_conflict_during_capture_does_not_claim_backend_failure_or_cancel_others() {
        let (state, dir) = cooperative_fixture("cooperative-capture");
        let _guard = input_policy::Guard::bind(state.input_policy.generation());
        physical_activity(&state, false, false);
        capture_unavailable(&state, 77, "fixture-interrupted-visual-capture");
        assert_eq!(state.epoch.load(Ordering::SeqCst), 77);
        assert!(state.capture_available.load(Ordering::SeqCst));
        assert_eq!(state.observations.lock().unwrap().len(), 1);
        assert!(!state.takeover.load(Ordering::SeqCst));
        fs::remove_dir_all(dir).unwrap();
    }
    #[test]
    fn cooperative_old_semantic_handle_cannot_be_combined_with_new_visual_observation() {
        let (state, dir) = cooperative_fixture("cooperative-semantic");
        let window = json!({"id":1,"pid":42,"app_id":"offline-fixture","workspace_id":1});
        let snapshot: atspi::Snapshot = serde_json::from_value(json!({"pid":42,"requested_title":"fixture","application":{"bus":":1.999","path":"/fixture"},"window":{"bus":":1.999","path":"/fixture/window"},"nodes":[],"complete":true,"visible_modals":[],"semantic_scope":"offline fixture","sole_window_fallback_allowed":false,"bus_guid":"never-connect"})).unwrap();
        let object = snapshot.window.clone();
        state.semantic_targets.lock().unwrap().insert(
            "offline-handle".into(),
            SemanticTarget {
                background: false,
                at: Instant::now(),
                epoch: 77,
                input_generation: state.input_policy.generation(),
                window: window.clone(),
                snapshot: Arc::new(snapshot),
                object,
            },
        );
        let mut visual = obs(&dir);
        visual.focused_window = Some(window);
        assert!(semantic_target(&state, &visual, "offline-handle").is_ok());
        physical_activity(&state, false, false);
        visual.input_generation = state.input_policy.generation();
        assert!(semantic_target(&state, &visual, "offline-handle")
            .err()
            .unwrap()
            .contains("Ordinary physical input"));
        assert_eq!(state.epoch.load(Ordering::SeqCst), 77);
        assert!(!state.takeover.load(Ordering::SeqCst));
        fs::remove_dir_all(dir).unwrap();
    }
    #[test]
    fn cooperative_held_control_and_unknown_snapshot_block_even_fresh_batch_without_latch() {
        let (state, dir) = cooperative_fixture("cooperative-held");
        for (count, known) in [(1, true), (2, true), (0, false)] {
            state.input_policy.update_holds(count, known);
            let mut fresh = obs(&dir);
            fresh.input_generation = state.input_policy.generation();
            state
                .observations
                .lock()
                .unwrap()
                .insert(fresh.id.clone(), fresh);
            let result = act(&state, &json!({"observation_id":"cloned-before-recovery","actions":[{"kind":"wait","ms":1}],"observe_after":false}), 77).unwrap();
            let data = result_data(&result);
            assert_eq!(data["status"], "input_conflict");
            assert_eq!(data["effects"], json!([]));
            assert_eq!(data["actor_release_confirmed"], true);
            assert_eq!(state.epoch.load(Ordering::SeqCst), 77);
            assert!(!state.takeover.load(Ordering::SeqCst));
            assert!(!dir.join("takeover.json").exists());
        }
        physical_activity(&state, false, false); // observed key/button release
        state.input_policy.update_holds(0, true);
        let mut fresh = obs(&dir);
        fresh.input_generation = state.input_policy.generation();
        state
            .observations
            .lock()
            .unwrap()
            .insert(fresh.id.clone(), fresh);
        let recovered = act(&state, &json!({"observation_id":"cloned-before-recovery","actions":[{"kind":"wait","ms":1}],"observe_after":false}), 77).unwrap();
        assert_eq!(result_data(&recovered)["status"], "dispatched");
        fs::remove_dir_all(dir).unwrap();
    }
    #[test]
    fn cooperative_key_state_read_failure_is_unknown_never_invented_release() {
        let (state, dir) = cooperative_fixture("cooperative-read-failure");
        let ordinary_file = fs::File::open(dir.join("events.jsonl")).unwrap();
        assert_eq!(physical_held_controls(&ordinary_file, true), None);
        assert_eq!(physical_held_controls(&ordinary_file, false), Some(0));
        state.input_policy.update_holds(0, false);
        assert!(!state.input_policy.controls_released());
        assert_eq!(state.epoch.load(Ordering::SeqCst), 77);
        assert!(!state.takeover.load(Ordering::SeqCst));
        fs::remove_dir_all(dir).unwrap();
    }
    #[test]
    fn resume_already_unlatched_is_idempotent_despite_recent_input_and_historical_cause() {
        let (state, dir) = cooperative_fixture("resume-idempotent");
        {
            let mut latch = state.takeover_marker.lock().unwrap();
            latch
                .set("physical_input_activity", &state.session_id)
                .unwrap();
            latch.clear().unwrap();
        }
        physical_activity(&state, false, false);
        let epoch = state.epoch.load(Ordering::SeqCst);
        let generation = state.input_policy.generation();
        let observations = state.observations.lock().unwrap().len();
        for _ in 0..3 {
            let result = handle(&state, "desktop_resume", &json!({}), epoch).unwrap();
            let data = result_data(&result);
            assert_eq!(data["status"], "already_resumed");
            assert_eq!(data["epoch"], epoch);
            assert_eq!(data["input_generation"], generation);
            assert_eq!(data["input_permission_changed"], false);
            assert_eq!(data["fresh_observation_required"], false);
        }
        assert_eq!(state.epoch.load(Ordering::SeqCst), epoch);
        assert_eq!(state.input_policy.generation(), generation);
        assert_eq!(state.observations.lock().unwrap().len(), observations);
        assert!(state.capture_available.load(Ordering::SeqCst));
        assert!(!dir.join("takeover.json").exists());
        assert_eq!(
            state.takeover_marker.lock().unwrap().status()["reason"],
            Value::Null
        );
        assert_eq!(
            state.takeover_marker.lock().unwrap().status()["last_reason"]["source"],
            "physical_input_activity"
        );
        // Even a no-op resume cannot hide unconfirmed release or a busy writer.
        state.release_confirmed.store(false, Ordering::SeqCst);
        assert!(handle(&state, "desktop_resume", &json!({}), epoch)
            .unwrap_err()
            .contains("not confirmed"));
        state.release_confirmed.store(true, Ordering::SeqCst);
        state.queued.store(1, Ordering::SeqCst);
        assert!(handle(&state, "desktop_resume", &json!({}), epoch)
            .unwrap_err()
            .contains("empty actor queue"));
        state.queued.store(0, Ordering::SeqCst);
        let writer = state.actor.lock().unwrap();
        assert!(handle(&state, "desktop_resume", &json!({}), epoch)
            .unwrap_err()
            .contains("Actor busy"));
        drop(writer);
        assert_eq!(state.epoch.load(Ordering::SeqCst), epoch);
        assert_eq!(state.input_policy.generation(), generation);
        fs::remove_dir_all(dir).unwrap();
    }
    #[test]
    fn resume_true_latch_still_requires_released_controls_and_quiet_physical_input() {
        let (mut state, dir) = fixture("resume-real-latch");
        state.started = Instant::now() - Duration::from_millis(350);
        state.release_confirmed.store(true, Ordering::SeqCst);
        *state.human_monitor.lock().unwrap() = json!({"available":true,"watched_devices":1});
        state.input_policy.update_holds(1, true);
        assert!(handle(&state, "desktop_resume", &json!({}), 77)
            .unwrap_err()
            .contains("still held"));
        state.input_policy.update_holds(0, true);
        physical_activity(&state, false, false);
        assert!(handle(&state, "desktop_resume", &json!({}), 77)
            .unwrap_err()
            .contains("recent"));
        assert!(state.takeover.load(Ordering::SeqCst));
        assert_eq!(state.epoch.load(Ordering::SeqCst), 77);
        assert!(dir.join("takeover.json").exists());
        state.last_human_ms.store(0, Ordering::SeqCst);
        let result = handle(
            &state,
            "desktop_resume",
            &json!({"expected_epoch":77,"expected_session_id":state.session_id}),
            77,
        )
        .unwrap();
        assert_eq!(result_data(&result)["status"], "resumed");
        assert_eq!(state.epoch.load(Ordering::SeqCst), 78);
        assert!(!state.takeover.load(Ordering::SeqCst));
        assert!(!dir.join("takeover.json").exists());
        fs::remove_dir_all(dir).unwrap();
    }
    fn semantic_readiness_snapshot(complete: bool) -> atspi::Snapshot {
        serde_json::from_value(json!({"pid":42,"requested_title":"fixture","application":{"bus":":1.999","path":"/fixture"},"window":{"bus":":1.999","path":"/fixture/window"},"nodes":[{"object":{"bus":":1.999","path":"/fixture/filter"},"parent":null,"depth":1,"name":"Filter Menge","description":"quantity filter","role":"text","role_id":60,"interfaces":["org.a11y.atspi.EditableText","org.a11y.atspi.Action","org.a11y.atspi.Component"],"states":[(1u32<<7)|(1u32<<8)|(1u32<<11)|(1u32<<12)|(1u32<<24)|(1u32<<25)],"protected":false,"action_names":["click"],"text_excerpt":"Menge >= 5"}],"complete":complete,"visible_modals":[],"semantic_scope":"offline fixture","sole_window_fallback_allowed":false,"bus_guid":"never-connect"})).unwrap()
    }
    fn semantic_readiness_window() -> Value {
        json!({"id":1,"pid":42,"app_id":"offline-fixture","workspace_id":1})
    }
    #[test]
    fn semantic_truncation_diagnostics_publish_exactly_without_granting_handles() {
        let (state, dir) = cooperative_fixture("semantic-truncation-diagnostics");
        let generation = state.input_policy.generation();
        let mut snapshot = semantic_readiness_snapshot(false);
        assert!(snapshot.traversal_diagnostics.is_none());
        snapshot.traversal_diagnostics = Some(atspi::TraversalDiagnostics {
            revision: 1,
            effective_max_nodes: 200,
            effective_max_depth: 12,
            effective_deadline_ms: 3000,
            incomplete_reasons: atspi::IncompleteReasons {
                max_depth_cutoffs: 1,
                ..atspi::IncompleteReasons::default()
            },
        });
        let expected = serde_json::to_value(&snapshot.traversal_diagnostics).unwrap();
        let result = semantic_snapshot_result(
            &state,
            &json!({}),
            semantic_readiness_window(),
            snapshot,
            77,
            generation,
            Instant::now(),
        )
        .unwrap();
        let data = result_data(&result);
        assert_eq!(data["traversal_diagnostics"], expected);
        assert_eq!(data["elements_complete"], false);
        assert_eq!(data["status"], "limited");
        assert_eq!(data["action_ready"], false);
        assert_eq!(data["elements"][0]["handle_id"], Value::Null);
        assert_eq!(data["elements"][0]["capabilities"]["focus"], false);
        assert!(state.semantic_targets.lock().unwrap().is_empty());
        let legacy = semantic_snapshot_result(
            &state,
            &json!({}),
            semantic_readiness_window(),
            semantic_readiness_snapshot(false),
            77,
            generation,
            Instant::now(),
        )
        .unwrap();
        assert_eq!(result_data(&legacy)["traversal_diagnostics"], Value::Null);
        assert_eq!(state.input_policy.generation(), generation);
        assert_eq!(state.epoch.load(Ordering::SeqCst), 77);
        assert_eq!(state.queued.load(Ordering::SeqCst), 0);
        assert!(state.active.lock().unwrap().is_null());
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn semantic_focus_is_standalone_and_rejects_mixed_batch_before_queue_or_device() {
        let (state, dir) = cooperative_fixture("semantic-focus-standalone");
        let result = act(&state, &json!({"observation_id":"does-not-exist","actions":[{"kind":"semantic_focus","handle_id":"opaque"},{"kind":"key","keys":["ctrl","c"]}],"observe_after":false}), 77).unwrap_err();
        assert!(result.contains("standalone"), "{result}");
        assert!(state.active.lock().unwrap().is_null());
        assert_eq!(state.queued.load(Ordering::SeqCst), 0);
        assert!(state.actor.lock().unwrap().is_none());
        assert!(state.keyboard.lock().unwrap().is_none());
        assert!(state.global_keyboard.lock().unwrap().is_none());
        assert_eq!(state.log.lock().unwrap().metadata().unwrap().len(), 0);
        for invalid in ["".to_owned(), "x".repeat(129)] {
            assert!(preflight(&obs(&dir), &Action::SemanticFocus { handle_id: invalid }).is_err());
        }
        fs::remove_dir_all(dir).unwrap();
    }
    #[test]
    fn semantic_focus_stale_held_cancel_epoch_or_window_handle_has_no_dispatch() {
        for reason in ["stale_generation", "held", "cancel", "epoch", "window"] {
            let (state, dir) = cooperative_fixture(&format!("semantic-focus-{reason}"));
            let data = result_data(
                &semantic_snapshot_result(
                    &state,
                    &json!({}),
                    semantic_readiness_window(),
                    semantic_readiness_snapshot(true),
                    77,
                    0,
                    Instant::now(),
                )
                .unwrap(),
            );
            let handle = data["elements"][0]["handle_id"].as_str().unwrap();
            let mut visual = obs(&dir);
            visual.focused_window = Some(semantic_readiness_window());
            match reason {
                "stale_generation" => {
                    physical_activity(&state, false, false);
                    visual.input_generation = state.input_policy.generation();
                }
                "held" => {
                    state.input_policy.update_holds(1, true);
                    visual.input_generation = state.input_policy.generation();
                }
                "cancel" => CURRENT_CANCEL_FLAG
                    .with(|f| *f.borrow_mut() = Some(Arc::new(AtomicBool::new(true)))),
                "epoch" => {
                    state.epoch.fetch_add(1, Ordering::SeqCst);
                }
                "window" => {
                    visual.focused_window =
                        Some(json!({"id":99,"pid":42,"app_id":"offline-fixture","workspace_id":1}))
                }
                _ => unreachable!(),
            }
            state
                .observations
                .lock()
                .unwrap()
                .insert(visual.id.clone(), visual.clone());
            let result = act_inner(
                &state,
                &json!({"observation_id":visual.id,"actions":[{"kind":"semantic_focus","handle_id":handle}],"observe_after":false}),
                77,
            );
            CURRENT_CANCEL_FLAG.with(|f| *f.borrow_mut() = None);
            assert!(result.is_err(), "{reason}");
            assert!(state.active.lock().unwrap().is_null(), "{reason}");
            assert_eq!(state.queued.load(Ordering::SeqCst), 0, "{reason}");
            assert!(state.actor.lock().unwrap().is_none(), "{reason}");
            assert!(state.keyboard.lock().unwrap().is_none(), "{reason}");
            assert!(state.global_keyboard.lock().unwrap().is_none(), "{reason}");
            assert_eq!(
                state.log.lock().unwrap().metadata().unwrap().len(),
                0,
                "only read publisher ran, no action event for {reason}"
            );
            fs::remove_dir_all(dir).unwrap();
        }
    }

    #[test]
    fn semantic_readiness_complete_ready_issues_handles_but_incomplete_tree_is_read_only() {
        let (state, dir) = cooperative_fixture("semantic-ready");
        let ready = result_data(
            &semantic_snapshot_result(
                &state,
                &json!({"query":"Filter"}),
                semantic_readiness_window(),
                semantic_readiness_snapshot(true),
                77,
                0,
                Instant::now(),
            )
            .unwrap(),
        );
        assert_eq!(ready["action_ready"], true);
        assert_eq!(ready["input_generation"], 0);
        assert_eq!(ready["current_input_generation"], 0);
        assert_eq!(ready["elements"][0]["capabilities"]["click"], true);
        assert_eq!(ready["elements"][0]["capabilities"]["focus"], true);
        assert_eq!(ready["elements"][0]["focusable"], true);
        assert_eq!(ready["elements"][0]["focused"], true);
        assert_eq!(ready["elements"][0]["capabilities"]["set_value"], true);
        let handle = ready["elements"][0]["handle_id"].as_str().unwrap();
        let mut visual = obs(&dir);
        visual.focused_window = Some(semantic_readiness_window());
        assert!(semantic_target(&state, &visual, handle).is_ok());
        let before = state.semantic_targets.lock().unwrap().len();
        let limited = result_data(
            &semantic_snapshot_result(
                &state,
                &json!({}),
                semantic_readiness_window(),
                semantic_readiness_snapshot(false),
                77,
                0,
                Instant::now(),
            )
            .unwrap(),
        );
        assert_eq!(limited["status"], "limited");
        assert_eq!(limited["input_ready"], true);
        assert_eq!(limited["action_ready"], false);
        assert_eq!(limited["fresh_semantic_snapshot_required_for_input"], true);
        assert_eq!(limited["elements"][0]["label"], "Filter Menge");
        assert_eq!(limited["elements"][0]["focusable"], true);
        assert_eq!(limited["elements"][0]["focused"], true);
        assert_eq!(limited["elements"][0]["handle_id"], Value::Null);
        assert_eq!(
            limited["elements"][0]["capabilities"],
            json!({"set_value":false,"click":false,"focus":false})
        );
        assert_eq!(state.semantic_targets.lock().unwrap().len(), before);
        fs::remove_dir_all(dir).unwrap();
    }
    #[test]
    fn semantic_readiness_held_or_unknown_controls_preserve_readable_labels_without_handles() {
        let (state, dir) = cooperative_fixture("semantic-held");
        for (count, known) in [(2, true), (0, false)] {
            state.input_policy.update_holds(count, known);
            let generation = state.input_policy.generation();
            let data = result_data(
                &semantic_snapshot_result(
                    &state,
                    &json!({}),
                    semantic_readiness_window(),
                    semantic_readiness_snapshot(true),
                    77,
                    generation,
                    Instant::now(),
                )
                .unwrap(),
            );
            assert_eq!(
                data["status"], "available",
                "tree availability is separate from input"
            );
            assert_eq!(data["input_ready"], false);
            assert_eq!(data["action_ready"], false);
            assert_eq!(data["input_generation"], generation);
            assert_eq!(data["current_input_generation"], generation);
            assert_eq!(data["input_activity_during_snapshot"], false);
            assert_eq!(data["fresh_semantic_snapshot_required_for_input"], true);
            assert_eq!(data["elements"][0]["text_excerpt"], "Menge >= 5");
            assert_eq!(data["elements"][0]["handle_id"], Value::Null);
            assert_eq!(data["elements"][0]["parent_handle_id"], Value::Null);
            assert_eq!(
                data["elements"][0]["capabilities"],
                json!({"set_value":false,"click":false,"focus":false})
            );
            assert!(state.semantic_targets.lock().unwrap().is_empty());
            assert_eq!(state.epoch.load(Ordering::SeqCst), 77);
            assert!(!state.takeover.load(Ordering::SeqCst));
            assert_eq!(state.input_policy.generation(), generation);
        }
        fs::remove_dir_all(dir).unwrap();
    }
    #[test]
    fn background_semantic_handles_survive_unrelated_input_but_keep_escape_and_context_guards() {
        let (state, dir) = cooperative_fixture("background-semantic");
        *state.human_monitor.lock().unwrap() = json!({"available":true,"watched_devices":1});
        let mut target = semantic_readiness_window();
        target["is_focused"] = json!(false);
        target["workspace_id"] = json!(33);
        let mut visual = obs(&dir);
        visual.output = "HDMI-A-2".into();
        visual.output_geometry = json!({"width":800,"height":600,"x":0,"y":0});
        visual.windows = json!([target.clone(), {"id":99,"is_focused":true,"workspace_id":55}]);
        visual.focused_window = Some(json!({"id":99,"is_focused":true,"workspace_id":55}));
        let windows = visual.windows.clone();
        let workspaces = json!([{"id":33,"output":"HDMI-A-2","is_active":true,"is_focused":false}]);
        let outputs = json!({"HDMI-A-2":{"logical":visual.output_geometry}});
        physical_activity(&state, false, false);
        state.input_policy.update_holds(1, true);
        let data = result_data(&semantic_snapshot_result(&state, &json!({"background":true}),
            target.clone(), semantic_readiness_snapshot(true), 77, 0, Instant::now()).unwrap());
        assert_eq!(data["action_ready"], true);
        assert_eq!(data["background"], true);
        assert_eq!(data["input_readiness"]["shared_input_ready"], false);
        assert_eq!(data["fresh_observation_required_for_input"], false);
        assert_eq!(data["input_activity_during_snapshot"], true);
        assert_eq!(data["elements"][0]["capabilities"]["focus"], false);
        assert_eq!(data["elements"][0]["capabilities"]["set_value"], true);
        assert!(data["elements"][0]["handle_id"].is_string());
        assert!(state.semantic_targets.lock().unwrap().values().all(|t| t.background));
        validate_background_semantic_inventory(&visual, &target, &windows, &workspaces, &outputs, true).unwrap();
        let _guard = input_policy::Guard::background();
        assert!(check_epoch(&state, Some(77)).is_ok());
        physical_activity(&state, false, false);
        assert!(check_epoch(&state, Some(77)).is_ok());
        let mut changed = windows.clone();
        changed[0]["pid"] = json!(999);
        assert!(validate_background_semantic_inventory(&visual, &target, &changed, &workspaces, &outputs, true).is_err());
        changed = windows.clone(); changed[0]["is_focused"] = json!(true);
        assert!(validate_background_semantic_inventory(&visual, &target, &changed, &workspaces, &outputs, true).is_err());
        // Being focused is not a product prohibition: without overlapping
        // physical input, the direct object action can use the same window.
        validate_background_semantic_inventory(&visual, &target, &changed, &workspaces, &outputs, false).unwrap();
        let mut changed_output = outputs.clone(); changed_output["HDMI-A-2"]["logical"]["width"] = json!(900);
        assert!(validate_background_semantic_inventory(&visual, &target, &windows, &workspaces, &changed_output, true).is_err());
        test_live_scope(&state, "background-escape");
        physical_activity(&state, true, false);
        assert!(check_epoch(&state, Some(77)).is_err());
        assert_eq!(state.capture_attempts.load(Ordering::SeqCst), 0);
        fs::remove_dir_all(dir).unwrap();
    }
    #[test]
    fn semantic_readiness_changed_generation_keeps_tree_but_stale_handle_act_has_zero_effects() {
        let (state, dir) = cooperative_fixture("semantic-changed-generation");
        let prior = result_data(
            &semantic_snapshot_result(
                &state,
                &json!({}),
                semantic_readiness_window(),
                semantic_readiness_snapshot(true),
                77,
                0,
                Instant::now(),
            )
            .unwrap(),
        );
        let old_handle = prior["elements"][0]["handle_id"].as_str().unwrap();
        physical_activity(&state, false, false);
        let data = result_data(
            &semantic_snapshot_result(
                &state,
                &json!({}),
                semantic_readiness_window(),
                semantic_readiness_snapshot(true),
                77,
                0,
                Instant::now(),
            )
            .unwrap(),
        );
        assert_eq!(data["elements"][0]["label"], "Filter Menge");
        assert_eq!(
            data["input_generation"], 0,
            "never rebind a captured semantic tree"
        );
        assert_eq!(data["current_input_generation"], 1);
        assert_eq!(data["input_activity_during_snapshot"], true);
        assert_eq!(data["action_ready"], false);
        assert_eq!(data["elements"][0]["handle_id"], Value::Null);
        assert_eq!(
            state.semantic_targets.lock().unwrap().len(),
            1,
            "no new handles"
        );
        let mut visual = obs(&dir);
        visual.id = "fresh-visual-with-stale-semantic".into();
        visual.input_generation = 1;
        visual.focused_window = Some(semantic_readiness_window());
        state
            .observations
            .lock()
            .unwrap()
            .insert(visual.id.clone(), visual.clone());
        let event_bytes = state.log.lock().unwrap().metadata().unwrap().len();
        let result = act(&state, &json!({"observation_id":visual.id,"actions":[{"kind":"semantic_click","handle_id":old_handle,"action_name":"click"}],"observe_after":false}), 77).unwrap();
        let stopped = result_data(&result);
        assert_eq!(stopped["status"], "input_conflict");
        assert_eq!(stopped["completed_actions"], 0);
        assert_eq!(stopped["effects"], json!([]));
        let recorded = fs::read_to_string(dir.join("events.jsonl")).unwrap();
        let rejected: Vec<Value> = recorded[event_bytes as usize..]
            .lines()
            .map(|line| serde_json::from_str(line).unwrap())
            .collect();
        assert_eq!(rejected.len(), 1, "only the rejection audit is emitted");
        assert_eq!(rejected[0]["event"], "input_conflict");
        assert!(state.active.lock().unwrap().is_null());
        assert_eq!(state.queued.load(Ordering::SeqCst), 0);
        assert!(state.actor.lock().unwrap().is_none());
        assert!(state.keyboard.lock().unwrap().is_none());
        assert!(state.global_keyboard.lock().unwrap().is_none());
        assert_eq!(state.epoch.load(Ordering::SeqCst), 77);
        assert!(!state.takeover.load(Ordering::SeqCst));
        let fresh = result_data(
            &semantic_snapshot_result(
                &state,
                &json!({}),
                semantic_readiness_window(),
                semantic_readiness_snapshot(true),
                77,
                1,
                Instant::now(),
            )
            .unwrap(),
        );
        assert_eq!(fresh["action_ready"], true);
        assert_ne!(fresh["elements"][0]["handle_id"], old_handle);
        assert!(semantic_target(
            &state,
            &visual,
            fresh["elements"][0]["handle_id"].as_str().unwrap()
        )
        .is_ok());
        fs::remove_dir_all(dir).unwrap();
    }
    #[test]
    fn semantic_readiness_physical_escape_local_cancel_and_deadline_remain_errors() {
        let (state, dir) = cooperative_fixture("semantic-stop");
        CURRENT_CANCEL_FLAG.with(|f| *f.borrow_mut() = Some(Arc::new(AtomicBool::new(true))));
        let canceled = semantic_snapshot_result(
            &state,
            &json!({}),
            semantic_readiness_window(),
            semantic_readiness_snapshot(true),
            77,
            0,
            Instant::now(),
        );
        CURRENT_CANCEL_FLAG.with(|f| *f.borrow_mut() = None);
        assert!(canceled
            .unwrap_err()
            .contains("client request was canceled"));
        assert!(state.semantic_targets.lock().unwrap().is_empty());
        {
            let _deadline = DeadlineGuard::set(Some(Instant::now() - Duration::from_millis(1)));
            assert!(semantic_snapshot_result(
                &state,
                &json!({}),
                semantic_readiness_window(),
                semantic_readiness_snapshot(true),
                77,
                0,
                Instant::now()
            )
            .unwrap_err()
            .contains("deadline"));
        }
        test_live_scope(&state, "semantic-readiness-escape");
        physical_activity(&state, true, false);
        assert!(semantic_snapshot_result(
            &state,
            &json!({}),
            semantic_readiness_window(),
            semantic_readiness_snapshot(true),
            77,
            0,
            Instant::now()
        )
        .unwrap_err()
        .contains("taken over"));
        assert!(!state.takeover.load(Ordering::SeqCst));
        assert!(!dir.join("takeover.json").exists());
        assert!(state.semantic_targets.lock().unwrap().is_empty());
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn semantic_readiness_final_boundary_scrubs_only_own_handles_on_conflict_or_stop() {
        // Staged records come from the actual production publisher; the lower
        // production boundary is invoked directly, without sleeps/test hooks.
        for reason in ["activity", "held", "cancel", "escape", "initial_not_ready"] {
            let (state, dir) = cooperative_fixture(&format!("semantic-final-{reason}"));
            let data = result_data(
                &semantic_snapshot_result(
                    &state,
                    &json!({}),
                    semantic_readiness_window(),
                    semantic_readiness_snapshot(true),
                    77,
                    0,
                    Instant::now(),
                )
                .unwrap(),
            );
            let own = data["elements"][0]["handle_id"]
                .as_str()
                .unwrap()
                .to_owned();
            let mut elements = data["elements"].as_array().unwrap().clone();
            elements[0]["parent_handle_id"] = json!("parent-from-current-snapshot");
            let mut cache = state.semantic_targets.lock().unwrap().clone();
            let target = cache[&own].clone();
            cache.insert("foreign-kept".into(), target.clone());
            let handles = HashMap::from([(target.object, own.clone())]);
            match reason {
                "activity" => {
                    physical_activity(&state, false, false);
                }
                "held" => state.input_policy.update_holds(2, true),
                "cancel" => CURRENT_CANCEL_FLAG
                    .with(|f| *f.borrow_mut() = Some(Arc::new(AtomicBool::new(true)))),
                "escape" => {
                    test_live_scope(&state, "semantic-handle-escape");
                    physical_activity(&state, true, false)
                }
                _ => {}
            }
            let result = finalize_semantic_handles(
                &state,
                77,
                0,
                true,
                reason != "initial_not_ready",
                false,
                &handles,
                &mut cache,
                &mut elements,
            );
            CURRENT_CANCEL_FLAG.with(|f| *f.borrow_mut() = None);
            assert!(
                !cache.contains_key(&own),
                "own staged handle must be removed for {reason}"
            );
            assert!(
                cache.contains_key("foreign-kept"),
                "foreign cache entries are preserved for {reason}"
            );
            if matches!(reason, "cancel" | "escape") {
                assert!(result.is_err(), "hard stop remains Err for {reason}");
            } else {
                let readiness = result.unwrap();
                assert_eq!(readiness["action_ready"], false);
                assert_eq!(
                    readiness["fresh_semantic_snapshot_required_for_input"],
                    true
                );
                assert_eq!(elements[0]["handle_id"], Value::Null);
                assert_eq!(elements[0]["parent_handle_id"], Value::Null);
                assert_eq!(
                    elements[0]["capabilities"],
                    json!({"set_value":false,"click":false,"focus":false})
                );
                assert_eq!(elements[0]["label"], "Filter Menge");
                assert_eq!(elements[0]["focusable"], true);
                assert_eq!(elements[0]["focused"], true);
                assert_eq!(elements[0]["text_excerpt"], "Menge >= 5");
            }
            assert!(state.active.lock().unwrap().is_null());
            assert_eq!(state.queued.load(Ordering::SeqCst), 0);
            assert!(state.actor.lock().unwrap().is_none());
            fs::remove_dir_all(dir).unwrap();
        }
    }

    #[test]
    fn observation_readiness_returns_metadata_for_ready_held_unknown_and_latched_states() {
        let (state, dir) = cooperative_fixture("observation-readiness");
        let ready = observation_input_readiness(&state, state.input_policy.generation());
        assert_eq!(ready["input_ready"], true);
        assert_eq!(ready["action_ready"], true);
        assert_eq!(ready["input_activity_during_capture"], false);
        assert_eq!(ready["fresh_observation_required_for_input"], false);
        state.input_policy.update_holds(1, true);
        let held_generation = state.input_policy.generation();
        let held = observation_input_readiness(&state, held_generation);
        assert_eq!(held["input_ready"], false);
        assert_eq!(held["action_ready"], false);
        assert_eq!(
            held["input_activity_during_capture"], false,
            "a held control can predate capture"
        );
        assert_eq!(held["fresh_observation_required_for_input"], true);
        assert_eq!(
            state.input_policy.generation(),
            held_generation,
            "readiness reads do not mutate input state"
        );
        state.input_policy.update_holds(0, false);
        let unknown = observation_input_readiness(&state, state.input_policy.generation());
        assert_eq!(unknown["input_ready"], false);
        state.input_policy.update_holds(0, true);
        state.takeover.store(true, Ordering::SeqCst);
        let latched = observation_input_readiness(&state, state.input_policy.generation());
        assert_eq!(latched["input_ready"], true);
        assert_eq!(latched["action_ready"], false);
        assert_eq!(latched["takeover_latched"], true);
        assert_eq!(state.epoch.load(Ordering::SeqCst), 77);
        fs::remove_dir_all(dir).unwrap();
    }
    #[test]
    fn observation_activity_metadata_preserves_start_generation_and_stale_input_never_dispatches() {
        let (state, dir) = cooperative_fixture("observation-activity");
        let captured = obs(&dir);
        physical_activity(&state, false, false);
        let metadata = observation_input_readiness(&state, captured.input_generation);
        assert_eq!(metadata["input_activity_during_capture"], true);
        assert_eq!(metadata["input_ready"], false);
        assert_eq!(metadata["action_ready"], false);
        assert_eq!(metadata["fresh_observation_required_for_input"], true);
        assert_eq!(
            captured.input_generation, 0,
            "never silently rebind a captured frame"
        );
        assert_eq!(
            metadata["current_input_generation"],
            state.input_policy.generation()
        );
        let stale = act(&state, &json!({"observation_id":captured.id,"actions":[{"kind":"wait","ms":1}],"observe_after":false}), 77).unwrap();
        let data = result_data(&stale);
        assert_eq!(data["status"], "input_conflict");
        assert_eq!(data["completed_actions"], 0);
        assert_eq!(data["effects"], json!([]));
        assert_eq!(
            state.observations.lock().unwrap()[&captured.id].input_generation,
            0
        );
        assert_eq!(state.epoch.load(Ordering::SeqCst), 77);
        assert!(!state.takeover.load(Ordering::SeqCst));
        fs::remove_dir_all(dir).unwrap();
    }
    #[test]
    fn cooperative_dropped_event_stream_requires_resync_and_never_replays_queued_keys() {
        let packet = |events: &[(u16, u16, i32)]| {
            let mut bytes = Vec::new();
            for (kind, code, value) in events {
                let mut event = [0u8; 24];
                event[16..18].copy_from_slice(&kind.to_ne_bytes());
                event[18..20].copy_from_slice(&code.to_ne_bytes());
                event[20..24].copy_from_slice(&value.to_ne_bytes());
                bytes.extend(event);
            }
            bytes
        };
        let mut lost = false;
        let dropped = packet(&[(0, 3, 0), (1, 1, 1), (1, 30, 1)]);
        assert_eq!(
            physical_event_chunk(&dropped, input_policy::Source::Physical, &mut lost),
            (true, false)
        );
        assert!(lost, "held state must remain unknown until SYN_REPORT");
        let stale = packet(&[(1, 1, 1), (0, 0, 0)]);
        assert_eq!(
            physical_event_chunk(&stale, input_policy::Source::Physical, &mut lost),
            (false, false)
        );
        assert!(!lost, "next report permits authoritative ioctl resync");
        let fresh = packet(&[(1, 1, 1)]);
        assert_eq!(
            physical_event_chunk(&fresh, input_policy::Source::Physical, &mut lost),
            (true, true)
        );
        assert_eq!(
            physical_event_chunk(&fresh, input_policy::Source::Synthetic, &mut lost),
            (false, false)
        );
    }
    #[test]
    fn release_recovery_preserves_epoch_latch_cause_original_failure_and_invalidates_cloned_target()
    {
        let (state, dir) = fixture("preserve");
        let o = obs(&dir);
        state
            .observations
            .lock()
            .unwrap()
            .insert(o.id.clone(), o.clone());
        validate_retained_observation(&state, &o, true).unwrap();
        let marker_before = fs::read(dir.join("takeover.json")).unwrap();
        let failed_before = state.last_result.lock().unwrap().clone();
        let result = handle(&state, "desktop_recover_release", &json!({}), 77).unwrap();
        assert_eq!(result["isError"], false);
        assert!(state.release_confirmed.load(Ordering::SeqCst));
        assert_eq!(state.epoch.load(Ordering::SeqCst), 77);
        assert!(state.takeover.load(Ordering::SeqCst));
        assert_eq!(fs::read(dir.join("takeover.json")).unwrap(), marker_before);
        assert_eq!(*state.last_result.lock().unwrap(), failed_before);
        assert!(!state.capture_available.load(Ordering::SeqCst));
        assert!(state.active.lock().unwrap().is_null());
        assert!(validate_retained_observation(&state, &o, true).is_err());
        fs::remove_dir_all(dir).unwrap();
    }
    #[test]
    fn release_recovery_refuses_busy_writer_without_wait_or_state_change() {
        let (state, dir) = fixture("busy");
        let held = state.actor.lock().unwrap();
        let t = Instant::now();
        assert!(recover_release(&state, &json!({}))
            .unwrap_err()
            .contains("Actor busy"));
        assert!(t.elapsed() < Duration::from_millis(100));
        assert!(!state.release_confirmed.load(Ordering::SeqCst));
        drop(held);
        fs::remove_dir_all(dir).unwrap();
    }
    #[test]
    fn release_recovery_refuses_active_or_queued_before_any_release() {
        let (state, dir) = fixture("pending");
        *state.active.lock().unwrap() = json!({"task_id":"old active"});
        assert!(recover_release(&state, &json!({}))
            .unwrap_err()
            .contains("idle and queue empty"));
        *state.active.lock().unwrap() = Value::Null;
        state.queued.store(1, Ordering::SeqCst);
        assert!(recover_release(&state, &json!({}))
            .unwrap_err()
            .contains("idle and queue empty"));
        assert!(!state.release_confirmed.load(Ordering::SeqCst));
        fs::remove_dir_all(dir).unwrap();
    }
    #[test]
    fn release_recovery_strict_args_and_bounds_refuse_without_state_change() {
        let (state, dir) = fixture("args");
        for args in [
            json!({"actions":[{"kind":"click"}]}),
            json!({"timeout_ms":0}),
            json!({"timeout_ms":199}),
            json!({"timeout_ms":2001}),
            json!({"timeout_ms":-1}),
            json!({"timeout_ms":1.5}),
        ] {
            assert!(recover_release(&state, &args).is_err());
            assert!(!state.release_confirmed.load(Ordering::SeqCst));
            assert!(state.active.lock().unwrap().is_null());
        }
        fs::remove_dir_all(dir).unwrap();
    }
    #[test]
    fn release_recovery_lock_preflight_refuses_before_stage_or_active_marker() {
        let (state, dir) = fixture("kb-busy");
        let held = state.keyboard.lock().unwrap();
        assert!(recover_release(&state, &json!({}))
            .unwrap_err()
            .contains("Keyboard busy"));
        assert!(state.active.lock().unwrap().is_null());
        assert!(!state.release_confirmed.load(Ordering::SeqCst));
        drop(held);
        fs::remove_dir_all(dir).unwrap();
    }
    #[test]
    fn release_recovery_already_confirmed_does_not_invalidate_capture_or_resume() {
        let (state, dir) = fixture("already");
        state.release_confirmed.store(true, Ordering::SeqCst);
        let result = recover_release(&state, &json!({})).unwrap();
        let data: Value =
            serde_json::from_str(result["content"][0]["text"].as_str().unwrap()).unwrap();
        assert_eq!(data["release_attempted"], false);
        assert_eq!(data["status"], "already_confirmed");
        assert!(state.capture_available.load(Ordering::SeqCst));
        assert!(state.takeover.load(Ordering::SeqCst));
        assert_eq!(state.epoch.load(Ordering::SeqCst), 77);
        fs::remove_dir_all(dir).unwrap();
    }
    #[test]
    fn release_recovery_tool_truthfully_exposes_only_timeout_and_write_effects() {
        let catalog = tools();
        let tool = catalog
            .as_array()
            .unwrap()
            .iter()
            .find(|t| t["name"] == "desktop_recover_release")
            .unwrap();
        assert_eq!(tool["annotations"]["readOnlyHint"], false);
        assert_eq!(tool["annotations"]["destructiveHint"], true);
        assert_eq!(tool["annotations"]["openWorldHint"], true);
        assert_eq!(tool["inputSchema"]["additionalProperties"], false);
        assert_eq!(
            tool["inputSchema"]["properties"].as_object().unwrap().len(),
            1
        );
    }

    // Independent integration review: private fixtures only. This drives actual
    // MCP classification, cancellation registry, proxy transport, daemon scope
    // and resume handler. It does not start serve(), input, capture or apps.
    #[test]
    fn independent_r8_public_and_bound_resume_requests_receive_scoped_cancel_flags() {
        use mcp_cancel_registry::{Registry, RequestKey, RequestKind};
        for (n, name) in [(7001, "desktop_resume"), (7002, "desktop_resume_bound")] {
            let request = json!({"method":"tools/call","params":{"name":name}});
            let mut registry = Registry::new();
            let flag = registry
                .admit_then(
                    RequestKey::Number(n),
                    RequestKind::from_request(&request),
                    |flag| flag,
                )
                .unwrap();
            assert!(
                flag.is_some(),
                "{name} is an input-permission mutation and must receive scoped cancellation"
            );
            registry.cancel(RequestKey::Number(n));
            assert!(flag.unwrap().load(Ordering::SeqCst));
        }
    }

    #[test]
    fn independent_r8_precancelled_resume_stops_before_proxy_socket_contact() {
        use mcp_cancel_registry::{Registry, RequestKey, RequestKind};
        let request = json!({"method":"tools/call","params":{"name":"desktop_resume"}});
        let mut registry = Registry::new();
        let key = RequestKey::Number(7003);
        let cancel = registry
            .admit_then(key.clone(), RequestKind::from_request(&request), |flag| {
                flag
            })
            .unwrap();
        registry.cancel(key);
        let result = daemon_call_owned(
            Path::new("/nonexistent-private-review-resume.sock"),
            "desktop_resume",
            &json!({"expected_epoch":7,"expected_session_id":"owned-fixture"}),
            cancel,
        );
        assert!(
            result.unwrap_err().contains("canceled before dispatch"),
            "Canceled Resume must fail before any socket connection attempt"
        );
    }

    #[test]
    fn independent_r8_registry_cancel_reaches_proxy_disconnect_and_waiting_resume_marker() {
        use mcp_cancel_registry::{Registry, RequestKey, RequestKind};
        let (mut raw, dir) = fixture("r8-mcp-cancel");
        raw.started = Instant::now() - Duration::from_secs(2);
        raw.release_confirmed.store(true, Ordering::SeqCst);
        *raw.human_monitor.lock().unwrap() = json!({"available":true,"watched_devices":1});
        physical_activity(&raw, true, false); // private function fixture, no evdev
        raw.last_human_ms.store(0, Ordering::SeqCst);
        let state = Arc::new(raw);
        let epoch = state.epoch.load(Ordering::SeqCst);
        let args = json!({"expected_epoch":epoch,"expected_session_id":state.session_id});
        let original_marker = fs::read(dir.join("takeover.json")).unwrap();
        let marker_guard = state.takeover_marker.lock().unwrap();
        let socket = dir.join("mock-actor.sock");
        let listener = UnixListener::bind(&socket).unwrap();
        listener.set_nonblocking(true).unwrap();
        fn accept_private_mock(listener: &UnixListener) -> UnixStream {
            let until = Instant::now() + Duration::from_secs(2);
            loop {
                match listener.accept() {
                    Ok((stream, _)) => return stream,
                    Err(e) if e.kind() == io::ErrorKind::WouldBlock => {
                        assert!(
                            Instant::now() < until,
                            "private mock accept exceeded bounded deadline"
                        );
                        thread::sleep(Duration::from_millis(1));
                    }
                    Err(e) => panic!("private mock accept: {e}"),
                }
            }
        }
        let saw_disconnect = Arc::new(AtomicBool::new(false));
        let daemon_scope_cancel = Arc::new(AtomicBool::new(false));
        let (ready_tx, ready_rx) = std::sync::mpsc::channel();
        let server_state = state.clone();
        let server_disconnect = saw_disconnect.clone();
        let server_cancel = daemon_scope_cancel.clone();
        let server = thread::spawn(move || {
            let mut requests = Vec::new();
            let mut stream = accept_private_mock(&listener);
            stream
                .set_read_timeout(Some(Duration::from_secs(2)))
                .unwrap();
            let mut line = String::new();
            BufReader::new(stream.try_clone().unwrap())
                .read_line(&mut line)
                .unwrap();
            let status_request: Value = serde_json::from_str(&line).unwrap();
            assert_eq!(status_request["tool"], "desktop_status");
            requests.push(status_request);
            writeln!(stream,"{}",text_result(json!({"resume_binding_revision":1,"session_id":server_state.session_id,"epoch":epoch,"takeover_latched":true}))).unwrap();
            drop(stream);
            let mut stream = accept_private_mock(&listener);
            stream
                .set_read_timeout(Some(Duration::from_secs(2)))
                .unwrap();
            let mut line = String::new();
            BufReader::new(stream.try_clone().unwrap())
                .read_line(&mut line)
                .unwrap();
            let bound_request: Value = serde_json::from_str(&line).unwrap();
            assert_eq!(bound_request["tool"], "desktop_resume_bound");
            requests.push(bound_request.clone());
            // Same disconnect-to-thread-local cancellation binding as serve().
            let watch = stream.try_clone().unwrap();
            let watch_cancel = server_cancel.clone();
            let watch_disconnect = server_disconnect.clone();
            let done = Arc::new(AtomicBool::new(false));
            let monitor_done = done.clone();
            let monitor = thread::spawn(move || {
                while !monitor_done.load(Ordering::SeqCst) {
                    let mut fd = libc::pollfd {
                        fd: watch.as_raw_fd(),
                        events: libc::POLLRDHUP,
                        revents: 0,
                    };
                    let status = unsafe { libc::poll(&mut fd, 1, 5) };
                    if status > 0
                        && fd.revents
                            & (libc::POLLRDHUP | libc::POLLHUP | libc::POLLERR | libc::POLLNVAL)
                            != 0
                    {
                        watch_disconnect.store(true, Ordering::SeqCst);
                        watch_cancel.store(true, Ordering::SeqCst);
                        break;
                    }
                    if status < 0 {
                        watch_cancel.store(true, Ordering::SeqCst);
                        break;
                    }
                }
            });
            CURRENT_CANCEL_FLAG.with(|f| *f.borrow_mut() = Some(server_cancel));
            ready_tx.send(()).unwrap();
            let handled = handle(
                &server_state,
                "desktop_resume_bound",
                &bound_request["arguments"],
                epoch,
            );
            CURRENT_CANCEL_FLAG.with(|f| *f.borrow_mut() = None);
            done.store(true, Ordering::SeqCst);
            monitor.join().unwrap();
            let result = handled.clone().unwrap_or_else(error_result);
            let _ = writeln!(stream, "{result}");
            (requests, handled)
        });
        let request = json!({"method":"tools/call","params":{"name":"desktop_resume"}});
        let mut registry = Registry::new();
        let key = RequestKey::String("owned-resume-7004".into());
        let proxy_cancel = registry
            .admit_then(key.clone(), RequestKind::from_request(&request), |flag| {
                flag
            })
            .unwrap();
        let proxy_socket = socket.clone();
        let client = thread::spawn(move || {
            daemon_call_owned(&proxy_socket, "desktop_resume", &args, proxy_cancel)
        });
        ready_rx.recv_timeout(Duration::from_secs(2)).unwrap();
        let until = Instant::now() + Duration::from_secs(2);
        while !fs::read_to_string(dir.join("events.jsonl"))
            .unwrap()
            .contains("resume_preparing")
        {
            assert!(
                Instant::now() < until,
                "Resume did not reach private marker gate"
            );
            thread::sleep(Duration::from_millis(1));
        }
        registry.cancel(key); // exact production notification routing target
        let cancel_until = Instant::now() + Duration::from_millis(400);
        while !daemon_scope_cancel.load(Ordering::SeqCst) && Instant::now() < cancel_until {
            thread::sleep(Duration::from_millis(1));
        }
        drop(marker_guard);
        let proxy_result = client.join().unwrap();
        let (requests, daemon_result) = server.join().unwrap();
        let retained = state.takeover.load(Ordering::SeqCst)
            && fs::read(dir.join("takeover.json")).ok().as_deref()
                == Some(original_marker.as_slice());
        let disconnected = saw_disconnect.load(Ordering::SeqCst);
        println!("independent MCP Resume cancel: requests={} proxy={:?} daemon={:?} disconnected={} marker_retained={}",requests.len(),proxy_result,daemon_result,disconnected,retained);
        fs::remove_dir_all(dir).unwrap();
        assert_eq!(requests.len(), 2, "one status and one bound request only");
        assert!(
            disconnected,
            "MCP cancellation must close the owned still-connected Resume transport"
        );
        assert!(
            proxy_result.is_err(),
            "canceled proxy must not return successful Resume"
        );
        assert!(
            daemon_result.is_err(),
            "waiting Resume must recheck scoped cancellation before marker clear"
        );
        assert!(retained,"confirmed cancellation before marker transition must preserve the exact explicit takeover cause");
    }
    #[test]
    fn optional_selection_evidence_does_not_bypass_incomplete_or_held_guards() {
        for (complete, holds) in [(true, 0), (true, 2), (false, 0)] {
            let (state, dir) = cooperative_fixture("semantic-selection");
            state.input_policy.update_holds(holds, true);
            let generation = state.input_policy.generation();
            let mut snapshot = semantic_readiness_snapshot(complete);
            snapshot.nodes[0].text_selection = Some(
                serde_json::from_value(json!({
                    "status":"available","character_count":10,"selection_count":1,
                    "ranges":[{"start":0,"end":10}],"full_text_selected":true,
                    "stable_readback":true,"offset_unit":"unicode_codepoints","end_exclusive":true,
                    "text_identity_verified":false,"reason":null
                }))
                .unwrap(),
            );
            let data = result_data(
                &semantic_snapshot_result(
                    &state,
                    &json!({}),
                    semantic_readiness_window(),
                    snapshot,
                    77,
                    generation,
                    Instant::now(),
                )
                .unwrap(),
            );
            assert_eq!(
                data["elements"][0]["text_selection"]["full_text_selected"],
                true
            );
            assert_eq!(
                data["elements"][0]["text_selection"]["text_identity_verified"],
                false
            );
            assert_eq!(data["action_ready"], complete && holds == 0);
            assert_eq!(data["input_generation"], generation);
            assert_eq!(data["current_input_generation"], generation);
            assert_eq!(
                data["elements"][0]["handle_id"].is_null(),
                !complete || holds != 0
            );
            assert_eq!(
                data["elements"][0]["capabilities"]["focus"],
                complete && holds == 0
            );
            fs::remove_dir_all(dir).unwrap();
        }
    }
    #[test]
    fn selection_is_absent_by_default_and_error_never_becomes_full_selection() {
        let (state, dir) = cooperative_fixture("semantic-selection-error");
        let generation = state.input_policy.generation();
        let snapshot = semantic_readiness_snapshot(true);
        assert!(snapshot.nodes[0].text_selection.is_none());
        let default = result_data(
            &semantic_snapshot_result(
                &state,
                &json!({}),
                semantic_readiness_window(),
                snapshot.clone(),
                77,
                generation,
                Instant::now(),
            )
            .unwrap(),
        );
        assert!(default["elements"][0].get("text_selection").is_none());
        let mut snapshot = snapshot;
        snapshot.nodes[0].text_selection=Some(serde_json::from_value(json!({
            "status":"error","character_count":null,"selection_count":null,"ranges":[],
            "full_text_selected":null,"stable_readback":false,"offset_unit":"unicode_codepoints",
            "end_exclusive":true,"text_identity_verified":false,"reason":"selection_changed_during_readback"
        })).unwrap());
        let error = result_data(
            &semantic_snapshot_result(
                &state,
                &json!({}),
                semantic_readiness_window(),
                snapshot,
                77,
                generation,
                Instant::now(),
            )
            .unwrap(),
        );
        assert_eq!(error["elements"][0]["text_selection"]["status"], "error");
        assert_eq!(
            error["elements"][0]["text_selection"]["full_text_selected"],
            Value::Null
        );
        assert_eq!(
            error["elements"][0]["text_selection"]["reason"],
            "selection_changed_during_readback"
        );
        assert_eq!(
            default["elements"][0]["editable"],
            error["elements"][0]["editable"]
        );
        assert_eq!(default["input_generation"], error["input_generation"]);
        fs::remove_dir_all(dir).unwrap();
    }
}

#[cfg(test)]
mod reference_decode_regression {
    use super::*;
    fn write_png(path: &Path, bytes: &[u8]) {
        let mut encoder = png::Encoder::new(fs::File::create(path).unwrap(), 40, 40);
        encoder.set_color(png::ColorType::Rgb);
        encoder.set_depth(png::BitDepth::Eight);
        encoder
            .write_header()
            .unwrap()
            .write_image_data(bytes)
            .unwrap();
    }
    #[test]
    fn warm_reference_pointer_act_refuses_conflicts_and_stops_before_input_or_devices() {
        // Every exercised refusal precedes Niri, fresh capture and device setup.
        // This proves the cache cannot substitute for action readiness.
        for reason in ["ordinary", "held", "scoped_cancel", "explicit_takeover"] {
            let (state, dir) =
                release_recovery_tests::cooperative_fixture(&format!("cache-pointer-{reason}"));
            let mut obs = release_recovery_tests::obs(&dir);
            obs.image = dir.join("reference.png");
            obs.view_image = obs.image.clone();
            obs.image_width = 40;
            obs.image_height = 40;
            obs.view = Crop {
                x: 0,
                y: 0,
                width: 20,
                height: 20,
            };
            write_png(&obs.image, &vec![57; 40 * 40 * 3]);
            state
                .observations
                .lock()
                .unwrap()
                .insert(obs.id.clone(), obs.clone());
            reference_pixels(&state, &obs.id, &obs.image, 77, obs.input_generation).unwrap();
            assert!(state.reference_cache.retained_bytes() > 0);
            assert_eq!(state.reference_cache.decodes(), 1);
            match reason {
                "ordinary" => physical_activity(&state, false, false),
                "held" => state.input_policy.update_holds(2, true),
                "scoped_cancel" => CURRENT_CANCEL_FLAG
                    .with(|f| *f.borrow_mut() = Some(Arc::new(AtomicBool::new(true)))),
                "explicit_takeover" => {
                    handle(&state, "desktop_takeover", &json!({}), 77).unwrap();
                }
                _ => unreachable!(),
            }
            let epoch = state.epoch.load(Ordering::SeqCst);
            let result = act(
                &state,
                &json!({"task_id":format!("offline-warm-pointer-{reason}"),"observation_id":obs.id,"actions":[{"kind":"click","x":5.0,"y":5.0}],"observe_after":false}),
                epoch,
            );
            CURRENT_CANCEL_FLAG.with(|f| *f.borrow_mut() = None);
            if matches!(reason, "ordinary" | "held") {
                let data: Value =
                    serde_json::from_str(result.unwrap()["content"][0]["text"].as_str().unwrap())
                        .unwrap();
                assert_eq!(data["status"], "input_conflict");
                assert_eq!(data["completed_actions"], 0);
                assert_eq!(data["effects"], json!([]));
                assert_eq!(data["actor_release_confirmed"], true);
                assert_eq!(data["automatic_replay"], false);
            } else {
                let error = result.unwrap_err();
                assert!(if reason == "scoped_cancel" {
                    error.contains("client request was canceled")
                } else {
                    error.contains("takeover is latched")
                });
            }
            assert!(state.active.lock().unwrap().is_null());
            assert_eq!(state.queued.load(Ordering::SeqCst), 0);
            assert!(state.release_confirmed.load(Ordering::SeqCst));
            assert!(state.actor.lock().unwrap().is_none());
            assert!(state.keyboard.lock().unwrap().is_none());
            assert!(state.global_keyboard.lock().unwrap().is_none());
            assert_eq!(state.capture_attempts.load(Ordering::SeqCst), 0);
            assert_eq!(
                state.reference_cache.decodes(),
                1,
                "no guard/reference replay after refusal"
            );
            if reason == "held" {
                assert!(
                    state.reference_cache.retained_bytes() > 0,
                    "held-state retention is no input permission"
                );
            } else {
                assert_eq!(state.reference_cache.retained_bytes(), 0);
            }
            if reason == "explicit_takeover" {
                assert!(state.takeover.load(Ordering::SeqCst));
                assert_eq!(
                    state.takeover_marker.lock().unwrap().reason.source,
                    "explicit_desktop_takeover"
                );
            } else {
                assert!(!state.takeover.load(Ordering::SeqCst));
            }
            fs::remove_dir_all(dir).unwrap();
        }
    }

    #[test]
    fn crop_to_pointer_guard_decodes_prior_once_but_captures_current_every_time() {
        let (state, dir) = release_recovery_tests::cooperative_fixture("cache-crop-pointer");
        let mut obs = release_recovery_tests::obs(&dir);
        obs.image = dir.join("reference.png");
        obs.image_width = 40;
        obs.image_height = 40;
        obs.view = Crop {
            x: 8,
            y: 9,
            width: 20,
            height: 20,
        };
        let data: Vec<u8> = (0..40 * 40 * 3).map(|n| (n % 239) as u8).collect();
        write_png(&obs.image, &data);
        let prior =
            reference_pixels(&state, &obs.id, &obs.image, 77, obs.input_generation).unwrap();
        let cropped = dir.join("crop.png");
        crop_png(&prior, &cropped, &obs.view).unwrap();
        let actual = png_pixels(&cropped).unwrap();
        assert_eq!((actual.width, actual.height), (20, 20));
        for y in 0..20 {
            let start = ((y + 9) * 40 + 8) * 3;
            assert_eq!(
                &actual.bytes[y * 20 * 3..(y + 1) * 20 * 3],
                &data[start..start + 20 * 3]
            );
        }
        let mut captures = 0;
        for _ in 0..2 {
            let result = visual_guard_with_capture(
                &state,
                &obs,
                &Action::Click {
                    x: 5.0,
                    y: 5.0,
                    button: None,
                    count: None,
                },
                77,
                |path| {
                    captures += 1;
                    write_png(path, &data);
                    Ok(())
                },
            )
            .unwrap();
            assert_eq!(result["applied"], true);
        }
        let error = visual_guard_with_capture(
            &state,
            &obs,
            &Action::Click {
                x: 5.0,
                y: 5.0,
                button: None,
                count: None,
            },
            77,
            |path| {
                captures += 1;
                write_png(path, &vec![255; 40 * 40 * 3]);
                Ok(())
            },
        )
        .unwrap_err();
        assert!(error.contains("Visual target changed"));
        assert_eq!(captures, 3);
        assert_eq!(
            state.reference_cache.decodes(),
            1,
            "crop and three guards share one prior inflate, never current frames"
        );
        fs::remove_dir_all(dir).unwrap();
    }
    #[test]
    fn actor_lifecycle_clears_cache_and_keeps_original_input_guards() {
        let (state, dir) = release_recovery_tests::cooperative_fixture("cache-lifecycle");
        let mut obs = release_recovery_tests::obs(&dir);
        obs.image = dir.join("reference.png");
        write_png(&obs.image, &vec![57; 40 * 40 * 3]);
        let seed = || {
            reference_pixels(
                &state,
                &obs.id,
                &obs.image,
                state.epoch.load(Ordering::SeqCst),
                state.input_policy.generation(),
            )
            .unwrap()
        };
        seed();
        assert!(state.reference_cache.retained_bytes() > 0);
        physical_activity(&state, false, false);
        assert_eq!(state.reference_cache.retained_bytes(), 0);
        assert!(
            validate_focus_state(&state, &obs).is_err(),
            "ordinary activity still invalidates old generation"
        );
        obs.input_generation = state.input_policy.generation();
        reference_pixels(&state, &obs.id, &obs.image, 77, obs.input_generation).unwrap();
        handle(&state, "desktop_cancel", &json!({}), 77).unwrap();
        assert_eq!(state.reference_cache.retained_bytes(), 0);
        assert!(
            validate_focus_state(&state, &obs).is_err(),
            "cancel still rejects old epoch"
        );
        reference_pixels(
            &state,
            &obs.id,
            &obs.image,
            state.epoch.load(Ordering::SeqCst),
            obs.input_generation,
        )
        .unwrap();
        capture_unavailable(
            &state,
            state.epoch.load(Ordering::SeqCst),
            "offline-cache-test",
        );
        assert_eq!(state.reference_cache.retained_bytes(), 0);
        assert!(!state.capture_available.load(Ordering::SeqCst));
        fs::remove_dir_all(dir).unwrap();
    }
    #[test]
    fn scoped_cancelled_wait_releases_actor_and_reference_cache() {
        let (state, dir) = release_recovery_tests::cooperative_fixture("cache-scoped-cancel");
        let mut obs = release_recovery_tests::obs(&dir);
        obs.image = dir.join("reference.png");
        write_png(&obs.image, &vec![57; 40 * 40 * 3]);
        reference_pixels(&state, &obs.id, &obs.image, 77, 0).unwrap();
        let canceled = Arc::new(AtomicBool::new(false));
        let cancellation = canceled.clone();
        let actor = state.clone();
        let timer = thread::spawn(move || {
            let until = Instant::now() + Duration::from_secs(2);
            while actor.active.lock().unwrap().is_null() && Instant::now() < until {
                thread::sleep(Duration::from_millis(1));
            }
            cancellation.store(true, Ordering::SeqCst);
        });
        CURRENT_CANCEL_FLAG.with(|f| *f.borrow_mut() = Some(canceled));
        let result = act(
            &state,
            &json!({"observation_id":obs.id,"actions":[{"kind":"wait","ms":2000}],"observe_after":false}),
            77,
        );
        CURRENT_CANCEL_FLAG.with(|f| *f.borrow_mut() = None);
        timer.join().unwrap();
        let result = result.unwrap();
        let data: Value =
            serde_json::from_str(result["content"][0]["text"].as_str().unwrap()).unwrap();
        assert_eq!(data["status"], "canceled");
        assert_eq!(data["actor_release_confirmed"], true);
        assert_eq!(state.reference_cache.retained_bytes(), 0);
        fs::remove_dir_all(dir).unwrap();
    }
    #[test]
    fn successful_release_recovery_invalidates_reference_decode() {
        let (state, dir) = release_recovery_tests::fixture("cache-release-recovery");
        let path = dir.join("reference.png");
        write_png(&path, &vec![57; 40 * 40 * 3]);
        reference_pixels(&state, "offline-reference", &path, 77, 0).unwrap();
        assert!(state.reference_cache.retained_bytes() > 0);
        let result = recover_release(&state, &json!({})).unwrap();
        let data: Value =
            serde_json::from_str(result["content"][0]["text"].as_str().unwrap()).unwrap();
        assert_eq!(data["actor_release_confirmed"], true);
        assert_eq!(state.reference_cache.retained_bytes(), 0);
        assert!(!state.capture_available.load(Ordering::SeqCst));
        fs::remove_dir_all(dir).unwrap();
    }
}

#[cfg(test)]
mod close_window_contract_tests {
    use super::*;
    use release_recovery_tests::{cooperative_fixture, obs};

    fn result_data(value: &Value) -> Value {
        serde_json::from_str(value["content"][0]["text"].as_str().unwrap()).unwrap()
    }

    #[test]
    fn tagged_close_request_roundtrip_keeps_exact_u64_id_and_refuses_implicit_or_foreign_fields() {
        for id in [0, 77, u64::MAX] {
            let request = json!({"kind":"close_window","window_id":id});
            let wire = serde_json::to_vec(&request).unwrap();
            let decoded: Value = serde_json::from_slice(&wire).unwrap();
            assert_eq!(decoded, request);
            assert!(
                matches!(serde_json::from_value::<Action>(decoded).unwrap(), Action::CloseWindow { window_id } if window_id == id)
            );
        }
        for request in [
            json!({"kind":"close_window"}),
            json!({"kind":"close_window","window_id":null}),
            json!({"kind":"close_window","window_id":-1}),
            json!({"kind":"close_window","window_id":77.0}),
            json!({"kind":"close_window","window_id":77,"force":true}),
            json!({"kind":"close_window","window_id":77,"pid":42}),
        ] {
            assert!(serde_json::from_value::<Action>(request).is_err());
        }
        let catalog = tools();
        let schema = &catalog
            .as_array()
            .unwrap()
            .iter()
            .find(|t| t["name"] == "desktop_act")
            .unwrap()["inputSchema"];
        let variants = schema["properties"]["actions"]["items"]["anyOf"]
            .as_array()
            .unwrap();
        assert_eq!(variants.len(), 16); // Thirteen baseline operations plus three closed launches.
        let close = variants
            .iter()
            .find(|v| v["properties"]["kind"]["enum"] == json!(["close_window"]))
            .unwrap();
        assert_eq!(close["required"], json!(["kind", "window_id"]));
        assert_eq!(close["additionalProperties"], false);
        let bytes = serde_json::to_vec(schema).unwrap().len();
        assert!(
            bytes < 5000,
            "Published Codex input schema exceeded budget: {bytes}"
        );
        eprintln!("close_schema_variants=13 input_schema_utf8_bytes={bytes} pinned_budget=5000");
    }

    #[test]
    fn close_mixed_batch_or_mismatched_ids_refuse_before_queue_device_or_command() {
        let (state, dir) = cooperative_fixture("close-zero-admission");
        let initial = state.last_result.lock().unwrap().clone();
        for (top, actions) in [
            (
                Some(77),
                json!([{"kind":"wait","ms":1},{"kind":"close_window","window_id":77}]),
            ),
            (
                Some(77),
                json!([{"kind":"close_window","window_id":77},{"kind":"wait","ms":1}]),
            ),
            (Some(88), json!([{"kind":"close_window","window_id":77}])),
            (None, json!([{"kind":"close_window","window_id":77}])),
        ] {
            let mut args = json!({"observation_id":"cloned-before-recovery","observe_after":false,"actions":actions});
            if let Some(top) = top {
                args["window_id"] = json!(top);
            }
            let error = act(&state, &args, 77).unwrap_err();
            assert!(error.contains("standalone with the same explicit"));
            assert!(state.active.lock().unwrap().is_null());
            assert_eq!(state.queued.load(Ordering::SeqCst), 0);
            assert!(state.release_confirmed.load(Ordering::SeqCst));
            assert!(state.actor.lock().unwrap().is_none());
            assert!(state.keyboard.lock().unwrap().is_none());
            assert!(state.global_keyboard.lock().unwrap().is_none());
            assert_eq!(*state.last_result.lock().unwrap(), initial);
            assert_eq!(fs::metadata(dir.join("events.jsonl")).unwrap().len(), 0);
        }
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn close_focus_output_identity_title_and_generation_guards_never_dispatch_wrong_target_with_shim(
    ) {
        const FLAG: &str = "WEASEL_PRIVATE_CLOSE_PROBE_CHILD";
        const TEST: &str = "close_window_contract_tests::close_focus_output_identity_title_and_generation_guards_never_dispatch_wrong_target_with_shim";
        if env::var(FLAG).as_deref() != Ok("1") {
            let child = Command::new(env::current_exe().unwrap())
                .args(["--exact", TEST, "--test-threads=1"])
                .env(FLAG, "1")
                .output()
                .unwrap();
            assert!(
                child.status.success(),
                "Private close shim failed: {} {}",
                String::from_utf8_lossy(&child.stdout),
                String::from_utf8_lossy(&child.stderr)
            );
            assert!(String::from_utf8_lossy(&child.stdout).contains("1 passed; 0 failed"));
            return;
        }
        // Private child fake-Niri-only PATH; no live Niri, capture or actuator.
        let (state, dir) = cooperative_fixture("close-target-shim");
        let shimdir = dir.join("fake-bin");
        private_dir(&shimdir).unwrap();
        let shim = shimdir.join("niri");
        fs::write(&shim, b"#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$WEASEL_PRIVATE_CLOSE_CALLS\"\ncase \"$*\" in\n 'msg -j windows') printf '%s\\n' \"$WEASEL_PRIVATE_CLOSE_WINDOWS\" ;;\n 'msg -j workspaces') printf '%s\\n' \"$WEASEL_PRIVATE_CLOSE_WORKSPACES\" ;;\n 'msg -j outputs') printf '%s\\n' \"$WEASEL_PRIVATE_CLOSE_OUTPUTS\" ;;\n 'msg action close-window --id 77') printf '%s\\n' '{}' ;;\n *) exit 17 ;;\nesac\n").unwrap();
        fs::set_permissions(&shim, fs::Permissions::from_mode(0o700)).unwrap();
        let calls = dir.join("fake-calls.txt");
        env::set_var("PATH", &shimdir);
        env::set_var("WEASEL_PRIVATE_CLOSE_CALLS", &calls);
        let target = json!({"id":77,"pid":42,"app_id":"private-fixture-editor","title":"Saved own fixture","workspace_id":33,"is_focused":true,"is_floating":false,"layout":{"fixture":1}});
        let workspace = json!({"id":33,"output":"DP-6","is_active":true,"is_focused":true,"active_window_id":77});
        let geometry = json!({"x":0,"y":0,"width":800,"height":600,"scale":1});
        let mut captured = obs(&dir);
        captured.output = "DP-6".into();
        captured.output_geometry = geometry.clone();
        captured.focused_window = Some(target.clone());
        captured.windows = json!([target.clone()]);
        captured.focus_output = Some("DP-6".into());
        state
            .observations
            .lock()
            .unwrap()
            .insert(captured.id.clone(), captured.clone());
        let args = json!({"observation_id":captured.id,"window_id":77,"actions":[{"kind":"close_window","window_id":77}],"observe_after":false});
        for scenario in [
            "focus",
            "pid",
            "app",
            "layout",
            "title",
            "output",
            "workspace_active",
            "active_window",
            "geometry",
            "duplicate",
            "missing",
            "generation",
        ] {
            let mut actual = target.clone();
            let mut ws = workspace.clone();
            let mut geo = geometry.clone();
            let mut windows = json!([actual.clone()]);
            match scenario {
                "focus" => {
                    actual["is_focused"] = json!(false);
                    windows = json!([actual.clone(),{"id":88,"pid":42,"app_id":"private-fixture-editor","workspace_id":33,"is_focused":true}]);
                }
                "pid" => actual["pid"] = json!(999),
                "app" => actual["app_id"] = json!("other-app"),
                "layout" => actual["layout"] = json!({"fixture":2}),
                "title" => actual["title"] = json!("Other document"),
                "output" => ws["output"] = json!("DP-5"),
                "workspace_active" => ws["is_active"] = json!(false),
                "active_window" => ws["active_window_id"] = json!(88),
                "geometry" => geo["scale"] = json!(1.25),
                "duplicate" => windows = json!([actual.clone(), actual.clone()]),
                "missing" => windows = json!([]),
                "generation" => {
                    state.input_policy.update_holds(1, true);
                    state.input_policy.update_holds(0, true);
                }
                _ => unreachable!(),
            }
            if !matches!(scenario, "focus" | "duplicate" | "missing") {
                windows = json!([actual]);
            }
            env::set_var("WEASEL_PRIVATE_CLOSE_WINDOWS", windows.to_string());
            env::set_var("WEASEL_PRIVATE_CLOSE_WORKSPACES", json!([ws]).to_string());
            env::set_var(
                "WEASEL_PRIVATE_CLOSE_OUTPUTS",
                json!({"DP-6":{"logical":geo}}).to_string(),
            );
            fs::write(&calls, b"").unwrap();
            let result = act(&state, &args, 77);
            if let Ok(result) = result {
                assert_eq!(
                    result_data(&result)["completed_actions"],
                    0,
                    "scenario {scenario}"
                );
            }
            let commands = fs::read_to_string(&calls).unwrap();
            assert!(
                !commands.contains("msg action"),
                "Wrong target command sent: {scenario}: {commands}"
            );
            assert!(state.active.lock().unwrap().is_null());
            assert_eq!(state.queued.load(Ordering::SeqCst), 0);
            assert!(state.release_confirmed.load(Ordering::SeqCst));
            assert!(state.actor.lock().unwrap().is_none());
            assert!(state.keyboard.lock().unwrap().is_none());
            assert!(state.global_keyboard.lock().unwrap().is_none());
        }
        state.input_policy.update_holds(0, true);
        captured.input_generation = state.input_policy.generation();
        state
            .observations
            .lock()
            .unwrap()
            .insert(captured.id.clone(), captured.clone());
        env::set_var("WEASEL_PRIVATE_CLOSE_WINDOWS", json!([target]).to_string());
        env::set_var(
            "WEASEL_PRIVATE_CLOSE_WORKSPACES",
            json!([workspace]).to_string(),
        );
        env::set_var(
            "WEASEL_PRIVATE_CLOSE_OUTPUTS",
            json!({"DP-6":{"logical":geometry}}).to_string(),
        );
        fs::write(&calls, b"").unwrap();
        let result = result_data(&act(&state, &args, 77).unwrap());
        assert_eq!(result["completed_actions"], 1);
        assert_eq!(result["effects"][0]["details"]["window_id"], 77);
        assert_eq!(
            result["effects"][0]["details"]["window_closed"],
            Value::Null
        );
        assert_eq!(
            result["effects"][0]["details"]["saved_document_or_ownership_attested"],
            false
        );
        let actual = fs::read_to_string(&calls).unwrap();
        let dispatched: Vec<_> = actual
            .lines()
            .filter(|line| line.starts_with("msg action"))
            .collect();
        assert_eq!(dispatched, ["msg action close-window --id 77"]);
        assert!(state.release_confirmed.load(Ordering::SeqCst));
        assert!(state.actor.lock().unwrap().is_none());
        fs::remove_dir_all(dir).unwrap();
    }
}

#[cfg(test)]
mod launch_contract_tests {
    use super::*;
    use release_recovery_tests::{cooperative_fixture, obs};
    fn fixture(label: &str) -> (Arc<State>, PathBuf, Observation, Value, Value, Value) {
        let (state, dir) = cooperative_fixture(label);
        let mut captured = obs(&dir);
        captured.output = "DP-6".into();
        captured.focus_output = Some("DP-6".into());
        captured.output_geometry = json!({"x":0,"y":0,"width":800,"height":600});
        captured.focused_window = Some(json!({"id":77,"pid":42,"app_id":"owned-editor","workspace_id":33,"is_focused":true}));
        let reserved = json!({"id":88,"pid":99,"app_id":"google-chrome","title":"YouTube / Alarm protected","workspace_id":55,"is_focused":false});
        let windows = json!([captured.focused_window.clone().unwrap(), reserved]);
        let workspaces = json!([{"id":33,"output":"DP-6","is_active":true,"is_focused":true,"active_window_id":77},{"id":55,"output":"DP-5","is_active":true,"is_focused":false,"active_window_id":88}]);
        let outputs = json!({"DP-6":{"logical":captured.output_geometry},"DP-5":{"logical":{"protected":true}}});
        captured.windows = windows.clone();
        state.observations.lock().unwrap().insert(captured.id.clone(), captured.clone());
        *state.human_monitor.lock().unwrap() = json!({"available":true,"watched_devices":1});
        (state, dir, captured, windows, workspaces, outputs)
    }
    #[test]
    fn launch_accepts_runtime_selected_outputs_beyond_trial_monitor_names() {
        for output in ["DP-5", "HDMI-A-2", "USB-C-1"] {
            let (state, dir, mut captured, windows, mut workspaces, _) = fixture("runtime-output");
            captured.output = output.into();
            captured.focus_output = Some(output.into());
            workspaces[0]["output"] = json!(output);
            let outputs = json!({output: {"logical": captured.output_geometry.clone()}});
            state.observations.lock().unwrap().insert(captured.id.clone(), captured.clone());
            preflight(&captured, &Action::LaunchApp { app: launch::App::Chrome }).unwrap();
            validate_launch_state_with_inventory(&state, &captured, 77,
                || Ok((windows.clone(), workspaces.clone(), outputs.clone()))).unwrap();
            fs::remove_dir_all(dir).unwrap();
        }
    }
    #[test]
    fn closed_shapes_reject_foreign_apps_projects_arguments_and_mixed_batches() {
        for request in [
            json!({"kind":"launch_app","app":"terminal"}),
            json!({"kind":"launch_app","app":"chrome","argv":["--profile-directory=Default"]}),
            json!({"kind":"launch_app","app":"zed","executable":"sh"}),
            json!({"kind":"editor_open_project","project":"other"}),
            json!({"kind":"editor_open_file","project":"jarvis","relative_path":"x","project_root":"/tmp"}),
            json!({"kind":"editor_open_file","project":"jarvis"}),
        ] { assert!(serde_json::from_value::<Action>(request).is_err()); }
        let (state, dir, captured, _, _, _) = fixture("launch-mixed");
        let args = json!({"observation_id":captured.id,"actions":[{"kind":"wait","ms":0},{"kind":"launch_app","app":"chrome"}]});
        assert!(act_inner(&state, &args, 77).unwrap_err().contains("standalone"));
        assert_eq!(state.capture_attempts.load(Ordering::SeqCst), 0);
        fs::remove_dir_all(dir).unwrap();
    }
    #[test]
    fn fake_inventory_gate_rejects_stop_stale_held_forbidden_and_replaced_targets_without_effects() {
        for failure in ["stop","epoch","generation","held","unretained","expired","monitor","output","workspace","identity","geometry"] {
            let (state, dir, mut captured, mut windows, mut workspaces, mut outputs) = fixture(failure);
            let reserved = windows[1].clone();
            match failure {
                "stop" => state.takeover.store(true, Ordering::SeqCst),
                "epoch" => state.epoch.store(78, Ordering::SeqCst),
                "generation" => { state.input_policy.activity(); },
                "held" => state.input_policy.update_holds(1, true),
                "unretained" => state.observations.lock().unwrap().clear(),
                "expired" => captured.at = Instant::now() - MAX_AGE - Duration::from_secs(1),
                "monitor" => *state.human_monitor.lock().unwrap() = json!({"available":false}),
                "output" => { captured.output = "DP-5".into(); captured.focus_output = Some("DP-5".into()); },
                "workspace" => workspaces[0]["is_active"] = json!(false),
                "identity" => windows[0]["pid"] = json!(43),
                "geometry" => outputs["DP-6"]["logical"]["width"] = json!(801),
                _ => unreachable!(),
            }
            let effects = AtomicUsize::new(0);
            let mut receipt = launch::Receipt::default();
            let result = launch::dispatch(&mut receipt,
                || validate_launch_state_with_inventory(&state, &captured, 77, || Ok((windows.clone(), workspaces.clone(), outputs.clone()))),
                |gate, receipt| { gate()?; effects.fetch_add(1, Ordering::SeqCst); receipt.commit(123); Ok(()) });
            assert!(result.is_err(), "{failure}");
            assert_eq!(effects.load(Ordering::SeqCst), 0, "{failure}");
            assert!(!receipt.spawn_committed);
            assert_eq!(windows[1], reserved);
            assert_eq!(state.capture_attempts.load(Ordering::SeqCst), 0);
            fs::remove_dir_all(dir).unwrap();
        }
    }
    #[test]
    fn cancel_and_stop_while_queued_never_acquire_or_dispatch_actor() {
        for cancel_only in [false, true] {
            let (state, dir, captured, _, _, _) = fixture(if cancel_only {"launch-queue-cancel"} else {"launch-queue-stop"});
            let actor = state.actor.lock().unwrap();
            let cancel = Arc::new(AtomicBool::new(false));
            let worker = {
                let state = state.clone();
                let cancel = cancel.clone();
                thread::spawn(move || {
                    CURRENT_CANCEL_FLAG.with(|f| *f.borrow_mut() = Some(cancel));
                    let result = act_inner(&state, &json!({"observation_id":captured.id,"observe_after":false,"actions":[{"kind":"launch_app","app":"chrome"}]}), 77);
                    CURRENT_CANCEL_FLAG.with(|f| *f.borrow_mut() = None);
                    result
                })
            };
            let deadline = Instant::now() + Duration::from_secs(2);
            while state.queued.load(Ordering::SeqCst) == 0 {
                assert!(Instant::now() < deadline, "queue admission did not reach barrier");
                thread::yield_now();
            }
            if cancel_only { cancel.store(true, Ordering::SeqCst); }
            else { state.takeover.store(true, Ordering::SeqCst); state.epoch.store(78, Ordering::SeqCst); }
            assert!(worker.join().unwrap().is_err());
            assert_eq!(state.queued.load(Ordering::SeqCst), 0);
            assert_eq!(*state.active.lock().unwrap(), Value::Null);
            assert!(state.release_confirmed.load(Ordering::SeqCst));
            assert_eq!(state.capture_attempts.load(Ordering::SeqCst), 0);
            assert!(state.actor.try_lock().is_err(), "fake writer still owns serialization");
            drop(actor);
            fs::remove_dir_all(dir).unwrap();
        }
    }
    #[test]
    fn unreleased_actor_refuses_before_any_inventory_or_spawn() {
        let (state, dir, captured, _, _, _) = fixture("launch-unreleased");
        state.release_confirmed.store(false, Ordering::SeqCst);
        assert!(act_inner(&state, &json!({"observation_id":captured.id,"actions":[{"kind":"launch_app","app":"zed"}]}), 77).unwrap_err().contains("release/receipt"));
        assert_eq!(state.queued.load(Ordering::SeqCst), 0);
        fs::remove_dir_all(dir).unwrap();
    }
}
