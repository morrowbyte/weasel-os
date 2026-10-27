#!/usr/bin/env python3
"""Failure-path tests for the privileged source transaction; no system activation."""
import importlib.util
from contextlib import ExitStack
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location("activation", Path(__file__).resolve().parents[1] / "scripts/weasel-update-activate.py")
activation = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = activation
SPEC.loader.exec_module(activation)


class LocalGit:
    """Unprivileged fixture Git; never invokes sudo, runuser, Nix or real services."""
    def __init__(self):
        self.account = types.SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())

    def git(self, repository, *args, extra_env=None):
        env = os.environ.copy()
        env["GIT_OPTIONAL_LOCKS"] = "0"
        env["GIT_NO_REPLACE_OBJECTS"] = "1"
        if extra_env:
            env.update(extra_env)
        result = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-C", str(repository), *map(str, args)],
                                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if result.returncode:
            raise activation.ActivationError("Fixture Git refused operation")
        return result.stdout


class ActivationTests(unittest.TestCase):
    def test_real_store_multicall_tools_keep_the_requested_program(self):
        # Match the service's immutable runtime PATH, independent of a harness
        # that places mutable FHS aliases such as /usr/bin/env first.
        bins = [str(Path(shutil.which(name)).resolve().parent) for name in ['env', 'bash', 'nix-store']]
        with patch.dict(os.environ, {"PATH": os.pathsep.join(bins)}):
            runner = activation.Runner()
            result = runner.run([runner.executable("env"), "-i", "WEASEL_FIXTURE=isolated",
                                 runner.executable("bash"), "-c", 'printf %s "$WEASEL_FIXTURE"'])
            self.assertEqual(result, b"isolated")
            version = runner.run([runner.executable("nix-store"), "--version"])
            self.assertIn(b"nix-store (Nix)", version)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="weasel-update-activation-test-")
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)

    def request(self):
        return {"schema": 1, "id": "20261008T140000Z-1234abcd", "baseline_commit": "a" * 40,
                "baseline_system": "/nix/store/" + "a" * 32 + "-nixos-system-nixy-laptop-25.11.20260501.26ef669",
                "candidate_commit": "b" * 40, "candidate_ref": "refs/heads/weasel-update-20261008T140000Z-1234abcd"}

    def fixture(self):
        runner = LocalGit()
        repo = self.root / "repository"
        repo.mkdir()
        runner.git(repo, "init", "-b", "main")
        runner.git(repo, "config", "user.name", "Fixture")
        runner.git(repo, "config", "user.email", "fixture@example.invalid")
        runner.git(repo, "config", "commit.gpgsign", "false")
        source = repo / "packages/t3code/source.json"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"old source\n")
        (repo / "agent-learnings.md").write_bytes(b"previous learning\n")
        runner.git(repo, "add", ".")
        runner.git(repo, "commit", "-m", "baseline")
        baseline = runner.git(repo, "rev-parse", "HEAD").decode().strip()
        before, index = activation.repository_manifest(runner, repo, baseline)
        runner.git(repo, "switch", "-c", "candidate")
        source.write_bytes(b"new source\n")
        (repo / "agent-learnings.md").write_bytes(b"previous learning\nnew learning\n")
        runner.git(repo, "add", ".")
        runner.git(repo, "commit", "-m", "candidate")
        candidate = runner.git(repo, "rev-parse", "HEAD").decode().strip()
        runner.git(repo, "switch", "main")
        # Git recreates files/index during switch; capture the actual current identity.
        before, index = activation.repository_manifest(runner, repo, baseline)
        backup = self.root / "backup"
        backup.mkdir()
        workspace = self.root / "workspace"
        workspace.mkdir()
        request = self.request()
        request.update(baseline_commit=baseline, candidate_commit=candidate)
        changes = {"packages/t3code/source.json": (b"old source\n", b"new source\n"),
                   "agent-learnings.md": (b"previous learning\n", b"previous learning\nnew learning\n")}
        return runner, repo, request, before, index, changes, backup, workspace

    def test_strict_request_rejects_commands_paths_and_duplicate_keys(self):
        request = self.request()
        self.assertEqual(activation.parse_request(json.dumps(request).encode()), request)
        for key, value in [("command", "sh"), ("candidate_commit", "HEAD"), ("candidate_ref", "refs/heads/main"),
                           ("baseline_system", "/tmp/fake-system"), ("schema", True), ("id", "../../escape")]:
            bad = dict(request)
            bad[key] = value
            with self.subTest(key=key), self.assertRaises(activation.ActivationError):
                activation.parse_request(json.dumps(bad).encode())
        with self.assertRaises(activation.ActivationError):
            activation.parse_request(b'{"schema":1,"schema":1}')

    def test_request_rejects_oversize_and_nul(self):
        with self.assertRaises(activation.ActivationError):
            activation.parse_request(b" " * 65537)
        request = self.request()
        request["candidate_ref"] += "\0"
        with self.assertRaises(activation.ActivationError):
            activation.parse_request(json.dumps(request).encode())

    def test_schema2_has_explicit_modes_without_broadening_schema1(self):
        for mode in ("batch", "release-migration"):
            request = dict(self.request(), schema=2, mode=mode)
            self.assertEqual(activation.parse_request(json.dumps(request).encode()), request)
        for extra in ({"schema": 2}, {"schema": 2, "mode": "pins"}, {"schema": 2, "mode": []},
                      {"schema": 1, "mode": "batch"}, {"schema": 3, "mode": "batch"},
                      {"schema": 2, "mode": "batch", "script": "/tmp/test.py"}):
            request = dict(self.request(), **extra)
            with self.subTest(extra=extra), self.assertRaises(activation.ActivationError):
                activation.parse_request(json.dumps(request).encode())

    def test_request_file_rejects_symlink_and_hardlinks(self):
        real = self.root / "real"
        real.write_bytes(b"{}")
        linked = self.root / "symlink"
        linked.symlink_to(real)
        with self.assertRaises(OSError):
            activation.read_regular(linked)
        hard = self.root / "hard"
        os.link(real, hard)
        with self.assertRaises(activation.ActivationError):
            activation.read_regular(real)

    def test_request_claim_preserves_newer_file(self):
        inbox, archive = self.root / "request.json", self.root / "archived.json"
        inbox.write_bytes(b"original")
        actual_rename = activation.rename_no_replace
        def race(source, destination):
            if source == inbox:
                newer = self.root / "newer"
                newer.write_bytes(b"concurrent request")
                os.replace(newer, inbox)
            actual_rename(source, destination)
        with patch.object(activation, "rename_no_replace", race), self.assertRaises(activation.ActivationError):
            activation.consume_request(inbox, archive, os.getuid())
        self.assertEqual(inbox.read_bytes(), b"concurrent request")
        self.assertEqual(archive.with_suffix(".original.json").read_bytes(), b"original")

    def test_interrupted_journal_blocks_even_no_candidate(self):
        runs = self.root / "runs"
        runs.mkdir()
        for phase in ["preparing", "tested", "checkpoint-created", "integrating", "source-integrated", "activating", "publishing", "recovery-required"]:
            run = runs / phase
            run.mkdir()
            (run / "journal.json").write_bytes(activation.encoded({"phase": phase}))
            with self.subTest(phase=phase), self.assertRaises(activation.ActivationError):
                activation.check_recovery(self.root)
            shutil.rmtree(run)
        for phase in activation.TERMINAL_PHASES:
            run = runs / phase
            run.mkdir()
            (run / "journal.json").write_bytes(activation.encoded({"phase": phase}))
        activation.check_recovery(self.root)

    def test_missing_daily_journal_blocks_without_deleting_retained_artifacts(self):
        run = self.root / "runs" / "20261008T140000Z-1234abcd"
        run.mkdir(parents=True)
        for read_only in [True, False]:
            with self.subTest(stage="before-first-journal", read_only=read_only):
                with self.assertRaises(activation.RecoveryRequired):
                    activation.check_recovery(self.root, read_only=read_only)
                self.assertTrue(run.is_dir())
                self.assertEqual(list(run.iterdir()), [])
        journal = run / "journal.json"
        journal.write_bytes(activation.encoded({"marker": activation.OWNER_TAG, "id": run.name, "phase": "pruning"}))
        retained = run / "retained-proof.json"
        retained.write_bytes(b"proof from interrupted cleanup\n")
        # A cleanup interruption can remove the journal before the run directory.
        journal.unlink()
        for read_only in [True, False]:
            with self.subTest(stage="partially-pruned-run", read_only=read_only):
                with self.assertRaises(activation.RecoveryRequired):
                    activation.check_recovery(self.root, read_only=read_only)
                self.assertFalse(journal.exists())
                self.assertEqual(retained.read_bytes(), b"proof from interrupted cleanup\n")

    def test_existing_file_destination_is_never_overwritten(self):
        source, destination = self.root / "source", self.root / "dest"
        source.write_bytes(b"source")
        destination.write_bytes(b"unrelated")
        with self.assertRaises(FileExistsError):
            activation.rename_no_replace(source, destination)
        self.assertEqual(source.read_bytes(), b"source")
        self.assertEqual(destination.read_bytes(), b"unrelated")

    def test_replacement_retains_original_inode_and_permissions(self):
        path, retained = self.root / "source", self.root / "retained"
        path.write_bytes(b"old")
        path.chmod(0o644)
        before = path.stat()
        activation.replace_preserving_inode(path, b"old", b"new", retained, uid=os.getuid(), gid=os.getgid(), mode=0o644)
        self.assertEqual(retained.stat().st_ino, before.st_ino)
        self.assertEqual(retained.read_bytes(), b"old")
        self.assertEqual(path.read_bytes(), b"new")
        self.assertEqual(path.stat().st_mode & 0o777, 0o644)

    def test_editor_fd_save_retains_bytes_and_refuses_activation(self):
        path, retained = self.root / "source", self.root / "retained"
        path.write_bytes(b"old")
        fd = os.open(path, os.O_WRONLY)
        self.addCleanup(os.close, fd)
        actual_rename = activation.rename_no_replace
        def race(source, destination):
            actual_rename(source, destination)
            if destination == path:
                os.lseek(fd, 0, os.SEEK_SET)
                os.write(fd, b"editor save")
                os.ftruncate(fd, len(b"editor save"))
        with patch.object(activation, "rename_no_replace", race), self.assertRaises(activation.ActivationError):
            activation.replace_preserving_inode(path, b"old", b"new", retained, uid=os.getuid(), gid=os.getgid(), mode=0o644)
        self.assertEqual(retained.read_bytes(), b"editor save")
        self.assertEqual(path.read_bytes(), b"new")

    def test_atomic_editor_replacement_is_not_clobbered(self):
        path, retained = self.root / "source", self.root / "retained"
        path.write_bytes(b"old")
        actual_rename = activation.rename_no_replace
        def race(source, destination):
            actual_rename(source, destination)
            if source == path:
                path.write_bytes(b"concurrent replacement")
        with patch.object(activation, "rename_no_replace", race), self.assertRaises(activation.ActivationError):
            activation.replace_preserving_inode(path, b"old", b"new", retained, uid=os.getuid(), gid=os.getgid(), mode=0o644)
        self.assertEqual(path.read_bytes(), b"concurrent replacement")
        self.assertEqual(retained.read_bytes(), b"old")
        self.assertEqual(retained.with_name(retained.name + ".candidate").read_bytes(), b"new")

    def test_source_manifest_detects_ignored_git_index_flags(self):
        runner, repo, request, _before, _index, _changes, _backup, _workspace = self.fixture()
        runner.git(repo, "update-index", "--assume-unchanged", "packages/t3code/source.json")
        (repo / "packages/t3code/source.json").write_bytes(b"hidden dirty content")
        with self.assertRaises(activation.ActivationError):
            activation.repository_manifest(runner, repo, request["baseline_commit"])

    def test_source_integration_keeps_clean_main_and_old_versions(self):
        runner, repo, request, before, index, changes, backup, workspace = self.fixture()
        phases = []
        after = activation.integrate_source(runner, repo, request, before, index, changes, backup, workspace,
                                            lambda phase, **fields: phases.append(phase))
        self.assertEqual(after["commit"], request["candidate_commit"])
        self.assertFalse(runner.git(repo, "status", "--porcelain"))
        self.assertEqual(phases, ["integrating", "source-integrated"])
        self.assertEqual((backup / "packages__t3code__source.json").read_bytes(), b"old source\n")
        self.assertEqual((backup / "index-original").read_bytes(), index)
        self.assertFalse((repo / ".git/index.lock").exists())

    def test_concurrent_main_commit_is_not_reset(self):
        runner, repo, request, before, index, changes, backup, workspace = self.fixture()
        runner.git(repo, "commit", "--allow-empty", "-m", "parallel work")
        concurrent_head = runner.git(repo, "rev-parse", "HEAD")
        with self.assertRaises(activation.ActivationError):
            activation.integrate_source(runner, repo, request, before, index, changes, backup, workspace, lambda *a, **k: None)
        self.assertEqual(runner.git(repo, "rev-parse", "HEAD"), concurrent_head)
        self.assertEqual((repo / "packages/t3code/source.json").read_bytes(), b"old source\n")

    def test_other_git_lock_is_preserved(self):
        runner, repo, request, before, index, changes, backup, workspace = self.fixture()
        lock = repo / ".git/index.lock"
        lock.write_bytes(b"parallel git transaction")
        with self.assertRaises(FileExistsError):
            activation.integrate_source(runner, repo, request, before, index, changes, backup, workspace, lambda *a, **k: None)
        self.assertEqual(lock.read_bytes(), b"parallel git transaction")

    def test_power_gate_uses_ac_and_battery(self):
        sysroot = self.root / "power"
        sysroot.mkdir()
        mains = sysroot / "AC"
        battery = sysroot / "BAT0"
        mains.mkdir()
        battery.mkdir()
        (mains / "type").write_text("Mains")
        (mains / "online").write_text("1")
        (battery / "type").write_text("Battery")
        (battery / "capacity").write_text("30")
        activation.check_power(sysroot)
        for online, capacity in [("0", "100"), ("1", "29")]:
            (mains / "online").write_text(online)
            (battery / "capacity").write_text(capacity)
            with self.assertRaises(activation.ActivationError):
                activation.check_power(sysroot)

    def test_exact_active_and_boot_profile_both_required(self):
        baseline = self.root / "system"
        (baseline / "bin").mkdir(parents=True)
        (baseline / "bin/switch-to-configuration").write_text("fixture")
        current, profile = self.root / "current", self.root / "profile"
        current.symlink_to(baseline)
        profile.symlink_to(baseline)
        with patch.object(activation, "CURRENT", current), patch.object(activation, "PROFILE", profile):
            activation.check_system(baseline)
            profile.unlink()
            profile.symlink_to(self.root / "different")
            with self.assertRaises(activation.ActivationError):
                activation.check_system(baseline)

    def test_snapshot_requires_own_btrfs_mount_and_read_only_checkpoint(self):
        class Snapshots:
            def __init__(self, filesystem="btrfs", ro="ro=true"):
                self.filesystem = filesystem
                self.ro = ro
            def root(self, name, arguments):
                if name == "findmnt":
                    return json.dumps({"filesystems": [{"target": "/home", "fstype": self.filesystem}]}).encode()
                if name == "snapper":
                    return b"7\n"
                if name == "btrfs" and arguments[0] == "property":
                    return self.ro.encode()
                return b""
        with patch.object(activation, "check_space"):
            self.assertEqual(activation.create_snapshot(Snapshots(), "fixture"), 7)
            for runner in [Snapshots(filesystem="ext4"), Snapshots(ro="ro=false")]:
                with self.assertRaises(activation.ActivationError):
                    activation.create_snapshot(runner, "fixture")


    def checked_fixture(self):
        runner, repo, request, before, index, changes, backup, workspace = self.fixture()
        runner.git(repo, "update-ref", request["candidate_ref"], request["candidate_commit"])
        original_git = runner.git
        def signed_git(repository, *args, **kwargs):
            if args == ("config", "--get", "user.signingkey"):
                return activation.EXPECTED_PRIMARY_FINGERPRINT.encode()
            if args[:1] == ("verify-commit",):
                return b""
            if args[:3] == ("show", "-s", "--format=%G? %GF %GP"):
                return ("G " + activation.EXPECTED_PRIMARY_FINGERPRINT + " " + activation.EXPECTED_PRIMARY_FINGERPRINT).encode()
            return original_git(repository, *args, **kwargs)
        runner.git = signed_git
        discovery = types.SimpleNamespace(
            validate_transition=lambda *a, **k: {"old": {"version": "0.1.0"}, "new": {"version": "0.2.0"}},
            learning_suffix=lambda *a, **k: b"new learning\n",
        )
        return runner, repo, request, discovery, original_git

    def test_check_mode_verifies_exact_scope_without_writing(self):
        runner, repo, request, discovery, original_git = self.checked_fixture()
        before = {str(p.relative_to(repo)): p.read_bytes() for p in repo.rglob("*") if p.is_file()}
        with patch.object(activation, "import_peer", return_value=discovery), patch.object(activation, "check_system"), patch.object(activation, "check_other_activation"), patch.object(activation, "check_remote"):
            result = activation.process_request(runner, request, self.root / "state", repo, check_only=True)
        after = {str(p.relative_to(repo)): p.read_bytes() for p in repo.rglob("*") if p.is_file()}
        self.assertTrue(result["ok"])
        self.assertFalse(result["activation_verified"])
        self.assertEqual(before, after)
        self.assertFalse((self.root / "state").exists())

    def test_check_rejects_forged_learning_suffix(self):
        runner, repo, request, discovery, original_git = self.checked_fixture()
        discovery.learning_suffix = lambda *a: b"unrelated expected suffix\n"
        with patch.object(activation, "import_peer", return_value=discovery), patch.object(activation, "check_system"), patch.object(activation, "check_other_activation"), patch.object(activation, "check_remote"):
            with self.assertRaisesRegex(activation.ActivationError, "Learning entry"):
                activation.process_request(runner, request, self.root / "state", repo, check_only=True)

    def test_check_rejects_other_valid_signer(self):
        runner, repo, request, discovery, original_git = self.checked_fixture()
        signed_git = runner.git
        def wrong_signer(repository, *args, **kwargs):
            if args[:3] == ("show", "-s", "--format=%G? %GF %GP"):
                return b"G 1111111111111111111111111111111111111111 1111111111111111111111111111111111111111"
            return signed_git(repository, *args, **kwargs)
        runner.git = wrong_signer
        with patch.object(activation, "import_peer", return_value=discovery), patch.object(activation, "check_system"), patch.object(activation, "check_other_activation"), patch.object(activation, "check_remote"):
            with self.assertRaisesRegex(activation.ActivationError, "signer"):
                activation.process_request(runner, request, self.root / "state", repo, check_only=True)

    def batch_fixture(self):
        runner, repo, request, discovery, original_git = self.checked_fixture()
        request.update(schema=2, mode="batch")
        policy_calls = []
        class BatchPolicy:
            sensitive_eval_args = staticmethod(activation.import_peer("weasel-update-batch.py").sensitive_eval_args)
            update_hosts = staticmethod(activation.import_peer("weasel-update-batch.py").update_hosts)
            @staticmethod
            def learning_suffix(identifier, mode, names):
                self.assertEqual(identifier, request["id"])
                self.assertEqual(mode, request["mode"])
                self.assertEqual(names, ["packages/t3code/source.json"])
                return b"new learning\n"
            @staticmethod
            def validate_changes(changes, mode, identifier):
                policy_calls.append((changes, mode, identifier))
                return {"mode": mode, "changed_sources": sorted(n for n in changes if n != "agent-learnings.md")}
        return runner, repo, request, BatchPolicy, policy_calls

    def test_batch_readonly_check_passes_exact_blobs_to_policy(self):
        runner, repo, request, policy, calls = self.batch_fixture()
        before = {str(p.relative_to(repo)): p.read_bytes() for p in repo.rglob("*") if p.is_file()}
        with patch.object(activation, "import_peer", return_value=policy) as imported, patch.object(activation, "check_system"), patch.object(activation, "check_other_activation"), patch.object(activation, "check_remote"):
            result = activation.process_request(runner, request, self.root / "state", repo, check_only=True)
        after = {str(p.relative_to(repo)): p.read_bytes() for p in repo.rglob("*") if p.is_file()}
        imported.assert_called_once_with("weasel-update-batch.py")
        self.assertEqual(result["schema"], 2)
        self.assertEqual(result["packages"]["batch"]["mode"], "batch")
        self.assertFalse(result["activation_verified"])
        self.assertEqual(before, after)
        self.assertFalse((self.root / "state").exists())
        self.assertEqual(calls, [({"packages/t3code/source.json": (b"old source\n", b"new source\n"),
                                  "agent-learnings.md": (b"previous learning\n", b"previous learning\nnew learning\n")},
                                 "batch", request["id"])])

    def test_batch_rejects_tree_addition_deletion_or_mode_change_before_policy(self):
        runner, repo, request, policy, calls = self.batch_fixture()
        old = activation.tracked_tree(runner, repo, request["baseline_commit"])
        new = activation.tracked_tree(runner, repo, request["candidate_commit"])
        scenarios = [dict(new, **{"packages/new.nix": next(iter(new.values()))}),
                     {name: value for name, value in new.items() if name != "packages/t3code/source.json"},
                     dict(new, **{"packages/t3code/source.json": ("100755", new["packages/t3code/source.json"][1])})]
        for modified in scenarios:
            with self.subTest(tree=modified), self.assertRaises(activation.ActivationError):
                activation.validate_candidate_changes(runner, repo, request, old, modified, batch=policy)
        self.assertEqual(calls, [])

    def test_batch_policy_refusal_and_wrong_learning_never_fall_back_to_pin_lane(self):
        runner, repo, request, policy, calls = self.batch_fixture()
        class Refused(RuntimeError):
            pass
        policy.validate_changes = lambda *args: (_ for _ in ()).throw(Refused("Protected source"))
        old = activation.tracked_tree(runner, repo, request["baseline_commit"])
        new = activation.tracked_tree(runner, repo, request["candidate_commit"])
        with self.assertRaisesRegex(Refused, "Protected"):
            activation.validate_candidate_changes(runner, repo, request, old, new, batch=policy)
        policy.learning_suffix = lambda *args: b"forged suffix\n"
        with self.assertRaisesRegex(activation.ActivationError, "Learning entry"):
            activation.validate_candidate_changes(runner, repo, request, old, new, batch=policy)

    def test_unchanged_batch_apps_and_acp_skip_probes_but_keep_closure_roots(self):
        old_source, new_source = self.root / "old", self.root / "new"
        workspace, run = self.root / "worker", self.root / "run"
        workspace.mkdir(); run.mkdir()
        runner = types.SimpleNamespace(account=types.SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid()),
                                       user=lambda *args: self.fail("Unchanged app executed a probe"))
        profile_verify = Mock(return_value={"ok": True, "coverage": "mocked orchestration only"})
        peer = types.SimpleNamespace(verify_codex_profile=profile_verify)
        with patch.object(activation, "build_application", side_effect=lambda runner, source, kind: Path("/nix/store/" + kind)) as builds, patch.object(activation, "nix_build", return_value=Path("/nix/store/acp")) as acp, patch.object(activation, "import_peer", return_value=peer):
            pairs, tests = activation.probe_applications(runner, old_source, new_source, ["t3", "codex", "chatgpt"], workspace, run, changed_outputs_only=True)
        self.assertEqual(builds.call_count, 6)
        self.assertEqual(acp.call_count, 3)
        profile_verify.assert_called_once_with(Path("/nix/store/acp"), Path("/nix/store/codex"), Path("/nix/store/acp"))
        self.assertEqual(set(pairs), {"t3", "codex", "chatgpt", "codex-acp"})
        self.assertTrue(all(not t["output_changed"] and not t["probed"] for t in tests.values()))
        self.assertEqual(list(workspace.iterdir()), [])
        self.assertEqual(list(run.iterdir()), [])

    def test_batch_acp_only_change_runs_native_codex_handshake(self):
        old_source, new_source = self.root / "old", self.root / "new"
        workspace, run = self.root / "worker", self.root / "run"
        workspace.mkdir(); run.mkdir()
        commands = []
        def user(name, args):
            commands.append((name, args))
            return json.dumps({"ok": True, "kind": "codex"}).encode()
        runner = types.SimpleNamespace(account=types.SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid()), user=user)
        profile_verify = Mock(return_value={"ok": True, "coverage": "mocked orchestration only"})
        peer = types.SimpleNamespace(verify_codex_profile=profile_verify)
        with patch.object(activation, "build_application", return_value=Path("/nix/store/codex")), patch.object(activation, "nix_build", side_effect=lambda runner, source, *args: Path("/nix/store/" + source.name + "-acp")), patch.object(activation, "import_peer", return_value=peer):
            pairs, tests = activation.probe_applications(runner, old_source, new_source, ["codex"], workspace, run, changed_outputs_only=True)
        self.assertEqual(pairs["codex"]["old"], pairs["codex"]["new"])
        self.assertNotEqual(pairs["codex-acp"]["old"], pairs["codex-acp"]["new"])
        self.assertTrue(tests["codex"]["output_changed"])
        self.assertTrue(tests["codex"]["probed"])
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0][0], "python3")
        args = commands[0][1]
        self.assertEqual(args[args.index("--acp-app") + 1], Path("/nix/store/new-acp"))
        self.assertTrue((run / "probe-codex.json").exists())

    def test_batch_t3_probe_receives_old_app_and_failure_refuses_candidate(self):
        workspace, run = self.root / "worker", self.root / "run"
        workspace.mkdir(); run.mkdir()
        commands = []
        def user(name, args):
            commands.append(args)
            return json.dumps({"ok": False}).encode()
        runner = types.SimpleNamespace(account=types.SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid()), user=user)
        with patch.object(activation, "build_application", side_effect=lambda runner, source, kind: source / kind):
            with self.assertRaisesRegex(activation.ActivationError, "probe did not pass"):
                activation.probe_applications(runner, self.root / "old", self.root / "new", ["t3"], workspace, run, changed_outputs_only=True)
        self.assertEqual(commands[0][commands[0].index("--old-app") + 1], self.root / "old/t3")
        self.assertEqual(list(run.iterdir()), [])

    def test_full_flake_check_preserves_lock_and_rejects_mutation(self):
        source = self.root / "source"
        source.mkdir(); (source / "flake.lock").write_bytes(b"original lock")
        commands = []
        def user(name, args):
            commands.append((name, args))
            return b""
        activation.nix_check(types.SimpleNamespace(user=user), source)
        self.assertEqual(commands[0][0], "nix")
        self.assertEqual(commands[0][1][:3], ["flake", "check", "--no-write-lock-file"])
        self.assertNotIn("--no-build", commands[0][1])
        def changed(name, args):
            (source / "flake.lock").write_bytes(b"changed lock")
            return b""
        with self.assertRaisesRegex(activation.ActivationError, "changed frozen flake.lock"):
            activation.nix_check(types.SimpleNamespace(user=changed), source)

    def test_batch_full_validation_and_unit_refusal_happen_before_checkpoint(self):
        runner, repo, request, policy, calls = self.batch_fixture()
        state = self.root / "state"
        state.mkdir()
        for name in ("runs", "sources", "workers"):
            (state / name).mkdir()
        root_calls, events = [], []
        runner.root = lambda *args, **kwargs: root_calls.append(args) or b""
        user_calls = []
        def user(name, args, **kwargs):
            user_calls.append((name, args))
            if name == "python3":
                events.append(("niri-config",) if Path(args[0]).name == "weasel-update-niri.py" else ("published-pins",))
                return json.dumps({"ok": True, "coverage": "fixture-parse"}).encode()
            return b"a" * 64
        runner.user = user
        baseline = Path(request["baseline_system"])
        candidate = Path("/nix/store/" + "b" * 32 + "-nixos-system-nixy-laptop-25.11.fixture")
        pairs = {kind: {"old": "old-" + kind, "new": "new-" + kind} for kind in ("t3", "codex", "chatgpt", "codex-acp")}
        def evaluate(runner, source, attribute, expression=None):
            events.append(("eval", source.name, attribute))
            return str(baseline) if source.name == "baseline" else "fixture-drv"
        def closure(old, new, applications, changes):
            events.append(("closure", old, new))
            self.assertEqual(applications, pairs)
            self.assertEqual(set(changes), {"packages/t3code/source.json", "agent-learnings.md"})
            return {"ok": True}
        policy.verify_closure = closure
        def sensitive(old, new, evaluator, *, mode, migration_executor):
            self.assertEqual((old.name, new.name), ("baseline", "candidate"))
            self.assertEqual(mode, request["mode"])
            self.assertEqual(migration_executor("nix", ["fixture-fresh-ssh-build"]), b"a" * 64)
            self.assertEqual(evaluator("fixture-sensitive-expression"), b"a" * 64)
            events.append(("sensitive-state",))
            return {"ok": True, "auth_and_state_version_sha256": {"fixture": "a" * 64}}
        policy.verify_sensitive_state = sensitive
        def units(old, new):
            events.append(("units", old, new))
            raise RuntimeError("Graphical session unit would restart")
        policy.verify_runtime_units = units
        with patch.object(activation, "import_peer", return_value=policy), patch.object(activation, "check_system"), patch.object(activation, "check_other_activation"), patch.object(activation, "check_remote"), patch.object(activation, "check_power"), patch.object(activation, "check_space"), patch.object(activation, "prune_owned"), patch.object(activation, "write_status"), patch.object(activation, "notify_outcome"), patch.object(activation, "nix_eval", side_effect=evaluate), patch.object(activation, "nix_check", side_effect=lambda *args: events.append(("flake-check",))), patch.object(activation, "nix_build", side_effect=lambda *args: events.append(("system-build",)) or candidate), patch.object(activation, "probe_applications", return_value=(pairs, {"t3": {"probed": True}})) as probes, patch.object(activation, "create_snapshot") as snapshot, patch.object(activation, "integrate_source") as integration, patch.object(activation, "activate_exact") as activate:
            previous_mask = os.umask(0o077)
            try:
                with self.assertRaisesRegex(RuntimeError, "Graphical session"):
                    activation.process_request(runner, request, state, repo)
                self.assertEqual(os.umask(0o077), 0o077)
            finally:
                os.umask(previous_mask)
        hosts = [event[2] for event in events if event[0] == "eval" and event[1] == "candidate"]
        self.assertEqual(hosts, ["nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath"])
        self.assertLess(events.index(("flake-check",)), next(i for i, e in enumerate(events) if e[0] == "closure"))
        self.assertLess(events.index(("sensitive-state",)), events.index(("flake-check",)))
        self.assertLess(events.index(("published-pins",)), events.index(("flake-check",)))
        self.assertLess(events.index(("system-build",)), events.index(("niri-config",)))
        self.assertLess(events.index(("niri-config",)), next(i for i, e in enumerate(events) if e[0] == "closure"))
        self.assertIn(("nix", ["eval", "--raw", "--impure", "--no-write-lock-file", "--offline", "--max-jobs", "0", "--builders", "", "--option", "substitute", "false", "--option", "allow-import-from-derivation", "true", "--expr", "fixture-sensitive-expression"]), user_calls)
        self.assertEqual(probes.call_args.args[3], ["t3", "codex", "chatgpt"])
        self.assertTrue(probes.call_args.kwargs["changed_outputs_only"])
        snapshot.assert_not_called(); integration.assert_not_called(); activate.assert_not_called()
        self.assertEqual(root_calls, [])
        journal = json.loads((state / "runs" / request["id"] / "journal.json").read_text())
        self.assertEqual(journal["phase"], "failed-before-integration")
        self.assertEqual(journal["schema"], 2)
        self.assertTrue(journal["sensitive_state"]["ok"])
        self.assertTrue(journal["published_pins"]["ok"])
        self.assertTrue((state / "runs" / request["id"] / "probe-niri.json").exists())
        namespace = state / "sources" / request["id"]
        self.assertEqual(stat.S_IMODE(namespace.stat().st_mode), 0o711)
        for name in ("baseline", "candidate"):
            source = namespace / name
            self.assertEqual((source / "packages/t3code/source.json").read_bytes(),
                             b"old source\n" if name == "baseline" else b"new source\n")
            for path in [source, *source.rglob("*")]:
                self.assertEqual(stat.S_IMODE(path.stat().st_mode),
                                 0o555 if path.is_dir() else 0o444)
        self.assertEqual(stat.S_IMODE((state / "runs" / request["id"]).stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((state / "runs" / request["id"] / "journal.json").stat().st_mode), 0o600)

    @unittest.skipUnless(os.geteuid() == 0, "Cross-UID root archive fixture requires root")
    def test_root_archive_under_private_umask_is_readable_but_not_writable_by_other_uid(self):
        runner, repo, request, policy, _ = self.batch_fixture()
        self.root.chmod(0o755)
        state = self.root / "state"
        state.mkdir(mode=0o711)
        for name, mode in (("runs", 0o700), ("sources", 0o711), ("workers", 0o711)):
            (state / name).mkdir(mode=mode)
        namespace = state / "sources" / request["id"]
        run = state / "runs" / request["id"]
        workspace = state / "workers" / request["id"]
        def user(name, args, **kwargs):
            self.assertEqual(name, "python3")  # First user action is published-source validation.
            self.assertEqual(namespace.stat().st_uid, 0)
            probe = '''
import os, sys
from pathlib import Path
namespace, run, workspace = map(Path, sys.argv[1:])
assert os.geteuid() == 65534
for label, expected in [('baseline', b'old source\\n'), ('candidate', b'new source\\n')]:
    source = namespace / label
    assert (source / 'packages/t3code/source.json').read_bytes() == expected
    assert list(source.iterdir())
    assert not os.access(source, os.W_OK)
    assert not os.access(source / 'packages', os.W_OK)
    assert not os.access(source / 'packages/t3code/source.json', os.W_OK)
assert not os.access(namespace, os.R_OK | os.W_OK)
assert os.access(namespace, os.X_OK)
for private in [run, run / 'journal.json', workspace]:
    assert not os.access(private, os.R_OK)
'''
            result = subprocess.run([sys.executable, "-I", "-B", "-c", probe,
                                     str(namespace), str(run), str(workspace)],
                                    user=65534, group=65534, extra_groups=[], env={},
                                    capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            raise RuntimeError("Cross-UID archive fixture complete")
        runner.user = user
        with patch.object(activation, "import_peer", return_value=policy), patch.object(activation, "check_system"), patch.object(activation, "check_other_activation"), patch.object(activation, "check_remote"), patch.object(activation, "check_power"), patch.object(activation, "check_space"), patch.object(activation, "prune_owned"), patch.object(activation, "write_status"), patch.object(activation, "notify_outcome"), patch.object(activation, "nix_eval") as evaluate, patch.object(activation, "create_snapshot") as snapshot, patch.object(activation, "integrate_source") as integrate, patch.object(activation, "activate_exact") as activate:
            previous_mask = os.umask(0o077)
            try:
                with self.assertRaisesRegex(RuntimeError, "Cross-UID archive fixture complete"):
                    activation.process_request(runner, request, state, repo)
                self.assertEqual(os.umask(0o077), 0o077)
            finally:
                os.umask(previous_mask)
        evaluate.assert_not_called(); snapshot.assert_not_called()
        integrate.assert_not_called(); activate.assert_not_called()
        self.assertEqual(json.loads((run / "journal.json").read_text())["phase"],
                         "failed-before-integration")

    def test_missing_cached_sensitive_artifact_blocks_root_transaction_without_retry(self):
        runner, repo, request, policy, _ = self.batch_fixture()
        state = self.root / 'state'
        state.mkdir()
        for name in ('runs', 'sources', 'workers'):
            (state / name).mkdir()
        root_calls, eval_calls = [], []
        runner.root = lambda *args, **kwargs: root_calls.append(args) or b''
        def user(name, args, **kwargs):
            if name == 'python3':
                return json.dumps({'ok': True, 'coverage': 'fixture-parse'}).encode()
            self.assertEqual(name, 'nix')
            self.assertEqual(args, policy.sensitive_eval_args('fixture-sensitive-expression'))
            eval_calls.append(args)
            raise activation.ActivationError('Missing cached IFD artifact')
        runner.user = user
        policy.verify_sensitive_state = lambda old, new, evaluator, **kwargs: evaluator('fixture-sensitive-expression')
        def evaluate(_runner, source, attribute, expression=None):
            return request['baseline_system'] if source.name == 'baseline' else 'fixture-drv'
        with patch.object(activation, 'import_peer', return_value=policy), patch.object(activation, 'check_system'), patch.object(activation, 'check_other_activation'), patch.object(activation, 'check_remote'), patch.object(activation, 'check_power'), patch.object(activation, 'check_space'), patch.object(activation, 'prune_owned'), patch.object(activation, 'write_status'), patch.object(activation, 'notify_outcome'), patch.object(activation, 'nix_eval', side_effect=evaluate), patch.object(activation, 'nix_check') as check, patch.object(activation, 'nix_build') as build, patch.object(activation, 'create_snapshot') as snapshot, patch.object(activation, 'integrate_source') as integrate, patch.object(activation, 'activate_exact') as activate:
            with self.assertRaisesRegex(activation.ActivationError, 'Missing cached IFD artifact'):
                activation.process_request(runner, request, state, repo)
        self.assertEqual(len(eval_calls), 1)
        self.assertEqual(root_calls, [])
        check.assert_not_called(); build.assert_not_called(); snapshot.assert_not_called()
        integrate.assert_not_called(); activate.assert_not_called()
        journal = json.loads((state / 'runs' / request['id'] / 'journal.json').read_text())
        self.assertEqual(journal['phase'], 'failed-before-integration')

    def test_batch_rechecks_session_after_real_cas_and_keeps_source_for_recovery(self):
        runner, repo, request, policy, calls = self.batch_fixture()
        state = self.root / "state"
        state.mkdir()
        for name in ("runs", "sources", "workers"):
            (state / name).mkdir()
        runner.root = lambda *args, **kwargs: b""
        runner.user = lambda *args, **kwargs: json.dumps({"ok": True, "coverage": "fixture-parse"}).encode()
        baseline = Path(request["baseline_system"])
        candidate = Path("/nix/store/" + "b" * 32 + "-nixos-system-nixy-laptop-25.11.fixture")
        policy.verify_sensitive_state = lambda *args, **kwargs: {"ok": True}
        policy.verify_closure = lambda *args: {"ok": True}
        unit_checks = []
        def units(old, new):
            unit_checks.append((old, new))
            if len(unit_checks) == 2:
                raise RuntimeError("Graphical session changed before activation")
            return {"ok": True}
        policy.verify_runtime_units = units
        retained = self.root / "retained"
        def directory(path, mode=0o700, owner=0):
            path.mkdir(mode=mode)
            return path
        def evaluate(runner, source, attribute, expression=None):
            return str(baseline) if source.name == "baseline" else "fixture-drv"
        original_inode = (repo / "packages/t3code/source.json").stat().st_ino
        with patch.object(activation, "import_peer", return_value=policy), patch.object(activation, "check_system"), patch.object(activation, "check_other_activation"), patch.object(activation, "check_remote"), patch.object(activation, "check_power"), patch.object(activation, "check_space"), patch.object(activation, "prune_owned"), patch.object(activation, "write_status"), patch.object(activation, "notify_outcome"), patch.object(activation, "nix_eval", side_effect=evaluate), patch.object(activation, "nix_check"), patch.object(activation, "nix_build", return_value=candidate), patch.object(activation, "probe_applications", return_value=({}, {})), patch.object(activation, "BACKUPS", retained), patch.object(activation, "ensure_directory", side_effect=directory), patch.object(activation, "create_snapshot", return_value=7), patch.object(activation, "activate_exact") as activate:
            with self.assertRaisesRegex(RuntimeError, "Graphical session changed"):
                activation.process_request(runner, request, state, repo)
        activate.assert_not_called()
        self.assertEqual(len(unit_checks), 2)
        self.assertEqual(runner.git(repo, "rev-parse", "HEAD").decode().strip(), request["candidate_commit"])
        self.assertEqual((repo / "packages/t3code/source.json").read_bytes(), b"new source\n")
        original = retained / request["id"] / "packages__t3code__source.json"
        self.assertEqual(original.read_bytes(), b"old source\n")
        self.assertEqual(original.stat().st_ino, original_inode)
        journal = json.loads((state / "runs" / request["id"] / "journal.json").read_text())
        self.assertEqual(journal["phase"], "recovery-required")
        self.assertEqual(journal["snapshot"], 7)

    def test_original_open_fd_prevents_cleanup(self):
        backup = self.root / "backup"
        backup.mkdir()
        retained = backup / "original"
        retained.write_bytes(b"editor")
        with open(retained, "rb"):
            self.assertTrue(activation.retained_has_open_fds(backup))

    def test_other_activation_blocks_switch_but_parallel_build_is_allowed(self):
        proc = self.root / "processes"
        process = proc / "123456"
        process.mkdir(parents=True)
        (process / "cmdline").write_bytes(b"/nix/store/test/bin/nixos-rebuild\0build\0")
        activation.check_other_activation(proc)
        (process / "cmdline").write_bytes(b"/nix/store/test/bin/nixos-rebuild\0switch\0")
        with self.assertRaises(activation.ActivationError):
            activation.check_other_activation(proc)


    def test_root_reads_reject_symlinked_parent_directories(self):
        real = self.root / "real"
        real.mkdir()
        (real / "source").write_bytes(b"private")
        linked = self.root / "linked"
        linked.symlink_to(real, target_is_directory=True)
        with self.assertRaises(OSError):
            activation.read_regular(linked / "source")
        destination = self.root / "retained"
        with self.assertRaises(OSError):
            activation.rename_no_replace(linked / "source", destination)
        self.assertEqual((real / "source").read_bytes(), b"private")
        self.assertFalse(destination.exists())


    def test_remote_provenance_and_current_head_are_required(self):
        expected = "a" * 40
        class Remote:
            def __init__(self, url=activation.ORIGIN_URL, head=expected, push=None):
                self.url, self.head, self.push = url, head, push
            def git(self, repository, *args):
                if args[:2] == ("remote", "get-url"):
                    return ((self.push if "--push" in args and self.push is not None else self.url) + "\n").encode()
                return (self.head + "\trefs/heads/main\n").encode()
        activation.check_remote(Remote(), self.root, expected)
        for remote in [Remote(url="https://example.invalid/repo"), Remote(head="b" * 40), Remote(push="https://example.invalid/push")]:
            with self.assertRaises(activation.ActivationError):
                activation.check_remote(remote, self.root, expected)


    def test_late_editor_save_blocks_and_readonly_check_preserves_journal(self):
        state = self.root / "state"
        run = state / "runs/20261008T140000Z-1234abcd"
        run.mkdir(parents=True)
        backups = self.root / "backups"
        backup = backups / run.name
        backup.mkdir(parents=True)
        (backup / "original").write_bytes(b"late editor save")
        journal = run / "journal.json"
        journal.write_bytes(activation.encoded({"marker": activation.OWNER_TAG, "phase": "complete",
                                               "retained_originals": {"original": activation.digest(b"old")}}))
        original = journal.read_bytes()
        with patch.object(activation, "BACKUPS", backups):
            with self.assertRaises(activation.RecoveryRequired):
                activation.check_recovery(state, read_only=True)
            self.assertEqual(journal.read_bytes(), original)
            with self.assertRaises(activation.RecoveryRequired):
                activation.check_recovery(state)
        self.assertEqual(json.loads(journal.read_bytes())["phase"], "recovery-required")
        self.assertEqual((backup / "original").read_bytes(), b"late editor save")

    def test_retention_defers_untagged_or_user_deleted_snapshot_without_blocking(self):
        state, backups = self.root / "state", self.root / "backups"
        (state / "runs").mkdir(parents=True)
        (state / "requests").mkdir()
        for serial in range(4):
            identifier = "20261008T140000Z-" + str(serial).zfill(8)
            run = state / "runs" / identifier
            run.mkdir()
            backup = backups / identifier
            backup.mkdir(parents=True)
            for name, contents in {"original": b"old", "index-original": b"index", "original-index-copy": b"index"}.items():
                (backup / name).write_bytes(contents)
            (run / "journal.json").write_bytes(activation.encoded({
                "marker": activation.OWNER_TAG, "id": identifier, "phase": "complete", "snapshot": serial + 1,
                "retained_originals": {"original": activation.digest(b"old")},
                "original_index_sha256": activation.digest(b"index")}))
        originals = {path: path.read_bytes() for parent in (state, backups)
                     for path in parent.rglob("*") if path.is_file()}
        class SnapshotReader:
            account = types.SimpleNamespace(pw_uid=os.getuid())
            def __init__(self, rows):
                self.rows = rows
            def root(self, name, args):
                if name == "snapper" and "list" in args:
                    return activation.encoded({"home": self.rows})
                raise AssertionError("Deletion must not execute")
        for rows in ([], [{"number": 1, "userdata": {"other-owner": "yes"}}],
                     [{"number": 7, "userdata": {"weasel-daily-update": "yes",
                       "weasel-update-id": "20261008T140000Z-00000000"}}]):
            with self.subTest(rows=rows), patch.object(activation, "BACKUPS", backups), patch.object(activation, "retained_has_open_fds", return_value=False):
                self.assertEqual(activation.prune_owned(SnapshotReader(rows), state, keep=3),
                                 ["20261008T140000Z-00000000"])
            self.assertEqual({path: path.read_bytes() for parent in (state, backups)
                              for path in parent.rglob("*") if path.is_file()}, originals)
            self.assertEqual(len(list((state / "runs").iterdir())), 4)
            self.assertEqual(len(list(backups.iterdir())), 4)


    def test_corrupted_git_blob_cannot_replace_signed_content(self):
        runner, repo, request, _before, _index, _changes, _backup, _workspace = self.fixture()
        original = runner.git
        blob = activation.tracked_tree(runner, repo, request["baseline_commit"])["packages/t3code/source.json"][1]
        def substituted(repository, *args, **kwargs):
            if args == ("cat-file", "blob", blob):
                return b"corrupted loose object"
            return original(repository, *args, **kwargs)
        runner.git = substituted
        with self.assertRaisesRegex(activation.ActivationError, "signed content address"):
            activation.read_blob(runner, repo, blob)

    def test_corrupted_git_tree_cannot_redirect_source_paths(self):
        runner, repo, request, _before, _index, _changes, _backup, _workspace = self.fixture()
        original = runner.git
        def substituted(repository, *args, **kwargs):
            result = original(repository, *args, **kwargs)
            if args[:2] == ("cat-file", "tree"):
                return result.replace(b"packages", b"dangerous")
            return result
        runner.git = substituted
        with self.assertRaisesRegex(activation.ActivationError, "signed content address"):
            activation.tracked_tree(runner, repo, request["baseline_commit"])


class BootstrapResumeRecoveryFenceTests(unittest.TestCase):
    """Private journal fixtures prove that only one reviewed root exception is admitted."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="weasel-resume-recovery-fence-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.commit = "a" * 40
        self.run = self.root / "bootstrap" / self.commit
        self.run.mkdir(parents=True)
        self.record = {"marker": "weasel-daily-bootstrap-v1", "commit": self.commit,
                       "phase": "recovery-required", "system_rollback": "complete",
                       "home_rollback": False}
        self.data = activation.encoded(self.record)
        (self.run / "journal.json").write_bytes(self.data)

    def root_fixture(self, stack):
        real = activation.read_regular
        def fixture_read(path, *args, **kwargs):
            if kwargs.get("expected_uid") == 0:
                self.assertTrue(Path(path).is_relative_to(self.root))
                kwargs["expected_uid"] = os.getuid()
            return real(path, *args, **kwargs)
        stack.enter_context(patch.object(activation.os, "geteuid", return_value=0))
        return stack.enter_context(patch.object(activation, "read_regular", side_effect=fixture_read))

    def resume(self, **kwargs):
        return activation.check_recovery(self.root, read_only=True,
                         bootstrap_resume=(self.commit, activation.digest(self.data)), **kwargs)

    def test_default_fence_remains_unconditional_and_read_only(self):
        with self.assertRaises(activation.RecoveryRequired):
            activation.check_recovery(self.root, read_only=True)
        self.assertEqual((self.run / "journal.json").read_bytes(), self.data)
        self.assertEqual(sorted(p.name for p in self.run.iterdir()), ["journal.json"])

    def test_explicit_root_exception_admits_exact_one_and_does_not_relabel_it(self):
        with ExitStack() as stack:
            reads = self.root_fixture(stack)
            self.resume()
        self.assertEqual(reads.call_args.kwargs["expected_uid"], 0)
        self.assertEqual((self.run / "journal.json").read_bytes(), self.data)
        self.assertEqual(json.loads(self.data)["phase"], "recovery-required")

    def test_unprivileged_or_malformed_resume_never_broadens_recovery(self):
        digest = activation.digest(self.data)
        with patch.object(activation.os, "geteuid", return_value=1000), self.assertRaises(activation.ActivationError):
            self.resume()
        variants = [[self.commit, digest], (self.commit,), (self.commit, digest, "extra"),
                    ("main", digest), (self.commit, "bad"), (self.commit, "F" * 64),
                    (self.commit, None), (None, digest)]
        with ExitStack() as stack:
            self.root_fixture(stack)
            for value in variants:
                with self.subTest(value=value), self.assertRaises(activation.ActivationError):
                    activation.check_recovery(self.root, read_only=True, bootstrap_resume=value)
        self.assertEqual((self.run / "journal.json").read_bytes(), self.data)

    def test_wrong_digest_commit_phase_or_rollback_keeps_fence(self):
        with ExitStack() as stack:
            self.root_fixture(stack)
            for value in [(self.commit, "0" * 64), ("b" * 40, activation.digest(self.data))]:
                with self.subTest(binding=value), self.assertRaises(activation.RecoveryRequired):
                    activation.check_recovery(self.root, read_only=True, bootstrap_resume=value)
            for key, value in [("phase", "activating"), ("phase", "complete"),
                               ("phase", "failed-before-integration"), ("system_rollback", "failed"),
                               ("system_rollback", None), ("home_rollback", True),
                               ("home_rollback", 0), ("home_rollback", None)]:
                raw = activation.encoded(dict(self.record, **{key: value}))
                (self.run / "journal.json").write_bytes(raw)
                with self.subTest(key=key, value=value), self.assertRaises(activation.RecoveryRequired):
                    activation.check_recovery(self.root, read_only=True,
                                             bootstrap_resume=(self.commit, activation.digest(raw)))
                self.assertEqual((self.run / "journal.json").read_bytes(), raw)

    def test_other_bootstrap_and_daily_transactions_remain_blocked(self):
        with ExitStack() as stack:
            self.root_fixture(stack)
            for kind in ["bootstrap", "runs"]:
                parent = self.root / kind
                parent.mkdir(exist_ok=True)
                other = parent / ("b" * 40 if kind == "bootstrap" else "20261012T120000Z-1234abcd")
                other.mkdir()
                record = {"phase": "recovery-required"}
                if kind == "bootstrap":
                    record.update(marker="weasel-daily-bootstrap-v1", commit=other.name,
                                  system_rollback="complete", home_rollback=False)
                raw = activation.encoded(record)
                (other / "journal.json").write_bytes(raw)
                with self.subTest(kind=kind), self.assertRaises(activation.RecoveryRequired):
                    self.resume()
                self.assertEqual((other / "journal.json").read_bytes(), raw)
                (other / "journal.json").unlink()
                with self.subTest(kind=kind, missing_journal=True), self.assertRaises(activation.RecoveryRequired):
                    self.resume()
                other.rmdir()
        self.assertEqual((self.run / "journal.json").read_bytes(), self.data)

    def test_resume_journal_uid_is_enforced_without_fixture_owner_substitution(self):
        if os.getuid() == 0:
            self.skipTest("Non-root ownership rejection requires normal-user fixtures")
        with patch.object(activation.os, "geteuid", return_value=0), self.assertRaises(activation.ActivationError):
            self.resume()


if __name__ == "__main__":
    unittest.main()
