import argparse
import fcntl
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sandbox
import workspace


class Profiles(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.data = self.root / "data"
        self.runtime = self.root / "runtime"
        self.runtime.mkdir(mode=0o700)
        self.enterContext(patch.dict(os.environ, XDG_RUNTIME_DIR=str(self.runtime),
                                     XDG_DATA_HOME=str(self.data)))
        self.path = self.data / "weasel-workspace/profiles/report"

    def test_mount_pins_validated_leaf_through_rename(self):
        with sandbox.profile_dir("report") as fd:
            self.path.rename(self.root / "original")
            self.path.symlink_to(self.root)
            args = sandbox.sandbox_args(self.runtime, None, False, fd)
            bind = args.index("--bind-fd")
            self.assertEqual(args[bind + 1:bind + 3], [str(fd), "/home/workspace"])
            self.assertTrue(os.path.samestat(os.fstat(fd), (self.root / "original").stat()))
        with self.assertRaises(OSError), sandbox.profile_dir("report"):
            pass
        with self.assertRaises(ValueError), sandbox.profile_dir("../escape"):
            pass

    def test_unsafe_permissions_and_exchange_overlap(self):
        with sandbox.profile_dir("report"):
            pass
        self.path.parent.chmod(0o755)
        with self.assertRaisesRegex(ValueError, "0700"), sandbox.profile_dir("report"):
            pass
        with self.assertRaisesRegex(ValueError, "overlaps"), sandbox.exchange_dir(self.path / "subdir"):
            pass
        self.assertFalse((self.path / "subdir").exists())
        with sandbox.exchange_dir(self.root / "exchange"):
            with patch.dict(os.environ, XDG_DATA_HOME=str(self.root / "exchange/data")):
                with self.assertRaisesRegex(ValueError, "inside an exchange"), sandbox.profile_dir("other"):
                    pass

    def test_sync_fd_alone_holds_both_locks_until_close(self):
        live_path = self.runtime / "live"
        with live_path.open("w") as live, sandbox.profile_dir("report") as profile:
            with sandbox.lifetime_fd(live, profile) as sync:
                retained = os.fdopen(os.dup(sync), "rb", buffering=0)
        with retained, live_path.open("r") as live, sandbox.profile_dir("report") as profile:
            with self.assertRaises(BlockingIOError):
                fcntl.flock(live, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with (self.runtime / "other-live").open("w") as other:
                with self.assertRaisesRegex(ValueError, "already in use"), sandbox.lifetime_fd(other, profile):
                    pass
            retained.close()
            with sandbox.lifetime_fd(live, profile):
                pass

    def test_fresh_start_is_explicit_and_old_sessions_are_ephemeral(self):
        paths = workspace.Paths(workspace.base_dir(), "report")
        args = argparse.Namespace(exchange=None, network=False, persistent=False)
        with patch.object(workspace, "spawn", return_value={"running": True}) as spawn:
            self.assertFalse(workspace.start(paths, args)["persistent"])
            self.assertIsNone(spawn.call_args.kwargs["profile_fd"])
            self.assertFalse(self.data.exists())
            args.persistent = True
            self.assertTrue(workspace.start(paths, args)["persistent"])
            self.assertIsInstance(spawn.call_args.kwargs["profile_fd"], int)
            args.persistent = False
            self.assertFalse(workspace.start(paths, args)["persistent"])
            self.assertIsNone(spawn.call_args.kwargs["profile_fd"])
        workspace.save(paths.options, {"network": False, "exchange": None})
        with patch.object(paths, "running", return_value=True), patch.object(workspace, "rpc", return_value={}):
            self.assertFalse(workspace.start(paths, args)["persistent"])
            args.persistent = True
            with self.assertRaisesRegex(ValueError, "already running"):
                workspace.start(paths, args)

    def test_bubblewrap_writes_home_and_retains_both_locks(self):
        live_path = self.runtime / "live"
        with live_path.open("w") as live, sandbox.profile_dir("report") as profile:
            with sandbox.lifetime_fd(live, profile) as sync:
                proc = subprocess.Popen([
                    shutil.which("bwrap"), "--unshare-all", "--uid", "1000", "--gid", "1000",
                    "--cap-drop", "ALL", "--ro-bind", "/nix/store", "/nix/store", "--proc", "/proc",
                    "--dev", "/dev", "--bind-fd", str(profile), "/home/workspace", "--sync-fd", str(sync),
                    "--", Path(shutil.which("bash")).resolve(), "-c",
                    "echo saved > /home/workspace/document && echo ready && read -r finish",
                ], env={}, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    pass_fds=(sync, profile))
        with proc, live_path.open("r") as live, sandbox.profile_dir("report") as profile:
            try:
                if proc.stdout.readline() != b"ready\n":
                    self.fail(proc.stderr.read(2048).decode())
                for fd in (live, profile):
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                proc.communicate(b"finish\n", timeout=5)
                self.assertEqual(proc.returncode, 0)
                self.assertEqual((self.path / "document").read_bytes(), b"saved\n")
                for fd in (live, profile):
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                proc.kill()


if __name__ == "__main__":
    unittest.main()
