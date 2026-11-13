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
import subprocess
import sys
import time
from unittest import mock

from pet_bridge import render_commands
from pet_protocol import (Projection, ProtocolError, SPECIES, STATES, decode_pack, load_builtin_packs,
                          read_regular, text, validate_builtin_pets, validate_message, validate_pack)
from indicator import CapabilityError, RendererEvents, process_start_ticks
from companion import Companion, focus_output
from output_probe import FocusProbe, FocusProbeError


def projection(revision=0, items=()):
    return {"v": 1, "type": "projection", "revision": revision, "workingCount": 2, "items": list(items)}


def item(identity, kind):
    return {"id": identity, "kind": kind, "summary": "Eine Rückmeldung ist bereit."}


def png(width=256, height=512):
    def chunk(kind, payload):
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress((b"\0" + bytes(width * 4)) * height)) + chunk(b"IEND", b""))


def write_pack(root, name="sudo"):
    root.mkdir(parents=True, exist_ok=True)
    metadata = {"schema": 1, "name": name, "frameWidth": 64, "frameHeight": 64, "sheet": "pet.png",
                "states": {state: {"row": i, "frames": 4, "fps": 6, "loop": False} for i, state in enumerate(STATES)}}
    (root / "pet.json").write_text(json.dumps(metadata))
    (root / "pet.png").write_bytes(png())
    return root


def configure(**fields):
    return {"v": 1, "type": "configure", "persistent": False, "visible": True,
            "reducedMotion": False, "petPath": None, **fields}


def selection_companion(packs):
    # Selection tests have no desktop, Actor or socket side effects.
    companion = Companion.__new__(Companion)
    companion.renderer_path = "/test/renderer"
    companion.builtin_packs = packs
    companion.config = {key: value for key, value in configure(preferredSpecies=None).items() if key not in ("v", "type")}
    companion.pack, companion.character = companion.select_character(companion.config)
    companion.last_unavailable_species = None
    companion.projection = Projection()
    companion.renderer_failed = False
    companion.stop = False
    companion.feed_open = True
    companion.live_task = True
    companion.cu_peer = object()
    companion.close_renderer = mock.Mock()
    companion.project = mock.Mock()
    return companion


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
        self.assertIn("v1 asset none", sent)
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

    def test_completed_focus_response_and_eof_need_only_one_dispatch(self):
        with tempfile.TemporaryDirectory(prefix="pet-focus-drain-") as directory:
            executable = Path(directory) / "niri"
            executable.write_text("#!" + sys.executable + "\nimport json\nprint(json.dumps({'name':'test','logical':{'x':0,'y':0,'width':800,'height':600}}))\n")
            executable.chmod(0o700)
            with selectors.DefaultSelector() as selector:
                with mock.patch.dict(os.environ, {"WEASEL_INDICATOR_NIRI": str(executable)}):
                    probe = FocusProbe(selector, dict(os.environ))
                try:
                    probe.process.wait(timeout=1)
                    # Model a higher-priority paint barrier postponing the read.
                    time.sleep(0.31)
                    events = selector.select(0)
                    self.assertEqual(len(events), 1)
                    self.assertEqual(probe.advance(events[0][1]), ("test", (0, 0, 800, 600)))
                finally:
                    probe.close()

    def test_invalid_output_entries_use_geometry_error_in_both_focus_paths(self):
        for entry in (None, [], "invalid", 7, True):
            with self.subTest(entry=entry):
                responses = [mock.Mock(stdout=b'{"name":"test"}'),
                             mock.Mock(stdout=json.dumps({"test": entry}).encode())]
                with mock.patch("companion.subprocess.run", side_effect=responses) as run:
                    with self.assertRaises(CapabilityError):
                        focus_output({})
                    self.assertEqual([call.args[0][-1] for call in run.call_args_list], ["focused-output", "outputs"])
                with tempfile.TemporaryDirectory(prefix="pet-focus-entry-") as directory:
                    executable = Path(directory) / "niri"
                    replies = {"focused-output": {"name": "test"}, "outputs": {"test": entry}}
                    executable.write_text("#!" + sys.executable + "\nimport json,sys\nreplies=" + repr(replies) + "\nprint(json.dumps(replies[sys.argv[-1]]))\n")
                    executable.chmod(0o700)
                    with selectors.DefaultSelector() as selector:
                        with mock.patch.dict(os.environ, {"WEASEL_INDICATOR_NIRI": str(executable)}):
                            probe = FocusProbe(selector, dict(os.environ))
                        try:
                            deadline = time.monotonic() + 2
                            with self.assertRaises(FocusProbeError) as error:
                                while time.monotonic() < deadline:
                                    for key, mask in selector.select(0.05):
                                        key.data.advance(mask)
                            self.assertEqual(error.exception.code, "geometry_unavailable")
                        finally:
                            probe.close()

    def test_projection_rejects_authority_fields_and_secret_payload_channels(self):
        for field in ("observationMode", "observationActive", "phase", "windowTitle", "payload", "preferredSpecies", "character"):
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
        commands = ["v1 working 2", "v1 count 1", "v1 motion reduced", f"v1 bubble approval opaque-1 {summary}", "v1 asset none"]
        self.assertEqual(render_commands(commands), commands)
        for command in ("v1 asset none extra", "v1 asset None", "v1 asset none\nv1 end", "v1 asset none "):
            with self.subTest(command=command), self.assertRaises(ProtocolError):
                render_commands([command])

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


class PetSelectionTests(unittest.TestCase):
    def setUp(self):
        self.sudo = {"name": "sudo", "sheet": "/test/sudo/pet.png", "frameWidth": 64, "frameHeight": 64}

    def test_configure_accepts_legacy_null_and_only_fixed_species(self):
        self.assertEqual(validate_message(configure()), configure())
        for identity in (None, *SPECIES):
            with self.subTest(identity=identity):
                self.assertEqual(validate_message(configure(preferredSpecies=identity))["preferredSpecies"], identity)
        for identity in ("sudo", "Weasel", "lumi", "../dog", "https://pets/dog", "", True, 1, [], {}):
            with self.subTest(identity=identity), self.assertRaises(ProtocolError):
                validate_message(configure(preferredSpecies=identity))
        for field in ("species", "builtinPets", "character", "observationMode", "phase"):
            with self.subTest(field=field), self.assertRaises(ProtocolError):
                validate_message(configure(**{field: None}))

    def test_capabilities_are_bounded_unique_exact_and_named_safely(self):
        self.assertEqual(validate_builtin_pets([]), [])
        available = [{"species": identity, "name": identity} for identity in SPECIES]
        self.assertEqual(validate_builtin_pets(available), available)
        invalid = [None, {}, available + available[:1], available[:1] * 2,
                   [{"species": "sudo", "name": "sudo"}], [{"species": None, "name": "sudo"}],
                   [{"species": "weasel"}], [{"species": "weasel", "name": "sudo", "path": "/tmp"}]]
        for name in (None, "", " ", "x" * 61, "su\ndo", "su\x00do", "su\x7fdo", "su\u0085do",
                     *("su" + control + "do" for control in "\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")):
            invalid.append([{"species": "weasel", "name": name}])
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ProtocolError):
                validate_builtin_pets(value)
        self.assertEqual(validate_builtin_pets([{"species": "weasel", "name": "x" * 60}])[0]["name"], "x" * 60)

    def test_catalog_uses_fixed_sudo_path_and_only_validated_available_packs(self):
        with tempfile.TemporaryDirectory(prefix="pet-catalog-") as directory:
            root = Path(directory)
            write_pack(root / "dog", "dog")
            write_pack(root / "lumi", "lumi")
            with mock.patch.dict(os.environ, {"WEASEL_PET_BUILTIN_ROOT": directory}):
                self.assertEqual(load_builtin_packs("/test/renderer"), {})
                write_pack(root / "sudo")
                with mock.patch("pet_protocol.subprocess.run", return_value=mock.Mock(returncode=0)) as decoder:
                    packs = load_builtin_packs("/test/renderer")
                self.assertEqual(list(packs), ["weasel"])
                self.assertEqual(packs["weasel"]["name"], "sudo")
                self.assertEqual(decoder.call_args.args[0], ["/test/renderer", "--validate-sheet", str(root / "sudo/pet.png"), "64", "64"])
                self.assertEqual(decoder.call_args.kwargs["timeout"], 2)
                with mock.patch("pet_protocol.subprocess.run", return_value=mock.Mock(returncode=5)):
                    self.assertEqual(load_builtin_packs("/test/renderer"), {})
                with mock.patch("pet_protocol.subprocess.run", side_effect=subprocess.TimeoutExpired("renderer", 2)):
                    self.assertEqual(load_builtin_packs("/test/renderer"), {})
                write_pack(root / "sudo", "lumi")
                with mock.patch("pet_protocol.subprocess.run", return_value=mock.Mock(returncode=0)):
                    self.assertEqual(load_builtin_packs("/test/renderer"), {})

    def test_pack_names_and_bounds_fail_before_decode(self):
        with tempfile.TemporaryDirectory(prefix="pet-bounds-") as directory:
            root = Path(directory)
            for name in ("sudo\n", "sudo\u202e", "sudo\u2069", "x" * 61):
                with self.subTest(name=name):
                    write_pack(root, name)
                    with mock.patch("pet_protocol.subprocess.run") as decoder, self.assertRaises(ProtocolError):
                        decode_pack(root, "/test/renderer")
                    decoder.assert_not_called()
            write_pack(root)
            data = png()
            (root / "pet.png").write_bytes(data[:16] + struct.pack(">I", 2**31) + data[20:])
            with mock.patch("pet_protocol.subprocess.run") as decoder, self.assertRaises(ProtocolError):
                decode_pack(root, "/test/renderer")
            decoder.assert_not_called()

    def test_default_is_sudo_and_legacy_configure_clears_prior_preference(self):
        companion = selection_companion({"weasel": self.sudo})
        self.assertEqual(companion.character, {"source": "builtin", "requestedSpecies": None, "species": "weasel", "name": "sudo"})
        with mock.patch("companion.emit"):
            companion.input_message(configure(preferredSpecies="dog"))
            companion.input_message(configure())
        self.assertEqual(companion.pack, self.sudo)
        self.assertEqual(companion.character["source"], "builtin")
        self.assertIsNone(companion.character["requestedSpecies"])
        self.assertFalse(companion.config["persistent"])

    def test_missing_species_clears_last_pack_without_scope_end_or_error_flood(self):
        companion = selection_companion({"weasel": self.sudo})
        peer = companion.cu_peer
        with mock.patch("companion.emit") as emit, mock.patch("pet_protocol.subprocess.run") as decoder:
            companion.input_message(configure(preferredSpecies="dog"))
            companion.input_message(configure(preferredSpecies="dog"))
            companion.input_message(projection(1))
            companion.input_message(projection(2))
        emit.assert_called_once_with("error", code="builtin_pet_unavailable")
        decoder.assert_not_called()
        self.assertIsNone(companion.pack)
        self.assertEqual(companion.character, {"source": "unavailable", "requestedSpecies": "dog", "species": None, "name": None})
        self.assertEqual(companion.commands()[-1], "v1 asset none")
        self.assertIs(companion.cu_peer, peer)
        self.assertTrue(companion.feed_open)
        self.assertTrue(companion.live_task)
        self.assertFalse(companion.stop)
        companion.close_renderer.assert_not_called()
        with mock.patch("companion.emit") as emit:
            companion.input_message(configure(preferredSpecies="weasel"))
            companion.input_message(configure(preferredSpecies="dog"))
        emit.assert_called_once_with("error", code="builtin_pet_unavailable")

    def test_missing_default_is_unavailable_and_different_missing_choice_reports_once(self):
        companion = selection_companion({})
        self.assertEqual(companion.character, {"source": "unavailable", "requestedSpecies": None, "species": None, "name": None})
        with mock.patch("companion.emit") as emit:
            companion.report_selection_error()
            companion.input_message(configure())
            companion.input_message(configure(preferredSpecies="weasel"))
            self.assertEqual(emit.call_count, 1)
            companion.input_message(configure(preferredSpecies="cat"))
            self.assertEqual(emit.call_count, 2)

    def test_custom_precedes_missing_species_and_reset_obeys_preference(self):
        companion = selection_companion({"weasel": self.sudo})
        with tempfile.TemporaryDirectory(prefix="pet-custom-") as directory:
            root = write_pack(Path(directory), "Mein Otter")
            with mock.patch("pet_protocol.subprocess.run", return_value=mock.Mock(returncode=0)), mock.patch("companion.emit") as emit:
                companion.input_message(configure(petPath=str(root), preferredSpecies="otter"))
                self.assertEqual(companion.character, {"source": "custom", "requestedSpecies": "otter", "species": None, "name": "Mein Otter"})
                self.assertEqual(companion.pack["sheet"], str(root / "pet.png"))
                self.assertEqual(companion.commands()[-1], "v1 asset " + base64.b64encode(str(root / "pet.png").encode()).decode() + " 64 64")
                emit.assert_not_called()
                companion.input_message(configure(preferredSpecies="otter"))
                self.assertIsNone(companion.pack)
                self.assertEqual(companion.character["source"], "unavailable")
                emit.assert_called_once_with("error", code="builtin_pet_unavailable")
                companion.input_message(configure(preferredSpecies=None))
                self.assertEqual(companion.pack, self.sudo)
                self.assertEqual(companion.character, {"source": "builtin", "requestedSpecies": None, "species": "weasel", "name": "sudo"})

    def test_invalid_custom_decode_does_not_adopt_config_or_pack(self):
        companion = selection_companion({"weasel": self.sudo})
        original_config, original_character = companion.config, companion.character
        with tempfile.TemporaryDirectory(prefix="pet-invalid-custom-") as directory:
            root = write_pack(Path(directory))
            with mock.patch("pet_protocol.subprocess.run", return_value=mock.Mock(returncode=5)), self.assertRaises(ProtocolError):
                companion.input_message(configure(petPath=str(root), preferredSpecies="dog"))
        self.assertIs(companion.config, original_config)
        self.assertIs(companion.character, original_character)
        self.assertIs(companion.pack, self.sudo)
        companion.close_renderer.assert_not_called()
        companion.project.assert_not_called()

    def test_character_state_is_output_only_and_unchanged_ticks_do_not_decode(self):
        companion = selection_companion({"weasel": self.sudo})
        companion.last_focus = companion.last_actor = companion.last_heartbeat = 10.0
        companion.renderer_ready = False
        companion.monitor = None
        companion.cu_state = {"sessionId": "confirmed-session", "epoch": 1}
        companion.last_state = None
        companion.update_renderer = mock.Mock()
        with mock.patch("companion.time.monotonic", return_value=10.0), mock.patch("companion.emit") as emit, mock.patch("pet_protocol.subprocess.run") as decoder:
            companion.tick()
            companion.tick()
        decoder.assert_not_called()
        emit.assert_called_once_with("state", visible=True, persistent=False, monitor=None,
                                     computerUse=companion.cu_state, character=companion.character)


class RealPackDecoderTests(unittest.TestCase):
    def test_packaged_sudo_is_the_only_advertised_builtin(self):
        root = os.environ.get("WEASEL_PET_BUILTIN_ROOT")
        if root is None:
            self.skipTest("packaged builtin root required")
        renderer = os.environ["WEASEL_PET_RENDERER"]
        packs = load_builtin_packs(renderer)
        self.assertEqual(set(packs), {"weasel"})
        self.assertEqual(packs["weasel"]["name"], "sudo")
        self.assertEqual(packs["weasel"]["frameWidth"], 256)
        self.assertEqual(packs["weasel"]["frameHeight"], 256)

    def test_real_decoder_rejects_header_only_pack_for_catalog_and_custom_selection(self):
        renderer = os.environ.get("WEASEL_PET_RENDERER", str(Path(__file__).resolve().with_name("weasel-desktop-pet-renderer")))
        if not os.access(renderer, os.X_OK):
            self.skipTest("built native renderer required for actual PNG decoding")
        with tempfile.TemporaryDirectory(prefix="pet-real-decode-") as directory:
            root = Path(directory)
            pack_path = write_pack(root / "sudo")
            self.assertEqual(decode_pack(pack_path, renderer)["name"], "sudo")
            (pack_path / "pet.png").write_bytes(png()[:33])
            # Valid dimensions alone must not advertise or select corrupt PNGs.
            self.assertEqual(validate_pack(pack_path)["frameWidth"], 64)
            with self.assertRaises(ProtocolError):
                decode_pack(pack_path, renderer)
            with mock.patch.dict(os.environ, {"WEASEL_PET_BUILTIN_ROOT": directory}):
                self.assertEqual(load_builtin_packs(renderer), {})
            companion = selection_companion({})
            companion.renderer_path = renderer
            with self.assertRaises(ProtocolError):
                companion.input_message(configure(petPath=str(pack_path)))
            self.assertIsNone(companion.pack)
            self.assertEqual(companion.character["source"], "unavailable")


if __name__ == "__main__":
    unittest.main()
