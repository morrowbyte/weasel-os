"""Seed the local provider only on first launch of a packaged Crew desktop."""

import fcntl
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile


def main():
    explicit_home = os.environ.get("T3CODE_HOME", "").strip()
    if os.environ.get("ELECTRON_RUN_AS_NODE") or os.environ.get("VITE_DEV_SERVER_URL"):
        return
    profile = os.environ.get("T3CODE_DESKTOP_PROFILE", "morrowbyte").strip()
    default_home = Path.home() / (f".t3-{profile}" if profile else ".t3")
    base = Path(explicit_home) if explicit_home else default_home
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    base_fd = os.open(base, os.O_DIRECTORY | os.O_RDONLY)
    try:
        fcntl.flock(base_fd, fcntl.LOCK_EX)
        state = base / "userdata"
        if any(entry != state for entry in base.iterdir()):
            return
        state.mkdir(mode=0o700, exist_ok=True)
        temp_prefix = ".crew-initial-settings-"
        if any(not entry.name.startswith(temp_prefix) for entry in state.iterdir()):
            return
        config = {
            "setupMode": "existing",
            "binaryPath": "/run/current-system/sw/bin/crew-codex",
        }
        instance = {"driver": "codex", "enabled": True, "config": config}
        # Publish a complete writable file; another settings writer always wins.
        with NamedTemporaryFile(mode="w", dir=state, prefix=temp_prefix) as temporary:
            json.dump({"providerInstances": {"codex": instance}}, temporary)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
            try:
                os.link(temporary.name, state / "settings.json")
            except FileExistsError:
                pass
    finally:
        os.close(base_fd)


if __name__ == "__main__":
    main()
