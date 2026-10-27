#!/usr/bin/env python3
"""Immutable policy and evidence for broad, signed configured-channel batches.

This module permits package/configuration repairs, not replacement of its own
transaction machinery or credentials. Build evidence never asserts that every
application was exercised or that running processes adopted a new closure.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import stat
import struct
import subprocess
import sys

MODES = {"batch", "release-migration"}
HOSTS = ["nixy-laptop", "nixy-desktop", "michapc", "michapc-debug"]
PREFIXES = {"packages", "programs", "hosts", "modules", "profiles", "lib", "flake"}
PROTECTED = {"modules/daily-updates.nix", "programs/proton-infra-ssh.nix", "programs/vps-ssh.nix"}
URL = re.compile(rb'\burl\s*=\s*"([^"\n]+)"\s*;')
STATE_VERSION = re.compile(rb'\b(?:system\.|home\.)?stateVersion\s*=\s*[^;]+;')
SECURITY = re.compile(rb'(?m)^\s*[^\n]*(?:security\b|openssh\b|authorizedKeys|hashedPassword|passwordFile|extraGroups|sops\b|age\.secrets|identityFile|identityAgent)[^\n]*$')
RELEASES = {
    b"github:nixos/nixpkgs/nixos-25.11": b"github:nixos/nixpkgs/nixos-26.05",
    b"github:nix-community/home-manager/release-25.11": b"github:nix-community/home-manager/release-26.05",
}
STORE = re.compile(r"/nix/store/[0-9a-z]{32}-[A-Za-z0-9.+_?=-]+")


class BatchError(RuntimeError):
    pass


def pair(value):
    if isinstance(value, dict):
        value = (value["old"], value["new"])
    if not isinstance(value, (tuple, list)) or len(value) != 2 or not all(isinstance(v, bytes) for v in value):
        raise BatchError("Source changes must contain exact old and new bytes")
    return value


def discovery():
    spec = importlib.util.spec_from_file_location("batch_discovery", Path(__file__).with_name("weasel-update-discover.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def learning_suffix(identifier, mode, names):
    if mode not in MODES or not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}", identifier):
        raise BatchError("Invalid batch identity or mode")
    names = sorted(n for n in names if n != "agent-learnings.md")
    date = identifier[:4] + "-" + identifier[4:6] + "-" + identifier[6:8]
    return (f"\n### {date} — {mode} candidate {identifier}\n\n"
            f"- Updated existing configured sources: {', '.join(names)}.\n"
            "- Required before submission: all host evaluations, flake checks, the complete laptop build, changed critical app probes, "
            "closure inventory and session preservation. Build, activation and background completion require their own receipts.\n").encode()


def _url_transition(old, new):
    if old == new:
        return
    if old in RELEASES and new == RELEASES[old]:
        return
    # Frozen upstream releases/commits may advance within the same repository.
    # A configured rolling branch remains that branch; following master instead
    # of stable (or stable instead of nightly) is a distinct policy decision.
    pattern = rb"github:([^/]+)/([^/?]+)(?:/([^?]+))?(\?[^\n]+)?"
    a, b = re.fullmatch(pattern, old), re.fullmatch(pattern, new)
    if not a or not b or a.group(1, 2, 4) != b.group(1, 2, 4):
        raise BatchError("Input URL changes upstream repository, protocol or subdirectory")
    before, after = a[3], b[3]
    immutable = lambda v: v is not None and bool(re.fullmatch(rb"[0-9a-f]{40}|v?[0-9]+(?:[.][0-9]+)+(?:[-.][A-Za-z0-9.]+)?", v))
    if not immutable(before) or not immutable(after):
        raise BatchError("Input URL changes a configured branch/channel")


def _json(data, *, allow_identical=False):
    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                if not allow_identical or result[key] != value:
                    raise BatchError("Duplicate JSON key")
            result[key] = value
        return result
    try:
        return json.loads(data, object_pairs_hook=unique)
    except (ValueError, UnicodeError) as exc:
        raise BatchError("Invalid lock JSON") from exc


def validate_lock(old, new, mode="batch"):
    # The historical source has a repeated, byte-equivalent systems_4 node.
    # Only an unambiguous baseline duplicate is readable; candidates must be
    # canonical and cannot introduce/retain duplicate JSON declarations.
    before, after = _json(old, allow_identical=True), _json(new)
    for graph in (before, after):
        if graph.get("version") != 7 or not isinstance(graph.get("nodes"), dict) or graph.get("root") not in graph["nodes"]:
            raise BatchError("Unsupported lock graph")
        for name, node in graph["nodes"].items():
            if not isinstance(node, dict):
                raise BatchError("Invalid lock node")
            locked = node.get("locked")
            if locked is None:
                if name != graph["root"]:
                    raise BatchError("Unresolved upstream lock node")
                continue
            kind = locked.get("type")
            if kind not in {"github", "git", "path", "tarball"}:
                raise BatchError("Unsupported lock source transport")
            if kind == "path" and locked.get("path") != "./packages/t3code":
                raise BatchError("Lock references another local filesystem path")
            if kind in {"git", "tarball"} and not locked.get("url", "").startswith(("https://", "git+https://")):
                raise BatchError("Lock references a non-HTTPS upstream")
            if kind in {"github", "git"} and not re.fullmatch(r"[0-9a-f]{40,64}", locked.get("rev", "")):
                raise BatchError("Upstream revision is not immutable")
            original = node.get("original", {})
            if original.get("type") != kind:
                raise BatchError("Locked transport differs from declared upstream")
            for key in {"github": ("owner", "repo"), "git": ("url",), "path": ("path",), "tarball": ("url",)}[kind]:
                if original.get(key) != locked.get(key):
                    raise BatchError("Locked upstream identity differs from declaration")
            if original.get("rev") and original["rev"] != locked.get("rev"):
                raise BatchError("Locked revision differs from immutable declared revision")
    inputs = lambda g: set(g["nodes"][g["root"]].get("inputs", {}))
    if inputs(before) != inputs(after):
        raise BatchError("Batch adds or removes configured root inputs")
    for name in inputs(before):
        old_ref = before["nodes"][before["root"]]["inputs"][name]
        new_ref = after["nodes"][after["root"]]["inputs"][name]
        if not isinstance(old_ref, str) or not isinstance(new_ref, str):
            if old_ref != new_ref:
                raise BatchError("Root input follows policy changed")
            continue
        a, b = before["nodes"][old_ref], after["nodes"][new_ref]
        for dependency, followed in a.get("inputs", {}).items():
            if isinstance(followed, list) and b.get("inputs", {}).get(dependency) != followed:
                raise BatchError("Configured root input follows selects a different channel")
        original_a, original_b = a.get("original", {}), b.get("original", {})
        base = lambda v: {k: value for k, value in v.items() if k not in {"rev", "ref"}}
        if base(original_a) != base(original_b):
            raise BatchError("Root input upstream identity changed")
        if original_a.get("type") == "github":
            url = lambda v: ("github:" + v["owner"] + "/" + v["repo"] +
                              ("/" + v.get("ref", v.get("rev")) if v.get("ref", v.get("rev")) else "")).encode()
            _url_transition(url(original_a), url(original_b))
            if mode == "batch" and url(original_a) in RELEASES and url(original_b) != url(original_a):
                raise BatchError("Lock release change requires the explicit migration lane")
            for node in (a, b):
                if any(node["locked"].get(k) != node["original"].get(k) for k in ("type", "owner", "repo")):
                    raise BatchError("Root locked fetch differs from declared upstream")
        elif original_a.get("type") == "git":
            if (original_a.get("ref") != original_b.get("ref")
                    or (not original_a.get("rev") and a["locked"].get("ref") != b["locked"].get("ref"))):
                raise BatchError("Configured Git root switches branch/channel")
    return {"old_lock_nodes": len(before["nodes"]) - 1, "new_lock_nodes": len(after["nodes"]) - 1,
            "inputs": sorted(inputs(after))}


def validate_changes(changes, mode, identifier):
    if mode not in MODES or not isinstance(changes, dict):
        raise BatchError("Unknown batch mode")
    names = sorted(n for n in changes if n != "agent-learnings.md")
    if not names:
        raise BatchError("Batch has no changed package/configured input")
    result = {"mode": mode, "changed_sources": names, "runtime_coverage": "critical-app-probes-and-build; other apps not exercised"}
    migrated = set()
    for name in names:
        path = Path(name)
        if name.startswith("packages/codex-source/"):
            raise BatchError("Codex source/patch/toolchain bundle needs a reviewed source adapter")
        if path.is_absolute() or ".." in path.parts or name in PROTECTED:
            raise BatchError("Protected source path")
        old, new = pair(changes[name])
        if old == new:
            raise BatchError("Unchanged file listed as a transition")
        if name == "flake.nix":
            a, b = URL.findall(old), URL.findall(new)
            if len(a) != len(b) or URL.sub(b'url = "__INPUT__";', old) != URL.sub(b'url = "__INPUT__";', new):
                raise BatchError("Flake changes code beyond existing input URL literals")
            for before, after in zip(a, b):
                _url_transition(before, after)
                if before in RELEASES and after != before:
                    migrated.add(before)
            result["input_url_changes"] = [{"old": a.decode(), "new": b.decode()} for a, b in zip(a, b) if a != b]
        elif name == "flake.lock":
            result.update(validate_lock(old, new, mode))
        elif name == "packages/t3code/source.json":
            _json(old)
            _json(new)
        elif path.parts[0] not in PREFIXES or path.suffix != ".nix" or any(
                word in name.lower() for word in ("credential", "secret", "ssh", "sudo", "daily-updates", "weasel-update")):
            raise BatchError("Batch source is outside existing package/configuration scope")
        if STATE_VERSION.findall(old) != STATE_VERSION.findall(new):
            raise BatchError("Batch cannot change system/home stateVersion")
        if SECURITY.findall(old) != SECURITY.findall(new):
            raise BatchError("Batch cannot change authentication/credential policy")
        lane = {"packages/t3code/source.json": "t3", "packages/codex-source/default.nix": "codex", "packages/chatgpt/default.nix": "chatgpt"}.get(name)
        if lane:
            try:
                d = discovery()
                a, b = d.read_pin(lane, old), d.read_pin(lane, new)
                if a != b and d.version_key(b["version"]) <= d.version_key(a["version"]):
                    raise BatchError("Critical pin is a downgrade or same-version artifact substitution")
            except RuntimeError as exc:
                raise BatchError("Invalid critical published pin: " + str(exc)) from exc
    if mode == "batch" and migrated:
        raise BatchError("Release change requires the explicit release-migration lane")
    if mode == "release-migration" and (migrated != set(RELEASES) or "flake.lock" not in names):
        raise BatchError("Release migration requires the paired stable NixOS/Home Manager transition and lock")
    if mode == "release-migration":
        graph = _json(pair(changes["flake.lock"])[1])
        for name, expected in (("nixpkgs", "nixos-26.05"), ("home-manager", "release-26.05")):
            ref = graph["nodes"][graph["root"]]["inputs"][name]
            if not isinstance(ref, str) or graph["nodes"][ref]["original"].get("ref") != expected:
                raise BatchError("Migration lock does not match the paired release declarations")
    if "agent-learnings.md" in changes:
        old, new = pair(changes["agent-learnings.md"])
        if new != old + learning_suffix(identifier, mode, names):
            raise BatchError("Batch learning is not the independently generated append")
    else:
        raise BatchError("Batch needs its deterministic append-only learning")
    return result


def _command(args):
    run = subprocess.run(args, check=False, capture_output=True, timeout=180)
    if run.returncode:
        raise BatchError("Closure inventory command failed: " + run.stderr.decode(errors="replace")[-500:])
    return run.stdout


def _closure(system):
    system = str(system)
    if not STORE.fullmatch(system):
        raise BatchError("Closure root is not an immutable Store path")
    paths = set(_command(["nix-store", "--query", "--requisites", system]).decode().splitlines())
    if system not in paths or any(not STORE.fullmatch(p) for p in paths):
        raise BatchError("Invalid or incomplete closure inventory")
    return paths


def verify_closure(old_system, new_system, app_pairs, changes):
    before, after = _closure(old_system), _closure(new_system)
    if before == after:
        raise BatchError("Batch produces no new system closure")
    for pair_ in app_pairs.values():
        a, b = (pair_["old"], pair_["new"]) if isinstance(pair_, dict) else pair_
        if str(a) not in before or str(b) not in after:
            raise BatchError("Critical app is missing from the built system closure")
    return {"ok": True, "policy": "broad-batch-inventory", "old_system": str(old_system), "new_system": str(new_system),
            "removed": sorted(before - after), "added": sorted(after - before), "unchanged_count": len(before & after),
            "old_inventory_sha256": hashlib.sha256("\n".join(sorted(before)).encode()).hexdigest(),
            "new_inventory_sha256": hashlib.sha256("\n".join(sorted(after)).encode()).hexdigest(),
            "critical_apps": app_pairs, "source_changes": sorted(changes),
            "coverage": "full builds and critical probes; inventory does not prove every package runtime"}


def _nv_bound(path, roots, *, directory=False):
    """Resolve only immutable artifacts belonging to this exact system closure."""
    try:
        resolved = Path(path).resolve(strict=True)
        root = next((p for p in (resolved, *resolved.parents) if STORE.fullmatch(str(p))), None)
        if root is None or str(root) not in roots:
            raise BatchError("NVIDIA artifact escapes the exact immutable closure")
        cursor = resolved if directory else resolved.parent
        while True:
            info = cursor.stat()
            if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o222:
                raise BatchError("Writable/non-directory NVIDIA artifact parent")
            if cursor == root:
                break
            cursor = cursor.parent
        if directory:
            return resolved
        info = resolved.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o222:
            raise BatchError("Writable/non-regular NVIDIA artifact")
        return resolved
    except BatchError:
        raise
    except (OSError, RuntimeError) as exc:
        raise BatchError("Unavailable immutable NVIDIA artifact") from exc


def _nv_read(path, roots, *, maximum):
    resolved = _nv_bound(path, roots)
    try:
        fd = os.open(resolved, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_mode & 0o222 or before.st_size > maximum:
                raise BatchError("Invalid NVIDIA artifact type/mode/size")
            data = stream.read(maximum + 1)
            after = os.fstat(stream.fileno())
        identity = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if len(data) > maximum or identity(before) != identity(after) or _nv_bound(path, roots) != resolved:
            raise BatchError("NVIDIA artifact changed during verification")
        return resolved, data
    except OSError as exc:
        raise BatchError("Cannot read bound NVIDIA artifact") from exc


def _nvidia_selection(system):
    """Bind generated L+ graphics selection to the actual selected NVML file."""
    system = Path(system)
    roots = _closure(system)
    _nv_bound(system, roots, directory=True)
    configs = system / "etc/tmpfiles.d"
    rules = []
    if configs.exists():
        _nv_bound(configs, roots, directory=True)
        for entry in sorted(configs.glob("*.conf")):
            path, raw = _nv_read(entry, roots, maximum=2 * 1024 * 1024)
            try:
                for line in raw.decode("utf-8").splitlines():
                    fields = shlex.split(line, comments=True)
                    if len(fields) > 1 and fields[1] == "/run/opengl-driver":
                        if len(fields) != 7 or fields[0] != "L+" or any(x != "-" for x in fields[2:6]):
                            raise BatchError("Unsupported generated graphics-driver selection")
                        rules.append({"config": str(path), "config_sha256": hashlib.sha256(raw).hexdigest(),
                                      "graphics": fields[6]})
            except (UnicodeError, ValueError) as exc:
                raise BatchError("Cannot parse immutable graphics-driver rules") from exc
    elif configs.is_symlink():
        raise BatchError("Broken generated tmpfiles directory")
    if len(rules) > 1:
        raise BatchError("Ambiguous generated graphics-driver selection")
    if not rules:
        return {"nvidia": False, "graphics_rule": None}
    rule = rules[0]
    if not STORE.fullmatch(rule["graphics"]):
        raise BatchError("Graphics target is not an immutable Store output")
    _nv_bound(rule["graphics"], roots, directory=True)
    library = Path(rule["graphics"]) / "lib/libnvidia-ml.so.1"
    if not library.exists():
        if library.is_symlink():
            raise BatchError("Broken selected NVIDIA library")
        return {"nvidia": False, "graphics_rule": rule}
    path, raw = _nv_read(library, roots, maximum=64 * 1024 * 1024)
    match = re.fullmatch(r"libnvidia-ml\.so\.([0-9]+\.[0-9]+(?:\.[0-9]+)?)", path.name)
    if not match:
        raise BatchError("Selected NVIDIA library has unknown exact version")
    # No dlopen/GPU access: this checks ELF64/shared-object shape only.
    # Shape and the versioned filename do not prove vendor authenticity;
    # the existing trusted source/closure checks remain mandatory.
    if len(raw) < 64 or raw[:5] != b"\x7fELF\x02" or raw[5] not in (1, 2):
        raise BatchError("Selected NVIDIA library is not ELF64")
    byteorder = "<" if raw[5] == 1 else ">"
    if struct.unpack_from(byteorder + "H", raw, 16)[0] != 3:
        raise BatchError("Selected NVIDIA library is not a shared ELF object")
    return {"nvidia": True, "graphics_rule": rule, "library": str(path), "version": match[1],
            "library_sha256": hashlib.sha256(raw).hexdigest()}


def _loaded_nvidia_version():
    # Fixed kernel metadata only. Neither caller JSON nor candidate source can opt out.
    path = Path("/sys/module/nvidia/version")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise BatchError("Loaded NVIDIA version is not regular kernel metadata")
            raw = stream.read(129)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise BatchError("Cannot establish loaded NVIDIA driver version") from exc
    if not re.fullmatch(rb"[0-9]+\.[0-9]+(?:\.[0-9]+)?\n?", raw):
        raise BatchError("Unknown or unbounded loaded NVIDIA driver version")
    return raw.decode("ascii").strip()


def verify_nvidia_live_abi(old_system, new_system):
    loaded = _loaded_nvidia_version()
    before, after = _nvidia_selection(old_system), _nvidia_selection(new_system)
    if after["nvidia"]:
        if loaded is None or after["version"] != loaded:
            raise BatchError("Candidate NVIDIA userspace does not match the loaded kernel driver; coordinated boot required")
    elif before["nvidia"] or loaded is not None:
        raise BatchError("Candidate removes or cannot bind userspace for an existing NVIDIA driver")
    # Re-read selection, hashes and kernel metadata. Root invokes this again directly
    # before activating, independently of the preparer's candidate JSON receipt.
    if (_loaded_nvidia_version() != loaded or _nvidia_selection(old_system) != before
            or _nvidia_selection(new_system) != after):
        raise BatchError("NVIDIA selection or loaded kernel driver changed during verification")
    return {"ok": True, "old": before, "new": after, "loaded_kernel_driver": loaded,
            "policy": "exact-immutable-nvml-vs-loaded-kernel-version",
            "coverage": "static selected NVML ELF/version/hash and fixed loaded module metadata; no GPU query, module load, modeset or hardware acceptance"}


def verify_runtime_units(old_system, new_system):
    nvidia_abi = verify_nvidia_live_abi(old_system, new_system)
    checked = []
    def unit(path):
        content = path.read_bytes()
        # systemd aliases also inherit the target unit's drop-ins. Match
        # override names in lexical order; alias-specific files win a tie.
        directories = [path.parent / (path.resolve().name + '.d'), Path(str(path) + '.d')]
        overrides = {}
        for directory in directories:
            if directory.exists():
                overrides.update({entry.name: entry for entry in directory.glob('*.conf')})
        for name in sorted(overrides):
            content += b'\n' + overrides[name].read_bytes()
        return content
    for relative in ("etc/systemd/system/display-manager.service", "etc/systemd/user/niri.service", "etc/systemd/user/niri-session.service"):
        old, new = Path(old_system) / relative, Path(new_system) / relative
        if not old.exists() and not new.exists():
            continue
        if old.exists() != new.exists():
            raise BatchError("Batch adds/removes the active graphical session unit")
        before, after = unit(old), unit(new)
        section, restart = None, None
        for line in after.splitlines():
            line = line.strip()
            if line.startswith(b'[') and line.endswith(b']'):
                section = line[1:-1]
            elif section == b'Service' and line.startswith(b'X-RestartIfChanged='):
                restart = line.split(b'=', 1)[1].strip()
        if before != after and restart != b'false':
            raise BatchError("Changed graphical session unit would restart the user's session")
        checked.append({"unit": relative, "changed": before != after, "restart_on_change": False if before != after else "unchanged"})
    return {"ok": True, "checked": checked, "nvidia_abi": nvidia_abi, "reboot": "not performed; existing kernel/session may remain running"}


def codex_ownership_predicate(source, host):
    if host != "nixy-laptop":
        return "true"
    package = str(Path(source).absolute() / "packages/codex-source")
    adapter = str(Path(source).absolute() / "packages/codex-acp.nix")
    return (
        "(let p = import f.inputs.nixpkgs-unstable { "
        "system = host.pkgs.stdenv.hostPlatform.system; config.allowUnfree = true; }; "
        "expectedCodex = p.callPackage " + package + " {}; "
        "expectedAcp = p.callPackage " + adapter + " { codexPackage = expectedCodex; }; "
        "home = hm.evilweasel; selected = home.weasel.hephaestusRecoveryConsole.package; "
        "packages = home.home.packages; "
        "in selected.drvPath == expectedCodex.drvPath && "
        "builtins.any (pkg: (pkg.drvPath or null) == expectedAcp.drvPath) packages && "
        "builtins.all (pkg: if builtins.elem (pkg.pname or \"\") [\"codex\" \"codex-scoped-cancel\"] "
        "then (pkg.drvPath or null) == expectedCodex.drvPath else true) packages)"
    )


def sensitive_eval_args(expression):
    """Read cached IFD context without local/remote builds or substitution."""
    if not isinstance(expression, str) or not expression.strip() or "\0" in expression:
        raise BatchError("Sensitive proof requires a nonempty expression")
    return ["eval", "--raw", "--impure", "--no-write-lock-file", "--offline",
            "--max-jobs", "0", "--builders", "", "--option", "substitute", "false",
            "--option", "allow-import-from-derivation", "true", "--expr", expression]


def sensitive_expression(source, host):
    """Hash semantic auth/state options in Nix, never return credential values."""
    if host not in HOSTS:
        raise BatchError("Unknown invariant host")
    uri = json.dumps("path:" + str(Path(source).absolute()))
    return ('let f = builtins.getFlake ' + uri + '; host = f.nixosConfigurations.' + host + '; c = host.config; '
            'select = names: value: builtins.intersectAttrs (builtins.listToAttrs '
            '(map (name: { inherit name; value = null; }) names)) value; '
            'generatedUnit = u: (u.unitConfig or {}) // builtins.listToAttrs (builtins.concatLists '
            '(map (names: let key = builtins.elemAt names 0; name = builtins.elemAt names 1; '
            'in if builtins.hasAttr key u then [{ inherit name; value = if builtins.isList u.${key} '
            'then builtins.concatStringsSep " " u.${key} else u.${key}; }] else []) '
            '[["description" "Description"] ["after" "After"] ["before" "Before"] '
            '["wants" "Wants"] ["requires" "Requires"]])); '
            'hm = c.home-manager.users or {}; '
            'expected = import ' + str(Path(source).absolute() / 'modules/daily-updates.nix') + ' { config = c; pkgs = host.pkgs; }; '
            'serviceKeys = builtins.attrNames expected.systemd.services.weasel-update-activate.serviceConfig; '
            'actualService = c.systemd.services.weasel-update-activate or {}; '
            'serviceOkay = builtins.toJSON (actualService.serviceConfig or {}) == '
            'builtins.toJSON expected.systemd.services.weasel-update-activate.serviceConfig; '
            'pathOkay = builtins.toJSON (c.systemd.paths.weasel-update-activate.pathConfig or {}) == '
            'builtins.toJSON expected.systemd.paths.weasel-update-activate.pathConfig; '
            'hasUpdater = builtins.hasAttr "weasel-update-activate" c.systemd.services; '
            'serviceUnitOkay = builtins.toJSON (actualService.unitConfig or {}) == '
            'builtins.toJSON (generatedUnit expected.systemd.services.weasel-update-activate); '
            'pathUnitOkay = builtins.toJSON (c.systemd.paths.weasel-update-activate.unitConfig or {}) == '
            'builtins.toJSON (generatedUnit expected.systemd.paths.weasel-update-activate); '
            'restartPolicyOkay = (actualService.restartIfChanged or false) == '
            '(expected.systemd.services.weasel-update-activate.restartIfChanged or false); '
            'safeEnvironment = builtins.all (n: builtins.elem n ["PATH"]) '
            '(builtins.attrNames (actualService.environment or {})) && '
            'builtins.all (n: builtins.elem n ["LOCALE_ARCHIVE" "TZDIR"]) '
            '(builtins.attrNames (c.systemd.globalEnvironment or {})); '
            'enabled = (actualService.enable or false) && (c.systemd.paths.weasel-update-activate.enable or false); '
            'wanted = (c.systemd.paths.weasel-update-activate.wantedBy or []) == expected.systemd.paths.weasel-update-activate.wantedBy; '
            'tools = map toString c.environment.systemPackages; '
            'hasTools = builtins.all (p: builtins.elem (toString p) tools) expected.environment.systemPackages; '
            'codexOwnershipOkay = ' + codex_ownership_predicate(source, host) + '; '
            'v = { stateVersion = c.system.stateVersion; '
            'homeStateVersions = builtins.mapAttrs (_: h: h.home.stateVersion) hm; '
            'homeInfrastructure = builtins.mapAttrs (_: h: let '
            'file = h.home.file.".ssh/weasel-vps.conf" or {}; '
            'source = file.source or null; '
            'agent = h.systemd.user.services.weasel-proton-infra-ssh-agent or null; '
            'in { ssh = if source == null then null else { '
            'sha256 = builtins.hashString "sha256" (builtins.readFile source); force = file.force or false; }; '
            'agent = if agent == null then null else { Unit = agent.Unit or {}; Install = agent.Install or {}; '
            'Service = (agent.Service or {}) // { ExecStart = let raw = agent.Service.ExecStart or ""; '
            'commands = if builtins.isList raw then raw else [raw]; '
            'in map (command: let matched = builtins.match "/nix/store/[^/]+(/bin/pass-cli .*)" command; '
            'in if matched == null then throw "Infrastructure SSH agent command changed" else builtins.head matched) commands; }; }; }) hm; '
            'mutableUsers = c.users.mutableUsers; '
            'users = builtins.mapAttrs (_: u: select ["uid" "isNormalUser" "isSystemUser" "home" "group" '
            '"extraGroups" "hashedPassword" "hashedPasswordFile" "password" "initialPassword" '
            '"initialHashedPassword" "openssh"] u) c.users.users; '
            'ssh = select ["enable" "openFirewall" "settings" "authorizedKeysFiles"] c.services.openssh; '
            'sudo = select ["enable" "wheelNeedsPassword" "extraRules" "extraConfig"] c.security.sudo; '
            'polkit = select ["enable" "extraConfig"] c.security.polkit; '
            'doas = select ["enable" "extraRules" "extraConfig"] (c.security.doas or {}); '
            'nix = select ["trusted-users" "allowed-users" "require-sigs" "trusted-public-keys" "substituters"] c.nix.settings; '
            '}; in assert (' + ('hasUpdater && ' if host == 'nixy-laptop' else '!hasUpdater || ') +
            '(codexOwnershipOkay && serviceOkay && pathOkay && serviceUnitOkay && pathUnitOkay && restartPolicyOkay && '
            'safeEnvironment && enabled && wanted && hasTools)); '
            'builtins.hashString "sha256" (builtins.toJSON v)')


def update_hosts(mode):
    if mode == "batch":
        return ["nixy-laptop"]
    if mode == "release-migration":
        return list(HOSTS)
    raise BatchError("Unknown sensitive-state lane")


def verify_sensitive_state(old_source, new_source, evaluator, *, mode="batch", migration_executor=None):
    checks, releases, transitions, ssh_proofs = {}, {}, {}, {}
    if mode not in {"batch", "release-migration"}:
        raise BatchError("Unknown sensitive-state lane")
    spec = importlib.util.spec_from_file_location("batch_migration", Path(__file__).with_name("weasel-update-migration.py"))
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    for host in update_hosts(mode):
        # The ordinary SSH proof already evaluates and rechecks both raw
        # authentication fingerprints. Reuse those fresh values.
        if mode == "batch":
            try:
                transition = migration.verify_general(sensitive_expression, old_source, new_source,
                                                      host, evaluator, migration_executor)
            except RuntimeError as error:
                raise BatchError("Protected authentication/SSH check failed: " + host + ": " + str(error)) from error
            before, after = transition["old"], transition["new"]
        else:
            before = evaluator(sensitive_expression(old_source, host)).strip()
            after = evaluator(sensitive_expression(new_source, host)).strip()
        if isinstance(before, bytes):
            before = before.decode()
        if isinstance(after, bytes):
            after = after.decode()
        if not re.fullmatch(r"[0-9a-f]{64}", before) or not re.fullmatch(r"[0-9a-f]{64}", after):
            raise BatchError("Invalid authentication/stateVersion fingerprint: " + host)
        if mode == "batch" and before != after:
            raise BatchError("Batch changes evaluated authentication or stateVersion policy: " + host)
        if mode == "release-migration":
            try:
                transition = migration.verify(sensitive_expression, old_source, new_source,
                                              host, evaluator, migration_executor)
            except RuntimeError as error:
                raise BatchError("Protected authentication/SSH check failed: " + host + ": " + str(error)) from error
            if transition["old"] != before or transition["new"] != after:
                raise BatchError("Authentication policy changed during fresh SSH proof: " + host)
            # Preserve the separate release migration's multi-host proof.
            again_before = evaluator(sensitive_expression(old_source, host)).strip()
            if isinstance(again_before, bytes):
                again_before = again_before.decode()
            if again_before != before:
                raise BatchError("Baseline authentication changed during SSH proof: " + host)
            again = evaluator(sensitive_expression(new_source, host)).strip()
            if isinstance(again, bytes):
                again = again.decode()
            if again != after:
                raise BatchError("Candidate authentication changed during SSH proof: " + host)
            transitions[host] = transition
        ssh_proofs[host] = transition
        checks[host] = before
        release = lambda source: ('let f = builtins.getFlake ' + json.dumps("path:" + str(Path(source).absolute())) +
                                  '; in f.nixosConfigurations.' + host + '.config.system.nixos.release')
        a, b = evaluator(release(old_source)).strip(), evaluator(release(new_source)).strip()
        a, b = (v.decode() if isinstance(v, bytes) else v for v in (a, b))
        if (mode == "batch" and a != b) or (mode == "release-migration" and (a, b) != ("25.11", "26.05")):
            raise BatchError("Built NixOS release does not match the typed candidate lane: " + host)
        releases[host] = {"old": a, "new": b}
    return {"ok": True, "auth_and_state_version_sha256": checks, "nixos_releases": releases, "reviewed_migration_transitions": transitions, "protected_ssh": ssh_proofs}


def verify_published(old_source, new_source):
    if os.geteuid() == 0:
        raise BatchError("Published-source discovery runs as the ordinary user")
    d, checked = discovery(), {}
    for lane, filename in d.SOURCE_FILES.items():
        if lane == "codex":
            try:
                a, b = d.codex_source_bundle(old_source), d.codex_source_bundle(new_source)
            except (OSError, ValueError, RuntimeError) as error:
                raise BatchError("Invalid Codex source bundle: " + str(error)) from error
            if a != b:
                raise BatchError(d.CODEX_ADAPTER_REASON)
            checked[lane] = {"changed": False, "kind": "pinned-source", "bundle": b,
                             "verification": "Exact trusted baseline bundle preserved; no npm/source refresh claim"}
            continue
        a = d.read_pin(lane, (Path(old_source) / filename).read_bytes())
        b = d.read_pin(lane, (Path(new_source) / filename).read_bytes())
        if a == b:
            checked[lane] = {"changed": False}
            continue
        if d.version_key(b["version"]) <= d.version_key(a["version"]):
            raise BatchError("Published pin is not an upgrade")
        exact, evidence = d.verify_exact(lane, b["version"], d.Network(lane))
        if exact != b:
            raise BatchError("Batch pin differs from the exact official published artifact")
        checked[lane] = {"changed": True, "pin": b, "evidence": evidence}
    return {"ok": True, "published_pins": checked}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-published", action="store_true", required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(verify_published(args.baseline, args.source), sort_keys=True))
    except Exception as error:
        print(json.dumps({"ok": False, "reason": str(error)}), file=sys.stderr)
        raise SystemExit(1)
