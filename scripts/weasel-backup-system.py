#!/usr/bin/env python3
"""Root snapshot and streaming restic backup of mutable system state only."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import tempfile
import secrets
import stat

STATE = Path('/var/lib/weasel-system-state')
SNAPSHOT = STATE / 'snapshot'


def remove_snapshot():
    if SNAPSHOT.is_symlink():
        raise RuntimeError('Unsafe snapshot path')
    if SNAPSHOT.exists():
        subprocess.run(['btrfs', 'subvolume', 'delete', str(SNAPSHOT)], check=True)
    (STATE / 'ready.json').unlink(missing_ok=True)


def create_snapshot():
    with open('/var/lib/weasel-updates/activation.lock', 'a') as activation:
        fcntl.flock(activation, fcntl.LOCK_EX | fcntl.LOCK_NB)
        remove_snapshot()
        subprocess.run(['btrfs', 'subvolume', 'snapshot', '-r', '/', str(SNAPSHOT)], check=True)
        (STATE / 'ready.json').write_text(json.dumps({'time': time.time()}))


def backup_snapshot(restic, env, cycle='daily'):
    # A whole-root snapshot pins Nix garbage and obsolete VM/coredump blocks.
    # Keep it only for the actual upload, including cleanup after upload errors.
    try:
        create_snapshot()
        source = str(SNAPSHOT)
        command = [restic, '-o', 'rest.connections=1', 'backup', '--json', '--host', 'nixy-laptop',
                   '--tag', 'system-state', '--tag', cycle, '--group-by', 'host,paths', '--pack-size', '32']
        for item in ['var/lib/nix', 'var/lib/flatpak', 'var/lib/systemd/coredump',
                     'var/lib/weasel-system-state', 'var/lib/docker', 'var/lib/containers',
                     'root/.cache', 'root/.npm', 'root/.local/share/pnpm/store', 'root/.ollama/models']:
            command += ['--exclude', source + '/' + item]
        command += [source + '/' + item for item in ['etc', 'root', 'var/lib', 'var/spool']]
        subprocess.run(command, env=env, check=True)
    finally:
        remove_snapshot()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode', choices=['snapshot', 'backup', 'probe', 'cleanup'])
    p.add_argument('--restic', required=True)
    args = p.parse_args()
    if os.geteuid() != 0:
        p.error('root required')
    os.umask(0o077)
    if STATE.is_symlink():
        raise RuntimeError('Unsafe state path')
    STATE.mkdir(mode=0o700, exist_ok=True)
    with (STATE / 'snapshot.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.mode == 'cleanup':
            with open('/var/lib/weasel-updates/activation.lock', 'a') as activation:
                fcntl.flock(activation, fcntl.LOCK_EX | fcntl.LOCK_NB)
                remove_snapshot()
            print('Temporary system-state snapshot removed; live system unchanged')
            return
        if args.mode == 'snapshot':
            create_snapshot()
            print('Readonly system-state snapshot ready; no local data archive created')
            return
        request = json.load(sys.stdin)
        repo = request.get('repository', '')
        if not re.fullmatch(r'rest:http://weasel:[A-Za-z0-9_-]{40,64}@127\.0\.0\.1:[0-9]{1,5}/', repo):
            raise RuntimeError('Only an authenticated loopback restic transport is accepted')
        password = Path('/home/evilweasel/.local/share/weasel-backup/nixy-laptop-restic-password.txt')
        if password.is_symlink() or password.stat().st_uid != 1000 or password.stat().st_mode & 0o077:
            raise RuntimeError('Unsafe private password file')
        env = dict(os.environ, RESTIC_REPOSITORY=repo, RESTIC_PASSWORD_FILE=str(password),
                   RESTIC_CACHE_DIR='/var/cache/weasel-backup-root')
        if args.mode == 'probe':
            with tempfile.TemporaryDirectory(prefix='root-proof-', dir=STATE) as tmp:
                original = Path(tmp) / 'root-private.bin'
                payload = secrets.token_bytes(65536)
                original.write_bytes(payload)
                original.chmod(0o600)
                subprocess.run([args.restic, '-o', 'rest.connections=1', 'backup', '--tag', 'system-proof',
                                '--host', 'nixy-laptop', str(original)], env=env, check=True)
                target = Path(tmp) / 'restored'
                subprocess.run([args.restic, '-o', 'rest.connections=1', 'restore', 'latest', '--tag',
                                'system-proof', '--target', str(target)], env=env, check=True)
                restored = target / str(original).lstrip('/')
                if restored.read_bytes() != payload or restored.stat().st_uid != 0 or stat.S_IMODE(restored.stat().st_mode) != 0o600:
                    raise RuntimeError('Private root-state restore proof failed')
            print('Private root-state cloud restore proof passed, including ownership and permissions')
            return
        cycle = request.get('cycle', 'daily')
        if cycle not in ('daily', 'weekly'):
            raise RuntimeError('Invalid backup cycle')
        backup_snapshot(args.restic, env, cycle)
        print('Mutable system-state backup completed; temporary root snapshot removed')


if __name__ == '__main__':
    main()
