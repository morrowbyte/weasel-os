import asyncio
import fcntl
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

DESKTOP = Path(__file__).resolve().parents[1]
WORKSPACE = DESKTOP.parent / "agent-workspace"
sys.path.insert(0, str(DESKTOP))
import actions as compose
from desktop import ActionError

# These separately packaged CLIs each have their own module named actions.
with patch.dict(sys.modules), patch.object(sys, "path", [str(WORKSPACE), *sys.path]):
    del sys.modules["actions"]
    import broker
    import workspace


def key(value, name="default"):
    return {"action": "private_key", "args": {"name": name, "key": value}}


class Grouping(unittest.IsolatedAsyncioTestCase):
    async def test_existing_desktop_indicator_is_checked_between_inputs(self):
        indicator = Mock(returncode=None)

        async def first_input(*args, payload):
            self.assertEqual(payload, [["key", "a"]])
            indicator.returncode = 0
            return {"ok": True, "results": [{"action": "key"}]}

        with patch.object(compose, "Desktop") as desktop, patch.object(compose, "workspace", first_input):
            desktop.return_value.indicator = indicator
            result, _ = await compose.execute([key("a"), key("b"), key("c")])
        self.assertEqual((result["status"], result["remaining"]), ("partial", 1))
        self.assertEqual(result["steps"][1]["error"], "Desktop-Auftrag wurde beendet")

    async def test_boundaries_and_byte_budget(self):
        text = {"action": "private_text", "args": {"text": "\x01" * 8192}}
        steps = [key("a"), key("b"), {"action": "private_status"}, key("c"),
                 key("d", "other"), text, text]

        async def respond(name, op, *args, payload=None):
            if op == "status":
                return {"ok": True, "running": True}
            return {"ok": True, "results": [{"action": item[0]} for item in payload]}

        with patch.object(compose, "workspace", side_effect=respond) as cli:
            result, _ = await compose.execute(steps)
        self.assertEqual(result["status"], "completed")
        self.assertEqual([s["action"] for s in result["steps"]], [s["action"] for s in steps])
        self.assertEqual([(c.args[:2], len(c.kwargs.get("payload", []))) for c in cli.await_args_list], [
            (("default", "batch"), 2), (("default", "status"), 0), (("default", "batch"), 1),
            (("other", "batch"), 1), (("default", "batch"), 1), (("default", "batch"), 1)])
        with patch.object(compose, "workspace", new_callable=AsyncMock) as cli:
            with self.assertRaises(ValueError):
                await compose.execute([key("a"), key("Ctrl+Ctrl+a")])
            cli.assert_not_awaited()

    async def test_unknown_delivery_marks_whole_submitted_run_without_retry(self):
        replies = [ActionError("EOF"), ActionError("legacy", {"ok": False, "error": "legacy"}),
                   {"ok": True, "results": [{"action": "click"}]}]
        for reply in replies:
            with self.subTest(reply=reply), patch.object(compose, "workspace", side_effect=[reply]) as cli:
                result, _ = await compose.execute([key("a"), key("b"), {"action": "private_status"}])
                self.assertEqual(result["status"], "stopped")
                self.assertEqual(result["remaining"], 1)
                self.assertEqual([s["effect"] for s in result["steps"]], ["unknown", "unknown"])
                cli.assert_awaited_once()


class PrivateTransport(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Real CLI subprocess, Unix socket, gate and one-shot job store.
        # No private compositor or desktop input is needed.
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        bindir = root / "bin"
        bindir.mkdir()
        cli = bindir / "weasel-workspace"
        cli.write_text(f"#!{sys.executable}\nimport runpy\n"
                       f"runpy.run_path({str(WORKSPACE / 'workspace.py')!r}, run_name='__main__')\n")
        cli.chmod(0o700)
        self.enterContext(patch.dict(os.environ, PATH=str(bindir) + ":" + os.environ["PATH"],
                                     XDG_RUNTIME_DIR=str(root), WEASEL_WORKSPACE_TYPING="fast",
                                     PYTHONDONTWRITEBYTECODE="1"))
        base = workspace.base_dir()
        self.paths = workspace.Paths(base, "default")
        live = self.enterContext(self.paths.live.open("wb"))
        fcntl.flock(live, fcntl.LOCK_EX)
        self.broker = broker.Broker()
        self.enterContext(patch.object(self.broker, "gate", return_value={"paused": False,
                          "epoch": json.loads((base / "gate/state").read_bytes())["epoch"]}))
        server = await asyncio.start_unix_server(self.broker.client, path=self.paths.sock)
        self.addAsyncCleanup(server.wait_closed)
        self.addCleanup(server.close)

    async def test_mixed_batch_focus_failure_and_consumed_ids(self):
        steps = [key("Home"), {"action": "private_click", "args": {"x": 7, "y": 12, "button": "left"}},
                 {"action": "private_scroll", "args": {"x": 8, "y": 13, "direction": "down", "steps": 2}},
                 {"action": "private_text", "args": {"text": "Grüße"}}, key("End")]
        with patch.object(broker, "windows", side_effect=[[{"focused": True}], []]) as focus, \
             patch.object(broker, "outputs", new_callable=AsyncMock) as geometry, \
             patch.object(broker, "run", new_callable=AsyncMock) as run:
            result, _ = await compose.execute(steps, "once")
            self.assertEqual((result["status"], result["remaining"]), ("partial", 1))
            self.assertEqual([s["ok"] for s in result["steps"]], [True, True, True, False])
            failure = result["steps"][-1]
            self.assertIn("no focused application", failure["error"])
            self.assertEqual((failure["effect"], failure["receipt"]["attempted"]), ("unknown", 4))
            self.assertEqual(len({s["job"] for s in result["steps"]}), 1)
            self.assertEqual(focus.await_count, 2)
            self.assertEqual(geometry.await_count, 2)
            self.assertEqual([c.args for c in run.await_args_list], [
                (broker.HERE / "workspace-keyboard", "Home"),
                (broker.HERE / "workspace-pointer", "click", "7", "12", "left"),
                (broker.HERE / "workspace-pointer", "scroll", "8", "13", "down", "2")])
            with self.assertRaisesRegex(ValueError, "verbraucht"):
                await compose.execute(steps, "once")
            with self.assertRaisesRegex(ActionError, "already submitted"):
                await compose.workspace("default", "batch", failure["job"], payload=[["key", "a"]])

    async def test_original_job_cancel_and_timeout_retain_prefix(self):
        for guard in ("cancel", "timeout"):
            entered = asyncio.Event()

            async def act(action, mode):
                if action[1] == "b":
                    entered.set()
                    await asyncio.Future()
                return {"action": action[0]}

            with self.subTest(guard=guard), patch.object(broker, "act", side_effect=act) as actor, \
                 patch.object(broker, "MAX_SECONDS", 0.15 if guard == "timeout" else 5):
                task = asyncio.create_task(compose.execute([key("a"), key("b"), key("c")]))
                await asyncio.wait_for(entered.wait(), 5)
                if guard == "cancel":
                    await compose.workspace("default", "cancel", self.broker.job_id)
                result, _ = await asyncio.wait_for(task, 5)
                self.assertEqual((result["status"], result["remaining"]), ("partial", 1))
                failure = result["steps"][1]
                self.assertEqual((failure["effect"], failure["receipt"]["attempted"]), ("unknown", 2))
                self.assertIn("cancelled" if guard == "cancel" else "exceeded", failure["error"])
                self.assertEqual(actor.await_count, 2)

    async def test_client_disconnect_stops_running_job_with_unknown_reply(self):
        entered, interrupted = asyncio.Event(), asyncio.Event()

        async def act(action, mode):
            if action[1] == "b":
                entered.set()
                try:
                    await asyncio.Future()
                finally:
                    interrupted.set()
            return {"action": action[0]}

        with patch.object(broker, "act", side_effect=act) as actor:
            task = asyncio.create_task(compose.execute([key("a"), key("b"), key("c"),
                                                       {"action": "private_status"}]))
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            result, _ = await asyncio.wait_for(task, 5)
            await asyncio.wait_for(interrupted.wait(), 5)
        self.assertEqual((result["status"], result["remaining"]), ("stopped", 1))
        self.assertEqual([s["effect"] for s in result["steps"]], ["unknown"] * 3)
        self.assertEqual(actor.await_count, 2)

    async def test_typing_modes_and_gate_before_next_action(self):
        state = self.broker.gate()
        with patch.object(broker, "windows", return_value=[{"focused": True}]), \
             patch.object(broker, "run", new_callable=AsyncMock) as run:
            text = {"action": "private_text", "args": {"text": "Grüße!"}}
            for mode, binary in (("fast", broker.FAST_WTYPE), ("upstream", "wtype")):
                with patch.dict(os.environ, WEASEL_WORKSPACE_TYPING=mode):
                    result, _ = await compose.execute([text, key("Ctrl+Home")])
                self.assertEqual(result["status"], "completed")
                self.assertEqual(run.await_args_list[-2].args, (binary, "-"))
                self.assertEqual(run.await_args_list[-2].kwargs["data"], "Grüße!".encode())
                self.assertEqual(run.await_args_list[-1].args, (broker.HERE / "workspace-keyboard", "Home", "ctrl"))
            run.side_effect = lambda *args, **kwargs: state.update(paused=True)
            result, _ = await compose.execute([key("a"), key("b"), key("c")])
            self.assertEqual((result["status"], result["remaining"]), ("partial", 1))
            self.assertEqual(result["steps"][1]["effect"], "not_started")
            self.assertEqual(result["steps"][1]["receipt"]["attempted"], 1)
