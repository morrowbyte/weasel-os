"""Runs as operator inside the disposable NixOS guest, outside the private app."""
import hashlib
import json
import os
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
