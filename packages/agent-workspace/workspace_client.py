"""Workspace lifetime paths and the closed, cancellable client for exchange requests."""
import asyncio
import contextlib
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import time
import uuid

from exchange import validate

NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,31}")


def identifier(value):
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise ValueError("expected a 1–32 character name (letters, digits, hyphen, underscore)")
    return value


@contextlib.contextmanager
def locked(path, *, nonblocking=False):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblocking else 0))
        yield
    finally:
        os.close(fd)


def private_dir(path, create=True):
    if create:
        path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError(f"expected private directory owned by current user: {path}")
    return path


def runtime_base(*, create):
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", ""))
    if not runtime.is_absolute():
        raise ValueError("XDG_RUNTIME_DIR must be an absolute private directory")
    return private_dir(private_dir(runtime, create=False) / "weasel-workspace", create=create)


class Paths:
    def __init__(self, base, name, *, create=True):
        self.name = identifier(name)
        self.base = base
        self.directory = private_dir(base / ("ws-" + self.name), create=create)
        self.sock = self.directory / "control"
        self.live = self.directory / "live"
        self.options = self.directory / "options"

    def start_options(self):
        # Sessions launched before profile support have a temporary home.
        return {"persistent": False, **json.loads(self.options.read_bytes())}

    def running(self):
        try:
            with self.live.open("rb") as f:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except FileNotFoundError:
            pass
        except BlockingIOError:
            return True
        return False


class BrokerError(RuntimeError):
    def __init__(self, reply):
        super().__init__(reply["error"])
        self.reply = reply


async def connect_exchange(paths):
    # Wait for start/stop without blocking the caller's event loop. The socket
    # is connected before the lock is released, binding this request to that broker.
    while True:
        try:
            with locked(paths.directory, nonblocking=True):
                if not paths.running():
                    raise BrokerError({"ok": False, "error": "workspace is stopped"})
                return await asyncio.open_unix_connection(paths.sock, limit=256 << 10)
        except BlockingIOError:
            await asyncio.sleep(0.01)


async def exchange_request(name, operation, names, session):
    validate(operation, names)
    if session is not None and (not isinstance(session, str) or not re.fullmatch(r"[0-9a-f]{32}", session)):
        raise ValueError("invalid exchange session identifier")
    started = time.monotonic_ns()
    try:
        base = runtime_base(create=False)
        paths = Paths(base, name, create=False)
        gate = json.loads((private_dir(base / "gate", create=False) / "state").read_bytes())
    except FileNotFoundError as exc:
        raise BrokerError({"ok": False, "error": "workspace is stopped"}) from exc
    request = {"op": operation, "names": names, "session": session,
               "epoch": gate["epoch"], "job": uuid.uuid4().hex}
    submitted = False
    try:
        async with asyncio.timeout(35):
            reader, writer = await connect_exchange(paths)
            try:
                line = json.dumps(request).encode() + b"\n"
                connected = time.monotonic_ns()
                submitted = True
                writer.write(line)
                await writer.drain()
                line = await reader.readline()
                if not line.endswith(b"\n"):
                    raise ValueError("incomplete workspace reply; effect unconfirmed")
                reply = json.loads(line)
                if not isinstance(reply, dict) or type(reply.get("ok")) is not bool:
                    raise ValueError("invalid workspace reply; effect unconfirmed")
                if not isinstance(reply.get("timing_ns", {}), dict):
                    raise ValueError("invalid exchange timing receipt")
                if not reply["ok"] and not isinstance(reply.get("error"), str):
                    raise ValueError("missing broker diagnostic")
                elapsed = time.monotonic_ns() - connected
            finally:
                writer.close()
                await writer.wait_closed()
    except (asyncio.CancelledError, OSError, ValueError) as exc:
        error = str(exc) or type(exc).__name__
        raise BrokerError({"ok": False, "error": error, "job": request["job"],
                           "effect": "unknown" if submitted else "not_started"}) from exc
    timing = reply.setdefault("timing_ns", {})
    timing.update(client_admission=connected - started, client_roundtrip=elapsed)
    if not reply["ok"]:
        raise BrokerError(reply)
    return reply
