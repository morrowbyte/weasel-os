#!/usr/bin/env python3
"""Check local Codex-compatible binaries without a model turn, account or desktop input."""
import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

async def probe(binary, command, args, source):
    with tempfile.TemporaryDirectory(prefix="weasel-client-") as root:
        base = Path(root)
        env = dict(os.environ, CODEX_HOME=root, PYTHONDONTWRITEBYTECODE="1")
        actor = shutil.which("weasel-computer-use")
        config = (f"[mcp_servers.weasel_os]\ncommand = {json.dumps(command)}\n"
                  f"args = {json.dumps(args)}\n")
        if actor:
            config += (f"[mcp_servers.weasel_desktop]\ncommand = {json.dumps(actor)}\n"
                       'args = ["mcp"]\n')
        (base / "config.toml").write_text(config)
        process = await asyncio.create_subprocess_exec(
            binary, "app-server", cwd=base, env=env, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, limit=8 << 20)
        async def call(rid, method, params):
            msg = {"id": rid, "method": method, "params": params}
            process.stdin.write((json.dumps(msg) + "\n").encode())
            async with asyncio.timeout(25):
                while line := await process.stdout.readline():
                    reply = json.loads(line)
                    if reply.get("id") == rid:
                        if "error" in reply:
                            raise RuntimeError(f"{method}: {reply['error']}")
                        return reply["result"]
                raise RuntimeError(f"{method}: app-server closed stdout")
        try:
            hello = await call(1, "initialize", {
                "clientInfo": {"name": "weasel_probe", "title": "Weasel", "version": "1"},
                "capabilities": {"experimentalApi": True}})
            process.stdin.write(b'{"method":"initialized"}\n')
            session = await call(2, "thread/start", {"cwd": root, "ephemeral": True,
                                                     "approvalPolicy": "never", "sandbox": "read-only"})
            thread_id = session["thread"]["id"]
            listing = await call(3, "mcpServerStatus/list", {"threadId": thread_id, "limit": 100})
            servers = {s["name"]: s["tools"] for s in listing["data"]}
            required = {"catalog", "compose", "private_open", "private_text", "private_cancel_all",
                        "private_resume", "window_focus"}
            if not required <= servers["weasel_os"].keys():
                raise ValueError("Desktop action inventory incomplete")
            if actor and not {"desktop_act", "desktop_cancel"} <= servers["weasel_desktop"].keys():
                raise ValueError("Full desktop inventory incomplete")
            value = await call(4, "mcpServer/tool/call", {"threadId": thread_id, "server": "weasel_os",
                                                         "tool": "catalog", "arguments": {}})
            result = json.loads(value["content"][0]["text"])
            assert result["actions"].keys() == servers["weasel_os"].keys() - {"compose", "catalog"}
            print(json.dumps({"backend": binary, "userAgent": hello.get("userAgent"),
                              "entry": "source" if source else "installed", "catalog": len(result["actions"]),
                              "tools": len(servers["weasel_os"]), "actor_tools": len(servers.get("weasel_desktop", {}))}))
        finally:
            process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), 4)
            except TimeoutError:
                process.terminate()
                await process.wait()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", help="Codex binary of the client under test")
    parser.add_argument("--source", action="store_true", help="check repository source via Python")
    args = parser.parse_args()
    command = sys.executable if args.source else shutil.which("weasel")
    if not command:
        parser.error("weasel is not installed")
    argv = (["-B", str(Path(__file__).resolve().parents[1] / "weasel.py")] if args.source else []) + ["mcp"]
    asyncio.run(probe(args.binary, command, argv, args.source))

if __name__ == "__main__":
    main()
