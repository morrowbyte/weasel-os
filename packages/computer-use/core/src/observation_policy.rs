//! The permission to acquire desktop content is distinct from active acquisition.
//! A read lease covers a complete request, including delivery of its response.
//! Mode changes take the write lease; callers invalidate retained content before
//! enabling again. A separate generation never cancels an indicator task.
use serde::{Deserialize, Serialize};
use std::sync::{
    atomic::{AtomicU64, AtomicUsize, Ordering},
    RwLock,
};

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub(crate) enum Mode {
    Enabled,
    Disabled,
}

pub(crate) struct Policy {
    pub(crate) gate: RwLock<()>,
    // One publication: upper bits are generation, lowest bit is permission.
    // Lifecycle reads must never attach a new generation to an old mode.
    state: AtomicU64,
    active: AtomicUsize,
}

pub(crate) struct Snapshot {
    pub(crate) mode: Mode,
    pub(crate) generation: u64,
    pub(crate) active: bool,
}

impl Default for Policy {
    fn default() -> Self {
        Self {
            gate: RwLock::new(()),
            state: AtomicU64::new(3), // generation 1, enabled
            active: AtomicUsize::new(0),
        }
    }
}

impl Policy {
    pub(crate) fn enabled(&self) -> bool {
        self.state.load(Ordering::SeqCst) & 1 != 0
    }
    pub(crate) fn mode(&self) -> Mode {
        if self.enabled() { Mode::Enabled } else { Mode::Disabled }
    }
    pub(crate) fn generation(&self) -> u64 {
        self.state.load(Ordering::SeqCst) >> 1
    }
    pub(crate) fn active(&self) -> bool {
        self.active.load(Ordering::SeqCst) != 0
    }
    pub(crate) fn snapshot(&self) -> Snapshot {
        self.snapshot_with(|| {})
    }
    fn snapshot_with(&self, mut after_state_read: impl FnMut()) -> Snapshot {
        loop {
            let state = self.state.load(Ordering::SeqCst);
            after_state_read();
            let active = self.active();
            if self.state.load(Ordering::SeqCst) == state {
                return Snapshot {
                    mode: if state & 1 != 0 { Mode::Enabled } else { Mode::Disabled },
                    generation: state >> 1,
                    active,
                };
            }
        }
    }
    pub(crate) fn require_enabled(&self) -> Result<(), String> {
        if self.enabled() {
            Ok(())
        } else {
            Err("Observation is disabled; desktop content, images and AT-SPI are unavailable. Only explicitly bounded blind actions are permitted.".into())
        }
    }
    // Caller owns gate.write(). Disable first, purge, then enable if requested.
    pub(crate) fn disable_and_invalidate(&self) {
        self.state.store((self.generation() + 1) << 1, Ordering::SeqCst);
    }
    pub(crate) fn enable(&self) {
        // The transient disabled state during purge must have an older
        // generation than the published enabled state, even on purge failure.
        self.state.store(((self.generation() + 1) << 1) | 1, Ordering::SeqCst);
    }
    // Caller owns a request read lease. Scope this only around real acquisition,
    // never reasoning, image delivery, queueing or an input-only action.
    pub(crate) fn acquiring(&self) -> Result<Acquisition<'_>, String> {
        self.require_enabled()?;
        self.active.fetch_add(1, Ordering::SeqCst);
        Ok(Acquisition(self))
    }
}

pub(crate) struct Acquisition<'a>(&'a Policy);
impl Drop for Acquisition<'_> {
    fn drop(&mut self) {
        self.0.active.fetch_sub(1, Ordering::SeqCst);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn enabled_is_permission_and_active_counts_only_acquisition() {
        let policy = Policy::default();
        assert_eq!(policy.mode(), Mode::Enabled);
        assert!(!policy.active());
        let one = policy.acquiring().unwrap();
        let two = policy.acquiring().unwrap();
        drop(one);
        assert!(policy.active());
        drop(two);
        assert!(!policy.active());
        let _write = policy.gate.write().unwrap();
        policy.disable_and_invalidate();
        assert_eq!(policy.generation(), 2);
        assert!(policy.acquiring().is_err());
        assert!(!policy.active());
    }
    #[test]
    fn delivery_lease_prevents_transition_until_old_response_is_finished() {
        let policy = Policy::default();
        let delivery = policy.gate.read().unwrap();
        assert!(policy.gate.try_write().is_err());
        drop(delivery);
        let _transition = policy.gate.try_write().unwrap();
        policy.disable_and_invalidate();
        assert_eq!(policy.mode(), Mode::Disabled);
        assert!(!policy.active());
    }
    #[test]
    fn lifecycle_snapshot_retries_a_disable_enable_interleaving() {
        let policy = Policy::default();
        policy.disable_and_invalidate();
        let old = policy.snapshot();
        assert_eq!((old.mode, old.generation, old.active), (Mode::Disabled, 2, false));
        let mut first_read = true;
        let mut acquisition = None;
        let snapshot = policy.snapshot_with(|| {
            if first_read {
                first_read = false;
                policy.disable_and_invalidate();
                policy.enable();
                acquisition = Some(policy.acquiring().unwrap());
            }
        });
        assert_eq!((snapshot.mode, snapshot.generation, snapshot.active), (Mode::Enabled, 4, true));
        assert!(old.generation < snapshot.generation);
        drop(acquisition);
    }
}
