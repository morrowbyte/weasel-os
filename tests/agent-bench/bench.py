#!/usr/bin/env python3
"""Private Boat CLI for one NixOS test-driver guest and one model attempt."""
import argparse
import json
import os
import socket
import subprocess
import sys
from pathlib import Path


def start(driver, run, family):
    run = run.resolve()
    source = Path(__file__).resolve().parent
    executable = driver.resolve() / "bin/nixos-test-driver"
    references = subprocess.check_output(
        ["nix-store", "--query", "--references", str(driver.resolve())], text=True,
    ).splitlines()
    (config,) = [Path(ref) for ref in references if ref.endswith("-driverConfiguration.json")]
    settings = json.loads(config.read_text())
    settings["test_script"] = str(source / "driver.py")
    run.mkdir(mode=0o700)
    config = run / "driver.json"
    config.write_text(json.dumps(settings))
    temp = run / "tmp"
    temp.mkdir(mode=0o700)
    os.chdir(run)
    os.environ.update(BENCH_SOURCE=str(source), BENCH_RUN=str(run), BENCH_FAMILY=family,
                      TMPDIR=str(temp), XDG_RUNTIME_DIR=str(temp))
    # Driver output and screenshots are private, never forwarded through the model transcript.
    with (run / "driver.log").open("x") as output:
        os.dup2(output.fileno(), 1)
        os.dup2(output.fileno(), 2)
    os.execv(executable, [str(executable), "--config", str(config), "-o", str(run)])


def send(run, message):
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(125)
        client.connect(str(run / "control.sock"))
        client.sendall(json.dumps(message).encode() + b"\n")
        with client.makefile("rb") as stream:
            result = json.loads(stream.readline(2 << 20))
    print(json.dumps(result, ensure_ascii=False))
    if "error" in result or result.get("success") is False:
        return 1
    return result.get("cli_exit", 0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    cmds = parser.add_subparsers(dest="command", required=True)
    launch = cmds.add_parser("start", help="start one fresh guest and wait for its model")
    launch.add_argument("driver", type=Path)
    launch.add_argument("run", type=Path)
    launch.add_argument("family", choices=["edit", "files"])
    act = cmds.add_parser("compose", help="submit a weasel compose JSON array on stdin")
    act.add_argument("run", type=Path)
    act.add_argument("id", help="unique id; preserve it on uncertain submission")
    end = cmds.add_parser("finish", help="independently verify files, foreground draft and focus")
    end.add_argument("run", type=Path)
    args = parser.parse_args()
    if args.command == "start":
        start(args.driver, args.run, args.family)
    if args.command == "finish":
        return send(args.run, {"finish": True})
    return send(args.run, {"id": args.id, "steps": json.load(sys.stdin)})


if __name__ == "__main__":
    os.umask(0o077)
    sys.exit(main())
