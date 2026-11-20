"""Launch one desktop entry on the workspace's own bus and Wayland display."""
import ctypes
import os
from pathlib import Path
import select
import subprocess
import sys
import time


def launch(entry, stderr, started):
    # GLib caches XDG_DATA_DIRS on first use.
    import gi
    gi.require_version("GioUnix", "2.0")
    from gi.repository import GioUnix, GLib

    info = GioUnix.DesktopAppInfo.new_from_filename(entry)
    if info.get_boolean("DBusActivatable"):
        # Private sessions do not import desktop activation services. Use the
        # entry's Exec fallback with GIO's normal field-code and quoting rules.
        keyfile = GLib.KeyFile.new()
        keyfile.load_from_file(entry, GLib.KeyFileFlags.KEEP_TRANSLATIONS)
        keyfile.set_boolean("Desktop Entry", "DBusActivatable", False)
        private_entry = Path.home() / ".local/share/applications" / Path(entry).name
        private_entry.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        keyfile.save_to_file(str(private_entry))
        info = GioUnix.DesktopAppInfo.new_from_filename(str(private_entry))
    flags = GLib.SpawnFlags.SEARCH_PATH | GLib.SpawnFlags.DO_NOT_REAP_CHILD
    info.launch_uris_as_manager_with_fds([], None, flags, None, None, started, None, -1, -1, stderr)


def monitor(entry, accepted):
    # In a fresh PID namespace, a desktop launcher may fork the real GUI
    # process and exit. Adopt those descendants before declaring a failure.
    if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:  # Linux PR_SET_CHILD_SUBREAPER
        raise OSError(ctypes.get_errno(), "Prozessüberwachung nicht verfügbar")
    children = []
    readfd, writefd = os.pipe()
    try:
        launch(entry, writefd, lambda app, pid, data: children.append(pid))
    except BaseException:
        os.close(readfd)
        raise
    finally:
        os.close(writefd)

    tail = b""
    exitcode = 0
    deadline = time.monotonic() + 30
    with os.fdopen(readfd, "rb", buffering=0) as log:
        streams = [log]
        while True:
            finished = False
            try:
                while True:
                    pid, status = os.waitpid(-1, os.WNOHANG)
                    if not pid:
                        break
                    exitcode = os.waitstatus_to_exitcode(status) or exitcode
            except ChildProcessError:
                finished = True
            if finished:
                # All descendants closed their descriptors. Take only bytes
                # already readable (an unrelated service could hold a copy).
                while streams and select.select(streams, [], [], 0)[0]:
                    chunk = log.read(65536)
                    if not chunk:
                        break
                    tail = (tail + chunk)[-2048:]
                if children and exitcode and time.monotonic() < deadline and not accepted.exists():
                    detail = "".join(c if c.isprintable() else " " for c in tail.decode(errors="replace"))
                    detail = " ".join(detail.split())[-280:]
                    return f"Startprozess beendet (Exit {exitcode})" + (f": {detail}" if detail else "")
                return None
            if accepted.exists() or time.monotonic() >= deadline:
                if streams:
                    # The GUI keeps a writable stderr after launch supervision ends.
                    subprocess.Popen(["@cat@"], stdin=log, stdout=subprocess.DEVNULL)
                return None
            if select.select(streams, [], [], 0.15)[0]:
                chunk = log.read(65536)
                if not chunk:
                    streams.clear()
                else:
                    tail = (tail + chunk)[-2048:]


def main():
    entry, bindir, datadir, failure_id = sys.argv[1:]
    os.environ["PATH"] = bindir + os.pathsep + os.environ["PATH"]
    os.environ["XDG_DATA_DIRS"] = datadir
    accepted = Path(os.environ["XDG_RUNTIME_DIR"]) / failure_id
    try:
        failure = monitor(entry, accepted)
    except Exception as exc:
        failure = str(exc)
    if failure and not accepted.exists():
        # The broker already exposes native window IDs/titles via status.
        # A temporary diagnostic surface crosses no new sandbox boundary.
        foot = "@foot@"
        os.execv(foot, [foot, "--app-id=" + failure_id, "--title=" + failure,
                        "--", "@sleep@", "35"])


if __name__ == "__main__":
    main()
