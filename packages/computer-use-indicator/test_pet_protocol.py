"""Privacy boundary and queue regressions, runnable without a desktop."""
import base64
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
import zlib
import selectors
import select
import socket
import sys
import time
from unittest import mock

from pet_bridge import render_commands
from pet_protocol import (Projection, ProtocolError, STATES, read_regular,
                          text, validate_message, validate_pack)
from indicator import RendererEvents, process_start_ticks
from companion import Companion
from output_probe import FocusProbe


def projection(revision=0, items=()):
    return {"v": 1, "type": "projection", "revision": revision, "workingCount": 2, "items": list(items)}


def item(identity, kind):
    return {"id": identity, "kind": kind, "summary": "Eine Rückmeldung ist bereit."}


def png(width=256, height=512):
    def chunk(kind, payload):
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress((b"\0" + bytes(width * 4)) * height)) + chunk(b"IEND", b""))


class ProtocolTests(unittest.TestCase):
    def test_stale_renderer_readiness_after_ack_does_not_block(self):
        reader_fd, writer_fd = os.pipe()
        with os.fdopen(reader_fd, "rb", buffering=0) as stream:
            try:
                reader = RendererEvents(stream)
                os.write(writer_fd, b'{"event":"action","id":"q1"}\n{"event":"phase_applied","transition":7}\n')
                stale_snapshot = select.select([reader_fd], [], [], 0)[0]
                self.assertEqual(stale_snapshot, [reader_fd])
                self.assertEqual([x["event"] for x in reader.read()], ["action", "phase_applied"])
                self.assertFalse(os.get_blocking(reader_fd))
                self.assertEqual(reader.read(), [])  # Writer remains open, no EOF rescue.
            finally:
                os.close(writer_fd)

    def test_oversized_fragmented_ingress_cannot_retain_unbounded_buffer(self):
        companion = Companion.__new__(Companion)
        pending = bytearray()
        failures = 0
        for _ in range(100):
            try:
                companion.consume(pending, b"x" * 4096, lambda _: None)
            except ProtocolError:
                failures += 1
            self.assertLessEqual(len(pending), 16384)
        self.assertGreater(failures, 0)

    def test_unchanged_asset_not_retransmitted_for_working_count(self):
        companion = Companion.__new__(Companion)
        companion.config = {"reducedMotion": False}
        companion.pack = None
        companion.projection = Projection()
        companion.cu_peer = None
        companion.last_commands = None
        sent = []
        companion.command = sent.append
        companion.project(force=True)
        self.assertIn("v1 asset default", sent)
        sent.clear()
        companion.projection.working = 3
        companion.project()
        self.assertEqual(sent, ["v1 working 3"])

    def test_restart_detects_live_helper_before_starting_second_pet(self):
        with tempfile.TemporaryDirectory(prefix="pet-lease-") as directory:
            root = Path(directory)
            endpoint = root / "task-test.sock"
            lease_path = root / "task-test.json"
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                server.bind(str(endpoint))
                endpoint.chmod(0o600)
                metadata = {"task_id": "test", "pid": os.getpid(), "start_ticks": process_start_ticks(os.getpid()), "control_socket": str(endpoint)}
                lease_path.write_text(json.dumps(metadata))
                lease_path.chmod(0o600)
                companion = Companion.__new__(Companion)
                companion.directory = root
                with mock.patch("companion.task_paths", return_value=(endpoint, lease_path)):
                    self.assertTrue(companion.live_indicator())
                    metadata["start_ticks"] += 1
                    lease_path.write_text(json.dumps(metadata))
                    self.assertFalse(companion.live_indicator())
                companion.config = {"persistent": True, "visible": True}
                companion.feed_open = True
                companion.live_task = True
                companion.cu_peer = None
                companion.monitor = ("test", (0, 0, 800, 600))
                companion.close_renderer = mock.Mock()
                with mock.patch("companion.subprocess.Popen") as spawn:
                    companion.update_renderer()
                    spawn.assert_not_called()

    def test_failed_renderer_waits_for_explicit_retry_instead_of_spawn_loop(self):
        companion = Companion.__new__(Companion)
        companion.config = {"persistent": True, "visible": True}
        companion.feed_open = True
        companion.live_task = False
        companion.cu_peer = None
        companion.monitor = ("test", (0, 0, 800, 600))
        companion.renderer_failed = True
        with mock.patch("companion.subprocess.Popen") as spawn:
            for _ in range(20):
                companion.update_renderer()
            spawn.assert_not_called()

    def test_focus_probe_leaves_control_ready_during_two_slow_queries(self):
        with tempfile.TemporaryDirectory(prefix="pet-focus-") as directory:
            executable = Path(directory) / "niri"
            executable.write_text("#!" + sys.executable + "\nimport json,sys,time\ntime.sleep(0.08)\nprint(json.dumps({'name':'test'} if sys.argv[-1]=='focused-output' else {'test':{'logical':{'x':0,'y':0,'width':800,'height':600}}}))\n")
            executable.chmod(0o700)
            selector = selectors.DefaultSelector()
            read_fd, write_fd = os.pipe()
            try:
                selector.register(read_fd, selectors.EVENT_READ, "control")
                with mock.patch.dict(os.environ, {"WEASEL_INDICATOR_NIRI": str(executable)}):
                    probe = FocusProbe(selector, dict(os.environ))
                os.write(write_fd, b"stop")
                result = None
                control_seen = False
                deadline = time.monotonic() + 2
                while result is None and time.monotonic() < deadline:
                    for key, mask in selector.select(0.05):
                        if key.data == "control":
                            self.assertIsNone(result)
                            self.assertEqual(os.read(read_fd, 4), b"stop")
                            selector.unregister(read_fd)
                            control_seen = True
                        else:
                            result = key.data.advance(mask)
                self.assertTrue(control_seen)
                self.assertEqual(result, ("test", (0, 0, 800, 600)))
            finally:
                if "probe" in locals():
                    probe.close()
                selector.close()
                os.close(read_fd)
                os.close(write_fd)

    def test_projection_rejects_authority_fields_and_secret_payload_channels(self):
        for field in ("observationMode", "observationActive", "phase", "windowTitle", "payload"):
            with self.subTest(field=field), self.assertRaises(ProtocolError):
                validate_message({**projection(), field: "disabled"})

    def test_queue_priority_revision_and_action_binding(self):
        queue = Projection()
        self.assertTrue(queue.apply(projection(3, [item("r", "result"), item("q", "question"), item("a", "approval")])))
        self.assertEqual([x["id"] for x in queue.pending()], ["a", "q", "r"])
        self.assertFalse(queue.action("stale", "open"))
        self.assertFalse(queue.action("a", "approve"))
        self.assertTrue(queue.action("a", "open"))
        self.assertFalse(queue.action("a", "open"))
        self.assertFalse(queue.apply(projection(2, [item("stale", "approval")])))
        queue.apply(projection(4, [item("a", "approval"), item("q", "question")]))
        self.assertEqual([x["id"] for x in queue.pending()], ["q"])

    def test_strict_scalars_and_bounded_items(self):
        for message in ({**projection(), "revision": True}, {**projection(), "items": [item("x", "question")] * 2},
                        {**projection(), "items": [item(str(i), "result") for i in range(21)]},
                        {"v": True, "type": "toggle"}):
            with self.assertRaises(ProtocolError):
                validate_message(message)

    def test_controls_and_all_bidi_controls_rejected(self):
        for control in ("\n", "\x7f", "\u061c", "\u200e", "\u200f", "\u202e", "\u2067"):
            with self.subTest(control=repr(control)), self.assertRaises(ProtocolError):
                text("Pet" + control, 60)

    def test_cu_bridge_cannot_hide_change_policy_or_ack_capture(self):
        for command in ("v1 observation disabled idle", "v1 phase observing 9", "v1 visible 0", "v1 heartbeat", "v1 end", "v1 bind evil"):
            with self.subTest(command=command), self.assertRaises(ProtocolError):
                render_commands([command])
        summary = base64.b64encode("Freigabe wartet.".encode()).decode()
        commands = ["v1 working 2", "v1 count 1", "v1 motion reduced", f"v1 bubble approval opaque-1 {summary}"]
        self.assertEqual(render_commands(commands), commands)

    def test_pack_rejects_path_escape_symlink_and_fifo(self):
        with tempfile.TemporaryDirectory(prefix="pet-protocol-") as directory:
            root = Path(directory)
            metadata = {"schema": 1, "name": "Wiesel", "frameWidth": 64, "frameHeight": 64, "sheet": "pet.png",
                        "states": {state: {"row": i, "frames": 4, "fps": 6, "loop": False} for i, state in enumerate(STATES)}}
            (root / "pet.json").write_text(json.dumps(metadata))
            (root / "pet.png").write_bytes(png())
            self.assertEqual(validate_pack(directory)["frameWidth"], 64)
            metadata["sheet"] = "../outside.png"
            (root / "pet.json").write_text(json.dumps(metadata))
            with self.assertRaises(ProtocolError):
                validate_pack(directory)
            (root / "link").symlink_to(root / "pet.png")
            with self.assertRaises(ProtocolError):
                read_regular(root / "link", 16000000)
            os.mkfifo(root / "fifo")
            with self.assertRaises(ProtocolError):
                read_regular(root / "fifo", 16000000)


if __name__ == "__main__":
    unittest.main()
