"""Choose a private application or present its existing workspace in Niri."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
import time

import gi

gi.require_version("GioUnix", "2.0")
from gi.repository import Gio, GioUnix


BASE = Path(os.environ["XDG_RUNTIME_DIR"]) / "weasel-workspace"
STORE = Path("/nix/store")


def applications():
    for app in Gio.AppInfo.get_all():
        if not isinstance(app, GioUnix.DesktopAppInfo) or not app.should_show():
            continue
        executable = app.get_executable()
        if not executable or app.get_boolean("Terminal"):
            continue
        found = shutil.which(executable)
        if not found:
            continue
        entry = Path(app.get_filename()).resolve()
        binary = Path(found)
        # Resolve profile aliases while keeping a callable store path under the
        # original Exec basename, including aliases with differently named targets.
        bindir = binary.parent.resolve()
        while binary.is_symlink():
            binary = binary.parent / binary.readlink()
            if binary.name != Path(executable).name:
                break
            bindir = binary.parent.resolve()
        if not (entry.is_file() and entry.is_relative_to(STORE) and bindir.is_relative_to(STORE)
                and (bindir / Path(executable).name).resolve().is_relative_to(STORE)):
            continue
        # Absolute profile aliases are not mounted inside a workspace.
        if (os.path.isabs(executable) and not Path(executable).is_relative_to(STORE)
                and executable not in ("/usr/bin/env", "/bin/sh")):
            continue
        share = next((p.parent for p in entry.parents if p.name == "applications"), None)
        if share is None:
            continue
        # Exec can name a wrapper such as env, with a package-local tool as an argument.
        bins = os.pathsep.join(dict.fromkeys(map(str, (bindir, share.parent / "bin"))))
        data = os.pathsep.join(dict.fromkeys(map(str, (share, bindir.parent / "share"))))
        yield (f"Neu · {app.get_display_name()} ({app.get_id()})",
               (Path(executable).name, str(entry), bins, data))


def output(*args):
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"{args[0]}: Exit {result.returncode}")
    return result.stdout


def workspace(name, *args):
    return json.loads(output("weasel-workspace", "--name", name, *args))


def sessions():
    for control in sorted(BASE.glob("ws-*/control")):
        name = control.parent.name[3:]
        if not control.is_socket() or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,31}", name):
            continue
        try:
            state = workspace(name, "status")
        except RuntimeError as exc:
            print(f"{name}: {exc}", file=sys.stderr)
            continue
        if state["running"]:
            titles = ", ".join(w["title"] or w["app_id"] or "Fenster" for w in state["windows"])
            yield (f"Öffnen · {name} · {titles}", name)


def pick(rows):
    labels = ["".join(c if c.isprintable() else " " for c in label) for label, _ in rows]
    result = subprocess.run(["fuzzel", "--dmenu", "--index", "--prompt", "Privat › "],
                            input="\n".join(labels) + "\n", capture_output=True, text=True)
    if result.returncode in (1, 2):
        return None
    if result.returncode:
        raise RuntimeError(result.stderr.strip())
    index = int(result.stdout)
    if not 0 <= index < len(rows):
        raise ValueError("Ungültige Auswahl")
    return rows[index][1]


def new_workspace(program, entry, bindir, data):
    BASE.mkdir(mode=0o700, exist_ok=True)
    # Reserve an unused workspace name atomically, also across launcher invocations.
    slug = re.sub(r"[^A-Za-z0-9]+", "-", program).strip("-")[:20] or "app"
    directory = Path(tempfile.mkdtemp(prefix=f"ws-{slug}-", dir=BASE))
    name = directory.name[3:]
    if workspace(name, "status")["paused"]:
        directory.rmdir()
        raise RuntimeError("Agenteneingabe ist gesperrt.")
    workspace(name, "start")
    failure_id = "weasel-launch-error-" + uuid.uuid4().hex
    workspace(name, "open", "--", "launch-desktop-entry", entry, bindir, data, failure_id)
    # open acknowledges spawning the helper. Only a mapped private window
    # confirms that GIO and the application completed their part of the launch.
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        state = workspace(name, "status")
        if state["paused"] or not state["running"]:
            print(f"{name}: Start unterbrochen.", file=sys.stderr)
            return None
        windows = state["windows"]
        if any(w["app_id"] != failure_id for w in windows):
            # Acknowledge the map in the private runtime only. A later normal
            # app exit must not be reported as a startup error.
            workspace(name, "open", "--", "touch", "/run/workspace/" + failure_id)
            return name
        if len(windows) == 1 and windows[0]["app_id"] == failure_id:
            reason = windows[0]["title"]
            if not state["view_open"] and not state["job"]:
                workspace(name, "stop")
            raise RuntimeError(f"{program}: {reason} (privates Profil, offline, ohne Dateifreigabe).")
        time.sleep(0.15)
    raise RuntimeError(f"{name}: Kein privates Anwendungsfenster nach 30 Sekunden. Privates Profil, offline, "
                       "ohne Dateifreigabe. Startausgang offen; Sitzung bleibt verfügbar.")


def present(name):
    state = workspace(name, "status")
    if state["paused"] or not state["running"]:
        raise RuntimeError(f"{name}: Sitzung ist gesperrt oder beendet.")
    if state["view_open"]:
        windows = json.loads(output("niri", "msg", "--json", "windows"))
        for window in windows:
            if (window["app_id"] == "weasel-workspace-view"
                    and (window["title"] or "").startswith(name + " · ")):
                output("niri", "msg", "action", "focus-window", "--id", str(window["id"]))
                return
        raise RuntimeError(f"{name}: Die Ansicht ist bereits verbunden.")
    os.execlp("weasel-workspace", "weasel-workspace", "--name", name, "view")


def main():
    rows = [*sessions(), *sorted(applications())]
    if not rows:
        raise ValueError("Keine unterstützte Anwendung oder laufende Sitzung gefunden.")
    choice = pick(rows)
    if choice is None:
        return
    name = choice if isinstance(choice, str) else new_workspace(*choice)
    if name is not None:
        present(name)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        subprocess.run(["fuzzel", "--dmenu", "--prompt", "Privat › "], input=str(exc) + "\n", text=True)
        sys.exit(1)
