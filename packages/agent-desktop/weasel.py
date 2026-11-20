#!/usr/bin/env python3
import argparse
import asyncio
import base64
import json
import os
import sys

from actions import execute
from catalog import CATALOG, catalog
from server import serve

def printable(value):
    return "".join(ch if ch.isprintable() else " " for ch in str(value))

async def prompt(label, rows=None):
    options = ["--dmenu", "--prompt", label + " › "]
    if rows is not None:
        if not rows:
            raise ValueError("Keine Ziele vorhanden")
        options.append("--index")
    process = await asyncio.create_subprocess_exec("fuzzel", *options,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
    data = "" if rows is None else "\n".join(printable(v) for v in rows) + "\n"
    output, _ = await process.communicate(data.encode())
    if process.returncode == 1:
        raise KeyboardInterrupt
    if process.returncode:
        raise ValueError(f"fuzzel: Exit {process.returncode}")
    text = output.decode().removesuffix("\n")
    if rows is None:
        return text
    index = int(text)
    if not 0 <= index < len(rows):
        raise ValueError("Ungültige Auswahl")
    return index

async def show(label, rows):
    # The result view is read-only. Return and Escape both dismiss it.
    await prompt(label, rows or ["Keine Einträge"])

async def pick_id(key):
    source = "workspaces" if key == "workspace_id" else "windows"
    result, _ = await execute([{"action": source}])
    step = result["steps"][0]
    if not step["ok"]:
        raise ValueError(step["error"])
    items = step["result"]
    if source == "windows":
        labels = [f"{w['id']}  {w.get('app_id', '')}  {w.get('title', '')}" for w in items]
    else:
        labels = [f"{w['id']}  {w.get('output', '')} · {w.get('name') or w['idx']}" for w in items]
    return items[await prompt(key, labels)]["id"]

async def palette():
    entries = list(CATALOG)
    labels = [f"{v['title']}  ·  {v['focus']}" for v in CATALOG.values()]
    try:
        index = await prompt("weasel", [*labels, "Hilfe zu einer Aktion"])
        if index == len(entries):
            action = entries[await prompt("Hilfe", labels)]
            await show(action, json.dumps(CATALOG[action], ensure_ascii=False, indent=2).splitlines())
            return
        action = entries[index]
        args = {}
        for key, spec in CATALOG[action]["inputSchema"]["properties"].items():
            if key in ("window_id", "workspace_id"):
                args[key] = await pick_id(key)
            elif "enum" in spec:
                choices = spec["enum"]
                args[key] = choices[await prompt(key, choices)]
            else:
                label = key if "default" not in spec else f"{key} [{spec['default']}]"
                value = await prompt(label)
                if value or "default" not in spec:
                    args[key] = int(value) if spec["type"] == "integer" else value
        result, images = await execute([{"action": action, "args": args}])
        row = result["steps"][0]
        if not row["ok"]:
            await show("Gestoppt", [row["error"]])
        elif images:
            viewer = await asyncio.create_subprocess_exec("imv", "-", stdin=asyncio.subprocess.PIPE)
            await viewer.communicate(base64.b64decode(images[0]["data"]))
        elif CATALOG[action]["class"] == "read":
            await show(CATALOG[action]["title"],
                       json.dumps(row["result"], ensure_ascii=False, indent=2).splitlines())
    except ValueError as exc:
        await show("weasel", [str(exc)])

def parse_args():
    parser = argparse.ArgumentParser(prog="weasel", description="Niri-Desktop und private Arbeitsflächen")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("palette", help="Tastaturpalette mit Aktionen und Hilfe öffnen (Standard)")
    sub.add_parser("catalog", help="Maschinenlesbaren JSON-Katalog ausgeben")
    sub.add_parser("mcp", help="MCP über stdio starten")
    sub.add_parser("desktop-mcp", help="Vollen originalen Actor-MCP starten")
    help_cmd = sub.add_parser("help", help="Aktionen mit Fokuswirkung oder eine Eingabemaske anzeigen")
    help_cmd.add_argument("action", nargs="?", choices=CATALOG)
    run = sub.add_parser("run", help="Einzelaktion mit JSON-Objekt ausführen")
    run.add_argument("action", choices=CATALOG)
    run.add_argument("args", nargs="?", default="{}", help="JSON oder - für stdin")
    seq = sub.add_parser("compose", help="JSON-Array ausführen, beim ersten Fehler stoppen")
    seq.add_argument("--request-id", help="Einmalige Sequenz-ID in dieser Benutzersitzung")
    seq.add_argument("steps", nargs="?", default="-", help="JSON oder - für stdin (Standard)")
    return parser.parse_args()

def read_json(value):
    if value == "-":
        value = sys.stdin.buffer.read((1 << 20) + 1)
        if len(value) > 1 << 20:
            raise ValueError("Eingabe überschreitet 1 MiB")
    return json.loads(value)

async def main():
    args = parse_args()
    if args.command in (None, "palette"):
        await palette()
    elif args.command == "desktop-mcp":
        os.execlp("weasel-computer-use", "weasel-computer-use", "mcp")
    elif args.command == "mcp":
        await serve()
    elif args.command == "catalog":
        print(json.dumps(catalog(), ensure_ascii=False))
    elif args.command == "help":
        if args.action:
            print(json.dumps(CATALOG[args.action], ensure_ascii=False, indent=2))
        else:
            for name, action in CATALOG.items():
                print(f"{name:20} {action['title']} ({action['class']}, {action['focus']})")
    else:
        steps = (read_json(args.steps) if args.command == "compose"
                 else [{"action": args.action, "args": read_json(args.args)}])
        result, images = await execute(steps, getattr(args, "request_id", None))
        if images:
            result["images"] = images
        print(json.dumps(result, ensure_ascii=False))
        return int(result["status"] != "completed")
    return 0

if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except (OSError, ValueError) as error:
        print(json.dumps({"status": "invalid", "error": str(error), "automatic_replay": False}))
        sys.exit(2)
    except KeyboardInterrupt:
        sys.exit(130)
