#!/usr/bin/env python3
"""Measure allocation pressure and reclaim a bounded number of data chunks."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import time

GIB = 1024 ** 3
RESERVE_TRIGGER = 40 * GIB
RESERVE_TARGET = 50 * GIB
STATE = Path('/var/lib/weasel-storage-health/last.json')
PUBLIC = Path('/run/weasel-storage-health/status.json')


def run(*args):
    return subprocess.check_output(args, text=True, stderr=subprocess.PIPE,
                                   timeout=120, env={**os.environ, 'LC_ALL': 'C'})


def measure(target):
    fs = json.loads(run('findmnt', '-J', '-T', target, '-o', 'FSTYPE,UUID,TARGET'))['filesystems'][0]
    if fs['fstype'] != 'btrfs' or not fs.get('uuid'):
        raise RuntimeError('Expected a mounted Btrfs filesystem')
    usage = run('btrfs', 'filesystem', 'usage', '-b', target)
    match = re.search(r'^\s*Device unallocated:\s+(\d+)\s*$', usage, re.M)
    if not match:
        raise RuntimeError('Missing device-unallocated measurement')
    base = Path('/sys/fs/btrfs') / fs['uuid'] / 'allocation/metadata'
    counters = {key: int((base / key).read_text()) for key in
                ('total_bytes', 'bytes_used', 'bytes_reserved', 'bytes_pinned',
                 'bytes_may_use', 'bytes_readonly')}
    if counters['total_bytes'] <= 0 or any(n < 0 for n in counters.values()):
        raise RuntimeError('Invalid metadata accounting')
    errors = run('btrfs', 'device', 'stats', target)
    values = re.findall(r'^\S+\.(?:write_io_errs|read_io_errs|flush_io_errs|corruption_errs|generation_errs)\s+(\d+)\s*$', errors, re.M)
    if not values:
        raise RuntimeError('Missing device-error counters')
    stat = os.statvfs(target)
    return {'target': target, 'uuid': fs['uuid'], 'time': time.time(),
            'free_bytes': stat.f_bavail * stat.f_frsize,
            'filesystem_bytes': stat.f_blocks * stat.f_frsize,
            'unallocated_bytes': int(match[1]), 'metadata': counters,
            'device_errors': sum(map(int, values))}


def pressure(row):
    m = row['metadata']
    reservations = sum(m[key] for key in ('bytes_reserved', 'bytes_pinned', 'bytes_may_use'))
    # Reservations are commitments, not additional persisted metadata. Retain
    # their raw values separately; do not present their sum as a used percent.
    physical_fraction = m['bytes_used'] / m['total_bytes']
    commitment_headroom = max(0, m['total_bytes'] - m['bytes_used'] - reservations - m['bytes_readonly'])
    tight = physical_fraction >= .90 or commitment_headroom < GIB
    critical = row['device_errors'] > 0 or row['free_bytes'] < 30 * GIB or (
        row['unallocated_bytes'] < RESERVE_TRIGGER and tight)
    return {'level': 'critical' if critical else ('watch' if row['unallocated_bytes'] < RESERVE_TRIGGER else 'healthy'),
            'metadata_used_fraction': physical_fraction,
            'metadata_reservations_bytes': reservations,
            'metadata_readonly_bytes': m['bytes_readonly'],
            'metadata_commitment_headroom_bytes': commitment_headroom}


def write_json(path, value, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, mode)
    os.fchmod(fd, mode)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def report(row, maintenance=None):
    previous = json.loads(STATE.read_text()) if STATE.exists() else {}
    condition = pressure(row)
    consecutive = (previous.get('consecutive_critical', 0) + 1
                   if condition['level'] == 'critical' else 0)
    result = {'schema': 1, 'measurement': row, **condition,
              'consecutive_critical': consecutive,
              'maintenance': maintenance or previous.get('maintenance')}
    write_json(STATE, result, 0o600)
    write_json(PUBLIC, result, 0o644)
    return result


def transaction_reason(root=Path('/var/lib/weasel-updates'), status=None):
    status = status or Path('/var/lib/weasel-updates-status/status.json')
    terminal = {'complete', 'verified-only', 'blocked', 'failed-before-integration'}
    if status.exists() and json.loads(status.read_text()).get('phase') not in terminal:
        return 'unresolved daily update'
    # A terminal latest status cannot hide an older unresolved transaction.
    # Match the installed updater's run/bootstrap terminal phases. Manual
    # activation remains conservative and requires an explicit completion.
    for namespace in ('runs', 'bootstrap', 'manual-computer-use'):
        directory = root / namespace
        if not directory.exists():
            continue
        if directory.is_symlink() or not directory.is_dir():
            return 'unsafe transaction namespace'
        for entry in directory.iterdir():
            if entry.is_symlink() or not entry.is_dir():
                return 'unsafe transaction entry'
            journal = entry / 'journal.json'
            if journal.is_symlink() or not journal.is_file():
                return 'missing or unsafe transaction journal'
            phases = {'complete'} if namespace == 'manual-computer-use' else (
                {'complete', 'failed-before-integration'} if namespace == 'bootstrap' else terminal)
            if json.loads(journal.read_text()).get('phase') not in phases:
                return 'unresolved ' + namespace + ' transaction'
    return None


def active_build(proc=Path('/proc')):
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            args = [x.decode(errors='replace') for x in (entry / 'cmdline').read_bytes().split(b'\0') if x]
        except (FileNotFoundError, ProcessLookupError):
            continue
        except PermissionError:
            return 'process inventory unavailable'
        if not args:
            continue
        exe = Path(args[0]).name
        if exe in ('nix-build', 'nixos-rebuild', 'nixos-rebuild-ng') or (
                exe == 'nix' and 'build' in args[1:]) or (
                exe == 'nh' and any(x in args for x in ('switch', 'build', 'boot'))):
            return 'active system/build request'
    return None


def maintain(target, measure_fn=measure, run_fn=run, deadline_seconds=300):
    before = measure_fn(target)
    steps = []
    result = {'time': time.time(), 'before_unallocated_bytes': before['unallocated_bytes'], 'steps': steps}
    if before['unallocated_bytes'] >= RESERVE_TRIGGER:
        return {**result, 'outcome': 'not needed'}
    if before['device_errors'] or before['free_bytes'] < 30 * GIB:
        return {**result, 'outcome': 'deferred', 'reason': 'device errors or insufficient free space'}
    reason = transaction_reason() or active_build()
    if reason:
        return {**result, 'outcome': 'deferred', 'reason': reason}
    if 'No balance found' not in run_fn('btrfs', 'balance', 'status', target):
        return {**result, 'outcome': 'deferred', 'reason': 'existing balance'}
    started = time.monotonic()
    # At most four passes and 24 nonempty data chunks. Escalate the usage
    # filter if sparse chunks cannot restore the reserve. Never rebalance
    # metadata or change its DUP profile. Stop at the target or time budget.
    after = before
    budget_reason = 'pass budget exhausted before reserve target'
    for filters in ('usage=0', 'usage=5,limit=8', 'usage=30,limit=8', 'usage=50,limit=8'):
        if time.monotonic() - started >= deadline_seconds:
            budget_reason = 'time budget exhausted'
            break
        reason = transaction_reason() or active_build()
        if reason:
            return {**result, 'outcome': 'deferred', 'reason': reason}
        try:
            run_fn('btrfs', 'balance', 'start', '-d' + filters, target)
        except subprocess.TimeoutExpired:
            # Killing the userspace command alone need not stop the kernel
            # balance. Cancel only after our own start timed out under the lock.
            run_fn('btrfs', 'balance', 'cancel', target)
            if 'No balance found' not in run_fn('btrfs', 'balance', 'status', target):
                raise RuntimeError('Timed-out balance did not release')
            return {**result, 'outcome': 'cancelled', 'reason': 'pass time budget exceeded'}
        except subprocess.CalledProcessError as error:
            after = measure_fn(target)
            steps.append({'filter': filters, 'unallocated_bytes': after['unallocated_bytes'],
                          'returncode': error.returncode})
            return {**result, 'outcome': 'failed',
                    'reason': 'balance failed: ' + (error.stderr or str(error)).strip()[:512],
                    'after_unallocated_bytes': after['unallocated_bytes']}
        after = measure_fn(target)
        steps.append({'filter': filters, 'unallocated_bytes': after['unallocated_bytes']})
        if after['device_errors']:
            raise RuntimeError('Device errors observed after balance')
        if after['unallocated_bytes'] >= RESERVE_TARGET:
            break
    if after['unallocated_bytes'] >= RESERVE_TARGET:
        return {**result, 'outcome': 'completed',
                'after_unallocated_bytes': after['unallocated_bytes']}
    return {**result, 'outcome': 'target-not-reached', 'reason': budget_reason,
            'after_unallocated_bytes': after['unallocated_bytes']}


def notify():
    if not PUBLIC.exists():
        return
    state = json.loads(PUBLIC.read_text())
    if time.time() - state['measurement']['time'] > 3600:
        return
    if state['consecutive_critical'] < 1:
        return
    stamp = Path.home() / '.local/state/weasel-storage-health/last-notice'
    if stamp.exists() and time.time() - stamp.stat().st_mtime < 1800:
        return
    row = state['measurement']
    text = (f"{row['target']}: {row['free_bytes']/GIB:.1f} GiB frei, "
            f"{row['unallocated_bytes']/GIB:.1f} GiB Btrfs-Reserve; "
            f"Metadaten {state['metadata_used_fraction']:.1%}, "
            f"Reservierungen {state['metadata_reservations_bytes']/GIB:.2f} GiB.")
    if row['device_errors']:
        text += f" Gerätefehler: {row['device_errors']}."
    maintenance = state.get('maintenance') or {}
    if maintenance.get('outcome') in ('deferred', 'cancelled', 'failed', 'target-not-reached'):
        text += f" Wartung: {maintenance['outcome']} ({maintenance.get('reason', 'unbekannt')})."
    subprocess.run(['notify-send', '--urgency=critical', 'Development resource guard', text], check=True)
    stamp.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp.touch(mode=0o600)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('check', 'record', 'maintain', 'notify'))
    parser.add_argument('--target', default='/home')
    args = parser.parse_args()
    if args.mode == 'notify':
        notify()
        return
    if args.mode == 'check':
        row = measure(args.target)
        print(json.dumps({'measurement': row, **pressure(row)}, indent=2))
        return
    if os.geteuid() != 0:
        parser.error('record and maintain require root')
    STATE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(STATE.parent / 'operation.lock', 'a') as own_lock:
        try:
            fcntl.flock(own_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({'schema': 1, 'outcome': 'deferred', 'reason': 'storage operation lock held'}))
            return
        maintenance = None
        if args.mode == 'maintain':
            with open('/var/lib/weasel-updates/activation.lock', 'a') as update_lock:
                try:
                    fcntl.flock(update_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    maintenance = {'time': time.time(), 'outcome': 'deferred', 'reason': 'update lock held'}
                else:
                    maintenance = maintain(args.target)
        print(json.dumps(report(measure(args.target), maintenance), indent=2))


if __name__ == '__main__':
    main()
