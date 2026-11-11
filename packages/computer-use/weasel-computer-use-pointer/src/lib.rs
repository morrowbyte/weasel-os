//! Persistent, output-bound Wayland virtual-pointer transport.
//!
//! This module sends input only when an action method is called. Construction
//! and capability queries perform registry reads without creating input devices.
//! A single owner must serialize all physical desktop actions across processes.

use serde::Serialize;
use std::{
    collections::{HashMap, HashSet},
    error::Error,
    fmt,
    os::fd::AsRawFd,
    time::{Duration, Instant},
};
use wayland_client::{
    delegate_noop,
    protocol::{wl_callback, wl_output, wl_pointer, wl_registry},
    Connection, Dispatch, EventQueue, Proxy, QueueHandle, WEnum,
};
use wayland_protocols_wlr::virtual_pointer::v1::client::{
    zwlr_virtual_pointer_manager_v1::ZwlrVirtualPointerManagerV1,
    zwlr_virtual_pointer_v1::ZwlrVirtualPointerV1,
};

#[derive(Debug)]
pub struct PointerError(pub String);
impl fmt::Display for PointerError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}
impl Error for PointerError {}
pub type Result<T> = std::result::Result<T, PointerError>;
fn error(context: &str, source: impl fmt::Display) -> PointerError {
    PointerError(format!("{context}: {source}"))
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Button {
    Left,
    Right,
    Middle,
}
impl Button {
    fn evdev(self) -> u32 {
        match self {
            Self::Left => 0x110,
            Self::Right => 0x111,
            Self::Middle => 0x112,
        }
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct OutputInfo {
    pub global_id: u32,
    pub version: u32,
    pub name: Option<String>,
    pub description: Option<String>,
    /// Integer protocol scale only. Fractional scaling must come from Niri IPC.
    pub integer_scale: i32,
    pub current_physical_size: Option<(i32, i32)>,
}

#[derive(Debug, Clone, Serialize)]
pub struct Capabilities {
    pub manager_version: Option<u32>,
    pub output_bound_absolute: bool,
    pub advertised_globals: Vec<(String, u32)>,
}

struct OutputEntry {
    info: OutputInfo,
    proxy: wl_output::WlOutput,
    transform: OutputTransform,
}

#[derive(Default)]
struct OutputTransform {
    current: Option<WEnum<wl_output::Transform>>,
    pending: Option<WEnum<wl_output::Transform>>,
}

impl OutputTransform {
    fn confirmed(&self) -> Result<wl_output::Transform> {
        if self.pending.is_some() {
            return Err(PointerError(
                "output transform update incomplete; observe again".into(),
            ));
        }
        match self.current {
            Some(WEnum::Value(transform)) => Ok(transform),
            _ => Err(PointerError(
                "known output transform required before pointer input".into(),
            )),
        }
    }

    fn commit(&mut self) {
        if let Some(transform) = self.pending.take() {
            self.current = Some(transform);
        }
    }
}

const ABSOLUTE_EXTENT: u32 = 1_000_000;

/// The caller supplies normalized, displayed output coordinates. A named-output
/// virtual pointer is transformed by the compositor, so send its inverse first.
fn absolute_coordinates(transform: wl_output::Transform, x: f64, y: f64) -> Result<(u32, u32)> {
    use wl_output::Transform;
    let (x, y) = match transform {
        Transform::Normal => (x, y),
        Transform::_90 => (y, 1.0 - x),
        Transform::_180 => (1.0 - x, 1.0 - y),
        Transform::_270 => (1.0 - y, x),
        Transform::Flipped => (1.0 - x, y),
        Transform::Flipped90 => (y, x),
        Transform::Flipped180 => (x, 1.0 - y),
        Transform::Flipped270 => (1.0 - y, 1.0 - x),
        _ => return Err(PointerError("unsupported output transform".into())),
    };
    Ok((
        (x * ABSOLUTE_EXTENT as f64).round() as u32,
        (y * ABSOLUTE_EXTENT as f64).round() as u32,
    ))
}
#[derive(Default)]
struct State {
    manager: Option<(u32, ZwlrVirtualPointerManagerV1)>,
    manager_removed: bool,
    outputs: HashMap<u32, OutputEntry>,
    globals: HashMap<u32, (String, u32)>,
    completed_sync: u64,
}

impl Dispatch<wl_registry::WlRegistry, ()> for State {
    fn event(
        state: &mut Self,
        registry: &wl_registry::WlRegistry,
        event: wl_registry::Event,
        _: &(),
        _: &Connection,
        qh: &QueueHandle<Self>,
    ) {
        match event {
            wl_registry::Event::Global {
                name,
                interface,
                version,
            } => {
                state.globals.insert(name, (interface.clone(), version));
                match interface.as_str() {
                    "zwlr_virtual_pointer_manager_v1" => {
                        let manager = registry.bind::<ZwlrVirtualPointerManagerV1, _, _>(
                            name,
                            version.min(2),
                            qh,
                            (),
                        );
                        state.manager = Some((name, manager));
                    }
                    "wl_output" => {
                        let bound_version = version.min(4);
                        let proxy = registry.bind::<wl_output::WlOutput, _, _>(
                            name,
                            bound_version,
                            qh,
                            name,
                        );
                        state.outputs.insert(
                            name,
                            OutputEntry {
                                info: OutputInfo {
                                    global_id: name,
                                    version: bound_version,
                                    name: None,
                                    description: None,
                                    integer_scale: 1,
                                    current_physical_size: None,
                                },
                                proxy,
                                transform: OutputTransform::default(),
                            },
                        );
                    }
                    _ => {}
                }
            }
            wl_registry::Event::GlobalRemove { name } => {
                state.globals.remove(&name);
                state.outputs.remove(&name);
                if state.manager.as_ref().map(|x| x.0) == Some(name) {
                    state.manager = None;
                    state.manager_removed = true;
                }
            }
            _ => {}
        }
    }
}

impl Dispatch<wl_output::WlOutput, u32> for State {
    fn event(
        state: &mut Self,
        _: &wl_output::WlOutput,
        event: wl_output::Event,
        id: &u32,
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        if let Some(output) = state.outputs.get_mut(id) {
            match event {
                wl_output::Event::Geometry { transform, .. } => {
                    output.transform.pending = Some(transform);
                }
                wl_output::Event::Done => output.transform.commit(),
                wl_output::Event::Name { name } => output.info.name = Some(name),
                wl_output::Event::Description { description } => {
                    output.info.description = Some(description)
                }
                wl_output::Event::Scale { factor } => output.info.integer_scale = factor,
                wl_output::Event::Mode {
                    flags: WEnum::Value(flags),
                    width,
                    height,
                    ..
                } if flags.contains(wl_output::Mode::Current) => {
                    output.info.current_physical_size = Some((width, height));
                }
                _ => {}
            }
        }
    }
}
delegate_noop!(State: ignore ZwlrVirtualPointerManagerV1);
delegate_noop!(State: ignore ZwlrVirtualPointerV1);

impl Dispatch<wl_callback::WlCallback, u64> for State {
    fn event(
        state: &mut Self,
        _: &wl_callback::WlCallback,
        event: wl_callback::Event,
        serial: &u64,
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        if let wl_callback::Event::Done { .. } = event {
            state.completed_sync = state.completed_sync.max(*serial);
        }
    }
}

/// One persistent compositor connection; action methods require exclusive access.
pub struct Pointer {
    connection: Connection,
    queue: EventQueue<State>,
    state: State,
    pointers: HashMap<u32, ZwlrVirtualPointerV1>,
    active_output: Option<u32>,
    held: HashSet<Button>,
    clock: Instant,
    next_sync: u64,
}

impl Pointer {
    pub fn new() -> Result<Self> {
        Self::new_timeout(Duration::from_secs(2))
    }

    /// Bounds both discovery roundtrips by one bootstrap budget. This does not
    /// create a pointer device or send input; move_to still creates the device.
    pub fn new_timeout(timeout: Duration) -> Result<Self> {
        if timeout.is_zero() {
            return Err(PointerError("pointer bootstrap deadline expired".into()));
        }
        let deadline = Instant::now()
            .checked_add(timeout)
            .ok_or_else(|| PointerError("invalid pointer bootstrap deadline".into()))?;
        let remaining = || {
            let remaining = deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                Err(PointerError("pointer bootstrap deadline expired".into()))
            } else {
                Ok(remaining.min(Duration::from_secs(1)))
            }
        };
        let connection =
            Connection::connect_to_env().map_err(|e| error("Wayland connection", e))?;
        let queue = connection.new_event_queue();
        let state = State::default();
        connection.display().get_registry(&queue.handle(), ());
        let mut pointer = Self {
            connection,
            queue,
            state,
            pointers: HashMap::new(),
            active_output: None,
            held: HashSet::new(),
            clock: Instant::now(),
            next_sync: 1,
        };
        pointer.sync_timeout(remaining()?)?;
        // Globals arrive in roundtrip 1; the output names arrive after bindings.
        pointer.sync_timeout(remaining()?)?;
        Ok(pointer)
    }

    pub fn capabilities(&self) -> Capabilities {
        let version = self.state.manager.as_ref().map(|(_, m)| m.version());
        let mut globals: Vec<_> = self.state.globals.values().cloned().collect();
        globals.sort();
        Capabilities {
            manager_version: version,
            output_bound_absolute: version.map(|v| v >= 2).unwrap_or(false),
            advertised_globals: globals,
        }
    }

    pub fn outputs(&self) -> Vec<OutputInfo> {
        let mut outputs: Vec<_> = self
            .state
            .outputs
            .values()
            .map(|x| x.info.clone())
            .collect();
        outputs.sort_by_key(|x| x.global_id);
        outputs
    }

    /// Refresh registry removals and newly bound output names before resolving
    /// a pointer target. Both syncs consume one caller-owned time budget; no
    /// input device is created and no synthetic input is sent by this method.
    /// Stale idle proxies are destroyed, but a held proxy remains available to
    /// release_all even when its former output or manager has disappeared.
    pub fn refresh_capabilities_timeout(&mut self, timeout: Duration) -> Result<()> {
        if timeout.is_zero() {
            return Err(PointerError(
                "pointer capability refresh deadline expired".into(),
            ));
        }
        let deadline = Instant::now()
            .checked_add(timeout)
            .ok_or_else(|| PointerError("invalid pointer capability refresh deadline".into()))?;
        let remaining = || {
            let left = deadline.saturating_duration_since(Instant::now());
            if left.is_zero() {
                Err(PointerError(
                    "pointer capability refresh deadline expired".into(),
                ))
            } else {
                Ok(left)
            }
        };
        self.sync_timeout(remaining()?)?;
        // New wl_output bindings discovered in sync 1 receive names in sync 2.
        self.sync_timeout(remaining()?)?;
        let previous_active = self.active_output;
        let active_removed =
            previous_active.is_some_and(|id| !self.state.outputs.contains_key(&id));
        let held_output = if self.held.is_empty() {
            None
        } else {
            previous_active
        };
        let stale: Vec<u32> = self
            .pointers
            .keys()
            .copied()
            .filter(|id| {
                Some(*id) != held_output
                    && (self.state.manager_removed || !self.state.outputs.contains_key(id))
            })
            .collect();
        for id in stale {
            if let Some(pointer) = self.pointers.remove(&id) {
                pointer.destroy();
            }
        }
        if active_removed && self.held.is_empty() {
            self.active_output = None;
        }
        if self.state.manager_removed || self.state.manager.is_none() {
            return Err(PointerError("virtual pointer manager disappeared; release input and explicitly reconnect the backend".into()));
        }
        if active_removed {
            return Err(PointerError(
                "previous target output disappeared; release input and obtain a fresh observation"
                    .into(),
            ));
        }
        remaining()?;
        Ok(())
    }

    fn time(&self) -> u32 {
        self.clock.elapsed().as_millis() as u32
    }

    fn flush(&self) -> Result<()> {
        self.connection
            .flush()
            .map_err(|e| error("Wayland flush", e))
    }

    fn active(&self) -> Result<&ZwlrVirtualPointerV1> {
        if self.state.manager_removed || self.state.manager.is_none() {
            return Err(PointerError(
                "virtual pointer manager unavailable; only release cleanup is allowed".into(),
            ));
        }
        let id = self
            .active_output
            .ok_or_else(|| PointerError("move_to is required before pointer input".into()))?;
        if !self.state.outputs.contains_key(&id) {
            return Err(PointerError(
                "target output disappeared; observe again".into(),
            ));
        }
        self.pointers
            .get(&id)
            .ok_or_else(|| PointerError("active virtual pointer unavailable".into()))
    }

    /// Move to displayed output-local coordinates with full output width/height.
    /// A screenshot crop must first be mapped back through its output-local origin.
    /// Fractional coordinates are retained by normalization; no guessed global origin.
    pub fn move_to(&mut self, output: &str, x: f64, y: f64, width: u32, height: u32) -> Result<()> {
        if width == 0
            || height == 0
            || !x.is_finite()
            || !y.is_finite()
            || x < 0.0
            || y < 0.0
            || x >= width as f64
            || y >= height as f64
        {
            return Err(PointerError(
                "coordinates must be finite and inside nonzero output extents".into(),
            ));
        }
        self.queue
            .dispatch_pending(&mut self.state)
            .map_err(|e| error("Wayland pending events", e))?;
        if self.state.manager_removed || self.state.manager.is_none() {
            return Err(PointerError(
                "virtual pointer manager unavailable; only release cleanup is allowed".into(),
            ));
        }
        let mut matches = self
            .state
            .outputs
            .values()
            .filter(|entry| entry.info.name.as_deref() == Some(output));
        let entry = matches.next().ok_or_else(|| {
            PointerError(format!(
                "named output {output:?} unavailable; wl_output v4 name is required"
            ))
        })?;
        if matches.next().is_some() {
            return Err(PointerError("output name is ambiguous".into()));
        }
        let (nx, ny) = absolute_coordinates(
            entry.transform.confirmed()?,
            x / width as f64,
            y / height as f64,
        )?;
        let id = entry.info.global_id;
        if self.active_output != Some(id) && !self.held.is_empty() {
            return Err(PointerError(
                "cannot change output while a pointer button is held".into(),
            ));
        }
        if !self.pointers.contains_key(&id) {
            let (_, manager) = self.state.manager.as_ref().ok_or_else(|| {
                PointerError("zwlr_virtual_pointer_manager_v1 unavailable".into())
            })?;
            if manager.version() < 2 {
                return Err(PointerError(
                    "virtual-pointer v2 required for safe named-output mapping".into(),
                ));
            }
            let pointer = manager.create_virtual_pointer_with_output(
                None,
                Some(&entry.proxy),
                &self.queue.handle(),
                (),
            );
            self.pointers.insert(id, pointer);
        }
        self.active_output = Some(id);
        let pointer = self.active()?;
        pointer.motion_absolute(self.time(), nx, ny, ABSOLUTE_EXTENT, ABSOLUTE_EXTENT);
        pointer.frame();
        self.flush()
    }

    /// Press/release a Linux evdev pointer button. Duplicate transitions are refused.
    pub fn button(&mut self, button: Button, pressed: bool) -> Result<()> {
        if self.held.contains(&button) == pressed {
            return Err(PointerError("duplicate button transition refused".into()));
        }
        let pointer = self.active()?;
        pointer.button(
            self.time(),
            button.evdev(),
            if pressed {
                wl_pointer::ButtonState::Pressed
            } else {
                wl_pointer::ButtonState::Released
            },
        );
        pointer.frame();
        // Record the pending transition before flushing so cancellation can release it.
        if pressed {
            self.held.insert(button);
        } else {
            self.held.remove(&button);
        }
        self.flush()
    }

    /// Scroll by continuous protocol units; positive dx is right, positive dy down.
    pub fn scroll(&mut self, dx: f64, dy: f64) -> Result<()> {
        if !dx.is_finite() || !dy.is_finite() || dx.abs() > 10_000.0 || dy.abs() > 10_000.0 {
            return Err(PointerError(
                "scroll deltas must be finite and bounded".into(),
            ));
        }
        let pointer = self.active()?;
        pointer.axis_source(wl_pointer::AxisSource::Continuous);
        if dx != 0.0 {
            pointer.axis(self.time(), wl_pointer::Axis::HorizontalScroll, dx);
        }
        if dy != 0.0 {
            pointer.axis(self.time(), wl_pointer::Axis::VerticalScroll, dy);
        }
        pointer.frame();
        if dx != 0.0 {
            pointer.axis_stop(self.time(), wl_pointer::Axis::HorizontalScroll);
        }
        if dy != 0.0 {
            pointer.axis_stop(self.time(), wl_pointer::Axis::VerticalScroll);
        }
        pointer.frame();
        self.flush()
    }

    /// Discrete wheel steps, for UI controls which ignore continuous scrolling.
    pub fn scroll_steps(&mut self, dx: i32, dy: i32) -> Result<()> {
        if dx.unsigned_abs() > 100 || dy.unsigned_abs() > 100 {
            return Err(PointerError("wheel steps exceed 100 per axis".into()));
        }
        let pointer = self.active()?;
        pointer.axis_source(wl_pointer::AxisSource::Wheel);
        if dx != 0 {
            pointer.axis_discrete(
                self.time(),
                wl_pointer::Axis::HorizontalScroll,
                dx as f64 * 15.0,
                dx,
            );
        }
        if dy != 0 {
            pointer.axis_discrete(
                self.time(),
                wl_pointer::Axis::VerticalScroll,
                dy as f64 * 15.0,
                dy,
            );
        }
        pointer.frame();
        self.flush()
    }

    /// Release held buttons for cancellation/takeover. No movement is sent.
    pub fn release_all(&mut self) -> Result<()> {
        if self.held.is_empty() {
            return Ok(());
        }
        let buttons: Vec<_> = self.held.iter().copied().collect();
        // Release the device even if its formerly mapped output was unplugged.
        // New actions still require a current output through active().
        let pointer = self
            .active_output
            .and_then(|id| self.pointers.get(&id))
            .ok_or_else(|| PointerError("held pointer device unavailable".into()))?;
        for button in buttons {
            pointer.button(
                self.time(),
                button.evdev(),
                wl_pointer::ButtonState::Released,
            );
        }
        pointer.frame();
        self.held.clear();
        self.flush()
    }

    /// Confirm compositor receipt with a one-second deadline; not UI verification.
    pub fn sync(&mut self) -> Result<()> {
        self.sync_timeout(Duration::from_secs(1))
    }

    /// Bounded receipt acknowledgement for a responsive embedding action queue.
    /// No synthetic motion or other input is sent by this method.
    pub fn sync_timeout(&mut self, timeout: Duration) -> Result<()> {
        let serial = self.next_sync;
        self.next_sync = self
            .next_sync
            .checked_add(1)
            .ok_or_else(|| PointerError("sync serial exhausted".into()))?;
        self.connection.display().sync(&self.queue.handle(), serial);
        self.flush()?;
        let deadline = Instant::now()
            .checked_add(timeout)
            .ok_or_else(|| PointerError("sync timeout out of range".into()))?;
        loop {
            self.queue
                .dispatch_pending(&mut self.state)
                .map_err(|e| error("Wayland sync dispatch", e))?;
            if self.state.completed_sync >= serial {
                return Ok(());
            }
            let remaining = deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                return Err(PointerError(
                    "Wayland compositor acknowledgement timed out".into(),
                ));
            }
            if let Some(guard) = self.queue.prepare_read() {
                let millis = remaining.as_millis().clamp(1, i32::MAX as u128) as i32;
                let mut fd = libc::pollfd {
                    fd: self.connection.backend().poll_fd().as_raw_fd(),
                    events: libc::POLLIN,
                    revents: 0,
                };
                // Polling only the compositor fd; guard holds the coordinated read lease.
                let result = unsafe { libc::poll(&mut fd, 1, millis) };
                if result < 0 {
                    let source = std::io::Error::last_os_error();
                    if source.kind() == std::io::ErrorKind::Interrupted {
                        continue;
                    }
                    return Err(error("Wayland poll", source));
                }
                if result == 0 {
                    continue;
                }
                if fd.revents & (libc::POLLERR | libc::POLLHUP | libc::POLLNVAL) != 0 {
                    return Err(PointerError(
                        "Wayland connection unavailable during acknowledgement".into(),
                    ));
                }
                if fd.revents & libc::POLLIN != 0 {
                    guard.read().map_err(|e| error("Wayland sync read", e))?;
                }
            }
        }
    }
}

impl Drop for Pointer {
    fn drop(&mut self) {
        let _ = self.release_all();
        for pointer in self.pointers.values() {
            pointer.destroy();
        }
        let _ = self.connection.flush();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn displayed_point_has_expected_wire_coordinates_for_all_transforms() {
        use wl_output::Transform::*;
        let cases = [
            (Normal, (200_000, 700_000)),
            (_90, (700_000, 800_000)),
            (_180, (800_000, 300_000)),
            (_270, (300_000, 200_000)),
            (Flipped, (800_000, 700_000)),
            (Flipped90, (700_000, 200_000)),
            (Flipped180, (200_000, 300_000)),
            (Flipped270, (300_000, 800_000)),
        ];
        for (transform, expected) in cases {
            assert_eq!(
                absolute_coordinates(transform, 200.0 / 1000.0, 350.0 / 500.0).unwrap(),
                expected
            );
        }
    }

    #[test]
    fn quantized_wire_coordinates_restore_displayed_pixels_at_fractional_and_edge_points() {
        use wl_output::Transform::*;
        // Independently specified compositor forward matrices in the unit square.
        let cases = [
            (Normal, [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]),
            (_90, [0.0, -1.0, 1.0, 0.0, 1.0, 0.0]),
            (_180, [-1.0, 0.0, 0.0, -1.0, 1.0, 1.0]),
            (_270, [0.0, 1.0, -1.0, 0.0, 0.0, 1.0]),
            (Flipped, [-1.0, 0.0, 0.0, 1.0, 1.0, 0.0]),
            (Flipped90, [0.0, 1.0, 1.0, 0.0, 0.0, 0.0]),
            (Flipped180, [1.0, 0.0, 0.0, -1.0, 0.0, 1.0]),
            (Flipped270, [0.0, -1.0, -1.0, 0.0, 1.0, 1.0]),
        ];
        let (width, height) = (1276.0, 773.0);
        for (transform, [a, b, c, d, tx, ty]) in cases {
            for (x, y) in [(0.0, 0.0), (1110.25, 570.125), (1275.75, 772.5)] {
                let (wire_x, wire_y) =
                    absolute_coordinates(transform, x / width, y / height).unwrap();
                let (u, v) = (
                    wire_x as f64 / ABSOLUTE_EXTENT as f64,
                    wire_y as f64 / ABSOLUTE_EXTENT as f64,
                );
                let actual_x = (a * u + b * v + tx) * width;
                let actual_y = (c * u + d * v + ty) * height;
                assert!((actual_x - x).abs() <= width / (2.0 * ABSOLUTE_EXTENT as f64) + 1e-9);
                assert!((actual_y - y).abs() <= height / (2.0 * ABSOLUTE_EXTENT as f64) + 1e-9);
            }
        }
    }

    #[test]
    fn r13_crop_target_is_sent_before_the_compositor_vertical_flip() {
        // R13: fresh crop (500,400), target (610,170), full PNG 1276x773.
        let (x, y) = (500.0 + 610.0, 400.0 + 170.0);
        assert_eq!(
            absolute_coordinates(wl_output::Transform::Flipped180, x / 1276.0, y / 773.0).unwrap(),
            (869_906, 262_613)
        );
    }

    #[test]
    fn transform_requires_a_complete_known_output_event_group() {
        let mut transform = OutputTransform::default();
        assert!(transform.confirmed().is_err());
        transform.pending = Some(WEnum::Value(wl_output::Transform::Normal));
        assert!(transform.confirmed().is_err());
        transform.commit();
        assert_eq!(transform.confirmed().unwrap(), wl_output::Transform::Normal);
        transform.pending = Some(WEnum::Value(wl_output::Transform::Flipped180));
        assert!(transform.confirmed().is_err());
        transform.commit();
        assert_eq!(
            transform.confirmed().unwrap(),
            wl_output::Transform::Flipped180
        );
        transform.pending = Some(WEnum::Unknown(99));
        assert!(transform.confirmed().is_err());
        transform.commit();
        assert!(transform.confirmed().is_err());
    }
}
