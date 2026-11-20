"""One bounded file operation on the private broker's inherited exchange directory."""
import ctypes
import fcntl
import json
import os
from pathlib import Path
import stat
import sys

DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
MAX_ENTRIES = 128
MAX_SCANNED = 1024
RENAME_NOREPLACE = 1
OPERATIONS = {"exchange-list": (), "exchange-lstat": ("filename",),
              "exchange-rename-noreplace": ("source", "destination")}


def filename(value):
    if (not isinstance(value, str) or not value or value.startswith(".") or "/" in value
            or not value.isprintable() or len(value.encode()) > 255):
        raise ValueError("expected a visible, printable filename directly inside /exchange")
    return value


def validate(operation, names):
    if operation not in OPERATIONS or not isinstance(names, list) or len(names) != len(OPERATIONS[operation]):
        raise ValueError("invalid exchange operation or arguments")
    for name in names:
        filename(name)
    if len(names) == 2 and names[0] == names[1]:
        raise ValueError("source and destination must differ")


def inventory(directory):
    entries = []
    truncated = False
    with os.scandir(directory) as listing:
        for index, entry in enumerate(listing):
            if index >= MAX_SCANNED:
                truncated = True
                break
            if entry.name.startswith(".") or not entry.name.isprintable():
                continue
            if len(entries) == MAX_ENTRIES:
                truncated = True
                break
            entries.append(entry.name)
    return {"entries": sorted(entries), "truncated": truncated}


def operate(directory, operation, names, epoch, gate_dir=Path("/run/gate")):
    validate(operation, names)
    if operation == "exchange-list":
        # Independent stream position for each request, even across processes.
        stream_fd = os.open(".", DIRECTORY, dir_fd=directory)
        try:
            return inventory(stream_fd)
        finally:
            os.close(stream_fd)
    name = names[0]
    info = os.stat(name, dir_fd=directory, follow_symlinks=False)
    if operation == "exchange-lstat":
        return {"name": name, "inode": info.st_ino, "bytes": info.st_size, "mode": stat.filemode(info.st_mode)}
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("expected a regular source file")
    rename = ctypes.CDLL(None, use_errno=True).renameat2
    rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    rename.restype = ctypes.c_int
    # The host takes LOCK_EX on this same directory when changing the epoch.
    # Order the single name operation against cancel-all, including across resume.
    gate = os.open(gate_dir, DIRECTORY)
    try:
        fcntl.flock(gate, fcntl.LOCK_SH)
        state = json.loads((gate_dir / "state").read_bytes())
        if state["paused"] or state["epoch"] != epoch:
            raise ValueError("private input paused or admission expired")
        if rename(directory, os.fsencode(name), directory, os.fsencode(names[1]), RENAME_NOREPLACE):
            err = ctypes.get_errno()
            raise OSError(err, os.strerror(err))
    finally:
        os.close(gate)
    return {"source": name, "destination": names[1], "renamed": True}


if __name__ == "__main__":
    try:
        _, inherited, gate_dir, operation, epoch, *names = sys.argv
        print(json.dumps(operate(int(inherited), operation, names, epoch, Path(gate_dir))))
    except (ValueError, OSError) as exc:
        sys.exit(str(exc))
