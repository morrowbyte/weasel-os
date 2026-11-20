import asyncio
import json
import os
from contextlib import AsyncExitStack
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from actions import execute
from catalog import CATALOG, validate
from desktop import ActionError, Desktop
from weasel import pick_args

class Entry(unittest.IsolatedAsyncioTestCase):
    async def test_whole_plan_before_dispatch_and_partial_receipt_after_dispatch(self):
        with patch("actions.private", new_callable=AsyncMock) as private:
            steps = [{"action": "private_start"}, {"action": "private_click", "args": {"x": -1}}]
            with self.assertRaises(ValueError):
                await execute(steps)
            private.assert_not_awaited()
            private.side_effect = [{"running": True}, ActionError("busy", {"ok": False})]
            steps = [{"action": name} for name in ["private_start", "private_editor", "private_status"]]
            with tempfile.TemporaryDirectory() as runtime, patch.dict(os.environ, XDG_RUNTIME_DIR=runtime):
                receipt, _ = await execute(steps, "owned-task")
                self.assertEqual((receipt["status"], receipt["remaining"]), ("partial", 1))
                self.assertEqual(receipt["steps"][1]["receipt"], {"ok": False})
                with self.assertRaisesRegex(ValueError, "verbraucht"):
                    await execute(steps, "owned-task")
                self.assertEqual(private.await_count, 2)

    async def test_private_open_passes_literal_argv_and_waits_in_one_broker_job(self):
        window = {"id": 41, "app_id": "report", "title": "Bericht", "focused": True}
        spawn = {"pid": 16}
        argv = ["--app-id=report", "name with spaces", "", "$(literal); *"]
        with patch("actions.workspace", new_callable=AsyncMock) as broker:
            broker.side_effect = [{"running": True, "windows": []}, {"results": [spawn, window]}]
            result, _ = await execute([{"action": "private_open", "args": {
                "name": "report", "program": "foot", "argv": argv, "wait_window": "report",
            }}])
            self.assertEqual(result["status"], "completed")
            receipt = result["steps"][0]["result"]
            self.assertEqual(receipt["window"], window)
            self.assertEqual(receipt["spawn"], spawn)
            self.assertEqual(broker.await_args_list, [
                call("report", "status"),
                call("report", "batch", receipt["job"],
                     payload=[["open", "foot", *argv], ["wait-window", "report"]]),
            ])

    async def test_private_open_rejects_existing_id_and_keeps_unconfirmed_broker_receipt(self):
        args = {"program": "foot", "wait_window": "report"}
        plan = [{"action": "private_open", "args": args}, {"action": "private_text", "args": {"text": "Check"}}]
        existing = {"id": 41, "app_id": "report", "focused": False}
        with patch("actions.workspace", new_callable=AsyncMock) as broker:
            broker.return_value = {"running": True, "windows": [existing]}
            result, _ = await execute(plan)
            self.assertEqual((result["status"], result["remaining"]), ("stopped", 1))
            broker.assert_awaited_once_with("default", "status")

            broker.reset_mock()
            old_window = {**existing, "app_id": "other"}
            stale = {"results": [{"pid": 16}, {**existing, "focused": True}]}
            broker.side_effect = [{"running": True, "windows": [old_window]}, stale]
            result, _ = await execute(plan)
            self.assertEqual((result["status"], result["remaining"]), ("stopped", 1))
            self.assertEqual(result["steps"][0]["receipt"]["broker"], stale)
            self.assertEqual(broker.await_count, 2)

    async def test_private_start_and_gate_use_workspace_cli(self):
        with patch("actions.workspace", new_callable=AsyncMock) as broker:
            broker.side_effect = [
                {"running": True, "network": False, "exchange": None},
                {"running": True, "network": True, "exchange": "/tmp/A B"},
                {"paused": True}, {"paused": False},
            ]
            steps = [{"action": "private_start"},
                     {"action": "private_start", "args": {"name": "report", "network": True, "exchange": "/tmp/A B"}},
                     {"action": "private_cancel_all"}, {"action": "private_resume"}]
            result, _ = await execute(steps)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(broker.await_args_list, [
                call("default", "start"), call("report", "start", "--exchange=/tmp/A B", "--network"),
                call("default", "cancel-all"), call("default", "resume"),
            ])

    async def test_palette_reads_argv_as_json_and_uses_human_labels(self):
        with patch("weasel.prompt", new_callable=AsyncMock) as prompt:
            prompt.side_effect = ["", "foot", '["--app-id=report","two words",""]', "report"]
            args = await pick_args(CATALOG["private_open"])
            self.assertEqual(validate("private_open", args)["argv"], ["--app-id=report", "two words", ""])
            self.assertEqual([entry.args[0] for entry in prompt.await_args_list],
                             ["Name der privaten Arbeitsfläche [default]", "Ausführbares Programm",
                              "Argumente als JSON-Liste (optional)", "Wayland-App-ID oder X11-Klasse des neuen Fensters"])
        for invalid in ("--app-id=report", ["valid", 5], ["contains\0NUL"]):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "argv"):
                validate("private_open", {**args, "argv": invalid})
        with self.assertRaisesRegex(ValueError, "UTF-8"):
            validate("private_open", {**args, "wait_window": "ä" * 128})
        with self.assertRaisesRegex(ValueError, "network"):
            validate("private_start", {"network": "true"})

    async def test_cancel_sent_to_original_actor_request_only_and_retains_receipt(self):
        class Proxy:
            def __init__(self):
                self.messages = []
                self.stdin = self
                self.stdout = asyncio.StreamReader()

            def write(self, line):
                msg = json.loads(line)
                self.messages.append(msg)
                if msg["method"] == "notifications/cancelled":
                    self.stdout.feed_data(b'{"id":1,"result":{"isError":true}}\n')

        async with AsyncExitStack() as stack:
            desktop = Desktop(stack)
            desktop.proxy = Proxy()
            task = asyncio.create_task(desktop.rpc("tools/call", {"name": "desktop_act"}))
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(ActionError) as err:
                await task
            self.assertEqual(err.exception.receipt, {"isError": True})
            self.assertEqual([m["method"] for m in desktop.proxy.messages],
                             ["tools/call", "notifications/cancelled"])
            self.assertEqual(desktop.proxy.messages[-1]["params"]["requestId"],
                             desktop.proxy.messages[0]["id"])

    async def test_focus_readback_mismatch_stops_after_one_fresh_dispatch(self):
        async with AsyncExitStack() as stack:
            desktop = Desktop(stack)
            desktop.scope = AsyncMock()
            desktop.session, desktop.epoch = "bound", 4
            desktop.call = AsyncMock(side_effect=[
                {"workspaces": [{"id": 9, "is_focused": True, "output": "DP-1"}]},
                {"observation_id": "obs-bound-1", "epoch": 4, "workspaces": [{"id": 9, "active_window_id": 3}]},
                {"status": "dispatched", "actor_release_confirmed": True,
                 "after_observation": {"focused_window": {"id": 4}}}])
            with self.assertRaisesRegex(ActionError, "unbestätigt") as err:
                await desktop.act("workspace_focus", {"workspace_id": 9}, "Fokus")
            self.assertEqual(err.exception.receipt["status"], "dispatched")
            calls = desktop.call.await_args_list
            self.assertEqual([c.args[0] for c in calls], ["desktop_windows", "desktop_observe", "desktop_act"])
            self.assertEqual(calls[-1].args[1]["actions"], [{"kind": "focus", "window_id": 3}])
            self.assertEqual(calls[-1].args[1]["observation_id"], "obs-bound-1")
            self.assertEqual(calls[-1].args[1]["task_id"], desktop.task_id)

if __name__ == "__main__":
    unittest.main()
