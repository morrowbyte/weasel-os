"""Run as the operator inside the disposable Niri guest. See the workspace README."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import uuid


def call(*args):
    return json.loads(subprocess.check_output(args))


def wait_for(check, description):
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        if result := check():
            return result
        time.sleep(0.05)
    raise RuntimeError(f"timed out waiting for {description}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="profile-test-" + uuid.uuid4().hex[:12])
    parser.add_argument("--phase", choices=["both", "save", "reopen"], default="both")
    args = parser.parse_args()
    if not re.fullmatch(r"profile-test-[a-zA-Z0-9_-]{1,19}", args.name):
        parser.error("use a dedicated profile-test-* name of up to 32 characters")
    cli = ["weasel-workspace", "--name", args.name]
    data = Path(os.environ.get("XDG_DATA_HOME", ""))
    if not data.is_absolute():
        data = Path.home() / ".local/share"
    profile = data / "weasel-workspace/profiles" / args.name
    document = profile / "mousepad.txt"
    if call(*cli, "status")["running"]:
        raise RuntimeError("test workspace already running")
    if args.phase != "reopen" and profile.exists():
        raise RuntimeError("save requires an unused test profile")
    if args.phase == "reopen" and not document.is_file():
        raise RuntimeError("reopen requires the saved test document")

    def focused_window():
        return next((w for w in call("niri", "msg", "--json", "windows") if w["is_focused"]), None)

    foreground = focused_window()
    if not foreground:
        raise RuntimeError("focus an operator window in the disposable guest before running the probe")
    front_id = foreground["id"]
    print(f"Testing {args.name}, foreground {front_id}", flush=True)
    with tempfile.TemporaryDirectory(dir=Path.home(), prefix=".profile-probe-") as temp:
        host_marker = Path(temp) / "operator-only"
        host_marker.touch()
        events = Path(temp) / "focus.jsonl"
        with events.open("wb") as log, subprocess.Popen(
            ["niri", "msg", "--json", "event-stream"], stdout=log
        ) as stream:
            try:
                wait_for(lambda: b'"WindowsChanged"' in events.read_bytes(), "initial Niri windows")
                phases = ["save", "reopen"] if args.phase == "both" else [args.phase]
                for phase in phases:
                    state = call(*cli, "start", "--persistent")
                    try:
                        session = state["session"]
                        assert state["persistent"] and not state["network"] and state["exchange"] is None, state
                        assert not state["windows"], state
                        assert call(*cli, "start", "--persistent")["session"] == session
                        if phase == "save":
                            call(*cli, "open", "--", "bash", "--noprofile", "--norc", "-c",
                                 'test "$HOME" = /home/workspace && test "$XDG_RUNTIME_DIR" = /run/workspace && '
                                 'test "$DBUS_SESSION_BUS_ADDRESS" = unix:path=/run/workspace/bus && '
                                 'test -S "$XDG_RUNTIME_DIR/$WAYLAND_DISPLAY" && test -z "${SSH_AUTH_SOCK-}" && '
                                 'test ! -e "$1" && printf "seed\\n" > "$HOME/mousepad.txt"', "probe", str(host_marker))
                            wait_for(lambda: document.exists() and document.read_bytes() == b"seed\n", "seed file")
                            before = b"seed\n"
                        else:
                            before = document.read_bytes()
                            previous = re.fullmatch(rb"saved ([0-9a-f]{32}) seed\n", before)
                            assert previous and previous[1].decode() != session, (before, session)
                        call(*cli, "open", "--", "mousepad", "--disable-server", "/home/workspace/mousepad.txt")
                        def editor():
                            return next((w for w in call(*cli, "status")["windows"] if w["focused"]
                                         and "mousepad" in w["app_id"].lower() and "mousepad.txt" in w["title"]), None)
                        window = wait_for(editor, "new focused private Mousepad document")
                        prefix = f"saved {session} " if phase == "save" else "reopened "
                        call(*cli, "key", "Ctrl+Home")
                        call(*cli, "text", prefix)
                        call(*cli, "key", "Ctrl+s")
                        expected = prefix.encode() + before
                        wait_for(lambda: document.read_bytes() == expected, "exact bytes saved by Mousepad")
                        print(f"{phase}: session={session}, window={window['id']}", flush=True)
                    finally:
                        call(*cli, "stop")
                    assert not call(*cli, "status")["running"] and document.read_bytes() == expected
                    if phase == "save":
                        exchange = Path(temp) / "exchange"
                        ephemeral = call(*cli, "start", "--exchange", str(exchange))
                        try:
                            assert not ephemeral["persistent"] and ephemeral["session"] != session
                            call(*cli, "open", "--", "bash", "--noprofile", "--norc", "-c",
                                 'test ! -e "$HOME/mousepad.txt" && echo clean > /exchange/result')
                            receipt = exchange / "result"
                            wait_for(lambda: receipt.exists() and receipt.read_bytes() == b"clean\n",
                                     "empty ephemeral home")
                        finally:
                            call(*cli, "stop")
                    assert focused_window()["id"] == front_id
                assert stream.poll() is None, "Niri event stream ended early"
            finally:
                stream.terminate()
                stream.wait(timeout=5)
        for line in events.read_text().splitlines():
            event = json.loads(line)
            if "WindowsChanged" in event:
                assert [w["id"] for w in event["WindowsChanged"]["windows"] if w["is_focused"]] == [front_id], event
            if "WindowFocusChanged" in event:
                assert event["WindowFocusChanged"]["id"] == front_id, event
            if "WindowOpenedOrChanged" in event:
                window = event["WindowOpenedOrChanged"]["window"]
                assert window["is_focused"] == (window["id"] == front_id), event
    print(f"Passed {args.phase}; saved test document: {document}")


if __name__ == "__main__":
    main()
