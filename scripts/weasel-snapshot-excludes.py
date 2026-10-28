#!/usr/bin/env python3
"""Inventory reproducible build directories; migrate one idle directory on demand.

No automatic home reorganization. Original paths, sources, Git and application
state stay in place. A nested subvolume excludes its contents from future Home
snapshots, independently of the separate Restic exclusion policy.
"""
import argparse
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import time
import uuid

SPEC = importlib.util.spec_from_file_location('health', Path(__file__).absolute().with_name('weasel-btrfs-health.py'))
HEALTH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HEALTH)


def recognized(path):
    if path.is_symlink() or not path.is_dir() or path.is_mount() or path.stat().st_ino == 256:
        return False
    parent = path.parent
    if path.name == 'node_modules':
        return (parent / 'package.json').is_file()
    if path.name == 'target':
        return (parent / 'Cargo.toml').is_file() and (path / '.rustc_info.json').is_file()
    if path.name == '.venv':
        return (path / 'pyvenv.cfg').is_file()
    if path.name == '__pycache__':
        return True
    return False


def git_args(directory):
    args = ['git', '-c', 'core.fsmonitor=false', '-C', str(directory)]
    # Root must not misclassify Git's dubious-ownership refusal as "untracked".
    # Run the read-only Git query as the directory owner, with no fsmonitor hook.
    if os.geteuid() == 0 and directory.stat().st_uid != 0:
        user = pwd.getpwuid(directory.stat().st_uid).pw_name
        args = ['runuser', '--user', user, '--'] + args
    return args


def git_root(directory):
    result = subprocess.run(git_args(directory) + ['rev-parse', '--show-toplevel'],
                            capture_output=True, text=True, timeout=10)
    if result.returncode:
        if 'not a git repository' in result.stderr:
            return None
        raise RuntimeError('Cannot verify Git ownership/tracked files; migration deferred')
    return Path(result.stdout.strip())


def tracked(path):
    root = git_root(path.parent)
    if root is None:
        return False
    result = subprocess.run(git_args(root) + ['ls-files', '--', str(path.relative_to(root))],
                            capture_output=True, text=True, check=True, timeout=30)
    return bool(result.stdout)


def allocated(path):
    result = subprocess.run(['du', '-sx', '--block-size=1', str(path)],
                            capture_output=True, text=True, check=True, timeout=120)
    return int(result.stdout.split()[0])


def inventory(home):
    roots = [home / p for p in ('source', 'Documents/Codex', '.t3/worktrees',
                               '.t3/scratch', '.t3-morrowbyte/worktrees',
                               '.t3-morrowbyte/scratch', 'weasel-os', 'factorio-character-mod',
                               'factorio-space-age-modpack')]
    candidates = []
    visited = 0
    def walk_error(error):
        raise error
    for root in roots:
        if not root.is_dir() or root.is_symlink():
            continue
        for current, dirs, files in os.walk(root, followlinks=False, onerror=walk_error):
            visited += 1
            if visited > 150000:
                raise RuntimeError('Inventory exceeded directory budget; no incomplete plan emitted')
            for name in list(dirs):
                path = Path(current) / name
                if name in ('.git', '.snapshots') or path.is_symlink() or path.stat().st_ino == 256:
                    dirs.remove(name)
                elif recognized(path):
                    dirs.remove(name)
                    if not tracked(path):
                        size = allocated(path)
                        if size >= 64 * 1024 ** 2:
                            candidates.append({'path': str(path), 'allocated_bytes': size,
                                               'kind': name, 'disposition': 'deferred until writers and update release'})
                elif name in ('node_modules', 'target', 'venv', '.venv'):
                    # Unknown artifact-shaped trees may contain user data.
                    dirs.remove(name)
    return {'schema': 1, 'time': time.time(), 'home': str(home),
            'scope': 'build outputs only; source and Git stay in Home snapshots',
            'roots': [str(p) for p in roots],
            'candidates': sorted(candidates, key=lambda r: r['allocated_bytes'], reverse=True)}


def open_users(path):
    prefix = str(path) + '/'
    # An installer or watcher can be poised to write node_modules without an
    # open cache FD. Treat the containing project as busy when it is a cwd.
    project = git_root(path.parent) or path.parent
    project_prefix = str(project) + '/'
    matches = set()
    def within(value):
        return value == str(path) or value.startswith(prefix)
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        try:
            cwd = os.readlink(proc / 'cwd')
            if cwd == str(project) or cwd.startswith(project_prefix):
                matches.add(int(proc.name))
            links = [proc / 'cwd', proc / 'root'] + list((proc / 'fd').iterdir())
            for link in links:
                try:
                    if within(os.readlink(link).removesuffix(' (deleted)')):
                        matches.add(int(proc.name))
                except (FileNotFoundError, ProcessLookupError):
                    pass
            for line in (proc / 'maps').read_text().splitlines():
                parts = line.split(maxsplit=5)
                if len(parts) == 6 and within(parts[5]):
                    matches.add(int(proc.name))
        except (FileNotFoundError, ProcessLookupError):
            continue
        except PermissionError:
            raise RuntimeError('Cannot prove directory idle: process inventory incomplete')
    return sorted(matches)


def compare(source, destination):
    result = subprocess.run(['rsync', '-aHAXnc', '--delete', '--itemize-changes',
                             str(source) + '/', str(destination) + '/'],
                            capture_output=True, text=True, check=True)
    if result.stdout:
        raise RuntimeError('Copy verification differs; original retained')


def migrate(path, home):
    if os.geteuid() != 0:
        raise RuntimeError('Migration requires root for complete process inventory')
    if not path.is_relative_to(home) or path.resolve() != path:
        raise RuntimeError('Only canonical home paths are allowed; no symlink components')
    if not recognized(path) or tracked(path):
        raise RuntimeError('Not a verified, untracked build directory')
    resources = HEALTH.measure(str(home))
    if resources['device_errors'] or resources['unallocated_bytes'] < 15 * HEALTH.GIB or resources['free_bytes'] < 30 * HEALTH.GIB:
        raise RuntimeError('Insufficient safe Btrfs reserve or device errors; migration deferred')
    for current, dirs, files in os.walk(path, followlinks=False):
        for name in list(dirs):
            child = Path(current) / name
            if child.is_symlink():
                dirs.remove(name)
            elif child.is_mount() or child.stat().st_ino == 256:
                raise RuntimeError('Nested mount/subvolume found; preserve its separate contents')
    if HEALTH.transaction_reason() or HEALTH.active_build():
        raise RuntimeError('Update or build active; migration deferred')
    users = open_users(path)
    if users:
        raise RuntimeError(f'Directory still used by PIDs {users}; migration deferred')
    if 'No balance found' not in HEALTH.run('btrfs', 'balance', 'status', str(home)):
        raise RuntimeError('Balance active; migration deferred')
    state = Path('/var/lib/weasel-storage-health/migrations')
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    ident = uuid.uuid4().hex
    temporary = path.with_name(path.name + '.weasel-subvolume-' + ident)
    original = path.with_name(path.name + '.weasel-original-' + ident)
    journal = state / (ident + '.json')
    record = {'schema': 1, 'path': str(path), 'temporary': str(temporary),
              'original': str(original), 'original_inode': path.stat().st_ino,
              'phase': 'copying', 'time': time.time()}
    HEALTH.write_json(journal, record, 0o600)
    subprocess.run(['btrfs', 'subvolume', 'create', str(temporary)], check=True)
    # A reflink copy preserves data without a second physical copy. No fallback
    # to a full disk-consuming copy if the filesystem cannot clone extents.
    subprocess.run(['cp', '--archive', '--reflink=always', '--no-target-directory',
                    str(path), str(temporary)], check=True)
    compare(path, temporary)
    resources = HEALTH.measure(str(home))
    if resources['device_errors'] or resources['unallocated_bytes'] < 8 * HEALTH.GIB:
        raise RuntimeError('Reserve fell during copy; original retained, migration deferred')
    if open_users(path) or HEALTH.active_build():
        raise RuntimeError('Writer appeared during copy; original retained, migration deferred')
    record['phase'] = 'verified'
    HEALTH.write_json(journal, record, 0o600)
    os.rename(path, original)
    record['phase'] = 'original-renamed'
    HEALTH.write_json(journal, record, 0o600)
    try:
        os.rename(temporary, path)
    except Exception:
        os.rename(original, path)
        raise
    record['phase'] = 'installed-original-retained'
    HEALTH.write_json(journal, record, 0o600)
    compare(original, path)
    if path.stat().st_ino != 256 or original.stat().st_ino != record['original_inode']:
        raise RuntimeError('Unexpected filesystem identity; original retained')
    if open_users(original):
        raise RuntimeError('Original still open; retained for recovery')
    # Only the verified duplicate with the exact journal-owned inode is removed.
    # The installed cache remains, including permissions, hardlinks and xattrs.
    shutil.rmtree(original)
    record['phase'] = 'complete'
    record['completed_at'] = time.time()
    HEALTH.write_json(journal, record, 0o600)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, default=Path('/home/evilweasel'))
    parser.add_argument('--migrate', type=Path, help='One exact idle build directory, after coordinated release')
    args = parser.parse_args()
    if args.migrate:
        with open('/var/lib/weasel-updates/activation.lock', 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            own = Path('/var/lib/weasel-storage-health')
            own.mkdir(parents=True, exist_ok=True, mode=0o700)
            with open(own / 'operation.lock', 'a') as operation:
                fcntl.flock(operation, fcntl.LOCK_EX | fcntl.LOCK_NB)
                result = migrate(args.migrate, args.home)
    else:
        result = inventory(args.home)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
