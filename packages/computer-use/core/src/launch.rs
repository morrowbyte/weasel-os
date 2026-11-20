//! Closed desktop launch contracts. No caller-controlled executable, argv or profile.
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{fs, os::unix::fs::{DirBuilderExt, MetadataExt, PermissionsExt}, path::{Path, PathBuf}};

type R<T> = Result<T, String>;
pub const REVISION: u32 = 1;
pub const PROJECT_ROOT: &str = "/home/evilweasel/source/personal/jarvis";
// Preserve the installed laptop contract for the default build.
const LAPTOP_ZED_CLI: &str = "/nix/store/j7y0acvm99dcdmsf0f7llbb5hjmpq88j-zed-editor-1.22.0/bin/zeditor";
const LAPTOP_ZED_APP: &str = "/nix/store/j7y0acvm99dcdmsf0f7llbb5hjmpq88j-zed-editor-1.22.0/libexec/zed-editor";
const LAPTOP_CHROME: &str = "/nix/store/ki71ax98a77yj17xygmnk6ziizy4j099-google-chrome-155.0.8059.39/bin/google-chrome-stable";
const VM_ZED: Option<&str> = option_env!("WEASEL_VM_ZED");
const VM_CHROMIUM: Option<&str> = option_env!("WEASEL_VM_CHROMIUM");

#[derive(Debug, Clone, Copy, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum App { Zed, Chrome }
impl App {
    fn program(self) -> &'static str {
        match self {
            Self::Zed => VM_ZED.unwrap_or(LAPTOP_ZED_CLI),
            Self::Chrome => VM_CHROMIUM.unwrap_or(LAPTOP_CHROME),
        }
    }
    fn available(self) -> bool {
        executable(self.program())
            && (!matches!(self, Self::Zed) || VM_ZED.is_some() || executable(LAPTOP_ZED_APP))
    }
}
#[derive(Debug, Clone, Copy, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Project { Jarvis }
#[derive(Clone, Copy)]
pub enum Request<'a> {
    App(App),
    Project(Project),
    File(Project, &'a str),
}

pub fn relative_path(path: &str) -> R<()> {
    // Zed interprets ':' as a line/column suffix, so it cannot be a filename here.
    if path.is_empty() || path.len() > 4096 || path.contains(['\0', '\\', ':'])
        || path.split('/').any(|p| p.is_empty() || p == "." || p == "..") {
        return Err("relative_path must be a bounded relative file path without traversal, empty components or position syntax".into());
    }
    Ok(())
}
fn project_path(root: &Path, relative: Option<&str>) -> R<PathBuf> {
    let canonical = root.canonicalize().map_err(|e| format!("Jarvis project unavailable: {e}"))?;
    if canonical != root || !canonical.is_dir() {
        return Err("Jarvis project root is not the exact canonical allowlisted directory".into());
    }
    match relative {
        None => Ok(canonical),
        Some(path) => {
            relative_path(path)?;
            let file = canonical.join(path).canonicalize().map_err(|e| format!("Jarvis file unavailable: {e}"))?;
            if !file.starts_with(&canonical) || !file.is_file() {
                return Err("Jarvis file must be an existing regular file inside the canonical project; symlink escape refused".into());
            }
            relative_path(file.strip_prefix(&canonical).map_err(|e| e.to_string())?
                .to_str().ok_or("Canonical project path is not UTF-8")?)?;
            Ok(file)
        }
    }
}
fn executable(path: &str) -> bool {
    fs::metadata(path).is_ok_and(|m| m.is_file() && m.permissions().mode() & 0o111 != 0)
}
pub fn capability() -> Value {
    let isolation = if VM_ZED.is_some() {
        "fresh private user-data-dir per action; direct Zed GUI, Chromium --new-window"
    } else {
        "fresh private user-data-dir per action; Zed --new --foreground, Chrome --new-window"
    };
    json!({
        "revision": REVISION, "implemented": true, "standalone": true,
        "apps": ["zed", "chrome"], "projects": ["jarvis"],
        "project_roots_present": {"jarvis": project_path(Path::new(PROJECT_ROOT), None).is_ok()},
        "outputs": "runtime enabled outputs; current task target remains explicit",
        "pinned_binaries_present": {"zed": App::Zed.available(), "chrome": App::Chrome.available()},
        "isolation": isolation, "spawn_ack_is_ui_success": false,
        "uncertain_after_spawn_requires_verification": true, "automatic_retry": false,
        "live_isolation_attested": false
    })
}
pub struct Plan { pub program: &'static str, pub args: Vec<String> }
impl Plan {
    pub fn require_same_target(&self, current: &Self) -> R<()> {
        if self.program != current.program || self.args != current.args {
            return Err("Launch command target changed during preparation; no spawn, reobserve".into());
        }
        Ok(())
    }
}
pub fn plan(request: Request<'_>, profile: &Path) -> R<Plan> {
    let app = match request { Request::App(app) => app, _ => App::Zed };
    if !app.available() {
        return Err("Launch capability unavailable: exact isolated app binary pin missing".into());
    }
    let mut paths = Vec::new();
    match request {
        Request::Project(Project::Jarvis) => paths.push(project_path(Path::new(PROJECT_ROOT), None)?),
        Request::File(Project::Jarvis, path) => {
            paths.push(project_path(Path::new(PROJECT_ROOT), None)?);
            paths.push(project_path(Path::new(PROJECT_ROOT), Some(path))?);
        }
        Request::App(_) => {},
    }
    command_plan(app, profile, paths)
}
fn command_plan(app: App, profile: &Path, paths: Vec<PathBuf>) -> R<Plan> {
    let program = app.program();
    let profile = profile.to_str().ok_or("Private launch profile path is not UTF-8")?;
    let mut args: Vec<String> = match app {
        // The Linux CLI spawns a child even with --foreground. Launch the VM
        // GUI directly so Niri can report the PID from the actor's receipt.
        App::Zed if VM_ZED.is_some() => vec!["--user-data-dir".into(), profile.into()],
        App::Zed => vec!["--zed".into(), LAPTOP_ZED_APP.into(), "--foreground".into(), "--new".into(), "--user-data-dir".into(), profile.into()],
        App::Chrome => {
            let mut args = vec![format!("--user-data-dir={profile}"), "--new-window".into(), "--no-first-run".into(), "--no-default-browser-check".into()];
            if VM_CHROMIUM.is_some() {
                args.push("--ozone-platform=wayland".into());
            }
            args.push("about:blank".into());
            args
        }
    };
    for path in paths {
        args.push(path.to_str().ok_or("Canonical project path is not UTF-8")?.into());
    }
    Ok(Plan { program, args })
}

// Fresh directories prevent single-instance forwarding to any user profile.
// Drop removes only our empty uncommitted directory; committed profiles remain
// for explicit owned-app inspection/cleanup, never delete a running profile.
pub struct Profile { pub path: PathBuf, pub committed: bool }
impl Profile {
    pub fn create(path: PathBuf) -> R<Self> {
        let parent = path.parent().ok_or("Private profile parent missing")?;
        let m = fs::symlink_metadata(parent).map_err(|e| e.to_string())?;
        if !m.is_dir() || m.uid() != unsafe { libc::geteuid() } || m.permissions().mode() & 0o077 != 0 || parent.canonicalize().map_err(|e| e.to_string())? != parent {
            return Err("Launch capability unavailable: actor profile parent is not a private canonical owned directory".into());
        }
        fs::DirBuilder::new().mode(0o700).create(&path).map_err(|e| format!("Cannot exclusively create private launch profile: {e}"))?;
        Ok(Self { path, committed: false })
    }
}
impl Drop for Profile {
    fn drop(&mut self) { if !self.committed { let _ = fs::remove_dir(&self.path); } }
}
#[derive(Default, Serialize)]
pub struct Receipt {
    pub spawn_committed: bool,
    pub pid: Option<u32>,
    pub spawn_acknowledged: bool,
    pub verification_required: bool,
}
impl Receipt {
    pub fn commit(&mut self, pid: u32) {
        self.spawn_committed = true;
        self.pid = Some(pid);
        self.verification_required = true;
    }
}
// The executor runs the same gate immediately before its actual spawn syscall.
// Test executors can pause here without running applications or touching devices.
pub fn dispatch(
    receipt: &mut Receipt,
    mut gate: impl FnMut() -> R<()>,
    executor: impl FnOnce(&mut dyn FnMut() -> R<()>, &mut Receipt) -> R<()>,
) -> R<()> {
    gate()?;
    executor(&mut gate, receipt)?;
    receipt.spawn_acknowledged = true;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{sync::{Arc, Barrier, atomic::{AtomicBool, AtomicUsize, Ordering}}, thread};

    #[test]
    fn fake_executor_barriers_stop_before_commit_and_never_retry_after_commit() {
        for committed in [false, true] {
            let barrier = Arc::new(Barrier::new(2));
            let stopped = Arc::new(AtomicBool::new(false));
            let effects = Arc::new(AtomicUsize::new(0));
            let worker = {
                let barrier = barrier.clone();
                let stopped = stopped.clone();
                let effects = effects.clone();
                thread::spawn(move || {
                    let mut receipt = Receipt::default();
                    let result = dispatch(&mut receipt,
                        || if stopped.load(Ordering::SeqCst) { Err("canceled".into()) } else { Ok(()) },
                        |gate, receipt| {
                            if committed {
                                gate()?;
                                effects.fetch_add(1, Ordering::SeqCst);
                                receipt.commit(99);
                            }
                            barrier.wait(); // exact precommit or postcommit boundary
                            barrier.wait();
                            gate()?;
                            if !committed {
                                effects.fetch_add(1, Ordering::SeqCst);
                                receipt.commit(99);
                            }
                            Ok(())
                        });
                    (result, receipt)
                })
            };
            barrier.wait();
            stopped.store(true, Ordering::SeqCst);
            barrier.wait();
            let (result, receipt) = worker.join().unwrap();
            assert!(result.is_err());
            assert_eq!(effects.load(Ordering::SeqCst), usize::from(committed));
            assert_eq!(receipt.spawn_committed, committed);
            assert_eq!(receipt.verification_required, committed);
            assert!(!receipt.spawn_acknowledged);
        }
    }
    #[test]
    fn fake_executor_ack_does_not_claim_ui_success() {
        let mut receipt = Receipt::default();
        dispatch(&mut receipt, || Ok(()), |gate, receipt| {
            gate()?;
            receipt.commit(42);
            Ok(())
        }).unwrap();
        assert!(receipt.spawn_committed && receipt.spawn_acknowledged && receipt.verification_required);
    }
    #[test]
    fn file_containment_rejects_traversal_missing_directory_and_symlink_escape() {
        let root = std::env::temp_dir().join(format!("launch-files-{}",std::process::id()));
        fs::create_dir(&root).unwrap();
        fs::write(root.join("inside.rs"), "original").unwrap();
        std::os::unix::fs::symlink("/etc/passwd", root.join("escape.rs")).unwrap();
        assert_eq!(project_path(&root, Some("inside.rs")).unwrap(), root.join("inside.rs"));
        for path in ["", "../passwd", "/etc/passwd", "./inside.rs", "a/../inside.rs", "a//b", "inside.rs:12", "a\\b", "a\0b", "missing", "escape.rs"] {
            assert!(project_path(&root, Some(path)).is_err(), "{path:?}");
        }
        fs::create_dir(root.join("folder")).unwrap();
        assert!(project_path(&root, Some("folder")).is_err());
        std::os::unix::fs::symlink(&root, root.join("root-alias")).unwrap();
        assert!(project_path(&root.join("root-alias"), None).is_err());
        assert_eq!(fs::read_to_string(root.join("inside.rs")).unwrap(), "original");
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn profiles_are_private_exclusive_and_uncommitted_cleanup_preserves_reserved_files() {
        let parent = std::env::temp_dir().join(format!("launch-profile-{}", std::process::id()));
        fs::DirBuilder::new().mode(0o700).create(&parent).unwrap();
        let reserved = parent.join("reserved-user-profile");
        fs::create_dir(&reserved).unwrap();
        fs::write(reserved.join("original"), "protected").unwrap();
        assert!(Profile::create(reserved.clone()).is_err());
        let owned = parent.join("new-actor-profile");
        {
            let profile = Profile::create(owned.clone()).unwrap();
            assert_eq!(fs::metadata(&profile.path).unwrap().permissions().mode() & 0o777, 0o700);
            assert!(Profile::create(owned.clone()).is_err());
        }
        assert!(!owned.exists());
        {
            let mut profile = Profile::create(owned.clone()).unwrap();
            profile.committed = true;
        }
        assert!(owned.exists());
        assert_eq!(fs::read_to_string(reserved.join("original")).unwrap(), "protected");
        fs::remove_dir_all(parent).unwrap();
    }
}

#[cfg(test)]
mod command_tests {
    use super::*;
    #[test]
    fn fixed_commands_have_fresh_profile_and_no_reserved_identity_or_shell_arguments() {
        let private = Path::new("/private/actor/new-profile");
        let zed = command_plan(App::Zed, private, vec![PathBuf::from(PROJECT_ROOT), PathBuf::from(format!("{PROJECT_ROOT}/README.md"))]).unwrap();
        assert_eq!(zed.program, VM_ZED.unwrap_or(LAPTOP_ZED_CLI));
        let mut zed_args = vec!["--user-data-dir", "/private/actor/new-profile", PROJECT_ROOT, "/home/evilweasel/source/personal/jarvis/README.md"];
        if VM_ZED.is_none() {
            zed_args.splice(0..0, ["--zed", LAPTOP_ZED_APP, "--foreground", "--new"]);
        }
        assert_eq!(zed.args, zed_args);
        let chrome = command_plan(App::Chrome, private, vec![]).unwrap();
        assert_eq!(chrome.program, VM_CHROMIUM.unwrap_or(LAPTOP_CHROME));
        let mut chrome_args = vec!["--user-data-dir=/private/actor/new-profile", "--new-window", "--no-first-run", "--no-default-browser-check"];
        if VM_CHROMIUM.is_some() {
            chrome_args.push("--ozone-platform=wayland");
        }
        chrome_args.push("about:blank");
        assert_eq!(chrome.args, chrome_args);
        for args in [&zed.args, &chrome.args] {
            assert!(!args.iter().any(|a| a.contains("YouTube") || a.contains("Alarm") || a.contains("reserved") || a == "--reuse"));
        }
    }
    #[test]
    fn retargeted_file_precommit_rejects_stale_argv_without_spawning() {
        use std::{sync::{Arc, Barrier, atomic::{AtomicUsize, Ordering}}, thread};
        let root = std::env::temp_dir().join(format!("launch-retarget-{}", std::process::id()));
        fs::create_dir(&root).unwrap();
        fs::write(root.join("old.rs"), "first").unwrap();
        fs::write(root.join("new.rs"), "second").unwrap();
        std::os::unix::fs::symlink("old.rs", root.join("requested.rs")).unwrap();
        let profile = root.join("private-profile");
        let prepared = command_plan(App::Zed, &profile,
            vec![root.clone(), project_path(&root, Some("requested.rs")).unwrap()]).unwrap();
        let barrier = Arc::new(Barrier::new(2));
        let effects = Arc::new(AtomicUsize::new(0));
        let worker = {
            let root = root.clone();
            let profile = profile.clone();
            let barrier = barrier.clone();
            let effects = effects.clone();
            thread::spawn(move || {
                let mut receipt = Receipt::default();
                let result = dispatch(&mut receipt, || Ok(()), |gate, receipt| {
                    barrier.wait(); // prepared command, before the actual commit gate
                    barrier.wait();
                    gate()?;
                    let current = command_plan(App::Zed, &profile,
                        vec![root.clone(), project_path(&root, Some("requested.rs"))?])?;
                    prepared.require_same_target(&current)?;
                    effects.fetch_add(1, Ordering::SeqCst);
                    receipt.commit(123);
                    Ok(())
                });
                (result, receipt)
            })
        };
        barrier.wait();
        // A changed request can pass containment while the old argv filename
        // now escapes. The final gate must validate the command actually sent.
        fs::remove_file(root.join("requested.rs")).unwrap();
        std::os::unix::fs::symlink("new.rs", root.join("requested.rs")).unwrap();
        fs::remove_file(root.join("old.rs")).unwrap();
        std::os::unix::fs::symlink("/etc/passwd", root.join("old.rs")).unwrap();
        assert!(project_path(&root, Some("requested.rs")).is_ok());
        assert!(project_path(&root, Some("old.rs")).is_err());
        barrier.wait();
        let (result, receipt) = worker.join().unwrap();
        assert!(result.unwrap_err().contains("target changed"));
        assert_eq!(effects.load(Ordering::SeqCst), 0);
        assert!(!receipt.spawn_committed && !receipt.spawn_acknowledged);
        assert_eq!(fs::read_to_string(root.join("new.rs")).unwrap(), "second");
        fs::remove_dir_all(root).unwrap();
    }

}
