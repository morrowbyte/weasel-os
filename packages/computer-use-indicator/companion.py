#!/usr/bin/env python3
"""Local presentation bridge; never captures, inputs, or changes Actor policy."""
import argparse
import base64
import fcntl
import json
import os
from pathlib import Path
import selectors
import signal
import socket
import stat
import struct
import subprocess
import sys
import time

from indicator import (CapabilityError, desktop_lifecycle, graphical_environment,
                       observation_generation, observation_state, process_start_ticks, runtime_dir, task_paths)
from pet_protocol import (MAX_LINE, TOKEN, Projection, ProtocolError, decode_pack, load_builtin_packs,
                          read_regular, validate_builtin_pets, validate_message)


def emit(kind, **fields):
    print(json.dumps({"v": 1, "type": kind, **fields}, ensure_ascii=False), flush=True)


def encoded(value):
    return base64.b64encode(value.encode()).decode("ascii")


def focus_output(environment):
    """These two structural queries never request windows, titles, or AT-SPI."""
    niri = os.environ.get("WEASEL_INDICATOR_NIRI", "niri")
    result = subprocess.run([niri, "msg", "--json", "focused-output"], env=environment,
                            capture_output=True, timeout=0.3, check=True)
    output = json.loads(result.stdout)
    if not isinstance(output, dict) or not isinstance(output.get("name"), str) or not TOKEN.fullmatch(output["name"]):
        raise CapabilityError("focused output unavailable")
    logical = output.get("logical")
    if not isinstance(logical, dict):
        # Some Niri versions return the identity only from focused-output.
        result = subprocess.run([niri, "msg", "--json", "outputs"], env=environment,
                                capture_output=True, timeout=0.3, check=True)
        outputs = json.loads(result.stdout)
        selected = outputs.get(output["name"]) if isinstance(outputs, dict) else None
        logical = selected.get("logical") if isinstance(selected, dict) else None
    if not isinstance(logical, dict):
        raise CapabilityError("focused output geometry unavailable")
    geometry = tuple(logical.get(k) for k in ("x", "y", "width", "height"))
    if any(type(x) is not int or abs(x) > 100000 for x in geometry) or geometry[2] < 320 or geometry[3] < 200:
        raise CapabilityError("focused output geometry invalid")
    return output["name"], geometry


def safe_cu_state(status, task_id=None):
    mode, active = observation_state(status)
    generation = observation_generation(status)
    if mode not in ("enabled", "disabled") or generation < 0:
        return None
    return {"sessionId": status["session_id"], "epoch": status["epoch"],
            "taskId": task_id if status["task_scope_active"] else None,
            "observationMode": mode, "observationActive": active,
            "observationGeneration": generation}


class Companion:
    def __init__(self):
        self.environment = graphical_environment()
        self.directory = runtime_dir()
        self.path = self.directory / "pet.sock"
        self.actor = Path(os.environ.get("WEASEL_COMPUTER_USE_SOCKET", str(self.directory.parent / "desktop.sock")))
        self.renderer_path = os.environ.get("WEASEL_PET_RENDERER")
        if not self.renderer_path or not os.access(self.renderer_path, os.X_OK):
            raise CapabilityError("pet renderer unavailable")
        self.selector = selectors.DefaultSelector()
        self.renderer = None
        self.renderer_ready = False
        self.renderer_failed = False
        self.renderer_pending = bytearray()
        self.config = {"persistent": False, "visible": True, "reducedMotion": False, "petPath": None,
                       "preferredSpecies": None}
        self.builtin_packs = load_builtin_packs(self.renderer_path)
        self.pack, self.character = self.select_character(self.config)
        self.last_unavailable_species = None
        self.projection = Projection()
        self.peers = {}
        self.cu_peer = None
        self.monitor = None
        self.cu_state = None
        self.live_task = False
        self.last_state = None
        self.last_commands = None
        self.stop = False
        self.feed_open = True
        self.stdin_pending = bytearray()
        self.last_actor = self.last_focus = self.last_heartbeat = 0.0
        self.socket_inode = None
        self.lock = None
        self.server = None

    def select_character(self, config):
        requested = config["preferredSpecies"]
        if config["petPath"] is not None:
            pack = decode_pack(config["petPath"], self.renderer_path)
            return pack, {"source": "custom", "requestedSpecies": requested, "species": None, "name": pack["name"]}
        identity = requested if requested is not None else "weasel"
        pack = self.builtin_packs.get(identity)
        if pack is not None:
            return pack, {"source": "builtin", "requestedSpecies": requested, "species": identity, "name": pack["name"]}
        return None, {"source": "unavailable", "requestedSpecies": requested, "species": None, "name": None}

    def report_selection_error(self):
        if self.character["source"] == "unavailable":
            identity = self.character["requestedSpecies"] or "weasel"
            if identity != self.last_unavailable_species:
                self.last_unavailable_species = identity
                emit("error", code="builtin_pet_unavailable")
        else:
            self.last_unavailable_species = None

    def command(self, line):
        if self.renderer and self.renderer.poll() is None:
            self.renderer.stdin.write(line + "\n")
            self.renderer.stdin.flush()

    def close_renderer(self):
        renderer, self.renderer = self.renderer, None
        self.renderer_ready = False
        self.last_commands = None
        if renderer is None:
            return
        self.selector.unregister(renderer.stdout)
        try:
            renderer.stdin.write("v1 end\n")
            renderer.stdin.flush()
        except OSError:
            pass
        renderer.stdin.close()
        try:
            renderer.wait(timeout=0.4)
        except subprocess.TimeoutExpired:
            renderer.terminate()
            try:
                renderer.wait(timeout=0.4)
            except subprocess.TimeoutExpired:
                renderer.kill()
                renderer.wait(timeout=0.4)
        renderer.stdout.close()

    def commands(self):
        commands = [f"v1 working {self.projection.working}",
                    "v1 motion " + ("reduced" if self.config["reducedMotion"] else "full")]
        pending = self.projection.pending()
        commands.append(f"v1 count {len(pending)}")
        if pending:
            item = pending[0]
            commands.append(f"v1 bubble {item['kind']} {item['id']} {encoded(item['summary'])}")
        else:
            commands.append("v1 bubble clear")
        if self.pack:
            commands.append(f"v1 asset {encoded(self.pack['sheet'])} {self.pack['frameWidth']} {self.pack['frameHeight']}")
        else:
            commands.append("v1 asset none")
        return commands

    def project(self, force=False):
        commands = self.commands()
        if not force and commands == self.last_commands:
            return
        changed = commands if force or self.last_commands is None else [line for line in commands if line not in self.last_commands]
        self.last_commands = commands
        for command in changed:
            self.command(command)
        if self.cu_peer:
            self.send_peer(self.cu_peer, {"v": 1, "type": "projection", "commands": changed})

    def update_renderer(self):
        wanted = self.feed_open and self.config["persistent"] and self.config["visible"] and not self.cu_peer and not self.live_task and self.monitor
        if not wanted:
            self.close_renderer()
            return
        if self.renderer_failed:
            return
        if self.renderer and self.renderer.poll() is not None:
            self.close_renderer()
            self.renderer_failed = True
            emit("error", code="renderer_exited")
            return
        if not self.renderer:
            output, geometry = self.monitor
            self.renderer = subprocess.Popen([self.renderer_path, output, "crew-pet", *map(str, geometry), "Crew"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, bufsize=1, env=dict(self.environment, WEASEL_PET_COMPANION="1"))
            os.set_blocking(self.renderer.stdout.fileno(), False)
            self.renderer_pending.clear()
            self.selector.register(self.renderer.stdout, selectors.EVENT_READ, "renderer")
            self.command("v1 begin")
            self.command("v1 observation unknown idle")
            self.command("v1 phase waiting")
            self.project(force=True)

    def send_peer(self, peer, message):
        try:
            # Every peer receives at most one bounded projection on change.
            peer.settimeout(0.05)
            peer.sendall(json.dumps(message, separators=(",", ":")).encode() + b"\n")
            peer.setblocking(False)
        except OSError:
            self.drop_peer(peer)

    def drop_peer(self, peer):
        try:
            self.selector.unregister(peer)
        except KeyError:
            pass
        self.peers.pop(peer, None)
        peer.close()
        if self.cu_peer is peer:
            self.cu_peer = None
            self.cu_state = None
            self.last_actor = 0
            self.last_commands = None

    def action(self, message):
        item_id, action = message.get("id"), message.get("action")
        if self.projection.action(item_id, action):
            emit("action", id=item_id, action=action)
            self.project(force=True)

    def peer_message(self, peer, message):
        if not isinstance(message, dict) or message.get("v") != 1:
            raise ProtocolError("invalid_peer")
        if message.get("type") == "attach":
            if self.cu_peer is not None or set(message) != {"v", "type", "taskId", "sessionId", "epoch", "scopeToken"}:
                raise ProtocolError("pet_already_attached")
            if not isinstance(message["taskId"], str) or not TOKEN.fullmatch(message["taskId"]):
                raise ProtocolError("invalid_peer")
            if not isinstance(message["sessionId"], str) or not 1 <= len(message["sessionId"]) <= 128 or type(message["epoch"]) is not int or message["epoch"] < 0:
                raise ProtocolError("invalid_peer")
            if not isinstance(message["scopeToken"], str) or not 1 <= len(message["scopeToken"]) <= 128:
                raise ProtocolError("invalid_peer")
            status = desktop_lifecycle(self.actor, message["taskId"], binding={"session_id": message["sessionId"], "epoch": message["epoch"]}, scope_token=message["scopeToken"])
            if not status["task_scope_active"] or status["session_id"] != message["sessionId"] or status["epoch"] != message["epoch"]:
                raise ProtocolError("peer_task_unconfirmed")
            self.peers[peer]["binding"] = message
            self.cu_peer = peer
            self.close_renderer()  # Avoid two pets before acknowledging attach.
            self.send_peer(peer, {"v": 1, "type": "attached"})
            self.project(force=True)
        elif message.get("type") == "action" and peer is self.cu_peer:
            self.action(message)
        else:
            raise ProtocolError("invalid_peer")

    def input_message(self, message):
        validate_message(message)
        kind = message["type"]
        if kind == "configure":
            # Validate before changing any configuration or renderer.
            config = {key: message[key] for key in ("persistent", "visible", "reducedMotion", "petPath")}
            config["preferredSpecies"] = message.get("preferredSpecies")
            pack, character = self.select_character(config)
            if self.config["petPath"] != message["petPath"]:
                self.close_renderer()
            self.config = config
            self.pack = pack
            self.character = character
            self.renderer_failed = False
            self.report_selection_error()
            self.project(force=True)
        elif kind == "projection":
            if self.projection.apply(message):
                self.project()
        elif kind == "toggle":
            self.config["visible"] = not self.config["visible"]
        elif kind == "shutdown":
            self.feed_open = False
            self.stop = True

    def consume(self, pending, chunk, handler):
        pending.extend(chunk)
        while b"\n" in pending:
            line, _, tail = pending.partition(b"\n")
            pending[:] = tail
            if len(line) > MAX_LINE:
                pending.clear()
                raise ProtocolError("message_too_large")
            handler(json.loads(line))
        if len(pending) > MAX_LINE:
            pending.clear()
            raise ProtocolError("message_too_large")

    def live_indicator(self):
        """A verified helper lease suppresses duplicates during reattachment.

        It does not provide observation authority: that still comes exclusively
        from the Actor. No authored activity is returned or used here.
        """
        import itertools
        for path in itertools.islice(self.directory.glob("task-*.json"), 32):
            try:
                info = path.lstat()
                if info.st_uid != os.getuid() or info.st_mode & 0o077:
                    continue
                lease = json.loads(read_regular(path, 4096))
                task_id = lease.get("task_id")
                if not isinstance(task_id, str) or not TOKEN.fullmatch(task_id):
                    continue
                expected_socket, expected_lease = task_paths(task_id)
                if path != expected_lease or lease.get("control_socket") != str(expected_socket):
                    continue
                if type(lease.get("pid")) is not int or type(lease.get("start_ticks")) is not int:
                    continue
                if process_start_ticks(lease["pid"]) != lease["start_ticks"]:
                    continue
                endpoint = expected_socket.lstat()
                if stat.S_ISSOCK(endpoint.st_mode) and endpoint.st_uid == os.getuid() and not endpoint.st_mode & 0o077:
                    return True
            except (OSError, ValueError, CapabilityError):
                continue
        return False

    def tick(self):
        now = time.monotonic()
        if now - self.last_focus >= 0.5:
            self.last_focus = now
            try:
                monitor = focus_output(self.environment)
                if monitor != self.monitor:
                    self.monitor = monitor
                    output, geometry = monitor
                    self.command(f"v1 monitor {output} {' '.join(map(str, geometry))}")
            except (OSError, ValueError, CapabilityError, subprocess.SubprocessError):
                self.monitor = None
                self.close_renderer()
        if now - self.last_actor >= 0.5:
            self.last_actor = now
            self.live_task = self.live_indicator()
            try:
                binding = self.peers[self.cu_peer].get("binding") if self.cu_peer else None
                status = desktop_lifecycle(self.actor, binding["taskId"] if binding else None,
                    binding={"session_id": binding["sessionId"], "epoch": binding["epoch"]} if binding else None,
                    scope_token=binding["scopeToken"] if binding else None)
                self.cu_state = safe_cu_state(status, binding["taskId"] if binding else None)
            except (OSError, ValueError, CapabilityError):
                self.cu_state = None
            if self.cu_state:
                self.command(f"v1 observation {self.cu_state['observationMode']} {'active' if self.cu_state['observationActive'] else 'idle'}")
            else:
                self.command("v1 observation unknown idle")
        self.update_renderer()
        if now - self.last_heartbeat >= 1:
            self.last_heartbeat = now
            self.command("v1 heartbeat")
        state = {"visible": bool(self.cu_peer or self.renderer_ready), "persistent": self.config["persistent"],
                 "monitor": self.monitor[0] if self.monitor else None, "computerUse": self.cu_state,
                 "character": self.character}
        if state != self.last_state:
            emit("state", **state)
            self.last_state = state

    def run(self):
        self.lock = os.open(self.directory / "pet.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if self.path.exists():
            info = self.path.lstat()
            if info.st_uid != os.getuid() or not stat.S_ISSOCK(info.st_mode):
                raise CapabilityError("pet endpoint unavailable")
            self.path.unlink()  # Only the singleton-lock holder may clear stale socket.
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(str(self.path))
        self.socket_inode = self.path.lstat().st_ino
        os.chmod(self.path, 0o600)
        self.server.listen(4)
        self.server.setblocking(False)
        self.selector.register(self.server, selectors.EVENT_READ, "server")
        os.set_blocking(sys.stdin.fileno(), False)
        self.selector.register(sys.stdin, selectors.EVENT_READ, "stdin")
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(signum, lambda *_: setattr(self, "stop", True))
        builtin_pets = validate_builtin_pets([
            {"species": identity, "name": pack["name"]} for identity, pack in self.builtin_packs.items()])
        emit("ready", capabilities={"nativeOverlay": True, "focusMonitor": True, "customPet": True,
                                    "computerUse": True, "builtinPets": builtin_pets})
        self.report_selection_error()
        try:
            while not self.stop:
                for key, _ in self.selector.select(0.1):
                    if key.data == "stdin":
                        chunk = os.read(sys.stdin.fileno(), 4096)
                        if not chunk:
                            self.stop = True
                            break
                        try:
                            self.consume(self.stdin_pending, chunk, self.input_message)
                        except (ValueError, OSError):
                            self.stdin_pending.clear()
                            emit("error", code="invalid_message_or_pet")
                    elif key.data == "server":
                        peer, _ = self.server.accept()
                        _, uid, _ = struct.unpack("3i", peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                        if uid != os.getuid() or len(self.peers) >= 4:
                            peer.close()
                            continue
                        peer.setblocking(False)
                        self.peers[peer] = {"pending": bytearray(), "started": time.monotonic()}
                        self.selector.register(peer, selectors.EVENT_READ, peer)
                    elif key.data == "renderer":
                        chunk = os.read(key.fd, 4096)
                        if not chunk:
                            self.close_renderer()
                            self.renderer_failed = True
                            emit("error", code="renderer_exited")
                            continue
                        def rendered(message):
                            if message.get("revision") == 1 and message.get("event") == "ready":
                                self.renderer_ready = True
                            elif message.get("event") == "action":
                                self.action(message)
                        self.consume(self.renderer_pending, chunk, rendered)
                    else:
                        peer = key.data
                        try:
                            chunk = peer.recv(4096)
                            if not chunk:
                                self.drop_peer(peer)
                                continue
                            self.consume(self.peers[peer]["pending"], chunk, lambda message: self.peer_message(peer, message))
                        except (OSError, ValueError, CapabilityError):
                            self.drop_peer(peer)
                for peer, data in list(self.peers.items()):
                    if peer is not self.cu_peer and time.monotonic() - data["started"] > 1:
                        self.drop_peer(peer)
                self.tick()
        finally:
            self.close_renderer()
            for peer in list(self.peers):
                self.drop_peer(peer)
            emit("disconnected", reason="owner_ended")
        return 0

    def close(self):
        if self.server:
            self.server.close()
        if self.socket_inode is not None:
            try:
                if self.path.lstat().st_ino == self.socket_inode:
                    self.path.unlink()
            except FileNotFoundError:
                pass
        if self.lock is not None:
            os.close(self.lock)
        self.selector.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stdio", action="store_true", required=True)
    args = parser.parse_args()
    companion = None
    try:
        companion = Companion()
        return companion.run()
    except (OSError, ValueError, CapabilityError, subprocess.SubprocessError):
        emit("error", code="native_overlay_unavailable")
        return 1
    finally:
        if companion:
            companion.close()


if __name__ == "__main__":
    sys.exit(main())
