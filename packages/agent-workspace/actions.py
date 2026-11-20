"""Validate the whole operation before the first input reaches the compositor."""
import re
from pathlib import PurePosixPath

NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,31}")
MODIFIERS = {"ctrl", "shift", "alt", "logo", "altgr"}
MAX_REQUEST = 65536
MAX_REPLY = 8 * 1024 * 1024
MAX_SECONDS = 30


def identifier(value):
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise ValueError("expected a 1–32 character name (letters, digits, hyphen, underscore)")
    return value


def integer(value, low, high):
    if type(value) is not int and not (isinstance(value, str) and re.fullmatch(r"[0-9]{1,5}", value)):
        raise ValueError(f"expected an integer from {low} through {high}")
    value = int(value)
    if not low <= value <= high:
        raise ValueError(f"expected an integer from {low} through {high}")
    return value


def chord(value):
    parts = value.split("+")
    modifiers, key = [item.lower() for item in parts[:-1]], parts[-1]
    if (len(modifiers) != len(set(modifiers)) or set(modifiers) - MODIFIERS
            or not re.fullmatch(r"[A-Za-z0-9_]{1,64}", key)):
        raise ValueError("expected [Ctrl+][Shift+][Alt+][Logo+][AltGr+]XKB_KEY")
    return [key, *modifiers]


def validate(action):
    if not isinstance(action, list) or not action or not isinstance(action[0], str):
        raise ValueError("each action must be an array beginning with its command")
    if any(type(arg) not in (str, int) or "\0" in str(arg) for arg in action):
        raise ValueError("arguments must be strings or integers without NUL")
    name, *args = action
    if name == "open" and 1 <= len(args) <= 128 and all(isinstance(a, str) for a in args):
        path = PurePosixPath(args[0])
        if path.is_relative_to("/nix/store") and ".." not in path.parts:
            return action
    if name in ("text", "wait-window") and len(args) == 1 and isinstance(args[0], str):
        if len(args[0].encode()) <= (8192 if name == "text" else 255):
            return action
    if name == "key" and len(args) == 1 and isinstance(args[0], str):
        chord(args[0])
        return action
    if name in ("click", "scroll") and 2 <= len(args) <= 4:
        x, y = integer(args[0], 0, 1279), integer(args[1], 0, 719)
        if name == "click" and len(args) <= 3:
            button = args[2] if len(args) == 3 else "left"
            if button in ("left", "middle", "right"):
                return [name, x, y, button]
        if name == "scroll" and len(args) >= 3 and args[2] in ("up", "down", "left", "right"):
            return [name, x, y, args[2], integer(args[3], 1, 32) if len(args) == 4 else 1]
    raise ValueError(f"invalid arguments for {name!r}")


def validate_batch(actions):
    if not isinstance(actions, list) or not 1 <= len(actions) <= 64:
        raise ValueError("expected an array of 1–64 actions")
    return [validate(action) for action in actions]
