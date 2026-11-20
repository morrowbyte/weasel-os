#!/usr/bin/env python3
import argparse
import asyncio
import base64
import json
import os
import sys
from contextlib import AsyncExitStack

from actions import execute
from catalog import CATALOG, catalog
from desktop import ActionError, Desktop
from server import serve

FOCUS_LABELS = {
    "unchanged": "Lesen",
    "no-focus-request": "Im Hintergrund",
    "changes-focus": "Wechselt den Fokus",
    "may-change-focus": "Kann den Fokus wechseln",
    "private-only": "Private Arbeitsfläche",
}
FIELD_LABELS = {
    "if_sha256": "Hash der vorherigen Aufnahme",
    "window_id": "Fenster", "workspace_id": "Arbeitsfläche", "label": "Feldbeschriftung",
    "expected_text": "Erwarteter Feldinhalt", "text": "Text", "app": "Programm",
    "name": "Name der privaten Arbeitsfläche", "exchange": "Austauschordner (optional)",
    "network": "Netzwerkzugang", "program": "Ausführbares Programm", "argv": "Argumente als JSON-Liste (optional)",
    "wait_window": "Exakte Wayland-App-ID des neuen Fensters", "key": "Taste oder Tastenkürzel",
    "x": "X-Koordinate", "y": "Y-Koordinate", "button": "Maustaste", "direction": "Scrollrichtung",
    "steps": "Scrollschritte", "filename": "Dateiname",
    "source": "Bisheriger Dateiname", "destination": "Neuer Dateiname",
    "session": "Sitzungskennung (optional)",
}
CHOICE_LABELS = {
    "zed": "Zed", "chrome": "Chromium / Chrome", "left": "Links", "middle": "Mitte",
    "right": "Rechts", "up": "Nach oben", "down": "Nach unten",
}
PRIVATE_GATE_HELP = {
    "private_cancel_all": "Bricht aktive Eingaben ab und sperrt neue Aufträge auf allen privaten Arbeitsflächen.",
    "private_resume": "Erlaubt neue Aufträge auf allen privaten Arbeitsflächen.",
}

def printable(value):
    return "".join(ch if ch.isprintable() else " " for ch in str(value))

async def prompt(label, rows=None):
    options = ["--dmenu", "--prompt", label + " › ", "--width", "70"]
    if rows is not None:
        if not rows:
            raise ValueError("Keine Ziele vorhanden")
        options.append("--index")
    process = await asyncio.create_subprocess_exec("fuzzel", *options,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
    data = "" if rows is None else "\n".join(printable(v) for v in rows) + "\n"
    output, _ = await process.communicate(data.encode())
    if process.returncode == 2:
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

def target_labels(source, items):
    if source == "windows":
        return [f"{w.get('title') or w.get('app_id') or 'Fenster'}  ·  "
                f"{w.get('app_id') or 'App'}  ·  #{w['id']}"
                + ("  ·  aktiv" if w.get("is_focused") else "") for w in items]
    return [f"{w.get('name') or w['idx']}  ·  {w.get('output') or 'Ausgabe'}  ·  #{w['id']}"
            + ("  ·  aktiv" if w.get("is_focused") else "") for w in items]


async def pick_id(key):
    source = "workspaces" if key == "workspace_id" else "windows"
    result, _ = await execute([{"action": source}])
    step = result["steps"][0]
    if not step["ok"]:
        raise ValueError(step["error"])
    items = step["result"]
    return items[await prompt(FIELD_LABELS[key], target_labels(source, items))]["id"]

def action_label(spec):
    return f"{spec['title']}  ·  {FOCUS_LABELS[spec['focus']]}"

async def pick_args(spec):
    args = {}
    for key, field in spec["inputSchema"]["properties"].items():
        if key in ("if_sha256", "session"):
            continue  # The palette acts on the current session and requests full frames.
        label = FIELD_LABELS[key]
        if key in ("window_id", "workspace_id"):
            args[key] = await pick_id(key)
        elif "enum" in field:
            choices = field["enum"]
            args[key] = choices[await prompt(label, [CHOICE_LABELS[value] for value in choices])]
        elif field["type"] == "boolean":
            args[key] = bool(await prompt(label, ["Standard für neue Sitzung (offline)", "Netzwerkzugang anfordern"]))
        else:
            if field.get("default"):
                label = f"{label} [{field['default']}]"
            value = await prompt(label)
            if value or "default" not in field:
                match field["type"]:
                    case "integer":
                        args[key] = int(value)
                    case "array":
                        try:
                            args[key] = json.loads(value)
                        except ValueError as exc:
                            raise ValueError('Argumente als JSON-Liste eingeben, etwa ["--option", "zwei Worte"].') from exc
                    case _:
                        args[key] = value
    return args

async def show_action_help(action):
    spec = CATALOG[action]
    rows = [FOCUS_LABELS[spec["focus"]]]
    if action in PRIVATE_GATE_HELP:
        rows.append(PRIVATE_GATE_HELP[action])
    for key, field in spec["inputSchema"]["properties"].items():
        label = FIELD_LABELS[key]
        rows.append(f"{label} · {field['description']}" if "description" in field else label)
    await show(spec["title"], rows)

async def palette():
    entries = list(CATALOG)
    labels = [action_label(spec) for spec in CATALOG.values()]
    try:
        index = await prompt("weasel", [*labels, "Private Arbeitsfläche als Fenster anzeigen",
                                       "Hilfe zu einer Aktion"])
        if index == len(entries):
            args = await pick_args(CATALOG["private_status"])
            viewer = await asyncio.create_subprocess_exec(
                "weasel-workspace", "--name", args.get("name", "default"), "view",
                stderr=asyncio.subprocess.PIPE)
            _, error = await viewer.communicate()
            if viewer.returncode:
                await show("Ansicht geschlossen", [error.decode(errors="replace").strip()
                                                   or "Die Ansicht konnte nicht geöffnet werden."])
            return
        if index == len(entries) + 1:
            await show_action_help(entries[await prompt("Hilfe", labels)])
            return
        action = entries[index]
        args = await pick_args(CATALOG[action])
        result, images = await execute([{"action": action, "args": args}])
        row = result["steps"][0]
        if not row["ok"]:
            await show("Gestoppt", [row["error"]])
        elif images:
            viewer = await asyncio.create_subprocess_exec("imv", "-", stdin=asyncio.subprocess.PIPE)
            await viewer.communicate(base64.b64decode(images[0]["data"]))
        elif action == "private_exchange_list":
            listing = row["result"]
            rows = listing["entries"] or ["Keine sichtbaren Dateinamen"]
            if listing["truncated"]:
                rows = [*rows, "Weitere Einträge möglich (Inventargrenze erreicht)"]
            await show("Austauschordner", rows)
        elif action == "private_exchange_rename_noreplace":
            receipt = row["result"]
            await show("Datei umbenannt", [f"{receipt['source']} → {receipt['destination']}"])
        elif action == "private_start":
            session = row["result"]
            network = "Netzwerkzugang erlaubt" if session["network"] else "Offline"
            exchange = f"/exchange ↔ {session['exchange']}" if session["exchange"] else "Kein Austauschordner"
            await show("Private Arbeitsfläche bereit", [network, exchange])
        elif action == "private_open":
            window = row["result"]["window"]
            await show("Privates Fenster geöffnet", [f"{window.get('title') or window['app_id']} · Fenster {window['id']}"])
        elif action in ("windows", "workspaces"):
            await show(CATALOG[action]["title"], target_labels(action, row["result"]))
        elif action == "private_cancel_all":
            await show("Private Eingaben gesperrt", ["Gilt für alle privaten Arbeitsflächen."])
        elif action == "private_resume":
            await show("Private Eingaben freigegeben", ["Neue Aufträge sind auf allen privaten Arbeitsflächen möglich."])
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
    sub.add_parser("resume-desktop", help="Den aktuellen Desktop nach Start oder Übernahme ausdrücklich freigeben")
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
    elif args.command == "resume-desktop":
        async with AsyncExitStack() as stack:
            desktop = Desktop(stack)
            status = await desktop.call("desktop_status")
            binding = {"expected_epoch": status["epoch"], "expected_session_id": status["session_id"]}
            # The user's hotkey must be released. Another stop invalidates this request.
            deadline = asyncio.get_running_loop().time() + 2
            while status["takeover_latched"] and not status["resume_input_readiness"]["ready"]:
                if asyncio.get_running_loop().time() >= deadline:
                    raise ActionError("Freigabe erfordert einen bereiten Eingabemonitor und losgelassene Tasten")
                await asyncio.sleep(0.05)
                status = await desktop.call("desktop_status")
                if (status["session_id"], status["epoch"]) != (binding["expected_session_id"], binding["expected_epoch"]):
                    raise ActionError("Die Desktop-Sitzung oder der Stopp hat sich geändert")
            print(json.dumps(await desktop.call("desktop_resume", binding)))
    elif args.command == "mcp":
        await serve()
    elif args.command == "catalog":
        print(json.dumps(catalog(), ensure_ascii=False))
    elif args.command == "help":
        if args.action:
            print(json.dumps(CATALOG[args.action], ensure_ascii=False, indent=2))
        else:
            for name, action in CATALOG.items():
                print(f"{name:20} {action_label(action)}")
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
    except (OSError, ValueError, ActionError) as error:
        print(json.dumps({"status": "invalid", "error": str(error), "automatic_replay": False}))
        sys.exit(2)
    except KeyboardInterrupt:
        sys.exit(130)
