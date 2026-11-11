"""Consistent copies of live Crew SQLite databases; all other Crew files stay put."""
from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import time


DATABASES = (
    '.t3-morrowbyte/userdata/statev2.sqlite',
    '.local/share/t3code-weasel/userdata/statev2.sqlite',
)


def retire_stage(stage, quarantine):
    quarantine.mkdir(mode=0o700, parents=True, exist_ok=True)
    retired = quarantine / ('building-retired-' + stage.name.removeprefix('crew-'))
    stage.rename(retired)
    shutil.rmtree(retired)


def cleanup_stages(parent, unpublished=False, quarantine=None):
    for stage in parent.glob('building-*' if unpublished else 'crew-*'):
        manifest = stage / 'manifest.json'
        if stage.is_symlink() or not stage.is_dir() or manifest.is_symlink():
            raise RuntimeError('Unrecognized Crew staging directory; preserved for inspection')
        if not unpublished:
            if not manifest.is_file():
                raise RuntimeError('Unrecognized Crew staging directory; preserved for inspection')
            data = json.loads(manifest.read_text())
            if data.get('producer') != 'weasel-cloud-backup' or data.get('version') != 1:
                raise RuntimeError('Unrecognized Crew staging manifest; preserved for inspection')
        allowed = {'manifest.json'} | {name + suffix for name in DATABASES
                                       for suffix in ('', '-journal', '-wal', '-shm')}
        for path in stage.rglob('*'):
            if path.is_symlink() or (path.is_file() and str(path.relative_to(stage)) not in allowed):
                raise RuntimeError('Unexpected Crew staging contents; preserved for inspection')
        if unpublished:
            shutil.rmtree(stage)
        else:
            if quarantine is None:
                raise RuntimeError('Published Crew copies require an excluded cleanup directory')
            retire_stage(stage, quarantine)


@contextmanager
def prepare(home):
    parent = home / '.local/share/weasel-backup'
    unpublished = home / '.local/state/weasel-backup/crew-staging'
    if parent.is_symlink() or unpublished.is_symlink():
        raise RuntimeError('Crew staging root must not be a symlink')
    # Reconcile old copies even if the user has since removed both profiles.
    cleanup_stages(unpublished, unpublished=True)
    cleanup_stages(parent, quarantine=unpublished)
    sources = [name for name in DATABASES if (home / name).exists()]
    if not sources:
        yield []
        return
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    unpublished.mkdir(mode=0o700, parents=True, exist_ok=True)
    admit = home / '.local/libexec/agent-storage-admit'
    if not admit.is_file():
        raise RuntimeError('Storage admission is required before staging Crew databases')
    result = subprocess.run([str(admit), '--target', str(home)], capture_output=True, text=True)
    if result.returncode or not json.loads(result.stdout).get('allowed'):
        raise RuntimeError('Insufficient storage headroom for consistent Crew backup')
    required = sum((home / name).stat().st_size for name in sources) + 256 * 1024**2
    if shutil.disk_usage(home).free < required:
        raise RuntimeError('Insufficient free space for consistent Crew backup')
    # Incomplete exports live below the already excluded backup state folder.
    # Publish into Home atomically only after every database and manifest closes.
    with tempfile.TemporaryDirectory(prefix='building-', dir=unpublished) as directory:
        stage = Path(directory)
        manifest = {'producer': 'weasel-cloud-backup', 'version': 1,
                    'databases': sources, 'complete': False}
        for name in sources:
            original = home / name
            if original.is_symlink():
                raise RuntimeError('Crew database must not be a symlink')
            target = stage / name
            target.parent.mkdir(mode=0o700, parents=True)
            deadline = time.monotonic() + 300

            def progress(*_):
                if time.monotonic() > deadline:
                    raise RuntimeError('Crew database copy timed out; no incomplete backup accepted')

            source_db = sqlite3.connect(original.as_uri() + '?mode=ro', uri=True)
            target_db = sqlite3.connect(target)
            try:
                source_db.backup(target_db, pages=1024, progress=progress)
            finally:
                target_db.close()
                source_db.close()
        manifest['complete'] = True
        (stage / 'manifest.json').write_text(json.dumps(manifest))
        published = parent / ('crew-' + stage.name.removeprefix('building-'))
        stage.rename(published)
        # Copies live inside the ordinary home snapshot. Omit only their live
        # SQLite originals and journals, never the surrounding Crew userdata.
        try:
            yield [f'/{name}{suffix}' for name in sources for suffix in ('', '-wal', '-shm')]
        finally:
            retire_stage(published, unpublished)


def restore(destination, home):
    restored_home = destination / str(home).lstrip('/')
    parent = restored_home / '.local/share/weasel-backup'
    for manifest in parent.glob('crew-*/manifest.json'):
        data = json.loads(manifest.read_text())
        if (data.get('version') != 1 or data.get('producer') != 'weasel-cloud-backup'
                or data.get('complete') is not True or not isinstance(data.get('databases'), list)):
            raise RuntimeError('Invalid Crew database restore manifest')
        for name in data['databases']:
            if name not in DATABASES:
                raise RuntimeError('Unexpected Crew database restore path')
            source = manifest.parent / name
            target = restored_home / name
            if (not source.resolve().is_relative_to(destination.resolve()) or source.is_symlink()
                    or not target.parent.resolve().is_relative_to(destination.resolve())):
                raise RuntimeError('Unsafe Crew database restore path')
            if any(Path(str(target) + suffix).exists() for suffix in ('', '-wal', '-shm')):
                raise RuntimeError('Refusing to overwrite an existing Crew database during restore')
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            source.replace(target)
