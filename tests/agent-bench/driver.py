"""Test script executed by the qualified nixos-test-driver with its machine object."""
import json
import os
import re
import shlex
import socket
import time
from pathlib import Path

ROOT = Path(os.environ["BENCH_RUN"]).resolve()
SOURCE = Path(os.environ["BENCH_SOURCE"]).resolve()
MODE = os.environ["BENCH_FAMILY"]
FAMILY = "files" if MODE == "files-os" else MODE
HOME = "/home/operator"
BENCH = HOME + "/bench"
APP = "bench"
ALLOWED = {"private_status", "private_screenshot", "private_key", "private_text",
           "private_click", "private_scroll"}
if MODE == "files-os":
    ALLOWED |= {"private_exchange_list", "private_exchange_lstat", "private_exchange_rename_noreplace"}
assert FAMILY in {"edit", "files"}
assert ROOT.stat().st_uid == os.getuid() and ROOT.stat().st_mode & 0o077 == 0


def log(event, **values):
    with (ROOT / "events.jsonl").open("a") as file:
        print(json.dumps({"event": event, "host_wall_ns": time.time_ns(),
                          "host_mono_ns": time.monotonic_ns(), **values}), file=file)


def operator(command):
    env = ('set -eu -o pipefail; export XDG_RUNTIME_DIR=/run/user/$(id -u); '
           'export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"; '
           'set -- "$XDG_RUNTIME_DIR"/niri.*.sock; export NIRI_SOCKET="$1"; ')
    return shlex.join(["su", "-", "operator", "-c", env + command])


def run(*args):
    return machine.succeed(operator(shlex.join(args)), timeout=100)


def helper(op):
    return shlex.join(["python3", BENCH + "/guest.py", op, FAMILY])


def workspace(*args):
    return json.loads(run("weasel-workspace", "--name", APP, *args))


def key(code, *states):
    machine.qmp_client.send("input-send-event", {"events": [
        {"type": "key", "data": {"down": state, "key": {"type": "qcode", "data": code}}}
        for state in states]})


def wait_user(command):
    machine.wait_until_succeeds(operator(command), timeout=60)


def prepare():
    machine.start()
    machine.wait_for_unit("greetd.service")
    wait_user("niri msg --json outputs | jq -e 'length > 0'")
    machine.send_key("spc", delay=0.15)
    run("mkdir", "-m", "700", BENCH)
    machine.copy_from_host(str(SOURCE / "guest.py"), "/tmp/bench-fixture.py")
    machine.succeed("install -o operator -m 0500 /tmp/bench-fixture.py " + BENCH + "/guest.py")
    result = workspace("start", "--exchange", BENCH + "/exchange")
    assert result["running"] and result["network"] is False, result
    machine.succeed(operator(helper("seed")))
    filename = "Handover.txt" if FAMILY == "edit" else "Inventory.txt"
    workspace("open", "--", "mousepad", "--disable-server", "/exchange/" + filename)
    wait_user("weasel-workspace --name bench status | jq -e " +
              shlex.quote('any(.windows[]; .focused and (.title | contains("' + filename + '")))'))
    # GTK's actual file chooser supplies the file-management surface in this family.
    if FAMILY == "files":
        # GTK hides its Rename context action in OPEN mode; SAVE exposes it in Browse.
        workspace("key", "Ctrl+Shift+s")
        wait_user("weasel-workspace --name bench status | jq -e " +
                  shlex.quote('any(.windows[]; .focused and (.title | test("save|speichern"; "i")))'))
    draft = ('IFS= read -r line; printf "%s\\n" "$line" > "$HOME/bench/draft"; '
             "printf '\\033]2;draft-complete\\007'; exec sleep infinity")
    run("niri", "msg", "action", "spawn", "--", "foot", "--app-id=bench-draft",
        "--title=draft-ready", "sh", "-c", draft)
    wait_user("""niri msg --json windows | jq -e 'any(.[]; .is_focused and .app_id == "bench-draft")'""")
    run("systemd-run", "--user", "--unit=bench-focus", "-p", "StandardOutput=file:" + BENCH + "/focus",
        "env", "NIRI_SOCKET=" + run("printenv", "NIRI_SOCKET").strip(), "niri", "msg", "--json", "event-stream")
    wait_user("grep -q bench-draft " + BENCH + "/focus")
    machine.send_chars("draft", delay=0.15)
    key("shift", True)
    key("a", True, False)
    run("systemd-run", "--user", "--unit=bench-watch", "python3", BENCH + "/guest.py", "watch", FAMILY)
    wait_user("test -e " + BENCH + "/watch-ready")


def focus_ok(events):
    initial = None
    for event in events:
        if "WindowsChanged" in event:
            focused = [w for w in event["WindowsChanged"]["windows"] if w["is_focused"]]
            if len(focused) != 1 or focused[0]["app_id"] != "bench-draft":
                return False
            if initial is not None and focused[0]["id"] != initial:
                return False
            initial = focused[0]["id"]
        if initial is None:
            continue
        if "WindowFocusChanged" in event and event["WindowFocusChanged"]["id"] != initial:
            return False
        if "OverviewOpenedOrClosed" in event and event["OverviewOpenedOrClosed"]["is_open"]:
            return False
        if "WindowClosed" in event and event["WindowClosed"]["id"] == initial:
            return False
        if "WindowOpenedOrChanged" in event:
            w = event["WindowOpenedOrChanged"]["window"]
            if w["is_focused"] != (w["id"] == initial):
                return False
            if w["id"] == initial and w["title"] == "draft-complete":
                return True
    return False


def finish():
    key("b", True, False)
    key("shift", False)
    key("ret", True, False)
    wait_user("grep -q draft-complete " + BENCH + "/focus")
    events = map(json.loads, run("cat", BENCH + "/focus").splitlines())
    result = json.loads(machine.succeed(operator(helper("verify"))))
    result.update(draft=run("cat", BENCH + "/draft"), focus_ok=focus_ok(events))
    result["success"] = result["files_ok"] and result["focus_ok"] and result["draft"] == "draftAB\n"
    return result


def dispatch(request):
    request_id = request["id"]
    if not isinstance(request_id, str) or not re.fullmatch("[a-zA-Z0-9_-]{1,64}", request_id):
        raise ValueError("invalid request id")
    steps = request["steps"]
    if not isinstance(steps, list) or not 1 <= len(steps) <= 32:
        raise ValueError("expected 1 through 32 actions")
    for step in steps:
        if step["action"] not in ALLOWED:
            raise ValueError("action outside prepared workspace")
        step.setdefault("args", {})["name"] = APP
    if sum(s["action"] == "private_screenshot" for s in steps) > 1:
        raise ValueError("one screenshot per request")
    payload = shlex.quote(json.dumps({"steps": steps, "id": request_id}))
    response = json.loads(machine.succeed(operator("printf %s " + payload + " | " + helper("compose")), timeout=110))
    product = response["product"]
    if "screenshot" in product:
        machine.copy_from_machine(BENCH + "/" + product["screenshot"])
    return response


def serve():
    with socket.socket(socket.AF_UNIX) as listener:
        listener.bind(str(ROOT / "control.sock"))
        listener.listen(1)
        listener.settimeout(600)
        log("ready", family=FAMILY, mode=MODE, host=socket.gethostname())
        while True:
            conn, _ = listener.accept()
            with conn:
                conn.settimeout(120)
                request = {}
                try:
                    with conn.makefile("rb") as stream:
                        line = stream.readline(65537)
                    if len(line) > 65536 or not line.endswith(b"\n"):
                        raise ValueError("expected a bounded JSON line")
                    request = json.loads(line)
                    began = time.monotonic_ns()
                    log("request", request=request)
                    if request == {"finish": True}:
                        response = finish()
                    else:
                        response = dispatch(request)
                    response["host_call_ms"] = (time.monotonic_ns() - began) / 1e6
                except Exception as exc:
                    response = {"error": str(exc)}
                log("reply", response=response)
                try:
                    conn.sendall(json.dumps(response).encode() + b"\n")
                except BrokenPipeError:
                    log("disconnected")
                if request == {"finish": True}:
                    return


os.umask(0o077)
try:
    prepare()
    serve()
finally:
    if machine.qmp_client is not None:
        key("shift", False)
