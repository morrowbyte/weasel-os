import asyncio
import base64
import json
import os
import re
import sqlite3
import tempfile
import uuid
from contextlib import AsyncExitStack, closing
from pathlib import Path

from catalog import CATALOG, REQUEST_ID, plan
from desktop import ActionError, Desktop, PIPE

async def workspace(name, *args, payload=None):
    process = await asyncio.create_subprocess_exec(
        "weasel-workspace", "--name", name, *args, stdin=PIPE,
        stdout=PIPE, stderr=PIPE, limit=8 << 20)
    try:
        async with asyncio.timeout(45):
            out, err = await process.communicate(None if payload is None else json.dumps(payload).encode())
    except (asyncio.CancelledError, TimeoutError):
        # Closing this exact CLI connection tells the workspace broker to
        # cancel its running job. Other clients and sessions keep their jobs.
        if process.returncode is None:
            process.terminate()
        await process.wait()
        raise ActionError("Workspace-Aufruf unterbrochen; einzelne Wirkungen sind möglich")
    try:
        result = json.loads(out or err)
    except ValueError:
        result = {}
    if process.returncode or result.get("ok") is False or not result:
        raise ActionError(result.get("error") or err.decode(errors="replace")[:1000]
                          or f"Workspace lieferte keinen Beleg (Exit {process.returncode})", result)
    return result

async def open_private(name, args):
    app_id = args["wait_window"]
    before = await workspace(name, "status")
    if not before["running"]:
        raise ActionError("Private Arbeitsfläche zuerst starten", before)
    windows = before["windows"]
    if any(window["app_id"] == app_id for window in windows):
        raise ActionError("Diese Wayland-App-ID ist schon geöffnet. "
                          "Eine eigene App-ID oder eine andere private Sitzung verwenden.", before)

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
            if sum(len(image["data"]) for image in images) + (len(data) + 2) // 3 * 4 > 12 << 20:
                raise ActionError("Bildbudget der Sequenz erreicht")
            images.append({"type": "image", "mimeType": "image/png",
                           "data": base64.b64encode(data).decode()})
            return {"workspace": name, "image_index": len(images) - 1}
    if operation == "editor":
        editor = os.environ.get("WEASEL_PRIVATE_EDITOR")
        terminal = os.environ.get("WEASEL_PRIVATE_TERMINAL")
        if not editor or not terminal:
            raise ActionError("Paketierter privater Editor fehlt")
        app_id = "weasel-editor-" + uuid.uuid4().hex
        batch = [["open", terminal, "--app-id=" + app_id, "--title=Privater Texteditor", editor]]
    else:
        batch = [[operation, *(v for k, v in args.items() if k != "name")]]
    job = uuid.uuid4().hex
    receipt = await workspace(name, "batch", job, payload=batch)
    if operation == "editor":
        try:
            window = await workspace(name, "batch", uuid.uuid4().hex, payload=[["wait-window", app_id]])
        except (ActionError, OSError, asyncio.CancelledError) as exc:
            raise ActionError("Editorstart quittiert; Fenster unbestätigt", receipt) from exc
        return {"spawn": receipt, "window": window}
    return receipt

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
    images = []
    status = "completed"
    async with AsyncExitStack() as stack:
        desktop = Desktop(stack)
        for action, args in actions:
            try:
                if desktop.indicator is not None and desktop.indicator.returncode is not None:
                    raise ActionError("Desktop-Auftrag wurde beendet")
                if action.startswith("private_"):
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
    return ({"status": status, "request_id": request_id, "steps": records, "remaining": len(actions) - len(records),
             "automatic_replay": False}, images)
