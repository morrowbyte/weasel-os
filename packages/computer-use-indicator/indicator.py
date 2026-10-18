#!/usr/bin/env python3
"""Owned presentation lease. This helper never captures, focuses or inputs.

The Rust actor is the authority for input and cancellation. The child renderer
only draws five noninteractive layer surfaces and expires without heartbeats.
"""

import argparse
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import secrets
import signal
import socket
import stat
import struct
import subprocess
import sys
import time

REVISION = 1
POLL_SECONDS = 0.1
STATUS_TIMEOUT_SECONDS = 0.3
MAX_STATUS_BYTES = 16 * 1024
LIFECYCLE_REVISION = 3
STATUS_FRESH_SECONDS = POLL_SECONDS + STATUS_TIMEOUT_SECONDS
IDENTIFIER = re.compile(r"[A-Za-z0-9_.-]{1,128}\Z")


class CapabilityError(RuntimeError):
    pass


class LifecycleTimeout(CapabilityError):
    """Only a deadline expiry permits one bounded lifecycle retry."""
    pass


def emit(event, **fields):
    print(json.dumps({"revision": REVISION, "event": event, "monotonic": time.monotonic(), **fields}, ensure_ascii=False), flush=True)


def process_start_ticks(pid):
    # /proc stat comm can contain spaces or parentheses. Fields after the final
    # ')' start at #3 (state); process starttime is #22.
    process = Path(f"/proc/{pid}")
    if process.stat().st_uid != os.getuid():
        raise CapabilityError("owner process is not owned by the current user")
    raw = (process / "stat").read_text()
    fields = raw[raw.rfind(")") + 2 :].split()
    if fields[0] in ("Z", "X", "x"):
        raise ProcessLookupError("owner process exited")
    return int(fields[19])


def runtime_dir():
    base = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    info = base.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise CapabilityError("private XDG_RUNTIME_DIR unavailable")
    directory = base / "weasel-computer-use" / "indicators"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    for check in (directory.parent, directory):
        info = check.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise CapabilityError("private indicator runtime directory unavailable")
    return directory


def task_paths(task_id):
    token = hashlib.sha256(task_id.encode()).hexdigest()[:20]
    directory = runtime_dir()
    return directory / f"task-{token}.sock", directory / f"task-{token}.json"


def read_line(stream, limit, deadline=None):
    timeout = stream.gettimeout()
    deadline = deadline if deadline is not None else time.monotonic() + (timeout if timeout is not None else STATUS_TIMEOUT_SECONDS)
    buffer = bytearray()
    while len(buffer) <= limit:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise LifecycleTimeout("response deadline elapsed")
        stream.settimeout(remaining)
        chunk = stream.recv(min(65536, limit + 1 - len(buffer)))
        if not chunk:
            raise CapabilityError("connection closed before response")
        buffer.extend(chunk)
        if b"\n" in buffer:
            line, _, remainder = buffer.partition(b"\n")
            if remainder:
                raise CapabilityError("unexpected extra response data")
            return json.loads(line)
    raise CapabilityError("response exceeds bounded size")


def parse_lifecycle(result):
    if not isinstance(result, dict) or result.get("isError") is not False:
        raise CapabilityError("actor rejected lifecycle request")
    content = result.get("content")
    if not isinstance(content, list) or len(content) != 1 or not isinstance(content[0], dict):
        raise CapabilityError("actor lifecycle contract unavailable")
    item = content[0]
    if item.get("type") != "text" or not isinstance(item.get("text"), str):
        raise CapabilityError("actor lifecycle contract unavailable")
    status = json.loads(item["text"])
    if not isinstance(status, dict) or status.get("schema") != 1 or type(status.get("schema")) is not int:
        raise CapabilityError("actor lifecycle contract unavailable")
    if type(status.get("lifecycle_revision")) is not int or status["lifecycle_revision"] != LIFECYCLE_REVISION or status.get("complete") is not True:
        raise CapabilityError("actor lifecycle revision/completeness unavailable")
    if not isinstance(status.get("session_id"), str) or not status["session_id"]:
        raise CapabilityError("actor lifecycle identity unavailable")
    if any(type(status.get(key)) is not int or status[key] < 0 for key in ("epoch", "queued_batches")):
        raise CapabilityError("actor lifecycle counters unavailable")
    if any(type(status.get(key)) is not bool for key in ("takeover_latched", "actor_active", "actor_release_confirmed", "task_scope_active")):
        raise CapabilityError("actor lifecycle cancellation/release unavailable")
    return status


def actor_call(actor_socket, tool, arguments):
    # Startup fails closed without a renderer. Total connect/send/read budget,
    # not a fresh timeout for every chunk or phase; no full-status fallback.
    deadline = time.monotonic() + STATUS_TIMEOUT_SECONDS
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
        try:
            stream.settimeout(max(0.001, deadline - time.monotonic()))
            stream.connect(str(actor_socket))
            stream.settimeout(max(0.001, deadline - time.monotonic()))
            stream.sendall(json.dumps({"tool": tool, "arguments": arguments}, separators=(",", ":")).encode() + b"\n")
            result = read_line(stream, MAX_STATUS_BYTES, deadline)
        except TimeoutError as error:
            raise LifecycleTimeout("lifecycle startup deadline elapsed") from error
    return result


def desktop_lifecycle(actor_socket, task_id=None, event="status", binding=None, scope_token=None, owner_processes=None):
    arguments = {"event": event}
    if task_id:
        arguments["task_id"] = task_id
    if scope_token is not None:
        arguments["scope_token"] = scope_token
    if binding is not None:
        arguments["session_id"] = binding["session_id"]
        arguments["expected_epoch"] = binding["epoch"]
    if owner_processes is not None:
        arguments["owner_processes"] = owner_processes
    result = actor_call(actor_socket, "desktop_indicator_lifecycle", arguments)
    return parse_lifecycle(result)


class LifecycleProbe:
    """Exactly one nonblocking read; main-loop end/owner events remain reachable."""
    def __init__(self, selector, actor_socket, task_id, binding, scope_token):
        self.selector = selector
        self.stream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.stream.setblocking(False)
        self.started = time.monotonic()
        self.deadline = self.started + STATUS_TIMEOUT_SECONDS
        self.sent = 0
        self.buffer = bytearray()
        self.request = json.dumps({"tool":"desktop_indicator_lifecycle","arguments":{"event":"status","task_id":task_id,"session_id":binding["session_id"],"expected_epoch":binding["epoch"],"scope_token":scope_token}}, separators=(",", ":")).encode() + b"\n"
        self.closed = False
        try:
            result = self.stream.connect_ex(str(actor_socket))
            if result not in (0, errno.EINPROGRESS, errno.EAGAIN, errno.EALREADY):
                raise OSError(result, "actor lifecycle connection unavailable")
            selector.register(self.stream, selectors.EVENT_WRITE, self)
        except BaseException:
            self.stream.close()
            self.closed = True
            raise

    def advance(self, mask):
        if time.monotonic() >= self.deadline:
            raise LifecycleTimeout("lifecycle response deadline elapsed")
        if mask & selectors.EVENT_WRITE:
            error = self.stream.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
            if error:
                raise OSError(error, "actor lifecycle connection unavailable")
            try:
                self.sent += self.stream.send(self.request[self.sent:])
            except BlockingIOError:
                return None
            if self.sent == len(self.request):
                self.selector.modify(self.stream, selectors.EVENT_READ, self)
        if mask & selectors.EVENT_READ:
            try:
                chunk = self.stream.recv(min(4096, MAX_STATUS_BYTES + 1 - len(self.buffer)))
            except BlockingIOError:
                return None
            if not chunk:
                raise CapabilityError("connection closed before lifecycle response")
            self.buffer.extend(chunk)
            if len(self.buffer) > MAX_STATUS_BYTES:
                raise CapabilityError("lifecycle response exceeds bounded size")
            if b"\n" in self.buffer:
                line, _, remainder = self.buffer.partition(b"\n")
                if remainder:
                    raise CapabilityError("unexpected extra lifecycle response data")
                return parse_lifecycle(json.loads(line))
        return None

    def close(self):
        if not self.closed:
            self.closed = True
            try:
                self.selector.unregister(self.stream)
            except KeyError:
                pass
            self.stream.close()


def selected_geometry(output):
    niri = os.environ.get("WEASEL_INDICATOR_NIRI", "niri")
    result = subprocess.run([niri, "msg", "--json", "outputs"], capture_output=True, timeout=1, check=True)
    outputs = json.loads(result.stdout)
    if not isinstance(outputs, dict) or output not in outputs:
        raise CapabilityError("selected Niri output unavailable")
    logical = outputs[output].get("logical")
    if not isinstance(logical, dict):
        raise CapabilityError("selected Niri output is disabled")
    geometry = tuple(logical.get(key) for key in ("x", "y", "width", "height"))
    if any(type(value) is not int for value in geometry) or geometry[2] < 320 or geometry[3] < 200:
        raise CapabilityError("selected output logical geometry unavailable")
    return geometry, logical


def status_stop_reason(status, initial):
    if status["session_id"] != initial["session_id"]:
        return "actor_session_changed"
    if status["epoch"] != initial["epoch"]:
        return "actor_cancel_epoch_changed"
    if status["takeover_latched"]:
        return "actor_takeover_latched"
    if not status["task_scope_active"]:
        return "actor_task_scope_expired_or_ended"
    if not status["actor_active"] and not status["actor_release_confirmed"]:
        return "idle_release_unconfirmed"
    return None


def control(args):
    path, _ = task_paths(args.task_id)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
        stream.settimeout(1)
        stream.connect(str(path))
        stream.sendall(json.dumps({"revision": REVISION, "event": args.command, "task_id": args.task_id}).encode() + b"\n")
        response = read_line(stream, 4096)
    emit("control_reply", **response)
    return 0 if response.get("ok") is True else 1


def run(args):
    task_socket, lease_path = task_paths(args.task_id)
    actor_socket = Path(os.environ.get("WEASEL_COMPUTER_USE_SOCKET", str(task_socket.parent.parent / "desktop.sock")))
    owner_pid = args.owner_pid or os.getppid()
    owner_ticks = args.owner_start_ticks if args.owner_start_ticks is not None else process_start_ticks(owner_pid)
    if process_start_ticks(owner_pid) != owner_ticks:
        raise CapabilityError("owner process identity changed before begin")
    initial = desktop_lifecycle(actor_socket)
    if initial["takeover_latched"] or initial["actor_active"] or initial["queued_batches"] != 0:
        raise CapabilityError("actor must be resumed and idle before indicator begin")
    if initial.get("actor_release_confirmed") is not True:
        raise CapabilityError("actor release is unconfirmed")
    geometry, logical = selected_geometry(args.output)
    if task_socket.exists() or lease_path.exists():
        raise CapabilityError("task indicator already exists; no stale owner is replaced")
    renderer_path = os.environ.get("WEASEL_INDICATOR_RENDERER")
    if not renderer_path:
        raise CapabilityError("packaged indicator renderer unavailable")
    directory = runtime_dir()
    output_token = hashlib.sha256(args.output.encode()).hexdigest()[:20]
    lock_path = directory / f"output-{output_token}.lock"
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    server = None
    renderer = None
    selector = selectors.DefaultSelector()
    reason = "initialization_failed"
    started = time.monotonic()
    deadline = None if args.max_runtime_seconds is None else started + args.max_runtime_seconds
    owned_socket_identity = None
    owned_lease_identity = None
    old_signals = {}
    probe = None
    scope_begin_attempted = False
    scope_token = args.scope_token or secrets.token_urlsafe(32)
    stop_signal = [None]
    metadata = {
        "revision": REVISION, "task_id": args.task_id, "scope_token": scope_token, "output": args.output,
        "pid": os.getpid(), "start_ticks": process_start_ticks(os.getpid()),
        "owner_pid": owner_pid, "owner_start_ticks": owner_ticks,
        "actor_session_id": initial["session_id"], "actor_epoch": initial["epoch"],
        "started_monotonic": started, "deadline_monotonic": deadline,
        "geometry": geometry, "control_socket": str(task_socket),
    }
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise CapabilityError("selected output has another indicator owner") from error
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(task_socket))
        os.chmod(task_socket, 0o600)
        owned_socket_identity = task_socket.stat().st_ino
        server.listen(4)
        server.setblocking(False)
        selector.register(server, selectors.EVENT_READ, "control")
        lease_fd = os.open(lease_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        owned_lease_identity = os.fstat(lease_fd).st_ino
        with os.fdopen(lease_fd, "w") as lease:
            json.dump(metadata, lease, ensure_ascii=False)
        renderer = subprocess.Popen(
            [renderer_path, args.output, args.task_id, *(str(value) for value in geometry)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1,
        )
        selector.register(renderer.stdout, selectors.EVENT_READ, "renderer")
        if args.stdin_control:
            os.set_blocking(sys.stdin.fileno(), False)
            selector.register(sys.stdin, selectors.EVENT_READ, "stdin")
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            old_signals[signum] = signal.signal(signum, lambda received, _frame: stop_signal.__setitem__(0, received))
        # Set before sending: the actor can commit begin and then lose the ACK.
        # Finally must still attempt exact-owner cleanup in that ambiguous case.
        scope_begin_attempted = True
        registered = desktop_lifecycle(actor_socket, args.task_id, "begin", initial, scope_token, [
            {"pid": metadata["pid"], "start_ticks": metadata["start_ticks"]},
            {"pid": owner_pid, "start_ticks": owner_ticks},
        ])
        if registered.get("task_scope_lifetime") != "owner_process":
            raise CapabilityError("actor does not support owner-bound task scope lifetime")
        renderer.stdin.write("v1 begin\n")
        renderer.stdin.flush()
        emit("starting", task_id=args.task_id, output=args.output, pid=os.getpid(), geometry=geometry)
        next_poll = next_heartbeat = started
        next_geometry = started + 1
        ready = False
        last_valid_at = time.monotonic()
        timeout_retry = 0
        stdin_pending = bytearray()
        while True:
            now = time.monotonic()
            if stop_signal[0] is not None:
                reason = "owner_signal"
                break
            if deadline is not None and now >= deadline:
                reason = "maximum_runtime_expired"
                break
            if not ready and now - started > 4:
                reason = "renderer_readiness_timeout"
                break
            if renderer.poll() is not None:
                reason = "renderer_exited"
                break
            try:
                if process_start_ticks(owner_pid) != owner_ticks:
                    reason = "owner_process_changed"
                    break
            except (FileNotFoundError, ProcessLookupError):
                reason = "owner_process_exited"
                break
            # Socket I/O is part of this selector, not a synchronous full-status
            # call. Local end/stdin events are processed before lifecycle data.
            timeout = POLL_SECONDS if deadline is None else min(POLL_SECONDS, max(0, deadline - time.monotonic()))
            if probe is not None:
                timeout = min(timeout, max(0, probe.deadline - time.monotonic()))
            probe_error = None
            fresh_status = None
            events = selector.select(timeout=timeout)
            events.sort(key=lambda item: 0 if item[0].data in ("control", "stdin") else 1)
            for key, _mask in events:
                if key.data == "control":
                    with server.accept()[0] as client:
                        client.settimeout(0.05)
                        try:
                            _pid, uid, _gid = struct.unpack("3i", client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                            request = read_line(client, 4096)
                            valid = uid == os.getuid() and request.get("revision") == REVISION and request.get("task_id") == args.task_id
                            event = request.get("event")
                            response = {"ok": valid and event in ("status", "end"), "task_id": args.task_id, "ready": ready}
                            client.sendall(json.dumps(response).encode() + b"\n")
                            if valid and event == "end":
                                reason = "explicit_end"
                                stop_signal[0] = 0
                        except (OSError, ValueError, CapabilityError):
                            pass  # Invalid local control does not renew the renderer lease.
                elif isinstance(key.data, LifecycleProbe):
                    if stop_signal[0] == 0:
                        continue
                    try:
                        fresh_status = key.data.advance(_mask)
                    except (OSError, ValueError, CapabilityError) as error:
                        probe_error = error
                elif key.data == "renderer":
                    line = renderer.stdout.readline()
                    if not line or len(line) > 4096:
                        reason = "renderer_pipe_closed"
                        stop_signal[0] = 0
                        continue
                    event = json.loads(line)
                    if event.get("revision") == REVISION and event.get("event") == "ready":
                        if event.get("task_id") != args.task_id or event.get("output") != args.output:
                            raise CapabilityError("renderer identity differs from owned task")
                        ready = True
                        emit("ready", task_id=args.task_id, output=args.output, surfaces=event.get("surfaces"), geometry=geometry)
                    elif event.get("event") == "ended":
                        reason = "renderer_" + str(event.get("reason", "ended"))
                        stop_signal[0] = 0
                elif key.data == "stdin":
                    chunk = os.read(sys.stdin.fileno(), 4096)
                    if not chunk:
                        reason = "owner_stdin_closed"
                        stop_signal[0] = 0
                    else:
                        stdin_pending.extend(chunk)
                        if len(stdin_pending) > 4096:
                            reason = "invalid_stdin_control"
                            stop_signal[0] = 0
                        while b"\n" in stdin_pending:
                            line, _, tail = stdin_pending.partition(b"\n")
                            stdin_pending[:] = tail
                            reason = "explicit_end" if line == b"v1 end" else "invalid_stdin_control"
                            stop_signal[0] = 0
            if stop_signal[0] == 0:
                break
            # Recheck owner/signal before renewing any lease, even if a lifecycle
            # response and an owner stop arrived in the same selector iteration.
            if stop_signal[0] is not None:
                reason = "owner_signal"
                break
            try:
                if process_start_ticks(owner_pid) != owner_ticks:
                    reason = "owner_process_changed"
                    break
            except (FileNotFoundError, ProcessLookupError):
                reason = "owner_process_exited"
                break
            now = time.monotonic()
            if probe is not None and probe_error is None and fresh_status is None and now >= probe.deadline:
                probe_error = LifecycleTimeout("lifecycle response deadline elapsed")
            if probe_error is not None:
                request_started = probe.started
                probe.close()
                probe = None
                last_valid_at = None
                if isinstance(probe_error, LifecycleTimeout) and timeout_retry == 0:
                    timeout_retry = 1
                    next_poll = now
                    emit("lifecycle_timeout", retry=1, heartbeat_renewed=False, request_started_monotonic=request_started, request_elapsed_ms=round((now - request_started) * 1000, 2))
                else:
                    raise probe_error
            elif fresh_status is not None:
                probe.close()
                probe = None
                reason = status_stop_reason(fresh_status, initial)
                if reason:
                    break
                last_valid_at = now
                timeout_retry = 0
                next_poll = now + POLL_SECONDS
            if probe is None and now >= next_poll:
                probe = LifecycleProbe(selector, actor_socket, args.task_id, initial, scope_token)
                last_valid_at = None  # no renewal while authority is unknown
            if probe is None and now >= next_geometry:
                new_geometry, new_logical = selected_geometry(args.output)
                if new_geometry != geometry or new_logical != logical:
                    reason = "output_geometry_changed"
                    break
                next_geometry = time.monotonic() + 1
                # Geometry remains an independently bounded subprocess. Re-enter
                # owner/control checks before any renewal after that wait.
                continue
            now = time.monotonic()
            if deadline is not None and now >= deadline:
                reason = "maximum_runtime_expired"
                break
            if probe is None and last_valid_at is not None and now - last_valid_at <= STATUS_FRESH_SECONDS and now >= next_heartbeat:
                renderer.stdin.write("v1 heartbeat\n")
                renderer.stdin.flush()
                next_heartbeat = now + 1
        return 0 if reason in ("explicit_end", "owner_signal", "actor_cancel_epoch_changed", "actor_takeover_latched") else 1
    except (OSError, subprocess.SubprocessError, ValueError, CapabilityError) as error:
        reason = "capability_unavailable"
        # Do not print actor responses, window titles, environment or raw argv.
        emit("error", kind=type(error).__name__)
        return 1
    finally:
        cleanup_started = time.monotonic()
        if probe is not None:
            probe.close()
        if scope_begin_attempted:
            try:
                ended = desktop_lifecycle(actor_socket, args.task_id, "end", initial, scope_token)
                if ended["task_scope_active"]:
                    raise CapabilityError("actor did not confirm exact task scope end")
                emit("task_scope_ended", task_id=args.task_id, session_id=initial["session_id"], epoch=initial["epoch"])
            except (OSError, ValueError, CapabilityError):
                # The actor-side task scope remains fail-closed; never report an
                # idle/ended task when exact ownership cleanup was uncertain.
                emit("task_scope_end_unconfirmed", task_id=args.task_id, session_id=initial["session_id"], epoch=initial["epoch"], fail_closed=True)
        # Closing the owned pipe hides the renderer even if writing end fails.
        if renderer:
            try:
                renderer.stdin.write("v1 end\n")
                renderer.stdin.flush()
            except (OSError, BrokenPipeError):
                pass
            try:
                renderer.stdin.close()
            except OSError:
                pass
            try:
                renderer.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                renderer.terminate()
                try:
                    renderer.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    renderer.kill()
                    renderer.wait(timeout=0.5)
            renderer.stdout.close()
        selector.close()
        if server:
            server.close()
        for path, inode in ((task_socket, owned_socket_identity), (lease_path, owned_lease_identity)):
            try:
                if inode is not None and path.lstat().st_ino == inode:
                    path.unlink()
            except FileNotFoundError:
                pass
        os.close(lock_fd)
        for signum, previous in old_signals.items():
            signal.signal(signum, previous)
        emit(
            "ended", task_id=args.task_id, output=args.output, reason=reason,
            renderer_exit_confirmed=renderer is None or renderer.poll() is not None,
            cleanup_ms=round((time.monotonic() - cleanup_started) * 1000, 2),
            elapsed_ms=round((time.monotonic() - started) * 1000, 2),
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    runner = commands.add_parser("run", help="foreground task-owned indicator; use an owned exec session")
    runner.add_argument("--task-id", required=True)
    runner.add_argument("--scope-token", help="ownership token returned by desktop_resume for physical-Escape re-arm")
    runner.add_argument("--output", required=True)
    runner.add_argument("--owner-pid", type=int)
    runner.add_argument("--owner-start-ticks", type=int)
    runner.add_argument("--stdin-control", action="store_true", help="EOF or v1 end on stdin also ends the indicator; use a PTY/held pipe")
    runner.add_argument("--max-runtime-seconds", type=int, help="optional explicit lifetime bound; otherwise follows the task owner")
    for name in ("status", "end"):
        command = commands.add_parser(name)
        command.add_argument("--task-id", required=True)
    args = parser.parse_args()
    if not IDENTIFIER.fullmatch(args.task_id):
        parser.error("task ID must contain 1–128 ASCII letters, digits, dots, dashes or underscores")
    if args.command == "run":
        if not IDENTIFIER.fullmatch(args.output):
            parser.error("invalid output identifier")
        if args.max_runtime_seconds is not None and not 1 <= args.max_runtime_seconds <= sys.maxsize:
            parser.error("explicit maximum runtime must be a positive representable number of seconds")
        if args.owner_pid is not None and (args.owner_pid < 1 or args.owner_start_ticks is None):
            parser.error("explicit owner PID requires its exact --owner-start-ticks")
        if args.owner_start_ticks is not None and args.owner_pid is None:
            parser.error("--owner-start-ticks requires --owner-pid")
    try:
        return run(args) if args.command == "run" else control(args)
    except (OSError, ValueError, subprocess.SubprocessError, CapabilityError) as error:
        emit("error", kind=type(error).__name__, detail=str(error) if isinstance(error, CapabilityError) else "indicator capability unavailable")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
