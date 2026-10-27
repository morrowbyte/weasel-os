#!/usr/bin/env python3
"""Unprivileged candidate preparation for the fixed Weasel update service."""
import argparse
import ctypes
import datetime as dt
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid


REPOSITORY = Path('/home/evilweasel/weasel-os')
STATE = Path('/home/evilweasel/.local/state/weasel-updates')
INBOX = Path('/var/lib/weasel-updates-inbox/request.json')
STATUS = Path('/var/lib/weasel-updates-status/status.json')
CURRENT = Path('/run/current-system')
PROFILE = Path('/nix/var/nix/profiles/system')
ORIGIN = 'https://github.com/morrowbyte/weasel-os.git'
ID_PATTERN = r'[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}'
TOPLEVEL = 'nixosConfigurations.nixy-laptop.config.system.build.toplevel'
CODEX = 'nixosConfigurations.nixy-laptop.config.home-manager.users.evilweasel.weasel.hephaestusRecoveryConsole.package'
GIB = 1024 ** 3


class Blocked(RuntimeError):
    pass


def peer(name):
    filename = Path(__file__).with_name(f'weasel-update-{name}.py')
    spec = importlib.util.spec_from_file_location(f'weasel_update_{name}', filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()


def encoded_candidate(value):
    # Full closure/manifests fit the existing reader bound without discarding
    # evidence. Other receipts retain their human-readable serialization.
    data = (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode()
    if len(data) > 1024 * 1024:
        raise Blocked('Candidate document exceeds size bound')
    return data


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.weasel-update-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        directory = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def directory_fd(path):
    """Anchor a directory without following any parent symlink."""
    path = Path(path).absolute()
    if '..' in path.parts:
        raise Blocked('Parent traversal is not an update receipt path')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def publish_exclusive(source, destination):
    """Atomically publish one complete inode, without replacing another request."""
    rename = getattr(ctypes.CDLL(None, use_errno=True), 'renameat2', None)
    if rename is None:
        raise Blocked('Atomic exclusive publication is unavailable')
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    source, destination = Path(source), Path(destination)
    source_fd = directory_fd(source.parent)
    try:
        destination_fd = directory_fd(destination.parent)
        try:
            if rename(source_fd, os.fsencode(source.name), destination_fd,
                      os.fsencode(destination.name), 1) != 0:
                code = ctypes.get_errno()
                raise OSError(code, os.strerror(code), str(destination))
            os.fsync(destination_fd)
        finally:
            os.close(destination_fd)
    finally:
        os.close(source_fd)


def write_receipt_exclusive(path, data):
    """Explicit outputs never overwrite a concurrent writer or follow a link."""
    path = Path(path).absolute()
    fd = directory_fd(path.parent)
    temporary = '.weasel-update-' + uuid.uuid4().hex
    try:
        file_fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                          0o600, dir_fd=fd)
        with os.fdopen(file_fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        rename = getattr(ctypes.CDLL(None, use_errno=True), 'renameat2', None)
        if rename is None:
            raise Blocked('Atomic exclusive publication is unavailable')
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        if rename(fd, os.fsencode(temporary), fd, os.fsencode(path.name), 1) != 0:
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code), str(path))
        os.fsync(fd)
    finally:
        try:
            os.unlink(temporary, dir_fd=fd)
        except FileNotFoundError:
            pass
        os.close(fd)


def command(args, *, cwd=None, timeout=1800):
    result = subprocess.run([str(x) for x in args], cwd=cwd, capture_output=True,
                            timeout=timeout, check=False)
    if result.returncode:
        # No broad environment, auth files or provider event streams enter logs.
        raise Blocked(f'{Path(str(args[0])).name} failed ({result.returncode}): '
                      + result.stderr.decode(errors='replace')[-2500:])
    return result.stdout


def git(repo, *args):
    return command(['git', '--no-replace-objects', '-c', 'core.fsmonitor=false',
                    '-c', 'core.untrackedCache=false', '-C', repo, *args])


def source_manifest(repo):
    """A whole tracked-tree digest detects editor, index and branch changes."""
    repo = Path(repo).resolve()
    stage = git(repo, 'ls-files', '--stage', '-z')
    files = {}
    for entry in stage.split(b'\0'):
        if not entry:
            continue
        header, raw = entry.split(b'\t', 1)
        mode, _, index_stage = header.split(b' ')
        name = os.fsdecode(raw)
        if index_stage != b'0' or mode == b'160000':
            raise Blocked('Unmerged index or submodule requires separate review')
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts:
            raise Blocked('Unsafe tracked path')
        path = repo / relative
        parent = path.parent
        while parent != repo:
            if parent.is_symlink():
                raise Blocked(f'Tracked directory is a symlink: {name}')
            parent = parent.parent
        if path.is_symlink():
            files[name] = {'link': os.readlink(path)}
        elif path.is_file():
            files[name] = {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                           'mode': stat.S_IMODE(path.stat().st_mode)}
        else:
            raise Blocked(f'Tracked source missing or unsupported: {name}')
    return {'head': git(repo, 'rev-parse', 'HEAD').decode().strip(),
            'index': hashlib.sha256(stage).hexdigest(), 'files': files}


def baseline(repo):
    if git(repo, 'branch', '--show-current').decode().strip() != 'main':
        raise Blocked('Integration checkout must be on main')
    if git(repo, 'status', '--porcelain=v1', '--untracked-files=normal').strip():
        raise Blocked('Integration checkout has unpublished edits; preserve them first')
    head = git(repo, 'rev-parse', 'HEAD').decode().strip()
    for operation in [[], ['--push']]:
        urls = git(repo, 'remote', 'get-url', '--all', *operation, 'origin').decode().splitlines()
        if urls != [ORIGIN]:
            raise Blocked('Origin must use the designated canonical repository for fetch and push')
    advertised = git(repo, 'ls-remote', 'origin', 'refs/heads/main').decode().split()
    if advertised != [head, 'refs/heads/main']:
        raise Blocked('Remote main changed; integrate concurrent work first')
    remote = git(repo, 'rev-parse', 'origin/main').decode().strip()
    if head != remote:
        raise Blocked('Main and origin/main differ; integrate concurrent work first')
    current = CURRENT.resolve(strict=True)
    if PROFILE.resolve(strict=True) != current:
        raise Blocked('Active and boot-default system generations differ')
    manifest = source_manifest(repo)
    drv = command(['nix', 'eval', '--raw', '--no-write-lock-file',
                   f'{repo}#{TOPLEVEL}.drvPath']).decode().strip()
    installed = command(['nix', 'path-info', '--derivation', current]).decode().strip()
    if drv != installed:
        raise Blocked('Main no longer reproduces the running system; activate/integrate separately')
    if source_manifest(repo) != manifest:
        raise Blocked('Main changed during baseline evaluation')
    return manifest, current


def require_power(directory=Path('/sys/class/power_supply')):
    adapters, batteries = [], []
    for item in directory.iterdir():
        kind = (item / 'type').read_text().strip()
        if kind == 'Battery':
            batteries.append(int((item / 'capacity').read_text().strip()))
        elif (item / 'online').exists():
            adapters.append((item / 'online').read_text().strip() == '1')
    if not any(adapters) or any(x < 30 for x in batteries):
        raise Blocked('AC power and at least 30% battery required')


def require_space(paths, reserve=65):
    for path in paths:
        info = os.statvfs(path)
        if info.f_bavail * info.f_frsize < reserve * GIB:
            raise Blocked(f'At least {reserve} GiB free required on candidate/Home/store filesystems')


def app_args(source, lane):
    if lane == 't3':
        return [f'{source}#packages.x86_64-linux.t3code']
    if lane == 'codex':
        return [f'{source}#{CODEX}']
    if lane == 'chatgpt':
        # A fixed expression chooses the actually installed host package.
        escaped = json.dumps('path:' + str(Path(source).resolve()))
        expression = ('let f = builtins.getFlake ' + escaped + '; in '
                      'builtins.head (builtins.filter (p: (p.pname or "") == "chatgpt") '
                      'f.nixosConfigurations.nixy-laptop.config.environment.systemPackages)')
        return ['--impure', '--expr', expression]
    raise Blocked('Unimplemented activation adapter; record and build its separate review candidate')


def acp_args(source):
    escaped = json.dumps('path:' + str(Path(source).resolve()))
    expression = ('let f = builtins.getFlake ' + escaped + '; in '
                  'builtins.head (builtins.filter (p: (p.pname or "") == "codex-acp") '
                  'f.nixosConfigurations.nixy-laptop.config.home-manager.users.evilweasel.home.packages)')
    return ['--impure', '--expr', expression]


def build(source, destination, args):
    lock_before = (Path(source) / 'flake.lock').read_bytes()
    result = command(['nix', 'build', '--no-write-lock-file', '--max-jobs', '1',
                      '--cores', '2', '--print-out-paths', '--out-link', destination,
                      *args], timeout=10800).decode().strip().splitlines()
    if len(result) != 1 or not result[0].startswith('/nix/store/'):
        raise Blocked('Expected one immutable build result')
    if (Path(source) / 'flake.lock').read_bytes() != lock_before:
        raise Blocked('Build unexpectedly changed candidate lock')
    return Path(result[0])


def candidate_id():
    return dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid.uuid4().hex[:8]


def check_id(value):
    if not re.fullmatch(ID_PATTERN, value):
        raise Blocked('Invalid candidate ID')
    return value


def open_candidate_file(directory):
    prefix = str(Path(directory).resolve()) + '/'
    for process in Path('/proc').iterdir():
        if not process.name.isdigit():
            continue
        try:
            descriptors = list((process / 'fd').iterdir())
        except (FileNotFoundError, PermissionError):
            continue
        for descriptor in descriptors:
            try:
                if os.readlink(descriptor).startswith(prefix):
                    return True
            except (FileNotFoundError, PermissionError):
                continue
    return False


def candidate_source_unchanged(source, record):
    expected = record.get('candidate_commit', record.get('baseline_commit'))
    if not expected or git(source, 'rev-parse', 'HEAD').decode().strip() != expected:
        return False
    if git(source, 'status', '--porcelain=v1', '--untracked-files=all', '--ignored').strip():
        return False
    # Hidden editor saves must not be discarded by Git's assume-unchanged or
    # sparse-checkout flags. Normal `worktree remove` repeats its dirty check.
    if any(row[:1] != b'H' for row in git(source, 'ls-files', '-v', '-z').split(b'\0') if row):
        return False
    actual = source_manifest(source)
    if record.get('candidate_manifest') is not None and actual != record['candidate_manifest']:
        return False
    for row in git(source, 'ls-files', '--stage', '-z').split(b'\0'):
        if not row:
            continue
        header, raw = row.split(b'\t', 1)
        mode, blob, stage = header.split(b' ')
        if stage != b'0' or mode not in {b'100644', b'100755', b'120000'}:
            return False
        path = source / os.fsdecode(raw)
        data = os.fsencode(os.readlink(path)) if mode == b'120000' else path.read_bytes()
        algorithm = hashlib.sha1 if len(blob) == 40 else hashlib.sha256
        calculated = algorithm(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest().encode()
        if calculated != blob:
            return False
    return True


def prune_candidates(repo, state, *, keep=3):
    """Remove only this helper's old, disposable, resolved candidate worktrees."""
    parent = Path(state) / 'candidates'
    if not parent.exists():
        return
    resolved = []
    status = read_json(STATUS) if STATUS.exists() else {}
    for directory in parent.iterdir():
        if directory.is_symlink() or not directory.is_dir() or not re.fullmatch(ID_PATTERN, directory.name):
            continue
        marker = directory / 'candidate.json'
        if not marker.is_file() or marker.is_symlink():
            continue
        record = read_json(marker)
        if record.get('id') != directory.name or record.get('source') != str(directory / 'source'):
            continue
        phase = record.get('phase')
        if phase in {'preparing', 'building'}:
            # The caller holds the namespace preparation lock. A previous local
            # build cannot still run, and these phases were never submit-ready.
            phase = 'failed'
            record.update(phase=phase, error='Previous preparation interrupted before submission')
            atomic_write(marker, encoded_candidate(record))
        if phase == 'submitted' and status.get('id') == directory.name and status.get('phase') == 'complete':
            phase = 'complete'
            record['phase'] = phase
            atomic_write(marker, encoded_candidate(record))
        if phase not in {'failed', 'tested', 'complete'}:
            continue
        resolved.append(directory)
    for directory in sorted(resolved, reverse=True)[keep:]:
        record = read_json(directory / 'candidate.json')
        known = {'source', 'candidate.json', 'old-app', 'new-app', 'tested-system', 'tested-acp', 'tested-codex-profile', 'old-acp', 'probe', 'probes'}
        known |= {'old-' + kind for kind in ('t3', 'codex', 'chatgpt')}
        known |= {'new-' + kind for kind in ('t3', 'codex', 'chatgpt')}
        known |= {'probe-' + kind for kind in ('t3', 'codex', 'chatgpt')}
        if any(child.name not in known for child in directory.iterdir()) or open_candidate_file(directory):
            continue
        source = directory / 'source'
        if source.exists():
            if source.is_symlink() or git(source, 'rev-parse', '--show-toplevel').decode().strip() != str(source):
                continue
            branch = git(source, 'symbolic-ref', '-q', 'HEAD').decode().strip()
            if branch != 'refs/heads/weasel-update-' + directory.name:
                continue
            if not candidate_source_unchanged(source, record):
                continue
            git(repo, 'worktree', 'remove', source)
        # Candidate directories are explicitly disposable. Personal files and
        # any directory outside this exact private namespace are never scanned.
        shutil.rmtree(directory)


def candidate_directory(state, value):
    directory = Path(state) / 'candidates' / check_id(value)
    if directory.is_symlink():
        raise Blocked('Candidate directory cannot be a symlink')
    return directory


def read_json(path, limit=1024 * 1024):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise Blocked('Input document exceeds size bound')
    return json.loads(data)


def require_resolved():
    if STATUS.exists():
        record = read_json(STATUS)
        if (record.get('outcome') in {'running', 'working'} or
                record.get('phase') in {'recovery-required', 'integrating', 'activating', 'snapshot-created',
                                      'checkpoint-created', 'publishing', 'rolling-back-system',
                                      'preparing', 'tested', 'source-integrated', 'pruning'}):
            raise Blocked('Activation transaction requires recovery/finish before more updates')


def prepare(repo, state, lane, metadata):
    discover, gates = peer('discover'), peer('gates')
    if lane == 'codex':
        raise Blocked(discover.CODEX_ADAPTER_REASON)
    require_resolved()
    prune_candidates(repo, state, keep=2)
    require_power()
    require_space([repo, state, Path('/nix/store')])
    manifest, old_system = baseline(repo)
    info = read_json(metadata)
    if info.get('lane') != lane or info.get('candidate') is None:
        raise Blocked('Discovery has no new candidate for this lane')
    identifier = candidate_id()
    directory = candidate_directory(state, identifier)
    directory.mkdir(mode=0o700, parents=True)
    source = directory / 'source'
    ref = 'refs/heads/weasel-update-' + identifier
    record = {'schema': 1, 'id': identifier, 'lane': lane, 'phase': 'preparing',
              'baseline_commit': manifest['head'], 'baseline_system': str(old_system),
              'candidate_ref': ref, 'source': str(source)}
    atomic_write(directory / 'candidate.json', encoded_candidate(record))
    try:
        git(repo, 'worktree', 'add', '-b', ref.removeprefix('refs/heads/'), source, manifest['head'])
        old_bytes = (source / discover.SOURCE_FILES[lane]).read_bytes()
        new_bytes = discover.replace_pin(lane, old_bytes, info['candidate'])
        transition = discover.validate_transition(lane, old_bytes, new_bytes, network=True)
        atomic_write(source / discover.SOURCE_FILES[lane], new_bytes)
        # Preserve Git's original executable bits; metadata adapters are regular files.
        (source / discover.SOURCE_FILES[lane]).chmod((repo / discover.SOURCE_FILES[lane]).stat().st_mode & 0o777)
        record.update(phase='building', transition=transition)
        atomic_write(directory / 'candidate.json', encoded_candidate(record))
        if lane != 't3':
            command(['nix-instantiate', '--parse', source / discover.SOURCE_FILES[lane]])
        affected_hosts = ['nixy-laptop', 'michapc', 'michapc-debug'] if lane == 't3' else ['nixy-laptop']
        for host in affected_hosts:
            attribute = f'nixosConfigurations.{host}.config.system.build.toplevel.drvPath'
            command(['nix', 'eval', '--raw', '--no-write-lock-file', f'{source}#{attribute}'])
        old_app = build(repo, directory / 'old-app', app_args(repo, lane))
        new_app = build(source, directory / 'new-app', app_args(source, lane))
        new_system = build(source, directory / 'tested-system', [f'{source}#{TOPLEVEL}'])
        if lane == 'codex':
            acp_app = build(source, directory / 'tested-acp', acp_args(source))
            probe = gates.probe(lane, new_app, directory / 'probe', acp_app=acp_app)
        elif lane == 't3':
            probe = gates.probe(lane, new_app, directory / 'probe', old_app=old_app)
        else:
            probe = gates.probe(lane, new_app, directory / 'probe')
        if not probe.get('ok'):
            raise Blocked('Owned isolated application probe failed')
        # The learning is verified text, not an untrusted update to executable code.
        learning = discover.learning_suffix(identifier, lane,
                                             transition['old']['version'], transition['new']['version'])
        original_log = (source / 'agent-learnings.md').read_bytes()
        atomic_write(source / 'agent-learnings.md', original_log + learning)
        # Rebuild the exact final source after adding the mandated learning.
        final_system = build(source, directory / 'tested-system', [f'{source}#{TOPLEVEL}'])
        changes = {discover.SOURCE_FILES[lane]: {'old': old_bytes, 'new': new_bytes},
                   'agent-learnings.md': {'old': original_log, 'new': original_log + learning}}
        closure = gates.verify_closure(old_system, final_system,
                                      {lane: {'old': str(old_app), 'new': str(new_app)}}, changes)
        if source_manifest(repo) != manifest or CURRENT.resolve() != old_system or PROFILE.resolve() != old_system:
            raise Blocked('Baseline/editor/system changed during candidate checks; candidate retained')
        require_power()
        require_space([repo, state, Path('/nix/store')], reserve=50)
        git(source, 'add', '--', discover.SOURCE_FILES[lane], 'agent-learnings.md')
        tested_files = source_manifest(source)['files']
        version = transition['new']['version']
        git(source, 'commit', '-S', '-m', f'update: verify {lane} {version}')
        git(source, 'verify-commit', 'HEAD')
        commit = git(source, 'rev-parse', 'HEAD').decode().strip()
        if source_manifest(source)['files'] != tested_files or git(source, 'status', '--porcelain=v1').strip():
            raise Blocked('Commit hook/editor changed tested candidate; keep it for review')
        record.update(phase='tested', candidate_commit=commit,
                      tested_system=str(final_system), app=str(new_app), probe=probe,
                      closure=closure, manifest=manifest, candidate_manifest=source_manifest(source))
        atomic_write(directory / 'candidate.json', encoded_candidate(record))
        return record
    except Exception as error:
        record.update(phase='failed', error=str(error))
        atomic_write(directory / 'candidate.json', encoded_candidate(record))
        raise


def new_batch(repo, state, mode):
    batch = peer('batch')
    if mode not in batch.MODES:
        raise Blocked('Unknown batch mode')
    require_resolved()
    require_power()
    require_space([repo, state, Path('/nix/store')])
    manifest, system = baseline(repo)
    git(repo, 'verify-commit', manifest['head'])
    identifier = candidate_id()
    directory = candidate_directory(state, identifier)
    directory.mkdir(mode=0o700, parents=True)
    source = directory / 'source'
    record = {'schema': 2, 'mode': mode, 'id': identifier, 'phase': 'staging',
              'baseline_commit': manifest['head'], 'baseline_system': str(system),
              'candidate_ref': 'refs/heads/weasel-update-' + identifier,
              'source': str(source), 'manifest': manifest}
    atomic_write(directory / 'candidate.json', encoded_candidate(record))
    try:
        git(repo, 'worktree', 'add', '-b', record['candidate_ref'].removeprefix('refs/heads/'), source, manifest['head'])
        # Git checks out respecting this helper's private umask. Preserve the
        # original physical modes inside the still-private candidate parent.
        for name, entry in manifest['files'].items():
            if 'mode' in entry:
                (source / name).chmod(entry['mode'])
        if source_manifest(repo) != manifest:
            raise Blocked('Baseline changed while staging; candidate retained')
        return record
    except Exception as error:
        record.update(phase='failed', error=str(error))
        atomic_write(directory / 'candidate.json', encoded_candidate(record))
        raise


def batch_source_changes(source, record):
    """Read exact working bytes; refuse new/deleted/symlink or hidden mode edits."""
    current = source_manifest(source)
    before = record['manifest']
    if current['head'] != before['head'] or current['files'].keys() != before['files'].keys():
        raise Blocked('Batch must remain on its original baseline with the same tracked file set')
    if git(source, 'ls-files', '--others', '--exclude-standard').strip():
        raise Blocked('Batch contains untracked files; retain them outside the candidate before preparing')
    if git(source, 'ls-files', '--others', '--ignored', '--exclude-standard').strip():
        raise Blocked('Batch contains ignored personal/artifact files; preserve them outside the source')
    if any(row[:1] != b'H' for row in git(source, 'ls-files', '-v', '-z').split(b'\0') if row):
        raise Blocked('Batch index hides files with assume-unchanged or sparse flags')
    changes = {}
    for name, value in current['files'].items():
        old = before['files'][name]
        if value == old:
            continue
        if 'link' in value or 'link' in old or value.get('mode') != old.get('mode'):
            raise Blocked('Batch changes a symlink or source file mode')
        changes[name] = (git(source, 'show', before['head'] + ':' + name), (source / name).read_bytes())
    return changes


def prepare_batch(repo, state, source, mode):
    batch, gates = peer('batch'), peer('gates')
    source = Path(source).absolute()
    # Use the original spelling and anchored traversal; resolving first would
    # hide an alias into an unrelated worktree or replace a parent beneath us.
    identifier = source.parent.name
    directory = candidate_directory(state, identifier)
    if source != directory / 'source':
        raise Blocked('Batch source must be the helper-created private candidate worktree')
    fd = directory_fd(source)
    os.close(fd)
    record = read_json(directory / 'candidate.json')
    if (record.get('schema') != 2 or record.get('mode') != mode or record.get('id') != identifier
            or record.get('source') != str(source) or record.get('phase') not in {'staging', 'failed'}):
        raise Blocked('Batch does not match its helper-owned staging record')
    if git(source, 'symbolic-ref', '-q', 'HEAD').decode().strip() != record['candidate_ref']:
        raise Blocked('Batch branch changed')
    require_resolved()
    require_power()
    require_space([repo, state, Path('/nix/store')])
    manifest, old_system = baseline(repo)
    if manifest != record['manifest'] or str(old_system) != record['baseline_system']:
        raise Blocked('Main/active baseline changed; keep the batch and create a new one')
    try:
        changes = batch_source_changes(source, record)
        old_log = git(source, 'show', record['baseline_commit'] + ':agent-learnings.md')
        actual_log = (source / 'agent-learnings.md').read_bytes()
        previous = record.get('helper_learning', '').encode()
        if actual_log not in {old_log, old_log + previous}:
            raise Blocked('Batch learning was edited outside the helper')
        changes.pop('agent-learnings.md', None)
        learning = batch.learning_suffix(identifier, mode, changes)
        changes['agent-learnings.md'] = (old_log, old_log + learning)
        policy = batch.validate_changes(changes, mode, identifier)
        published = batch.verify_published(repo, source)
        atomic_write(source / 'agent-learnings.md', old_log + learning)
        (source / 'agent-learnings.md').chmod(manifest['files']['agent-learnings.md']['mode'])
        record.update(phase='building', policy=policy, helper_learning=learning.decode())
        atomic_write(directory / 'candidate.json', encoded_candidate(record))
        tested_manifest = source_manifest(source)
        for name in changes:
            if name.endswith('.nix'):
                command(['nix-instantiate', '--parse', source / name])
        derivations = {}
        for host in batch.update_hosts(mode):
            attribute = f'nixosConfigurations.{host}.config.system.build.toplevel.drvPath'
            derivations[host] = command(['nix', 'eval', '--raw', '--no-write-lock-file', f'{source}#{attribute}']).decode().strip()
        sensitive = batch.verify_sensitive_state(repo, source, lambda expr: command(
            ['nix', *batch.sensitive_eval_args(expr)]), mode=mode,
            migration_executor=lambda name, args: command([name, *args], timeout=1800))
        lock_before = (source / 'flake.lock').read_bytes()
        command(['nix', 'flake', 'check', '--no-write-lock-file', '--max-jobs', '1', '--cores', '2', str(source)], timeout=10800)
        if (source / 'flake.lock').read_bytes() != lock_before:
            raise Blocked('Flake checks changed the staged lock')
        new_system = build(source, directory / 'tested-system', [f'{source}#{TOPLEVEL}'])
        niri = json.loads(command([sys.executable, Path(__file__).with_name('weasel-update-niri.py'),
                                  '--source', source, '--run-dir', directory / 'probes' / ('niri-' + uuid.uuid4().hex)], timeout=300))
        if niri.get('ok') is not True:
            raise Blocked('Built Niri configuration validation failed')
        app_pairs, probes = {}, {}
        probe_root = directory / 'probes' / uuid.uuid4().hex
        probe_root.mkdir(mode=0o700, parents=True)
        old_acp = build(repo, directory / 'old-acp', acp_args(repo))
        new_acp = build(source, directory / 'tested-acp', acp_args(source))
        app_pairs['codex-acp'] = {'old': str(old_acp), 'new': str(new_acp)}
        for kind in ('t3', 'codex', 'chatgpt'):
            old_app = build(repo, directory / ('old-' + kind), app_args(repo, kind))
            new_app = build(source, directory / ('new-' + kind), app_args(source, kind))
            app_pairs[kind] = {'old': str(old_app), 'new': str(new_app)}
            profile_ownership = None
            if kind == 'codex':
                profile = build(source, directory / 'tested-codex-profile',
                                [f'{source}#nixosConfigurations.nixy-laptop.config.home-manager.users.evilweasel.home.path'])
                profile_ownership = gates.verify_codex_profile(profile, new_app, new_acp)
            if old_app == new_app and (kind != 'codex' or old_acp == new_acp):
                probes[kind] = {'changed': False, 'probed': False, 'profile_ownership': profile_ownership,
                                'reason': 'exact immutable app/adapter unchanged'}
                continue
            kwargs = {'old_app': old_app} if kind == 't3' else {'acp_app': new_acp} if kind == 'codex' else {}
            result = gates.probe(kind, new_app, probe_root / kind, **kwargs)
            if result.get('ok') is not True:
                raise Blocked('Critical application probe failed: ' + kind)
            probes[kind] = {'changed': True, 'probed': True, 'receipt': result, 'profile_ownership': profile_ownership}
        closure = batch.verify_closure(old_system, new_system, app_pairs, changes)
        units = batch.verify_runtime_units(old_system, new_system)
        if (source_manifest(source) != tested_manifest or source_manifest(repo) != manifest
                or CURRENT.resolve() != old_system or PROFILE.resolve() != old_system):
            raise Blocked('Source/editor/index/system changed while testing; candidate retained')
        require_power()
        require_space([repo, state, Path('/nix/store')], reserve=50)
        git(source, 'add', '--', *sorted(changes))
        git(source, 'commit', '-S', '-m', 'update: verify ' + mode + ' ' + identifier)
        git(source, 'verify-commit', 'HEAD')
        commit = git(source, 'rev-parse', 'HEAD').decode().strip()
        if (source_manifest(source)['files'] != tested_manifest['files']
                or git(source, 'status', '--porcelain=v1').strip()
                or git(source, 'rev-list', '--parents', '-n', '1', commit).decode().split() != [commit, record['baseline_commit']]):
            raise Blocked('Commit hook/editor changed tested batch or its parent; candidate retained')
        if not candidate_source_unchanged(source, {'candidate_commit': commit}):
            raise Blocked('Signed Git blobs differ from tested working bytes; candidate retained')
        record.update(phase='tested', candidate_commit=commit, tested_system=str(new_system),
                      host_derivations=derivations, app_tests=probes, closure=closure, runtime_units=units, sensitive_policy=sensitive,
                      niri=niri,
                      published=published,
                      candidate_manifest=source_manifest(source))
        atomic_write(directory / 'candidate.json', encoded_candidate(record))
        return record
    except Exception as error:
        record.update(phase='failed', error=str(error))
        atomic_write(directory / 'candidate.json', encoded_candidate(record))
        raise


def submit(repo, state, identifier, *, inbox=INBOX):
    require_resolved()
    directory = candidate_directory(state, identifier)
    record = read_json(directory / 'candidate.json')
    if record.get('phase') != 'tested' or record.get('id') != identifier:
        raise Blocked('Only a completed, tested candidate can be submitted')
    manifest, system = baseline(repo)
    if manifest != record['manifest'] or str(system) != record['baseline_system']:
        raise Blocked('Candidate baseline changed; keep source and re-prepare against new main')
    if git(repo, 'rev-parse', record['candidate_ref']).decode().strip() != record['candidate_commit']:
        raise Blocked('Candidate branch changed after tests')
    git(repo, 'verify-commit', record['candidate_commit'])
    request = {name: record[name] for name in ('id', 'baseline_commit', 'baseline_system',
                                             'candidate_commit', 'candidate_ref')}
    request['schema'] = record.get('schema', 1)
    if request['schema'] == 2:
        request['mode'] = record['mode']
    if not inbox.parent.is_dir() or inbox.parent.is_symlink():
        raise Blocked('Activation inbox is not installed; perform verified bootstrap first')
    # Validate the next record before publication; a size refusal must leave
    # no request pending with an unwritten submitted receipt.
    submitted = dict(record, phase='submitted')
    submitted_data = encoded_candidate(submitted)
    # A complete single-link inode avoids partial-file/inbox observation races.
    fd, temporary = tempfile.mkstemp(prefix='.request-', dir=inbox.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(encoded(request))
            stream.flush()
            os.fsync(stream.fileno())
        publish_exclusive(Path(temporary), inbox)
    except FileExistsError as error:
        raise Blocked('Another update request is pending; it was preserved') from error
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    atomic_write(directory / 'candidate.json', submitted_data)
    return {'id': identifier, 'phase': 'submitted', 'request': str(inbox),
            'note': 'Activation is pending; submission is not activation success.'}


def registry_path():
    for path in (Path(__file__).parent / 'update-pins.json',
                 Path(__file__).parent.parent / 'config/update-pins.json'):
        if path.is_file():
            return path
    raise Blocked('Installed pin registry missing')


def review_start(repo, state):
    require_resolved()
    prune_candidates(repo, state)
    registry = read_json(registry_path())
    head = git(repo, 'rev-parse', 'HEAD').decode().strip()
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    discover = peer('discover')
    current_pins = {lane: discover.read_pin(lane, (repo / filename).read_bytes())
                    for lane, filename in discover.SOURCE_FILES.items()}
    lock = read_json(repo / 'flake.lock')
    inputs = lock['nodes']['root']['inputs']
    current_inputs = {name: lock['nodes'][node].get('locked')
                      for name, node in inputs.items() if isinstance(node, str)}
    output = {'schema': 1, 'date': today, 'baseline_commit': head, 'repository': str(repo),
              'registry': registry, 'current_pins': current_pins, 'current_inputs': current_inputs,
              'all_lock_nodes': lock['nodes'],
              'codex_source_bundle': discover.codex_source_bundle(repo),
              'activation_installed': INBOX.parent.is_dir(),
              'support': registry.get('support'),
              'note': 'Review every entry; build/activate only verified adapter candidates. Unknown reasons get dated investigation.'}
    atomic_write(state / 'reviews' / f'{today}-context.json', encoded(output))
    return output


def review_baseline(repo):
    head = git(repo, 'rev-parse', 'HEAD').decode().strip()
    raw = (repo / 'flake.lock').read_bytes()
    nodes = json.loads(raw)['nodes']
    locked = {name: node['locked'] for name, node in nodes.items() if 'locked' in node}
    if git(repo, 'rev-parse', 'HEAD').decode().strip() != head:
        raise Blocked('Source changed while loading the review baseline')
    return {'commit': head, 'lock_sha256': hashlib.sha256(raw).hexdigest(), 'nodes': locked}


def validate_review_finding(entry, today):
    if not isinstance(entry, dict):
        raise Blocked('Review finding must be an object')
    if entry.get('decision') not in {'unchanged', 'candidate', 'investigate', 'migration', 'replacement',
                                     'blocked', 'hold-with-evidence', 'adapter-needed'}:
        raise Blocked('Unknown review decision')
    if not isinstance(entry.get('reason'), str) or not entry['reason'].strip():
        raise Blocked('Review needs a concrete pin reason or investigation finding')
    next_date = dt.date.fromisoformat(entry['next_review'])
    if next_date <= today or next_date > today + dt.timedelta(days=7):
        raise Blocked('Next review must be within seven days; no indefinite holds')
    if entry['decision'] == 'investigate' and next_date != today + dt.timedelta(days=1):
        raise Blocked('Unknown reasons need a fresh investigation by tomorrow')
    sources = entry.get('evidence_urls', entry.get('evidence'))
    if not isinstance(sources, list) or not sources:
        raise Blocked('Each review needs exact source evidence')
    for source in sources:
        if not isinstance(source, str) or not source.startswith(('https://', 'repo:')):
            raise Blocked('Evidence must be exact HTTPS or repo path reference')


def record_review(state, path, repo=REPOSITORY):
    value = read_json(path)
    entries = value.get('entries')
    if not isinstance(entries, list):
        raise Blocked('Daily review requires entries list')
    expected = {entry['id'] for entry in read_json(registry_path())['entries']}
    actual = [entry.get('id') for entry in entries]
    if any(not isinstance(value, str) or not re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,127}', value) for value in actual):
        raise Blocked('Review entry ID is invalid')
    if len(actual) != len(set(actual)) or not expected.issubset(set(actual)):
        raise Blocked('Review must cover every registered input/package exactly once; new pins may be added')
    today = dt.datetime.now(dt.timezone.utc).date()
    baseline = review_baseline(repo)
    if value.get('baseline_commit') != baseline['commit']:
        raise Blocked('Daily review must name the actual current source commit')
    for entry in entries:
        validate_review_finding(entry, today)
        if not isinstance(entry.get('current'), (str, dict)) or not entry['current']:
            raise Blocked('Review needs the actual current version or revision')
    nodes = value.get('lock_nodes')
    if not isinstance(nodes, dict) or set(nodes) != set(baseline['nodes']):
        raise Blocked('Daily review must explicitly cover every resolved lock node')
    for name, finding in nodes.items():
        validate_review_finding(finding, today)
        if finding.get('current_locked') != baseline['nodes'][name]:
            raise Blocked(f'Review lock node {name} does not match the current exact source')
    if review_baseline(repo) != baseline:
        raise Blocked('Source changed while recording the daily review')
    value.update(schema_version=1, reviewed_at=today.isoformat(), date=today.isoformat(),
                 coverage={'registered_pins': len(expected), 'reviewed_pins': len(entries),
                           'resolved_lock_nodes': len(nodes), 'lock_sha256': baseline['lock_sha256']})
    target = state / 'reviews' / f'{today}-review.json'
    atomic_write(target, encoded(value))
    return {'phase': 'review-recorded', 'path': str(target), 'entries': len(entries),
            'resolved_lock_nodes': len(nodes), 'baseline_commit': baseline['commit']}


def check_cli_paths(repository, state, output):
    if repository.resolve() != REPOSITORY.resolve():
        raise Blocked('The installed helper operates only on the designated integration checkout')
    resolved = state.resolve()
    if resolved != STATE.resolve() and not (resolved.parent == Path('/tmp') and
                                          re.fullmatch(r'weasel-updates-[A-Za-z0-9._-]+', resolved.name)):
        raise Blocked('State must use the designated private namespace or an owned /tmp/weasel-updates-* fixture')
    if state.is_symlink():
        raise Blocked('State cannot be a symlink')
    for path in [state, output] if output else [state]:
        path = path.absolute()
        if '..' in path.parts:
            raise Blocked('Parent traversal is not an update state path')
        for ancestor in [path, *path.parents]:
            if ancestor.is_symlink():
                raise Blocked('Update state and receipt parents cannot be symlinks')
    if output:
        destination = output.resolve()
        if not (destination.is_relative_to(resolved) or destination.is_relative_to(Path('/tmp'))):
            raise Blocked('Output belongs only in private update state or a temporary receipt file')
        if output.exists() or output.is_symlink():
            raise Blocked('Explicit output already exists; choose a new receipt path')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--review-start', action='store_true')
    action.add_argument('--record-review', type=Path)
    action.add_argument('--discover', choices=['t3', 'codex', 'chatgpt'])
    action.add_argument('--prepare', choices=['t3', 'codex', 'chatgpt'])
    action.add_argument('--new-batch', action='store_true')
    action.add_argument('--prepare-batch', type=Path)
    action.add_argument('--submit')
    action.add_argument('--status', action='store_true')
    parser.add_argument('--metadata', type=Path)
    parser.add_argument('--mode', choices=['batch', 'release-migration'], default='batch')
    parser.add_argument('--repository', type=Path, default=REPOSITORY)
    parser.add_argument('--state', type=Path, default=STATE)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    os.umask(0o077)
    if os.geteuid() == 0:
        raise Blocked('Prepare as the normal user; only immutable activation service runs as root')
    check_cli_paths(args.repository, args.state, args.output)
    args.state.mkdir(mode=0o700, parents=True, exist_ok=True)
    if (args.state.is_symlink() or args.state.stat().st_uid != os.getuid()
            or stat.S_IMODE(args.state.stat().st_mode) != 0o700):
        raise Blocked('State must be a private directory owned by the calling user')
    os.environ['GIT_OPTIONAL_LOCKS'] = '0'
    os.environ['GIT_NO_REPLACE_OBJECTS'] = '1'
    lock_fd = os.open(args.state / 'prepare.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(lock_fd, 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.status:
            result = read_json(STATUS) if STATUS.exists() else {'phase': 'not-installed'}
        elif args.review_start:
            result = review_start(args.repository, args.state)
        elif args.record_review:
            result = record_review(args.state, args.record_review, args.repository)
        elif args.discover:
            discover = peer('discover')
            # Discovery owns bounded source checks and its standalone CLI contract.
            target = args.state / 'discovery' / f'{candidate_id()}-{args.discover}.json'
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            command([sys.executable, Path(discover.__file__), '--lane', args.discover,
                     '--repository', args.repository, '--output', target], timeout=1200)
            result = read_json(target)
            result['metadata_path'] = str(target)
        elif args.prepare:
            if not args.metadata:
                raise Blocked('--prepare needs --metadata from bounded discovery')
            result = prepare(args.repository, args.state, args.prepare, args.metadata)
        elif args.new_batch:
            result = new_batch(args.repository, args.state, args.mode)
        elif args.prepare_batch:
            result = prepare_batch(args.repository, args.state, args.prepare_batch, args.mode)
        else:
            result = submit(args.repository, args.state, check_id(args.submit))
    if args.output:
        write_receipt_exclusive(args.output, encoded(result))
    print(encoded(result).decode(), end='')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({'phase': 'blocked', 'reason': str(error)}), file=sys.stderr)
        raise SystemExit(1)
