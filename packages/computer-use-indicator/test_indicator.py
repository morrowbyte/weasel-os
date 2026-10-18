"""Lifecycle contract tests with fake presentation and read-only actor fixtures.

These establish helper lifetime/cancellation behavior, never live Niri rendering.
"""

import copy
import importlib.util
import json
import os
import signal
from pathlib import Path
import select
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

SOURCE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("indicator", SOURCE / "indicator.py")
indicator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(indicator)


class StatusDeadline(unittest.TestCase):
    def test_fragmented_response_has_total_deadline(self):
        reader, writer = socket.socketpair()
        reader.settimeout(0.12)

        def fragment():
            try:
                for byte in b'{"ok":true}\n':
                    writer.send(bytes([byte]))
                    time.sleep(0.035)
            except OSError:
                pass
            finally:
                writer.close()

        sender = threading.Thread(target=fragment)
        sender.start()
        started = time.monotonic()
        try:
            with self.assertRaises((TimeoutError, indicator.CapabilityError)):
                indicator.read_line(reader, 4096)
            self.assertLess(time.monotonic() - started, 0.25)
        finally:
            reader.close()
            sender.join(timeout=1)


class IndicatorLifecycle(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = Path(self.temp.name)
        self.actor_dir = self.runtime / "weasel-computer-use"
        self.actor_dir.mkdir(mode=0o700)
        self.actor_path = self.actor_dir / "desktop.sock"
        self.actor_state = {
            "schema": 1, "session_id": "fixture-session", "epoch": 2,
            "takeover_latched": False, "active": None,
            "queued_batches": 0, "actor_release_confirmed": True,
            "task_scope_active": False,
        }
        self.task_scopes = {}
        self.actor_events = []
        self.drop_begin_ack_once = False
        self.supports_owner_lifetime = True
        self.actor_lock = threading.Lock()
        self.receipt_runs = []
        self.control_requests = []
        self.actor_plan = {}
        self.actor_requests = []
        self.actor_errors = []
        self.actor_handlers = []
        self.renderer_log = self.runtime / "renderer-protocol.jsonl"
        self.niri_state_path = self.runtime / "outputs.json"
        self.niri_state = {"DP-6": {"logical": {"x": 1920, "y": 0, "width": 3440, "height": 1440, "scale": 1}}}
        self.niri_state_path.write_text(json.dumps(self.niri_state))
        self.render_started = self.runtime / "renderer-started"
        self.renderer = self.runtime / "renderer.py"
        self.renderer.write_text(
            f"#!{sys.executable}\n"
            "import json, os, pathlib, sys, time\n"
            f"pathlib.Path({str(self.render_started)!r}).write_text(str(os.getpid()))\n"
            "for line in sys.stdin:\n"
            f" with pathlib.Path({str(self.renderer_log)!r}).open('a') as log: log.write(json.dumps({{'at':time.monotonic(),'line':line.strip()}})+'\\n')\n"
            " if line == 'v1 begin\\n':\n"
            "  print(json.dumps({'revision':1,'event':'ready','task_id':sys.argv[2],'output':sys.argv[1],'surfaces':5}),flush=True)\n"
            " elif line == 'v1 end\\n': break\n"
        )
        self.renderer.chmod(0o700)
        self.niri = self.runtime / "niri.py"
        self.niri.write_text(f"#!{sys.executable}\nfrom pathlib import Path\nprint(Path({str(self.niri_state_path)!r}).read_text())\n")
        self.niri.chmod(0o700)
        self.env = {
            **os.environ, "XDG_RUNTIME_DIR": str(self.runtime),
            "WEASEL_COMPUTER_USE_SOCKET": str(self.actor_path),
            "WEASEL_INDICATOR_NIRI": str(self.niri),
            "WEASEL_INDICATOR_RENDERER": str(self.renderer),
        }
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(str(self.actor_path))
        self.server.listen(4)
        self.server.settimeout(0.05)
        self.server_stop = threading.Event()
        self.actor_thread = threading.Thread(target=self.serve_actor, daemon=True)
        self.actor_thread.start()
        self.processes = []

    def serve_actor(self):
        while not self.server_stop.is_set():
            try:
                client, _ = self.server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            handler = threading.Thread(target=self.reply_actor, args=(client,), daemon=True)
            self.actor_handlers.append(handler)
            handler.start()

    def reply_actor(self, client):
        with client:
            client.settimeout(0.2)
            try:
                raw = client.recv(4096)
                if not raw:
                    return  # own signal/end may close a connected probe before send
                request = json.loads(raw)
                if request["tool"] != "desktop_indicator_lifecycle":
                    raise AssertionError("unexpected tool instead of lightweight lifecycle")
                event = request.get("arguments", {}).get("event", "status")
                arguments = request.get("arguments", {})
                task_id = arguments.get("task_id")
                with self.actor_lock:
                    index = len(self.actor_requests)
                    record = {"index":index,"at":time.monotonic(),"tool":request["tool"]}
                    if event == "status":
                        self.actor_requests.append(record)
                    plan = dict(self.actor_plan.get(index, {})) if event == "status" else {}
                    state = copy.deepcopy(self.actor_state)
                    self.actor_events.append(copy.deepcopy(request))
                    is_error = False
                    if event == "begin":
                        self.task_scopes[task_id] = {
                            "scope_token": arguments.get("scope_token"),
                            "session_id": arguments.get("session_id"),
                            "expected_epoch": arguments.get("expected_epoch"),
                        }
                    elif event == "end":
                        owner = self.task_scopes.get(task_id)
                        binding = {key: arguments.get(key) for key in ("scope_token", "session_id", "expected_epoch")}
                        if owner is None:
                            pass
                        elif owner == binding:
                            self.task_scopes.pop(task_id, None)
                        else:
                            is_error = True
                    owner = self.task_scopes.get(task_id)
                    state["task_scope_active"] = owner == {
                        "scope_token": arguments.get("scope_token"),
                        "session_id": arguments.get("session_id"),
                        "expected_epoch": arguments.get("expected_epoch"),
                    }
                    if owner is not None and self.supports_owner_lifetime:
                        state["task_scope_lifetime"] = "owner_process"
                    drop_begin_ack = event == "begin" and self.drop_begin_ack_once
                    if drop_begin_ack:
                        self.drop_begin_ack_once = False
                if drop_begin_ack:
                    return  # actor committed begin; response was lost
                if plan.get("eof"):
                    return
                if plan.get("delay"):
                    self.server_stop.wait(plan["delay"])
                state["actor_active"] = state.pop("active") is not None
                state["lifecycle_revision"] = 3
                state["complete"] = True
                state.update(plan.get("status", {}))
                response = {"content": [{"type": "text", "text": json.dumps(state)}], "isError": is_error}
                response.update(plan.get("envelope", {}))
                packet = plan.get("raw", json.dumps(response).encode() + b"\n")
                fragments = plan.get("fragments", 1)
                width = max(1, len(packet) // fragments)
                for offset in range(0, len(packet), width):
                    client.sendall(packet[offset:offset+width])
                    if offset + width < len(packet):
                        self.server_stop.wait(plan.get("fragment_delay", 0))
                record["replied_at"] = time.monotonic()
            except OSError:
                pass  # fixture client cancellation may close an owned read
            except Exception as error:
                self.actor_errors.append(repr(error))

    def tearDown(self):
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=2)
            process.stdout.close()
            process.stderr.close()
        self.server_stop.set()
        self.server.close()
        self.actor_thread.join(timeout=1)
        for handler in self.actor_handlers:
            handler.join(timeout=0.3)
        self.assertEqual(self.actor_errors, [])
        receipt_dir = os.environ.get("WEASEL_INDICATOR_TEST_RECEIPTS_DIR")
        if receipt_dir:
            Path(receipt_dir).mkdir(mode=0o700, parents=True, exist_ok=True)
            (Path(receipt_dir) / (self.id().split(".")[-1] + ".json")).write_text(json.dumps({"test":self.id(), "scope":"private fake actor/Niri/renderer only; no desktop/input", "runs":self.receipt_runs, "control_requests":self.control_requests}, indent=2) + "\n")
        self.temp.cleanup()

    def launch(self, task="fixture-task", extra=()):
        process = subprocess.Popen(
            [sys.executable, str(SOURCE / "indicator.py"), "run", "--task-id", task,
             "--output", "DP-6", *extra],
            env=self.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.processes.append(process)
        return process

    def ready(self, process):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if select.select([process.stdout], [], [], 0.1)[0]:
                line = process.stdout.readline()
                if line and json.loads(line).get("event") == "ready":
                    return
            if process.poll() is not None:
                break
        self.fail("fixture indicator was not ready")

    def ended(self, process):
        stdout, stderr = process.communicate(timeout=2)
        events = [json.loads(line) for line in stdout.splitlines()]
        ended = next(item for item in reversed(events) if item.get("event") == "ended")
        self.assertEqual(stderr, "")
        self.assertFalse(list((self.actor_dir / "indicators").glob("task-*")))
        self.last_events = events
        self.receipt_runs.append({"observed_process_end_monotonic":time.monotonic(), "events":events, "renderer_protocol":self.protocol(), "actor_requests":copy.deepcopy(self.actor_requests), "returncode":process.returncode})
        self.assertTrue(all(type(x.get("monotonic")) in (float, int) for x in events))
        return ended

    def test_old_core_cannot_claim_owner_lifetime_ready(self):
        self.supports_owner_lifetime = False
        process = self.launch("old-core")
        ended = self.ended(process)
        self.assertEqual(ended["reason"], "capability_unavailable")
        self.assertNotIn("ready", [event["event"] for event in self.last_events])
        self.assertEqual(self.task_scopes, {})
        self.assertNotIn("v1 begin", [item["line"] for item in self.protocol()])

    def test_begin_binds_helper_and_task_owner_processes(self):
        process = self.launch("process-bound", extra=("--stdin-control",))
        self.ready(process)
        begin = next(request["arguments"] for request in self.actor_events if request["arguments"].get("event") == "begin")
        self.assertEqual(begin["owner_processes"], [
            {"pid": process.pid, "start_ticks": indicator.process_start_ticks(process.pid)},
            {"pid": os.getpid(), "start_ticks": indicator.process_start_ticks(os.getpid())},
        ])
        process.stdin.write("v1 end\n")
        process.stdin.flush()
        process.wait(timeout=2)
        self.assertEqual(process.returncode, 0)

    def test_lost_begin_ack_cleanup_uses_exact_owner_and_removes_committed_scope(self):
        self.drop_begin_ack_once = True
        process = self.launch()
        ended = self.ended(process)
        self.assertEqual(ended["reason"], "capability_unavailable")
        begin = next(event for event in self.actor_events if event["arguments"].get("event") == "begin")
        cleanup = next(event for event in self.actor_events if event["arguments"].get("event") == "end")
        self.assertEqual(self.task_scopes, {}, "the daemon committed begin before the ACK was lost; exact cleanup removed it")
        for field in ("task_id", "session_id", "expected_epoch", "scope_token"):
            self.assertEqual(cleanup["arguments"].get(field), begin["arguments"].get(field))
        self.assertIn("v1 end", [item["line"] for item in self.protocol()], "failed startup still removes the renderer surfaces")
        self.assertTrue(any(event.get("event") == "task_scope_ended" for event in self.last_events))

    def test_thinking_gap_stays_owned_and_explicit_end_cleans(self):
        process = self.launch()
        self.ready(process)
        lease = next((self.actor_dir / "indicators").glob("task-*.json"))
        self.assertIsNone(json.loads(lease.read_text())["deadline_monotonic"])
        time.sleep(0.35)  # actor remains idle: lifetime is workflow, not action.
        self.assertIsNone(process.poll())
        result = subprocess.run([sys.executable, str(SOURCE / "indicator.py"), "end", "--task-id", "fixture-task"], env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.ended(process)["reason"], "explicit_end")

    def test_cancel_epoch_ends_without_replay(self):
        process = self.launch()
        self.ready(process)
        started = time.monotonic()
        with self.actor_lock:
            self.actor_state["epoch"] += 1
        self.assertEqual(self.ended(process)["reason"], "actor_cancel_epoch_changed")
        self.assertLess(time.monotonic() - started, 0.7)

    def test_takeover_ends(self):
        process = self.launch()
        self.ready(process)
        with self.actor_lock:
            self.actor_state["takeover_latched"] = True
        self.assertEqual(self.ended(process)["reason"], "actor_takeover_latched")

    def test_expired_actor_task_scope_hides_indicator_without_resuming_work(self):
        process = self.launch()
        self.ready(process)
        with self.actor_lock:
            self.task_scopes.pop("fixture-task", None)
        self.assertEqual(self.ended(process)["reason"], "actor_task_scope_expired_or_ended")
        self.assertNotIn("v1 heartbeat", [item["line"] for item in self.protocol()])

    def test_backend_restart_does_not_silently_reconnect(self):
        process = self.launch()
        self.ready(process)
        with self.actor_lock:
            self.actor_state["session_id"] = "replacement-session"
        self.assertEqual(self.ended(process)["reason"], "actor_session_changed")

    def test_unconfirmed_release_during_active_action_keeps_indicator(self):
        process = self.launch()
        self.ready(process)
        with self.actor_lock:
            self.actor_state["active"] = {"task_id": "action"}
            self.actor_state["actor_release_confirmed"] = False
        time.sleep(0.3)
        self.assertIsNone(process.poll())
        with self.actor_lock:
            self.actor_state["active"] = None
        self.assertEqual(self.ended(process)["reason"], "idle_release_unconfirmed")

    def test_initial_latch_refuses_without_renderer(self):
        with self.actor_lock:
            self.actor_state["takeover_latched"] = True
        process = self.launch()
        stdout, _ = process.communicate(timeout=2)
        self.assertNotEqual(process.returncode, 0)
        self.assertIn("actor must be resumed", stdout)
        self.assertFalse(self.render_started.exists())

    def test_owner_identity_mismatch_refuses_without_renderer(self):
        process = self.launch(extra=("--owner-pid", str(os.getpid()), "--owner-start-ticks", "0"))
        stdout, _ = process.communicate(timeout=2)
        self.assertNotEqual(process.returncode, 0)
        self.assertIn("owner process identity changed", stdout)
        self.assertFalse(self.render_started.exists())

    def test_signal_cleanup(self):
        process = self.launch()
        self.ready(process)
        process.terminate()
        self.assertEqual(self.ended(process)["reason"], "owner_signal")

    def test_explicit_maximum_runtime(self):
        process = self.launch(extra=("--max-runtime-seconds", "1"))
        self.ready(process)
        self.assertEqual(self.ended(process)["reason"], "maximum_runtime_expired")

    def test_explicit_runtime_can_exceed_old_twenty_minute_ceiling(self):
        process = self.launch(extra=("--max-runtime-seconds", "3600"))
        self.ready(process)
        lease = next((self.actor_dir / "indicators").glob("task-*.json"))
        data = json.loads(lease.read_text())
        self.assertEqual(data["deadline_monotonic"] - data["started_monotonic"], 3600)
        result = subprocess.run([sys.executable, str(SOURCE / "indicator.py"), "end", "--task-id", "fixture-task"], env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.ended(process)["reason"], "explicit_end")

    def test_same_output_cannot_have_two_owners(self):
        first = self.launch()
        self.ready(first)
        second = self.launch(task="second-task")
        stdout, _ = second.communicate(timeout=2)
        self.assertNotEqual(second.returncode, 0)
        self.assertIn("capability_unavailable", stdout)
        self.assertIsNone(first.poll())
        first.terminate()
        self.ended(first)

    def test_renderer_crash_cleans_lease_even_with_broken_pipe(self):
        process = self.launch()
        self.ready(process)
        os.kill(int(self.render_started.read_text()), signal.SIGKILL)
        ended = self.ended(process)
        self.assertTrue(ended["renderer_exit_confirmed"])

    def test_actor_connection_loss_hides_without_reconnecting(self):
        process = self.launch()
        self.ready(process)
        self.actor_path.unlink()
        started = time.monotonic()
        self.assertEqual(self.ended(process)["reason"], "capability_unavailable")
        self.assertLess(time.monotonic() - started, 0.7)

    def protocol(self):
        return [json.loads(line) for line in self.renderer_log.read_text().splitlines()]

    def wait_request(self, index):
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            with self.actor_lock:
                if len(self.actor_requests) > index:
                    return self.actor_requests[index]
            time.sleep(0.005)
        self.fail("bounded fake lifecycle request was not dispatched")

    def end_control(self):
        self.control_requests.append({"at":time.monotonic(),"event":"explicit_end"})
        return subprocess.run([sys.executable, str(SOURCE / "indicator.py"), "end", "--task-id", "fixture-task"], env=self.env, capture_output=True, text=True, timeout=1)

    def test_fragmented_valid_lifecycle_keeps_frame_owned(self):
        self.actor_plan[1] = {"fragments":4, "fragment_delay":0.035}
        process = self.launch()
        self.ready(process)
        self.wait_request(2)
        self.assertIsNone(process.poll())
        self.assertEqual(self.end_control().returncode, 0)
        self.assertEqual(self.ended(process)["reason"], "explicit_end")
        self.assertFalse(any(x["event"] == "lifecycle_timeout" for x in self.last_events))

    def test_one_timeout_recovers_without_renewing_unknown_lease(self):
        self.actor_plan[1] = {"delay":0.4}
        process = self.launch()
        self.ready(process)
        first = self.wait_request(1)
        self.wait_request(2)
        time.sleep(0.05)
        self.assertIsNone(process.poll())
        self.assertEqual(self.end_control().returncode, 0)
        self.assertEqual(self.ended(process)["reason"], "explicit_end")
        timeouts = [x for x in self.last_events if x["event"] == "lifecycle_timeout"]
        self.assertEqual(len(timeouts), 1)
        self.assertEqual(timeouts[0]["retry"], 1)
        self.assertFalse(timeouts[0]["heartbeat_renewed"])
        second = self.actor_requests[2]
        self.assertFalse(any(x["line"] == "v1 heartbeat" and first["at"] <= x["at"] < second["at"] for x in self.protocol()))
        self.assertTrue(all(x["tool"] == "desktop_indicator_lifecycle" for x in self.actor_requests))

    def test_two_timeouts_bound_recovery_and_hide_without_renewal(self):
        self.actor_plan.update({1:{"delay":1}, 2:{"delay":1}})
        process = self.launch()
        self.ready(process)
        first = self.wait_request(1)
        ended = self.ended(process)
        self.assertEqual(ended["reason"], "capability_unavailable")
        self.assertLess(time.monotonic() - first["at"], 0.9)
        self.assertEqual(len(self.actor_requests), 3)
        self.assertEqual(len([x for x in self.last_events if x["event"] == "lifecycle_timeout"]), 1)
        self.assertFalse(any(x["line"] == "v1 heartbeat" and x["at"] >= first["at"] for x in self.protocol()))

    def test_fragmented_slow_lifecycle_cannot_extend_total_deadline(self):
        self.actor_plan.update({1:{"fragments":20,"fragment_delay":0.045},2:{"fragments":20,"fragment_delay":0.045}})
        process = self.launch()
        self.ready(process)
        first = self.wait_request(1)
        self.assertEqual(self.ended(process)["reason"], "capability_unavailable")
        self.assertLess(time.monotonic() - first["at"], 0.9)
        self.assertEqual(len(self.actor_requests), 3)

    def test_known_stops_after_timeout_are_not_recovered_or_renewed(self):
        for changes, reason in (({"takeover_latched":True}, "actor_takeover_latched"), ({"epoch":3}, "actor_cancel_epoch_changed")):
            with self.subTest(changes=changes):
                self.actor_plan = {1:{"delay":0.4},2:{"status":changes}}
                self.actor_requests.clear()
                self.renderer_log.unlink(missing_ok=True)
                process = self.launch()
                self.ready(process)
                first = self.wait_request(1)
                self.assertEqual(self.ended(process)["reason"], reason)
                self.assertFalse(any(x["line"] == "v1 heartbeat" and x["at"] >= first["at"] for x in self.protocol()))
                self.assertEqual(len(self.actor_requests), 3)
                # The previous server's deliberately delayed reply is never a
                # shared client result; each request owns and closes its socket.
                time.sleep(0.1)

    def test_explicit_end_interrupts_pending_probe_before_timeout(self):
        self.actor_plan[1] = {"delay":1}
        process = self.launch()
        self.ready(process)
        self.wait_request(1)
        start = time.monotonic()
        self.assertEqual(self.end_control().returncode, 0)
        self.assertEqual(self.ended(process)["reason"], "explicit_end")
        self.assertLess(time.monotonic() - start, 0.25)
        self.assertEqual(len(self.actor_requests), 2)
        self.assertFalse(any(x["event"] == "lifecycle_timeout" for x in self.last_events))

    def test_stdin_end_and_eof_interrupt_pending_probe(self):
        for payload, reason in ((b"v1 end\n", "explicit_end"), (None, "owner_stdin_closed")):
            with self.subTest(reason=reason):
                self.actor_plan = {1:{"delay":1}}
                self.actor_requests.clear()
                process = self.launch(extra=("--stdin-control",))
                self.ready(process)
                self.wait_request(1)
                start = time.monotonic()
                if payload is not None:
                    process.stdin.write(payload.decode())
                    process.stdin.flush()
                else:
                    process.stdin.close()
                    process.stdin = None
                self.assertEqual(self.ended(process)["reason"], reason)
                self.assertLess(time.monotonic() - start, 0.25)
                self.assertEqual(len(self.actor_requests), 2)

    def test_signal_interrupts_pending_probe_before_timeout(self):
        self.actor_plan[1] = {"delay":1}
        process = self.launch()
        self.ready(process)
        self.wait_request(1)
        start = time.monotonic()
        process.terminate()
        self.assertEqual(self.ended(process)["reason"], "owner_signal")
        self.assertLess(time.monotonic() - start, 0.25)
        self.assertEqual(len(self.actor_requests), 2)
        self.assertFalse(any(x["event"] == "lifecycle_timeout" for x in self.last_events))

    def test_parent_death_interrupts_pending_probe(self):
        owner = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(5)"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.processes.append(owner)
        self.actor_plan[1] = {"delay":1}
        process = self.launch(extra=("--owner-pid", str(owner.pid), "--owner-start-ticks", str(indicator.process_start_ticks(owner.pid))))
        self.ready(process)
        self.wait_request(1)
        start = time.monotonic()
        owner.terminate()
        owner.wait(timeout=1)
        self.assertEqual(self.ended(process)["reason"], "owner_process_exited")
        self.assertLess(time.monotonic() - start, 0.25)
        self.assertEqual(len(self.actor_requests), 2)

    def test_non_timeout_failures_never_retry_or_fallback(self):
        for plan in ({"eof":True}, {"raw":b"not-json\n"}, {"envelope":{"isError":True}}, {"status":{"lifecycle_revision":1}}, {"status":{"complete":False}}, {"status":{"actor_active":None}}):
            with self.subTest(plan=plan):
                self.actor_plan = {1:plan}
                self.actor_requests.clear()
                process = self.launch()
                self.ready(process)
                self.assertEqual(self.ended(process)["reason"], "capability_unavailable")
                self.assertEqual(len(self.actor_requests), 2)
                self.assertFalse(any(x["event"] == "lifecycle_timeout" for x in self.last_events))

    def test_unknown_endpoint_fails_closed_before_renderer(self):
        self.actor_plan[0] = {"envelope":{"isError":True}}
        process = self.launch()
        stdout, _ = process.communicate(timeout=2)
        self.assertNotEqual(process.returncode, 0)
        self.assertIn("actor rejected lifecycle request", stdout)
        self.assertFalse(self.render_started.exists())
        self.assertEqual(len(self.actor_requests), 1)

    def test_wrong_task_control_cannot_end_owned_indicator(self):
        process = self.launch()
        self.ready(process)
        path = next((self.actor_dir / "indicators").glob("task-*.sock"))
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
            stream.connect(str(path))
            stream.sendall(b'{"revision":1,"event":"end","task_id":"wrong-task"}\n')
            response = json.loads(stream.recv(4096))
        self.assertFalse(response["ok"])
        self.assertIsNone(process.poll())
        process.terminate()
        self.ended(process)


if __name__ == "__main__":
    unittest.main()
