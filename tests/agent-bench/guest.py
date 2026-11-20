"""Runs as operator inside the disposable NixOS guest, outside the private app."""
import ctypes
import hashlib
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path.home() / "bench"
EXCHANGE = ROOT / "exchange"
ORIGINAL = b"Weekend handover\n\nDelivery is due on Monday.\n\nReference: 4815.\n"
KEEP = b"Inventory: 17 crates.\n"
SCENARIOS = {
    "edit": (
        {"Handover.txt": ORIGINAL, "Inventory.txt": KEEP},
        {"Handover-Friday.txt": ORIGINAL.replace(b"Monday", b"Friday")},
    ),
    "files": (
        {"Invoice-draft.txt": b"Invoice 118\nTotal EUR 72\n", "Inventory.txt": KEEP},
        {"Invoice-118.txt": b"Invoice 118\nTotal EUR 72\n"},
    ),
}


def snapshot():
    result = {}
    for path in EXCHANGE.iterdir():
        if path.name == ".weasel-workspace-exchange":
            continue
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
            result[path.name] = None
        else:
            result[path.name] = path.read_bytes()
    return result


def expected(family):
    source, changes = SCENARIOS[family]
    files = source | changes
    if family == "files":
        del files["Invoice-draft.txt"]
    return files


def manifest(files):
    return {name: hashlib.sha256(data).hexdigest() if data is not None else None
            for name, data in files.items()}


def watch(family):
    target = expected(family)
    (ROOT / "watch-ready").touch()
    while True:
        try:
            matches = snapshot() == target
        except FileNotFoundError:
            matches = False
        if matches:
            # First observed exact directory state, sampled every 20 ms plus scheduling.
            (ROOT / "effect-ns").write_text(str(time.monotonic_ns()))
            return
        time.sleep(0.02)


def file_name(value):
    if not isinstance(value, str) or not value or value.startswith(".") or "/" in value or "\0" in value:
        raise ValueError("expected a visible filename directly inside the exchange directory")
    return value


def file_operations(request):
    fields = {"list": set(), "lstat": {"name"}, "rename-noreplace": {"from", "to"}}
    steps = request["steps"]
    for step in steps:
        args = step.get("args", {})
        if step["action"] not in fields or not isinstance(args, dict) or set(args) != fields[step["action"]]:
            raise ValueError("invalid scoped file action or arguments")
        for value in args.values():
            file_name(value)

    libc = ctypes.CDLL(None, use_errno=True)
    rename = libc.renameat2
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    directory = os.open(EXCHANGE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    records = []
    started = time.monotonic_ns()
    try:
        # Keep filesystem request IDs separate from user-controlled exchange names.
        with (ROOT / ("fs-request-" + request["id"])).open("x"):
            pass
        for step in steps:
            action, args = step["action"], step.get("args", {})
            before = time.monotonic_ns()
            try:
                if action == "list":
                    value = sorted(n for n in os.listdir(directory) if not n.startswith("."))
                else:
                    name = args["name"] if action == "lstat" else args["from"]
                    info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                    value = {"name": name, "inode": info.st_ino, "bytes": info.st_size,
                             "mode": stat.filemode(info.st_mode)}
                    if action == "rename-noreplace":
                        if not stat.S_ISREG(info.st_mode):
                            raise ValueError("expected an ordinary source file")
                        if name == args["to"]:
                            raise ValueError("expected two different names")
                        # Linux RENAME_NOREPLACE atomically protects an existing destination.
                        if rename(directory, os.fsencode(name), directory, os.fsencode(args["to"]), 1):
                            err = ctypes.get_errno()
                            raise OSError(err, os.strerror(err))
                        value["name"] = args["to"]
                records.append({"action": action, "ok": True, "result": value,
                                "duration_ns": time.monotonic_ns() - before})
            except (ValueError, OSError) as exc:
                records.append({"action": action, "ok": False, "error": str(exc),
                                "duration_ns": time.monotonic_ns() - before})
                break
    finally:
        os.close(directory)
    ok = records[-1]["ok"]
    status = "completed" if ok else ("partial" if len(records) > 1 else "stopped")
    return {"guest_start_ns": started, "guest_end_ns": time.monotonic_ns(), "cli_exit": int(not ok),
            "product": {"status": status, "steps": records, "remaining": len(steps) - len(records),
                        "request_id": request["id"], "automatic_replay": False}}


def compose():
    request = json.load(sys.stdin)
    start = time.monotonic_ns()
    call = subprocess.run(
        ["weasel", "compose", "--request-id", request["id"], "-"],
        input=json.dumps(request["steps"]), text=True, capture_output=True, timeout=90,
    )
    end = time.monotonic_ns()
    try:
        product = json.loads(call.stdout)
    except ValueError:
        product = {"status": "error", "error": (call.stderr or call.stdout)[-1500:]}
    for image in product.pop("images", []):
        import base64
        path = ROOT / (request["id"] + ".png")
        path.write_bytes(base64.b64decode(image["data"], validate=True))
        product["screenshot"] = path.name
    return {"guest_start_ns": start, "guest_end_ns": end, "cli_exit": call.returncode,
            "product": product}


def main():
    action, family = sys.argv[1:]
    if action == "seed":
        for name, data in SCENARIOS[family][0].items():
            with (EXCHANGE / name).open("xb") as out:
                out.write(data)
        return
    if action == "watch":
        watch(family)
        return
    if action == "compose":
        print(json.dumps(compose()))
    elif action == "files-api" and family == "files":
        print(json.dumps(file_operations(json.load(sys.stdin))))
    elif action == "verify":
        files = snapshot()
        effect_path = ROOT / "effect-ns"
        effect = int(effect_path.read_text()) if effect_path.exists() else None
        print(json.dumps({"files_ok": files == expected(family), "effect_ns": effect,
                          "actual_sha256": manifest(files),
                          "expected_sha256": manifest(expected(family))}))
    else:
        raise ValueError(action)


if __name__ == "__main__":
    os.umask(0o077)
    main()
