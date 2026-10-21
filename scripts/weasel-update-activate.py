#!/usr/bin/env python3
"""Trusted root transaction for policy-scoped, signed laptop source updates.

Only this installed script and its immutable sibling verifiers execute as root.
Nix evaluation/builds and application probes always run as the desktop user.
The inbox supplies object IDs, never commands, verifier paths or source text.
"""
from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import errno
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid

USER = "evilweasel"
HOST = "nixy-laptop"
REPOSITORY = Path("/home/evilweasel/weasel-os")
INBOX = Path("/var/lib/weasel-updates-inbox/request.json")
STATE = Path("/var/lib/weasel-updates")
STATUS = Path("/var/lib/weasel-updates-status/status.json")
BACKUPS = Path("/home/.weasel-update-transactions")
CURRENT = Path("/run/current-system")
PROFILE = Path("/nix/var/nix/profiles/system")
GIB = 1024 ** 3
LANES = {
    "packages/t3code/source.json": "t3",
    "packages/codex-source/default.nix": "codex",
    "packages/chatgpt/default.nix": "chatgpt",
}
REQUEST_KEYS = {"schema", "id", "baseline_commit", "baseline_system", "candidate_commit", "candidate_ref"}
BATCH_REQUEST_KEYS = REQUEST_KEYS | {"mode"}
BATCH_MODES = {"batch", "release-migration"}
BATCH_HOSTS = [HOST, "nixy-desktop", "michapc", "michapc-debug"]
ORIGIN_URL = "https://github.com/morrowbyte/weasel-os.git"
OWNER_TAG = "weasel-daily-updates-v1"
EXPECTED_PRIMARY_FINGERPRINT = "7D184861D38A4EA986C541FC9B2586DEDAFE5BEF"
TERMINAL_PHASES = {"verified-only", "blocked", "failed-before-integration", "complete"}


class ActivationError(RuntimeError):
    pass


class RecoveryRequired(ActivationError):
    pass


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_object(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ActivationError("Duplicate JSON key")
            result[key] = value
        return result
    try:
        return json.loads(data, object_pairs_hook=pairs)
    except (ValueError, UnicodeError) as exc:
        raise ActivationError("Invalid JSON") from exc


def valid_system(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"/nix/store/[0-9a-z]{32}-nixos-system-nixy-laptop-[A-Za-z0-9.+_-]+", value
    ):
        raise ActivationError("Invalid laptop system path")
    return Path(value)


def parse_request(data):
    if len(data) > 65536:
        raise ActivationError("Request exceeds 64 KiB")
    record = json_object(data)
    if not isinstance(record, dict) or type(record.get("schema")) is not int:
        raise ActivationError("Unexpected request schema")
    if record["schema"] == 1:
        valid_keys = REQUEST_KEYS
    elif record["schema"] == 2:
        valid_keys = BATCH_REQUEST_KEYS
        if not isinstance(record.get("mode"), str) or record["mode"] not in BATCH_MODES:
            raise ActivationError("Unexpected batch update mode")
    else:
        raise ActivationError("Unexpected request schema")
    if set(record) != valid_keys:
        raise ActivationError("Unexpected request schema")
    if not isinstance(record["id"], str) or not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}", record["id"]):
        raise ActivationError("Invalid request identifier")
    for key in ["baseline_commit", "candidate_commit"]:
        if not isinstance(record[key], str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", record[key]):
            raise ActivationError("Commit must be a complete hexadecimal object ID")
    if record["baseline_commit"] == record["candidate_commit"]:
        raise ActivationError("Candidate equals baseline")
    if record["candidate_ref"] != "refs/heads/weasel-update-" + record["id"]:
        raise ActivationError("Candidate reference is outside the update namespace")
    valid_system(record["baseline_system"])
    return record



def open_parent_no_follow(path):
    path = Path(path)
    if not path.is_absolute():
        path = Path.cwd() / path
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in path.parent.parts[1:]:
            if component in {"", ".", ".."}:
                raise ActivationError("Unsafe directory component")
            next_descriptor = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def read_regular(path, maximum=65536, expected_uid=None, *, allow_hardlinks=False):
    """Do not follow links; return bytes and identity from the open descriptor."""
    parent_fd = open_parent_no_follow(path)
    try:
        fd = os.open(Path(path).name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent_fd)
    finally:
        os.close(parent_fd)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or (info.st_nlink != 1 and not allow_hardlinks):
            raise ActivationError("Expected a regular file with one link")
        if expected_uid is not None and info.st_uid != expected_uid:
            raise ActivationError("Unexpected file owner")
        if info.st_size > maximum:
            raise ActivationError("File exceeds its permitted size")
        chunks = []
        total = 0
        while True:
            part = os.read(fd, min(65536, maximum + 1 - total))
            if not part:
                break
            chunks.append(part)
            total += len(part)
            if total > maximum:
                raise ActivationError("File grew beyond its permitted size")
        after = os.fstat(fd)
        if (info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ActivationError("File changed while being read")
        return b"".join(chunks), after
    finally:
        os.close(fd)


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write(path, data, mode=0o600):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def rename_no_replace(source, destination):
    """Retain displaced inodes, using descriptor-bound parents without symlinks."""
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        rename = libc.renameat2
    except AttributeError as exc:
        raise ActivationError("renameat2 is required; no unsafe fallback") from exc
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    source_fd = open_parent_no_follow(source)
    try:
        destination_fd = open_parent_no_follow(destination)
        try:
            if rename(source_fd, os.fsencode(Path(source).name), destination_fd, os.fsencode(Path(destination).name), 1):
                number = ctypes.get_errno()
                raise OSError(number, os.strerror(number), os.fspath(destination))
            os.fsync(source_fd)
            os.fsync(destination_fd)
        finally:
            os.close(destination_fd)
    finally:
        os.close(source_fd)


def ensure_directory(path, mode=0o700, owner=0):
    path = Path(path)
    try:
        path.mkdir(mode=mode)
    except FileExistsError:
        pass
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != owner or stat.S_IMODE(info.st_mode) != mode:
        raise ActivationError("Unsafe transaction directory permissions")
    return path


def import_peer(name):
    path = Path(__file__).resolve().with_name(name)
    module_name = name.replace("-", "_").replace(".", "_")
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class Runner:
    def __init__(self, user=USER):
        self.account = pwd.getpwnam(user)
        self.paths = {}
        self.user_path = os.environ.get("PATH", "/run/current-system/sw/bin")

    def executable(self, name):
        if name not in self.paths:
            found = shutil.which(name)
            if not found:
                raise ActivationError("Required executable is not from the Nix store: " + name)
            # Preserve argv[0] for Nix/coreutils multicall symlinks such as
            # nix-store and env, while pinning their containing directory.
            pinned = Path(found).parent.resolve() / Path(found).name
            if (not str(pinned).startswith("/nix/store/")
                    or not str(pinned.resolve()).startswith("/nix/store/")):
                raise ActivationError("Required executable is not from the Nix store: " + name)
            self.paths[name] = str(pinned)
        return self.paths[name]

    def user(self, name, arguments, *, cwd=None, extra_env=None, timeout=10800):
        env = {
            "HOME": self.account.pw_dir, "USER": self.account.pw_name,
            "LOGNAME": self.account.pw_name, "PATH": self.user_path,
            "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0", "GIT_NO_REPLACE_OBJECTS": "1",
        }
        if extra_env:
            env.update(extra_env)
        argv = [self.executable("runuser"), "-u", self.account.pw_name, "--", self.executable("env"), "-i"]
        argv += [key + "=" + value for key, value in env.items()]
        argv += [self.executable(name)] + [str(a) for a in arguments]
        return self.run(argv, cwd=cwd, timeout=timeout)

    def root(self, name, arguments, *, timeout=600):
        return self.run([self.executable(name)] + [str(a) for a in arguments], timeout=timeout)

    @staticmethod
    def run(argv, cwd=None, timeout=600):
        require_switch_checks()
        result = subprocess.run(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=False)
        if result.returncode:
            # Neither arbitrary subprocess output nor private profile contents enter status logs.
            raise ActivationError("Trusted command failed: " + Path(argv[0]).name + " (exit " + str(result.returncode) + ")")
        return result.stdout

    def git(self, repository, *args, extra_env=None):
        return self.user("git", ["--no-replace-objects", "-c", "core.hooksPath=/dev/null",
                                  "-c", "core.fsmonitor=false", "-c", "gpg.format=openpgp", "-c",
                                  "gpg.program=" + self.executable("gpg"), "-C", repository, *args], extra_env=extra_env, timeout=120)


def read_object(runner, repository, kind, object_id):
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", object_id):
        raise ActivationError("Invalid Git object ID")
    contents = runner.git(repository, "cat-file", kind, object_id)
    if len(contents) > 128 * 1024 * 1024:
        raise ActivationError("Source object exceeds its permitted size")
    algorithm = hashlib.sha1 if len(object_id) == 40 else hashlib.sha256
    actual = algorithm(kind.encode() + b" " + str(len(contents)).encode() + b"\0" + contents).hexdigest()
    if actual != object_id:
        raise ActivationError("Git object bytes do not match their signed content address")
    return contents


def tracked_tree(runner, repository, commit):
    """Resolve every path from cryptographically checked commit/tree objects."""
    raw_commit = read_object(runner, repository, "commit", commit)
    trees = [line.removeprefix(b"tree ").decode("ascii") for line in raw_commit.split(b"\n\n", 1)[0].splitlines()
             if line.startswith(b"tree ")]
    if len(trees) != 1:
        raise ActivationError("Commit does not have exactly one source tree")
    result = {}
    stack = [(trees[0], "")]
    object_size = len(commit) // 2
    visited = 0
    while stack:
        tree_id, prefix = stack.pop()
        data = read_object(runner, repository, "tree", tree_id)
        offset = 0
        visited += 1
        if visited > 10000:
            raise ActivationError("Source tree exceeds its permitted entry bound")
        while offset < len(data):
            try:
                separator = data.index(b" ", offset)
                terminator = data.index(b"\0", separator + 1)
            except ValueError as exc:
                raise ActivationError("Malformed Git source tree") from exc
            mode = data[offset:separator].decode("ascii")
            component = os.fsdecode(data[separator + 1:terminator])
            object_bytes = data[terminator + 1:terminator + 1 + object_size]
            if len(object_bytes) != object_size or not component or "/" in component or component in {".", "..", ".git"}:
                raise ActivationError("Unsafe tracked source component")
            object_id = object_bytes.hex()
            offset = terminator + 1 + object_size
            name = prefix + component
            if mode in {"40000", "040000"}:
                stack.append((object_id, name + "/"))
            elif mode in {"100644", "100755"}:
                if name in result:
                    raise ActivationError("Duplicate tracked filename")
                result[name] = (mode, object_id)
            else:
                raise ActivationError("Unsupported tracked source type")
    return result


def read_blob(runner, repository, object_id):
    return read_object(runner, repository, "blob", object_id)


def repository_manifest(runner, repository, commit):
    if runner.git(repository, "symbolic-ref", "-q", "HEAD").decode().strip() != "refs/heads/main":
        raise ActivationError("Default checkout is not on main")
    if runner.git(repository, "rev-parse", "HEAD").decode().strip() != commit:
        raise ActivationError("Main HEAD changed")
    if runner.git(repository, "status", "--porcelain=v1", "-z", "--untracked-files=normal"):
        raise ActivationError("Main working tree or index is dirty")
    git_directory = Path(runner.git(repository, "rev-parse", "--absolute-git-dir").decode().strip())
    if git_directory != repository / ".git" or not git_directory.is_dir() or git_directory.is_symlink():
        raise ActivationError("Updater requires the normal main checkout")
    files = {}
    for name, (mode, object_id) in tracked_tree(runner, repository, commit).items():
        target = repository / name
        for parent in target.parents:
            if parent == repository:
                break
            if parent.is_symlink():
                raise ActivationError("Tracked parent is a symlink")
        contents, info = read_regular(target, maximum=128 * 1024 * 1024)
        expected = read_blob(runner, repository, object_id)
        if contents != expected or bool(info.st_mode & 0o111) != (mode == "100755"):
            raise ActivationError("Tracked file differs from its commit")
        files[name] = {"sha256": digest(contents), "mode": stat.S_IMODE(info.st_mode),
                       "uid": info.st_uid, "gid": info.st_gid, "dev": info.st_dev, "ino": info.st_ino}
    index, index_info = read_regular(git_directory / "index", maximum=128 * 1024 * 1024)
    return {"commit": commit, "files": files, "index_sha256": digest(index),
            "index_identity": [index_info.st_dev, index_info.st_ino]}, index


def check_system(baseline):
    if CURRENT.resolve() != baseline or PROFILE.resolve() != baseline:
        raise ActivationError("Active generation or boot-default profile changed")
    if not (baseline / "bin/switch-to-configuration").is_file():
        raise ActivationError("Baseline is not an existing NixOS system")




def check_remote(runner, repository, expected_commit):
    for options in [("--all",), ("--push", "--all")]:
        urls = runner.git(repository, "remote", "get-url", *options, "origin").decode().splitlines()
        if urls != [ORIGIN_URL]:
            raise ActivationError("Origin fetch/push URL differs from the authorized repository")
    rows = runner.git(repository, "ls-remote", "--heads", "origin", "main").decode().splitlines()
    if len(rows) != 1 or rows[0].split() != [expected_commit, "refs/heads/main"]:
        raise ActivationError("Remote main advanced; review the new baseline before activation")


def check_other_activation(proc_root=Path("/proc")):
    for process in proc_root.iterdir():
        if not process.name.isdecimal() or int(process.name) == os.getpid():
            continue
        try:
            arguments = (process / "cmdline").read_bytes().split(b"\0")
        except (FileNotFoundError, ProcessLookupError):
            continue
        except PermissionError as exc:
            raise ActivationError("Cannot inspect competing activation processes") from exc
        tools = [os.path.basename(os.fsdecode(value)) for value in arguments if value]
        if any(name in {"nixos-rebuild", "nixos-rebuild-ng", "switch-to-configuration", "nh"} for name in tools):
            if any(value in {b"switch", b"boot", b"test", b"dry-activate"} for value in arguments):
                raise ActivationError("Another system activation is in progress")


def check_power(sys_root=Path("/sys/class/power_supply")):
    online = []
    batteries = []
    for path in sys_root.iterdir():
        kind = (path / "type").read_text().strip()
        if kind in {"Mains", "USB", "USB_C"} and (path / "online").exists():
            online.append((path / "online").read_text().strip() == "1")
        if kind == "Battery":
            batteries.append(int((path / "capacity").read_text().strip()))
    if not online or not any(online) or not batteries or min(batteries) < 30:
        raise ActivationError("Update needs external power and at least 30 percent battery")


def check_space(minimum, paths=(Path("/home"), Path("/nix/store"), Path("/var/lib"))):
    for path in paths:
        if shutil.disk_usage(path).free < minimum:
            raise ActivationError("Insufficient filesystem reserve")


def check_recovery(state, *, read_only=False, bootstrap_resume=None):
    # Only the explicit root bootstrap resume operation can review its own
    # exact failed journal. Ordinary updates retain the unconditional fence.
    if bootstrap_resume is not None:
        if (os.geteuid() != 0 or not isinstance(bootstrap_resume, tuple)
                or len(bootstrap_resume) != 2
                or not all(isinstance(value, str) for value in bootstrap_resume)
                or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", bootstrap_resume[0])
                or not re.fullmatch(r"[0-9a-f]{64}", bootstrap_resume[1])):
            raise ActivationError("Bootstrap resume requires root and an exact journal digest")
    resumed = False
    bootstrap = state / "bootstrap"
    if bootstrap.exists():
        if bootstrap.is_symlink() or not bootstrap.is_dir():
            raise ActivationError("Unsafe bootstrap state directory")
        for path in bootstrap.iterdir():
            if path.is_symlink() or not path.is_dir() or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", path.name):
                raise ActivationError("Unexpected bootstrap transaction entry")
            journal = path / "journal.json"
            if not journal.exists():
                raise RecoveryRequired("An interrupted bootstrap requires recovery review")
            data, _ = read_regular(journal, maximum=8 * 1024 * 1024,
                                   expected_uid=0 if bootstrap_resume is not None else None)
            record = json_object(data)
            if record.get("marker") != "weasel-daily-bootstrap-v1" or record.get("commit") != path.name:
                raise ActivationError("Bootstrap recovery marker mismatch")
            if record.get("phase") not in {"complete", "failed-before-integration"}:
                if (bootstrap_resume == (path.name, digest(data))
                        and record.get("phase") == "recovery-required"
                        and record.get("system_rollback") == "complete"
                        and record.get("home_rollback") is False):
                    resumed = True
                else:
                    raise RecoveryRequired("An earlier bootstrap requires recovery review")
    if bootstrap_resume is not None and not resumed:
        raise RecoveryRequired("The exact rolled-back bootstrap journal is no longer pending")
    runs = state / "runs"
    if not runs.exists():
        return
    for path in runs.iterdir():
        if path.is_symlink() or not path.is_dir():
            raise ActivationError("Unexpected transaction state entry")
        journal = path / "journal.json"
        if not journal.exists():
            raise RecoveryRequired("An interrupted update or cleanup requires recovery review: " + path.name)
        data, _ = read_regular(journal, maximum=8 * 1024 * 1024)
        record = json_object(data)
        if record.get("phase") not in TERMINAL_PHASES:
            raise RecoveryRequired("An earlier update requires recovery review: " + path.name)
        if record.get("marker") == OWNER_TAG and record.get("phase") == "complete":
            retained = record.get("retained_originals", {})
            backup = BACKUPS / path.name
            for name, expected in retained.items():
                data, _ = read_regular(backup / name, maximum=128 * 1024 * 1024)
                if digest(data) != expected:
                    record.update(phase="recovery-required", reason="Late editor save retained in original inode")
                    if not read_only:
                        atomic_write(journal, encoded(record))
                    raise RecoveryRequired("An earlier original contains a late editor save; recovery required")


def consume_request(request_path, archive, account_uid):
    """Claim a request atomically; preserve a concurrently replaced inbox file."""
    original, info = read_regular(request_path, expected_uid=account_uid)
    rename_no_replace(request_path, archive)
    captured, displaced = read_regular(archive, expected_uid=account_uid)
    if (info.st_dev, info.st_ino) != (displaced.st_dev, displaced.st_ino) or captured != original:
        atomic_write(archive.with_suffix(".original.json"), original)
        try:
            rename_no_replace(archive, request_path)
        except FileExistsError:
            pass  # The newer request and captured raced request both remain preserved.
        raise ActivationError("Inbox changed while being claimed")
    os.chmod(archive, 0o600)
    os.chown(archive, 0, 0)
    atomic_write(archive, captured)
    return captured


def archive_source(runner, repository, commit, destination):
    destination.mkdir(mode=0o755)
    for name, (mode, object_id) in tracked_tree(runner, repository, commit).items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        target.write_bytes(read_blob(runner, repository, object_id))
        target.chmod(0o555 if mode == "100755" else 0o444)
    for path in sorted(destination.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.is_dir():
            path.chmod(0o555)
    destination.chmod(0o555)


def app_attribute(kind):
    if kind == "t3":
        return "packages.x86_64-linux.t3code"
    if kind == "codex":
        return "nixosConfigurations.nixy-laptop.config.home-manager.users.evilweasel.weasel.hephaestusRecoveryConsole.package"
    if kind == "chatgpt":
        return None
    raise ActivationError("Unknown application kind")


def app_expression(source):
    # source is constructed by this helper below the root-owned transaction directory.
    return ('let f = builtins.getFlake "path:' + str(source) + '"; in builtins.head '
            '(builtins.filter (p: (p.pname or "") == "chatgpt") '
            'f.nixosConfigurations.nixy-laptop.config.environment.systemPackages)')


def nix_target(source, attribute, expression=None):
    if expression is not None:
        return ["--impure", "--expr", expression]
    return ["path:" + str(source) + "#" + attribute]


def nix_eval(runner, source, attribute, expression=None):
    arguments = ["eval", "--no-write-lock-file", "--raw"]
    arguments += nix_target(source, attribute, expression)
    return runner.user("nix", arguments).decode().strip()


def nix_build(runner, source, attribute, expression=None):
    lock = (source / "flake.lock").read_bytes()
    arguments = ["build", "--no-write-lock-file", "--no-link", "--print-out-paths", "--max-jobs", "1", "--cores", "2"]
    arguments += nix_target(source, attribute, expression)
    output = runner.user("nix", arguments).decode().splitlines()
    if len(output) != 1 or not re.fullmatch(r"/nix/store/[0-9a-z]{32}-[^/]+", output[0]):
        raise ActivationError("Build did not produce exactly one store path")
    if (source / "flake.lock").read_bytes() != lock:
        raise ActivationError("Build changed frozen flake.lock")
    return Path(output[0])


def build_application(runner, source, kind):
    return nix_build(runner, source, app_attribute(kind), app_expression(source) if kind == "chatgpt" else None)


def acp_expression(source):
    return ('let f = builtins.getFlake "path:' + str(source) + '"; in builtins.head '
            '(builtins.filter (p: (p.pname or "") == "codex-acp") '
            'f.nixosConfigurations.nixy-laptop.config.home-manager.users.evilweasel.home.packages)')


def nix_check(runner, source):
    """Build the flake's native checks without changing the archived lock."""
    lock = (source / "flake.lock").read_bytes()
    runner.user("nix", ["flake", "check", "--no-write-lock-file", "--max-jobs", "1", "--cores", "2", "path:" + str(source)])
    if (source / "flake.lock").read_bytes() != lock:
        raise ActivationError("Flake check changed frozen flake.lock")


def validate_candidate_changes(runner, repository, request, old_tree, new_tree, discover=None, batch=None):
    """Reconstruct the exact signed diff and apply only immutable policy code."""
    if old_tree.keys() != new_tree.keys():
        raise ActivationError("Candidate adds or removes tracked source files")
    changed_names = sorted(name for name in old_tree if old_tree[name] != new_tree[name])
    if not changed_names:
        raise ActivationError("Candidate has no source changes")
    changes = {}
    for name in changed_names:
        if old_tree[name][0] != new_tree[name][0]:
            raise ActivationError("Candidate source mode changed")
        changes[name] = (read_blob(runner, repository, old_tree[name][1]),
                         read_blob(runner, repository, new_tree[name][1]))
    if "agent-learnings.md" not in changes:
        raise ActivationError("Candidate requires its deterministic learning entry")
    old_log, new_log = changes["agent-learnings.md"]
    if request["schema"] == 2:
        if batch is None:
            raise ActivationError("Installed batch policy is unavailable")
        source_names = [name for name in changed_names if name != "agent-learnings.md"]
        if not source_names:
            raise ActivationError("Batch candidate has no package or input changes")
        suffix = batch.learning_suffix(request["id"], request["mode"], source_names)
        if new_log != old_log + suffix:
            raise ActivationError("Learning entry differs from the independently generated batch suffix")
        return {"batch": batch.validate_changes(changes, request["mode"], request["id"])}, changes
    if any(name not in {*LANES, "agent-learnings.md"} for name in changed_names):
        raise ActivationError("Candidate diff exceeds the package pin allowlist")
    metadata = {}
    for name, contents in changes.items():
        if name != "agent-learnings.md":
            metadata[LANES[name]] = discover.validate_transition(LANES[name], *contents, network=True)
    if not metadata:
        raise ActivationError("Package candidate has no pin changes")
    suffix = b"".join(discover.learning_suffix(request["id"], kind, pin["old"]["version"], pin["new"]["version"])
                      for kind, pin in sorted(metadata.items()))
    if new_log != old_log + suffix:
        raise ActivationError("Learning entry differs from the independently generated suffix")
    return metadata, changes


def probe_applications(runner, old_source, new_source, kinds, workspace, run, *, changed_outputs_only=False):
    """Rebuild known apps and ACP independently, then exercise changed outputs."""
    app_pairs, app_tests = {}, {}
    for kind in kinds:
        old_app = build_application(runner, old_source, kind)
        new_app = build_application(runner, new_source, kind)
        app_pairs[kind] = {"old": str(old_app), "new": str(new_app)}
        changed = old_app != new_app
        acp_app = None
        profile_ownership = None
        if kind == "codex":
            acp_app = nix_build(runner, new_source, None, acp_expression(new_source))
            profile = nix_build(runner, new_source,
                                "nixosConfigurations.nixy-laptop.config.home-manager.users.evilweasel.home.path")
            profile_ownership = import_peer("weasel-update-gates.py").verify_codex_profile(profile, new_app, acp_app)
            if changed_outputs_only:
                old_acp = nix_build(runner, old_source, None, acp_expression(old_source))
                app_pairs["codex-acp"] = {"old": str(old_acp), "new": str(acp_app)}
                changed = changed or old_acp != acp_app
        app_tests[kind] = {"output_changed": changed, "probed": False, "profile_ownership": profile_ownership}
        if changed_outputs_only and not changed:
            continue
        probe_dir = workspace / ("probe-" + kind)
        probe_dir.mkdir(mode=0o700)
        os.chown(probe_dir, runner.account.pw_uid, runner.account.pw_gid)
        arguments = [Path(__file__).resolve().with_name("weasel-update-gates.py"),
                     "--kind", kind, "--app", new_app, "--run-dir", probe_dir]
        if kind == "t3":
            arguments += ["--old-app", old_app]
        if kind == "codex":
            arguments += ["--acp-app", acp_app]
        receipt = json_object(runner.user("python3", arguments))
        if receipt.get("ok") is not True:
            raise ActivationError("Trusted application probe did not pass")
        atomic_write(run / ("probe-" + kind + ".json"), encoded(receipt))
        app_tests[kind]["probed"] = True
    return app_pairs, app_tests


def published_batch_pins(runner, old_source, new_source):
    output = runner.user("python3", [Path(__file__).resolve().with_name("weasel-update-batch.py"),
                                    "--verify-published", "--baseline", old_source, "--source", new_source])
    receipt = json_object(output)
    if not isinstance(receipt, dict) or receipt.get("ok") is not True:
        raise ActivationError("Published critical pin verification did not pass")
    return receipt


def probe_niri(runner, source, workspace):
    output = runner.user("python3", [Path(__file__).resolve().with_name("weasel-update-niri.py"),
                                    "--source", source, "--run-dir", workspace / "niri"])
    receipt = json_object(output)
    if not isinstance(receipt, dict) or receipt.get("ok") is not True:
        raise ActivationError("Generated Niri configuration probe did not pass")
    return receipt


def create_snapshot(runner, identifier):
    check_space(50 * GIB)
    mount = json_object(runner.root("findmnt", ["--json", "--target", "/home", "--output", "TARGET,FSTYPE,OPTIONS"]))
    entries = mount.get("filesystems", [])
    if len(entries) != 1 or entries[0].get("target") != "/home" or entries[0].get("fstype") != "btrfs":
        raise ActivationError("Home must be its own Btrfs mount")
    runner.root("btrfs", ["subvolume", "show", "/home/.snapshots"])
    number = runner.root("snapper", ["-c", "home", "create", "--type", "single", "--print-number",
                                    "--description", "Verified daily package update " + identifier,
                                    "--userdata", "weasel-daily-update=yes,weasel-update-id=" + identifier]).decode().strip()
    if not re.fullmatch(r"[1-9][0-9]*", number):
        raise ActivationError("Snapper did not report a checkpoint number")
    snapshot = Path("/home/.snapshots") / number / "snapshot"
    runner.root("btrfs", ["subvolume", "show", snapshot])
    if runner.root("btrfs", ["property", "get", "-ts", snapshot, "ro"]).decode().strip() != "ro=true":
        raise ActivationError("Checkpoint is not read-only")
    return int(number)


def replace_preserving_inode(path, expected, replacement, retained, *, uid, gid, mode):
    """No existing file is overwritten or unlinked; save races remain recoverable."""
    before, _ = read_regular(path, maximum=128 * 1024 * 1024)
    if before != expected:
        raise ActivationError("Source changed before integration")
    staged = retained.with_name(retained.name + ".candidate")
    with open(staged, "xb") as stream:
        os.fchmod(stream.fileno(), mode)
        os.fchown(stream.fileno(), uid, gid)
        stream.write(replacement)
        stream.flush()
        os.fsync(stream.fileno())
    sync_directory(staged.parent)
    rename_no_replace(path, retained)
    old, _ = read_regular(retained, maximum=128 * 1024 * 1024)
    if old != expected:
        raise ActivationError("Concurrent editor save retained; recovery required")
    try:
        rename_no_replace(staged, path)
    except FileExistsError as exc:
        raise ActivationError("Concurrent replacement retained; recovery required") from exc
    if read_regular(path, maximum=128 * 1024 * 1024)[0] != replacement or read_regular(retained, maximum=128 * 1024 * 1024)[0] != expected:
        raise ActivationError("Concurrent editor save retained; recovery required")


def integrate_source(runner, repository, request, before_manifest, before_index, changes, backup, workspace, journal):
    current, current_index = repository_manifest(runner, repository, request["baseline_commit"])
    if current != before_manifest or current_index != before_index:
        raise ActivationError("Source or index changed before integration")
    index_path = repository / ".git/index"
    lock_path = repository / ".git/index.lock"
    # Cooperating Git commands cannot stage/checkout while the transaction holds this lock.
    git_parent_fd = open_parent_no_follow(lock_path)
    try:
        lock_fd = os.open(lock_path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=git_parent_fd)
    finally:
        os.close(git_parent_fd)
    lock_info = os.fstat(lock_fd)
    lock_identity = (lock_info.st_dev, lock_info.st_ino)
    try:
        prepared_index = workspace / "candidate-index"
        runner.git(repository, "read-tree", request["candidate_commit"], extra_env={"GIT_INDEX_FILE": str(prepared_index)})
        candidate_index, _ = read_regular(prepared_index, maximum=128 * 1024 * 1024, expected_uid=runner.account.pw_uid)
        os.fchown(lock_fd, runner.account.pw_uid, runner.account.pw_gid)
        with os.fdopen(os.dup(lock_fd), "wb") as index_stream:
            index_stream.write(candidate_index)
            index_stream.flush()
        os.fsync(lock_fd)
        journal("integrating", backup_directory=str(backup),
                retained_originals={name.replace("/", "__"): digest(old) for name, (old, _new) in changes.items()},
                original_index_sha256=digest(before_index))
        for name, (old_bytes, new_bytes) in changes.items():
            entry = before_manifest["files"][name]
            retained = backup / name.replace("/", "__")
            replace_preserving_inode(repository / name, old_bytes, new_bytes, retained,
                                     uid=entry["uid"], gid=entry["gid"], mode=entry["mode"])
        # Check original contents through displaced inodes, including still-open editor FDs.
        for name, (old_bytes, _new_bytes) in changes.items():
            if read_regular(backup / name.replace("/", "__"), maximum=128 * 1024 * 1024)[0] != old_bytes:
                raise ActivationError("Save through displaced source inode; recovery required")
        if read_regular(index_path, maximum=128 * 1024 * 1024)[0] != before_index:
            raise ActivationError("Index changed despite lock; recovery required")
        rename_no_replace(index_path, backup / "index-original")
        os.close(lock_fd)
        lock_fd = -1
        rename_no_replace(lock_path, index_path)
        runner.git(repository, "update-ref", "-m", "verified daily package update", "refs/heads/main",
                   request["candidate_commit"], request["baseline_commit"])
        after, _ = repository_manifest(runner, repository, request["candidate_commit"])
        for name, (old_bytes, _new_bytes) in changes.items():
            if read_regular(backup / name.replace("/", "__"), maximum=128 * 1024 * 1024)[0] != old_bytes:
                raise ActivationError("Save through displaced source inode; recovery required")
        journal("source-integrated", source_after=after)
        return after
    finally:
        if lock_fd >= 0:
            os.close(lock_fd)
        # Only remove our still-existing lock inode; never somebody else's newer lock.
        if lock_path.exists() and (lock_path.lstat().st_dev, lock_path.lstat().st_ino) == lock_identity:
            # On failure preserve our lock in the root-only backup for recovery.
            try:
                rename_no_replace(lock_path, backup / "index-lock-retained")
            except FileExistsError:
                pass


def write_status(outcome, identifier, phase, **fields):
    changed = True
    if STATUS.parent.is_dir():
        if STATUS.exists() and outcome == "blocked":
            previous = json_object(read_regular(STATUS)[0])
            changed = (previous.get("outcome"), previous.get("phase"), previous.get("reason")) != (outcome, phase, fields.get("reason"))
        atomic_write(STATUS, encoded({"schema": 1, "id": identifier, "outcome": outcome,
                                     "phase": phase, "at": dt.datetime.now(dt.timezone.utc).isoformat(), **fields}), mode=0o644)
    return changed



def retained_has_open_fds(backup):
    identities = set()
    for path in backup.iterdir():
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise ActivationError("Unexpected retained artifact type")
        identities.add((info.st_dev, info.st_ino))
    processes = {process.name: process for process in Path("/proc").iterdir() if process.name.isdecimal()}
    for process in processes.values():
        try:
            descriptors = list((process / "fd").iterdir())
        except FileNotFoundError:
            continue
        except PermissionError:
            return True  # Uninspectable holders conservatively prohibit pruning.
        for descriptor in descriptors:
            try:
                info = descriptor.stat()
            except FileNotFoundError:
                continue
            except PermissionError:
                return True
            if (info.st_dev, info.st_ino) in identities:
                return True
    after = {process.name for process in Path("/proc").iterdir() if process.name.isdecimal()}
    return after != set(processes)  # Fork/exit during the scan makes its evidence stale.


def safe_remove_tree(path, parent, expected_owner=0):
    if path.parent != parent or not path.name or path.is_symlink():
        raise ActivationError("Unexpected cleanup path")
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != expected_owner:
        raise ActivationError("Unexpected cleanup directory owner")
    if not shutil.rmtree.avoids_symlink_attacks:
        raise ActivationError("A descriptor-safe tree remover is required")
    shutil.rmtree(path)
    sync_directory(parent)


def owned_snapshots(runner):
    payload = json_object(runner.root("snapper", ["--jsonout", "-c", "home", "list", "--columns", "number,userdata"]))
    rows = payload.get("home") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ActivationError("Unexpected Snapper JSON format")
    result = {}
    for row in rows:
        userdata = row.get("userdata") or {}
        if isinstance(userdata, str):
            userdata = dict(pair.strip().split("=", 1) for pair in userdata.split(",") if "=" in pair)
        if not isinstance(userdata, dict):
            raise ActivationError("Unexpected Snapper userdata")
        number = int(row["number"])
        if number > 0 and userdata.get("weasel-daily-update") == "yes":
            result[number] = userdata.get("weasel-update-id")
    return result


def prune_owned(runner, state, keep=3):
    """Bound only proven complete, unchanged, unopened artifacts of this updater."""
    completed = []
    for run in sorted((state / "runs").iterdir()):
        if not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}", run.name) or run.is_symlink() or not run.is_dir():
            raise ActivationError("Unexpected updater retention entry")
        journal = run / "journal.json"
        data, _ = read_regular(journal, maximum=8 * 1024 * 1024)
        record = json_object(data)
        if record.get("marker") != OWNER_TAG or record.get("id") != run.name:
            raise ActivationError("Retention marker mismatch")
        if record.get("phase") in {"complete", "failed-before-integration"}:
            completed.append((run, record))
    eligible = completed[:-keep] if keep else completed
    snapshots = owned_snapshots(runner) if eligible else {}
    kept = []
    for run, record in eligible:
        backup = BACKUPS / run.name
        successful = record.get("phase") == "complete"
        if successful and (not backup.is_dir() or backup.is_symlink()):
            raise ActivationError("Missing or unsafe original retention directory")
        expected = dict(record.get("retained_originals", {}))
        if successful:
            expected.update({"index-original": record["original_index_sha256"], "original-index-copy": record["original_index_sha256"]})
        if not successful and backup.exists() and list(backup.iterdir()):
            kept.append(run.name)
            continue
        if not backup.exists():
            backup = None
        if backup is not None and set(path.name for path in backup.iterdir()) != set(expected):
            kept.append(run.name)
            continue
        if backup is not None and any(digest(read_regular(backup / name, maximum=128 * 1024 * 1024)[0]) != sha for name, sha in expected.items()):
            kept.append(run.name)
            continue
        if backup is not None and retained_has_open_fds(backup):
            kept.append(run.name)
            continue
        numbers = [number for number, owner in snapshots.items() if owner == run.name]
        if successful and (not isinstance(record.get("snapshot"), int) or numbers != [record["snapshot"]]):
            # The user may have deleted old snapshots or changed their ownership.
            # Leave this old run untouched; housekeeping is not an update prerequisite.
            kept.append(run.name)
            continue
        # Snapshot deletion and every directory deletion are confined to tagged artifacts.
        # A power loss midway leaves an unresolved durable cleanup transaction.
        record["phase"] = "pruning"
        atomic_write(run / "journal.json", encoded(record))
        for number in numbers:
            runner.root("snapper", ["-c", "home", "delete", "--sync", str(number)])
        if (state / "workers" / run.name).exists():
            safe_remove_tree(state / "workers" / run.name, state / "workers", runner.account.pw_uid)
        if (state / "sources" / run.name).exists():
            safe_remove_tree(state / "sources" / run.name, state / "sources")
        if backup is not None:
            safe_remove_tree(backup, BACKUPS)
        safe_remove_tree(run, state / "runs")
    # Root-captured requests have no project data; retain a bounded audit tail.
    archives = sorted((state / "requests").iterdir())
    for archive in archives[:-32]:
        if re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}\.json", archive.name):
            info = archive.lstat()
            if stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1:
                archive.unlink()
    return kept


def notify_outcome(runner, outcome):
    try:
        runner.user("notify-send", ["--app-name=Weasel Updates", "Daily updates",
                                   "Verified package update completed." if outcome == "updated" else
                                   "Daily update stopped. Read weasel-update --status for the receipt."],
                    extra_env={"XDG_RUNTIME_DIR": "/run/user/" + str(runner.account.pw_uid),
                               "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/" + str(runner.account.pw_uid) + "/bus"}, timeout=15)
    except (ActivationError, OSError, subprocess.TimeoutExpired):
        pass  # A closed graphical session does not invalidate an update receipt.


def require_switch_checks():
    if "NIXOS_NO_CHECK" in os.environ:
        raise ActivationError("NIXOS_NO_CHECK is forbidden in protected activation")


def activate_exact(runner, system):
    require_switch_checks()
    valid_system(str(system))
    runner.root("nix-env", ["--profile", PROFILE, "--set", system])
    # The validated immutable Store program is the only root executable from a candidate.
    runner.run([str(system / "bin/switch-to-configuration"), "switch"], timeout=1200)
    if CURRENT.resolve() != system or PROFILE.resolve() != system:
        raise ActivationError("Native activation did not select the exact tested system")


def process_request(runner, request, state, repository, *, check_only=False):
    require_switch_checks()
    batch_mode = request["schema"] == 2
    discover = None if batch_mode else import_peer("weasel-update-discover.py")
    batch = import_peer("weasel-update-batch.py") if batch_mode else None
    gates = None if batch_mode else import_peer("weasel-update-gates.py")
    baseline = valid_system(request["baseline_system"])
    check_recovery(state, read_only=check_only)
    check_system(baseline)
    check_other_activation()
    check_remote(runner, repository, request["baseline_commit"])
    raw_commit = read_object(runner, repository, "commit", request["candidate_commit"])
    header = raw_commit.split(b"\n\n", 1)[0].splitlines()
    parents = [line.removeprefix(b"parent ").decode("ascii") for line in header if line.startswith(b"parent ")]
    if parents != [request["baseline_commit"]]:
        raise ActivationError("Candidate must have exactly the baseline as its only parent")
    if runner.git(repository, "rev-parse", request["candidate_ref"]).decode().strip() != request["candidate_commit"]:
        raise ActivationError("Candidate ref no longer identifies the requested commit")
    configured_key = runner.git(repository, "config", "--get", "user.signingkey").decode().strip().rstrip("!")
    if configured_key != EXPECTED_PRIMARY_FINGERPRINT:
        raise ActivationError("Configured signing key changed; review required")
    for commit in [request["baseline_commit"], request["candidate_commit"]]:
        runner.git(repository, "verify-commit", "--raw", commit)
        signature = runner.git(repository, "show", "-s", "--format=%G? %GF %GP", commit).decode().split()
        if len(signature) != 3 or signature[0] not in {"G", "U"} or signature[2] != EXPECTED_PRIMARY_FINGERPRINT:
            raise ActivationError("Commit signer does not match the authorized primary key")
    before_manifest, before_index = repository_manifest(runner, repository, request["baseline_commit"])
    old_tree = tracked_tree(runner, repository, request["baseline_commit"])
    new_tree = tracked_tree(runner, repository, request["candidate_commit"])
    metadata, changes = validate_candidate_changes(runner, repository, request, old_tree, new_tree, discover, batch)
    if check_only:
        return {"schema": request["schema"], "ok": True, "id": request["id"], "packages": metadata,
                "checks": ["schema", "signed-direct-candidate", "clean-main", "exact-active-baseline",
                           "batch-source-policy" if batch_mode else "published-pin-diff"],
                "activation_verified": False}
    check_power()
    check_space(65 * GIB)
    # Bounded housekeeping before a new build; incomplete journals already blocked above.
    prune_owned(runner, state, keep=3)
    run = state / "runs" / request["id"]
    run.mkdir(mode=0o700)
    record = {"schema": request["schema"], "marker": OWNER_TAG, "id": request["id"], "phase": "preparing", "request": request,
              "source_before": before_manifest, "packages": metadata}
    def journal(phase, **fields):
        record.update(phase=phase, **fields)
        atomic_write(run / "journal.json", encoded(record))
        write_status("running", request["id"], phase)
    journal("preparing")
    source_root = state / "sources" / request["id"]
    source_root.mkdir(mode=0o711)
    # The service's private umask must not block the normal-user verifier.
    # Only this root-owned source namespace is searchable, never writable.
    source_root.chmod(0o711)
    old_source, new_source = source_root / "baseline", source_root / "candidate"
    workspace = state / "workers" / request["id"]
    workspace.mkdir(mode=0o700)
    os.chown(workspace, runner.account.pw_uid, runner.account.pw_gid)
    try:
        archive_source(runner, repository, request["baseline_commit"], old_source)
        archive_source(runner, repository, request["candidate_commit"], new_source)
        if batch_mode:
            published_pins = published_batch_pins(runner, old_source, new_source)
            journal("preparing", published_pins=published_pins)
        baseline_result = nix_eval(runner, old_source, "nixosConfigurations.nixy-laptop.config.system.build.toplevel.outPath")
        if baseline_result != str(baseline):
            raise ActivationError("Frozen committed baseline does not reproduce the running system")
        for name in changes:
            if name.endswith(".nix"):
                runner.user("nix-instantiate", ["--parse", new_source / name])
        affected_hosts = BATCH_HOSTS if batch_mode else ([HOST, "michapc", "michapc-debug"] if "t3" in metadata else [HOST])
        host_derivations = {}
        for host in affected_hosts:
            host_derivations[host] = nix_eval(runner, new_source, "nixosConfigurations." + host + ".config.system.build.toplevel.drvPath")
        sensitive_state = None
        if batch_mode:
            sensitive_state = batch.verify_sensitive_state(
                old_source, new_source,
                lambda expression: runner.user("nix", batch.sensitive_eval_args(expression)),
                mode=request["mode"],
                migration_executor=lambda name, args: runner.user(name, args, timeout=1800))
            nix_check(runner, new_source)
        journal("preparing", host_derivations=host_derivations, flake_check_passed=batch_mode, sensitive_state=sensitive_state)
        app_pairs, app_tests = probe_applications(runner, old_source, new_source,
                                                 ["t3", "codex", "chatgpt"] if batch_mode else list(metadata),
                                                 workspace, run, changed_outputs_only=batch_mode)
        new_system = nix_build(runner, new_source, "nixosConfigurations.nixy-laptop.config.system.build.toplevel")
        valid_system(str(new_system))
        if new_system == baseline:
            raise ActivationError("Candidate build equals baseline")
        niri_receipt = probe_niri(runner, new_source, workspace) if batch_mode else None
        if niri_receipt is not None:
            atomic_write(run / "probe-niri.json", encoded(niri_receipt))
            app_tests["niri"] = {"probed": True, "coverage": niri_receipt.get("coverage")}
        closure = (batch if batch_mode else gates).verify_closure(str(baseline), str(new_system), app_pairs, changes)
        runtime_units = batch.verify_runtime_units(str(baseline), str(new_system)) if batch_mode else None
        journal("tested", new_system=str(new_system), closure=closure, app_tests=app_tests, niri=niri_receipt, runtime_units=runtime_units)
        # Root-owned GC roots survive reboot/power loss and retain both recovery closures.
        for label, system in [("baseline-system", baseline), ("tested-system", new_system)]:
            runner.root("nix-store", ["--add-root", run / label, "--indirect", "--realise", system])
        check_power()
        check_space(50 * GIB)
        current, index = repository_manifest(runner, repository, request["baseline_commit"])
        if current != before_manifest or index != before_index:
            raise ActivationError("Main source changed while building or probing")
        check_system(baseline)
        check_other_activation()
        check_remote(runner, repository, request["baseline_commit"])
        backup_parent = ensure_directory(BACKUPS)
        backup = ensure_directory(backup_parent / request["id"])
        number = create_snapshot(runner, request["id"])
        journal("checkpoint-created", snapshot=number, backup_directory=str(backup))
        atomic_write(backup / "original-index-copy", before_index)
        after = integrate_source(runner, repository, request, before_manifest, before_index, changes, backup, workspace, journal)
        check_power()
        check_space(50 * GIB)
        check_system(baseline)
        check_other_activation()
        if repository_manifest(runner, repository, request["candidate_commit"])[0] != after:
            raise ActivationError("Source changed after integration; recovery required")
        for name, (old_bytes, _new_bytes) in changes.items():
            if read_regular(backup / name.replace("/", "__"), maximum=128 * 1024 * 1024)[0] != old_bytes:
                raise ActivationError("Save through displaced original retained; recovery required")
        check_remote(runner, repository, request["baseline_commit"])
        if batch_mode:
            runtime_units = batch.verify_runtime_units(str(baseline), str(new_system))
            journal(record["phase"], runtime_units=runtime_units)
        journal("activating")
        try:
            activate_exact(runner, new_system)
            runner.root("systemctl", ["is-active", "--quiet", "NetworkManager", "systemd-resolved", "netbird-personal"])
            runner.root("getent", ["hosts", "github.com"])
        except Exception as exc:
            journal("rolling-back-system", activation_error=type(exc).__name__)
            try:
                activate_exact(runner, baseline)
                journal("recovery-required", system_rollback="complete", reason="Source remains at candidate; Home snapshot retained")
            except Exception as rollback_error:
                journal("recovery-required", system_rollback="failed", rollback_error=type(rollback_error).__name__)
            raise ActivationError("Activation failed; transaction requires recovery review") from exc
        if repository_manifest(runner, repository, request["candidate_commit"])[0] != after:
            journal("recovery-required", reason="Source changed during activation; concurrent changes retained")
            raise ActivationError("Source changed during activation; recovery required")
        # Publishing is a normal-user authenticated Git operation, never forced.
        journal("publishing")
        check_remote(runner, repository, request["baseline_commit"])
        if repository_manifest(runner, repository, request["candidate_commit"])[0] != after:
            raise ActivationError("Main changed before publication; recovery required")
        runner.git(repository, "push", "origin", request["candidate_commit"] + ":refs/heads/main")
        runner.git(repository, "fetch", "origin", "main")
        if runner.git(repository, "rev-parse", "origin/main").decode().strip() != request["candidate_commit"]:
            raise ActivationError("Remote tracking main does not match the activated candidate")
        if repository_manifest(runner, repository, request["candidate_commit"])[0] != after:
            raise ActivationError("Main changed during publication; recovery required")
        journal("complete")
        retention = []
        try:
            retention = prune_owned(runner, state)
        except Exception:
            retention = ["Cleanup deferred; tagged artifacts retained for review"]
        write_status("updated", request["id"], "complete", snapshot=number, system=str(new_system), packages=metadata, retention=retention)
        notify_outcome(runner, "updated")
        return {"schema": request["schema"], "ok": True, "id": request["id"], "system": str(new_system),
                "snapshot": number, "packages": metadata, "app_tests": app_tests}
    except Exception as exc:
        if record["phase"] not in TERMINAL_PHASES and record["phase"] not in {"recovery-required", "rolling-back-system"}:
            journal("failed-before-integration" if record["phase"] in {"preparing", "tested"} else "recovery-required", error=type(exc).__name__)
        if write_status("blocked", request["id"], record["phase"], reason=str(exc)[:400]):
            notify_outcome(runner, "blocked")
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, default=INBOX)
    parser.add_argument("--state", type=Path, default=STATE)
    parser.add_argument("--repository", type=Path, default=REPOSITORY)
    parser.add_argument("--check", action="store_true", help="Read-only request and source policy validation; does not build or activate")
    parser.add_argument("--status", action="store_true", help="Read-only public transaction status")
    args = parser.parse_args()
    if args.status:
        if STATUS.exists():
            print(read_regular(STATUS)[0].decode(), end="")
        else:
            print(json.dumps({"schema": 1, "outcome": "not-installed"}))
        return 0
    require_switch_checks()
    if os.geteuid() != 0:
        raise ActivationError("Activation/check requires the fixed root system service")
    if args.request != INBOX or args.state != STATE or args.repository != REPOSITORY:
        raise ActivationError("Installed privileged paths are fixed")
    if os.uname().nodename != HOST:
        raise ActivationError("This transaction belongs to nixy-laptop")
    runner = Runner()
    if args.check:
        data, _ = read_regular(args.request, expected_uid=runner.account.pw_uid)
        result = process_request(runner, parse_request(data), args.state, args.repository, check_only=True)
    else:
        # The root state permits traversal into immutable sources/user-owned workers only.
        ensure_directory(args.state, mode=0o711)
        ensure_directory(STATUS.parent, mode=0o755)
        for name, mode in [("runs", 0o700), ("requests", 0o700), ("sources", 0o711), ("workers", 0o711)]:
            ensure_directory(args.state / name, mode=mode)
        lock = os.open(args.state / "activation.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            archive_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
            data = consume_request(args.request, args.state / "requests" / (archive_id + ".json"), runner.account.pw_uid)
            try:
                request = parse_request(data)
            except Exception as exc:
                write_status("blocked", archive_id, "request-rejected", reason=str(exc)[:400])
                raise
            try:
                result = process_request(runner, request, args.state, args.repository)
            except Exception as exc:
                # process_request preserves its specific recovery phase. Preflight errors
                # before its journal exists still need a public refusal receipt.
                journal_path = args.state / "runs" / request["id"] / "journal.json"
                if journal_path.exists():
                    phase = json_object(read_regular(journal_path, maximum=8 * 1024 * 1024)[0]).get("phase", "recovery-required")
                else:
                    phase = "recovery-required" if isinstance(exc, RecoveryRequired) else "preflight-refused"
                if write_status("blocked", request["id"], phase, reason=str(exc)[:400]):
                    notify_outcome(runner, "blocked")
                raise
        finally:
            os.close(lock)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"schema": 1, "ok": False, "error": str(exc)[:400]}), file=sys.stderr)
        sys.exit(1)
