import asyncio
import json
import os
from contextlib import AsyncExitStack
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from actions import execute
from desktop import ActionError, Desktop

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
