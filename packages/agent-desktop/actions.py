import asyncio
import base64
import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
import time
import uuid
from contextlib import AsyncExitStack, closing
from pathlib import Path

from catalog import CATALOG, REQUEST_ID, plan
from desktop import ActionError, Desktop, PIPE

# Same sibling layout in the source checkout and the Nix package.
sys.path.append(str(Path(__file__).resolve().parents[1] / "agent-workspace"))
from workspace_client import BrokerError, exchange_request

PRIVATE_INPUTS = {"private_text", "private_key", "private_click", "private_scroll"}

async def workspace(name, *args, payload=None):
    process = await asyncio.create_subprocess_exec(
        "weasel-workspace", "--name", name, *args, stdin=PIPE,
        stdout=PIPE, stderr=PIPE, limit=8 << 20)
    try:
        async with asyncio.timeout(45):
            out, err = await process.communicate(
                None if payload is None else json.dumps(payload, ensure_ascii=False).encode())
    except (asyncio.CancelledError, TimeoutError):
        # Closing this exact CLI connection tells the workspace broker to
        # cancel its running job. Other clients and sessions keep their jobs.
        if process.returncode is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
        await process.wait()
        raise ActionError("Workspace-Aufruf unterbrochen; einzelne Wirkungen sind möglich")
    try:
        result = json.loads(out or err)
    except ValueError:
        result = {}
    if not isinstance(result, dict):
        result = {}
    if process.returncode or result.get("ok") is not True:
        raise ActionError(result.get("error") or err.decode(errors="replace")[:1000]
                          or f"Workspace lieferte keinen Beleg (Exit {process.returncode})", result)
    return result

async def exchange_action(action, args, sessions):
    name = args["name"]
    fields = {"private_exchange_list": (), "private_exchange_lstat": ("filename",),
              "private_exchange_rename_noreplace": ("source", "destination")}[action]
    operation = action.removeprefix("private_").replace("_", "-")
    expected = args["session"] or sessions.get(name)
    if name in sessions and sessions[name] != expected:
        raise ValueError("Austauschordner gehört zu einer anderen Sitzung")
    try:
        receipt = await exchange_request(name, operation, [args[key] for key in fields], expected)
    except BrokerError as exc:
        raise ActionError(str(exc), exc.reply) from exc
    sessions[name] = receipt["session"]
    return receipt

async def open_private(name, args):
    app_id = args["wait_window"]
    before = await workspace(name, "status")
    if not before["running"]:
        raise ActionError("Private Arbeitsfläche zuerst starten", before)
    windows = before["windows"]
    if any(window["app_id"] == app_id for window in windows):
        raise ActionError("Diese Fensterkennung ist schon geöffnet. "
                          "Eine eigene Fensterkennung oder eine andere private Sitzung verwenden.", before)

    job = uuid.uuid4().hex
    batch = [["open", args["program"], *args["argv"]], ["wait-window", app_id]]
    try:
        receipt = await workspace(name, "batch", job, payload=batch)
    except (ActionError, OSError, asyncio.CancelledError) as exc:
        raise ActionError(f"Privater App-Start unbestätigt. Das Programm kann bereits laufen. {exc}",
                          {"job": job, "broker": getattr(exc, "receipt", None)}) from exc

    match receipt.get("results"):
        case [dict() as spawn, dict() as window] if (
            type(spawn.get("pid")) is int and spawn["pid"] > 0
            and type(window.get("id")) is int and window["id"] > 0
            and window.get("app_id") == app_id and window.get("focused") is True
            and all(previous["id"] != window["id"] for previous in windows)
        ):
            return {"job": job, "spawn": spawn, "window": window}
    raise ActionError("Neues fokussiertes Fenster unbestätigt", {"job": job, "broker": receipt})

async def private(action, args, images):
    operation = action.removeprefix("private_")
    # The workspace CLI owns the one shared gate, regardless of workspace name.
    if operation == "cancel_all":
        try:
            return await workspace("default", "cancel-all")
        except ActionError as exc:
            if exc.receipt and exc.receipt.get("paused") is True:
                failed = [item["name"] for item in exc.receipt["workspaces"] if "error" in item]
                raise ActionError("Private Eingaben gesperrt. Abbruch unbestätigt für: "
                                  + ", ".join(failed), exc.receipt) from exc
            raise
    if operation == "resume":
        return await workspace("default", "resume")
    name = args["name"]
    if operation == "start":
        options = []
        if args["exchange"]:
            options.append("--exchange=" + args["exchange"])
        if args["network"]:
            options.append("--network")
        if args["persistent"]:
            options.append("--persistent")
        return await workspace(name, "start", *options)
    if operation in ("stop", "status"):
        return await workspace(name, operation)
    if operation == "open":
        return await open_private(name, args)
    if operation == "screenshot":
        # The CLI receives image bytes in its JSON. MCP presents them as an
        # image content block. Both paths clean up the temporary file.
        with tempfile.TemporaryDirectory(prefix="weasel-view-") as directory:
            path = Path(directory, "workspace.png")
            await workspace(name, "screenshot", str(path))
            data = path.read_bytes()
            if not data.startswith(b"\x89PNG\r\n\x1a\n") or len(data) > 5 << 20:
                raise ActionError("Ungültiges privates PNG")
            digest = hashlib.sha256(data).hexdigest()
            frame = {"workspace": name, "sha256": digest, "unchanged": digest == args.get("if_sha256")}
            if frame["unchanged"]:
                return frame
            if sum(len(image["data"]) for image in images) + (len(data) + 2) // 3 * 4 > 12 << 20:
                raise ActionError("Bildbudget der Sequenz erreicht")
            images.append({"type": "image", "mimeType": "image/png",
                           "data": base64.b64encode(data).decode()})
            return {**frame, "image_index": len(images) - 1}
    if operation != "editor":
        raise ValueError(f"Unbekannte private Aktion: {action}")
    editor = os.environ.get("WEASEL_PRIVATE_EDITOR")
    terminal = os.environ.get("WEASEL_PRIVATE_TERMINAL")
    if not editor or not terminal:
        raise ActionError("Paketierter privater Editor fehlt")
    app_id = "weasel-editor-" + uuid.uuid4().hex
    batch = [["open", terminal, "--app-id=" + app_id, "--title=Privater Texteditor", editor]]
    job = uuid.uuid4().hex
    receipt = await workspace(name, "batch", job, payload=batch)
    try:
        window = await workspace(name, "batch", uuid.uuid4().hex, payload=[["wait-window", app_id]])
    except (ActionError, OSError, asyncio.CancelledError) as exc:
        raise ActionError("Editorstart quittiert; Fenster unbestätigt", receipt) from exc
    return {"spawn": receipt, "window": window}

def input_batch(actions, start, single):
    name = actions[start][1]["name"]
    payload = []
    size = 2  # JSON array brackets
    for action, args in actions[start:]:
        if action not in PRIVATE_INPUTS or args["name"] != name:
            break
        # plan() has validated and ordered arguments using the catalog.
        operation = [action.removeprefix("private_"), *(v for k, v in args.items() if k != "name")]
        size += len(json.dumps(operation, ensure_ascii=False).encode()) + (2 if payload else 0)
        # The CLI wraps this array with its op, 32-byte epoch and job ID.
        if size > 63 * 1024:
            break
        payload.append(operation)
        if single:
            break
    return payload

async def dispatch_inputs(name, payload):
    job = uuid.uuid4().hex
    try:
        reply = await workspace(name, "batch", job, payload=payload)
        error = None
    except (ActionError, OSError, asyncio.CancelledError) as exc:
        reply = getattr(exc, "receipt", None)
        error = str(exc) or type(exc).__name__

    results = reply.get("results") if isinstance(reply, dict) else None
    attempted = reply.get("attempted") if isinstance(reply, dict) else None
    if error is None:
        attempted = len(payload)
    valid = (isinstance(results, list) and type(attempted) is int
             and len(results) <= attempted <= len(payload)
             and all(isinstance(r, dict) and r.get("action") == op[0] for r, op in zip(results, payload)))
    if error is None:
        valid = valid and len(results) == len(payload)
    else:
        valid = (valid and reply.get("ok") is False and len(results) < len(payload)
                 and attempted - len(results) <= 1)
    if not valid:
        # Old brokers, lost connections and malformed acknowledgements give
        # no reliable boundary. Never replay any member of the submitted run.
        results, attempted = [], len(payload)
        error = error or "Ungültige Eingabequittung des Brokers"

    records = [{"action": "private_" + result["action"], "job": job, "ok": True,
                "result": {"ok": True, "results": [result]}} for result in results]
    if error is not None:
        first = len(results)
        for index in range(first, max(first + 1, attempted)):
            record = {"action": "private_" + payload[index][0], "job": job, "ok": False,
                      "effect": "unknown" if index < attempted else "not_started", "error": error}
            if index == first:
                record["receipt"] = reply
            records.append(record)
    return records

def reserve(request_id):
    if not isinstance(request_id, str) or not re.fullmatch(REQUEST_ID["pattern"], request_id):
        raise ValueError("Ungültige request_id")
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime or not os.path.isabs(runtime):
        raise ValueError("XDG_RUNTIME_DIR fehlt für die einmalige request_id")
    directory = Path(runtime, "weasel-sequences")
    directory.mkdir(mode=0o700, exist_ok=True)
    stat = directory.lstat()
    if directory.is_symlink() or stat.st_uid != os.getuid() or stat.st_mode & 0o077:
        raise ValueError("Privates Verzeichnis für Sequenzen nicht verfügbar")
    with closing(sqlite3.connect(directory / "ids.sqlite3")) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS consumed(id TEXT PRIMARY KEY)")
        try:
            db.execute("INSERT INTO consumed VALUES (?)", (request_id,))
        except sqlite3.IntegrityError as exc:
            raise ValueError("request_id bereits verbraucht; keine erneute Ausführung") from exc

async def execute(steps, request_id=None):
    actions = plan(steps)
    if request_id is not None:
        reserve(request_id)
    records = []
    exchange_sessions = {}
    images = []
    status = "completed"
    async with AsyncExitStack() as stack:
        desktop = Desktop(stack)
        while len(records) < len(actions):
            index = len(records)
            action, args = actions[index]
            started_ns = time.monotonic_ns() if action.startswith("private_exchange_") else None
            try:
                if desktop.indicator is not None and desktop.indicator.returncode is not None:
                    raise ActionError("Desktop-Auftrag wurde beendet")
                if action in PRIVATE_INPUTS:
                    # An existing desktop indicator must still be checked
                    # between private actions in a mixed desktop sequence.
                    payload = input_batch(actions, index, single=desktop.indicator is not None)
                    records.extend(await dispatch_inputs(args["name"], payload))
                    if not records[-1]["ok"]:
                        status = "partial" if any(r["ok"] for r in records) else "stopped"
                        break
                    continue
                if started_ns is not None:
                    value = await exchange_action(action, args, exchange_sessions)
                elif action.startswith("private_"):
                    value = await private(action, args, images)
                elif action in ("windows", "workspaces"):
                    value = (await desktop.call("desktop_windows"))[action]
                elif action == "actor_status":
                    value = await desktop.call("desktop_status")
                elif action == "window_inspect":
                    value = await desktop.call("desktop_semantic_direct", {**args, "background": True})
                else:
                    value = await desktop.act(action, args, CATALOG[action]["title"])
                records.append({"action": action, "ok": True, "result": value})
            except (ActionError, OSError, ValueError, KeyError, TypeError, IndexError,
                    TimeoutError, asyncio.CancelledError) as exc:
                prior = len(records)
                records.append({"action": action, "ok": False,
                                "error": str(exc) or type(exc).__name__,
                                "receipt": getattr(exc, "receipt", None)})
                status = "partial" if prior else "stopped"
                break
            finally:
                if started_ns is not None and len(records) > index:
                    records[index]["duration_ns"] = time.monotonic_ns() - started_ns
    return ({"status": status, "request_id": request_id, "steps": records, "remaining": len(actions) - len(records),
             "automatic_replay": False}, images)
