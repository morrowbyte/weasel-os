#!/usr/bin/env python3
"""Exercise the actual Restic file selection and an independently restored daily snapshot."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('cloud_backup', ROOT / 'scripts/weasel-cloud-backup.py')
cloud = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cloud)


class BackupTests(unittest.TestCase):
    def test_crew_copy_finishes_while_other_connection_keeps_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            database = home / cloud.crew.DATABASES[0]
            database.parent.mkdir(parents=True)
            connect = sqlite3.connect
            writer = connect(database)
            writer.execute('PRAGMA journal_mode=WAL')
            writer.execute('CREATE TABLE version (value INTEGER)')
            writer.execute('INSERT INTO version VALUES (0)')
            writer.execute('CREATE TABLE payload (data BLOB)')
            writer.executemany('INSERT INTO payload VALUES (zeroblob(8192))', [()] * 600)
            writer.commit()
            admit = home / '.local/libexec/agent-storage-admit'
            admit.parent.mkdir(parents=True)
            admit.write_text('#!/bin/sh\nprintf \'{"allowed":true}\\n\'\n')
            admit.chmod(0o700)
            writes = []

            class WritingDuringCopy(sqlite3.Connection):
                def backup(self, target, **kwargs):
                    progress = kwargs['progress']

                    def write_between_steps(status, remaining, total):
                        if remaining:
                            if len(writes) >= 8:
                                raise AssertionError('Concurrent writes repeatedly restarted the copy')
                            writer.execute('UPDATE version SET value = value + 1')
                            writer.commit()
                            writes.append(remaining)
                        progress(status, remaining, total)

                    kwargs['progress'] = write_between_steps
                    return super().backup(target, **kwargs)

            def source_connection(path, **kwargs):
                if kwargs.get('uri'):
                    kwargs['factory'] = WritingDuringCopy
                return connect(path, **kwargs)

            try:
                with patch.object(cloud.crew.sqlite3, 'connect', side_effect=source_connection):
                    with cloud.crew.prepare(home):
                        copies = list((home / '.local/share/weasel-backup').glob('crew-*'))
                        with connect(copies[0] / cloud.crew.DATABASES[0]) as copied:
                            self.assertEqual(copied.execute('SELECT value FROM version').fetchone(), (0,))
                            self.assertEqual(copied.execute('SELECT count(*) FROM payload').fetchone(), (600,))
                self.assertGreater(len(writes), 0, 'The test must write during the actual copy')
                self.assertEqual(writer.execute('SELECT value FROM version').fetchone(), (len(writes),))
            finally:
                writer.close()

    def test_failed_snapshot_readback_is_not_success(self):
        output = json.dumps({'message_type': 'summary', 'snapshot_id': 'a' * 64})
        with self.assertRaises(RuntimeError):
            cloud.verify_snapshot(lambda *args: '[]', output, 'personal-state', 'daily')

    def test_weekly_check_precedes_retention_and_failure_blocks_deletion(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            for name in ['last-success.json', 'restore-proof.json']:
                (state / name).write_text('{}')
            calls = []

            def fail_check(*args):
                calls.append(args)
                raise RuntimeError('damaged repository')

            with self.assertRaises(RuntimeError):
                cloud.maintain(fail_check, state)
            self.assertEqual([call[0] for call in calls], ['check'])
            self.assertFalse((state / 'last-maintain-success.json').exists())

    def test_unknown_staging_contents_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            stage = parent / 'crew-old'
            stage.mkdir()
            (stage / 'manifest.json').write_text(json.dumps({
                'producer': 'weasel-cloud-backup', 'version': 1}))
            (stage / 'unexpected-user-file').write_text('preserve')
            with self.assertRaises(RuntimeError):
                cloud.crew.cleanup_stages(parent)
            self.assertEqual((stage / 'unexpected-user-file').read_text(), 'preserve')

    def test_interrupted_stages_are_removed_even_without_live_profiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            unpublished = home / '.local/state/weasel-backup/crew-staging/building-interrupted'
            unpublished.mkdir(parents=True)
            (unpublished / 'manifest.json').write_text('{unfinished')
            published = home / '.local/share/weasel-backup/crew-old'
            published.mkdir(parents=True)
            (published / 'manifest.json').write_text(json.dumps({
                'producer': 'weasel-cloud-backup', 'version': 1, 'complete': True,
                'databases': [cloud.crew.DATABASES[0]]}))
            with cloud.crew.prepare(home) as excludes:
                self.assertEqual(excludes, [])
                self.assertFalse(unpublished.exists())
                self.assertFalse(published.exists())

    def test_interrupted_cleanup_is_already_outside_the_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            published = home / '.local/share/weasel-backup/crew-old'
            published.mkdir(parents=True)
            (published / 'manifest.json').write_text('{}')
            quarantine = home / '.local/state/weasel-backup/crew-staging'

            def interrupted(path):
                (path / 'manifest.json').unlink()
                raise KeyboardInterrupt()

            with patch.object(cloud.crew.shutil, 'rmtree', side_effect=interrupted):
                with self.assertRaises(KeyboardInterrupt):
                    cloud.crew.retire_stage(published, quarantine)
            self.assertFalse(published.exists())
            with cloud.crew.prepare(home) as excludes:
                self.assertEqual(excludes, [])
                self.assertFalse(list(quarantine.iterdir()))

    @unittest.skipUnless(shutil.which('restic'), 'Restic is required for the restore regression')
    def test_real_restic_preserves_crew_and_saves_without_downloads_or_parent_chain(self):
        # A fixture below ~/.cache would itself match the real cache exclusion.
        with tempfile.TemporaryDirectory(dir=os.environ.get('XDG_RUNTIME_DIR', '/tmp')) as tmp:
            base = Path(tmp)
            home = base / 'home'
            home.mkdir()
            env = dict(os.environ, RESTIC_REPOSITORY=str(base / 'repository'),
                       RESTIC_PASSWORD='isolated-test-repository', RESTIC_CACHE_DIR=str(base / 'cache'))

            def restic(*args):
                return subprocess.run(['restic', *args], env=env, check=True,
                                      capture_output=True, text=True).stdout

            def write(name, value='keep'):
                path = home / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(value)

            preserved = [
                '.t3-morrowbyte/userdata/attachments/user.txt',
                '.t3-morrowbyte/userdata/settings.json',
                '.codex/sessions/conversation.jsonl',
                '.local/share/Steam/steamapps/compatdata/1/pfx/drive_c/users/steamuser/save.dat',
                '.factorio/saves/world.zip',
                '.local/share/factorio-companion/voice-turns/recording',
            ]
            omitted = [
                '.local/share/pnpm/store/package',
                '.config/google-chrome/OptGuideOnDeviceModel/model',
                '.local/share/Steam/steamapps/common/game',
                '.local/share/Steam/steamapps/workshop/mod',
                '.local/share/Steam/compatibilitytools.d/proton',
                '.local/share/docker/overlay2/layer',
                '.local/share/factorio-companion/models/download.gguf',
                '.local/state/development-orchestrator/ship-integration-20261015/test/checkout/source',
            ]
            for name in preserved + omitted:
                write(name)
            database = home / cloud.crew.DATABASES[0]
            connection = sqlite3.connect(database)
            connection.execute('PRAGMA journal_mode=WAL')
            connection.execute('PRAGMA wal_autocheckpoint=0')
            connection.execute('CREATE TABLE messages (text TEXT)')
            connection.execute("INSERT INTO messages VALUES ('first')")
            connection.commit()
            admit = home / '.local/libexec/agent-storage-admit'
            write(str(admit.relative_to(home)), '#!/bin/sh\nprintf \'{"allowed":true}\\n\'\n')
            admit.chmod(0o700)
            restic('init')
            snapshots = []
            try:
                for cycle in ['weekly', 'daily']:
                    if cycle == 'daily':
                        connection.execute("INSERT INTO messages VALUES ('second, still in WAL')")
                        connection.commit()
                    with cloud.crew.prepare(home) as extra:
                        excludes = base / 'excludes'
                        excludes.write_text(cloud.backup_excludes(
                            home, (ROOT / 'config/backup-excludes.txt').read_text() + '\n' + '\n'.join(extra)))
                        output = restic('backup', '--json', '--host', 'nixy-laptop', '--tag', 'personal-state',
                                        '--tag', cycle, '--group-by', 'host,paths', '--exclude-file', str(excludes), str(home))
                        snapshots.append(cloud.verify_snapshot(restic, output, 'personal-state', cycle, home))
                rows = json.loads(restic('snapshots', '--json', snapshots[1]))
                self.assertEqual(rows[0]['parent'], snapshots[0])
                # The daily snapshot still restores after its weekly parent is
                # forgotten and unreferenced repository objects are pruned.
                restic('forget', snapshots[0], '--prune')
                destination = base / 'restore'
                restic('restore', snapshots[1], '--target', str(destination))
                cloud.crew.restore(destination, home)
                restored_home = destination / str(home).lstrip('/')
                for name in preserved:
                    self.assertEqual((restored_home / name).read_text(), 'keep')
                for name in omitted:
                    self.assertFalse((restored_home / name).exists(), name)
                with sqlite3.connect(restored_home / cloud.crew.DATABASES[0]) as restored:
                    self.assertEqual(restored.execute('SELECT text FROM messages').fetchall(),
                                     [('first',), ('second, still in WAL',)])
                self.assertFalse(list((home / '.local/share/weasel-backup').glob('crew-*')))
            finally:
                connection.close()


if __name__ == '__main__':
    unittest.main()
