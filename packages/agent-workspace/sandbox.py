"""Host-side namespace construction and lifetime. No desktop secrets are imported."""
import contextlib
import fcntl
import json
import os
import select
import signal
import socket
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

from actions import identifier

HERE = Path(__file__).resolve().parent
BWRAP = "@bwrap@"
SLIRP = "@slirp@"
DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


@contextlib.contextmanager
def directory_fd(path, *, parent=None, create=False):
    if create:
        try:
            os.mkdir(path, mode=0o700, dir_fd=parent)
        except FileExistsError:
            pass
    fd = os.open(path, DIRECTORY, dir_fd=parent)
    try:
        yield fd
    finally:
        os.close(fd)


def data_home():
    configured = Path(os.environ.get("XDG_DATA_HOME", ""))
    return configured if configured.is_absolute() else Path.home() / ".local/share"


def reject_exchange_parent(fd):
    try:
        os.stat(".weasel-workspace-exchange", dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    raise ValueError("workspace profiles cannot be stored inside an exchange")


@contextlib.contextmanager
def profile_dir(name, *, create=True):
    """Open every component relative to its pinned parent. Only the leaf enters the guest."""
    data = data_home()
    name = identifier(name)
    if data.is_relative_to(Path(os.environ["XDG_RUNTIME_DIR"]).resolve()) or ".." in data.parts:
        raise ValueError("profile storage requires a durable absolute XDG data directory")
    with contextlib.ExitStack() as stack:
        fd = stack.enter_context(directory_fd("/"))
        for part in data.parts[1:]:
            fd = stack.enter_context(directory_fd(part, parent=fd, create=create))
            info = os.fstat(fd)
            if info.st_uid not in (0, os.getuid()) or (info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX):
                raise ValueError("unsafe parent directory for workspace profiles")
            reject_exchange_parent(fd)
        for part in ("weasel-workspace", "profiles", name):
            fd = stack.enter_context(directory_fd(part, parent=fd, create=create))
            info = os.fstat(fd)
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise ValueError("workspace profile directories must be owned by this user with mode 0700")
            reject_exchange_parent(fd)
        yield fd


@contextlib.contextmanager
def exchange_dir(value):
    if value is None:
        yield None, None
        return
    path = Path(value).absolute()
    profiles = (data_home() / "weasel-workspace").resolve()
    if path.resolve().is_relative_to(profiles):
        raise ValueError("exchange overlaps private workspace profiles")
    with directory_fd(path, create=True) as fd:
        # Validate the very directory bubblewrap receives, even if its host name changes.
        path = Path(os.readlink(f"/proc/self/fd/{fd}"))
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("exchange must be a private directory owned by this user (0700)")
        protected = [Path.home().resolve(), Path(os.environ["XDG_RUNTIME_DIR"]).resolve()]
        if path.is_relative_to(profiles) or profiles.is_relative_to(path):
            raise ValueError("exchange overlaps private workspace profiles")
        if any(p.is_relative_to(path) for p in protected) or path.is_relative_to(protected[1] / "weasel-workspace"):
            raise ValueError("exchange overlaps the home or workspace control directory")
        marker = ".weasel-workspace-exchange"
        try:
            info = os.stat(marker, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            with os.scandir(fd) as entries:
                if next(entries, None) is not None:
                    raise ValueError("first use of an exchange directory requires an empty directory")
            os.close(os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600, dir_fd=fd))
        else:
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                raise ValueError("invalid exchange marker")
        yield str(path), fd


def sandbox_args(gate_dir, exchange, network, profile_fd=None):
    args = [BWRAP, "--unshare-all", "--unshare-user", "--uid", "1000", "--gid", "1000",
            "--cap-drop", "ALL", "--new-session", "--clearenv",
            "--ro-bind", "/nix/store", "/nix/store", "--proc", "/proc", "--dev", "/dev",
            "--tmpfs", "/tmp", "--dir", "/etc", "--dir", "/bin", "--dir", "/usr/bin",
            "--symlink", "@shell@", "/bin/sh", "--symlink", "@env@", "/usr/bin/env",
            "--ro-bind", HERE / "passwd", "/etc/passwd", "--ro-bind", HERE / "group", "/etc/group",
            "--ro-bind", "@fonts@", "/etc/fonts/fonts.conf",
            "--ro-bind", "@cert@", "/etc/ssl/certs/ca-certificates.crt",
            "--perms", "0700", "--dir", "/run/workspace", "--ro-bind", gate_dir, "/run/gate"]
    if profile_fd is None:
        args.extend(["--perms", "0700", "--dir", "/home/workspace"])
    else:
        args.extend(["--bind-fd", str(profile_fd), "/home/workspace"])
    args.extend(["--chdir", "/home/workspace"])
    if exchange is not None:
        args.extend(["--bind-fd", str(exchange), "/exchange"])
    if network:
        args.extend(["--ro-bind", HERE / "resolv.conf", "/etc/resolv.conf"])
    env = {
        "HOME": "/home/workspace", "USER": "workspace", "LOGNAME": "workspace",
        "SHELL": "@shell@", "PATH": "@path@", "LANG": "C.UTF-8", "TMPDIR": "/tmp",
        "XDG_RUNTIME_DIR": "/run/workspace", "XDG_SESSION_TYPE": "wayland",
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/workspace/bus",
        "GDK_BACKEND": "wayland,x11", "QT_QPA_PLATFORM": "wayland;xcb", "MOZ_ENABLE_WAYLAND": "1",
        "NIXOS_OZONE_WL": "1", "ELECTRON_OZONE_PLATFORM_HINT": "wayland",
        "WLR_BACKENDS": "headless", "WLR_HEADLESS_OUTPUTS": "1", "WLR_RENDERER": "pixman",
        "WLR_XWAYLAND": "@xwayland@",
        "FONTCONFIG_FILE": "/etc/fonts/fonts.conf", "SSL_CERT_FILE": "/etc/ssl/certs/ca-certificates.crt",
    }
    for key, value in env.items():
        args.extend(["--setenv", key, value])
    return args


@contextlib.contextmanager
def pipe():
    r, w = os.pipe()
    with os.fdopen(r, "rb", buffering=0) as receive, os.fdopen(w, "wb", buffering=0) as send:
        yield receive, send


def network_helper(pid, owner, log):
    with pipe() as (ready, notify), os.fdopen(os.pidfd_open(owner.pid), "rb", buffering=0) as lifetime:
        helper = subprocess.Popen(
            [SLIRP, "--configure", "--disable-host-loopback", f"--userns-path=/proc/{pid}/ns/user",
             "--ready-fd", str(notify.fileno()), "--exit-fd", str(lifetime.fileno()), str(pid), "tap0"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log, env={},
            pass_fds=[notify.fileno(), lifetime.fileno()], start_new_session=True)
        notify.close()
        if not select.select([ready], [], [], 5)[0] or ready.read(1) != b"1":
            if helper.poll() is None:
                helper.kill()
            helper.wait()
            raise RuntimeError("private network setup failed")


@contextlib.contextmanager
def lifetime_fd(live, profile_fd):
    fcntl.flock(live, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if profile_fd is None:
        yield live.fileno()
        return
    try:
        fcntl.flock(profile_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise ValueError("workspace profile is already in use") from exc
    # bwrap has one --sync-fd. Its private socket holds both locked open file
    # descriptions in one unread SCM_RIGHTS message until namespace teardown.
    sender, lifetime = socket.socketpair()
    with sender, lifetime:
        socket.send_fds(sender, [b"L"], [live.fileno(), profile_fd])
        yield lifetime.fileno()


def spawn(paths, options, exchange_fd, probe, *, profile_fd=None):
    """Caller holds the per-name lifecycle lock. Keep the lifetime flock in bwrap."""
    args = sandbox_args(paths.base / "gate", exchange_fd, options["network"], profile_fd)
    with socket.socket(socket.AF_UNIX) as listener, paths.live.open("a+b") as live, \
            tempfile.TemporaryFile(dir=paths.base) as log, pipe() as (info, report), \
            lifetime_fd(live, profile_fd) as sync_fd:
        paths.sock.unlink(missing_ok=True)
        listener.bind(str(paths.sock))
        listener.listen(32)
        args.extend(["--info-fd", str(report.fileno()), "--sync-fd", str(sync_fd),
                     "--", str(Path(sys.executable).resolve()), "-I", HERE / "broker.py"])
        inherited = [report.fileno(), sync_fd]
        if profile_fd is not None:
            inherited.append(profile_fd)
        if exchange_fd is not None:
            inherited.append(exchange_fd)
        proc = subprocess.Popen(args, stdin=listener, stdout=subprocess.DEVNULL, stderr=log,
                                env={}, pass_fds=inherited, start_new_session=True)
        listener.close()
        report.close()
        initfd = None
        try:
            if not select.select([info], [], [], 5)[0]:
                raise RuntimeError("namespace creation timed out")
            metadata = json.load(info)
            pid = metadata["child-pid"]
            initfd = os.pidfd_open(pid)
            # Only signal our own live PID-namespace init, even on startup failure.
            if (Path(f"/proc/{pid}/ns/pid").stat().st_ino != metadata["pid-namespace"]
                    or proc.poll() is not None):
                os.close(initfd)
                initfd = None
                raise RuntimeError("workspace exited during namespace setup")
            if options["network"]:
                network_helper(pid, proc, log)
            return probe()
        except BaseException as exc:
            if initfd is not None:
                try:
                    signal.pidfd_send_signal(initfd, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if proc.poll() is None:
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            # pread does not disturb the writer's file offset.
            size = os.fstat(log.fileno()).st_size
            detail = os.pread(log.fileno(), 2048, max(0, size - 2048)).decode(errors="replace").strip()
            paths.sock.unlink(missing_ok=True)
            raise RuntimeError(f"{exc}{': ' + detail if detail else ''}") from exc
        finally:
            if initfd is not None:
                os.close(initfd)
