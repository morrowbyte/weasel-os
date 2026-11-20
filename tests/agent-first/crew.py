"""Run as operator inside the disposable NixOS guest, after the Crew menu test."""

import json
import os
import pwd
import shutil
import subprocess
import tempfile
import time
import tomllib
from pathlib import Path


def protocol_frames(logs):
    # .131 writes decoded frames to per-thread logs. Early startup notifications
    # may be routed to _global before thread/start returns its native thread ID.
    frames = []
    for path in logs.glob("events.*.log"):
        with path.open() as entries:
            for line in entries:
                timestamp, marker, payload = line.partition(" NTIVE: ")
                if not marker or not line.endswith("\n"):
                    continue
                row = json.loads(payload)
                if row.get("protocol") != "codex.app-server":
                    continue
                event = row["event"]
                if event["stage"] == "decoded":
                    frames.append(
                        (
                            timestamp,
                            row["providerSessionId"],
                            event["direction"],
                            event["payload"],
                        )
                    )
    frames.sort(key=lambda frame: frame[0])
    return [(session, direction, message) for _, session, direction, message in frames]


def check_provider(logs, app_thread):
    frames = protocol_frames(logs)
    (start,) = [
        (session, message["id"])
        for session, direction, message in frames
        if direction == "outgoing"
        and message.get("method") == "thread/start"
        and (message["params"].get("config") or {}).get(
            "shell_environment_policy.set.T3CODE_THREAD_ID"
        )
        == app_thread
    ]
    session_id, request_id = start
    incoming = [
        m for session, d, m in frames if session == session_id and d == "incoming"
    ]
    (created,) = [m for m in incoming if m.get("id") == request_id and "result" in m]
    native = created["result"]["thread"]["id"]
    requests = [
        m
        for session, d, m in frames
        if session == session_id
        and d == "outgoing"
        and m.get("params", {}).get("threadId") == native
    ]
    assert all(m.get("method") != "turn/start" for m in requests), (
        "unexpected model turn"
    )
    reads = {m["id"] for m in requests if m.get("method") == "thread/goal/get"}
    assert any(
        m.get("id") in reads and m.get("result") == {"goal": None} for m in incoming
    ), (
        "missing native thread/goal/get response",
        native,
    )
    status = {
        m["params"]["name"]: m["params"]["status"]
        for m in incoming
        if m.get("method") == "mcpServer/startupStatus/updated"
        and m["params"].get("threadId") == native
    }
    required = {"weasel_os", "weasel_desktop"}
    assert all(status.get(name) == "ready" for name in required), (
        "missing per-thread MCP readiness",
        {name: status.get(name) for name in required},
    )
    return {
        "thread": app_thread,
        "nativeThread": native,
        "providerSession": session_id,
        "providerRead": "thread/goal/get",
        "readySystemMcp": sorted(required),
        "systemMcpToolCallsTested": 0,
        "modelTurns": 0,
    }


def main():
    home = Path.home()
    assert pwd.getpwuid(os.getuid()).pw_name == "operator" and home == Path(
        "/home/operator"
    )
    system_mcp = tomllib.loads(Path("/etc/codex/config.toml").read_text())[
        "mcp_servers"
    ]
    for name, command in (
        ("weasel_os", "weasel"),
        ("weasel_desktop", "weasel-computer-use"),
    ):
        assert system_mcp[name]["args"] == ["mcp"]
        assert os.path.samefile(
            system_mcp[name]["command"], Path("/run/current-system/sw/bin") / command
        )
    (record,) = home.rglob("server-runtime.json")
    assert record.parent.name == "userdata"
    base = record.parent.parent
    installed = shutil.which("t3code-morrowbyte")
    assert installed, "missing Crew launcher in guest profile"
    package = Path(installed).resolve().parents[1]
    assert package.name.endswith("-t3code-morrowbyte-0.1.11-nightly.20261118.131"), (
        package
    )
    pid = json.loads(record.read_text())["pid"]
    assert os.path.samefile(
        Path("/proc", str(pid), "exe"), package / "lib/t3code-morrowbyte/t3code"
    ), "runtime record does not belong to the shipped Crew backend"
    provider = json.loads((record.parent / "settings.json").read_text())[
        "providerInstances"
    ]["codex"]["config"]
    assert provider["setupMode"] == "existing"
    assert provider["binaryPath"] == "/run/current-system/sw/bin/crew-codex"
    # Crew installs this shim into its own base directory even when the PATH
    # integration is disabled. It talks to the live server as the guest owner.
    cli = base / "bin/t3"
    project = tempfile.mkdtemp(prefix="crew-provider-", dir=home)
    subprocess.run(
        [cli, "project", "add", project, "--base-dir", base], check=True, timeout=30
    )
    completed = subprocess.run(
        [
            cli,
            "thread",
            "start",
            project,
            "/goal",
            "--base-dir",
            base,
            "--provider",
            "codex",
            "--model",
            "gpt-6-astra",
            "--wait",
            "--json",
        ],
        check=False,
        stdout=subprocess.PIPE,
        text=True,
        timeout=90,
    )
    assert completed.stdout.strip(), (
        f"t3 thread start produced no JSON (exit {completed.returncode})"
    )
    result = json.loads(completed.stdout)
    assert completed.returncode == 0 and result["status"] == "completed", (
        "Crew provider read failed",
        result.get("status"),
        result.get("error"),
    )
    assert result["reply"] == "No goal is set.", result["reply"]
    # Each adapter owns its app-server. The CLI and ChatGPT probes run
    # separate binaries outside the Crew server's process tree.
    processes = subprocess.check_output(["ps", "-eo", "pid=,ppid="], text=True)
    parents = dict(tuple(map(int, line.split())) for line in processes.splitlines())
    provider_pids = []
    for child in parents:
        ancestor = child
        while ancestor in parents and ancestor != pid:
            ancestor = parents[ancestor]
        if ancestor != pid:
            continue
        proc = Path("/proc", str(child))
        try:
            if (proc / "exe").samefile(provider["binaryPath"]) and b"app-server" in (
                proc / "cmdline"
            ).read_bytes().split(b"\0"):
                provider_pids.append(child)
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
    assert provider_pids, "Crew has no running app-server from its packaged Codex"
    # Crew batches log writes; inspect the same completed run during the flush.
    deadline = time.monotonic() + 15
    while True:
        try:
            receipt = check_provider(
                record.parent / "logs/provider", result["threadId"]
            )
            break
        except (AssertionError, ValueError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.25)
    print(
        json.dumps(
            {
                **receipt,
                "packagedProvider": provider["binaryPath"],
                "appServerPids": provider_pids,
            }
        )
    )


if __name__ == "__main__":
    main()
