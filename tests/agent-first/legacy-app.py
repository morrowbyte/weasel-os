# Runs immediately after desktop.py while the original Niri draft holds focus.
def check_legacy_app() -> None:
    legacy = ["weasel-workspace", "--name", "legacy"]
    share = "/tmp/agent-first-legacy-exchange"
    started = json.loads(machine.succeed(operator(
        "env DISPLAY=:65432 XAUTHORITY=/tmp/host-cookie-must-not-be-used "
        + shlex.join([*legacy, "start", "--exchange", share])
    )))
    try:
        assert started["running"] and not started["network"], started
        # Record the socket and network namespace seen by the real X client.
        # Only input from the terminal determines the contents of saved.txt.
        child = """
            set -eu
            test -z "${XAUTHORITY-}"
            number=${DISPLAY#:}
            test "${number%%.*}" != 65432
            sock=/tmp/.X11-unix/X${number%%.*}
            test -S "$sock"
            printf '%s\\n' "$sock" > /exchange/socket
            stat -Lc '%d:%i' "$sock" > /exchange/socket-id
            readlink /proc/self/ns/net > /exchange/network
            IFS= read -r line
            printf '%s\\n' "$line" > /exchange/saved.txt
        """
        request = {"name": "legacy", "program": "@xterm@", "wait_window": "XTerm",
                   "argv": ["-name", "private-legacy", "-fa", "DejaVu Sans Mono",
                            "-e", "/bin/sh", "-c", child]}
        receipt = json.loads(machine.succeed(operator(shlex.join(
            ["weasel", "run", "private_open", json.dumps(request)]
        ))))
        window = receipt["steps"][0]["result"]["window"]
        assert window["focused"] and window["app_id"] == window["x11_class"] == "XTerm", window
        assert window["x11_window"] > 0 and window["x11_instance"] == "private-legacy", window
        machine.wait_until_succeeds(f"test -s {share}/network")
        path = machine.succeed(f"cat {share}/socket").strip()
        prefix = "/tmp/.X11-unix/X"
        assert path.startswith(prefix) and path.removeprefix(prefix).isdecimal(), path
        machine.succeed(f"test ! -e {path} || test \"$(stat -Lc '%d:%i' {path})\" != \"$(cat {share}/socket-id)\"")
        machine.succeed(f'test "$(readlink /proc/self/ns/net)" != "$(cat {share}/network)"')

        machine.send_chars("host", delay=0.15)
        for action in (["text", "legacy"], ["key", "1"], ["key", "Return"]):
            machine.succeed(operator(shlex.join([*legacy, *action])))
        machine.wait_until_succeeds(f"printf 'legacy1\\n' | cmp - {share}/saved.txt", timeout=15)
        finish_draft("legacy", "host")
    finally:
        machine.succeed(operator(shlex.join([*legacy, "stop"])))


with subtest("Type into an X11-only xterm in private Sway while Niri retains the human draft"):
    check_legacy_app()
