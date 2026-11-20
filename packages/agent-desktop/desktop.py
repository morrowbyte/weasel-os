"""Use one native MCP connection and one owned indicator per sequence."""
import asyncio
import json
import uuid
from contextlib import AsyncExitStack

PIPE = asyncio.subprocess.PIPE

class ActionError(Exception):
    def __init__(self, message, receipt=None):
        super().__init__(message)
        self.receipt = receipt

def one(items, message):
    items = list(items)
    if len(items) != 1:
        raise ActionError(message)
    return items[0]

async def finish(process):
    if process.stdin:
        process.stdin.close()
    # EOF on the native MCP connection cancels only that connection's pending
    # actor calls. Never send a global desktop_cancel for a local client Stop.
    try:
        await asyncio.wait_for(process.wait(), 8)
    except TimeoutError:
        process.kill()
        await process.wait()

class Desktop:
    def __init__(self, stack: AsyncExitStack):
        self.stack = stack
        self.proxy = None
        self.indicator = None
        self.task_id = "weasel-" + uuid.uuid4().hex
        self.serial = 0

    async def rpc(self, method, params):
        self.serial += 1
        rid = self.serial
        def send(message):
            self.proxy.stdin.write((json.dumps({"jsonrpc": "2.0", **message}) + "\n").encode())
        send({"id": rid, "method": method, "params": params})
        response = asyncio.create_task(self.proxy.stdout.readline())
        try:
            async with asyncio.timeout(40):
                line = await asyncio.shield(response)
        except (asyncio.CancelledError, TimeoutError):
            send({"method": "notifications/cancelled", "params": {"requestId": rid}})
            # Give the actor its own release/receipt window. Never dispatch a
            # replacement action when the acknowledgement or transport is lost.
            try:
                line = await asyncio.wait_for(response, 5)
                receipt = json.loads(line).get("result")
            except (ValueError, OSError, TimeoutError):
                receipt = None
            raise ActionError("Aufruf abgebrochen; Wirkung anhand des Belegs prüfen", receipt)
        if not line:
            raise ActionError("Actor-Verbindung beendet; Wirkung unbekannt")
        reply = json.loads(line)
        if reply.get("id") != rid or "result" not in reply:
            raise ActionError(reply.get("error", {}).get("message", "Ungültige Actor-Antwort"))
        return reply["result"]

    async def call(self, tool, args=None):
        if self.proxy is None:
            self.proxy = await asyncio.create_subprocess_exec(
                "weasel-computer-use", "mcp", stdin=PIPE, stdout=PIPE, limit=8 << 20)
            self.stack.push_async_callback(finish, self.proxy)
            await self.rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                          "clientInfo": {"name": "weasel", "version": "1"}})
            self.proxy.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        result = await self.rpc("tools/call", {"name": tool, "arguments": args or {}})
        body = json.loads(result["content"][0]["text"])
        if result.get("isError"):
            raise ActionError(body.get("error") or "Actor hat den Auftrag gestoppt", body)
        return body

    async def scope(self, output, activity):
        if self.indicator:
            if self.indicator.returncode is not None or self.output != output:
                raise ActionError("Indikator beendet oder Zielausgabe gewechselt")
            return
        state = await self.call("desktop_status")
        self.session = state["session_id"]
        self.epoch = state["epoch"]
        self.output = output
        self.indicator = await asyncio.create_subprocess_exec(
            "weasel-computer-use-indicator", "run", "--task-id", self.task_id,
            "--output", output, "--activity", activity,
            "--stdin-control", stdin=PIPE, stdout=PIPE)
        self.stack.push_async_callback(finish, self.indicator)
        async with asyncio.timeout(10):
            while line := await self.indicator.stdout.readline():
                event = json.loads(line)
                if (event.get("event"), event.get("task_id"), event.get("output")) == (
                        "ready", self.task_id, output):
                    return
        raise ActionError("Indikator konnte den Auftrag nicht öffnen")

    async def act(self, name, args, title):
        inventory = await self.call("desktop_windows")
        spaces = inventory["workspaces"]
        target = args.get("window_id")
        background = name == "window_set_value"
        if background:
            window = one((w for w in inventory["windows"] if w["id"] == target),
                         "Zielfenster nicht eindeutig")
            space = one((w for w in spaces if w["id"] == window["workspace_id"]),
                        "Zielfenster hat keine eindeutige Arbeitsfläche")
        else:
            space = one((w for w in spaces if w["is_focused"]), "Keine eindeutige fokussierte Ausgabe")
        output = space["output"]
        await self.scope(output, title)
        if background:
            observation = await self.call("desktop_semantic_direct", {"window_id": target, "background": True})
            prefix = "ax-obs"
        else:
            observation = await self.call("desktop_observe", {"output": output, "include_image": False})
            prefix = "obs"
        if (observation.get("epoch") != self.epoch
                or not (observation.get("observation_id") or "").startswith(f"{prefix}-{self.session}-")):
            raise ActionError("Keine handlungsbereite Beobachtung in der gebundenen Actor-Sitzung")
        if name == "workspace_focus":
            space = one((s for s in observation["workspaces"] if s["id"] == args["workspace_id"]),
                        "Niri-Arbeitsfläche fehlt")
            target = space.get("active_window_id")
            if target is None:
                raise ActionError("Leere Arbeitsfläche: Der Actor benötigt ein Zielfenster")
        if name == "app_launch":
            action = {"kind": "launch_app", "app": args["app"]}
        elif background:
            info = observation
            if info.get("elements_complete") is not True or info.get("action_ready") is not True:
                raise ActionError("Kein vollständiger, handlungsbereiter AT-SPI-Baum")
            element = one((e for e in info["elements"] if e["label"] == args["label"]
                           and e["capabilities"]["set_value"]), "Editierbares Feld nicht eindeutig")
            action = {"kind": "semantic_set_value", "handle_id": element["handle_id"],
                      "text": args["text"], "expected_text": args["expected_text"]}
        else:
            action = {"kind": "focus", "window_id": target}
        request = {"task_id": self.task_id, "observation_id": observation["observation_id"],
                   "actions": [action], "include_image": False, "observe_after": True}
        if background:
            # The AT-SPI write reads the exact buffer back before it returns.
            # Take the independent post-action tree immediately after that receipt.
            request["settle_ms"] = 0
        if target is not None:
            request["window_id"] = target
        receipt = await self.call("desktop_act", request)
        after = receipt.pop("after_observation", {}) or {}
        if receipt.get("status") != "dispatched" or receipt.get("actor_release_confirmed") is not True:
            raise ActionError("Dispatch oder Freigabe unbestätigt", receipt)
        if background:
            if receipt.get("background_semantic") is not True:
                raise ActionError("Hintergrundwirkung unbestätigt", receipt)
            details = receipt["effects"][0]["details"]
            verified = (after.get("niri_window_id") == target and after.get("elements_complete") is True
                        and details.get("exact_buffer_bytes_match") is True
                        and details.get("focus_events") == "no_change_observed")
        elif name == "app_launch":
            pid = receipt["effects"][0]["details"]["spawn"]["pid"]
            before_ids = {w["id"] for w in observation["windows"]}
            deadline = asyncio.get_running_loop().time() + 10
            windows = after.get("windows", [])
            while True:
                matches = [w for w in windows if pid is not None and w["pid"] == pid and w["id"] not in before_ids]
                if matches:
                    receipt["mapped_window"] = {"id": matches[0]["id"], "pid": pid, "app_id": matches[0]["app_id"]}
                    verified = True
                    break
                if pid is None or self.indicator.returncode is not None or asyncio.get_running_loop().time() >= deadline:
                    verified = False
                    break
                await asyncio.sleep(0.08)
                state = await self.call("desktop_status")
                if state["session_id"] != self.session or state["epoch"] != self.epoch:
                    raise ActionError("Start unterbrochen; Anwendung kann bereits laufen", receipt)
                windows = (await self.call("desktop_windows"))["windows"]
        else:
            verified = (after.get("focused_window") or {}).get("id") == target
            if name == "workspace_focus":
                verified = verified and after["focused_window"]["workspace_id"] == args["workspace_id"]
        if not verified:
            raise ActionError("Actor quittiert; erwartete UI-Wirkung unbestätigt", receipt)
        return {"verified": True, "observation_id": after.get("observation_id"), "receipt": receipt}
