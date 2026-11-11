#!/usr/bin/env python3
"""The temporary root snapshot must never pin garbage after an upload ends."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    'system_backup', Path(__file__).resolve().parents[1] / 'scripts/weasel-backup-system.py')
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)


class LifecycleTests(unittest.TestCase):
    def run_backup(self, failure=None):
        events = []

        def run(command, **kwargs):
            action = 'check' if 'check' in command else 'upload'
            events.append(action)
            if action == failure:
                raise subprocess.CalledProcessError(1, command)

        with patch.object(backup, 'create_snapshot', side_effect=lambda: events.append('create')), \
                patch.object(backup, 'remove_snapshot', side_effect=lambda: events.append('remove')), \
                patch.object(backup.subprocess, 'run', side_effect=run):
            if failure:
                with self.assertRaises(subprocess.CalledProcessError):
                    backup.backup_snapshot('restic', {})
            else:
                backup.backup_snapshot('restic', {})
        return events

    def test_success_releases_snapshot(self):
        self.assertEqual(self.run_backup(), ['create', 'upload', 'remove'])

    def test_upload_failure_releases_snapshot(self):
        self.assertEqual(self.run_backup('upload'), ['create', 'upload', 'remove'])

    def test_excludes_docker_and_keeps_system_paths(self):
        commands = []
        with patch.object(backup, 'create_snapshot'), patch.object(backup, 'remove_snapshot'), \
                patch.object(backup.subprocess, 'run', side_effect=lambda command, **kw: commands.append(command)):
            backup.backup_snapshot('restic', {}, 'weekly')
        self.assertEqual(len(commands), 1)
        self.assertIn(str(backup.SNAPSHOT / 'var/lib/docker'), commands[0])
        self.assertIn(str(backup.SNAPSHOT / 'etc'), commands[0])
        self.assertIn('weekly', commands[0])

    def test_partial_creation_failure_releases_snapshot(self):
        with patch.object(backup, 'create_snapshot', side_effect=OSError('readiness write failed')), \
                patch.object(backup, 'remove_snapshot') as remove:
            with self.assertRaises(OSError):
                backup.backup_snapshot('restic', {})
            remove.assert_called_once_with()

    def test_cleanup_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            snapshot = state / 'snapshot'
            snapshot.symlink_to('/nix')
            with patch.object(backup, 'STATE', state), patch.object(backup, 'SNAPSHOT', snapshot), \
                    patch.object(backup.subprocess, 'run') as run:
                with self.assertRaisesRegex(RuntimeError, 'Unsafe snapshot'):
                    backup.remove_snapshot()
                run.assert_not_called()

    def test_missing_snapshot_clears_stale_readiness(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / 'ready.json').write_text('{}')
            with patch.object(backup, 'STATE', state), patch.object(backup, 'SNAPSHOT', state / 'snapshot'), \
                    patch.object(backup.subprocess, 'run') as run:
                backup.remove_snapshot()
                run.assert_not_called()
                self.assertFalse((state / 'ready.json').exists())


if __name__ == '__main__':
    unittest.main()
