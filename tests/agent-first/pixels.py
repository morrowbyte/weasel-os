with subtest("Read the inactive window's own pixels while the user's clipboard and focus stay intact"):
    def fixture_window(app_id: str, shell: str) -> int:
        launch = ["niri", "msg", "action", "spawn", "--", "foot", "--app-id", app_id,
                  "--title", app_id, "sh", "-c", shell]
        machine.succeed(operator(shlex.join(launch)))
        predicate = f'.[] | select(.is_focused and .app_id == "{app_id}") | .id'
        return int(machine.wait_until_succeeds(operator(
            "niri msg --json windows | jq -er " + shlex.quote(predicate)
        ), timeout=30))

    protected_id = fixture_window("weasel-protected", "exec sleep infinity")
    colored_id = fixture_window("weasel-color", r"printf '\033[48;2;17;85;153m\033[2J'; exec sleep infinity")
    machine.succeed(operator("niri msg action move-column-to-workspace-down --focus true"))
    machine.succeed(operator(f"niri msg action focus-window --id {human_id}"))
    inventory = json.loads(machine.succeed(operator("niri msg --json windows")))
    target = next(window for window in inventory if window["id"] == colored_id)
    foreground = next(window for window in inventory if window["is_focused"])
    assert foreground["id"] == human_id and target["workspace_id"] != foreground["workspace_id"]

    display_env = 'export WAYLAND_DISPLAY=$(systemctl --user show-environment | sed -n "s/^WAYLAND_DISPLAY=//p"); '
    machine.succeed(operator(display_env + "printf user-clipboard | wl-copy"))
    stream = f"{home}/pixel-focus.jsonl"
    machine.succeed(operator(
        f"systemd-run --user --unit=pixel-focus -p StandardOutput=file:{stream} "
        'env NIRI_SOCKET="$NIRI_SOCKET" niri msg --json event-stream'
    ))
    machine.wait_until_succeeds(f"grep -Fq human-draft {stream}")
    machine.send_chars("pixels", delay=0.15)
    request = ["weasel-computer-use", "call", "desktop_window_pixels"]
    capture = shlex.join([*request, json.dumps({"window_id": colored_id})])
    machine.succeed(operator(capture + ' > "$HOME/frame.json"'))
    meta = json.loads(machine.succeed(operator(
        """jq -er 'select(.isError == false) | .content[] | select(.type == "text") | .text' "$HOME/frame.json" """
    )))
    assert meta["window_id"] == colored_id and meta["input_authority"] is False, meta
    machine.succeed(operator(
        """jq -er '.content[] | select(.mimeType == "image/png") | .data' "$HOME/frame.json" | base64 -d > "$HOME/window.png" """
    ))
    # Lossless sRGB sample halfway across the ANSI true-color terminal.
    rgb = machine.succeed(operator(
        """magick "$HOME/window.png" -crop 1x1+%[fx:floor(w/2)]+%[fx:floor(h/2)] -depth 8 rgb:- | od -An -tu1"""
    )).split()
    assert rgb == ["17", "85", "153"], rgb
    code, output = machine.execute(operator(shlex.join([*request, json.dumps({"window_id": protected_id})])))
    denied = json.loads(output)
    assert denied["isError"] and all(block["type"] != "image" for block in denied["content"]), (code, denied)
    assert "protect" in denied["content"][0]["text"].lower(), denied
    assert machine.succeed(operator(display_env + "wl-paste --no-newline")) == "user-clipboard"
    finish_draft("pixels", "pixels", stream)
    machine.copy_from_machine(f"{home}/window.png")
    machine.copy_from_machine(stream)
