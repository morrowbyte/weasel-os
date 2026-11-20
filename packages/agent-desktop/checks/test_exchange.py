"""Run the product CLI and MCP through a real Unix broker and inherited directory FD."""
import asyncio
import fcntl
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

DESKTOP = Path(__file__).resolve().parents[1]
WORKSPACE = DESKTOP.parent / "agent-workspace"
with patch.dict(sys.modules), patch.object(sys, "path", [str(WORKSPACE), *sys.path]):
    sys.modules.pop("actions", None)
    import broker
    import sandbox
    import workspace


class ExchangeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        bindir = self.root / "bin"
        bindir.mkdir()
        cli = bindir / "weasel-workspace"
        cli.write_text(f"#!{sys.executable}\nimport runpy\n"
                       f"runpy.run_path({str(WORKSPACE / 'workspace.py')!r}, run_name='__main__')\n")
        cli.chmod(0o700)
        self.enterContext(patch.dict(os.environ, XDG_RUNTIME_DIR=str(self.root),
                                     PATH=f"{bindir}:{os.environ['PATH']}"))
        base = workspace.base_dir()
        self.paths = workspace.Paths(base, "work")
        self.gate = base / "gate"
        self.enterContext(patch.object(broker, "GATE", self.gate))
        self.live = self.enterContext(self.paths.live.open("wb"))
        fcntl.flock(self.live, fcntl.LOCK_EX)
        self.directory = self.root / "files"
        _, fd = self.enterContext(sandbox.exchange_dir(self.directory))
        self.broker = broker.Broker(fd)
        self.server = await asyncio.start_unix_server(self.broker.client, path=self.paths.sock)

    async def asyncTearDown(self):
        self.server.close()
        await self.server.wait_closed()

    async def cli(self, *args, data=None):
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-B", DESKTOP / "weasel.py", *args,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(proc.communicate(json.dumps(data).encode() if data else None), 8)
        self.assertTrue(out, err)
        return proc.returncode, json.loads(out)

    def step(self, op, **args):
        return {"action": "private_exchange_" + op, "args": {"name": "work", **args}}

    async def test_real_compose_rename_and_no_overwrite(self):
        original = self.directory / "-draft.txt"
        original.write_bytes(b"invoice")
        inode = original.stat().st_ino
        occupied = self.directory / "occupied"
        occupied.write_bytes(b"keep")
        steps = [self.step("list"), self.step("lstat", filename=original.name),
                 self.step("rename_noreplace", source=original.name, destination="118.txt"),
                 self.step("lstat", filename="118.txt"),
                 self.step("rename_noreplace", source="118.txt", destination=occupied.name), self.step("list")]
        rc, result = await self.cli("compose", "--request-id", "once", "-", data=steps)
        self.assertEqual((rc, result["status"], result["remaining"]), (1, "partial", 1), result)
        listing, before, renamed, after, failed = result["steps"]
        self.assertEqual(listing["result"]["entries"], ["-draft.txt", "occupied"])
        self.assertFalse(listing["result"]["truncated"])
        self.assertEqual(before["result"]["inode"], inode)
        self.assertEqual(after["result"]["inode"], inode)
        self.assertEqual(after["result"]["bytes"], 7)
        self.assertTrue(renamed["result"]["renamed"])
        self.assertIn("File exists", failed["error"])
        self.assertTrue(all(s["duration_ns"] > 0 for s in result["steps"]))
        self.assertEqual((self.directory / "118.txt").read_bytes(), b"invoice")
        self.assertEqual(occupied.read_bytes(), b"keep")

    async def test_mcp_bound_exchange_and_paused_reads(self):
        # Replacing the host pathname cannot change what the running broker sees.
        original = self.root / "original"
        self.directory.rename(original)
        self.directory.mkdir()
        (self.directory / "wrong-session").touch()
        replacement = self.directory.stat()
        with self.assertRaisesRegex(workspace.BrokerError, "exchange identity changed"):
            await asyncio.to_thread(workspace.rpc, self.paths, {"op": "status", "verify_exchange":
                                    [replacement.st_dev, replacement.st_ino]})
        (original / "link").symlink_to(self.root)
        workspace.save(self.gate / "state", workspace.new_gate(True))
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-B", DESKTOP / "weasel.py", "mcp", stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE)
        async def call(method, params):
            request = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
            proc.stdin.write(json.dumps(request).encode() + b"\n")
            await proc.stdin.drain()
            return json.loads(await asyncio.wait_for(proc.stdout.readline(), 5))["result"]
        async def action(op, **args):
            step = self.step(op, **args)
            reply = await call("tools/call", {"name": step["action"], "arguments": step["args"]})
            return json.loads(reply["content"][0]["text"])["steps"][0]
        try:
            catalog = {tool["name"]: tool for tool in (await call("tools/list", {}))["tools"]}
            self.assertTrue(catalog["private_exchange_lstat"]["annotations"]["readOnlyHint"])
            self.assertFalse(catalog["private_exchange_rename_noreplace"]["annotations"]["readOnlyHint"])
            self.assertEqual((await action("list"))["result"]["entries"], ["link"])
            self.assertTrue((await action("lstat", filename="link"))["result"]["mode"].startswith("l"))
            self.assertIn("paused", (await action("rename_noreplace", source="link", destination="x"))["error"])
            self.assertIn("session changed", (await action("list", session="0" * 32))["error"])
            self.broker.exchange_fd = None
            self.assertFalse((await action("list"))["ok"])
            self.assertFalse((await action("list", name="other"))["ok"])
        finally:
            proc.stdin.close()
            await asyncio.wait_for(proc.wait(), 5)

    async def test_cancel_and_stop_block_queued_rename(self):
        (self.directory / "source").touch()
        request = {"op": "exchange-rename-noreplace", "job": "waiting",
                   "names": ["source", "dest"], "epoch": self.broker.gate()["epoch"]}
        entered = asyncio.Event()
        perform = self.broker.exchange_operation

        async def observe(*args):
            entered.set()
            return await perform(*args)

        # The worker must acquire this same lock before committing the rename.
        with workspace.locked(self.gate), patch.object(self.broker, "exchange_operation", observe):
            task = asyncio.create_task(asyncio.to_thread(workspace.rpc, self.paths, request))
            await asyncio.wait_for(entered.wait(), 5)
            cancelled = await asyncio.to_thread(workspace.rpc, self.paths, {"op": "cancel", "job": "waiting"})
            self.assertEqual(cancelled, {"cancelled": "waiting"})
            with self.assertRaisesRegex(workspace.BrokerError, "cancelled"):
                await task
        self.assertTrue((self.directory / "source").exists())
        self.assertFalse((self.directory / "dest").exists())
        await asyncio.to_thread(workspace.rpc, self.paths, {"op": "stop"})
        with self.assertRaisesRegex(workspace.BrokerError, "no running"):
            await asyncio.to_thread(workspace.rpc, self.paths, request)

    async def test_unsafe_names_budget_and_admission(self):
        fd = self.broker.exchange_fd
        operation = broker.exchange.operate
        state = broker.Broker.gate()
        for forbidden in (".weasel-workspace-exchange", "sub/file", "../abc", "", "/etc/passwd", "a\nb", "€" * 90):
            with self.subTest(forbidden=forbidden), self.assertRaises(ValueError):
                operation(fd, "exchange-lstat", [forbidden], "")
        source = self.directory / "valid"
        source.write_bytes(b"keep")
        request = {"op": "exchange-rename-noreplace", "names": ["valid", "later"],
                   "job": "rename", "epoch": state["epoch"]}
        with patch.object(self.broker, "view") as view:
            view.manual = True
            with self.assertRaisesRegex(RuntimeError, "manual use"):
                await self.broker.dispatch(request, asyncio.StreamReader(), {})
        with self.assertRaisesRegex(ValueError, "expired"):
            operation(fd, request["op"], request["names"], "old", self.gate)
        (self.directory / "link").symlink_to(source)
        with self.assertRaisesRegex(ValueError, "regular"):
            operation(fd, request["op"], ["link", "later"], state["epoch"], self.gate)
        for i in range(broker.exchange.MAX_ENTRIES):
            (self.directory / str(i)).touch()
        listing = operation(fd, "exchange-list", [], "")
        self.assertTrue(listing["truncated"])
        self.assertEqual(len(listing["entries"]), broker.exchange.MAX_ENTRIES)
        self.assertEqual(source.read_bytes(), b"keep")
        self.assertFalse((self.directory / "later").exists())


if __name__ == "__main__":
    unittest.main()
