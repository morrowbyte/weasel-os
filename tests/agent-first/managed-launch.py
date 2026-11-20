# Appended to desktop.py in the operator's Niri guest.
with subtest("Choose Mousepad by keyboard and present the same private window twice"):
    def choose_private(query: str) -> None:
        machine.succeed(operator("niri msg action spawn -- weasel-agent-launcher"))
        machine.wait_until_succeeds(operator(
            """niri msg --json layers | jq -e 'any(.[]; .namespace == "launcher" or .namespace == "fuzzel")'"""
        ), timeout=30)
        machine.send_chars(query)
        machine.send_key("ret")

    viewer_query = operator(
        """niri msg --json windows | jq -ec '.[] | select(.app_id == "weasel-workspace-view"
        and .is_focused and (.title | startswith("mousepad-")) and (.title | endswith(" · Manuelle Eingabe")))'"""
    )
    choose_private("mousepad.desktop")
    viewer = json.loads(machine.wait_until_succeeds(viewer_query, timeout=45))
    name = viewer["title"].split(" · ", 1)[0]
    cli = ["weasel-workspace", "--name", name]

    def launch_state() -> dict:
        return json.loads(machine.succeed(operator(shlex.join([*cli, "status"]))))

    state = launch_state()
    window, = state["windows"]
    assert "mousepad" in window["app_id"].lower(), state
    assert state["manual_input"] and not state["network"] and state["exchange"] is None, state
    foreground = "managed-launch-foreground"
    machine.succeed(operator(f"niri msg action spawn -- foot --app-id={foreground}"))
    focus_query = operator(f"""niri msg --json windows | jq -er '.[] | select(.is_focused and .app_id == "{foreground}") | .id'""")
    front_id = int(machine.wait_until_succeeds(focus_query))
    machine.wait_until_succeeds(operator(shlex.join([*cli, "status"]) +
        " | jq -e '.view_open and (.manual_input | not)'"))
    machine.succeed(operator(shlex.join([*cli, "text", "Privater Hintergrundentwurf"])))
    assert int(machine.succeed(focus_query)) == front_id
    choose_private(name)
    assert json.loads(machine.wait_until_succeeds(viewer_query))["id"] == viewer["id"]
    machine.succeed(operator(f"niri msg action close-window --id {viewer['id']}"))
    machine.wait_until_succeeds(operator(shlex.join([*cli, "status"]) + " | jq -e '.view_open | not'"))
    choose_private(name)
    reopened = json.loads(machine.wait_until_succeeds(viewer_query))
    assert reopened["id"] != viewer["id"], reopened
    same_window, = launch_state()["windows"]
    assert same_window["id"] == window["id"], (window, same_window)
    assert "*" in same_window["title"], same_window  # Mousepad marks the unsaved agent edit.
    machine.screenshot("managed-launch-mousepad")
    machine.log(f"Session {name}, private window {window['id']}, Niri viewers "
                f"{viewer['id']} and {reopened['id']}, foreground {front_id}")
    machine.succeed(operator(shlex.join([*cli, "stop"])))
    machine.succeed(operator(f"niri msg action close-window --id {front_id}"))
