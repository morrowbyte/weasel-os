#!/usr/bin/env python3
"""Preparation refusal and concurrency invariants on disposable real files."""
import importlib.util
import datetime as dt
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('preparation', ROOT / 'scripts/weasel-update-prepare.py')
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


class PreparationTests(unittest.TestCase):
    def test_exclusive_publication_keeps_concurrent_request(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            old, new = directory / 'request.json', directory / 'new.json'
            old.write_bytes(b'concurrent')
            new.write_bytes(b'tested')
            with self.assertRaises(FileExistsError):
                p.publish_exclusive(new, old)
            self.assertEqual(old.read_bytes(), b'concurrent')
            self.assertEqual(new.read_bytes(), b'tested')

    def test_published_inode_is_complete_and_single_link(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            staged, destination = directory / 'staged', directory / 'request.json'
            staged.write_bytes(b'{"complete":true}')
            inode = staged.stat().st_ino
            p.publish_exclusive(staged, destination)
            self.assertEqual(destination.stat().st_ino, inode)
            self.assertEqual(destination.stat().st_nlink, 1)
            self.assertFalse(staged.exists())

    def test_candidate_id_cannot_escape_namespace(self):
        for value in ['../documents', '20261008T120000Z-aaaaaaaa/../../x', 'x', '20261008T120000Z-AAAA0000']:
            with self.assertRaises(p.Blocked):
                p.candidate_directory(Path('/tmp/unused'), value)

    def test_state_file_symlink_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            real = directory / 'real.json'
            real.write_text('{"value":1}')
            link = directory / 'request.json'
            link.symlink_to(real)
            with self.assertRaises(OSError):
                p.read_json(link)

    def test_candidate_writer_and_reader_keep_existing_one_mib_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            candidate = Path(folder) / 'candidate.json'
            candidate.write_bytes(b'{"phase":"tested"}\n')
            oversized = {'phase': 'tested', 'evidence': 'x' * (1024 * 1024)}
            with self.assertRaisesRegex(p.Blocked, 'Candidate document exceeds size bound'):
                p.atomic_write(candidate, p.encoded_candidate(oversized))
            self.assertEqual(candidate.read_bytes(), b'{"phase":"tested"}\n')
            candidate.write_bytes(json.dumps(oversized).encode())
            with self.assertRaisesRegex(p.Blocked, 'Input document exceeds size bound'):
                p.read_json(candidate)

    def test_battery_and_power_failure_are_real_refusals(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            for name, values in [('AC', {'type': 'Mains', 'online': '1'}), ('BAT0', {'type': 'Battery', 'capacity': '90'})]:
                target = directory / name
                target.mkdir()
                for key, value in values.items():
                    (target / key).write_text(value)
            p.require_power(directory)
            (directory / 'AC/online').write_text('0')
            with self.assertRaises(p.Blocked):
                p.require_power(directory)
            (directory / 'AC/online').write_text('1')
            (directory / 'BAT0/capacity').write_text('29')
            with self.assertRaises(p.Blocked):
                p.require_power(directory)

    def test_all_unresolved_transaction_phases_block_new_candidates(self):
        with tempfile.TemporaryDirectory() as folder:
            status = Path(folder) / 'status.json'
            with patch.object(p, 'STATUS', status):
                for phase in ['recovery-required', 'checkpoint-created', 'activating', 'publishing', 'rolling-back-system']:
                    status.write_text(json.dumps({'phase': phase}))
                    with self.assertRaises(p.Blocked):
                        p.require_resolved()
                status.write_text(json.dumps({'phase': 'complete'}))
                p.require_resolved()

    def test_hidden_working_file_save_changes_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            repo = Path(folder)
            subprocess.run(['git', 'init', '-q', '-b', 'main', repo], check=True)
            subprocess.run(['git', '-C', repo, 'config', 'user.name', 'Fixture'], check=True)
            subprocess.run(['git', '-C', repo, 'config', 'user.email', 'fixture@invalid'], check=True)
            file = repo / 'source.nix'
            file.write_text('old source')
            subprocess.run(['git', '-C', repo, 'add', 'source.nix'], check=True)
            subprocess.run(['git', '-C', repo, '-c', 'commit.gpgsign=false', 'commit', '-qm', 'fixture'], check=True)
            before = p.source_manifest(repo)
            subprocess.run(['git', '-C', repo, 'update-index', '--assume-unchanged', 'source.nix'], check=True)
            # Ignore the flag's index change: capture it before the editor save.
            before = p.source_manifest(repo)
            file.write_text('new unsaved-to-git source')
            self.assertNotEqual(p.source_manifest(repo), before)

    def test_review_coverage_cannot_silently_drop_unknown_pin(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            registry = directory / 'registry.json'
            registry.write_text(json.dumps({'entries': [{'id': 't3'}, {'id': 'unknown-pin'}]}))
            review = directory / 'review.json'
            review.write_text(json.dumps({'entries': [{'id': 't3', 'decision': 'unchanged'}]}))
            with patch.object(p, 'registry_path', return_value=registry):
                with self.assertRaisesRegex(p.Blocked, 'every registered'):
                    p.record_review(directory, review)

    def test_review_accepts_new_pin_and_dated_explicit_adapter_need(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            registry = directory / 'registry.json'
            registry.write_text(json.dumps({'entries': [{'id': 'known-pin'}]}))
            tomorrow = (dt.datetime.now(dt.timezone.utc).date() + dt.timedelta(days=1)).isoformat()
            entries = [{'id': name, 'decision': 'adapter-needed', 'current': '1.0.0',
                        'reason': 'Device hotplug needs a concrete probe',
                        'next_review': tomorrow, 'evidence_urls': ['https://example.invalid/release']}
                       for name in ['known-pin', 'new-pin']]
            review = directory / 'review.json'
            review.write_text(json.dumps({'schema_version': 1, 'entries': entries,
                                          'baseline_commit': 'a' * 40, 'lock_nodes': {}}))
            with patch.object(p, 'registry_path', return_value=registry), patch.object(
                    p, 'review_baseline', return_value={'commit': 'a' * 40, 'lock_sha256': 'b' * 64, 'nodes': {}}):
                result = p.record_review(directory, review)
            self.assertEqual(result['entries'], 2)

    def test_transitive_lock_node_coverage_and_revision_are_explicit(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            registry = directory / 'registry.json'
            registry.write_text(json.dumps({'entries': []}))
            review = directory / 'review.json'
            locked = {'type': 'github', 'owner': 'fixture', 'repo': 'upstream', 'rev': 'c' * 40}
            baseline = {'commit': 'a' * 40, 'lock_sha256': 'b' * 64,
                        'nodes': {'transitive': locked}}
            finding = {'current_locked': locked, 'decision': 'investigate',
                       'reason': 'Follow parent update; upstream freshness remains unverified',
                       'next_review': (dt.datetime.now(dt.timezone.utc).date() + dt.timedelta(days=1)).isoformat(),
                       'evidence': ['repo:flake.lock#nodes.transitive']}
            value = {'entries': [], 'baseline_commit': baseline['commit'], 'lock_nodes': {}}
            with patch.object(p, 'registry_path', return_value=registry), patch.object(
                    p, 'review_baseline', return_value=baseline):
                review.write_text(json.dumps(value))
                with self.assertRaisesRegex(p.Blocked, 'every resolved'):
                    p.record_review(directory, review)
                value['lock_nodes']['transitive'] = finding
                value['lock_nodes']['transitive']['current_locked'] = dict(locked, rev='d' * 40)
                review.write_text(json.dumps(value))
                with self.assertRaisesRegex(p.Blocked, 'exact source'):
                    p.record_review(directory, review)
                finding['current_locked'] = locked
                value['baseline_commit'] = 'e' * 40
                review.write_text(json.dumps(value))
                with self.assertRaisesRegex(p.Blocked, 'actual current'):
                    p.record_review(directory, review)
                value['baseline_commit'] = baseline['commit']
                review.write_text(json.dumps(value))
                result = p.record_review(directory, review)
                self.assertEqual(result['resolved_lock_nodes'], 1)
                receipt = json.loads(Path(result['path']).read_text())
                self.assertEqual(receipt['coverage']['lock_sha256'], baseline['lock_sha256'])

    def test_review_source_race_cannot_publish_coverage(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            registry = directory / 'registry.json'
            registry.write_text(json.dumps({'entries': []}))
            review = directory / 'review.json'
            baseline = {'commit': 'a' * 40, 'lock_sha256': 'b' * 64, 'nodes': {}}
            review.write_text(json.dumps({'entries': [], 'baseline_commit': baseline['commit'], 'lock_nodes': {}}))
            with patch.object(p, 'registry_path', return_value=registry), patch.object(
                    p, 'review_baseline', side_effect=[baseline, dict(baseline, lock_sha256='c' * 64)]):
                with self.assertRaisesRegex(p.Blocked, 'Source changed'):
                    p.record_review(directory, review)
            self.assertFalse((directory / 'reviews').exists())

    def test_allowlisted_helper_cannot_overwrite_personal_output(self):
        with self.assertRaises(p.Blocked):
            p.check_cli_paths(p.REPOSITORY, p.STATE, Path('/home/evilweasel/.codex/auth.json'))

    def test_explicit_output_preserves_file_created_after_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            receipt = Path(folder) / 'receipt.json'
            p.check_cli_paths(p.REPOSITORY, p.STATE, receipt)
            receipt.write_bytes(b'concurrent save')
            with self.assertRaises(FileExistsError):
                p.write_receipt_exclusive(receipt, b'new receipt')
            self.assertEqual(receipt.read_bytes(), b'concurrent save')

    def test_explicit_output_parent_swap_cannot_follow_symlink(self):
        with tempfile.TemporaryDirectory() as folder:
            parent = Path(folder) / 'receipts'
            other = Path(folder) / 'personal'
            parent.mkdir()
            other.mkdir()
            receipt = parent / 'auth.json'
            p.check_cli_paths(p.REPOSITORY, p.STATE, receipt)
            parent.rmdir()
            parent.symlink_to(other, target_is_directory=True)
            with self.assertRaises(OSError):
                p.write_receipt_exclusive(receipt, b'new receipt')
            self.assertFalse((other / 'auth.json').exists())

    def test_interrupted_unsubmitted_candidate_is_retained_and_resolved(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            identifier = '20261008T120000Z-1234abcd'
            directory = state / 'candidates' / identifier
            directory.mkdir(parents=True)
            marker = directory / 'candidate.json'
            marker.write_text(json.dumps({'id': identifier, 'source': str(directory / 'source'),
                                          'phase': 'building'}))
            with patch.object(p, 'STATUS', state / 'absent-status'):
                p.prune_candidates(Path('/unused/repository'), state)
            self.assertTrue(directory.exists())
            self.assertEqual(json.loads(marker.read_text())['phase'], 'failed')

    def test_prune_preserves_editor_changes_untracked_ignored_and_open_files(self):
        for change in ['tracked', 'hidden', 'untracked', 'ignored', 'open', 'extra-artifact', 'clean', 'profile-outlink', 'profile-outlink-with-foreign']:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                repo, state = root / 'main', root / 'state'
                subprocess.run(['git', 'init', '-q', '-b', 'main', repo], check=True)
                subprocess.run(['git', '-C', repo, 'config', 'user.name', 'Fixture'], check=True)
                subprocess.run(['git', '-C', repo, 'config', 'user.email', 'fixture@invalid'], check=True)
                (repo / 'source.nix').write_text('tested source')
                (repo / '.gitignore').write_text('ignored.txt\n')
                subprocess.run(['git', '-C', repo, 'add', '.'], check=True)
                subprocess.run(['git', '-C', repo, '-c', 'commit.gpgsign=false', 'commit', '-qm', 'fixture'], check=True)
                identifier = '20261008T120000Z-1234abcd'
                directory = state / 'candidates' / identifier
                directory.mkdir(parents=True)
                source = directory / 'source'
                subprocess.run(['git', '-C', repo, 'worktree', 'add', '-q', '-b',
                                'weasel-update-' + identifier, source], check=True)
                manifest = p.source_manifest(source)
                (directory / 'candidate.json').write_text(json.dumps({
                    'id': identifier, 'source': str(source), 'phase': 'tested',
                    'candidate_commit': manifest['head'], 'candidate_manifest': manifest}))
                stream = None
                profile_target = None
                if change.startswith('profile-outlink'):
                    profile_target = root / 'owned-profile-fixture'
                    profile_target.mkdir()
                    (profile_target / 'preserve.txt').write_text('external profile target stays intact')
                    (directory / 'tested-codex-profile').symlink_to(profile_target, target_is_directory=True)
                    if change == 'profile-outlink-with-foreign':
                        (directory / 'personal-notes.txt').write_text('foreign data stays intact')
                if change in {'tracked', 'hidden'}:
                    if change == 'hidden':
                        subprocess.run(['git', '-C', source, 'update-index', '--assume-unchanged', 'source.nix'], check=True)
                    (source / 'source.nix').write_text('later editor save')
                elif change in {'untracked', 'ignored'}:
                    (source / ('ignored.txt' if change == 'ignored' else 'notes.txt')).write_text('personal notes')
                elif change == 'open':
                    stream = (source / 'source.nix').open('rb')
                elif change == 'extra-artifact':
                    (directory / 'personal-notes.txt').write_text('preserve')
                try:
                    with patch.object(p, 'STATUS', root / 'absent-status'):
                        p.prune_candidates(repo, state, keep=0)
                    self.assertEqual(directory.exists(), change not in {'clean', 'profile-outlink'})
                    if profile_target is not None:
                        self.assertEqual((profile_target / 'preserve.txt').read_text(), 'external profile target stays intact')
                    if change == 'profile-outlink-with-foreign':
                        self.assertEqual((directory / 'personal-notes.txt').read_text(), 'foreign data stays intact')
                    if change in {'tracked', 'hidden'}:
                        self.assertEqual((source / 'source.nix').read_text(), 'later editor save')
                finally:
                    if stream:
                        stream.close()


class BatchPreparationTests(unittest.TestCase):
    """Use real Git trees and requests; replace Nix workloads and fixture signing."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='weasel-update-batch-prepare-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo, self.state = self.root / 'main', self.root / 'state'
        self.repo.mkdir()
        self.state.mkdir(mode=0o700)
        self.original_command = p.command
        for arguments in [
                ['init', '-q', '-b', 'main'], ['config', 'user.name', 'Fixture'],
                ['config', 'user.email', 'fixture@example.invalid'],
                ['config', 'commit.gpgsign', 'false'], ['config', 'core.hooksPath', '/dev/null']]:
            p.git(self.repo, *arguments)
        for name, content in {
                'flake.nix': '{ inputs = {}; }\n',
                'flake.lock': '{"nodes":{"root":{"inputs":{}}},"root":"root","version":7}\n',
                '.gitignore': 'ignored.txt\n',
                'packages/demo.nix': '{ version = "1.0.0"; }\n',
                'agent-learnings.md': '# Previous learning\n'}.items():
            target = self.repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        p.git(self.repo, 'add', '.')
        p.git(self.repo, 'commit', '-qm', 'fixture baseline')
        self.baseline_commit = p.git(self.repo, 'rev-parse', 'HEAD').decode().strip()
        self.old_system = Path('/nix/store/' + 'a' * 32 + '-nixos-system-nixy-laptop-25.11.fixture')
        self.new_system = Path('/nix/store/' + 'b' * 32 + '-nixos-system-nixy-laptop-25.11.fixture')
        current, profile = self.root / 'current', self.root / 'profile'
        current.symlink_to(self.old_system)
        profile.symlink_to(self.old_system)
        self.batch, self.gates = p.peer('batch'), p.peer('gates')
        from types import SimpleNamespace
        def general_ssh_fixture(expression, before, after, host, evaluator, executor):
            # This real-Git orchestration fixture declares no NixOS inputs.
            # Real canonical SSH proof is covered by its separate suites and
            # mandatory runtime evidence; verify the immutable executor is
            # passed even when these source/package workloads are mocked.
            self.assertEqual(executor('nix', ['eval', '--expr', 'fixture-fresh-ssh-proof']), b'f' * 64)
            return {'old': evaluator(expression(before, host)).decode().strip(),
                    'new': evaluator(expression(after, host)).decode().strip(),
                    'coverage': 'Mocked SSH orchestration only; no daemon'}
        self.ssh_adapter = SimpleNamespace(verify_general=general_ssh_fixture)
        self.ssh_spec = SimpleNamespace(loader=SimpleNamespace(exec_module=lambda module: None))
        self.sign_requests, self.verified, self.workloads, self.probes = [], [], [], []
        self.profile_checks = []
        self.changed_apps = {'t3', 'codex', 'chatgpt', 'acp'}
        self.niri_ok = True
        for patcher in [
                patch.object(p, 'STATUS', self.root / 'absent-status'),
                patch.object(p, 'CURRENT', current), patch.object(p, 'PROFILE', profile),
                patch.object(p, 'candidate_id', return_value='20261008T140000Z-1234abcd'),
                patch.object(p, 'require_power'), patch.object(p, 'require_space'),
                patch.object(p, 'baseline', side_effect=lambda repo: (p.source_manifest(repo), self.old_system)),
                patch.object(p, 'command', side_effect=self.command),
                patch.object(p, 'build', side_effect=self.build),
                patch.object(p, 'peer', side_effect=lambda name: {'batch': self.batch, 'gates': self.gates}[name]),
                patch.object(self.batch.importlib.util, 'spec_from_file_location', return_value=self.ssh_spec),
                patch.object(self.batch.importlib.util, 'module_from_spec', return_value=self.ssh_adapter),
                patch.object(self.batch, 'verify_published', return_value={'ok': True, 'published_pins': {}}),
                patch.object(self.batch, 'verify_closure', side_effect=self.closure),
                # The real-Git fake-closure fixture has no installed NVIDIA/store graph.
                # Actual static file/ABI negatives live in test-update-batch.py.
                patch.object(self.batch, 'verify_nvidia_live_abi', return_value={
                    'ok': True, 'coverage': 'Mocked hardware boundary for preparation orchestration only'}),
                patch.object(self.gates, 'probe', side_effect=self.probe),
                patch.object(self.gates, 'verify_codex_profile', side_effect=self.profile_ownership)]:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.record = p.new_batch(self.repo, self.state, 'batch')
        self.source = Path(self.record['source'])
        self.directory = self.source.parent
        self.file = self.source / 'packages/demo.nix'
        self.file.write_text('{ version = "1.0.1"; }\n')

    def command(self, arguments, **kwargs):
        arguments = list(map(str, arguments))
        if len(arguments) > 1 and Path(arguments[1]).name == 'weasel-update-niri.py':
            self.workloads.append(arguments)
            self.assertEqual(arguments[arguments.index('--source') + 1], str(self.source))
            directory = Path(arguments[arguments.index('--run-dir') + 1])
            directory.mkdir(mode=0o700, parents=True)
            receipt = {'ok': self.niri_ok, 'kind': 'fixture-niri-config',
                       'receipt_path': str(directory / 'receipt.json')}
            (directory / 'receipt.json').write_text(json.dumps(receipt))
            return json.dumps(receipt).encode()
        if arguments[0] == 'git':
            if 'verify-commit' in arguments:
                self.verified.append(arguments[-1])
                return b''
            if 'commit' in arguments:
                # Exercise a real commit without accessing the user's GPG key.
                # Removing -S in production must still fail this regression.
                self.assertIn('-S', arguments)
                self.sign_requests.append(list(arguments))
                arguments.remove('-S')
                arguments[1:1] = ['-c', 'commit.gpgsign=false']
            return self.original_command(arguments, **kwargs)
        if arguments[0] == 'nix-instantiate' or arguments[:3] == ['nix', 'flake', 'check']:
            self.workloads.append(arguments)
            return b''
        if arguments[:2] == ['nix', 'eval']:
            self.workloads.append(arguments)
            if '--expr' in arguments:
                if 'config.system.nixos.release' in arguments[-1]:
                    return b'25.11'
                return b'f' * 64
            return ('/nix/store/' + 'c' * 32 + '-host.drv').encode()
        self.fail('Unexpected external fixture command: ' + arguments[0])

    def build(self, source, destination, arguments):
        self.workloads.append(['build', str(source), str(destination)])
        name = Path(destination).name
        if name == 'tested-system':
            return self.new_system
        kind = name.removeprefix('old-').removeprefix('new-').removeprefix('tested-')
        old = name.startswith('old-') or kind not in self.changed_apps
        return Path('/nix/store/' + ('d' if old else 'e') * 32 + '-' + kind + '-fixture')

    def profile_ownership(self, profile, codex, acp):
        self.profile_checks.append((profile, codex, acp))
        return {'ok': True, 'profile': str(profile), 'coverage': 'mocked orchestration only'}

    def probe(self, kind, app, directory, **kwargs):
        # Preserve the real gate's empty-directory contract, including retries.
        directory = self.gates._private_directory(Path(directory))
        receipt = {'ok': True, 'kind': kind, 'app': str(app), 'receipt_path': str(directory / 'receipt.json')}
        (directory / 'receipt.json').write_text(json.dumps(receipt))
        self.probes.append((kind, app, kwargs))
        return receipt

    def closure(self, old, new, app_pairs, changes):
        self.assertEqual((old, new), (self.old_system, self.new_system))
        self.assertEqual(set(app_pairs), {'t3', 'codex', 'chatgpt', 'codex-acp'})
        return {'ok': True, 'policy': 'fixture-build-inventory', 'source_changes': sorted(changes)}

    def prepare(self, *, source=None, mode='batch'):
        return p.prepare_batch(self.repo, self.state, source or self.source, mode)

    def test_nvidia_abi_failure_blocks_signing_and_tested_receipt(self):
        with patch.object(self.batch,'verify_nvidia_live_abi',side_effect=self.batch.BatchError('NVIDIA loaded ABI mismatch')):
            with self.assertRaisesRegex(self.batch.BatchError,'NVIDIA loaded ABI mismatch'):
                self.prepare()
        self.assert_not_committed()
        self.assertFalse(self.sign_requests)
        self.assertEqual(p.read_json(self.directory/'candidate.json')['phase'],'failed')

    def assert_not_committed(self):
        self.assertEqual(p.git(self.repo, 'rev-parse', self.record['candidate_ref']).decode().strip(), self.baseline_commit)
        self.assertEqual(self.sign_requests, [])

    def test_fresh_ssh_proof_executor_uses_fixed_preparer_and_cannot_sign_after_failure(self):
        calls = []
        def sensitive(old, new, evaluator, *, mode, migration_executor):
            self.assertEqual((old, new), (self.repo, self.source))
            self.assertEqual(mode, 'batch')
            self.assertEqual(evaluator('fixture-sensitive-expression'), b'f' * 64)
            self.assertEqual(migration_executor('nix', ['eval', '--expr', 'fixture-fresh-ssh-proof']), b'f' * 64)
            calls.append('fixed-executor')
            raise self.batch.BatchError('Fresh built SSH proof failed')
        with patch.object(self.batch, 'verify_sensitive_state', side_effect=sensitive):
            with self.assertRaisesRegex(self.batch.BatchError, 'Fresh built SSH'):
                self.prepare()
        self.assertEqual(calls, ['fixed-executor'])
        self.assertIn(['nix', 'eval', '--raw', '--impure', '--no-write-lock-file', '--offline',
                       '--max-jobs', '0', '--builders', '', '--option', 'substitute', 'false',
                       '--option', 'allow-import-from-derivation', 'true', '--expr',
                       'fixture-sensitive-expression'], self.workloads)
        self.assert_not_committed()
        self.assertEqual(self.probes, [])
        self.assertFalse(any(v[:3] == ['nix', 'flake', 'check'] for v in self.workloads))

    def test_missing_cached_sensitive_artifact_has_no_retry_build_or_sign(self):
        calls = []
        def command(arguments, **kwargs):
            arguments = list(map(str, arguments))
            if arguments[:2] == ['nix', 'eval'] and '--expr' in arguments and '--offline' in arguments:
                calls.append(arguments)
                raise p.Blocked('Missing cached IFD artifact')
            return self.command(arguments, **kwargs)
        with patch.object(p, 'command', side_effect=command):
            with self.assertRaisesRegex(self.batch.BatchError, 'Missing cached IFD artifact'):
                self.prepare()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1:-1], self.batch.sensitive_eval_args('ignored')[:-1])
        self.assert_not_committed()
        self.assertEqual(self.probes, [])
        self.assertFalse(any(v[:3] == ['nix', 'flake', 'check'] or v[0] == 'build' for v in self.workloads))
        self.assertEqual(p.read_json(self.directory / 'candidate.json')['phase'], 'failed')

    def test_foreign_worktree_path_and_source_symlink_are_refused(self):
        foreign = self.root / 'other-task' / self.directory.name / 'source'
        foreign.parent.mkdir(parents=True)
        p.git(self.repo, 'worktree', 'add', '-q', '-b', 'other-task', foreign)
        with self.assertRaises(p.Blocked):
            self.prepare(source=foreign)
        kept = self.directory / 'kept-source'
        self.source.rename(kept)
        self.source.symlink_to(kept, target_is_directory=True)
        with self.assertRaises(OSError):
            self.prepare()
        self.assertEqual((kept / 'packages/demo.nix').read_text(), '{ version = "1.0.1"; }\n')
        self.assertEqual(p.git(foreign, 'branch', '--show-current').decode().strip(), 'other-task')
        self.assert_not_committed()

    def test_wrong_mode_and_changed_main_baseline_are_refused(self):
        with self.assertRaises(p.Blocked):
            self.prepare(mode='release-migration')
        (self.repo / 'packages/demo.nix').write_text('later main editor save\n')
        with self.assertRaises(p.Blocked):
            self.prepare()
        self.assertEqual((self.repo / 'packages/demo.nix').read_text(), 'later main editor save\n')
        self.assert_not_committed()

    def test_changed_active_baseline_is_refused_before_building(self):
        with patch.object(p, 'baseline', return_value=(p.source_manifest(self.repo), self.new_system)):
            with self.assertRaises(p.Blocked):
                self.prepare()
        self.assertEqual(self.workloads, [])
        self.assert_not_committed()

    def test_owned_worktree_on_another_tasks_branch_is_preserved(self):
        p.git(self.source, 'switch', '-q', '-c', 'other-task')
        with self.assertRaises(p.Blocked):
            self.prepare()
        self.assertEqual(p.git(self.source, 'branch', '--show-current').decode().strip(), 'other-task')
        self.assertEqual(self.file.read_text(), '{ version = "1.0.1"; }\n')
        self.assert_not_committed()

    def test_new_tracked_source_is_refused_and_preserved(self):
        added = self.source / 'packages/new.nix'
        added.write_text('{ added = true; }\n')
        p.git(self.source, 'add', 'packages/new.nix')
        with self.assertRaises(p.Blocked):
            self.prepare()
        self.assertEqual(added.read_text(), '{ added = true; }\n')
        self.assert_not_committed()

    def test_deleted_source_is_refused_without_recreating_it(self):
        self.file.unlink()
        with self.assertRaises(p.Blocked):
            self.prepare()
        self.assertFalse(self.file.exists())
        self.assert_not_committed()

    def test_source_mode_change_and_symlink_are_refused(self):
        self.file.chmod(0o755)
        with self.assertRaises(p.Blocked):
            self.prepare()
        self.file.chmod(0o644)
        personal = self.root / 'personal-note'
        personal.write_text('private retained note\n')
        self.file.unlink()
        self.file.symlink_to(personal)
        with self.assertRaises(p.Blocked):
            self.prepare()
        self.assertTrue(self.file.is_symlink())
        self.assertEqual(personal.read_text(), 'private retained note\n')
        self.assert_not_committed()

    def test_untracked_source_is_refused_and_preserved(self):
        for name in ['notes.txt', 'ignored.txt']:
            with self.subTest(name=name):
                note = self.source / name
                note.write_text('untracked editor work\n')
                with self.assertRaises(p.Blocked):
                    self.prepare()
                self.assertEqual(note.read_text(), 'untracked editor work\n')
                note.unlink()
        self.assert_not_committed()

    def test_hidden_source_change_cannot_be_certified_as_a_tested_child(self):
        for flag in ['assume-unchanged', 'skip-worktree']:
            with self.subTest(flag=flag):
                p.git(self.source, 'update-index', '--' + flag, 'packages/demo.nix')
                with self.assertRaises(p.Blocked):
                    self.prepare()
                p.git(self.source, 'update-index', '--no-' + flag, 'packages/demo.nix')
        self.assertEqual(self.file.read_text(), '{ version = "1.0.1"; }\n')
        self.assert_not_committed()

    def test_signing_or_signature_verification_failure_never_produces_tested_receipt(self):
        for operation in ['commit', 'verify-commit']:
            with self.subTest(operation=operation):
                def refuse_signing(arguments, **kwargs):
                    values = list(map(str, arguments))
                    if values[0] == 'git' and operation in values and (operation == 'commit' or values[-1] == 'HEAD'):
                        raise p.Blocked('Fixture signing unavailable')
                    return self.command(arguments, **kwargs)
                with patch.object(p, 'command', side_effect=refuse_signing):
                    with self.assertRaises(p.Blocked):
                        self.prepare()
                record = p.read_json(self.directory / 'candidate.json')
                self.assertEqual(record['phase'], 'failed')
                self.assertNotIn('candidate_commit', record)
                if operation == 'commit':
                    self.assert_not_committed()
                else:
                    child = p.git(self.source, 'rev-parse', 'HEAD').decode().strip()
                    self.assertNotEqual(child, self.baseline_commit)
                    self.assertEqual(p.git(self.source, 'rev-list', '--parents', '-n', '1', child).decode().split(),
                                     [child, self.baseline_commit])

    def test_editor_save_after_checks_prevents_commit_and_retains_bytes(self):
        def save_after_checks(*args):
            self.file.write_text('editor save after all builds\n')
            return {'ok': True}
        with patch.object(self.batch, 'verify_runtime_units', side_effect=save_after_checks):
            with self.assertRaisesRegex(p.Blocked, 'changed while testing'):
                self.prepare()
        self.assertEqual(self.file.read_text(), 'editor save after all builds\n')
        self.assert_not_committed()
        self.assertEqual(p.read_json(self.directory / 'candidate.json')['phase'], 'failed')

    def test_success_checks_signing_exact_child_and_schema2_submission(self):
        expected = self.file.read_bytes()
        record = self.prepare()
        self.assertEqual(record['phase'], 'tested')
        self.assertEqual(record['schema'], 2)
        self.assertEqual(record['mode'], 'batch')
        self.assertEqual(len(self.sign_requests), 1)
        self.assertIn('HEAD', self.verified)
        self.assertEqual(p.git(self.source, 'show', record['candidate_commit'] + ':packages/demo.nix'), expected)
        self.assertEqual(p.git(self.source, 'rev-list', '--parents', '-n', '1', record['candidate_commit']).decode().split(),
                         [record['candidate_commit'], self.baseline_commit])
        self.assertEqual(record['candidate_manifest'], p.source_manifest(self.source))
        self.assertTrue(record['niri']['ok'])
        self.assertTrue(record['published']['ok'])
        self.assertEqual(len(self.profile_checks), 1)
        self.assertTrue(record['app_tests']['codex']['profile_ownership']['ok'])
        self.assertEqual(set(record['host_derivations']), {'nixy-laptop'})
        self.assertEqual({kind for kind, *_ in self.probes}, {'t3', 'codex', 'chatgpt'})
        self.assertTrue(any(command[:3] == ['nix', 'flake', 'check'] for command in self.workloads))
        inbox = self.root / 'inbox' / 'request.json'
        inbox.parent.mkdir(mode=0o700)
        result = p.submit(self.repo, self.state, record['id'], inbox=inbox)
        request = p.read_json(inbox)
        self.assertEqual(result['phase'], 'submitted')
        self.assertEqual(request, {name: record[name] for name in (
            'schema', 'mode', 'id', 'baseline_commit', 'baseline_system', 'candidate_commit', 'candidate_ref')})
        self.assertIn(record['candidate_commit'], self.verified)
        self.assertEqual(inbox.stat().st_nlink, 1)
        self.assertEqual(p.read_json(self.directory / 'candidate.json')['phase'], 'submitted')
        self.assertEqual(p.git(self.repo, 'rev-parse', 'HEAD').decode().strip(), self.baseline_commit)

    def test_large_full_candidate_roundtrips_preparation_submission_and_completion_losslessly(self):
        inventory = [{'path': '/nix/store/' + f'{i:032x}' + '-fixture-package',
                      'deriver': '/nix/store/' + f'{i:032x}' + '-fixture-package.drv',
                      'nar_hash': 'sha256-' + 'a' * 44, 'references': []}
                     for i in range(4000)]
        closure = {'ok': True, 'policy': 'fixture-full-closure', 'inventory': inventory}
        with patch.object(self.batch, 'verify_closure', return_value=closure):
            record = self.prepare()
        self.assertEqual(record['phase'], 'tested')
        self.assertGreater(len(p.encoded(record)), 1024 * 1024)
        candidate = self.directory / 'candidate.json'
        self.assertLessEqual(candidate.stat().st_size, 1024 * 1024)
        self.assertEqual(p.read_json(candidate), record)
        self.assertEqual(stat.S_IMODE(candidate.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)
        inbox = self.root / 'inbox/request.json'
        inbox.parent.mkdir(mode=0o700)
        self.assertEqual(p.submit(self.repo, self.state, record['id'], inbox=inbox)['phase'], 'submitted')
        status = self.root / 'root-status.json'
        status.write_text(json.dumps({'id': record['id'], 'phase': 'complete'}))
        for phase in ('submitted', 'complete'):
            expected = dict(record, phase=phase)
            if phase == 'complete':
                with patch.object(p, 'STATUS', status):
                    p.prune_candidates(self.repo, self.state)
            self.assertEqual(p.read_json(candidate), expected)
            self.assertLessEqual(candidate.stat().st_size, 1024 * 1024)
            self.assertEqual(p.read_json(candidate)['closure']['inventory'], inventory)
        for interrupted in ('preparing', 'building'):
            p.atomic_write(candidate, p.encoded_candidate(dict(record, phase=interrupted)))
            with patch.object(p, 'STATUS', status):
                p.prune_candidates(self.repo, self.state)
            expected = dict(record, phase='failed',
                            error='Previous preparation interrupted before submission')
            self.assertEqual(p.read_json(candidate), expected)
            self.assertLessEqual(candidate.stat().st_size, 1024 * 1024)
            self.assertEqual(p.read_json(candidate)['closure']['inventory'], inventory)

    def test_submitted_record_size_refusal_cannot_publish_a_request(self):
        record = self.prepare()
        candidate = self.directory / 'candidate.json'
        record['size_fixture'] = ''
        remaining = 1024 * 1024 - 2 - len(p.encoded_candidate(record))
        self.assertGreater(remaining, 0)
        record['size_fixture'] = 'x' * remaining
        p.atomic_write(candidate, p.encoded_candidate(record))
        before = candidate.read_bytes()
        self.assertEqual(len(before), 1048574)
        self.assertEqual(p.read_json(candidate), record)
        inbox = self.root / 'inbox/request.json'
        inbox.parent.mkdir(mode=0o700)
        with self.assertRaisesRegex(p.Blocked, 'Candidate document exceeds size bound'):
            p.submit(self.repo, self.state, record['id'], inbox=inbox)
        self.assertFalse(inbox.exists())
        self.assertEqual(list(inbox.parent.iterdir()), [])
        self.assertEqual(candidate.read_bytes(), before)

    def test_private_cli_umask_stages_a_preparable_batch_without_changing_main_modes(self):
        before = p.source_manifest(self.repo)
        previous_mask = os.umask(0o077)
        try:
            with patch.object(p, 'candidate_id', return_value='20261008T140001Z-1234abcd'):
                self.record = p.new_batch(self.repo, self.state, 'batch')
        finally:
            os.umask(previous_mask)
        self.source = Path(self.record['source'])
        self.directory = self.source.parent
        self.file = self.source / 'packages/demo.nix'
        self.file.write_text('{ version = "1.0.1"; }\n')
        result = self.prepare()
        self.assertEqual(result['phase'], 'tested')
        self.assertEqual(p.source_manifest(self.repo), before)
        self.assertEqual(p.git(self.source, 'show', result['candidate_commit'] + ':packages/demo.nix'), self.file.read_bytes())

    def test_acp_change_alone_still_probes_codex(self):
        self.changed_apps = {'acp'}
        record = self.prepare()
        self.assertEqual([kind for kind, *_ in self.probes], ['codex'])
        self.assertTrue(record['app_tests']['codex']['probed'])
        self.assertFalse(record['app_tests']['t3']['probed'])
        self.assertFalse(record['app_tests']['chatgpt']['probed'])

    def test_failed_niri_validation_cannot_sign_or_probe_other_apps(self):
        self.niri_ok = False
        with self.assertRaisesRegex(p.Blocked, 'Niri configuration'):
            self.prepare()
        self.assert_not_committed()
        self.assertEqual(self.probes, [])
        self.assertEqual(p.read_json(self.directory / 'candidate.json')['phase'], 'failed')

    def test_published_source_failure_stops_before_builds_or_signing(self):
        with patch.object(self.batch, 'verify_published', side_effect=self.batch.BatchError('upstream identity failed')):
            with self.assertRaises(self.batch.BatchError):
                self.prepare()
        self.assertEqual(self.workloads, [])
        self.assertEqual(self.probes, [])
        self.assert_not_committed()
        self.assertEqual(p.read_json(self.directory / 'candidate.json')['phase'], 'failed')

    def test_failed_checks_can_retry_in_new_private_probe_directories(self):
        with patch.object(self.batch, 'verify_runtime_units', side_effect=self.batch.BatchError('repair required')):
            with self.assertRaises(self.batch.BatchError):
                self.prepare()
        first_receipts = list(self.directory.glob('**/receipt.json'))
        self.assertEqual(len(first_receipts), 4)
        self.assert_not_committed()
        record = self.prepare()
        self.assertEqual(record['phase'], 'tested')
        self.assertTrue(all(path.exists() for path in first_receipts))
        fresh = [Path(record['app_tests'][kind]['receipt']['receipt_path']) for kind in ['t3', 'codex', 'chatgpt']]
        self.assertTrue(all(path not in first_receipts for path in fresh))


if __name__ == '__main__':
    unittest.main()
