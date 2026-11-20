import json
import shlex
import time
from typing import Any


home = "/home/operator"
events_file = f"{home}/niri-events.jsonl"
exchange = "/tmp/agent-first-test-exchange"
workspace_cli = ["weasel-workspace", "--name", "integration"]


def operator(command: str) -> str:
    session = (
        "set -eu -o pipefail; export XDG_RUNTIME_DIR=/run/user/$(id -u); "
        'export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"; '
        'set -- "$XDG_RUNTIME_DIR"/niri.*.sock; export NIRI_SOCKET="$1"; '
    )
    return shlex.join(["su", "-", "operator", "-c", session + command])


def workspace(*args: str) -> dict:
    return json.loads(machine.succeed(operator(shlex.join([*workspace_cli, *args]))))


def actor(tool: str, **arguments: Any) -> dict:
    command = shlex.join(["weasel-computer-use", "call", tool, json.dumps(arguments)])
    result = json.loads(machine.succeed(operator(command)))
    data = json.loads(
        next(block["text"] for block in result["content"] if block["type"] == "text")
    )
    assert not result["isError"], data
    return data


def qemu_key(key: str, *states: bool) -> None:
    # Keep Shift held across guest commands without relying on a timer or on
    # the speed of QEMU. These events enter the VM via its emulated keyboard.
    assert machine.qmp_client is not None
    args: dict[str, Any] = {
        "events": [
            {
                "type": "key",
                "data": {"down": down, "key": {"type": "qcode", "data": key}},
            }
            for down in states
        ]
    }
    machine.qmp_client.send("input-send-event", args)


def finish_draft(phase: str, expected_line: str) -> None:
    qemu_key("ret", True, False)
    marker = f"draft-{phase}-complete"
    machine.wait_until_succeeds(f"grep -Fq {marker} {events_file}", timeout=30)
    actual = machine.succeed(f"cat {home}/draft-{phase}.txt")
    assert actual == expected_line + "\n", (phase, actual)
    # Parse every event through the title marker, including all focus losses
    # to null and all intermediate windows. Never resubscribe after startup.
    lines = machine.succeed(f"cat {events_file}").splitlines()
    assert any("WindowsChanged" in json.loads(line) for line in lines)
    for line in lines:
        event = json.loads(line)
        if "WindowsChanged" in event:
            focused = [
                w["id"] for w in event["WindowsChanged"]["windows"] if w["is_focused"]
            ]
            assert focused == [human_id], event
        if "WindowFocusChanged" in event:
            assert event["WindowFocusChanged"]["id"] == human_id, event
        if "WindowClosed" in event:
            assert event["WindowClosed"]["id"] != human_id, event
        if "OverviewOpenedOrClosed" in event:
            assert not event["OverviewOpenedOrClosed"]["is_open"], event
        if "WindowOpenedOrChanged" in event:
            w = event["WindowOpenedOrChanged"]["window"]
            assert w["is_focused"] == (w["id"] == human_id), event
            if w["id"] == human_id and w["title"] == marker:
                return
    raise AssertionError(f"missing end marker {marker} for window {human_id}")


machine.start()
machine.wait_for_unit("greetd.service")
try:
    machine.wait_until_succeeds(
        operator("niri msg --json outputs | jq -e 'length == 1'"), timeout=90
    )
except Exception:
    machine.log(machine.succeed("journalctl -b -u greetd --no-pager -n 60"))
    machine.log(machine.succeed("journalctl -b --user-unit=niri --no-pager -n 45"))
    raise

with subtest("Set up a Mousepad background target and an active foot draft on Niri"):
    # Dismiss Niri's first-login hotkey overlay before any draft is open.
    machine.send_key("spc", delay=0.15)
    # Seed known short contents. Only the application's UI changes these files thereafter.
    machine.succeed(operator('printf actorbefore > "$HOME/actor-target.txt"'))
    machine.succeed(
        operator(
            "niri msg action spawn -- mousepad --disable-server /home/operator/actor-target.txt"
        )
    )
    target_id = int(
        machine.wait_until_succeeds(
            operator("""niri msg --json windows | jq -er '.[] | select(.is_focused and
            (.title | contains("actor-target.txt"))) | .id'"""),
            timeout=45,
        )
    )
    machine.succeed(
        operator(
            "niri msg action spawn -- foot --app-id=human-draft --title=draft-ready @draft@"
        )
    )
    human_id = int(
        machine.wait_until_succeeds(
            operator("""niri msg --json windows | jq -er '.[] | select(.is_focused and
            .app_id == "human-draft") | .id'"""),
            timeout=45,
        )
    )
    machine.succeed(operator("niri msg action move-column-to-workspace-down --focus true"))
    separated = json.loads(machine.succeed(operator("niri msg --json windows")))
    workspaces = {window["id"]: window["workspace_id"] for window in separated}
    assert workspaces[target_id] != workspaces[human_id]
    machine.succeed(
        operator(
            f"systemd-run --user --unit=test-focus-events -p StandardOutput=file:{events_file} "
            'env NIRI_SOCKET="$NIRI_SOCKET" niri msg --json event-stream'
        )
    )
    machine.wait_until_succeeds(f"grep -Fq human-draft {events_file}", timeout=30)
    machine.send_chars("draft", delay=0.15)

with subtest(
    "Edit and save in private Mousepad while the foreground keyboard holds Shift"
):
    result = workspace("start", "--exchange", exchange)
    assert result["running"] and result["network"] is False, result
    # The only shared directory is a fresh, explicit test exchange.
    machine.succeed(operator(f"printf 'seed\\n' > {exchange}/private-target.txt"))
    workspace(
        "open", "--", "mousepad", "--disable-server", "/exchange/private-target.txt"
    )
    machine.wait_until_succeeds(
        operator(
            shlex.join([*workspace_cli, "status"])
            + """ | jq -e '
        .windows[] | select(.focused and (.title | contains("private-target.txt")))'"""
        ),
        timeout=45,
    )
    qemu_key("shift", True)
    try:
        qemu_key("a", True, False)
        workspace("click", "640", "360")
        workspace("key", "Ctrl+Home")
        workspace("text", "private edit ")
        qemu_key("b", True, False)
        workspace("key", "Ctrl+s")
        machine.wait_until_succeeds(
            f"printf 'private edit seed\\n' | cmp - {exchange}/private-target.txt",
            timeout=30,
        )
    finally:
        qemu_key("shift", False)
    workspace("screenshot", f"{home}/private-mousepad.png")
    workspace("stop")
    assert workspace("status")["running"] is False
    finish_draft("private", "draftAB")
    machine.copy_from_machine(f"{home}/private-mousepad.png")
    machine.copy_from_machine(f"{exchange}/private-target.txt")
    machine.copy_from_machine(f"{home}/draft-private.txt")

with subtest(
    "Existing actor edits the unfocused Niri Mousepad via AT-SPI with buffer readback"
):
    semantic_args = {"window_id": target_id, "background": True}
    tree = actor("desktop_semantic_direct", **semantic_args)
    assert tree["background"] and tree["elements_complete"], tree
    fields = [
        e
        for e in tree["elements"]
        if e["capabilities"]["set_value"] and e["text_excerpt"] == "actorbefore"
    ]
    assert len(fields) == 1, tree
    # Ordinary foreground input after handle acquisition challenges background routing.
    machine.send_chars("human", delay=0.15)
    action = {
        "kind": "semantic_set_value",
        "handle_id": fields[0]["handle_id"],
        "expected_text": "actorbefore",
        "text": "actorafter",
    }
    started = time.perf_counter()
    result = actor(
        "desktop_act",
        observation_id=tree["observation_id"],
        window_id=target_id,
        task_id="vm-background-edit",
        actions=[action],
        include_image=False,
        observe_after=True,
    )
    assert result["background_semantic"] and result["completed_actions"] == 1, result
    assert result["status"] == "dispatched" and result["actor_release_confirmed"], (
        result
    )
    effect = result["effects"][0]["details"]
    assert (
        effect["route"] == "direct_atspi_object" and effect["exact_buffer_bytes_match"]
    ), effect
    elapsed = 1000 * (time.perf_counter() - started)
    machine.log(f"Action through target readback, including driver shell transport: {elapsed:.1f} ms")
    assert effect["focus_events"] == "no_change_observed", effect
    fresh = result["after_observation"]
    assert fresh["niri_window_id"] == target_id, fresh
    assert fresh["elements_complete"] and any(
        e["editable"] and e["text_excerpt"] == "actorafter" for e in fresh["elements"]
    ), fresh
    finish_draft("actor", "human")
    machine.log(
        f"Window {target_id}: {effect['route']} returned {effect['status']}; "
        f"fresh AT-SPI readback matched actorafter while window {human_id} retained focus"
    )
    machine.copy_from_machine(f"{home}/draft-actor.txt")
    machine.copy_from_machine(events_file)
