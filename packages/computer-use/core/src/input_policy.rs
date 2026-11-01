//! Cooperative desktop input: ordinary physical activity invalidates targets,
//! while a physical Alt+Escape chord cancels the current Computer Use task.
//! No raw key, text, pointer position or button values are retained here.
use super::*;

pub(super) const REVISION: u32 = 1;
thread_local! {
    static EXPECTED_GENERATION: RefCell<Option<u64>> = const { RefCell::new(None) };
}
pub(super) struct Guard(Option<u64>);
impl Guard {
    pub fn bind(generation: u64) -> Self {
        Self(EXPECTED_GENERATION.with(|g| g.replace(Some(generation))))
    }
    // Direct object calls do not enter the shared keyboard/pointer input stream.
    // Cancellation still flows through the request and task epoch checks.
    pub fn background() -> Self {
        Self(EXPECTED_GENERATION.with(|g| g.replace(None)))
    }
}
impl Drop for Guard {
    fn drop(&mut self) {
        EXPECTED_GENERATION.with(|g| *g.borrow_mut() = self.0);
    }
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub(super) enum Source {
    Physical,
    ControlledSimulation,
    Synthetic,
}
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub(super) enum Event {
    Ignore,
    Activity,
    Escape,
    Resync,
}
pub(super) fn classify(kind: u16, code: u16, value: i32, source: Source) -> Event {
    if source == Source::Synthetic {
        return Event::Ignore;
    }
    if kind == 0 && code == 3 {
        return Event::Resync; // EV_SYN/SYN_DROPPED: never infer quiet input.
    }
    // Linux evdev KEY_ESC=1; only a new physical press requests abort. Release
    // and repeat remain ordinary activity, without storing key identity.
    if kind == 1 && code == 1 && value == 1 {
        Event::Escape
    } else if (kind == 1 && (0..=2).contains(&value)) || (kind == 2 && value != 0) || kind == 3 {
        Event::Activity
    } else {
        Event::Ignore
    }
}

pub(super) struct Policy {
    generation: AtomicU64,
    held_controls: AtomicUsize,
    held_state_known: AtomicBool,
}
impl Policy {
    pub fn new() -> Self {
        Self {
            generation: AtomicU64::new(0),
            held_controls: AtomicUsize::new(0),
            held_state_known: AtomicBool::new(false),
        }
    }
    #[cfg(test)]
    pub fn test_ready() -> Self {
        Self {
            generation: AtomicU64::new(0),
            held_controls: AtomicUsize::new(0),
            held_state_known: AtomicBool::new(true),
        }
    }
    pub fn generation(&self) -> u64 {
        self.generation.load(Ordering::SeqCst)
    }
    pub fn activity(&self) -> u64 {
        self.generation.fetch_add(1, Ordering::SeqCst) + 1
    }
    pub fn matches(&self, expected: u64) -> bool {
        self.generation() == expected
    }
    pub fn update_holds(&self, count: usize, known: bool) {
        if self.held_state_known.load(Ordering::SeqCst) == known
            && self.held_controls.load(Ordering::SeqCst) == count
        {
            return;
        }
        // During an update/read failure refuse dispatch before updating counts.
        // Any capability/held-state transition invalidates old observations.
        let previous_known = self.held_state_known.swap(false, Ordering::SeqCst);
        let previous_count = self.held_controls.swap(count, Ordering::SeqCst);
        if previous_known != known || previous_count != count {
            self.activity();
        }
        self.held_state_known.store(known, Ordering::SeqCst);
    }
    pub fn controls_released(&self) -> bool {
        self.held_state_known.load(Ordering::SeqCst)
            && self.held_controls.load(Ordering::SeqCst) == 0
    }
    pub fn check(&self, expected: u64) -> R<()> {
        if self.matches(expected) && self.controls_released() && self.matches(expected) {
            Ok(())
        } else {
            Err("Ordinary physical input changed the desktop; current batch stopped without takeover. Input generation must match and physical controls must be released with known monitor state. Inspect completed/possible effects, wait for release, observe again and deliberately continue; do not replay blindly.".into())
        }
    }
    pub fn check_bound(&self) -> R<()> {
        EXPECTED_GENERATION.with(|g| match *g.borrow() {
            Some(expected) => self.check(expected),
            None => Ok(()),
        })
    }
    pub fn status(&self, last_activity_ms: u64) -> Value {
        json!({"revision":REVISION,"mode":"cooperative_alt_escape_only","abort_shortcut":"Alt+Escape","generation":self.generation(),"last_activity_monotonic_ms":last_activity_ms,"held_controls":self.held_controls.load(Ordering::SeqCst),"held_state_known":self.held_state_known.load(Ordering::SeqCst),"escape_aborts":true,"escape_policy":"cancel_current_task","idle_escape_latches":false,"persistent_escape_stop":false,"ordinary_activity_latches":false,"automatic_replay":false,"recovery":"ordinary activity or held controls require release, known input monitor state, fresh observation and deliberate continuation; no desktop_resume needed","collision_limit":"evdev observation and input dispatch cannot be atomic; overlapping dispatched input may have partial effects"})
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn ordinary_activity_invalidates_only_bound_targets_and_recovers_with_fresh_generation() {
        let policy = Policy::new();
        policy.update_holds(0, true);
        let prior = policy.generation();
        assert!(policy.check_bound().is_ok());
        policy.activity();
        assert!(policy.check_bound().is_ok(), "idle work is not canceled");
        assert!(policy.check(prior).is_err(), "old observation is stale");
        {
            let _guard = Guard::bind(policy.generation());
            assert!(policy.check_bound().is_ok());
            policy.activity();
            assert!(policy.check_bound().is_err(), "active batch stops");
        }
        let _fresh = Guard::bind(policy.generation());
        assert!(policy.check_bound().is_ok(), "fresh work can continue");
    }
    #[test]
    fn only_escape_press_requests_abort_and_agent_synthetic_escape_is_excluded() {
        assert_eq!(classify(1, 1, 1, Source::Physical), Event::Escape);
        assert_eq!(classify(1, 1, 0, Source::Physical), Event::Activity);
        assert_eq!(classify(1, 1, 2, Source::Physical), Event::Activity);
        assert_eq!(classify(1, 30, 1, Source::Physical), Event::Activity);
        assert_eq!(classify(2, 0, 20, Source::Physical), Event::Activity);
        assert_eq!(classify(0, 0, 0, Source::Physical), Event::Ignore);
        assert_eq!(classify(0, 3, 0, Source::Physical), Event::Resync);
        assert_eq!(classify(1, 1, 1, Source::Synthetic), Event::Ignore);
        assert_eq!(classify(2, 0, 20, Source::Synthetic), Event::Ignore);
        assert_eq!(
            classify(1, 1, 1, Source::ControlledSimulation),
            Event::Escape
        );
    }
    #[test]
    fn held_modifiers_or_pointer_buttons_refuse_dispatch_until_authoritative_release() {
        let policy = Policy::new();
        assert!(!policy.controls_released(), "startup state is unknown");
        policy.update_holds(0, true);
        for count in [1, 2] {
            let before = policy.generation();
            policy.update_holds(count, true);
            assert!(policy.check(before).is_err());
            assert!(
                policy.check(policy.generation()).is_err(),
                "fresh image does not release a held control"
            );
            let held_generation = policy.generation();
            // Same authoritative snapshot after queued press events must not
            // count the press twice or continually invalidate quiet captures.
            policy.update_holds(count, true);
            assert_eq!(policy.generation(), held_generation);
            assert_eq!(policy.status(0)["held_controls"], count);
            policy.update_holds(0, true);
            assert!(policy.check(held_generation).is_err());
            assert!(policy.check(policy.generation()).is_ok());
        }
    }
    #[test]
    fn unreadable_or_hotplug_state_refuses_temporarily_and_resynchronizes_without_latch() {
        let policy = Policy::test_ready();
        let before = policy.generation();
        policy.update_holds(0, false);
        assert!(policy.check(before).is_err());
        assert!(policy.check(policy.generation()).is_err());
        assert_eq!(policy.status(0)["held_state_known"], false);
        policy.update_holds(1, true);
        assert!(policy.check(policy.generation()).is_err());
        policy.update_holds(0, true);
        assert!(policy.check(policy.generation()).is_ok());
        assert_eq!(policy.status(0)["ordinary_activity_latches"], false);
    }
}
