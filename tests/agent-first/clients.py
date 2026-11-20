# Appended to desktop.py in the same disposable operator/Niri guest.
from pathlib import PurePosixPath


def client_layer(visible: bool) -> str:
    # Layer-shell popups do not appear in the regular Niri window list.
    predicate = 'any(.[]; .namespace == "launcher" or .namespace == "fuzzel")'
    if not visible:
        predicate += " | not"
    return operator("niri msg --json layers | jq -e " + shlex.quote(predicate))


def launch_menu_entry(entry: str, label: str, app_ids: list[str]) -> dict:
    desktop_file = f"/run/current-system/sw/share/applications/{entry}.desktop"
    machine.succeed("grep -Fx " + shlex.quote("Name=" + label) + " " + desktop_file)
    before = json.loads(machine.succeed(operator("niri msg --json windows")))
    machine.succeed(operator("niri msg action spawn -- fuzzel"))
    machine.wait_until_succeeds(client_layer(True), timeout=30)
    machine.send_chars(label.lower())
    machine.send_key("ret")
    old_ids = [w["id"] for w in before]
    select = """.[] | .id as $id | select(($old | index($id)) == null)
        | (.app_id // "" | ascii_downcase) as $app
        | select(($apps | index($app)) != null and .is_focused)"""
    command = "niri msg --json windows | " + shlex.join(
        [
            "jq",
            "-ec",
            "--argjson",
            "old",
            json.dumps(old_ids),
            "--argjson",
            "apps",
            json.dumps(app_ids),
            select,
        ]
    )
    try:
        window = json.loads(machine.wait_until_succeeds(operator(command), timeout=90))
    except Exception:
        machine.log(machine.succeed(operator("niri msg --json windows")))
        raise
    machine.log(f"{desktop_file}: {window}")
    machine.screenshot("menu-" + entry)
    return window


clients_seen = {}
with subtest("Codex CLI menu opens a new foot running the shipped Codex executable"):
    codex_window = launch_menu_entry("codex-cli", "Codex CLI", ["foot"])
    foot_pid = int(codex_window["pid"])
    # foot's default app_id is shared by terminals. Bind this specific seat
    # window to its child and to the executable delivered by the CLI package.
    package = PurePosixPath(machine.succeed("readlink -f /run/current-system/sw/bin/codex").strip()).parents[1]
    try:
        # The independent guest oracle may inspect the executable of non-dumpable
        # child processes. Neither the client nor the desktop actor gets this access.
        machine.wait_until_succeeds(
            f"for child in $(pgrep -P {foot_pid}); do "
            'exe=$(readlink "/proc/$child/exe") || continue; '
            f'case "$exe" in {package}/bin/.codex-wrapped|{package}/bin/codex) '
            'echo "$exe"; exit 0;; esac; done; exit 1',
            timeout=20,
        )
    except Exception:
        machine.log(machine.succeed("ps -e -o pid,ppid,user,comm | tail -140"))
        machine.screenshot("codex-process-check")
        raise
    clients_seen["Codex CLI"] = codex_window

with subtest("ChatGPT menu opens the desktop application"):
    clients_seen["ChatGPT"] = launch_menu_entry(
        "chatgpt",
        "ChatGPT",
        ["chatgpt", "codex", "com.openai.chat", "com.openai.codex"],
    )

with subtest("Crew menu opens the desktop application"):
    clients_seen["Crew"] = launch_menu_entry(
        "t3code-morrowbyte",
        "Crew",
        ["com.morrowbyte.t3code"],
    )

with subtest("Super+Space opens the action palette and Enter reads the window list"):
    machine.wait_until_succeeds(client_layer(False), timeout=15)
    machine.send_key("meta_l-spc")
    machine.wait_until_succeeds(client_layer(True), timeout=15)
    machine.succeed(operator("pgrep -f '[f]uzzel --dmenu --prompt weasel ›'"))
    machine.screenshot("palette-actions")
    machine.send_chars("laufende fenster")
    machine.send_key("ret")
    machine.wait_until_succeeds(
        operator("pgrep -f '[f]uzzel --dmenu --prompt Laufende Fenster anzeigen ›'"),
        timeout=30,
    )
    machine.wait_until_succeeds(client_layer(True), timeout=15)
    machine.screenshot("palette-windows")
    machine.send_key("esc")
    machine.wait_until_succeeds(client_layer(False), timeout=15)

with subtest("Crew's local server returns its environment descriptor without a login"):
    # Read the fresh guest's own runtime record. Only select PID and port.
    descriptor = machine.wait_until_succeeds(
        operator(
            'record=$(find "$HOME" -type f -name server-runtime.json -print -quit); '
            'test -n "$record"; port=$(jq -er .port "$record"); pid=$(jq -er .pid "$record"); '
            'ss -H -ltnp "sport = :$port" | grep -Fq "pid=$pid,"; '
            'curl --noproxy "*" -fsS --max-time 3 '
            '"http://127.0.0.1:$port/.well-known/t3/environment" | '
            """jq -ce '{environmentId,serverVersion} | select(
            (.environmentId | type == "string" and length > 0) and
            (.serverVersion | type == "string" and length > 0))'"""
        ),
        timeout=90,
    )
    machine.log("Crew local server (unauthenticated GET): " + descriptor.strip())


# Run inside the guest so stdio talks to the packaged binaries and their own
# stdio MCP children. An empty CODEX_HOME leaves only the shipped system
# defaults available. No tool or server configuration is injected.
client_probe = r"""
import json
import os
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

binary, expected_json = sys.argv[1:]
system = tomllib.loads(Path("/etc/codex/config.toml").read_text())["mcp_servers"]
for name, program in (("weasel_os", "weasel"), ("weasel_desktop", "weasel-computer-use")):
    assert system[name]["args"] == ["mcp"], name
    assert os.path.samefile(system[name]["command"], "/run/current-system/sw/bin/" + program), name

with tempfile.TemporaryDirectory(prefix="codex-backend-") as directory:
    env = dict(os.environ, CODEX_HOME=directory)
    with subprocess.Popen(
        [binary, "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        text=True, cwd=directory, env=env,
    ) as server:
        def send(message):
            server.stdin.write(json.dumps(message) + "\n")
            server.stdin.flush()

        def rpc(serial, method, params):
            send({"id": serial, "method": method, "params": params})
            for line in server.stdout:
                response = json.loads(line)
                if response.get("id") == serial:
                    assert "result" in response, (method, response.get("error"))
                    return response["result"]
            raise RuntimeError(f"{method}: backend closed stdout")

        try:
            hello = rpc(1, "initialize", {
                "clientInfo": {"name": "guest_acceptance", "version": "1"},
                "capabilities": {"experimentalApi": True},
            })
            send({"method": "initialized"})
            thread = rpc(2, "thread/start", {
                "cwd": directory, "ephemeral": True,
                "approvalPolicy": "never", "sandbox": "read-only",
            })["thread"]["id"]
            listing = rpc(3, "mcpServerStatus/list", {"threadId": thread, "limit": 100})
            tools = {row["name"]: row["tools"] for row in listing["data"]}
            assert "windows" in tools["weasel_os"] and "desktop_windows" in tools["weasel_desktop"]
            result = rpc(4, "mcpServer/tool/call", {
                "threadId": thread, "server": "weasel_os", "tool": "windows", "arguments": {},
            })
            assert not result.get("isError"), result
            body = json.loads(next(row["text"] for row in result["content"] if row["type"] == "text"))
            step, = body["steps"]
            assert body["status"] == "completed" and step["action"] == "windows" and step["ok"], body
            observed = {str(w["id"]): w["app_id"] for w in step["result"]}
            expected = json.loads(expected_json)
            assert expected.items() <= observed.items(), (expected, observed)
            skills = rpc(5, "skills/list", {"cwds": [directory]})
            assert any(skill["name"] == "weasel-desktop" and skill.get("enabled", True)
                       for entry in skills["data"] for skill in entry["skills"]), skills
            print(json.dumps({"backend": binary, "userAgent": hello["userAgent"],
                              "readOnlyTool": "weasel_os.windows", "matchedWindows": expected,
                              "systemSkill": "weasel-desktop", "modelTurns": 0}))
        finally:
            server.stdin.close()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
"""

chatgpt_path = machine.succeed(operator('readlink -f "$(command -v chatgpt)"')).strip()
chatgpt_backend = PurePosixPath(chatgpt_path).parents[1] / "lib/chatgpt/resources/codex"
client_ids = {w["id"]: w["app_id"] for w in clients_seen.values()}
probe_path = "/home/operator/client-backend-probe.py"
machine.succeed(operator(f"cat > {probe_path} <<'PYPROBE'\n{client_probe}\nPYPROBE"))
for backend_label, backend_path in [
    ("Codex CLI", "/run/current-system/sw/bin/codex"),
    ("ChatGPT bundled Codex", str(chatgpt_backend)),
]:
    with subtest(
        backend_label + " app-server reads the three real windows through system MCP"
    ):
        command = shlex.join(
            [
                "timeout",
                "90",
                "python3",
                probe_path,
                backend_path,
                json.dumps(client_ids),
            ]
        )
        receipt = machine.succeed(operator(command))
        machine.log(receipt.strip())

with subtest("Start the pinned browser and editor through the same action available to agents"):
    for app in ("chrome", "zed"):
        command = shlex.join(["weasel", "run", "app_launch", json.dumps({"app": app})])
        code, output = machine.execute(operator(command), timeout=60)
        result = json.loads(output)
        assert code == 0 and result["status"] == "completed", result
        outcome = result["steps"][0]["result"]
        assert outcome["verified"] is True, outcome
        receipt = outcome["receipt"]
        window = receipt["mapped_window"]
        assert window["pid"] == receipt["effects"][0]["details"]["spawn"]["pid"]
        machine.log(f"{app} opened window {window['id']} from the pinned program, app_id={window['app_id']}")
