#!/usr/bin/env python3
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
import urllib.request
import urllib.error

ROOT = Path(__file__).resolve().parents[1]


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


retention = load('retention', 'weasel-storage-retention.py')
transport = load('transport', 'proton-restic-server.py')
backup = load('backup', 'weasel-cloud-backup.py')


class MemoryDrive:
    def __init__(self):
        self.lock = threading.RLock()
        self.objects = {}

    def folders(self):
        pass

    def listing(self, kind):
        return {n: len(b) for (k, n), b in self.objects.items() if k == kind}

    def read(self, kind, name):
        try:
            return self.objects[kind, name]
        except KeyError:
            raise FileNotFoundError(name)

    def write(self, kind, name, payload):
        if name != 'config' and hashlib.sha256(payload).hexdigest() != name:
            raise ValueError('Hash mismatch')
        self.objects[kind, name] = payload

    def delete(self, kind, name):
        self.objects.pop((kind, name), None)


class RecoveryTests(unittest.TestCase):
    def test_interrupted_trash_retries_only_recorded_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            drive = transport.Drive('unused', '/my-files/test', Path(tmp) / 'spool')
            name = 'a' * 64
            key = drive.remote('locks', name)
            drive.pending[key] = 'original-uid'
            active = {name: {'uid': 'original-uid', 'type': 'file',
                             'name': {'ok': True, 'value': name}}}
            trash = []
            operations = []
            def command(action, path):
                operations.append((action, path))
                if action == 'list':
                    return trash if path == '/trash' else list(active.values())
                if action == 'trash':
                    trash.append(active.pop(name))
                elif action == 'delete':
                    trash.clear()
            drive.command = command
            drive.catalogue['locks'] = {name: 10}
            drive.delete('locks', name)
            self.assertIn(('trash', key), operations)
            self.assertIn(('delete', '/trash/' + name), operations)
            self.assertFalse(drive.pending)
            active[name] = {'uid': 'replacement-uid', 'type': 'file',
                            'name': {'ok': True, 'value': name}}
            drive.pending[key] = 'original-uid'
            operations.clear()
            with self.assertRaises(RuntimeError):
                drive.delete('locks', name)
            self.assertFalse(any(action != 'list' for action, _ in operations))

    def test_booted_and_current_generations_survive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for n in range(1, 9):
                target = root / str(n)
                target.mkdir()
                (root / f'system-{n}-link').symlink_to(target)
            (root / 'system').symlink_to(root / '8')
            plan = retention.generation_plan(root / 'system', protected=(root / '1', root / '4'))
            self.assertEqual(plan['keep'], [1, 4, 5, 6, 7, 8])
            self.assertEqual(plan['delete'], [2, 3])

    def test_transaction_and_explicit_pins_survive(self):
        rows = [{'number': n, 'userdata': {}} for n in range(9)]
        rows[1]['userdata'] = {'weasel-daily-update': 'yes'}
        rows[2]['userdata'] = {'weasel-retain': 'yes'}
        self.assertEqual(retention.snapshot_plan(rows)['keep'], [1, 2, 6, 7, 8])

    def test_source_relative_excludes_real_restic_dryrun(self):
        restic = os.environ.get('TEST_RESTIC')
        if not restic:
            self.skipTest('TEST_RESTIC needs installed restic path')
        excluded = [
            '.local/share/Steam/steamapps/common/game/payload',
            '.local/share/Trash/files/trashed', '.cache/rebuildable',
            'source/project/node_modules/dependency',
            'source/project/target/debug/binary',
            'source/system/services/docker/paperless-ngx/db/retained',
        ]
        included = [
            'source/project/main.rs', 'source/project/.git/config',
            '.codex/sessions/turn.jsonl', '.config/application/profile',
            '.local/share/application/database.sqlite',
            '.local/share/Steam/userdata/save',
            '.local/share/weasel-retired-services/paperless.tar.zst',
            'source/system/services/docker/other-service/db/keep',
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'home'
            for name in excluded + included:
                item = source / name
                item.parent.mkdir(parents=True, exist_ok=True)
                item.write_text('fixture')
            env = dict(os.environ, RESTIC_PASSWORD='local-fixture-only',
                       RESTIC_REPOSITORY=str(root / 'repo'))
            def run(*args):
                result = subprocess.run([restic, '--no-cache', *args], env=env,
                                        cwd=root, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                return result.stdout
            run('init')
            patterns = (ROOT / 'config/backup-excludes.txt').read_text()
            absolute = backup.backup_excludes(source, patterns)
            old_cwd = Path.cwd()
            try:
                os.chdir(root)
                relative = backup.backup_excludes(Path('home'), patterns)
            finally:
                os.chdir(old_cwd)
            self.assertEqual(absolute, relative)
            self.assertIn('**/node_modules', absolute.splitlines())
            self.assertIn(str(source) + '/.local/share/Trash', absolute.splitlines())
            rules = root / 'rules'
            rules.write_text(absolute)
            for spelling in (str(source), 'home'):
                output = run('backup', '--dry-run', '--json', '--verbose=2',
                             '--exclude-file', str(rules), spelling)
                events = [json.loads(line) for line in output.splitlines()]
                files = {row['item'] for row in events
                         if row.get('message_type') == 'verbose_status'
                         and row.get('action') == 'new' and row['item'] != ''}
                # Restic reports relative sources under /<source-name>, even
                # though anchored exclusion matching uses their absolute path.
                reported = source if Path(spelling).is_absolute() else Path('/home')
                for name in excluded:
                    self.assertNotIn(str(reported / name), files, spelling + ': ' + name)
                for name in included:
                    self.assertIn(str(reported / name), files, spelling + ': ' + name)
            self.assertEqual(json.loads(run('snapshots', '--json')), [])

    def test_archive_mode_rejected_before_cloud_access(self):
        result = subprocess.run([
            os.sys.executable, str(ROOT / 'scripts/weasel-cloud-backup.py'),
            'archive', '--excludes', str(ROOT / 'config/backup-excludes.txt')],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('invalid choice', result.stderr)

    def test_real_restic_roundtrip_and_access_control(self):
        restic = os.environ.get('TEST_RESTIC')
        if not restic:
            self.skipTest('TEST_RESTIC needs installed restic path')
        server = transport.serve(MemoryDrive(), 'test-token', allow_delete=True)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'source'
            source.mkdir()
            data = os.urandom(1024 * 1024)
            (source / 'personal').write_bytes(data)
            (source / 'link').symlink_to('personal')
            cache = source / 'node_modules'
            cache.mkdir()
            (cache / 'reinstallable').write_text('not needed')
            env = dict(os.environ, RESTIC_REPOSITORY=f'rest:http://weasel:test-token@127.0.0.1:{server.server_port}/',
                       RESTIC_PASSWORD='test-repository-password')
            def run(*args):
                result = subprocess.run([restic, '--no-cache', *args], env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            try:
                run('init')
                run('backup', '--exclude', '**/node_modules', str(source))
                run('check', '--read-data')
                run('restore', 'latest', '--target', str(root / 'restore'))
                restored = root / 'restore' / str(source).lstrip('/')
                self.assertEqual((restored / 'personal').read_bytes(), data)
                self.assertEqual(os.readlink(restored / 'link'), 'personal')
                self.assertFalse((restored / 'node_modules').exists())
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}/config')
                self.assertEqual(caught.exception.code, 401)
                run('forget', '--keep-last', '1', '--prune')
            finally:
                server.shutdown()
                server.server_close()


if __name__ == '__main__':
    unittest.main()
