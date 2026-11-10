"""Optional presentation feed for the task-owned indicator.

The bridge cannot set phase, observation policy, visibility or input. Losing
Crew removes its bubble; it never ends a Computer Use task or renews its lease.
"""
import base64
import json
import os
from pathlib import Path
import socket
import stat
import struct
import queue
import threading
import time

from pet_protocol import MAX_LINE, ProtocolError, TOKEN, validate_pack


def render_commands(commands):
    if not isinstance(commands, list) or len(commands) > 6:
        raise ProtocolError("invalid_render_projection")
    checked = []
    for command in commands:
        if not isinstance(command, str) or len(command) > MAX_LINE or "\n" in command or "\r" in command:
            raise ProtocolError("invalid_render_projection")
        parts = command.split(" ")
        valid = command in ("v1 bubble clear", "v1 motion full", "v1 motion reduced", "v1 asset default")
        if len(parts) == 3 and parts[:2] in (["v1", "working"], ["v1", "count"]):
            valid = parts[2].isascii() and parts[2].isdigit() and 0 <= int(parts[2]) <= 10000
        elif len(parts) == 5 and parts[:2] == ["v1", "bubble"]:
            summary = base64.b64decode(parts[4], validate=True).decode("utf-8")
            from pet_protocol import text
            text(summary, 240)
            valid = parts[2] in ("approval", "question", "result") and bool(TOKEN.fullmatch(parts[3]))
        elif len(parts) == 5 and parts[:2] == ["v1", "asset"]:
            path = Path(base64.b64decode(parts[2], validate=True).decode("utf-8"))
            pack = validate_pack(str(path.parent))
            valid = pack["sheet"] == str(path) and str(pack["frameWidth"]) == parts[3] and str(pack["frameHeight"]) == parts[4]
        if not valid:
            raise ProtocolError("forbidden_render_command")
        checked.append(command)
    return checked


class PetBridge:
    def __init__(self, stream):
        self.stream = stream
        self.pending = bytearray()

    @classmethod
    def attach(cls, directory, task_id, binding, scope_token):
        path = directory / "pet.sock"
        stream = None
        try:
            info = path.lstat()
            if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                return None
            stream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            deadline = time.monotonic() + 0.35
            stream.settimeout(0.35)
            stream.connect(str(path))
            _, uid, _ = struct.unpack("3i", stream.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if uid != os.getuid():
                stream.close()
                return None
            stream.settimeout(max(0.001, deadline - time.monotonic()))
            stream.sendall(json.dumps({"v": 1, "type": "attach", "taskId": task_id,
                "sessionId": binding["session_id"], "epoch": binding["epoch"], "scopeToken": scope_token}).encode() + b"\n")
            # Do not consume partial projections after the acknowledgement.
            pending = bytearray()
            while b"\n" not in pending and len(pending) <= MAX_LINE:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("bridge attachment deadline elapsed")
                stream.settimeout(remaining)
                chunk = stream.recv(1)
                if not chunk:
                    raise OSError("bridge closed")
                pending.extend(chunk)
            if json.loads(pending) != {"v": 1, "type": "attached"}:
                raise OSError("bridge rejected attachment")
            stream.setblocking(False)
            return cls(stream)
        except (OSError, ValueError):
            if stream:
                stream.close()
            return None

    def read(self):
        try:
            chunk = self.stream.recv(4096)
        except BlockingIOError:
            chunk = None
        if chunk == b"":
            raise OSError("bridge closed")
        if chunk is not None:
            self.pending.extend(chunk)
        commands = []
        while b"\n" in self.pending:
            line, _, tail = self.pending.partition(b"\n")
            self.pending[:] = tail
            if len(line) > MAX_LINE:
                raise ProtocolError("bridge_message_too_large")
            message = json.loads(line)
            if not isinstance(message, dict) or message.keys() != {"v", "type", "commands"} or message["v"] != 1 or message["type"] != "projection":
                raise ProtocolError("invalid_bridge_projection")
            commands.extend(render_commands(message["commands"]))
        if len(self.pending) > MAX_LINE:
            raise ProtocolError("bridge_message_too_large")
        return commands

    def action(self, event):
        item_id, action = event.get("id"), event.get("action")
        if isinstance(item_id, str) and TOKEN.fullmatch(item_id) and action in ("open", "dismiss"):
            try:
                self.stream.sendall(json.dumps({"v": 1, "type": "action", "id": item_id, "action": action}).encode() + b"\n")
            except OSError:
                pass  # An optional notification action never interrupts input.

    def close(self):
        self.stream.close()


class PetConnector:
    """Only a local socket handshake runs off the CU control thread."""
    def __init__(self, directory, task_id, binding, scope_token):
        self.results = queue.Queue(maxsize=1)
        self.cancelled = threading.Event()
        self.lock = threading.Lock()

        def connect():
            bridge = PetBridge.attach(directory, task_id, binding, scope_token)
            with self.lock:
                if self.cancelled.is_set():
                    if bridge:
                        bridge.close()
                    return
                self.results.put(bridge)

        self.worker = threading.Thread(target=connect, daemon=True)
        self.worker.start()

    def poll(self):
        try:
            return True, self.results.get_nowait()
        except queue.Empty:
            return False, None

    def close(self):
        with self.lock:
            self.cancelled.set()
            completed, bridge = self.poll()
            if completed and bridge:
                bridge.close()
