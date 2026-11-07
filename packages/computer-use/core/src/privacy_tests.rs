//! Offline unit/Unix-socket transport tests; no real compositor/input/content.
use super::*;
use super::release_recovery_tests::{cooperative_fixture, obs};

const SECRET: &str = "SYNTHETIC_PRIVATE_CANARY_74e5";
fn data(result: &Value) -> Value {
    serde_json::from_str(result["content"][0]["text"].as_str().unwrap()).unwrap()
}
fn scope(state: &State, task: &str) {
    state.task_scopes.lock().unwrap().insert(task.into(), TaskScope {
        expires: Instant::now() + Duration::from_secs(60), owner_processes: None,
        epoch: state.epoch.load(Ordering::SeqCst), scope_token: Some("owned".into()), indicator_socket: None,
    });
}
fn disable(state: &State) {
    handle(state, "desktop_set_observation_mode", &json!({"mode":"disabled"}), 77).unwrap();
}

#[test]
fn disabled_purges_retained_content_and_preserves_task_epoch() {
    let (state, dir) = cooperative_fixture("privacy-purge");
    scope(&state, "live-task");
    let path = dir.join("obs-secret.png");
    fs::write(&path, SECRET).unwrap();
    let mut old = obs(&dir);
    old.image = path.clone();
    old.view_image = path.clone();
    state.observations.lock().unwrap().insert(old.id.clone(), old.clone());
    *state.last_result.lock().unwrap() = json!({"after_observation":{"title":SECRET},"content":[{"type":"image","data":SECRET}]});
    disable(&state);
    assert!(!path.exists());
    assert!(state.observations.lock().unwrap().is_empty());
    assert!(state.semantic_targets.lock().unwrap().is_empty());
    assert!(state.last_result.lock().unwrap().is_null());
    assert_eq!(state.epoch.load(Ordering::SeqCst), 77);
    assert!(state.task_scopes.lock().unwrap().contains_key("live-task"));
    assert_eq!(state.observation_policy.generation(), 2);
    assert!(validate_retained_observation(&state, &old, true).is_err());
    for tool in ["desktop_status", "desktop_indicator_lifecycle"] {
        let result = handle(&state, tool, &json!({}), 77).unwrap();
        assert!(!result.to_string().contains(SECRET));
        assert_eq!(data(&result)["observation_mode"], "disabled");
        assert_eq!(data(&result)["observation_active"], false);
    }
    // An interrupted purge can be retried even if mode is already disabled.
    fs::write(&path, SECRET).unwrap();
    disable(&state);
    assert!(!path.exists());
    assert_eq!(state.observation_policy.generation(), 2);
    handle(&state, "desktop_set_observation_mode", &json!({"mode":"enabled"}), 77).unwrap();
    assert_eq!(state.observation_policy.generation(), 4);
    assert!(validate_retained_observation(&state, &old, true).is_err());
    assert!(!state.capture_available.load(Ordering::SeqCst));
    fs::remove_dir_all(dir).unwrap();
}

#[test]
fn disabled_blocks_all_content_entries_before_any_capture_or_image_read() {
    let (state, dir) = cooperative_fixture("privacy-entries");
    disable(&state);
    for (tool, args) in [
        ("desktop_observe", json!({"include_image":false,"crop":{"x":0,"y":0,"width":1,"height":1}})),
        ("desktop_windows", json!({})),
        ("desktop_semantic", json!({"window_id":42,"query":SECRET})),
        ("desktop_semantic_direct", json!({"window_id":42,"include_text_selection":true,"background":true})),
    ] {
        let error = handle(&state, tool, &args, 77).unwrap_err();
        assert!(error.contains("disabled"), "{tool}: {error}");
        assert!(!error.contains(SECRET));
    }
    let o = obs(&dir);
    assert!(capture(&state, "never", &dir.join("obs-never.png"), "never", Duration::from_millis(1), 77).is_err());
    assert!(reference_pixels(&state, &o.id, &o.image, 77, 0).is_err());
    assert!(atspi_helper(&state, json!({"operation":"observe"}), 77).is_err());
    let mut captured = false;
    assert!(visual_guard_with_capture(&state, &o, &Action::Click { x: 0.0, y: 0.0, button: None, count: None }, 77, |_| { captured = true; Ok(()) }).is_err());
    assert!(!captured);
    assert_eq!(state.capture_attempts.load(Ordering::SeqCst), 0);
    assert!(state.actor.lock().unwrap().is_none());
    fs::remove_dir_all(dir).unwrap();
}

#[test]
fn private_batch_whitelist_and_default_post_observe_fail_before_effects() {
    let (state, dir) = cooperative_fixture("privacy-whitelist");
    disable(&state);
    let allowed = json!({"observation_id":"unused","actions":[{"kind":"key","keys":["ctrl","l"]},{"kind":"type","text":"https://example.invalid/","text_method":"keyboard"},{"kind":"key","keys":["Return"]}],"observe_after":false,"include_image":false});
    preflight_blind_batch(&serde_json::from_value(allowed.clone()).unwrap()).unwrap();
    for action in [
        json!({"kind":"type","text":SECRET}),
        json!({"kind":"type","text":SECRET,"text_method":"clipboard"}),
        json!({"kind":"paste","text":SECRET,"restore_clipboard":false}),
        json!({"kind":"key","keys":["ctrl","c"]}),
        json!({"kind":"key","keys":["ctrl","x"]}),
        json!({"kind":"key","keys":["ctrl","v"]}),
        json!({"kind":"key","keys":["shift","Insert"]}),
        json!({"kind":"click","x":1,"y":1}),
        json!({"kind":"semantic_click","handle_id":SECRET,"action_name":"click"}),
        json!({"kind":"wait","ms":251}),
    ] {
        let mut args = allowed.clone();
        args["actions"] = json!([{"kind":"type","text":"earlier","text_method":"keyboard"},action]);
        let error = handle(&state, "desktop_act", &args, 77).unwrap_err();
        assert!(error.contains("Observation disabled"), "{error}");
        assert!(!error.contains(SECRET));
    }
    let mut defaults = allowed;
    defaults.as_object_mut().unwrap().remove("observe_after");
    defaults.as_object_mut().unwrap().remove("include_image");
    assert!(handle(&state, "desktop_act", &defaults, 77).unwrap_err().contains("observe_after=false"));
    assert_eq!(state.queued.load(Ordering::SeqCst), 0);
    assert_eq!(state.capture_attempts.load(Ordering::SeqCst), 0);
    assert!(state.keyboard.lock().unwrap().is_none());
    fs::remove_dir_all(dir).unwrap();
}

#[test]
fn private_target_cannot_move_to_a_new_task_or_ignore_release_gates() {
    let (state, dir) = cooperative_fixture("privacy-task-binding");
    disable(&state);
    let mut old = obs(&dir);
    old.blind = true;
    old.blind_task = Some("task-a".into());
    old.observation_generation = state.observation_policy.generation();
    state.observations.lock().unwrap().insert(old.id.clone(), old.clone());
    scope(&state, "task-b");
    let args = json!({"observation_id":old.id,"task_id":"task-b","actions":[{"kind":"wait","ms":0}],"observe_after":false,"include_image":false});
    assert!(handle(&state,"desktop_act",&args,77).unwrap_err().contains("different task"));
    state.release_confirmed.store(false, Ordering::SeqCst);
    assert!(handle(&state,"desktop_act",&args,77).unwrap_err().contains("release/receipt"));
    assert_eq!(state.queued.load(Ordering::SeqCst), 0);
    fs::remove_dir_all(dir).unwrap();
}

#[test]
fn structural_decoders_discard_titles_names_and_unknown_content() {
    let windows = json!([{"id":1,"pid":42,"app_id":"editor","workspace_id":2,"is_focused":true,"is_floating":false,"title":SECRET,"unknown":{"text":SECRET}}]);
    let parsed: Vec<StructuralWindow> = serde_json::from_reader(windows.to_string().as_bytes()).unwrap();
    assert!(!serde_json::to_string(&parsed).unwrap().contains(SECRET));
    let workspaces = json!([{"id":2,"output":"DP-6","is_active":true,"is_focused":true,"active_window_id":1,"name":SECRET}]);
    let parsed: Vec<StructuralWorkspace> = serde_json::from_reader(workspaces.to_string().as_bytes()).unwrap();
    assert!(!serde_json::to_string(&parsed).unwrap().contains(SECRET));
}

#[test]
fn lifecycle_reports_real_acquisition_without_conflating_permission() {
    let (state, dir) = cooperative_fixture("privacy-active");
    assert_eq!(data(&handle(&state,"desktop_indicator_lifecycle",&json!({}),77).unwrap())["observation_active"], false);
    let acquisition = state.observation_policy.acquiring().unwrap();
    let status = data(&handle(&state,"desktop_indicator_lifecycle",&json!({}),77).unwrap());
    assert_eq!(status["observation_mode"], "enabled");
    assert_eq!(status["observation_active"], true);
    drop(acquisition);
    assert_eq!(data(&handle(&state,"desktop_indicator_lifecycle",&json!({}),77).unwrap())["observation_active"], false);
    fs::remove_dir_all(dir).unwrap();
}

#[test]
fn proxy_retains_policy_lease_until_public_output_is_finished() {
    let (state, dir) = cooperative_fixture("privacy-delivery");
    let socket = dir.join("fenced.sock");
    let listener = UnixListener::bind(&socket).unwrap();
    let remote = state.clone();
    let server = thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        stream.set_read_timeout(Some(Duration::from_secs(2))).unwrap();
        let mut request = String::new();
        BufReader::new(stream.try_clone().unwrap()).read_line(&mut request).unwrap();
        assert_eq!(serde_json::from_str::<Value>(&request).unwrap()["response_fence"], true);
        let _lease = remote.observation_policy.gate.read().unwrap();
        let mut response = text_result(json!({"title":SECRET}));
        response["_observation_delivery_fence"] = json!(1);
        writeln!(stream,"{response}").unwrap();
        let mut ack = [0;10];
        stream.read_exact(&mut ack).unwrap();
        assert_eq!(&ack,b"delivered\n");
    });
    let delivery = ProxyDeliveryGuard::start();
    let response = daemon_call_transport(&socket,"desktop_observe",&json!({}),None,Duration::from_secs(2)).unwrap();
    assert!(response.to_string().contains(SECRET));
    assert!(response.get("_observation_delivery_fence").is_none());
    assert!(state.observation_policy.gate.try_write().is_err(),"transition must wait for buffered proxy content");
    // Represents successful public flush or terminal unbuffered-output failure.
    drop(response);
    drop(delivery);
    server.join().unwrap();
    disable(&state);
    assert!(!data(&handle(&state,"desktop_status",&json!({}),77).unwrap()).to_string().contains(SECRET));
    fs::remove_dir_all(dir).unwrap();
}

#[test]
fn public_output_failure_is_terminal_and_cannot_flush_old_content_later() {
    struct FailsOnFlush { writes: usize }
    impl Write for FailsOnFlush {
        fn write(&mut self, bytes: &[u8]) -> io::Result<usize> { self.writes += 1; Ok(bytes.len()) }
        fn flush(&mut self) -> io::Result<()> { Err(io::Error::other("synthetic flush failure")) }
    }
    let mut out = PublicOutput { writer: FailsOnFlush { writes: 0 }, failed: false };
    assert!(out.send(&json!({"private":SECRET})).is_err());
    let before = out.writer.writes;
    assert!(out.send(&json!({"public":"later"})).is_err());
    assert_eq!(out.writer.writes,before);
}

#[test]
fn old_unfenced_proxy_content_is_withheld_even_after_reconnect() {
    let (_, dir) = cooperative_fixture("privacy-old-proxy");
    let socket = dir.join("old.sock");
    let listener = UnixListener::bind(&socket).unwrap();
    let server = thread::spawn(move || {
        for _ in 0..2 {
            let (mut stream, _) = listener.accept().unwrap();
            let mut line = String::new();
            BufReader::new(stream.try_clone().unwrap()).read_line(&mut line).unwrap();
            writeln!(stream,"{}",text_result(json!({"after_observation":{"title":SECRET}}))).unwrap();
        }
    });
    for _ in 0..2 {
        let _delivery = ProxyDeliveryGuard::start();
        let error = daemon_call_transport(&socket,"desktop_observe",&json!({}),None,Duration::from_secs(2)).unwrap_err();
        assert!(error.contains("delivery fence"));
        assert!(!error.contains(SECRET));
    }
    server.join().unwrap();
    fs::remove_dir_all(dir).unwrap();
}

#[test]
fn realistic_three_action_batch_saves_two_socket_roundtrips() {
    // Transport measurement only: exact same ctrl+l/type/Return sequence; no
    // physical input or latency claim. Server verifies request schemas, records
    // ordered actions and acknowledges one completed RPC per connection.
    let (state, dir) = cooperative_fixture("privacy-batch-transport");
    let socket = dir.join("roundtrips.sock");
    let listener = UnixListener::bind(&socket).unwrap();
    let server = thread::spawn(move || {
        let mut traces = Vec::new();
        for _ in 0..4 {
            let (mut stream,_) = listener.accept().unwrap();
            let mut line = String::new();
            BufReader::new(stream.try_clone().unwrap()).read_line(&mut line).unwrap();
            let request: Value = serde_json::from_str(&line).unwrap();
            preflight_blind_batch(&serde_json::from_value(request["arguments"].clone()).unwrap()).unwrap();
            traces.push(request["arguments"]["actions"].as_array().unwrap().clone());
            writeln!(stream,"{}",text_result(json!({"status":"transport_ack"}))).unwrap();
        }
        traces
    });
    let actions = vec![json!({"kind":"key","keys":["ctrl","l"]}),json!({"kind":"type","text":"https://example.invalid/","text_method":"keyboard"}),json!({"kind":"key","keys":["Return"]})];
    let call = |actions: Vec<Value>| daemon_call_owned(&socket,"desktop_act",&json!({"observation_id":"structural-fixture","task_id":"fixture-task","actions":actions,"observe_after":false,"include_image":false}),None).unwrap();
    for action in &actions { call(vec![action.clone()]); }
    call(actions.clone());
    let traces = server.join().unwrap();
    assert_eq!(traces[..3].concat(),actions);
    assert_eq!(traces[3],actions);
    println!("Transport evidence: identical ctrl+l/type/Return; single RPCs=3, batch RPCs=1, saved roundtrips=2; no GUI latency measured");
    drop(state);
    fs::remove_dir_all(dir).unwrap();
}
