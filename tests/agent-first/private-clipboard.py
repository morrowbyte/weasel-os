with subtest("Paste and read exact Unicode text in Mousepad without changing the human clipboard"):
    def verify_private_clipboard() -> None:
        name = "clip-test"
        share = "/tmp/clipboard-test-exchange"
        cli = ["weasel-workspace", "--name", name]
        sample = "Freitag · Grüße 界\nZeile zwei\n"
        machine.succeed(operator("printf human-selection | wl-copy"))
        machine.succeed(operator(shlex.join([*cli, "start", "--exchange", share])))
        try:
            filename = share + "/clipboard.txt"
            machine.succeed(operator("touch " + filename))
            machine.succeed(operator(shlex.join([*cli, "open", "--", "mousepad", "--disable-server",
                                                 "/exchange/clipboard.txt"])))
            machine.wait_until_succeeds(operator(shlex.join([*cli, "status"]) +
                """ | jq -e '.windows[] | select(.focused and (.title | contains("clipboard.txt")))'"""))
            steps = [
                ("private_clipboard_write", {"text": sample}),
                ("private_key", {"key": "Ctrl+v"}),
                ("private_key", {"key": "Ctrl+s"}),
                ("private_clipboard_write", {"text": "old selection"}),
                ("private_key", {"key": "Ctrl+a"}),
                ("private_key", {"key": "Ctrl+c"}),
                ("private_clipboard_read", {}),
            ]
            actions = [{"action": action, "args": {"name": name, **args}} for action, args in steps]
            command = shlex.join(["weasel", "compose", json.dumps(actions)])
            result = json.loads(machine.succeed(operator(command)))
            assert result["status"] == "completed", result
            observed = result["steps"][-1]["result"]["results"][0]
            assert (observed["text"], observed["bytes"]) == (sample, len(sample.encode())), observed
            machine.succeed("printf %s " + shlex.quote(sample) + " | cmp - " + filename)
            assert machine.succeed(operator("wl-paste --no-newline")) == "human-selection"
        finally:
            machine.succeed(operator(shlex.join([*cli, "stop"])))

    verify_private_clipboard()
