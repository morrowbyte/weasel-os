#!/usr/bin/env python3
"""Restic REST v2 transport using the official Proton CLI; ephemeral bounded spool.

Only a dedicated repository is exposed, on loopback with a per-process token.
No source file selection, encryption, deduplication or restore format lives here:
restic owns those. CLI output and credential material never enter HTTP errors.
"""
import argparse
import base64
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import time
from urllib.parse import urlsplit

KINDS = {'data', 'index', 'keys', 'snapshots', 'locks'}


def private_json(path, value):
    """Atomically publish private state without following an existing log symlink."""
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(value, stream)
            stream.close()
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def record_failure(state, tool, operation, code, stdout='', stderr=''):
    """Retain ten bounded failures; successes must not erase the diagnosis."""
    def safe(text):
        if isinstance(text, bytes):
            text = text.decode(errors='replace')
        text = text or ''
        if len(text) > 32768:
            text = text[:8192] + '\n<output shortened>\n' + text[-24576:]
        text = re.sub(r'https?://[^\s\"<>]+', '<URL redacted>', text)
        text = re.sub(r'(?i)(bearer\s+)\S+', r'\1<redacted>', text)
        text = re.sub(r'(?i)((?<![\w-])[\"\']?[\w-]{0,40}(?:token|password|secret|authorization)[\w-]{0,40}[\"\']?\s*[:=]\s*)(\"[^\"]*\"|\'[^\']*\'|[^\s,}]+)',
                      r'\1"<redacted>"', text)
        return text

    errors = state / 'errors'
    errors.mkdir(mode=0o700, exist_ok=True)
    private_json(errors / f'{time.time_ns()}-{tool}.json', {
        'time': time.time(), 'tool': tool, 'operation': operation, 'code': code,
        'stdout': safe(stdout), 'stderr': safe(stderr)})
    for old in sorted(errors.glob('*.json'))[:-10]:
        old.unlink()


class Drive:
    def __init__(self, cli, root, spool):
        self.cli, self.root, self.spool = cli, root.rstrip('/'), Path(spool)
        self.spool.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        self.catalogue = {}
        self.pending_path = self.spool.parent / 'pending-deletes.json'
        self.pending = json.loads(self.pending_path.read_text()) if self.pending_path.exists() else {}
        self.metrics = {'started': time.time(), 'data_packs_uploaded': 0, 'data_bytes_uploaded': 0}

    def command(self, *args):
        try:
            result = subprocess.run([self.cli, 'filesystem', *args, '--json'],
                                    capture_output=True, text=True, timeout=3600)
        except subprocess.TimeoutExpired as exc:
            record_failure(self.spool.parent, 'proton', args[0], 'timeout', exc.stdout, exc.stderr)
            raise RuntimeError('Proton Drive operation timed out; see private errors directory') from None
        if result.returncode:
            record_failure(self.spool.parent, 'proton', args[0], result.returncode, result.stdout, result.stderr)
            raise RuntimeError('Proton Drive operation failed; CLI exit ' + str(result.returncode)
                               + '; see private errors directory')
        try:
            payload = json.loads(result.stdout) if result.stdout.strip() else None
        except ValueError:
            record_failure(self.spool.parent, 'proton', args[0], 'invalid-json', result.stdout, result.stderr)
            raise RuntimeError('Invalid Proton Drive response; see private errors directory') from None
        def failed(value):
            if isinstance(value, dict):
                return value.get('ok') is False or any(failed(v) for v in value.values())
            return isinstance(value, list) and any(failed(v) for v in value)
        if failed(payload):
            record_failure(self.spool.parent, 'proton', args[0], 'failed-item', result.stdout, result.stderr)
            raise RuntimeError('Proton Drive reported a failed item; see private errors directory')
        return payload

    @staticmethod
    def rows(payload):
        if not isinstance(payload, list):
            raise RuntimeError('Unexpected Proton CLI list format')
        result = {}
        for row in payload:
            name = row.get('name', {})
            if name.get('ok'):
                result[name['value']] = row
        return result

    def folders(self):
        parent = '/my-files'
        for part in self.root.split('/')[2:]:
            entries = self.rows(self.command('list', parent))
            if part not in entries:
                self.command('create-folder', parent, part)
            elif entries[part]['type'] != 'folder':
                raise RuntimeError('Repository parent is not a folder')
            parent += '/' + part
        for kind in sorted(KINDS):
            entries = self.rows(self.command('list', self.root))
            if kind not in entries:
                self.command('create-folder', self.root, kind)

    def listing(self, kind):
        if kind not in self.catalogue:
            rows = self.rows(self.command('list', self.root + '/' + kind if kind else self.root))
            self.catalogue[kind] = {name: int(row['activeRevision']['claimedSize'])
                                    for name, row in rows.items() if row['type'] == 'file'}
        return self.catalogue[kind]

    def remote(self, kind, name):
        return self.root + ('/' + kind if kind else '') + '/' + name

    def read(self, kind, name):
        if name not in self.listing(kind):
            raise FileNotFoundError(name)
        with tempfile.TemporaryDirectory(dir=self.spool) as tmp:
            self.command('download', '-f', 'remove', self.remote(kind, name), tmp)
            payload = (Path(tmp) / name).read_bytes()
        if name != 'config' and hashlib.sha256(payload).hexdigest() != name:
            raise RuntimeError('Downloaded repository object failed SHA256 validation')
        return payload

    def write(self, kind, name, payload):
        if name != 'config' and hashlib.sha256(payload).hexdigest() != name:
            raise ValueError('Object name does not match content hash')
        if name in self.listing(kind):
            if self.read(kind, name) != payload:
                raise RuntimeError('Refusing repository object overwrite')
            return
        with tempfile.TemporaryDirectory(dir=self.spool) as tmp:
            p = Path(tmp) / name
            p.write_bytes(payload)
            self.command('upload', '-f', 'skip', '-d', 'merge', '-t', str(p),
                         self.root + ('/' + kind if kind else ''))
        # Re-list after the upload; never acknowledge a skipped/missing object.
        self.catalogue.pop(kind, None)
        if self.listing(kind).get(name) != len(payload):
            raise RuntimeError('Uploaded repository object missing or size mismatch')
        if kind == 'data':
            self.metrics['data_packs_uploaded'] += 1
            self.metrics['data_bytes_uploaded'] += len(payload)
            self.metrics['updated'] = time.time()
            (self.spool.parent / 'transfer-progress.json').write_text(json.dumps(self.metrics))

    def delete(self, kind, name):
        key = self.remote(kind, name)
        if name in self.listing(kind) or key in self.pending:
            # Dedicated restic repository only. Permanent delete avoids a Drive
            # trash copy retaining every expired pack. No account-wide trash API.
            if key not in self.pending:
                rows = self.rows(self.command('list', self.root + '/' + kind))
                if name not in rows:
                    self.catalogue[kind].pop(name, None)
                    return
                self.pending[key] = rows[name]['uid']
                self.pending_path.write_text(json.dumps(self.pending))
            # A previous process may have stopped after journalling the UID but
            # before moving the object. Retry only the same recorded object.
            active = self.rows(self.command('list', self.root + '/' + kind))
            if name in active:
                if active[name]['uid'] != self.pending[key]:
                    raise RuntimeError('Repository object identity changed; deletion refused')
                self.command('trash', key)
                remaining = self.rows(self.command('list', self.root + '/' + kind))
                if name in remaining:
                    raise RuntimeError('Repository object still active after trash operation')
            # CLI 0.9 resolves trash paths by decrypted name, even though its
            # help mentions UIDs. Verify a unique exact UID before name lookup.
            matches = [r for r in self.command('list', '/trash')
                       if r.get('name', {}).get('value') == name]
            if matches:
                if len(matches) != 1 or matches[0]['uid'] != self.pending[key]:
                    raise RuntimeError('Ambiguous trash entry; permanent deletion refused')
                self.command('delete', '/trash/' + name)
            self.pending.pop(key)
            self.pending_path.write_text(json.dumps(self.pending))
            self.catalogue[kind].pop(name, None)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, code, data=b'', content_type='application/octet-stream', extra=None):
        self.send_response(code)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Content-Type', content_type)
        for k, v in (extra or {}).items():
            self.send_header(k, str(v))
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(data)

    def request(self):
        try:
            supplied = self.headers.get('Authorization', '')
            if not hmac.compare_digest(supplied, self.server.authorization):
                return self.reply(401)
            path = urlsplit(self.path).path.strip('/')
            if not path:
                if self.command == 'POST':
                    self.server.drive.folders()
                    return self.reply(200)
                return self.reply(400)
            parts = path.split('/')
            if path == 'config':
                kind, name = '', 'config'
            elif len(parts) == 1 and parts[0] in KINDS:
                if self.command != 'GET':
                    return self.reply(405)
                with self.server.drive.lock:
                    items = self.server.drive.listing(parts[0])
                    v2 = self.headers.get('Accept') == 'application/vnd.x.restic.rest.v2'
                    data = [{'name': n, 'size': s} for n, s in items.items()] if v2 else list(items)
                    return self.reply(200, json.dumps(data).encode(),
                                      'application/vnd.x.restic.rest.v2' if v2 else 'application/json')
            elif len(parts) == 2 and parts[0] in KINDS and re.fullmatch('[a-f0-9]{64}', parts[1]):
                kind, name = parts
            else:
                return self.reply(400)
            with self.server.drive.lock:
                if self.command == 'HEAD':
                    size = self.server.drive.listing(kind).get(name)
                    if size is None:
                        return self.reply(404)
                    self.send_response(200)
                    self.send_header('Content-Length', str(size))
                    self.end_headers()
                elif self.command == 'GET':
                    data = self.server.drive.read(kind, name)
                    match = re.fullmatch(r'bytes=(\d+)-(\d*)', self.headers.get('Range', ''))
                    if match:
                        start = int(match[1])
                        end = int(match[2]) if match[2] else len(data) - 1
                        if start >= len(data) or end < start or end >= len(data):
                            return self.reply(416)
                        return self.reply(206, data[start:end + 1], extra={
                            'Content-Range': f'bytes {start}-{end}/{len(data)}'})
                    return self.reply(200, data)
                elif self.command == 'POST':
                    size = int(self.headers.get('Content-Length', '-1'))
                    if not 0 <= size <= 128 * 1024**2:
                        return self.reply(413)
                    data = self.rfile.read(size)
                    if len(data) != size:
                        return self.reply(400)
                    self.server.drive.write(kind, name, data)
                    return self.reply(200)
                elif self.command == 'DELETE' and kind and (kind == 'locks' or self.server.allow_delete):
                    self.server.drive.delete(kind, name)
                    return self.reply(200)
                else:
                    return self.reply(405)
        except FileNotFoundError:
            self.reply(404)
        except (ValueError, RuntimeError, KeyError, OSError, subprocess.SubprocessError):
            self.reply(500, b'Repository operation failed')

    do_GET = do_HEAD = do_POST = do_DELETE = request


def serve(drive, token, allow_delete=False):
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.drive = drive
    server.authorization = 'Basic ' + base64.b64encode(('weasel:' + token).encode()).decode()
    server.allow_delete = allow_delete
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cli', required=True)
    p.add_argument('--root', required=True)
    p.add_argument('--spool', required=True)
    args = p.parse_args()
    raise SystemExit('Start through weasel-cloud-backup; transport has no unauthenticated standalone mode')
