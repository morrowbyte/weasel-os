import asyncio
import io
import json
import os
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import broker
import workspace


class KeyboardTests(unittest.IsolatedAsyncioTestCase):
    async def test_cli_and_broker_aliases(self):
        cases = {
            "ENTER": "Return", "eSc": "Escape", "PGUP": "Page_Up", "PgDn": "Page_Down",
            "DEL": "Delete", "Ins": "Insert", "PageUp": "Page_Up", "PageDown": "Page_Down",
            "PGDOWN": "Page_Down", "BKSP": "BackSpace", "BS": "BackSpace",
            "spacebar": "space", "ArrowUp": "Up", "ArrowDown": "Down",
            "ArrowLeft": "Left", "ARROWRIGHT": "Right", "KPEnter": "KP_Enter",
            "NumPadEnter": "KP_Enter", "F12": "F12", "XF86AudioMute": "XF86AudioMute",
        }
        with tempfile.TemporaryDirectory() as runtime, patch.dict(os.environ, XDG_RUNTIME_DIR=runtime), \
             patch.object(workspace.Paths, "running", return_value=True), patch.object(workspace, "rpc") as rpc, \
             patch.object(broker, "windows", return_value=[{"focused": True}]), \
             patch.object(broker, "run", new_callable=AsyncMock) as run:
            for alias, canonical in cases.items():
                with self.subTest(alias=alias):
                    chord = "Ctrl+Shift+" + alias
                    workspace.execute(Namespace(command="key", name="default", values=[chord]))
                    submitted = rpc.call_args.args[1]["actions"]
                    self.assertEqual(submitted, [["key", "ctrl+shift+" + canonical]])
                    # Direct broker clients may submit the original spelling.
                    direct = broker.validate_batch([["key", chord]])
                    self.assertEqual(direct, broker.validate_batch(submitted))
                    await broker.act(direct[0], "fast")
                    run.assert_awaited_with(broker.HERE / "workspace-keyboard", canonical, "ctrl", "shift")

    async def test_invalid_late_keysym_rejects_whole_batch(self):
        actions = [["text", "first effect"], ["key", "Ctrl+NoSuchWorkspaceKey"]]
        with tempfile.TemporaryDirectory() as runtime, patch.dict(os.environ, XDG_RUNTIME_DIR=runtime), \
             patch.object(workspace.Paths, "running", return_value=True), patch.object(workspace, "rpc") as rpc, \
             patch.object(sys, "stdin") as stdin:
            stdin.buffer = io.BytesIO(json.dumps(actions).encode())
            with self.assertRaisesRegex(ValueError, "unknown XKB keysym 'NoSuchWorkspaceKey'"):
                workspace.execute(Namespace(command="batch", name="default", job="late"))
            rpc.assert_not_called()
            self.assertFalse((workspace.base_dir() / "jobs.sqlite3").exists())

        service = broker.Broker()
        progress = {"attempted": 0, "results": []}
        request = {"op": "batch", "epoch": "epoch", "job": "late", "actions": actions}
        with patch.object(service, "gate", return_value={"paused": False, "epoch": "epoch"}), \
             patch.object(broker, "act", new_callable=AsyncMock) as act:
            with self.assertRaisesRegex(ValueError, "unknown XKB keysym 'NoSuchWorkspaceKey'"):
                await service.dispatch(request, asyncio.StreamReader(), progress)
            act.assert_not_awaited()
            self.assertEqual(progress, {"attempted": 0, "results": []})


if __name__ == "__main__":
    unittest.main()
