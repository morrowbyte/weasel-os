"""Runs only inside the new namespaces. The listener is inherited on fd 0."""
import asyncio
import base64
import json
import os
import socket
import sys
import uuid
from pathlib import Path
from subprocess import DEVNULL, PIPE

sys.path.insert(0, str(Path(__file__).resolve().parent))
from actions import MAX_REQUEST, MAX_SECONDS, chord, identifier, validate_batch

HERE = Path(__file__).resolve().parent
RUNTIME = Path("/run/workspace")
FAST_WTYPE = "@fastWtype@"


async def run(*args, data=None):
    proc = await asyncio.create_subprocess_exec(
        *args, stdin=PIPE if data is not None else DEVNULL, stdout=PIPE, stderr=PIPE)
    try:
        out, err = await proc.communicate(data)
    finally:
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
    if proc.returncode:
        raise RuntimeError(f"{Path(args[0]).name} exited {proc.returncode}: {err[-800:].decode(errors='replace')}")
    return out


async def outputs():
    result = json.loads(await run("swaymsg", "-r", "-t", "get_outputs"))
    active = [o for o in result if o["active"]]
    if len(active) != 1 or active[0]["name"] != "HEADLESS-1":
        raise RuntimeError("expected exactly one active HEADLESS-1")
    rect = active[0]["rect"]
    if [rect[k] for k in ("x", "y", "width", "height")] != [0, 0, 1280, 720]:
        raise RuntimeError("unexpected private output geometry")
    return {"name": "HEADLESS-1", "width": 1280, "height": 720}


async def windows():
    tree = json.loads(await run("swaymsg", "-r", "-t", "get_tree"))
    result = []

    def visit(node):
        if node.get("pid") and node["type"] in ("con", "floating_con"):
            result.append({"id": node["id"], "app_id": node.get("app_id"),
                           "title": (node.get("name") or "")[:512], "focused": node["focused"]})
        for child in node["nodes"] + node["floating_nodes"]:
            visit(child)
    visit(tree)
    return result


async def act(action, typing_mode):
    name, *args = action
    if name == "open":
        proc = await asyncio.create_subprocess_exec(*args, stdin=DEVNULL, stdout=DEVNULL, stderr=DEVNULL)
        return {"pid": proc.pid}
    if name == "wait-window":
        while True:
            for window in await windows():
                if window["focused"] and window["app_id"] == args[0]:
                    return window
            await asyncio.sleep(0.05)
    if name in ("text", "key"):
        if not any(w["focused"] for w in await windows()):
            raise RuntimeError("no focused application in this workspace")
        if name == "text":
            # One stdin keymap chunk in wtype 0.4 holds up to 100 characters.
            wtype = FAST_WTYPE if typing_mode == "fast" and len(args[0]) <= 100 else "wtype"
            await run(wtype, "-", data=args[0].encode())
        else:
            await run(HERE / "workspace-keyboard", *chord(args[0]))
    else:
        await outputs()
        await run(HERE / "workspace-pointer", name, *map(str, args))
    return {"action": name}


class Broker:
    def __init__(self):
        self.active = None
        self.job_id = None
        self.epoch = None
        self.stopping = asyncio.Event()

    @staticmethod
    def gate():
        return json.loads(Path("/run/gate/state").read_bytes())

    def admitted(self):
        gate = self.gate()
        return not gate["paused"] and gate["epoch"] == self.epoch

    async def watch_gate(self):
        while self.admitted():
            await asyncio.sleep(0.05)

    async def batch(self, actions, progress, typing_mode):
        for index, action in enumerate(actions):
            try:
                if not self.admitted():
                    raise RuntimeError("input revoked")
                progress["attempted"] = index + 1
                progress["results"].append(await act(action, typing_mode))
            except Exception as exc:
                raise RuntimeError(f"action {index + 1} ({action[0]}): {exc}") from exc
        return {"results": progress["results"]}

    async def dispatch(self, request, reader, progress):
        op = request["op"]
        if op == "status":
            return {"running": True, "output": await outputs(), "windows": await windows(),
                    "job": self.job_id}
        if op == "gate":
            stopped = None
            if self.active and self.epoch is not None and not self.admitted():
                stopped = self.job_id
                self.active.cancel()
                await asyncio.gather(self.active, return_exceptions=True)
            return {"interrupted": stopped}
        if op == "stop":
            if self.active:
                self.active.cancel()
            return {"stopping": True}
        if op == "cancel":
            wanted = identifier(request["job"])
            if not self.active or self.job_id != wanted:
                raise ValueError("no active job with that name")
            self.active.cancel()
            await asyncio.gather(self.active, return_exceptions=True)
            return {"cancelled": wanted}
        if self.active:
            raise RuntimeError(f"workspace busy (job {self.job_id})")
        if op == "batch":
            job = identifier(request["job"])
            gate = self.gate()
            if gate["paused"] or gate["epoch"] != request["epoch"]:
                raise RuntimeError("input paused or admission expired")
            actions = validate_batch(request["actions"])
            typing_mode = request.get("typing_mode", "fast")
            if typing_mode not in ("fast", "upstream"):
                raise ValueError("unknown private typing mode")
            self.epoch = gate["epoch"]
            work = self.batch(actions, progress, typing_mode)
        elif op == "screenshot":
            job = "screenshot"
            work = self.capture()
        else:
            raise ValueError("unknown operation")

        self.job_id = job
        self.active = asyncio.create_task(work)
        disconnected = asyncio.create_task(reader.read(1))
        guards = [disconnected]
        if self.epoch is not None:
            guards.append(asyncio.create_task(self.watch_gate()))
        try:
            done, _ = await asyncio.wait([self.active, *guards], timeout=MAX_SECONDS,
                                         return_when=asyncio.FIRST_COMPLETED)
            if self.active in done:
                if self.active.cancelled():
                    raise RuntimeError(f"job {job} cancelled")
                return self.active.result()
            if disconnected in done:
                raise RuntimeError("client disconnected or sent another request")
            if any(task in done for task in guards):
                raise RuntimeError(f"job {job} revoked by input gate")
            raise TimeoutError(f"job {job} exceeded {MAX_SECONDS}s")
        finally:
            self.active.cancel()
            for task in guards:
                task.cancel()
            await asyncio.gather(self.active, *guards, return_exceptions=True)
            self.active = self.job_id = self.epoch = None

    async def capture(self):
        await outputs()
        png = await run("grim", "-o", "HEADLESS-1", "-")
        if not png.startswith(b"\x89PNG\r\n\x1a\n") or len(png) > 5 * 1024 * 1024:
            raise RuntimeError("invalid screenshot from private output")
        return {"png": base64.b64encode(png).decode("ascii")}

    async def client(self, reader, writer):
        stop = False
        progress = {}
        try:
            async with asyncio.timeout(5):
                line = await reader.readline()
            if not line or not line.endswith(b"\n") or len(line) > MAX_REQUEST:
                raise ValueError("incomplete or oversized request")
            request = json.loads(line)
            if request["op"] == "batch":
                # Survives cancellation of the worker. Only finished actions
                # enter results; one started action may have unknown effects.
                progress = {"attempted": 0, "results": []}
            async with asyncio.timeout(MAX_SECONDS + 3):
                result = await self.dispatch(request, reader, progress)
            stop = request["op"] == "stop"
            reply = {"ok": True, **result}
        except Exception as exc:
            reply = {"ok": False, "error": str(exc) or type(exc).__name__, **progress}
        try:
            writer.write(json.dumps(reply).encode() + b"\n")
            async with asyncio.timeout(5):
                await writer.drain()
        except (ConnectionError, TimeoutError):
            pass
        finally:
            writer.close()
            if stop:
                self.stopping.set()


async def serve():
    listener = socket.socket(fileno=0)
    if listener.family != socket.AF_UNIX or not listener.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN):
        raise RuntimeError("expected an inherited Unix listener")
    Path("/etc/machine-id").write_text(uuid.uuid4().hex + "\n")
    bus = await asyncio.create_subprocess_exec(
        "dbus-daemon", "--config-file=@dbusConf@", "--nofork", "--nopidfile",
        "--address=" + os.environ["DBUS_SESSION_BUS_ADDRESS"], stdin=DEVNULL, stdout=DEVNULL)
    # Sway sees no parent display or IPC endpoint. Discover only its private sockets.
    sway = await asyncio.create_subprocess_exec("sway", "-c", HERE / "sway.conf",
                                                stdin=DEVNULL, stdout=DEVNULL, stderr=PIPE)
    tail = bytearray()

    async def drain_sway():
        while chunk := await sway.stderr.read(4096):
            tail.extend(chunk)
            del tail[:-2048]

    drain = asyncio.create_task(drain_sway())
    try:
        async with asyncio.timeout(10):
            while True:
                if sway.returncode is not None or bus.returncode is not None:
                    raise RuntimeError(f"private desktop exited (sway={sway.returncode}, dbus={bus.returncode})")
                displays = [p for p in RUNTIME.glob("wayland-*") if p.is_socket()]
                ipc = [p for p in RUNTIME.glob("sway-ipc.*.sock") if p.is_socket()]
                if len(displays) == len(ipc) == 1 and (RUNTIME / "bus").is_socket():
                    os.environ.update(WAYLAND_DISPLAY=displays[0].name, SWAYSOCK=str(ipc[0]))
                    await outputs()
                    break
                await asyncio.sleep(0.05)
    except Exception as exc:
        raise RuntimeError(f"private desktop startup failed: {exc}; {tail.decode(errors='replace')}") from exc
    broker = Broker()
    async with await asyncio.start_unix_server(broker.client, sock=listener, limit=MAX_REQUEST):
        watchers = [asyncio.create_task(p.wait()) for p in (bus, sway)]
        watchers.append(asyncio.create_task(broker.stopping.wait()))
        await asyncio.wait(watchers, return_when=asyncio.FIRST_COMPLETED)
        for task in watchers:
            task.cancel()
    drain.cancel()
    # Returning exits the namespace's main process. Bubblewrap's PID 1 then
    # exits, which tears down every remaining descendant, including daemonized apps.


if __name__ == "__main__":
    asyncio.run(serve())
