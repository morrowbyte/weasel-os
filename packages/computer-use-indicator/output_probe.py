"""Bounded structural Niri output query integrated into a selector."""
import json
import os
import selectors
import subprocess
import time

from pet_protocol import TOKEN


class FocusProbe:
    def __init__(self, selector, environment):
        self.selector = selector
        self.environment = environment
        self.niri = os.environ.get("WEASEL_INDICATOR_NIRI", "niri")
        self.process = None
        self.name = None
        self.stage = "focused-output"
        self.pending = bytearray()
        self._start()

    def _start(self):
        self.process = subprocess.Popen([self.niri, "msg", "--json", self.stage],
            env=self.environment, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        os.set_blocking(self.process.stdout.fileno(), False)
        self.deadline = time.monotonic() + 0.3
        self.selector.register(self.process.stdout, selectors.EVENT_READ, self)

    def _release(self):
        if self.process is None:
            return
        try:
            self.selector.unregister(self.process.stdout)
        except KeyError:
            pass
        self.process.stdout.close()
        if self.process.poll() is None:
            self.process.kill()
        try:
            self.process.wait(timeout=0.05)
        except subprocess.TimeoutExpired:
            pass
        self.process = None

    def close(self):
        self._release()

    def advance(self, mask):
        if not mask & selectors.EVENT_READ:
            return None
        try:
            chunk = os.read(self.process.stdout.fileno(), 4096)
        except BlockingIOError:
            return None
        self.pending.extend(chunk)
        if len(self.pending) > 16384:
            raise ValueError("output response exceeds limit")
        if chunk or self.process.poll() is None:
            return None
        if self.process.returncode != 0:
            raise ValueError("output query unavailable")
        result = json.loads(self.pending)
        self._release()
        if self.stage == "focused-output":
            if not isinstance(result, dict) or not isinstance(result.get("name"), str) or not TOKEN.fullmatch(result["name"]):
                raise ValueError("focused output identity unavailable")
            self.name = result["name"]
            logical = result.get("logical")
            if not isinstance(logical, dict):
                self.stage = "outputs"
                self.pending.clear()
                self._start()
                return None
        else:
            logical = result.get(self.name, {}).get("logical") if isinstance(result, dict) else None
        if not isinstance(logical, dict):
            raise ValueError("focused output geometry unavailable")
        geometry = tuple(logical.get(k) for k in ("x", "y", "width", "height"))
        if any(type(x) is not int or abs(x) > 100000 for x in geometry) or geometry[2] < 320 or geometry[3] < 200:
            raise ValueError("focused output geometry invalid")
        return self.name, geometry
