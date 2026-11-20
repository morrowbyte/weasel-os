with subtest("Move and drag in private Mousepad while the foreground draft keeps receiving input"):
    workspace("start", "--exchange", exchange)
    try:
        drag_file = f"{exchange}/drag-target.txt"
        pointer_log = f"{exchange}/drag-motion.txt"
        # Identical one-word lines put a whole word at the test height across
        # font sizes. Starting beyond the word also works with a left gutter.
        machine.succeed(operator(f"for i in {{1..64}}; do echo target; done > {drag_file}"))
        mousepad_bin, awk_bin = machine.succeed(operator(
            'readlink -f "$(command -v mousepad)" "$(command -v awk)"'
        )).splitlines()
        workspace("open", "--", "sh", "-c",
                  """WAYLAND_DEBUG=client "$1" --disable-server /exchange/drag-target.txt 2>&1 |
                  "$2" '/wl_pointer[@#][0-9]+[.]motion[(]/ {print; fflush()}' > /exchange/drag-motion.txt""",
                  "drag-probe", mousepad_bin, awk_bin)
        machine.wait_until_succeeds(operator(
            shlex.join([*workspace_cli, "status"])
            + """ | jq -e '.windows[] | select(.focused and (.title | contains("drag-target.txt")))'"""
        ), timeout=45)
        for letter in "before":
            qemu_key(letter, True, False)
        workspace("move", "420", "360")
        machine.wait_until_succeeds(f"test -s {pointer_log}", timeout=10)
        motion_count = int(machine.succeed(f"wc -l < {pointer_log}"))
        workspace("move", "500", "360")
        machine.wait_until_succeeds(f'test "$(wc -l < {pointer_log})" -gt {motion_count}', timeout=10)

        drag_steps = [
            {"action": "private_drag", "args": {"name": "integration", "start_x": 500,
             "start_y": 360, "end_x": 0, "end_y": 360, "button": "left"}},
            {"action": "private_text", "args": {"name": "integration", "text": "replacement"}},
            {"action": "private_key", "args": {"name": "integration", "key": "Ctrl+s"}},
        ]
        drag_reply = json.loads(machine.succeed(operator(shlex.join(
            ["weasel", "compose", json.dumps(drag_steps)]
        ))))
        assert drag_reply["status"] == "completed", drag_reply
        machine.wait_until_succeeds(f"grep -qx replacement {drag_file}", timeout=30)
        saved = machine.succeed(f"cat {drag_file}")
        assert saved.count("replacement\n") == 1 and saved.replace("replacement\n", "target\n") == "target\n" * 64, saved
        for letter in "after":
            qemu_key(letter, True, False)
        finish_draft("drag", "beforeafter")
        machine.copy_from_machine(drag_file)
        machine.copy_from_machine(f"{home}/draft-drag.txt")
    finally:
        workspace("stop")
