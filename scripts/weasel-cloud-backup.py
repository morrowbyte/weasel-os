#!/usr/bin/env python3
"""Versioned cloud backup and recovery, using restic and official Proton Drive."""
import argparse
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time

spec = importlib.util.spec_from_file_location('transport', Path(__file__).with_name('proton-restic-server.py'))
transport = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transport)


def backup_excludes(source, patterns):
    """Anchor source-relative rules for Restic's absolute path matching."""
    root = str(source.expanduser().resolve(strict=True))
    return '\n'.join(root + line if line.startswith('/') else line
                     for line in patterns.splitlines()) + '\n'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode', choices=['probe', 'backup', 'snapshots', 'check', 'restore', 'maintain'])
    p.add_argument('--cli', default='proton-drive')
    p.add_argument('--restic', default='restic')
    p.add_argument('--excludes', type=Path, required=True)
    p.add_argument('--root', default='/my-files/Weasel-Recovery/nixy-laptop-restic-v1')
    p.add_argument('--source', type=Path, default=Path.home())
    p.add_argument('--target', type=Path)
    p.add_argument('--snapshot', default='latest')
    p.add_argument('--tag', choices=['personal-state', 'system-state'], default='personal-state')
    p.add_argument('--probe-system', action='store_true')
    args = p.parse_args()
    args.source = args.source.expanduser().resolve(strict=True)
    os.umask(0o077)
    state = Path.home() / '.local/state/weasel-backup'
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (state / 'job.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with tempfile.TemporaryDirectory(prefix='spool-', dir=state) as spool:
            drive = transport.Drive(args.cli, args.root, spool)
            drive.folders()
            parent = args.root.rsplit('/', 1)[0]
            key_name = 'nixy-laptop-restic-password.txt'
            password = Path.home() / '.local/share/weasel-backup' / key_name
            password.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            remote_keys = drive.rows(drive.command('list', parent))
            if not password.exists():
                if key_name in remote_keys:
                    drive.command('download', '-f', 'skip', parent + '/' + key_name, str(password.parent))
                    password.chmod(0o600)
                elif 'config' in drive.listing(''):
                    raise RuntimeError('Repository exists but recovery password is missing; initialization refused')
                else:
                    with password.open('x') as stream:
                        stream.write(secrets.token_urlsafe(48) + '\n')
            if password.is_symlink() or password.stat().st_uid != os.getuid() or password.stat().st_mode & 0o077:
                raise RuntimeError('Unsafe backup password file permissions')
            if key_name not in remote_keys:
                drive.command('upload', '-f', 'skip', '-t', str(password), parent)
            # Keep recovery possible after total laptop loss. Verify the private
            # recovery material without exposing it in output or process args.
            with tempfile.TemporaryDirectory(dir=spool) as tmp:
                drive.command('download', '-f', 'skip', parent + '/' + key_name, tmp)
                if (Path(tmp) / key_name).read_bytes() != password.read_bytes():
                    raise RuntimeError('Cloud recovery password does not match local private store')
            token = secrets.token_urlsafe(32)
            server = transport.serve(drive, token, allow_delete=args.mode == 'maintain')
            env = dict(os.environ, RESTIC_REPOSITORY=f'rest:http://weasel:{token}@127.0.0.1:{server.server_port}/',
                       RESTIC_PASSWORD_FILE=str(password), RESTIC_CACHE_DIR=str(state / 'restic-cache'))

            def restic(*command):
                result = subprocess.run([args.restic, '-o', 'rest.connections=1', *command],
                                        env=env, capture_output=True, text=True)
                # Logs are private; command line/environment never logged here.
                (state / 'last-restic.log').write_text(result.stdout + result.stderr)
                if result.returncode:
                    raise RuntimeError('restic failed (exit ' + str(result.returncode) + '); see private last-restic.log')
                return result.stdout

            # Restic matches absolute source paths. Expand the declared
            # home-relative rules against the actual backup source.
            excludes = Path(spool) / 'excludes.txt'
            excludes.write_text(backup_excludes(args.source, args.excludes.read_text()))

            try:
                if 'config' not in drive.listing(''):
                    restic('init', '--repository-version', '2')
                if args.mode == 'probe':
                    with tempfile.TemporaryDirectory(prefix='restore-proof-', dir=state) as tmp:
                        source = Path(tmp) / 'source'
                        source.mkdir()
                        payload = secrets.token_bytes(1024 * 1024)
                        (source / 'proof.bin').write_bytes(payload)
                        (source / 'link').symlink_to('proof.bin')
                        restic('backup', '--tag', 'restore-proof', '--host', 'nixy-laptop', str(source))
                        restic('check', '--read-data')
                        destination = Path(tmp) / 'restored'
                        restic('restore', 'latest', '--tag', 'restore-proof', '--target', str(destination))
                        restored = destination / str(source).lstrip('/')
                        if (restored / 'proof.bin').read_bytes() != payload or os.readlink(restored / 'link') != 'proof.bin':
                            raise RuntimeError('Cloud restore proof failed')
                    (state / 'restore-proof.json').write_text(json.dumps({'time': time.time(), 'ok': True}))
                    print('Cloud backup restore proof passed: full remote read, byte equality and symlink preservation')
                    if args.probe_system:
                        result = subprocess.run(['sudo', '-n', '/run/current-system/sw/bin/weasel-system-state-export', 'probe'],
                                                input=json.dumps({'repository': env['RESTIC_REPOSITORY']}), capture_output=True, text=True)
                        (state / 'last-system-restic.log').write_text(result.stdout + result.stderr)
                        if result.returncode:
                            raise RuntimeError('Private root-state restore proof failed; see private last-system-restic.log')
                        print('Private root-state restore proof passed, including owner root and mode 0600')
                elif args.mode == 'backup':
                    restic('backup', '--host', 'nixy-laptop', '--tag', 'personal-state',
                           '--exclude-file', str(excludes), '--pack-size', '32', str(args.source))
                    if args.source == Path.home():
                        # The token travels through stdin, not process arguments,
                        # a public environment file or the root journal.
                        result = subprocess.run(['sudo', '-n', '/run/current-system/sw/bin/weasel-system-state-export', 'backup'],
                                                input=json.dumps({'repository': env['RESTIC_REPOSITORY']}),
                                                capture_output=True, text=True)
                        (state / 'last-system-restic.log').write_text(result.stdout + result.stderr)
                        if result.returncode:
                            raise RuntimeError('Mutable system-state backup failed; see private last-system-restic.log')
                    restic('check')
                    (state / 'last-success.json').write_text(json.dumps({'time': time.time(), 'ok': True,
                                                                       'source': str(args.source)}))
                    print('Cloud backup completed; repository structural check passed')
                elif args.mode == 'check':
                    restic('check', '--read-data-subset', '5%')
                    print('Repository check with remote data sample passed')
                elif args.mode == 'maintain':
                    if not (state / 'last-success.json').exists() or not (state / 'restore-proof.json').exists():
                        raise RuntimeError('Retention needs a successful backup and restore proof first')
                    restic('check', '--read-data-subset', '5%')
                    restic('forget', '--tag', 'personal-state', '--tag', 'system-state', '--group-by', 'host,paths',
                           '--keep-daily', '7', '--keep-weekly', '4', '--keep-monthly', '3', '--prune')
                    print('Cloud retention completed')
                elif args.mode == 'snapshots':
                    print(restic('snapshots', '--tag', args.tag))
                elif args.mode == 'restore':
                    if not args.target or args.target.exists():
                        raise RuntimeError('Restore requires a new, absent --target directory')
                    print(restic('restore', args.snapshot, '--tag', args.tag, '--target', str(args.target)))
            finally:
                server.shutdown()
                server.server_close()


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # Do not print arbitrary subprocess exceptions containing auth URLs.
        print('Cloud backup stopped: ' + (str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__))
        raise SystemExit(1)
