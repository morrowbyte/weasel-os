#!/usr/bin/env python3
"""Safety boundaries for allocation maintenance and snapshot exclusions."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


health = load('weasel-btrfs-health')
excludes = load('weasel-snapshot-excludes')


def row(unallocated=7, errors=0, free=150):
    return {'unallocated_bytes': unallocated * health.GIB, 'free_bytes': free * health.GIB,
            'device_errors': errors, 'metadata': {
                'total_bytes': 12 * health.GIB, 'bytes_used': 11 * health.GIB,
                'bytes_reserved': 2 * health.GIB, 'bytes_pinned': 0,
                'bytes_may_use': 0, 'bytes_readonly': 0}}


class SafetyTests(unittest.TestCase):
    def test_busy_storage_operation_lock_is_a_clean_skip(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(health, 'STATE', Path(tmp) / 'last.json'), \
                 patch.object(health.os, 'geteuid', return_value=0), \
                 patch('sys.argv', ['health', 'record']), \
                 patch.object(health.fcntl, 'flock', side_effect=BlockingIOError), \
                 patch.object(health, 'measure') as measured, \
                 patch('builtins.print') as output:
                health.main()
                measured.assert_not_called()
                self.assertIn('storage operation lock held', output.call_args.args[0])

    def test_latest_complete_status_cannot_hide_unresolved_journal(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            status = root / 'status.json'
            status.write_text(json.dumps({'phase': 'complete'}))
            for namespace in ('runs', 'bootstrap', 'manual-computer-use'):
                entry = root / namespace / 'transaction'
                entry.mkdir(parents=True)
                journal = entry / 'journal.json'
                self.assertIsNotNone(health.transaction_reason(root, status))
                journal.write_text(json.dumps({'phase': 'recovery-required'}))
                self.assertIsNotNone(health.transaction_reason(root, status))
                journal.write_text(json.dumps({'phase': 'complete'}))
                self.assertIsNone(health.transaction_reason(root, status))
            (root / 'runs/transaction/journal.json').write_text(json.dumps({'phase': 'failed-before-integration'}))
            self.assertIsNone(health.transaction_reason(root, status))

    def test_generous_reserve_thresholds_and_bounded_target(self):
        calls = []
        measured = iter([row(39), row(40), row(50)])
        with patch.object(health, 'transaction_reason', return_value=None), \
             patch.object(health, 'active_build', return_value=None):
            result = health.maintain('/home', lambda p: next(measured),
                                    lambda *args: calls.append(args) or 'No balance found')
        self.assertEqual(result['after_unallocated_bytes'], 50 * health.GIB)
        self.assertEqual(len([args for args in calls if 'start' in args]), 2)
        self.assertEqual(health.pressure(row(39))['level'], 'critical')
        self.assertEqual(health.pressure(row(50))['level'], 'healthy')

    def test_stalled_reserve_escalates_without_expanding_pass_or_chunk_budget(self):
        calls = []
        with patch.object(health, 'transaction_reason', return_value=None), \
             patch.object(health, 'active_build', return_value=None):
            result = health.maintain('/home', lambda p: row(unallocated=1),
                                    lambda *args: calls.append(args) or 'No balance found')
        starts = [args for args in calls if 'start' in args]
        self.assertEqual([args[3] for args in starts], [
            '-dusage=0', '-dusage=5,limit=8', '-dusage=30,limit=8',
            '-dusage=50,limit=8'])
        self.assertEqual(len(starts), 4)
        self.assertEqual(sum(int(args[3].split('limit=')[1])
                             for args in starts if 'limit=' in args[3]), 24)
        self.assertEqual(result['outcome'], 'target-not-reached')
        self.assertEqual(result['after_unallocated_bytes'], health.GIB)
        self.assertIn('budget', result['reason'])

    def test_deadline_prevents_additional_balance_passes(self):
        calls = []
        with patch.object(health, 'transaction_reason', return_value=None), \
             patch.object(health, 'active_build', return_value=None), \
             patch.object(health.time, 'monotonic', side_effect=[0, 0, 301]):
            result = health.maintain('/home', lambda p: row(unallocated=1),
                                    lambda *args: calls.append(args) or 'No balance found')
        self.assertEqual(len([args for args in calls if 'start' in args]), 1)
        self.assertEqual(result['outcome'], 'target-not-reached')
        self.assertEqual(result['reason'], 'time budget exhausted')

    def test_enospc_is_reported_with_partial_reclamation_and_no_more_passes(self):
        calls = []
        samples = iter([row(unallocated=1), row(unallocated=2)])
        def run(*args):
            calls.append(args)
            if 'start' in args:
                raise subprocess.CalledProcessError(1, args, stderr='ERROR: No space left on device')
            return 'No balance found'
        with patch.object(health, 'transaction_reason', return_value=None), \
             patch.object(health, 'active_build', return_value=None):
            result = health.maintain('/home', lambda p: next(samples), run)
        self.assertEqual(result['outcome'], 'failed')
        self.assertIn('No space left on device', result['reason'])
        self.assertEqual(result['after_unallocated_bytes'], 2 * health.GIB)
        self.assertEqual(result['steps'][0]['returncode'], 1)
        self.assertEqual(len([args for args in calls if 'start' in args]), 1)

    def test_tight_metadata_is_critical_before_reserve_reaches_eight_gib(self):
        self.assertEqual(health.pressure(row(unallocated=39))['level'], 'critical')
        sample = row(unallocated=39)
        sample['metadata'].update(bytes_used=6 * health.GIB, bytes_reserved=0)
        self.assertEqual(health.pressure(sample)['level'], 'watch')

    def test_first_critical_measurement_notifies_with_failed_maintenance(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            public = root / 'status.json'
            sample = row(unallocated=1)
            sample.update(target='/home', time=health.time.time())
            public.write_text(json.dumps({
                'measurement': sample, **health.pressure(sample),
                'consecutive_critical': 1,
                'maintenance': {'outcome': 'target-not-reached', 'reason': 'pass budget exhausted'}}))
            with patch.object(health, 'PUBLIC', public), \
                 patch.object(health.Path, 'home', return_value=root), \
                 patch.object(health.subprocess, 'run') as command:
                health.notify()
                command.assert_called_once()
                self.assertIn('target-not-reached', command.call_args.args[0][-1])
                self.assertTrue((root / '.local/state/weasel-storage-health/last-notice').exists())

    def test_migration_refuses_low_reserve_before_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            target = root / 'target'
            target.mkdir()
            with patch.object(excludes.os, 'geteuid', return_value=0), \
                 patch.object(excludes, 'recognized', return_value=True), \
                 patch.object(excludes, 'tracked', return_value=False), \
                 patch.object(excludes.HEALTH, 'measure', return_value=row(unallocated=1)), \
                 patch.object(excludes.HEALTH, 'write_json') as write, \
                 patch.object(excludes.subprocess, 'run') as command:
                with self.assertRaisesRegex(RuntimeError, 'reserve'):
                    excludes.migrate(target, root)
                write.assert_not_called()
                command.assert_not_called()
            self.assertEqual(list(root.iterdir()), [target])

    def test_reservations_are_not_a_used_percentage(self):
        result = health.pressure(row())
        self.assertAlmostEqual(result['metadata_used_fraction'], 11 / 12)
        self.assertEqual(result['metadata_reservations_bytes'], 2 * health.GIB)
        self.assertEqual(result['metadata_commitment_headroom_bytes'], 0)
        self.assertEqual(result['level'], 'critical')

    def test_build_transaction_and_existing_balance_do_not_mutate(self):
        for transaction, build, status in [('unresolved', None, ''), (None, 'build', ''),
                                            (None, None, 'Balance on /home is running')]:
            calls = []
            with patch.object(health, 'transaction_reason', return_value=transaction), \
                 patch.object(health, 'active_build', return_value=build):
                result = health.maintain('/home', lambda p: row(),
                                        lambda *args: calls.append(args) or status)
            self.assertEqual(result['outcome'], 'deferred')
            self.assertFalse(any('start' in args for args in calls))

    def test_data_only_passes_stop_at_target_and_have_a_chunk_limit(self):
        calls = []
        measured = iter([row(), row(9), row(50)])
        with patch.object(health, 'transaction_reason', return_value=None), \
             patch.object(health, 'active_build', return_value=None):
            result = health.maintain('/home', lambda p: next(measured),
                                    lambda *args: calls.append(args) or 'No balance found')
        starts = [args for args in calls if 'start' in args]
        self.assertEqual([args[3] for args in starts], ['-dusage=0', '-dusage=5,limit=8'])
        self.assertEqual(result['after_unallocated_bytes'], 50 * health.GIB)

    def test_timeout_cancels_owned_kernel_balance(self):
        calls = []
        def run(*args):
            calls.append(args)
            if 'start' in args:
                raise subprocess.TimeoutExpired(args, 120)
            return 'No balance found'
        with patch.object(health, 'transaction_reason', return_value=None), \
             patch.object(health, 'active_build', return_value=None):
            result = health.maintain('/home', lambda p: row(), run)
        self.assertIn(('btrfs', 'balance', 'cancel', '/home'), calls)
        self.assertEqual(result['outcome'], 'cancelled')

    def test_errors_and_low_free_space_prevent_balance(self):
        for sample in (row(errors=1), row(free=20), row(unallocated=51)):
            calls = []
            health.maintain('/home', lambda p: sample, lambda *args: calls.append(args))
            self.assertEqual(calls, [])

    def test_unrecognized_and_tracked_directories_remain_in_snapshots(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('build', 'dist', 'bin', 'obj', 'target', 'node_modules'):
                path = root / name
                path.mkdir()
                self.assertFalse(excludes.recognized(path))
            (root / 'Cargo.toml').touch()
            (root / 'target/.rustc_info.json').touch()
            self.assertTrue(excludes.recognized(root / 'target'))
            (root / 'package.json').touch()
            self.assertTrue(excludes.recognized(root / 'node_modules'))
            (root / 'linked').symlink_to(root / 'target')
            self.assertFalse(excludes.recognized(root / 'linked'))
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            subprocess.run(['git', '-C', str(root), 'add', 'target/.rustc_info.json'], check=True)
            self.assertTrue(excludes.tracked(root / 'target'))


if __name__ == '__main__':
    unittest.main()
