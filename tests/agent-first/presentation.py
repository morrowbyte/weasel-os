with subtest("Manually edit the same private window, then continue behind a Niri foreground draft"):
    root = "/tmp/workspace-view-exchange"
    cli = ["weasel-workspace", "--name", "presentation"]

    def view_cmd(*args: str) -> str:
        return shlex.join([*cli, *args])

    def private_window() -> dict:
        state = json.loads(machine.succeed(operator(view_cmd("status"))))
        window, = state["windows"]
        assert window["focused"], state
        return window

    machine.succeed(operator(view_cmd("start", "--exchange", root)))
    private_file = root + "/presentation.txt"
    machine.succeed(operator(f"touch {private_file}"))
    machine.succeed(operator(view_cmd("open", "--", "mousepad", "--disable-server",
                                      "/exchange/presentation.txt")))
    machine.wait_until_succeeds(operator(view_cmd("status") +
        """ | jq -e '.windows[] | select(.focused and (.title | contains("presentation.txt")))'"""))
    identity = private_window()
    host_display = ('export WAYLAND_DISPLAY=$(systemctl --user show-environment | '
                    'sed -n "s/^WAYLAND_DISPLAY=//p"); ')
    machine.succeed(operator(host_display + "printf foreground-clipboard | wl-copy"))
    machine.succeed(operator("niri msg action spawn -- " + view_cmd("view")))
    viewer_id = int(machine.wait_until_succeeds(operator(
        """niri msg --json windows | jq -er '.[] | select(.is_focused and
        .title == "presentation · Manuelle Eingabe") | .id'"""), timeout=30))
    conflict_code, conflict = machine.execute(operator(view_cmd("text", "collision") + " 2>&1"))
    assert conflict_code != 0 and "manual use" in conflict, conflict
    machine.send_chars("manual")
    machine.send_key("ctrl-a")
    machine.send_key("ctrl-c")
    machine.send_key("ctrl-end")
    machine.send_key("ctrl-s")
    machine.wait_until_succeeds(f"test \"$(cat {private_file})\" = manual")

    machine.screenshot("private-presentation-manual")
    # The viewer stays mapped while the real guest keyboard types in a second
    # window and the broker edits the original editor through its private seat.
    foreground_file = home + "/presentation-foreground.txt"
    command = shlex.join(["foot", "--app-id=presentation-draft", "sh", "-c",
        f'IFS= read -r line; printf "%s\\n" "$line" > {foreground_file}; exec sleep infinity'])
    machine.succeed(operator("niri msg action spawn -- " + command))
    foreground_id = int(machine.wait_until_succeeds(operator(
        """niri msg --json windows | jq -er '.[] | select(.is_focused and
        .app_id == "presentation-draft") | .id'""")))
    machine.wait_until_succeeds(operator(view_cmd("status") +
        " | jq -e '.view_open and (.manual_input | not)'"))
    machine.succeed(operator(view_cmd("open", "--", "sh", "-c",
        "wl-paste --no-newline > /exchange/copied.txt")))
    machine.wait_until_succeeds(f"test \"$(cat {root}/copied.txt)\" = manual")
    assert machine.succeed(operator(host_display + "wl-paste --no-newline")) == "foreground-clipboard"
    machine.send_chars("fore")
    machine.succeed(operator(view_cmd("text", "agent")))
    machine.succeed(operator(view_cmd("key", "Ctrl+s")))
    machine.send_chars("ground")
    machine.send_key("ret")
    machine.wait_until_succeeds(f"test \"$(cat {foreground_file})\" = foreground")
    machine.wait_until_succeeds(f"test \"$(cat {private_file})\" = manualagent")
    assert private_window()["id"] == identity["id"]
    machine.succeed(operator(f"niri msg action close-window --id {viewer_id}"))
    machine.wait_until_succeeds(operator(view_cmd("status") + " | jq -e '.view_open | not'"))
    assert private_window()["id"] == identity["id"]
    machine.succeed(operator(view_cmd("text", "closed")))
    machine.succeed(operator(view_cmd("key", "Ctrl+s")))
    machine.wait_until_succeeds(f"test \"$(cat {private_file})\" = manualagentclosed")
    machine.succeed(operator("niri msg --json windows | jq -e " + shlex.quote(
        f".[] | select(.id == {foreground_id} and .is_focused)")))
    machine.log(f"Private Sway window {identity['id']} ({identity['app_id']}), Niri viewer "
                f"{viewer_id}, foreground {foreground_id}: manualagentclosed / foreground")
    machine.succeed(operator("niri msg action spawn -- " + view_cmd("view")))
    machine.wait_until_succeeds(operator(view_cmd("status") + " | jq -e '.manual_input'"))
    machine.succeed(operator(view_cmd("cancel-all")))
    machine.wait_until_succeeds(operator(view_cmd("status") + " | jq -e '.paused and (.view_open | not)'"))
    assert private_window()["id"] == identity["id"]
    machine.succeed(operator(view_cmd("resume")))
    machine.copy_from_machine(private_file)
    machine.copy_from_machine(foreground_file)
    machine.succeed(operator(view_cmd("stop")))
