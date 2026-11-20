"""weasel-workspace host CLI. The only input backend is the private broker."""
import argparse
import asyncio
import base64
import concurrent.futures
import contextlib
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import sys
import tempfile
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
from actions import MAX_REPLY, MAX_REQUEST, MAX_SECONDS, identifier, validate_batch
from exchange import OPERATIONS
from presentation import open_window
from sandbox import exchange_dir, profile_dir, spawn
from workspace_client import BrokerError, Paths, exchange_request, locked, private_dir, runtime_base


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
    base = runtime_base(create=True)
    gate = private_dir(base / "gate")
    with locked(gate):
        if not (gate / "state").exists():
            save(gate / "state", new_gate(False))
    return base


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
                   "network": args.network, "persistent": args.persistent}
        if paths.running():
            actual = paths.start_options()
            if args.persistent and not actual["persistent"]:
                raise ValueError("already running with a temporary home; stop before changing profiles")
            if (args.exchange and str(Path(args.exchange).resolve()) != actual["exchange"]
                    or args.network and not actual["network"]):
                raise ValueError("already running with different rights; stop before changing them")
            request = {"op": "status"}
            if args.exchange:
                info = os.stat(args.exchange, follow_symlinks=False)
                request["verify_exchange"] = [info.st_dev, info.st_ino]
            if args.persistent:
                with profile_dir(paths.name, create=False) as fd:
                    info = os.fstat(fd)
                    request["verify_profile"] = [info.st_dev, info.st_ino]
            status = rpc(paths, request)
            if args.persistent and not status.pop("profile_verified", False):
                raise ValueError("running broker cannot confirm profile identity")
            if args.exchange and not status.pop("exchange_verified", False):
                raise ValueError("running broker cannot confirm exchange identity; stop before changing rights")
            return {**status, **actual}
        profile = profile_dir(paths.name) if args.persistent else contextlib.nullcontext(None)
        with profile as profile_fd, exchange_dir(options["exchange"]) as (path, fd):
            options["exchange"] = path
            save(paths.options, options)
            result = spawn(paths, options, fd, lambda: rpc(paths, {"op": "status"}, timeout=13),
                           profile_fd=profile_fd)
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
    op = args.command
    if op in OPERATIONS:
        names = [getattr(args, field) for field in OPERATIONS[op]]
        return asyncio.run(exchange_request(args.name, op, names, args.session))
    base = base_dir()
    if op in ("cancel-all", "resume"):
        return global_gate(base, op == "cancel-all")
    paths = Paths(base, args.name)
    if op == "start":
        return start(paths, args)
    if op == "stop":
        return stop(paths)
    if op == "status":
        with locked(paths.directory):
            result = (rpc(paths, {"op": op}, timeout=4) | paths.start_options()
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
    if op == "view":
        open_window(paths, gate["epoch"])
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
    start_cmd.add_argument("--persistent", action="store_true", help="reuse this name's private home across sessions")
    for name in ("status", "view", "stop", "cancel-all", "resume"):
        commands.add_parser(name)
    for name in ("batch", "cancel"):
        commands.add_parser(name).add_argument("job")
    for operation, fields in OPERATIONS.items():
        command = commands.add_parser(operation)
        command.add_argument("--session", help="require the same running exchange session")
        for field in fields:
            command.add_argument(field)
    commands.add_parser("screenshot").add_argument("file")
    for name, description in (("open", "-- PROGRAM [ARG ...]"), ("key", "CHORD"), ("text", "TEXT"),
                              ("wait-window", "APP_ID"), ("click", "X Y [BUTTON]"),
                              ("move", "X Y"), ("drag", "START_X START_Y END_X END_Y [BUTTON]"),
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
