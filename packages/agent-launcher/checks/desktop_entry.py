"""Run with the launcher's Python + PyGObject. No compositor or user bus required."""
import importlib.util
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, SOURCE / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    store = root / "store"
    bindir = store / "vendor/bin"
    share = store / "vendor/share"
    entries = share / "applications"
    bindir.mkdir(parents=True)
    entries.mkdir(parents=True)
    app = bindir / "++vendor.überlange.app-version+1"
    receipt = root / "launches"
    # The GLib-launched parent exits first. Its child emits the real failure.
    app.write_text(f"""#!{sys.executable}
import os, sys
if os.fork():
    sys.exit(0)
with open({str(receipt)!r}, 'a') as stream:
    print(repr(sys.argv[1:]), file=stream)
print("Missing private test profile", file=sys.stderr)
sys.exit(23)
""")
    app.chmod(0o700)
    for filename, options in [("Vendor", ""), ("Hidden", "NoDisplay=true"), ("Terminal", "Terminal=true")]:
        (entries / (filename + ".desktop")).write_text(
            f'[Desktop Entry]\nName={filename}\nType=Application\nDBusActivatable=true\n'
            f'Exec={app.name} "two words" %c %U\n{options}\n')
    os.environ.update(HOME=directory, XDG_RUNTIME_DIR=directory, XDG_DATA_HOME=str(root / "user-data"),
                      XDG_DATA_DIRS=str(share), DBUS_SESSION_BUS_ADDRESS=f"unix:path={root}/absent",
                      PATH=str(bindir) + os.pathsep + os.environ["PATH"])
    launcher = load("launcher")
    launcher.STORE = store
    choice, = list(launcher.applications())
    label, (program, entry, app_bin, data) = choice
    assert label == "Neu · Vendor (Vendor.desktop)" and program == app.name, choice
    assert (entry, app_bin, data) == (str(entries / "Vendor.desktop"), str(bindir), str(share))
    diagnosis = load("private").monitor(entry, root / "unmapped")
    assert "Exit 23" in diagnosis and "Missing private test profile" in diagnosis, diagnosis
    assert receipt.read_text().splitlines() == ["['two words', 'Vendor']"]

    cancelled = subprocess.CompletedProcess([], 2, stdout="", stderr="")
    with patch("subprocess.run", return_value=cancelled):
        assert launcher.pick([choice]) is None

    # Check host-side cleanup only for the fresh, empty failed workspace.
    calls = []
    def workspace(name, action, *args):
        assert re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,31}", name), name
        calls.append(action)
        if action == "open":
            workspace.failure = args[-1]
        if action == "status" and "open" in calls:
            return {"running": True, "paused": False, "job": None, "view_open": False,
                    "windows": [{"app_id": workspace.failure, "title": diagnosis}]}
        return {"paused": False}

    with patch.object(launcher, "workspace", workspace):
        try:
            launcher.new_workspace(program, entry, app_bin, data)
        except RuntimeError as exc:
            assert diagnosis in str(exc)
        else:
            raise AssertionError("Missing startup failure")
    assert calls == ["status", "start", "open", "status", "stop"], calls
    print("Third-party catalog, single private GLib launch, descendant error, scoped cleanup and Escape passed.")
