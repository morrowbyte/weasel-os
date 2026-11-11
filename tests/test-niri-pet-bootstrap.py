#!/usr/bin/env python3
"""Run the evaluated ensureCrewPetBindings activation in private test directories.

CREW_PET_ACTIVATION names its nix-eval --raw output, evaluated with
xdg.configHome = "$CREW_PET_TEST_ROOT/config with spaces and ' quote".
The test root must be a fresh mktemp directory. No script rewriting or real
home/system activation is used.
"""
import concurrent.futures
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest


class PetBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(os.environ["CREW_PET_TEST_ROOT"])
        self.assertEqual(self.root.parent, Path(tempfile.gettempdir()))
        self.assertTrue(self.root.name.startswith("crew-pet-bootstrap-"))
        self.assertTrue(self.root.is_dir() and not self.root.is_symlink())
        self.config = self.root / "config with spaces and ' quote"
        self.assertEqual(os.environ["CREW_PET_CONFIG_HOME"], str(self.config))
        self.target = self.config / "niri/crew-pet.kdl"
        self.assertFalse(self.config.exists())
        self.script = Path(os.environ["CREW_PET_ACTIVATION"]).read_text()
        self.addCleanup(self.remove_config)

    def remove_config(self):
        if self.config.is_dir() and not self.config.is_symlink():
            shutil.rmtree(self.config)
        elif os.path.lexists(self.config):
            self.config.unlink()

    def activate(self, dry_run=False):
        environment = dict(os.environ, DRY_RUN_CMD="echo" if dry_run else "")
        return subprocess.run(["bash", "-eu", "-c", self.script], env=environment,
                              capture_output=True, text=True, timeout=5)

    def test_missing_file_is_empty_private_and_repeatable(self):
        self.assertEqual(self.activate().returncode, 0)
        self.assertEqual(self.target.read_bytes(), b"")
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.target.parent.stat().st_mode), 0o700)
        self.target.write_text('binds { Ctrl+Alt+P { spawn "crew"; } }\n')
        self.target.chmod(0o640)
        before = self.target.stat()
        content = self.target.read_bytes()
        self.assertEqual(self.activate().returncode, 0)
        after = self.target.stat()
        self.assertEqual((after.st_ino, after.st_mtime_ns, after.st_mode),
                         (before.st_ino, before.st_mtime_ns, before.st_mode))
        self.assertEqual(self.target.read_bytes(), content)

    def test_existing_entries_and_neighbors_are_preserved(self):
        self.target.parent.mkdir(parents=True)
        neighbor = self.target.parent / "binds.kdl"
        neighbor.write_text("existing bindings\n")
        for kind in ("symlink", "dangling", "directory", "fifo"):
            with self.subTest(kind=kind):
                destination = self.root / "existing.kdl"
                destination.write_text("keep me\n")
                if kind in ("symlink", "dangling"):
                    self.target.symlink_to(destination if kind == "symlink" else self.root / "absent")
                elif kind == "directory":
                    self.target.mkdir()
                else:
                    os.mkfifo(self.target)
                before = self.target.lstat()
                self.assertEqual(self.activate().returncode, 0)
                self.assertEqual(self.target.lstat(), before)
                self.assertEqual(destination.read_text(), "keep me\n")
                self.assertEqual(neighbor.read_text(), "existing bindings\n")
                if kind == "directory":
                    self.target.rmdir()
                else:
                    self.target.unlink()

    def test_concurrent_first_activation_does_not_replace_file(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.activate(), range(16)))
        self.assertTrue(all(result.returncode == 0 for result in results))
        self.assertEqual(self.target.read_bytes(), b"")
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o600)

    def test_dry_run_creates_nothing(self):
        self.assertEqual(self.activate(dry_run=True).returncode, 0)
        self.assertFalse(self.config.exists())

    def test_parent_failure_is_reported(self):
        self.config.write_text("not a directory\n")
        self.assertNotEqual(self.activate().returncode, 0)
        self.assertEqual(self.config.read_text(), "not a directory\n")


if __name__ == "__main__":
    unittest.main()
