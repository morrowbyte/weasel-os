"""Bounded desktop-pet protocol. No desktop content is acquired here."""
import json
import os
from pathlib import Path
import re
import stat
import struct
import subprocess

STATES = ("idle", "working", "reading", "lookingAway", "waiting", "attention", "success", "error")
SPECIES = ("weasel", "dog", "cat", "fox", "otter", "raccoon", "owl", "raven", "red-panda", "capybara", "bear", "turtle")
# Fixed install paths; missing packs are never advertised.
BUILTIN_PACKS = {"weasel": ("sudo", "sudo")}
TOKEN = re.compile(r"[A-Za-z0-9_.-]{1,128}\Z")
MAX_LINE = 16384
MAX_PACK_BYTES = 16 * 1024 * 1024


class ProtocolError(ValueError):
    pass


def text(value, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ProtocolError("invalid_text")
    if any(ord(c) < 32 or 127 <= ord(c) <= 159 or c in "\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069" for c in value):
        raise ProtocolError("invalid_text")
    return value


def integer(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ProtocolError("invalid_integer")
    return value


def exact(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= value.keys() or value.keys() - set(required) - set(optional):
        raise ProtocolError("invalid_fields")


def species(value):
    if not isinstance(value, str) or value not in SPECIES:
        raise ProtocolError("invalid_species")
    return value


def validate_builtin_pets(value):
    if not isinstance(value, list) or len(value) > len(SPECIES):
        raise ProtocolError("invalid_builtin_pets")
    seen = set()
    for pet in value:
        exact(pet, ("species", "name"))
        identity = species(pet["species"])
        if identity in seen:
            raise ProtocolError("invalid_builtin_pets")
        seen.add(identity)
        text(pet["name"], 60)
    return value


def validate_message(value):
    if not isinstance(value, dict) or type(value.get("v")) is not int or value["v"] != 1:
        raise ProtocolError("unsupported_protocol")
    kind = value.get("type")
    if kind == "configure":
        exact(value, ("v", "type", "persistent", "visible", "reducedMotion", "petPath"), ("preferredSpecies",))
        if any(type(value[k]) is not bool for k in ("persistent", "visible", "reducedMotion")):
            raise ProtocolError("invalid_configuration")
        if value.get("preferredSpecies") is not None:
            species(value["preferredSpecies"])
        if value["petPath"] is not None:
            text(value["petPath"], 4096)
            if not Path(value["petPath"]).is_absolute():
                raise ProtocolError("invalid_pet_path")
    elif kind == "projection":
        exact(value, ("v", "type", "revision", "workingCount", "items"))
        integer(value["revision"], 0, 2**53 - 1)
        integer(value["workingCount"], 0, 10000)
        if not isinstance(value["items"], list) or len(value["items"]) > 20:
            raise ProtocolError("invalid_items")
        ids = set()
        for item in value["items"]:
            exact(item, ("id", "kind", "summary"))
            if not isinstance(item["id"], str) or not TOKEN.fullmatch(item["id"]) or item["id"] in ids:
                raise ProtocolError("invalid_item_id")
            ids.add(item["id"])
            if item["kind"] not in ("approval", "question", "result"):
                raise ProtocolError("invalid_item_kind")
            text(item["summary"], 240)
    elif kind in ("toggle", "shutdown"):
        exact(value, ("v", "type"))
    else:
        raise ProtocolError("unknown_message")
    return value


def read_regular(path, maximum):
    before = Path(path).lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
        raise ProtocolError("invalid_pet_file")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > maximum or (before.st_dev, before.st_ino) != (info.st_dev, info.st_ino):
            raise ProtocolError("invalid_pet_file")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(maximum + 1)
        if len(data) > maximum:
            raise ProtocolError("pet_too_large")
        return data
    finally:
        os.close(fd)


def validate_pack(directory):
    root = Path(directory)
    if not root.is_absolute() or root.is_symlink() or root.resolve() != root:
        raise ProtocolError("invalid_pet_directory")
    metadata = json.loads(read_regular(root / "pet.json", 16384))
    exact(metadata, ("schema", "name", "frameWidth", "frameHeight", "sheet", "states"))
    if type(metadata["schema"]) is not int or metadata["schema"] != 1:
        raise ProtocolError("unsupported_pet_schema")
    text(metadata["name"], 60)
    width = integer(metadata["frameWidth"], 64, 512)
    height = integer(metadata["frameHeight"], 64, 512)
    if metadata["sheet"] != "pet.png":
        raise ProtocolError("invalid_pet_sheet")
    exact(metadata["states"], STATES)
    for row, state in enumerate(STATES):
        spec = metadata["states"][state]
        exact(spec, ("row", "frames", "fps", "loop"))
        if type(spec["row"]) is not int or spec["row"] != row or type(spec["frames"]) is not int or spec["frames"] != 4:
            raise ProtocolError("invalid_pet_grid")
        if type(spec["fps"]) is not int or spec["fps"] != 6 or spec["loop"] is not False:
            raise ProtocolError("invalid_pet_timing")
    png = read_regular(root / "pet.png", MAX_PACK_BYTES)
    if len(png) < 33 or png[:8] != b"\x89PNG\r\n\x1a\n" or png[12:16] != b"IHDR":
        raise ProtocolError("invalid_pet_png")
    png_width, png_height, depth, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", png[16:29])
    if (png_width, png_height) != (width * 4, height * 8) or depth != 8 or color != 6 or compression or filtering or interlace not in (0, 1):
        raise ProtocolError("invalid_pet_dimensions_or_alpha")
    return {"name": metadata["name"], "sheet": str(root / "pet.png"), "frameWidth": width, "frameHeight": height}


def decode_pack(directory, renderer_path):
    """Apply metadata bounds before using the renderer's real PNG decoder."""
    pack = validate_pack(directory)
    try:
        decoded = subprocess.run(
            [renderer_path, "--validate-sheet", pack["sheet"], str(pack["frameWidth"]), str(pack["frameHeight"])],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=2, check=False)
    except (OSError, subprocess.SubprocessError) as error:
        raise ProtocolError("invalid_pet_png") from error
    if decoded.returncode != 0:
        raise ProtocolError("invalid_pet_png")
    return pack


def load_builtin_packs(renderer_path):
    """One bounded startup snapshot; heartbeats and projections never decode."""
    root = Path(os.environ.get("WEASEL_PET_BUILTIN_ROOT", str(Path(__file__).resolve().parent / "assets")))
    packs = {}
    for identity, (directory, name) in BUILTIN_PACKS.items():
        try:
            pack = decode_pack(root / directory, renderer_path)
            if pack["name"] != name:
                continue
        except (OSError, ValueError):
            continue
        packs[identity] = pack
    return packs


class Projection:
    def __init__(self):
        self.revision = -1
        self.working = 0
        self.items = []
        self.dismissed = set()

    def apply(self, message):
        validate_message(message)
        if message["type"] != "projection" or message["revision"] <= self.revision:
            return False
        self.revision = message["revision"]
        self.working = message["workingCount"]
        current = {item["id"] for item in message["items"]}
        self.dismissed.intersection_update(current)
        self.items = sorted(message["items"], key=lambda x: {"approval": 0, "question": 1, "result": 2}[x["kind"]])
        return True

    def pending(self):
        return [item for item in self.items if item["id"] not in self.dismissed]

    def action(self, item_id, action):
        if action not in ("open", "dismiss") or not any(item["id"] == item_id for item in self.pending()):
            return False
        self.dismissed.add(item_id)
        return True
