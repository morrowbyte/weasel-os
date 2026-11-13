//! Bounded, connection-local ownership of MCP request cancellation.
//! No timestamps, expiry, eviction, sockets, actor access or global cancellation.
use serde_json::Value;
use std::collections::HashMap;
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Arc,
};

const MAX_ID_BYTES: usize = 256;
const MAX_SEEN: usize = 65_536;
const MAX_UNKNOWN_CANCELS: usize = 1_024;
const MAX_PRIORITY_INFLIGHT: usize = 256;

#[derive(Clone, Debug, Eq, Hash, PartialEq)]
pub(crate) enum RequestKey {
    Number(i64),
    String(String),
}

impl RequestKey {
    pub(crate) fn parse(value: &Value) -> Result<Self, Refusal> {
        match value {
            Value::Number(n) => n.as_i64().map(Self::Number).ok_or(Refusal::InvalidId),
            Value::String(s) if s.len() <= MAX_ID_BYTES => Ok(Self::String(s.clone())),
            _ => Err(Refusal::InvalidId),
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum RequestKind {
    Act,
    Priority,
    Other,
}

impl RequestKind {
    pub(crate) fn from_request(request: &Value) -> Self {
        match request["method"].as_str() {
            Some("initialize" | "ping" | "tools/list") => Self::Priority,
            Some("tools/call") => match request["params"]["name"].as_str() {
                // Resume changes input permission, so it needs the same scoped
                // cancellation ownership as input. It must remain outside the
                // degraded priority reserve, including the internal wire name.
                Some("desktop_act" | "desktop_resume" | "desktop_resume_bound" | "desktop_set_observation_mode") => Self::Act,
                Some(
                    "desktop_status"
                    | "desktop_cancel"
                    | "desktop_takeover"
                    | "desktop_recover_release",
                ) => Self::Priority,
                _ => Self::Other,
            },
            _ => Self::Other,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum Refusal {
    InvalidId,
    Duplicate,
    CancelledBeforeDispatch,
    HistoryExhausted,
    PriorityInflightExhausted,
}

impl Refusal {
    pub(crate) fn code(self) -> i64 {
        match self {
            Self::InvalidId | Self::Duplicate => -32600,
            _ => -32603,
        }
    }
    pub(crate) fn message(self) -> &'static str {
        match self {
            Self::InvalidId => "MCP request ID must be an i64 integer or string of at most 256 UTF-8 bytes",
            Self::Duplicate => "MCP request ID already used in this connection; original cancellation ownership retained; use a fresh ID",
            Self::CancelledBeforeDispatch => "MCP request canceled before dispatch; no worker or desktop daemon request started",
            Self::HistoryExhausted => "MCP cancellation identity registry exhausted; only protocol metadata and desktop_status/cancel/takeover/recover_release remain available; all other requests refused until this MCP client reconnects; no desktop action started",
            Self::PriorityInflightExhausted => "MCP priority in-flight reserve exhausted; wait for existing priority calls to complete; no desktop action started",
        }
    }
}

enum Entry {
    PreCancelled,
    Running(Option<Arc<AtomicBool>>),
    Finished,
}

pub(crate) struct Registry {
    entries: HashMap<RequestKey, Entry>,
    // Only non-action maintenance/priority calls can enter this bounded reserve.
    // Terminal IDs here are not retained once history is exhausted. No Act or
    // Resume can ever be admitted again on this degraded connection.
    priority_inflight: HashMap<RequestKey, ()>,
    unknown_cancels: usize,
    degraded: bool,
    max_seen: usize,
    max_unknown: usize,
    max_priority: usize,
}

impl Registry {
    pub(crate) fn new() -> Self {
        Self::with_limits(MAX_SEEN, MAX_UNKNOWN_CANCELS, MAX_PRIORITY_INFLIGHT)
    }

    fn with_limits(max_seen: usize, max_unknown: usize, max_priority: usize) -> Self {
        Self {
            entries: HashMap::new(),
            priority_inflight: HashMap::new(),
            unknown_cancels: 0,
            degraded: false,
            max_seen,
            max_unknown,
            max_priority,
        }
    }

    // The caller holds its ONE connection-local mutex around this method.
    // A rejected request never invokes dispatch (which production uses only to
    // spawn its worker), so a pre-canceled Act cannot open a daemon connection.
    pub(crate) fn admit_then<T>(
        &mut self,
        key: RequestKey,
        kind: RequestKind,
        dispatch: impl FnOnce(Option<Arc<AtomicBool>>) -> T,
    ) -> Result<T, Refusal> {
        if let Some(entry) = self.entries.get_mut(&key) {
            return match entry {
                Entry::PreCancelled => {
                    *entry = Entry::Finished;
                    self.unknown_cancels -= 1;
                    Err(Refusal::CancelledBeforeDispatch)
                }
                Entry::Running(_) | Entry::Finished => Err(Refusal::Duplicate),
            };
        }
        if self.priority_inflight.contains_key(&key) {
            return Err(Refusal::Duplicate);
        }
        if self.degraded || self.entries.len() >= self.max_seen {
            self.degraded = true;
            if kind != RequestKind::Priority {
                return Err(Refusal::HistoryExhausted);
            }
            if self.priority_inflight.len() >= self.max_priority {
                return Err(Refusal::PriorityInflightExhausted);
            }
            self.priority_inflight.insert(key, ());
            return Ok(dispatch(None));
        }
        let flag = (kind == RequestKind::Act).then(|| Arc::new(AtomicBool::new(false)));
        self.entries.insert(key, Entry::Running(flag.clone()));
        Ok(dispatch(flag))
    }

    pub(crate) fn cancel(&mut self, key: RequestKey) {
        if let Some(entry) = self.entries.get(&key) {
            if let Entry::Running(Some(flag)) = entry {
                flag.store(true, Ordering::SeqCst);
            }
            // Late/completed, repeated unknown, and non-action cancellation
            // neither create a new tombstone nor affect any other request.
            return;
        }
        if self.priority_inflight.contains_key(&key) {
            return;
        }
        if self.degraded
            || self.entries.len() >= self.max_seen
            || self.unknown_cancels >= self.max_unknown
        {
            // Never evict old ownership/tombstones. Every future Act on this
            // connection is now refused, including this unretained canceled ID.
            self.degraded = true;
            return;
        }
        self.entries.insert(key, Entry::PreCancelled);
        self.unknown_cancels += 1;
    }

    pub(crate) fn finish(&mut self, key: &RequestKey) {
        if let Some(entry) = self.entries.get_mut(key) {
            if matches!(entry, Entry::Running(_)) {
                *entry = Entry::Finished;
            }
        }
        self.priority_inflight.remove(key);
    }

    pub(crate) fn cancel_owned_pending(&self) {
        for entry in self.entries.values() {
            if let Entry::Running(Some(flag)) = entry {
                flag.store(true, Ordering::SeqCst);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::cell::Cell;

    fn key(n: i64) -> RequestKey {
        RequestKey::Number(n)
    }
    fn act(registry: &mut Registry, n: i64) -> Arc<AtomicBool> {
        registry
            .admit_then(key(n), RequestKind::Act, |flag| flag.unwrap())
            .unwrap()
    }

    #[test]
    fn cancel_before_act_never_invokes_production_dispatch_gate() {
        let mut registry = Registry::new();
        registry.cancel(key(7));
        let dispatches = Cell::new(0);
        let result = registry.admit_then(key(7), RequestKind::Act, |_| dispatches.set(1));
        assert_eq!(result, Err(Refusal::CancelledBeforeDispatch));
        assert_eq!(dispatches.get(), 0);
        // The same ID can never later be revived on this connection.
        assert_eq!(
            registry.admit_then(key(7), RequestKind::Act, |_| ()),
            Err(Refusal::Duplicate)
        );
    }

    #[test]
    fn during_act_targets_exact_typed_id_and_preserves_other_pending() {
        let mut registry = Registry::new();
        let a = act(&mut registry, 7);
        let b = act(&mut registry, 8);
        let string = registry
            .admit_then(RequestKey::String("7".into()), RequestKind::Act, |flag| {
                flag.unwrap()
            })
            .unwrap();
        registry.cancel(key(7));
        assert!(a.load(Ordering::SeqCst));
        assert!(!b.load(Ordering::SeqCst));
        assert!(!string.load(Ordering::SeqCst));
    }

    #[test]
    fn late_completed_cancel_and_reuse_cannot_hit_other_work() {
        let mut registry = Registry::new();
        let a = act(&mut registry, 7);
        registry.finish(&key(7));
        let b = act(&mut registry, 8);
        registry.cancel(key(7));
        assert!(!a.load(Ordering::SeqCst));
        assert!(!b.load(Ordering::SeqCst));
        assert_eq!(
            registry.admit_then(key(7), RequestKind::Act, |_| ()),
            Err(Refusal::Duplicate)
        );
    }

    #[test]
    fn separate_connections_isolate_same_request_id() {
        let mut first = Registry::new();
        let mut second = Registry::new();
        first.cancel(key(7));
        let flag = act(&mut second, 7);
        assert!(!flag.load(Ordering::SeqCst));
        assert_eq!(
            first.admit_then(key(7), RequestKind::Act, |_| ()),
            Err(Refusal::CancelledBeforeDispatch)
        );
    }

    #[test]
    fn ids_are_bounded_integer_or_string_without_numeric_aliasing() {
        assert_eq!(RequestKey::parse(&json!(7)), Ok(key(7)));
        assert_eq!(RequestKey::parse(&json!(-7)), Ok(key(-7)));
        assert_ne!(RequestKey::parse(&json!(7)), RequestKey::parse(&json!("7")));
        assert!(RequestKey::parse(&json!("ä".repeat(128))).is_ok());
        for invalid in [
            json!(null),
            json!(true),
            json!(1.5),
            json!({}),
            json!([]),
            json!(u64::MAX),
            json!("ä".repeat(129)),
        ] {
            assert_eq!(RequestKey::parse(&invalid), Err(Refusal::InvalidId));
        }
    }

    #[test]
    fn history_cap_never_dispatches_new_act_and_keeps_priority() {
        let mut registry = Registry::with_limits(2, 2, 2);
        let pending = act(&mut registry, 1);
        registry
            .admit_then(key(2), RequestKind::Other, |_| ())
            .unwrap();
        registry.finish(&key(2));
        let effects = Cell::new(0);
        assert_eq!(
            registry.admit_then(key(3), RequestKind::Act, |_| effects.set(1)),
            Err(Refusal::HistoryExhausted)
        );
        assert_eq!(effects.get(), 0);
        assert!(!pending.load(Ordering::SeqCst));
        for name in ["desktop_status", "desktop_cancel", "desktop_takeover"] {
            let kind =
                RequestKind::from_request(&json!({"method":"tools/call","params":{"name":name}}));
            assert_eq!(kind, RequestKind::Priority);
            registry
                .admit_then(key(4), kind, |flag| assert!(flag.is_none()))
                .unwrap();
            registry.finish(&key(4));
        }
        assert_eq!(registry.entries.len(), 2);
        assert!(registry.priority_inflight.is_empty());
        assert_eq!(
            registry.admit_then(key(5), RequestKind::Other, |_| ()),
            Err(Refusal::HistoryExhausted)
        );
    }

    #[test]
    fn unknown_cap_does_not_evict_and_poison_never_recovers() {
        let mut registry = Registry::with_limits(8, 1, 2);
        let pending = act(&mut registry, 1);
        registry.cancel(key(2));
        registry.cancel(key(3));
        assert!(registry.degraded);
        assert_eq!(registry.entries.len(), 2);
        assert!(!pending.load(Ordering::SeqCst));
        assert_eq!(
            registry.admit_then(key(2), RequestKind::Act, |_| ()),
            Err(Refusal::CancelledBeforeDispatch)
        );
        for id in [3, 4] {
            assert_eq!(
                registry.admit_then(key(id), RequestKind::Act, |_| ()),
                Err(Refusal::HistoryExhausted)
            );
        }
        registry
            .admit_then(key(5), RequestKind::Priority, |_| ())
            .unwrap();
        registry.finish(&key(5));
        assert!(registry.degraded);
    }

    #[test]
    fn priority_reserve_is_bounded_and_duplicate_inflight_refused() {
        let mut registry = Registry::with_limits(1, 1, 1);
        registry.cancel(key(1));
        registry
            .admit_then(key(2), RequestKind::Priority, |_| ())
            .unwrap();
        assert_eq!(
            registry.admit_then(key(2), RequestKind::Priority, |_| ()),
            Err(Refusal::Duplicate)
        );
        assert_eq!(
            registry.admit_then(key(3), RequestKind::Priority, |_| ()),
            Err(Refusal::PriorityInflightExhausted)
        );
        registry.finish(&key(2));
        registry
            .admit_then(key(3), RequestKind::Priority, |_| ())
            .unwrap();
        assert_eq!(registry.priority_inflight.len(), 1);
    }

    #[test]
    fn eof_only_cancels_owned_pending_and_no_other_connection() {
        let mut first = Registry::new();
        let mut second = Registry::new();
        let a = act(&mut first, 1);
        let b = act(&mut second, 1);
        first
            .admit_then(
                key(2),
                RequestKind::Priority,
                |flag| assert!(flag.is_none()),
            )
            .unwrap();
        first.cancel_owned_pending();
        assert!(a.load(Ordering::SeqCst));
        assert!(!b.load(Ordering::SeqCst));
    }

    #[test]
    fn cancelled_read_only_id_is_remembered_and_not_reused_for_act() {
        let mut registry = Registry::new();
        registry.cancel(key(9));
        assert_eq!(
            registry.admit_then(key(9), RequestKind::Priority, |_| ()),
            Err(Refusal::CancelledBeforeDispatch)
        );
        assert_eq!(
            registry.admit_then(key(9), RequestKind::Act, |_| ()),
            Err(Refusal::Duplicate)
        );
    }

    #[test]
    fn immediate_worker_completion_shares_one_lock_without_deadlock() {
        use std::sync::Mutex;
        use std::thread;
        let registry = Arc::new(Mutex::new(Registry::new()));
        let worker_registry = registry.clone();
        let worker = registry
            .lock()
            .unwrap()
            .admit_then(key(1), RequestKind::Act, move |_| {
                thread::spawn(move || worker_registry.lock().unwrap().finish(&key(1)))
            })
            .unwrap();
        worker.join().unwrap();
        let mut calls = registry.lock().unwrap();
        calls.cancel(key(1));
        assert_eq!(
            calls.admit_then(key(1), RequestKind::Act, |_| ()),
            Err(Refusal::Duplicate)
        );
    }

    #[test]
    fn finish_cancel_race_cannot_remove_or_cancel_other_request() {
        use std::sync::{Barrier, Mutex};
        use std::thread;
        for _ in 0..32 {
            let registry = Arc::new(Mutex::new(Registry::new()));
            let other = act(&mut registry.lock().unwrap(), 2);
            let finished = act(&mut registry.lock().unwrap(), 1);
            let barrier = Arc::new(Barrier::new(2));
            let completion_registry = registry.clone();
            let completion_barrier = barrier.clone();
            let completion = thread::spawn(move || {
                completion_barrier.wait();
                completion_registry.lock().unwrap().finish(&key(1));
            });
            barrier.wait();
            registry.lock().unwrap().cancel(key(1));
            completion.join().unwrap();
            // Either order is valid: the finished flag can be true only if
            // Cancel won before Finish. Ownership of the other request is fixed.
            let _ = finished.load(Ordering::SeqCst);
            assert!(!other.load(Ordering::SeqCst));
            assert_eq!(
                registry
                    .lock()
                    .unwrap()
                    .admit_then(key(1), RequestKind::Act, |_| ()),
                Err(Refusal::Duplicate)
            );
        }
    }
    #[test]
    fn release_recovery_remains_priority_after_history_cap() {
        use serde_json::json;
        let req = json!({"method":"tools/call","params":{"name":"desktop_recover_release"}});
        assert_eq!(RequestKind::from_request(&req), RequestKind::Priority);
        let mut r = Registry::with_limits(0, 0, 1);
        let key = RequestKey::Number(42);
        let calls = std::cell::Cell::new(0);
        r.admit_then(key.clone(), RequestKind::from_request(&req), |flag| {
            assert!(flag.is_none());
            calls.set(calls.get() + 1)
        })
        .unwrap();
        assert_eq!(calls.get(), 1);
        r.finish(&key);
        assert!(r
            .admit_then(RequestKey::Number(43), RequestKind::Act, |_| panic!(
                "no Act dispatch at cap"
            ))
            .is_err());
    }

    #[test]
    fn independent_r8_resume_cancel_keeps_typed_ids_duplicates_and_late_isolation() {
        for name in ["desktop_resume", "desktop_resume_bound"] {
            let request = json!({"method":"tools/call","params":{"name":name}});
            let kind = RequestKind::from_request(&request);
            let mut registry = Registry::new();
            let numeric = registry
                .admit_then(key(70), kind, |flag| flag)
                .unwrap()
                .expect("Resume cancellation flag");
            let string_key = RequestKey::String("70".into());
            let string = registry
                .admit_then(string_key.clone(), kind, |flag| flag)
                .unwrap()
                .expect("Resume cancellation flag");
            registry.cancel(key(70));
            assert!(numeric.load(Ordering::SeqCst));
            assert!(!string.load(Ordering::SeqCst));
            registry.finish(&key(70));
            registry.cancel(key(70));
            assert!(!string.load(Ordering::SeqCst));
            assert_eq!(
                registry.admit_then(key(70), kind, |_| ()),
                Err(Refusal::Duplicate)
            );
            registry.cancel(string_key);
            assert!(string.load(Ordering::SeqCst));
        }
    }
    #[test]
    fn independent_r8_resume_never_enters_degraded_priority_reserve() {
        for name in ["desktop_resume", "desktop_resume_bound"] {
            let request = json!({"method":"tools/call","params":{"name":name}});
            let kind = RequestKind::from_request(&request);
            let mut registry = Registry::with_limits(1, 1, 1);
            registry.cancel(key(1));
            let dispatched = Cell::new(false);
            let result = registry.admit_then(key(2), kind, |_| dispatched.set(true));
            assert_eq!(result, Err(Refusal::HistoryExhausted));
            assert!(!dispatched.get());
            assert!(registry
                .admit_then(
                    key(3),
                    RequestKind::Priority,
                    |flag| assert!(flag.is_none())
                )
                .is_ok());
        }
    }
    #[test]
    fn independent_r8_eof_cancels_own_pending_resumes_without_other_connection() {
        for name in ["desktop_resume", "desktop_resume_bound"] {
            let request = json!({"method":"tools/call","params":{"name":name}});
            let kind = RequestKind::from_request(&request);
            let mut own = Registry::new();
            let mut other = Registry::new();
            let a = own
                .admit_then(key(1), kind, |flag| flag)
                .unwrap()
                .expect("Resume cancellation flag");
            let b = other
                .admit_then(key(1), kind, |flag| flag)
                .unwrap()
                .expect("Resume cancellation flag");
            own.cancel_owned_pending();
            assert!(a.load(Ordering::SeqCst));
            assert!(!b.load(Ordering::SeqCst));
        }
    }
}
