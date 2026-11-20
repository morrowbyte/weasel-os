"""One closed catalog for interactive prompts, CLI validation and MCP schemas."""
import json
import re

def integer(low, high):
    return {"type": "integer", "minimum": low, "maximum": high}

def string(limit, **constraints):
    return {"type": "string", "minLength": 1, "maxLength": limit, **constraints}

def choice(*values):
    return {"type": "string", "enum": list(values)}

def obj(fields, required=None):
    return {"type": "object", "properties": fields,
            "required": list(fields) if required is None else required,
            "additionalProperties": False}

ID = integer(1, 2**63 - 1)
NAME = string(32, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$", default="default")
SESSION = string(32, minLength=0, pattern=r"^(?:[0-9a-f]{32})?$", default="",
                 description="Optionale Sitzungskennung aus einem früheren Austauschbeleg.")
FILENAME = string(255, pattern=r"^(?!\.)[^/\x00-\x1f\x7f]+$",
                  description="Sichtbarer Dateiname direkt im Austauschordner, bis zu 255 UTF-8-Bytes.")
X, Y = integer(0, 1279), integer(0, 719)
ARGV = {"type": "array", "items": {**string(32768), "minLength": 0},
        "maxItems": 127, "default": []}

def action(title, category, focus, **fields):
    return {"title": title, "class": category, "focus": focus,
            "inputSchema": obj(fields, [k for k, v in fields.items() if "default" not in v])}

def read(title, **fields):
    return action(title, "read", "unchanged", **fields)

def private(title, read_only=False, **fields):
    return action(title, "read" if read_only else "private-workspace", "private-only",
                  name=NAME, **fields)

CATALOG = {
    "windows": read("Laufende Fenster anzeigen"),
    "workspaces": read("Niri-Arbeitsflächen anzeigen"),
    "actor_status": read("Bereitschaft und Startziele des Desktop-Actors prüfen"),
    "window_inspect": read("Bedienelemente eines Fensters lesen", window_id=ID),
    "window_set_value": action("Benanntes Textfeld über AT-SPI ändern", "app-background",
                               "no-focus-request", window_id=ID, label={**string(512), "minLength": 0},
                               expected_text={**string(8192), "minLength": 0},
                               text={**string(511), "minLength": 0}),
    "window_focus": action("Fenster fokussieren", "foreground", "changes-focus", window_id=ID),
    "workspace_focus": action("Niri-Arbeitsfläche über ihr aktives Fenster fokussieren",
                              "foreground", "changes-focus", workspace_id=ID),
    "app_launch": action("App in einem neuen privaten Profil starten", "foreground",
                         "may-change-focus", app=choice("zed", "chrome")),
    "private_start": private("Private Arbeitsfläche starten",
                             exchange={**string(4096), "minLength": 0, "default": "",
                                       "description": "Optionaler Host-Ordner, privat eingebunden unter /exchange."},
                             network={"type": "boolean", "default": False,
                                      "description": "Ausgehenden Netzwerkzugang für die Sitzung zulassen."},
                             persistent={"type": "boolean", "default": False,
                                         "description": "Privates Home unter diesem Namen dauerhaft speichern."}),
    "private_exchange_list": private("Dateinamen im Austauschordner anzeigen", read_only=True,
                                     session=SESSION),
    "private_exchange_lstat": private("Dateimetadaten im Austauschordner anzeigen", read_only=True,
                                      filename=FILENAME, session=SESSION),
    "private_exchange_rename_noreplace": private("Datei im Austauschordner ohne Überschreiben umbenennen",
                                                source=FILENAME, destination=FILENAME, session=SESSION),
    "private_status": private("Private Arbeitsfläche prüfen", read_only=True),
    "private_editor": private("Texteditor auf der privaten Arbeitsfläche öffnen"),
    "private_open": private("Programm auf privater Arbeitsfläche öffnen",
                            program=string(4096, description="Befehl im PATH oder Pfad zu einem Nix-Store-Programm."),
                            argv={**ARGV, "description": 'Ein Argument pro Listeneintrag, z. B. ["--option", "zwei Worte"].'},
                            wait_window=string(255, description="Exakte Wayland-App-ID oder X11-Klasse (WM_CLASS) eines neuen fokussierten Fensters.")),
    "private_text": private("Text ins private Fenster schreiben", text=string(8192)),
    "private_key": private("Taste im privaten Fenster senden", key=string(64)),
    "private_click": private("Privat klicken", x=X, y=Y, button=choice("left", "middle", "right")),
    "private_scroll": private("Privat scrollen", x=X, y=Y,
                              direction=choice("up", "down", "left", "right"), steps=integer(1, 32)),
    "private_screenshot": private("Private Arbeitsfläche aufnehmen", read_only=True,
                                  if_sha256=string(64, minLength=0, pattern=r"^(?:[0-9a-f]{64})?$", default="",
                                                   description="Hash des bisherigen PNG. Bei gleichem Inhalt genügt der neue Vergleichsbeleg.")),
    "private_cancel_all": action("Private Eingaben abbrechen und sperren", "private-workspace",
                                 "private-only"),
    "private_resume": action("Private Eingaben für neue Aufträge freigeben", "private-workspace",
                             "private-only"),
    "private_stop": private("Private Sitzung samt ihren Apps beenden"),
}
STEP = obj({"action": choice(*CATALOG), "args": {"type": "object"}}, ["action"])
REQUEST_ID = string(80, pattern=r"^[A-Za-z0-9_-]{1,80}$")
COMPOSE = obj({"steps": {"type": "array", "items": STEP, "minItems": 1, "maxItems": 32},
               "request_id": REQUEST_ID}, ["steps"])

def catalog():
    return {"version": 1, "actions": CATALOG, "composition": COMPOSE,
            "full_desktop": ["weasel-computer-use", "mcp"]}

def valid_value(value, spec):
    if "enum" in spec:
        return value in spec["enum"]
    match spec["type"]:
        case "integer":
            return type(value) is int and spec["minimum"] <= value <= spec["maximum"]
        case "boolean":
            return type(value) is bool
        case "array":
            return (isinstance(value, list) and len(value) <= spec["maxItems"]
                    and all(valid_value(item, spec["items"]) for item in value))
        case "string":
            return (isinstance(value, str) and not re.search(r"[\0\ud800-\udfff]", value)
                    and spec["minLength"] <= len(value) <= spec["maxLength"]
                    and ("pattern" not in spec or re.fullmatch(spec["pattern"], value)))
    return False

def validate(action_id, args):
    if not isinstance(action_id, str) or action_id not in CATALOG:
        raise ValueError("Unbekannte Aktion")
    schema = CATALOG[action_id]["inputSchema"]
    if not isinstance(args, dict) or args.keys() - schema["properties"].keys():
        raise ValueError(f"{action_id}: unbekannte Argumente")
    result = {}
    for key, spec in schema["properties"].items():
        value = args.get(key, spec.get("default"))
        if not valid_value(value, spec):
            raise ValueError(f"{action_id}: ungültiges oder fehlendes Argument {key}")
        result[key] = value
    if action_id.startswith("private_exchange_"):
        for key in ("filename", "source", "destination"):
            if key in result and (not result[key].isprintable() or len(result[key].encode()) > 255):
                raise ValueError(f"{action_id}: ungültiger Dateiname {key}")
        if "source" in result and result["source"] == result["destination"]:
            raise ValueError("Quell- und Zielname müssen verschieden sein")
    if action_id == "private_open":
        if len(result["wait_window"].encode()) > 255:
            raise ValueError("private_open: Fensterkennung höchstens 255 UTF-8-Bytes")
        batch = [["open", result["program"], *result["argv"]], ["wait-window", result["wait_window"]]]
        if len(json.dumps(batch)) > 64 << 10:
            raise ValueError("private_open: Workspace-Auftrag überschreitet 64 KiB")
    if action_id == "private_text" and len(result["text"].encode()) > 8192:
        raise ValueError("private_text: höchstens 8192 UTF-8-Bytes")
    if action_id == "private_key":
        *mods, key = result["key"].split("+")
        mods = [mod.lower() for mod in mods]
        if (len(set(mods)) != len(mods) or set(mods) - {"ctrl", "shift", "alt", "logo", "altgr"}
                or not re.fullmatch(r"[A-Za-z0-9_]{1,64}", key)):
            raise ValueError("private_key: ungültiger XKB-Tastenname oder Modifikator")
    return result

def plan(steps):
    if not isinstance(steps, list) or not 1 <= len(steps) <= 32:
        raise ValueError("Eine Sequenz benötigt 1 bis 32 Schritte")
    result = []
    for step in steps:
        if not isinstance(step, dict) or "action" not in step or step.keys() - STEP["properties"].keys():
            raise ValueError("Schritt benötigt action und optional args")
        result.append((step["action"], validate(step["action"], step.get("args", {}))))
    return result
