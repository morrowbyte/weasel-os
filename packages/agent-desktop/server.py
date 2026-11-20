"""Newline-delimited stdio MCP. Stdout carries JSON-RPC only."""
import asyncio
import json
import sys

from actions import execute
from catalog import CATALOG, COMPOSE, catalog, obj

def tool(name, title, schema, read_only):
    return {"name": name, "description": title, "inputSchema": schema,
            "annotations": {"readOnlyHint": read_only, "destructiveHint": not read_only,
                            "openWorldHint": not read_only}}

def tools():
    return [tool("catalog", "Aktionen mit Klasse, Fokuswirkung und Argumenten", obj({}), True),
            tool("compose", "1–32 Aktionen der Reihe nach. Stoppt beim ersten Fehler mit Teilbelegen.",
                 COMPOSE, False),
            *(tool(name, f"{a['title']} ({a['class']}, {a['focus']})", a["inputSchema"],
                   a["class"] == "read") for name, a in CATALOG.items())]

def emit(rid, **payload):
    print(json.dumps({"jsonrpc": "2.0", "id": rid, **payload}, separators=(",", ":")), flush=True)

def error(rid, code, message):
    emit(rid, error={"code": code, "message": message})

async def invoke(rid, name, args):
    try:
        if name == "catalog":
            if args != {}:
                raise ValueError("catalog erwartet keine Argumente")
            data, images = catalog(), []
        elif name == "compose":
            if not isinstance(args, dict) or ("steps" not in args or args.keys() - {"steps", "request_id"}):
                raise ValueError("compose erwartet steps")
            data, images = await execute(args["steps"], args.get("request_id"))
        else:
            data, images = await execute([{"action": name, "args": args}])
        content = [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}, *images]
        emit(rid, result={"content": content, "isError": data.get("status", "completed") != "completed"})
    except ValueError as exc:
        error(rid, -32602, str(exc))
    except asyncio.CancelledError:
        error(rid, -32800, "Aufruf abgebrochen")
    except Exception as exc:
        error(rid, -32603, type(exc).__name__)

async def serve():
    pending = {}
    versions = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
    try:
        while line := await asyncio.to_thread(sys.stdin.buffer.readline, 1 << 20):
            if not line.endswith(b"\n"):
                error(None, -32700, "Unvollständige oder zu große Nachricht")
                break
            try:
                msg = json.loads(line)
            except (ValueError, RecursionError):
                error(None, -32700, "Ungültiges JSON")
                continue
            if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
                error(None, -32600, "Ungültiger Request")
                continue
            method, p = msg.get("method"), msg.get("params", {})
            if "id" not in msg:
                if method == "notifications/cancelled" and isinstance(p, dict):
                    key = p.get("requestId")
                    if type(key) in (str, int) and key in pending:
                        pending[key].cancel()
                continue
            rid = msg["id"]
            if type(rid) not in (str, int) or rid in pending:
                error(None, -32600, "Ungültige oder aktive Request-ID")
            elif not isinstance(p, dict):
                error(rid, -32602, "Ungültige Parameter")
            elif method == "initialize":
                requested = p.get("protocolVersion")
                emit(rid, result={"protocolVersion": requested if requested in versions else versions[0],
                                  "serverInfo": {"name": "weasel", "version": "0.1.0"},
                                  "capabilities": {"tools": {"listChanged": False}}})
            elif method == "tools/list":
                emit(rid, result={"tools": tools()})
            elif method == "ping":
                emit(rid, result={})
            elif method == "tools/call":
                if len(pending) >= 8:
                    error(rid, -32000, "Zu viele laufende Aufträge")
                    continue
                task = asyncio.create_task(invoke(rid, p.get("name"), p.get("arguments", {})))
                pending[rid] = task
                task.add_done_callback(lambda _, key=rid: pending.pop(key, None))
            else:
                error(rid, -32601, "Unbekannte Methode")
    finally:
        running = list(pending.values())
        for task in running:
            task.cancel()
        await asyncio.gather(*running, return_exceptions=True)
