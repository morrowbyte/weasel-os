"""weasel-workspace host CLI. The only input backend is the private broker."""
import argparse
import base64
import concurrent.futures
import contextlib
import fcntl
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import stat
import sys
import tempfile
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
from actions import MAX_REPLY, MAX_REQUEST, MAX_SECONDS, identifier, validate_batch
from sandbox import exchange_dir, spawn


@contextlib.contextmanager
def locked(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
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


def save(path, value):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
        try:
            f.write(json.dumps(value).encode())
            f.flush()
            os.replace(f.name, path)
        finally:
            Path(f.name).unlink(missing_ok=True)


def new_gate(paused):
    return {"paused": paused, "epoch": uuid.uuid4().hex}


def base_dir():
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", ""))
    if not runtime.is_absolute():
        raise ValueError("XDG_RUNTIME_DIR must be an absolute private directory")
    base = private_dir(private_dir(runtime, create=False) / "weasel-workspace")
    gate = private_dir(base / "gate")
    with locked(gate):
        if not (gate / "state").exists():
            save(gate / "state", new_gate(False))
    return base


class Paths:
    def __init__(self, base, name):
        self.name = identifier(name)
        self.base = base
        self.directory = private_dir(base / ("ws-" + name))
        self.sock = self.directory / "control"
        self.live = self.directory / "live"
        self.options = self.directory / "options"

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


def rpc(paths, request, timeout=MAX_SECONDS + 5):
    line = json.dumps(request, ensure_ascii=False).encode() + b"\n"
    if len(line) > MAX_REQUEST:
        raise ValueError("request exceeds 64 KiB")
    with socket.socket(socket.AF_UNIX) as peer:
        peer.settimeout(timeout)
        peer.connect(str(paths.sock))
        try:
            peer.sendall(line)
            with peer.makefile("rb") as stream:
                line = stream.readline(MAX_REPLY + 1)
            if not line.endswith(b"\n") or len(line) > MAX_REPLY:
                raise ValueError("incomplete or oversized workspace reply")
            reply = json.loads(line)
            if not isinstance(reply, dict) or type(reply.get("ok")) is not bool:
                raise ValueError("invalid workspace reply")
            if not reply["ok"] and not isinstance(reply.get("error"), str):
                raise ValueError("missing error in workspace reply")
        except (OSError, ValueError) as exc:
            # Once sending begins the peer might have run any prefix.
            raise BrokerError({"ok": False, "error": str(exc)}) from exc
    if not reply["ok"]:
        raise BrokerError(reply)
    del reply["ok"]
    return reply


def program(value):
    if not isinstance(value, str) or not value:
        raise ValueError("expected an executable")
    found = shutil.which(value)
    if not found:
        raise ValueError(f"executable not found: {value}")
    path = Path(found).absolute()
    # Keep the store symlink's basename for multicall programs.
    for _ in range(40):
        path = path.parent.resolve(strict=True) / path.name
        if path.is_relative_to("/nix/store") and path.resolve(strict=True).is_relative_to("/nix/store"):
            return str(path)
        if not path.is_symlink():
            break
        path = path.parent / path.readlink()
    raise ValueError(f"open requires an executable in the read-only Nix store: {value}")


def start(paths, args):
    with locked(paths.directory):
        options = {"exchange": str(Path(args.exchange).absolute()) if args.exchange else None,
                   "network": args.network}
        if paths.running():
            actual = json.loads(paths.options.read_bytes())
            if args.exchange and str(Path(args.exchange).resolve()) != actual["exchange"] or args.network and not actual["network"]:
                raise ValueError("already running with different rights; stop before changing them")
            return {**rpc(paths, {"op": "status"}), **actual}
        if args.exchange:
            options["exchange"] = str(exchange_dir(args.exchange))
        result = spawn(paths, options, lambda: rpc(paths, {"op": "status"}, timeout=13))
        save(paths.options, options)
        return {**result, **options}


def stop(paths):
    with locked(paths.directory):
        if paths.running():
            rpc(paths, {"op": "stop"}, timeout=5)
            deadline = time.monotonic() + 5
            while paths.running():
                if time.monotonic() >= deadline:
                    raise TimeoutError("namespace still alive after stop")
                time.sleep(0.025)
        paths.sock.unlink(missing_ok=True)
        paths.options.unlink(missing_ok=True)
        paths.live.unlink(missing_ok=True)
        return {"running": False}


def global_gate(base, pause):
    directory = base / "gate"
    with locked(directory):
        state = json.loads((directory / "state").read_bytes())
        if state["paused"] != pause:
            save(directory / "state", new_gate(pause))
    if not pause:
        return {"paused": False}
    # The gate is already closed. Each broker also watches it independently.
    paths = [Paths(base, p.name[3:]) for p in base.glob("ws-*") if p.is_dir()]
    live = [p for p in paths if p.running()]

    def interrupt(p):
        try:
            return {"name": p.name, **rpc(p, {"op": "gate"}, timeout=3)}
        except (OSError, ValueError, RuntimeError) as exc:
            return {"name": p.name, "error": str(exc)} if p.running() else {"name": p.name, "stopped": True}

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        return {"paused": True, "workspaces": list(pool.map(interrupt, live))}


def claim_job(paths, job):
    # Consume the caller's id before sending. A lost reply is never replayed.
    with contextlib.closing(sqlite3.connect(paths.base / "jobs.sqlite3")) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS jobs(workspace TEXT, job TEXT, PRIMARY KEY(workspace, job))")
        try:
            db.execute("INSERT INTO jobs VALUES (?, ?)", (paths.name, job))
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"job {job} was already submitted") from exc


def screenshot(paths, target):
    data = base64.b64decode(rpc(paths, {"op": "screenshot"})["png"], validate=True)
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise RuntimeError("expected PNG")
    # Publish a complete new file without following or overwriting a destination.
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as f:
        try:
            f.write(data)
            f.flush()
            os.link(f.name, target)
        finally:
            Path(f.name).unlink(missing_ok=True)
    return {"path": str(target.absolute()), "bytes": len(data)}


def execute(args):
    base = base_dir()
    op = args.command
    if op in ("cancel-all", "resume"):
        return global_gate(base, op == "cancel-all")
    paths = Paths(base, args.name)
    if op == "start":
        return start(paths, args)
    if op == "stop":
        return stop(paths)
    if op == "status":
        with locked(paths.directory):
            result = (rpc(paths, {"op": op}, timeout=4) | json.loads(paths.options.read_bytes())
                      if paths.running() else {"running": False})
            return {**result, "paused": json.loads((base / "gate/state").read_bytes())["paused"]}
    if not paths.running():
        raise RuntimeError("workspace is stopped")
    if op == "screenshot":
        return screenshot(paths, Path(args.file))
    if op == "cancel":
        return rpc(paths, {"op": op, "job": identifier(args.job)})
    gate = json.loads((base / "gate/state").read_bytes())
    if gate["paused"]:
        raise RuntimeError("input paused; explicitly resume before starting a new action")
    if op == "batch":
        job = identifier(args.job)
        raw = sys.stdin.buffer.read(MAX_REQUEST + 1)
        if len(raw) > MAX_REQUEST:
            raise ValueError("batch exceeds 64 KiB")
        actions = json.loads(raw)
    else:
        job = uuid.uuid4().hex
        values = args.values
        if values[:1] == ["--"]:
            values = values[1:]
        actions = [[op, *values]]
    if isinstance(actions, list):
        for action in actions:
            if isinstance(action, list) and len(action) > 1 and action[0] == "open":
                action[1] = program(action[1])
    actions = validate_batch(actions)
    typing_mode = os.environ.get("WEASEL_WORKSPACE_TYPING", "fast")
    if typing_mode not in ("fast", "upstream"):
        raise ValueError("WEASEL_WORKSPACE_TYPING must be fast or upstream")
    if op == "batch":
        claim_job(paths, job)
    return rpc(paths, {"op": "batch", "actions": actions, "epoch": gate["epoch"], "job": job,
                       "typing_mode": typing_mode})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="default", help="private workspace name")
    commands = parser.add_subparsers(dest="command", required=True)
    start_cmd = commands.add_parser("start")
    start_cmd.add_argument("--exchange", metavar="DIR")
    start_cmd.add_argument("--network", action="store_true")
    for name in ("status", "stop", "cancel-all", "resume"):
        commands.add_parser(name)
    for name in ("batch", "cancel"):
        commands.add_parser(name).add_argument("job")
    commands.add_parser("screenshot").add_argument("file")
    for name, description in (("open", "-- PROGRAM [ARG ...]"), ("key", "CHORD"), ("text", "TEXT"),
                              ("wait-window", "APP_ID"), ("click", "X Y [BUTTON]"),
                              ("scroll", "X Y DIRECTION [STEPS]")):
        commands.add_parser(name, usage=f"%(prog)s {description}").add_argument("values", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        result = execute(args)
    except BrokerError as exc:
        print(json.dumps(exc.reply), file=sys.stderr)
        return 1
    except (ValueError, RuntimeError, OSError, sqlite3.Error) as exc:
        progress = {"attempted": 0, "results": []} if args.command == "batch" else {}
        print(json.dumps({"ok": False, "error": str(exc), **progress}), file=sys.stderr)
        return 1
    ok = not any("error" in item for item in result.get("workspaces", []))
    print(json.dumps({"ok": ok, **result}, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
