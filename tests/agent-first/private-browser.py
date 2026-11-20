def check_private_browser() -> None:
    name = "browser-test"
    cli = ["weasel-workspace", "--name", name]
    share = "/tmp/agent-first-browser-exchange"

    def browser_command(*args: str) -> str:
        return operator(shlex.join([*cli, *args]))

    def wait_page(title: str) -> int:
        query = '.windows[] | select(.focused and (.title | startswith($title))) | .id'
        return int(machine.wait_until_succeeds(
            browser_command("status") + " | " + shlex.join(["jq", "-er", "--arg", "title", title, query]),
            timeout=25,
        ))

    machine.succeed(browser_command("start", "--exchange", share))
    try:
        # Ordinary local documents keep this test offline. Navigation runs in
        # the unmodified Chromium renderer, with its normal browser sandbox.
        index = '<title>Team handbook</title><a style="position:fixed;inset:0" href="schedule.html">Schedule</a>'
        schedule = '<title>Release schedule</title><h1>Friday</h1>'
        for filename, html in [("index.html", index), ("schedule.html", schedule)]:
            machine.succeed(operator("printf %s " + shlex.quote(html) + " > " + share + "/" + filename))
        machine.succeed(browser_command("open", "--", "chromium", "--no-first-run",
                                        "--no-default-browser-check", "file:///exchange/index.html"))
        first = wait_page("Team handbook")
        machine.send_chars("brow", delay=0.12)
        machine.succeed(browser_command("click", "640", "360"))
        assert wait_page("Release schedule") == first
        machine.succeed(browser_command("key", "Alt+Left"))
        assert wait_page("Team handbook") == first
        machine.send_chars("ser", delay=0.12)
        finish_draft("browser", "browser")
    finally:
        machine.succeed(browser_command("stop"))


with subtest("Navigate real Chromium in the private seat while the user types in Niri"):
    check_private_browser()
