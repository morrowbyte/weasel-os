#!/usr/bin/env python3
"""Proposal-only negative fixtures; private synthetic Nix, no host activation.

Daemon execution is mocked here. A real fresh-built daemon proof and full
bootstrap closure remain mandatory and are explicitly not asserted by this suite.
"""
import ast
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

batch = load("proposal_batch", ROOT / "scripts/weasel-update-batch.py")
migration = load("proposal_migration", ROOT / "scripts/weasel-update-migration.py")
activation = load("proposal_activation", ROOT / "scripts/weasel-update-activate.py")
old_tests = load("proposal_existing_batch_tests", ROOT / "tests/test-update-batch.py")


def envelope(side, **changed):
    expected = migration.TRANSITION[side]
    value = {"schema": 1, "raw_sha256": ("a" if side == "old" else "b") * 64,
             "normalized_sha256": "c" * 64, "release": expected["release"],
             "ssh_keys": list(migration.SSH_OLD_KEYS) if side == "old" else sorted(migration.SSH_OLD_KEYS + migration.SSH_ADDED_NULL_KEYS),
             "ssh_added_fields_are_null": side == "new", "sudo_prefix_accepted": side == "new",
             "module_sha256": copy.deepcopy(expected["modules"] | migration.RELEASE_DEFAULT_MODULE_HASHES[side])}
    value.update(changed)
    return json.dumps(value)


class TypedEvidenceTests(unittest.TestCase):
    def test_exact_old_and_new_envelopes_are_accepted(self):
        for side in ["old", "new"]:
            self.assertEqual(migration.validate_envelope(envelope(side), side)["schema"], 1)

    def test_unknown_fields_are_not_ignored(self):
        for side in ["old", "new"]:
            with self.assertRaises(migration.MigrationError):
                migration.validate_envelope(envelope(side, allow_auth_change=True), side)

    def test_duplicate_fields_are_rejected(self):
        with self.assertRaises(migration.MigrationError):
            migration.object_json('{"schema":1,"schema":1}')

    def test_boolean_schema_is_rejected(self):
        with self.assertRaises(migration.MigrationError):
            migration.validate_envelope(envelope("new", schema=True), "new")

    def test_wrong_release_or_downgrade_is_rejected(self):
        for value in ["25.11", "26.11", "unstable"]:
            with self.subTest(value=value), self.assertRaises(migration.MigrationError):
                migration.validate_envelope(envelope("new", release=value), "new")

    def test_invalid_fingerprints_are_rejected(self):
        for field in ["raw_sha256", "normalized_sha256"]:
            with self.subTest(field=field), self.assertRaises(migration.MigrationError):
                migration.validate_envelope(envelope("new", **{field: "malformed"}), "new")

    def test_unknown_ssh_fields_are_rejected_even_when_null(self):
        keys = sorted(migration.SSH_OLD_KEYS + migration.SSH_ADDED_NULL_KEYS + ("PermitUserEnvironment",))
        with self.assertRaises(migration.MigrationError):
            migration.validate_envelope(envelope("new", ssh_keys=keys), "new")

    def test_missing_shared_ssh_field_is_rejected(self):
        keys = sorted(migration.SSH_OLD_KEYS[1:] + migration.SSH_ADDED_NULL_KEYS)
        with self.assertRaises(migration.MigrationError):
            migration.validate_envelope(envelope("new", ssh_keys=keys), "new")

    def test_added_fields_must_be_null_and_sudo_prefix_explicit(self):
        for field in ["ssh_added_fields_are_null", "sudo_prefix_accepted"]:
            for value in [False, 1, "true", None]:
                with self.subTest(field=field, value=value), self.assertRaises(migration.MigrationError):
                    migration.validate_envelope(envelope("new", **{field: value}), "new")

    def test_unreviewed_module_source_hash_is_rejected(self):
        modules = copy.deepcopy(migration.TRANSITION["new"]["modules"])
        modules[next(iter(modules))] = "d" * 64
        with self.assertRaises(migration.MigrationError):
            migration.validate_envelope(envelope("new", module_sha256=modules), "new")

    def test_no_probe_callback_is_a_hard_failure(self):
        with self.assertRaisesRegex(migration.MigrationError, "Fresh built"):
            migration.verify_built_ssh("old", "new", "nixy-laptop", None, None)


class FullVerifierControlFlowTests(unittest.TestCase):
    """Real verifier orchestration with synthetic evaluator/probe results."""
    def run_case(self, *, changed=False, race=None, wrong_release=False, probe_failure=False):
        old, new = Path("/tmp/old-auth-fixture"), Path("/tmp/new-auth-fixture")
        raw_counts = {"old": 0, "new": 0}
        def evaluate(expression):
            side = "old" if str(old) in expression else "new"
            if "raw_sha256 =" in expression:
                return envelope(side, normalized_sha256=("d" * 64 if changed and side == "new" else "c" * 64)).encode()
            if ".config.system.nixos.release" in expression:
                return ("26.11" if wrong_release and side == "new" else migration.TRANSITION[side]["release"]).encode()
            raw_counts[side] += 1
            if race == side and raw_counts[side] > 1:
                return b"d" * 64
            return ("a" * 64 if side == "old" else "b" * 64).encode()
        executor = lambda *args: b"synthetic-executor"
        events = []
        def effective(before, after, host, evaluator, callback, *, allow_default_transition):
            self.assertIs(allow_default_transition,True)
            self.assertEqual((before, after), (old, new))
            self.assertIs(callback, executor)
            events.append(host)
            if probe_failure:
                raise migration.MigrationError("Fresh SSH proof failed")
            return {"ok": True, "coverage": "Synthetic orchestration only; no daemon executed"}
        # Keep the loaded immutable module injectable only in this unit fixture.
        # No caller in production can supply or override the module or proof.
        from types import SimpleNamespace
        spec = SimpleNamespace(loader=SimpleNamespace(exec_module=lambda module: None))
        with patch.object(batch.importlib.util, "spec_from_file_location", return_value=spec), \
             patch.object(batch.importlib.util, "module_from_spec", return_value=migration), \
             patch.object(migration, "check_lock"), patch.object(migration, "verify_built_ssh", side_effect=effective), \
             patch.dict(os.environ, {}, clear=True):
            result = batch.verify_sensitive_state(old, new, evaluate, mode="release-migration", migration_executor=executor)
        return result, events

    def test_all_four_hosts_require_fresh_proof_through_the_same_verifier(self):
        result, events = self.run_case()
        self.assertTrue(result["ok"])
        self.assertEqual(events, batch.HOSTS)
        self.assertEqual(set(result["reviewed_migration_transitions"]), set(batch.HOSTS))

    def test_another_authentication_or_state_change_cannot_pass_normalized_gate(self):
        with self.assertRaisesRegex(batch.BatchError, "another authentication"):
            self.run_case(changed=True)

    def test_both_baseline_and_candidate_are_rechecked_after_proof(self):
        for side in ["old", "new"]:
            with self.subTest(side=side), self.assertRaisesRegex(batch.BatchError, "changed during SSH proof"):
                self.run_case(race=side)

    def test_actual_release_query_must_still_match_the_lane(self):
        with self.assertRaisesRegex(batch.BatchError, "typed candidate lane"):
            self.run_case(wrong_release=True)

    def test_fresh_probe_failure_cannot_return_a_passed_gate(self):
        with self.assertRaisesRegex(batch.BatchError, "Fresh SSH proof failed"):
            self.run_case(probe_failure=True)


class GeneralSSHControlFlowTests(unittest.TestCase):
    """Real ordinary verifier control flow; no migration normalization."""
    def run_case(self, *, raw_change=False, policy_change=False, race=False, proof_failure=False):
        calls={"old":0,"new":0};events=[]
        locked=copy.deepcopy(migration.TRANSITION["old"]["locked"])
        def evaluate(expression):
            side="old" if "/tmp/general-old" in expression else "new"
            calls[side]+=1
            self.assertIn("fresh",events,"IFD-disabled policy evaluation must follow actual realization")
            return json.dumps({"schema":1,"raw_sha256":("b" if raw_change and side=="new" else "a")*64,
                               "ssh_policy_sha256":("d" if side=="new" and (policy_change or race and calls[side]>1) else "c")*64,
                               "release":"25.11","ssh_module_sha256":"e"*64})
        executor=lambda *args:b"synthetic-proof"
        def probe(before,after,host,evaluator,callback,**metadata):
            self.assertIs(callback,executor)
            self.assertEqual(metadata,{"locked_pair":[locked,locked],"releases":["25.11","25.11"]})
            events.append("fresh")
            if proof_failure:raise migration.MigrationError("Fresh proof failed")
            return {"ok":True,"coverage":"Mocked execution only"}
        with patch.object(migration,"general_lock",return_value=(locked,"25.11")), \
             patch.object(migration,"verify_built_ssh",side_effect=probe), \
             patch.object(batch,"codex_ownership_predicate",return_value="true"), \
             patch.dict(os.environ,{},clear=True):
            value=migration.verify_general(batch.sensitive_expression,"/tmp/general-old","/tmp/general-new","nixy-laptop",evaluate,executor)
        return value,events

    def test_ordinary_equal_auth_and_authored_ssh_requires_fresh_proof(self):
        value,events=self.run_case()
        self.assertEqual(events,["fresh"]);self.assertEqual(value["old"],value["new"])

    def test_no_migration_optin_is_used_for_ordinary_batch(self):
        with patch.object(migration,"APPROVED_2511_TO_2605_ENV_KEEP",False):
            self.assertTrue(self.run_case()[0]["ssh_effective"]["ok"])

    def test_raw_auth_change_never_receives_normalization(self):
        with self.assertRaisesRegex(migration.MigrationError,"raw authentication"):
            self.run_case(raw_change=True)

    def test_indirect_authored_ssh_change_cannot_hide_behind_equal_raw_hash(self):
        with self.assertRaisesRegex(migration.MigrationError,"authored SSH"):
            self.run_case(policy_change=True)

    def test_general_ssh_source_race_after_probe_is_rejected(self):
        with self.assertRaisesRegex(migration.MigrationError,"changed during"):
            self.run_case(race=True)

    def test_missing_fresh_proof_never_returns_passed_ordinary_auth(self):
        with self.assertRaisesRegex(migration.MigrationError,"Fresh proof"):
            self.run_case(proof_failure=True)

    def test_general_envelope_rejects_unknowns_and_boolean_schema(self):
        value={"schema":1,"raw_sha256":"a"*64,"ssh_policy_sha256":"b"*64,"ssh_module_sha256":"c"*64,"release":"25.11"}
        migration.validate_general_envelope(json.dumps(value),"25.11")
        for change in [{"schema":True},{"unknown":None},{"release":"26.05"},{"ssh_policy_sha256":"bad"}]:
            with self.subTest(change=change),self.assertRaises(migration.MigrationError):
                migration.validate_general_envelope(json.dumps(value|change),"25.11")

    def test_general_binding_does_not_accept_caller_receipt_or_bypass_flag(self):
        import inspect
        self.assertEqual(list(inspect.signature(migration.verify_general).parameters),
                         ["sensitive_expression","old_source","new_source","host","evaluator","executor"])


class OrdinaryBatchFreshProofTests(unittest.TestCase):
    def test_ordinary_update_checks_only_laptop_and_reuses_fresh_raw_proof(self):
        from types import SimpleNamespace
        executor=lambda *args:b"owned-fixed-executor";events=[]
        def evaluate(expression):
            self.assertIn(".config.system.nixos.release", expression,
                          "raw auth is already evaluated and rechecked by the general proof")
            self.assertIn("nixosConfigurations.nixy-laptop", expression)
            return b"25.11"
        def general(expression,before,after,host,evaluator,callback):
            self.assertIs(callback,executor);events.append(host)
            return {"old":"a"*64,"new":"a"*64,"ssh_effective":{"ok":True}}
        adapter=SimpleNamespace(verify_general=general)
        spec=SimpleNamespace(loader=SimpleNamespace(exec_module=lambda module:None))
        with patch.object(batch.importlib.util,"spec_from_file_location",return_value=spec), \
             patch.object(batch.importlib.util,"module_from_spec",return_value=adapter):
            result=batch.verify_sensitive_state("/tmp/old","/tmp/new",evaluate,mode="batch",migration_executor=executor)
        self.assertEqual(events,["nixy-laptop"])
        self.assertEqual(set(result["protected_ssh"]),{"nixy-laptop"})
        self.assertEqual(result["reviewed_migration_transitions"],{})

    def test_host_scope_is_fixed_and_release_migration_remains_explicit(self):
        self.assertEqual(batch.update_hosts("batch"), ["nixy-laptop"])
        self.assertEqual(batch.update_hosts("release-migration"), batch.HOSTS)
        with self.assertRaises(batch.BatchError):
            batch.update_hosts("all")

    def test_changed_or_invalid_fresh_auth_proof_is_rejected(self):
        from types import SimpleNamespace
        for after in ["b"*64, "invalid"]:
            adapter=SimpleNamespace(verify_general=lambda *args: {"old":"a"*64,"new":after})
            spec=SimpleNamespace(loader=SimpleNamespace(exec_module=lambda module:None))
            with self.subTest(after=after), \
                 patch.object(batch.importlib.util,"spec_from_file_location",return_value=spec), \
                 patch.object(batch.importlib.util,"module_from_spec",return_value=adapter), \
                 self.assertRaises(batch.BatchError):
                batch.verify_sensitive_state("/tmp/old","/tmp/new",lambda expr:self.fail("must reject before release query"))

    def test_indirect_ssh_failure_blocks_equal_original_raw_hash(self):
        from types import SimpleNamespace
        def evaluate(expression):return b"a"*64
        def general(*args):raise migration.MigrationError("Indirect Match/Include/package/unit change")
        adapter=SimpleNamespace(verify_general=general)
        spec=SimpleNamespace(loader=SimpleNamespace(exec_module=lambda module:None))
        with patch.object(batch.importlib.util,"spec_from_file_location",return_value=spec), \
             patch.object(batch.importlib.util,"module_from_spec",return_value=adapter), \
             self.assertRaisesRegex(batch.BatchError,"Indirect Match"):
            batch.verify_sensitive_state("/tmp/old","/tmp/new",evaluate,mode="batch",migration_executor=lambda *args:b"fixture")


class ExactLockTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="weasel-migration-lock-fixture-", dir="/tmp")
        self.addCleanup(self.temporary.cleanup)
        self.source = Path(self.temporary.name)

    def write(self, side, **changed):
        expected = migration.TRANSITION[side]
        node = {"locked": copy.deepcopy(expected["locked"]),
                "original": {"type": "github", "owner": "nixos", "repo": "nixpkgs", "ref": "nixos-" + expected["release"]}}
        node.update(changed)
        data = {"version": 7, "root": "root", "nodes": {"root": {"inputs": {"nixpkgs": "stable"}}, "stable": node}}
        (self.source / "flake.lock").write_text(json.dumps(data))
        return data

    def test_exact_official_locked_revision_and_nar_are_required(self):
        for side in ["old", "new"]:
            self.write(side)
            migration.check_lock(self.source, side)

    def test_every_locked_field_and_unknown_fields_are_rejected(self):
        for key in list(migration.TRANSITION["new"]["locked"]) + ["unreviewed"]:
            with self.subTest(key=key):
                data = self.write("new")
                data["nodes"]["stable"]["locked"][key] = "changed"
                (self.source / "flake.lock").write_text(json.dumps(data))
                with self.assertRaises(migration.MigrationError):
                    migration.check_lock(self.source, "new")

    def test_other_repository_ref_or_protocol_is_rejected(self):
        for key in ["owner", "repo", "ref", "type"]:
            with self.subTest(key=key):
                data = self.write("new")
                data["nodes"]["stable"]["original"][key] = "changed"
                (self.source / "flake.lock").write_text(json.dumps(data))
                with self.assertRaises(migration.MigrationError):
                    migration.check_lock(self.source, "new")

    def test_real_main_baseline_exact_historical_duplicate_is_accepted(self):
        path = Path("/home/evilweasel/weasel-os/flake.lock")
        if not path.is_file():self.skipTest("Exact historical baseline unavailable; required bootstrap integration receipt is separate")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest()!=migration.LEGACY_BASELINE_LOCK_SHA256:
            self.skipTest("Main no longer has the historical lock; preserve the actual bootstrap baseline receipt")
        self.assertEqual(hashlib.sha256(data).hexdigest(), migration.LEGACY_BASELINE_LOCK_SHA256)
        (self.source / "flake.lock").write_bytes(data)
        migration.check_lock(self.source, "old")
        with self.assertRaisesRegex(migration.MigrationError, "Duplicate"):
            migration.object_json(data)
        with self.assertRaisesRegex(migration.MigrationError, "Duplicate"):
            migration.object_json(data+b"\n", legacy_baseline_lock=True)
        with self.assertRaisesRegex(migration.MigrationError, "Duplicate"):
            migration.check_lock(self.source, "new")

    def test_root_follows_alias_is_rejected(self):
        data = self.write("new")
        data["nodes"]["root"]["inputs"]["nixpkgs"] = ["parent", "nixpkgs"]
        (self.source / "flake.lock").write_text(json.dumps(data))
        with self.assertRaises(migration.MigrationError):
            migration.check_lock(self.source, "new")


def effective_fixture(identity=None, **changed):
    values = {key: "fixture-value" for key in migration.REQUIRED_EFFECTIVE_KEYS}
    values["banner"] = "none"
    values.update(changed)
    if identity is not None:
        values.update({"xauthlocation": identity["xauth"], "subsystem": identity["subsystem"]})
        values.update({key:identity[key] for key in ["sshdauthpath","sshdsessionpath"] if key in identity})
    return "".join(key + " " + value + "\n" for key, value in sorted(values.items()))


def compiled_default_fixture(**changed):
    return "".join(line + "\n" for line in effective_fixture(
        xauthlocation="/usr/X11R6/bin/xauth", **changed).splitlines()
        if not line.startswith("subsystem "))


class ProvenSSHDefaultsTests(unittest.TestCase):
    def fixture(self, versions=("10.2p1","10.5p1")):
        identities=[];configured=[];defaults=[]
        for i,version in enumerate(versions):
            package="/nix/store/"+str(i+1)*32+"-openssh-"+version
            identity={"version":version,"xauth":"/usr/X11R6/bin/xauth","set_xauth_location":False,
                      "subsystem":"sftp "+package+"/libexec/sftp-server",
                      "sshdauthpath":package+"/libexec/sshd-auth","sshdsessionpath":package+"/libexec/sshd-session"}
            fields={key:pair[0 if version=="10.2p1" else 1] for key,pair in migration.SSH_DEFAULT_TRANSITION_VALUES.items()}
            output=effective_fixture(identity,revokedkeys="none",**fields)
            if version=="10.5p1":
                output="".join(line+"\n" for line in output.splitlines() if not line.startswith("revokedkeys "))
            default="".join(line+"\n" for line in output.splitlines() if not line.startswith("subsystem "))
            identities.append(identity);configured.append(output);defaults.append(default)
        return configured,identities,defaults

    def compare(self,configured,identities,defaults,**options):
        return migration.compare_proven_effective_outputs(*configured,identities,defaults,**options)

    def test_exact_version_transition_requires_each_own_canonical_default(self):
        configured,identities,defaults=self.fixture()
        result=self.compare(configured,identities,defaults,allow_default_transition=True)
        self.assertTrue(result["ok"])
        self.assertEqual(set(result["approved_default_transition_fields"]),set(migration.SSH_DEFAULT_TRANSITION_VALUES))
        self.assertEqual(result["proven_empty_revocation_rendering"],[False,True])
        self.assertEqual(result["old_output_sha256"],hashlib.sha256(configured[0].encode()).hexdigest())
        self.assertEqual(result["new_output_sha256"],hashlib.sha256(configured[1].encode()).hexdigest())
        self.assertEqual(result["canonical_default_sha256"],[hashlib.sha256(value.encode()).hexdigest() for value in defaults])

    def test_general_batches_do_not_accept_semantic_default_transition(self):
        values=self.fixture()
        with self.assertRaisesRegex(migration.MigrationError,"effective policy"):
            self.compare(*values)
        for invalid in [1,"yes"]:
            with self.subTest(invalid=invalid),self.assertRaisesRegex(migration.MigrationError,"typed"):
                self.compare(*values,allow_default_transition=invalid)

    def test_same_version_10_5_omission_and_canonical_helper_paths_are_supported(self):
        configured,identities,defaults=self.fixture(("10.5p1","10.5p1"))
        result=self.compare(configured,identities,defaults)
        self.assertTrue(result["ok"])
        self.assertEqual(result["approved_default_transition_fields"],[])
        self.assertEqual(result["proven_empty_revocation_rendering"],[True,True])
        with self.assertRaisesRegex(migration.MigrationError,"outside exact"):
            self.compare(configured,identities,defaults,allow_default_transition=True)

    def test_authored_algorithm_or_penalty_change_is_not_normalized(self):
        for key in migration.SSH_DEFAULT_TRANSITION_VALUES:
            configured,identities,defaults=self.fixture()
            configured[1]=configured[1].replace(key+" "+migration.SSH_DEFAULT_TRANSITION_VALUES[key][1],key+" arbitrary-policy")
            with self.subTest(key=key),self.assertRaisesRegex(migration.MigrationError,"Authored"):
                self.compare(configured,identities,defaults,allow_default_transition=True)

    def test_changed_canonical_defaults_and_unknown_fields_are_rejected(self):
        for which in ["algorithm","new-default-field","new-configured-field"]:
            configured,identities,defaults=self.fixture()
            if which=="algorithm":
                key="pubkeyacceptedalgorithms";value=migration.SSH_DEFAULT_TRANSITION_VALUES[key][1]
                defaults[1]=defaults[1].replace(key+" "+value,key+" unknown-upstream-default")
            elif which=="new-default-field":defaults[1]+="unreviewedfield value\n"
            else:configured[1]+="unreviewedfield value\n"
            with self.subTest(which=which),self.assertRaises(migration.MigrationError):
                self.compare(configured,identities,defaults,allow_default_transition=True)

    def test_revocation_omission_requires_exact_canonical_evidence(self):
        for which in ["missing-default","unexpected-new-rendering","configured-override","old-omission","other-missing-field"]:
            configured,identities,defaults=self.fixture()
            if which=="missing-default":defaults[1]=""
            elif which=="unexpected-new-rendering":defaults[1]+="RevokedKeys none\n"
            elif which=="configured-override":configured[1]+="RevokedKeys /mutable/revocations\n"
            elif which=="old-omission":configured[0]="".join(line+"\n" for line in configured[0].splitlines() if not line.startswith("revokedkeys "))
            else:configured[1]="".join(line+"\n" for line in configured[1].splitlines() if not line.startswith("passwordauthentication "))
            with self.subTest(which=which),self.assertRaises(migration.MigrationError):
                self.compare(configured,identities,defaults,allow_default_transition=True)
        output=compiled_default_fixture()
        omitted="".join(line+"\n" for line in output.splitlines() if not line.startswith("revokedkeys "))
        with self.assertRaisesRegex(migration.MigrationError,"required"):
            migration.verify_compiled_default_output(omitted)
        self.assertTrue(migration.verify_compiled_default_output(omitted,version="10.5p1")["canonical_empty_revocation_omitted"])

    def test_arbitrary_helper_paths_fail_in_configured_and_default_outputs(self):
        for key in ["sshdauthpath","sshdsessionpath"]:
            for target in ["configured","default"]:
                configured,identities,defaults=self.fixture()
                outputs=configured if target=="configured" else defaults
                outputs[1]=outputs[1].replace(identities[1][key],"/mutable/helper")
                with self.subTest(key=key,target=target),self.assertRaisesRegex(migration.MigrationError,"helper"):
                    self.compare(configured,identities,defaults,allow_default_transition=True)

    def test_only_migration_callsite_enables_default_transition(self):
        tree=ast.parse((ROOT/"scripts/weasel-update-migration.py").read_text())
        functions={node.name:node for node in tree.body if isinstance(node,ast.FunctionDef)}
        calls=lambda name:[node for node in ast.walk(functions[name]) if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=="verify_built_ssh"]
        call=calls("verify")[0]
        enabled=next(keyword.value for keyword in call.keywords if keyword.arg=="allow_default_transition")
        self.assertIsInstance(enabled,ast.Constant);self.assertIs(enabled.value,True)
        self.assertFalse(any(keyword.arg=="allow_default_transition" for keyword in calls("verify_general")[0].keywords))


class SSHKeywordCaseTests(unittest.TestCase):
    def test_official_10_2_lowercase_and_10_5_keyword_spellings_compare_equal(self):
        # Public keyword spellings observed in the actual 10.5 parse; values
        # here are synthetic and daemon execution remains a separate gate.
        spellings = {"port":"Port", "addressfamily":"AddressFamily", "passwordauthentication":"PasswordAuthentication",
                     "kbdinteractiveauthentication":"KbdInteractiveAuthentication", "pubkeyauthentication":"PubkeyAuthentication",
                     "strictmodes":"StrictModes", "xauthlocation":"XAuthLocation", "subsystem":"Subsystem"}
        old = effective_fixture()
        new = "".join(spellings.get(key,key) + " " + value + "\n"
                      for key,value in (line.split(" ",1) for line in old.splitlines()))
        self.assertEqual(migration.parse_effective_output(old),migration.parse_effective_output(new))
        self.assertTrue(migration.compare_effective_outputs(old,new)["ok"])

    def test_ascii_case_normalization_never_changes_values(self):
        output = effective_fixture(futurekeyword="Keep_CASE")
        upper = "".join(key.upper()+" "+value+"\n" for key,value in (line.split(" ",1) for line in output.splitlines()))
        self.assertTrue(migration.compare_effective_outputs(output,upper)["ok"])
        self.assertEqual(migration.parse_effective_output(upper)["futurekeyword"],["Keep_CASE"])
        with self.assertRaisesRegex(migration.MigrationError,"effective policy"):
            migration.compare_effective_outputs(output,upper.replace("Keep_CASE","keep_case"))

    def test_case_insensitive_duplicate_aliases_are_rejected_even_for_multivalued_keys(self):
        for output in ["port 22\nPort 22\n", "HostKey /public/one\nhostkey /public/two\n",
                       "XAuthLocation /public/xauth\nxauthlocation /public/xauth\n"]:
            with self.subTest(output=output),self.assertRaisesRegex(migration.MigrationError,"case-insensitive"):
                migration.parse_effective_output(output)
        self.assertEqual(migration.parse_effective_output("HostKey /public/one\nHostKey /public/two\n")["hostkey"],["/public/one","/public/two"])

    def test_punctuation_nonascii_and_malformed_keywords_still_fail(self):
        for output in ["Permit_RootLogin no\n","Bänner none\n","1Port 22\n","Port- 22\n"," Port 22\n","Port\n"]:
            with self.subTest(output=output),self.assertRaises(migration.MigrationError):
                migration.parse_effective_output(output)

    def test_mixedcase_defaults_keep_43_required_fields_and_unique_names(self):
        output = compiled_default_fixture()
        mixed = "".join(key.title()+" "+value+"\n" for key,value in (line.split(" ",1) for line in output.splitlines()))
        self.assertTrue(migration.verify_compiled_default_output(mixed)["ok"])
        with self.assertRaisesRegex(migration.MigrationError,"case-insensitive"):
            migration.verify_compiled_default_output(mixed+"xauthlocation /usr/X11R6/bin/xauth\n")


class CompiledDefaultSSHTests(unittest.TestCase):
    def test_unconfigured_subsystem_is_the_only_required_key_exception(self):
        output = compiled_default_fixture()
        values = migration.parse_effective_output(output)
        self.assertEqual(migration.REQUIRED_EFFECTIVE_KEYS - set(values), {"subsystem"})
        proof = migration.verify_compiled_default_output(output)
        self.assertTrue(proof["ok"])
        self.assertEqual(proof["checked_keys"], sorted(values))
        self.assertEqual(values["xauthlocation"], ["/usr/X11R6/bin/xauth"])
        self.assertEqual(proof["old_output_sha256"], hashlib.sha256(output.encode()).hexdigest())
        self.assertEqual(proof["new_output_sha256"], proof["old_output_sha256"])

    def test_every_other_required_default_key_is_required(self):
        output = compiled_default_fixture()
        for key in migration.REQUIRED_EFFECTIVE_KEYS - {"subsystem"}:
            omitted = "".join(line + "\n" for line in output.splitlines() if not line.startswith(key + " "))
            with self.subTest(key=key), self.assertRaisesRegex(migration.MigrationError, "required"):
                migration.verify_compiled_default_output(omitted)

    def test_duplicate_scalar_default_fields_are_rejected(self):
        output = compiled_default_fixture()
        values = migration.parse_effective_output(output)
        for key in set(values) - migration.MULTIVALUED_EFFECTIVE_KEYS:
            duplicate = output + key + " " + values[key][0] + "\n"
            with self.subTest(key=key), self.assertRaisesRegex(migration.MigrationError, "duplicate"):
                migration.verify_compiled_default_output(duplicate)
        with self.assertRaisesRegex(migration.MigrationError, "duplicate"):
            migration.verify_compiled_default_output(output + "futurekeyword yes\nfuturekeyword yes\n")

    def test_malformed_or_ambiguous_defaults_fail(self):
        output = compiled_default_fixture()
        for changed in [output+"not-a-line\n", output+"Banner none\n", "banner "+"a"*(1024*1024),
                        output+"allowusers \n", output+"acceptenv LANG\n", output.replace("banner none", "banner /mutable/banner")]:
            with self.subTest(changed_prefix=changed[:20]), self.assertRaises(migration.MigrationError):
                migration.verify_compiled_default_output(changed)

    def test_complete_extra_default_fields_are_retained_in_digest(self):
        output = compiled_default_fixture()
        extra = output + "futurekeyword yes\n"
        before, after = (migration.verify_compiled_default_output(value) for value in [output,extra])
        self.assertIn("futurekeyword",after["checked_keys"])
        self.assertNotEqual(before["old_output_sha256"],after["old_output_sha256"])


class FreshEffectiveSSHTests(unittest.TestCase):
    def test_complete_unique_full_effective_output_is_accepted(self):
        output = effective_fixture()
        self.assertTrue(migration.compare_effective_outputs(output, output)["ok"])

    def test_configured_output_still_requires_the_sftp_subsystem(self):
        output = compiled_default_fixture()
        with self.assertRaisesRegex(migration.MigrationError, "required"):
            migration.compare_effective_outputs(output,output)
        self.assertIn("subsystem",migration.REQUIRED_EFFECTIVE_KEYS)

    def test_plain_daemon_without_compiled_gssapi_is_accepted(self):
        self.assertNotIn("gssapiauthentication", migration.REQUIRED_EFFECTIVE_KEYS)
        output = effective_fixture()
        self.assertNotIn("gssapiauthentication", migration.parse_effective_output(output))
        self.assertTrue(migration.compare_effective_outputs(output, output)["ok"])

    def test_present_gssapi_authentication_value_is_compared(self):
        output = effective_fixture(gssapiauthentication="no")
        self.assertTrue(migration.compare_effective_outputs(output, output)["ok"])
        with self.assertRaisesRegex(migration.MigrationError, "effective policy"):
            migration.compare_effective_outputs(output, effective_fixture(gssapiauthentication="yes"))

    def test_gssapi_authentication_presence_change_is_rejected(self):
        absent = effective_fixture()
        present = effective_fixture(gssapiauthentication="no")
        for old, new in [(absent, present), (present, absent)]:
            with self.subTest(old_has_gssapi="gssapiauthentication" in old), self.assertRaisesRegex(migration.MigrationError, "effective policy"):
                migration.compare_effective_outputs(old, new)

    def test_gssapi_authentication_scalar_duplication_is_rejected(self):
        output = effective_fixture(gssapiauthentication="no")
        duplicate = output + "gssapiauthentication no\n"
        for old, new in [(output, duplicate), (duplicate, output)]:
            with self.subTest(duplicate_before=old == duplicate), self.assertRaisesRegex(migration.MigrationError, "duplicates"):
                migration.compare_effective_outputs(old, new)

    def test_banner_only_or_eighteen_missing_keys_cannot_pass(self):
        for output in ["banner none\n", "banner none\npermitrootlogin no\n"]:
            with self.subTest(output=output), self.assertRaisesRegex(migration.MigrationError, "required"):
                migration.compare_effective_outputs(output, output)

    def test_every_mandatory_field_must_be_present_and_unique(self):
        output = effective_fixture()
        for key in migration.REQUIRED_EFFECTIVE_KEYS:
            for duplicate in [False, True]:
                changed = "".join(line + "\n" for line in output.splitlines() if not line.startswith(key + " "))
                if duplicate:
                    changed = output + next(line for line in output.splitlines() if line.startswith(key + " ")) + "\n"
                with self.subTest(key=key, duplicate=duplicate), self.assertRaises(migration.MigrationError):
                    migration.compare_effective_outputs(output, changed)

    def test_banner_change_is_rejected(self):
        with self.assertRaisesRegex(migration.MigrationError, "Banner"):
            migration.compare_effective_outputs(effective_fixture(), effective_fixture(banner="/private/banner"))

    def test_acceptenv_from_daemon_defaults_is_rejected(self):
        with self.assertRaisesRegex(migration.MigrationError, "environment"):
            migration.compare_effective_outputs(effective_fixture(), effective_fixture() + "acceptenv LANG\n")

    def test_every_unselected_effective_authentication_change_is_rejected(self):
        for key in ["authenticationmethods", "permituserenvironment", "pubkeyauthentication", "hostbasedauthentication", "forcecommand", "authorizedkeyscommand", "newfuturekeyword"]:
            with self.subTest(key=key), self.assertRaisesRegex(migration.MigrationError, "effective policy"):
                migration.compare_effective_outputs(effective_fixture(), effective_fixture(**{key: "changed"}))

    def test_optional_empty_lists_are_absent_but_cannot_change(self):
        output = effective_fixture()
        self.assertEqual(migration.compare_effective_outputs(output, output)["optional_absent_keys"], sorted(migration.OPTIONAL_EMPTY_KEYS))
        for key in migration.OPTIONAL_EMPTY_KEYS:
            with self.subTest(key=key), self.assertRaises(migration.MigrationError):
                migration.compare_effective_outputs(output, output + key + " root\n")

    def test_only_exact_trusted_executable_paths_can_change(self):
        old = {"xauth": "/nix/store/" + "a"*32 + "-xauth/bin/xauth", "subsystem": "sftp /nix/store/" + "b"*32 + "-openssh/libexec/sftp-server", "set_xauth_location": True}
        new = {"xauth": "/nix/store/" + "c"*32 + "-xauth/bin/xauth", "subsystem": "sftp /nix/store/" + "d"*32 + "-openssh/libexec/sftp-server", "set_xauth_location": True}
        self.assertTrue(migration.compare_effective_outputs(effective_fixture(old), effective_fixture(new), [old,new])["ok"])
        for key in ["xauthlocation", "subsystem"]:
            changed=effective_fixture(new, **{key:"/mutable/fake"})
            # Explicit override after fixture identity population.
            changed=changed.replace(new["xauth" if key=="xauthlocation" else "subsystem"], "/mutable/fake")
            with self.subTest(key=key), self.assertRaises(migration.MigrationError):
                migration.compare_effective_outputs(effective_fixture(old), changed, [old,new])

    def test_false_xauth_keeps_the_exact_compiled_default_literal(self):
        identity = {"xauth": "/usr/X11R6/bin/xauth", "subsystem": "sftp /nix/store/" + "a"*32 + "-openssh/libexec/sftp-server", "set_xauth_location": False}
        self.assertTrue(migration.compare_effective_outputs(effective_fixture(identity), effective_fixture(identity), [identity,identity])["ok"])
        changed = dict(identity, xauth="/usr/bin/xauth")
        with self.assertRaisesRegex(migration.MigrationError, "effective policy"):
            migration.compare_effective_outputs(effective_fixture(identity), effective_fixture(changed), [identity,changed])
        for xauth in ["/mutable/xauth", "/nix/store/" + "b"*32 + "-xauth/bin/xauth"]:
            with self.subTest(xauth=xauth), self.assertRaisesRegex(migration.MigrationError, "outside"):
                migration.compare_effective_outputs(effective_fixture(identity), effective_fixture(identity).replace(identity["xauth"],xauth), [identity,identity])

    def test_xauth_typed_option_changes_or_missing_identity_fail(self):
        identity = {"xauth": "/usr/X11R6/bin/xauth", "subsystem": "sftp /nix/store/" + "a"*32 + "-openssh/libexec/sftp-server", "set_xauth_location": False}
        for changed in [dict(identity,set_xauth_location=True), dict(identity,set_xauth_location=0), {key:value for key,value in identity.items() if key!="set_xauth_location"}]:
            with self.subTest(changed=changed), self.assertRaisesRegex(migration.MigrationError, "XAuth"):
                migration.compare_effective_outputs(effective_fixture(identity), effective_fixture(changed), [identity,changed])

    def test_malformed_or_huge_daemon_output_is_rejected(self):
        for value in ["not-a-line", "Bänner none\n", "banner " + "a" * (1024 * 1024)]:
            with self.subTest(value=value[:16]), self.assertRaises(migration.MigrationError):
                migration.parse_effective_output(value)

    def run_fixture(self, *, bad_build=False, changed_port=False, invalid_identity=False,
                    set_xauth_location=True, with_limits=False, missing_limits=False, changed_compiled_default=False, unit_names=None,
                    allow_default_transition=False, missing_root_tree=False, wrong_root_tree=False,
                    extra_build=False, unexpected_root_tree=False):
        events = []
        sources = [Path("/tmp/old-fixture"), Path("/tmp/new-fixture")]
        roots = [{"package": "/nix/store/" + "a" * 32 + "-openssh-old", "config": "/nix/store/" + "b" * 32 + "-sshd.conf", "ports": [22]},
                 {"package": "/nix/store/" + "c" * 32 + "-openssh-new", "config": "/nix/store/" + "d" * 32 + "-sshd.conf", "ports": [23 if changed_port else 22]}]
        for i,root in enumerate(roots):
            root.update(xauth="/nix/store/" + str(i+1)*32 + "-xauth/bin/xauth", subsystem="sftp " + root["package"] + "/libexec/sftp-server",
                        set_xauth_location=set_xauth_location, version=["10.2p1","10.5p1"][i],
                        pam_limits="/nix/store/" + str(i+1)*32 + "-limits.conf" if with_limits else None,
                        unit_tree="/nix/store/" + str(i+3)*32 + "-system-units", root_unit_tree="/nix/store/"+str(i+7)*32+"-system-units", units={"sshd.service":"/nix/store/"+str(i+5)*32+"-unit-sshd.service"})
        if unit_names is not None:
            for index,root in enumerate(roots):
                root["units"] = {name: "/nix/store/"+str(index+5)*32+"-unit-"+name.replace("@", "") for name in unit_names}
        if invalid_identity:
            roots[1]["package"] = "/home/evilweasel/mutable-sshd"
        def evaluate(expression):
            index = 0 if str(sources[0]) in expression else 1
            self.assertIn('in assert sshBindingsOkay;',expression)
            self.assertIn(("build",index),events,"Cold-source identity cannot be evaluated before realization")
            events.append(("identity", index))
            return json.dumps(roots[index])
        def execute(name, arguments):
            if name == "nix":
                index = 0 if str(sources[0]) in arguments[-1] else 1
                self.assertIn('in assert sshBindingsOkay;',arguments[-1])
                self.assertEqual(arguments[arguments.index('--option')+1:arguments.index('--option')+3], ['allow-import-from-derivation','true'])
                self.assertEqual(arguments[arguments.index('--max-jobs')+1], '1')
                self.assertEqual(arguments[arguments.index('--cores')+1], '2')
                events.append(("build", index))
                outputs = [{"outputs": {"out": roots[index]["package"]}}, {"outputs": {"out": roots[index]["config"]}},
                           {"outputs": {"out": roots[index]["unit_tree"]}}]
                self.assertIn("cleanPkgs.lib.optional pamLimitsEnabled pamLimitsArtifact",arguments[-1])
                full_tree_suffix = ' ++ [c.environment.etc."systemd/system".source]'
                self.assertEqual(arguments[-1].endswith(full_tree_suffix), allow_default_transition)
                if with_limits and not missing_limits:
                    outputs.append({"outputs":{"out":roots[index]["pam_limits"]}})
                if (allow_default_transition and not missing_root_tree) or unexpected_root_tree:
                    root = roots[index]["root_unit_tree"]
                    if wrong_root_tree:
                        root = "/nix/store/" + "e" * 32 + "-wrong-system-units"
                    outputs.append({"outputs":{"out":root}})
                if extra_build:
                    outputs.append({"outputs":{"out":"/nix/store/" + "f" * 32 + "-unexpected-output"}})
                if bad_build and index == 1:
                    outputs[1]["outputs"]["out"] = "/nix/store/" + "e" * 32 + "-unrelated-conf"
                return json.dumps(outputs).encode()
            self.assertEqual(name, "bwrap")
            self.assertIn("--clearenv", arguments)
            self.assertIn("--unshare-all", arguments)
            self.assertNotIn("/etc/ssh", [arguments[i + 1] for i, v in enumerate(arguments[:-1]) if v == "--ro-bind"])
            self.assertIn("-G", arguments)
            self.assertIn("-T", arguments)
            self.assertNotIn("SSH_AUTH_SOCK", arguments)
            self.assertNotIn("NIXOS_NO_CHECK", arguments)
            index = 0 if roots[0]["package"] + "/bin/sshd" in arguments else 1
            self.assertIn(("build", index), events)
            identity = dict(roots[index])
            identity.update(sshdauthpath=roots[index]["package"]+"/libexec/sshd-auth",
                            sshdsessionpath=roots[index]["package"]+"/libexec/sshd-session")
            if not set_xauth_location:
                identity["xauth"] = "/usr/bin/xauth" if changed_compiled_default and index==1 else "/usr/X11R6/bin/xauth"
            fields = ({key: pair[index] for key, pair in migration.SSH_DEFAULT_TRANSITION_VALUES.items()}
                      if allow_default_transition else {})
            if arguments[arguments.index("-f")+1] == "/dev/null":
                self.assertNotIn("-C",arguments)
                events.append(("defaults",index))
                identity["xauth"]="/usr/bin/xauth" if changed_compiled_default and index==1 else "/usr/X11R6/bin/xauth"
                return "".join(line + "\n" for line in effective_fixture(identity,revokedkeys="none",**fields).splitlines()
                               if not line.startswith("subsystem ") and not (index==1 and line.startswith("revokedkeys "))).encode()
            else:
                events.append(("parse", index))
            configured = effective_fixture(identity, **({"revokedkeys":"none"} | fields) if allow_default_transition else {})
            if allow_default_transition and index == 1:
                configured = "".join(line + "\n" for line in configured.splitlines() if not line.startswith("revokedkeys "))
            return configured.encode()
        with patch.dict(os.environ, {}, clear=True), patch.object(Path, "is_file", return_value=True), patch.object(Path, "read_bytes", return_value=b"synthetic-private-fixture"), \
             patch.object(migration, "verify_selected_ssh_units", return_value={"mocked":"orchestration only"}) as selected:
            # Real graph selection is covered separately with immutable cached units.
            result = migration.verify_built_ssh(*sources, "nixy-laptop", evaluate, execute,
                                                allow_default_transition=allow_default_transition)
            self.assertEqual(selected.call_count,2)
        return result, events

    def test_false_xauth_uses_each_exact_daemon_private_default_before_contexts(self):
        result, events = self.run_fixture(set_xauth_location=False)
        self.assertTrue(result["ok"])
        self.assertEqual([e for e in events if e[0]=="defaults"],[("defaults",0),("defaults",1)])
        self.assertEqual(len([e for e in events if e[0]=="parse"]),10)
        for index in [0,1]:
            self.assertLess(events.index(("defaults",index)),events.index(("parse",index)))
            self.assertEqual(result["built"][index]["executable_identity"]["xauth"],"/usr/X11R6/bin/xauth")
            self.assertFalse(result["built"][index]["executable_identity"]["set_xauth_location"])
            self.assertTrue(result["built"][index]["compiled_default_proof"]["ok"])
        with self.assertRaisesRegex(migration.MigrationError,"effective policy"):
            self.run_fixture(set_xauth_location=False,changed_compiled_default=True)

    def test_exact_canonical_limits_output_is_required_when_enabled(self):
        result, events = self.run_fixture(with_limits=True)
        self.assertTrue(result["ok"])
        self.assertTrue(all(item["pam_limits"].endswith("-limits.conf") for item in result["built"]))
        with self.assertRaisesRegex(migration.MigrationError,"outputs do not match"):
            self.run_fixture(with_limits=True,missing_limits=True)

    def test_migration_realizes_exact_full_unit_tree_on_both_sides(self):
        for with_limits in [False,True]:
            with self.subTest(with_limits=with_limits):
                result,events=self.run_fixture(allow_default_transition=True,with_limits=with_limits)
                self.assertTrue(result["ok"])
                self.assertEqual([event for event in events if event[0]=="build"],[("build",0),("build",1)])
                self.assertEqual(len([event for event in events if event[0]=="parse"]),10)
                self.assertTrue(all(item["full_root_tree_derivation"].endswith("-system-units") for item in result["built"]))

    def test_migration_rejects_missing_wrong_or_extra_full_unit_outputs(self):
        for options in [{"missing_root_tree":True},{"wrong_root_tree":True},{"extra_build":True}]:
            with self.subTest(options=options),self.assertRaisesRegex(migration.MigrationError,"outputs do not match"):
                self.run_fixture(allow_default_transition=True,**options)

    def test_ordinary_batch_does_not_admit_full_unit_tree_output(self):
        result,events=self.run_fixture()
        self.assertTrue(result["ok"])
        for options in [{"unexpected_root_tree":True},{"extra_build":True}]:
            with self.subTest(options=options),self.assertRaisesRegex(migration.MigrationError,"outputs do not match"):
                self.run_fixture(**options)

    def test_both_exact_outputs_are_built_before_private_parse(self):
        result, events = self.run_fixture()
        self.assertTrue(result["ok"])
        self.assertEqual([e for e in events if e[0] == "build"], [("build", 0), ("build", 1)])
        self.assertEqual(len([e for e in events if e[0] == "parse"]), 10)
        self.assertEqual(len(result["contexts"]), 5)

    def test_cold_source_build_precedes_ifd_disabled_identity_on_both_sides(self):
        _,events=self.run_fixture()
        for index in [0,1]:
            self.assertLess(events.index(("build",index)),events.index(("identity",index)))

    def test_failed_fixed_build_never_attempts_evaluator_or_fallback(self):
        evaluated=[];executed=[]
        def evaluate(expression):
            evaluated.append(expression)
            raise AssertionError("Evaluator must remain unexecuted")
        def execute(name,arguments):
            executed.append((name,arguments))
            raise migration.MigrationError("Actual fixed mini-build failed")
        with patch.dict(os.environ,{},clear=True),self.assertRaisesRegex(migration.MigrationError,"mini-build failed"):
            migration.verify_built_ssh("/tmp/old","/tmp/new","nixy-laptop",evaluate,execute)
        self.assertFalse(evaluated);self.assertEqual(len(executed),1)
        self.assertEqual(executed[0][0],"nix")
        self.assertNotIn("system.build.toplevel",executed[0][1][-1])

    def test_wrong_built_config_cannot_reuse_a_receipt(self):
        with self.assertRaisesRegex(migration.MigrationError, "exact configured"):
            self.run_fixture(bad_build=True)

    def test_mutable_program_cannot_be_parsed_instead_of_built_daemon(self):
        with self.assertRaisesRegex(migration.MigrationError, "identity"):
            self.run_fixture(invalid_identity=True)

    def test_official_old_three_and_new_five_unit_name_schemas_pass(self):
        old_names = ["sshd.service", "sshd@.service", "sshd-keygen.service"]
        new_names = old_names + ["sshd-vsock.socket", "sshd-vsock@.service"]
        for names in [old_names,new_names]:
            with self.subTest(names=names):
                result,events=self.run_fixture(unit_names=names)
                self.assertTrue(result["ok"])
                self.assertEqual(len([e for e in events if e[0]=="parse"]),10)

    def test_unknown_ssh_units_and_dropin_names_still_fail_before_parsing(self):
        for name in ["sshd-evil.service", "sshd.socket", "sshd-vsock.service", "sshd-vsock@evil.service", "sshd.service.d", "../sshd.service"]:
            with self.subTest(name=name), self.assertRaisesRegex(migration.MigrationError,"build identity"):
                self.run_fixture(unit_names=["sshd.service",name])

    def test_listen_port_change_is_rejected(self):
        with self.assertRaisesRegex(migration.MigrationError, "listen-port"):
            self.run_fixture(changed_port=True)


class SelectedSSHUnitGraphTests(unittest.TestCase):
    """Real filesystem graph fixtures referencing only public immutable units."""
    def setUp(self):
        live = Path("/run/current-system/etc/systemd/system").resolve()
        names = {"sshd.service", "sshd@.service", "sshd-keygen.service"}
        if not all((live/name).is_symlink() for name in names):
            self.skipTest("Public cached NixOS SSH units unavailable in this test host")
        self.units = {name:str(Path(os.readlink(live/name)).parent) for name in names}
        self.temporary=tempfile.TemporaryDirectory(prefix="weasel-selected-unit-graph-",dir="/tmp")
        self.addCleanup(self.temporary.cleanup)
        self.tree=Path(self.temporary.name)/"tree";self.tree.mkdir()
        for name,root in self.units.items():
            (self.tree/name).symlink_to(Path(root)/name)

    def test_exact_selected_immutable_units_pass_with_real_file_hashes(self):
        result=migration.verify_selected_ssh_units(self.tree,self.units)
        self.assertEqual(set(result),set(self.units))
        self.assertTrue(all(len(v["sha256"])==64 for v in result.values()))

    def test_extra_ssh_dropin_or_unknown_unit_cannot_shadow_expected_unit(self):
        for name in ["sshd.service.d","sshd-evil.service","sshd.socket"]:
            path=self.tree/name;path.mkdir()
            with self.subTest(name=name),self.assertRaises(migration.MigrationError):
                migration.verify_selected_ssh_units(self.tree,self.units)
            path.rmdir()

    def test_selected_mutable_or_other_immutable_unit_is_rejected(self):
        link=self.tree/"sshd.service";link.unlink()
        fake=Path(self.temporary.name)/"fake";fake.write_text("[Service]\nExecStart=/mutable/fake\n")
        for target in [fake,Path(self.units["sshd@.service"])/"sshd@.service"]:
            link.symlink_to(target)
            with self.subTest(target=str(target)),self.assertRaises(migration.MigrationError):
                migration.verify_selected_ssh_units(self.tree,self.units)
            link.unlink()

    def add_official_dropins(self):
        roots = {"sshd-vsock.socket": "/nix/store/wklnf511c9fhb47ym01a9h2i1jj8vgl2-unit-sshd-vsock.socket",
                 "sshd-vsock@.service": "/nix/store/fds7d1mag1q0m1qc06v3mvv4ikhijk2w-unit-sshd-vsock-.service"}
        if not all((Path(root)/name).is_file() for name,root in roots.items()):
            self.skipTest("Exact approved26.05 public SSH drop-in artifacts unavailable")
        self.units.update(roots)
        for name,root in roots.items():
            directory=self.tree/(name+".d");directory.mkdir()
            (directory/"overrides.conf").symlink_to(Path(root)/name)
        return roots

    def test_official_vsock_dropins_preserve_exact_canonical_file_hashes(self):
        roots=self.add_official_dropins()
        result=migration.verify_selected_ssh_units(self.tree,self.units)
        self.assertEqual(set(result),set(self.units))
        for name,root in roots.items():
            self.assertEqual(result[name]["selected_file"],str((Path(root)/name).resolve()))
            self.assertEqual(result[name]["sha256"],hashlib.sha256((Path(root)/name).read_bytes()).hexdigest())

    def test_vsock_extra_dropin_files_and_base_shadows_are_rejected(self):
        roots=self.add_official_dropins();name="sshd-vsock.socket"
        extra=self.tree/(name+".d")/"extra.conf";extra.write_text("[Socket]\nListenStream=vsock::2222\n")
        with self.assertRaises(migration.MigrationError):migration.verify_selected_ssh_units(self.tree,self.units)
        extra.unlink()
        shadow=self.tree/name;shadow.symlink_to(Path(roots[name])/name)
        with self.assertRaises(migration.MigrationError):migration.verify_selected_ssh_units(self.tree,self.units)
        shadow.unlink()
        unknown=self.tree/"sshd-unknown.service.d";unknown.mkdir()
        with self.assertRaises(migration.MigrationError):migration.verify_selected_ssh_units(self.tree,self.units)

    def test_vsock_override_targets_files_and_directory_aliases_remain_strict(self):
        roots=self.add_official_dropins();name="sshd-vsock.socket";directory=self.tree/(name+".d")
        link=directory/"overrides.conf";link.unlink()
        with self.assertRaises(migration.MigrationError):migration.verify_selected_ssh_units(self.tree,self.units)
        link.symlink_to(Path(self.units["sshd.service"])/"sshd.service")
        with self.assertRaises(migration.MigrationError):migration.verify_selected_ssh_units(self.tree,self.units)
        link.unlink();link.write_bytes((Path(roots[name])/name).read_bytes())
        with self.assertRaises(migration.MigrationError):migration.verify_selected_ssh_units(self.tree,self.units)
        link.unlink();link.symlink_to(Path(roots[name])/name)
        moved=self.tree.parent/"private-dropin-directory";directory.rename(moved);directory.symlink_to(moved,target_is_directory=True)
        with self.assertRaises(migration.MigrationError):migration.verify_selected_ssh_units(self.tree,self.units)

    def test_missing_and_noncanonical_tree_are_rejected(self):
        (self.tree/"sshd.service").unlink()
        with self.assertRaises(migration.MigrationError):migration.verify_selected_ssh_units(self.tree,self.units)
        alias=Path(self.temporary.name)/"alias";alias.symlink_to(self.tree)
        with self.assertRaises(migration.MigrationError):migration.verify_selected_ssh_units(alias,self.units)


class ActivatorEnvironmentTests(unittest.TestCase):
    def test_absent_bypass_variable_is_allowed(self):
        migration.reject_switch_bypass_environment({"PATH": "/nix/store/fixture/bin"})
        with patch.dict(os.environ, {}, clear=True):
            activation.require_switch_checks()

    def test_every_present_bypass_value_is_refused(self):
        for value in ["", "0", "1", "false", "malformed"]:
            with self.subTest(value=value), patch.dict(os.environ, {"NIXOS_NO_CHECK": value}, clear=True):
                with self.assertRaises(migration.MigrationError):
                    migration.reject_switch_bypass_environment(os.environ)
                with self.assertRaises(activation.ActivationError):
                    activation.require_switch_checks()

    def test_runner_refuses_before_subprocess(self):
        with patch.dict(os.environ, {"NIXOS_NO_CHECK": "1"}, clear=True), patch.object(activation.subprocess, "run") as command:
            with self.assertRaises(activation.ActivationError):
                activation.Runner.run(["/unexecuted/fixture"])
            command.assert_not_called()

    def test_exact_activation_refuses_before_profile_or_switch(self):
        with patch.dict(os.environ, {"NIXOS_NO_CHECK": "1"}, clear=True), patch.object(activation, "valid_system") as valid:
            with self.assertRaises(activation.ActivationError):
                activation.activate_exact(None, Path("/unexecuted/fixture"))
            valid.assert_not_called()

    def test_cli_refuses_before_consuming_request_or_creating_state(self):
        with patch.dict(os.environ, {"NIXOS_NO_CHECK": "1"}, clear=True), patch.object(sys, "argv", ["activation"]), \
             patch.object(activation, "consume_request") as consume, patch.object(activation, "ensure_directory") as directory:
            with self.assertRaises(activation.ActivationError):
                activation.main()
            consume.assert_not_called()
            directory.assert_not_called()

    def test_process_refuses_before_any_request_or_journal_work(self):
        with patch.dict(os.environ, {"NIXOS_NO_CHECK": "1"}, clear=True):
            with self.assertRaises(activation.ActivationError):
                activation.process_request(None, {}, None, None)

    def test_manager_variable_is_explicitly_unset_in_proposed_unit(self):
        text = (ROOT / "modules/daily-updates.nix").read_text()
        self.assertIn('UnsetEnvironment = "NIXOS_NO_CHECK";', text)


class SyntheticNixTransitionTests(unittest.TestCase):
    """Execute actual Nix hash/assert logic with only synthetic users/data.

    Module provenance references the already verified public read-only cache.
    Fresh sshd execution is separately mocked and never counted as real proof.
    """
    def setUp(self):
        self.fixture = old_tests.SemanticBatchTests(methodName="test_unchanged_effective_protection_is_allowed")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.before, self.after = self.fixture.before, self.fixture.after
        self.config = copy.deepcopy(self.fixture.config)
        settings = {key: None for key in migration.SSH_OLD_KEYS}
        settings.update({"PermitRootLogin": "no", "PasswordAuthentication": True,
                         "KbdInteractiveAuthentication": True, "UsePAM": True,
                         "UseDns": False, "StrictModes": True, "PrintMotd": False,
                         "X11Forwarding": False, "LogLevel": "INFO", "GatewayPorts": "no",
                         "Ciphers": ["fixture-cipher"], "Macs": ["fixture-mac"],
                         "KexAlgorithms": ["fixture-kex"], "AuthorizedPrincipalsFile": "none"})
        self.config["services"]["openssh"]["settings"] = settings
        self.config["security"]["sudo"]["extraConfig"] = "\n\n# Synthetic retained sudo policy\nDefaults:root,%wheel env_keep+=TERMINFO\n"
        self.candidate = copy.deepcopy(self.config)
        self.candidate["system"]["nixos"]["release"] = "26.05"
        self.candidate["services"]["openssh"]["settings"].update({"AcceptEnv": None, "Banner": None})
        self.candidate["security"]["sudo"]["extraConfig"] = migration.SUDO_PREFIX + self.config["security"]["sudo"]["extraConfig"]

    def write(self, candidate):
        for source, config, upstream in [(self.before, self.config, "/nix/store/zg0fzjv0hmm0g6jdbqcf6b81b4hrqi5p-source"),
                                         (self.after, candidate, "/nix/store/1ffpr0yhnxfkyj467rjjxfzsmc9564qb-source")]:
            if source.exists():
                assert source.parent == self.before.parent and source.name in {"old", "new"}
                shutil.rmtree(source)
            self.fixture.write_source(source, config)
            path = source / "flake.nix"
            text = path.read_text().replace("in { nixosConfigurations", 'in { inputs.nixpkgs.outPath = ' + json.dumps(upstream) + '; nixosConfigurations')
            path.write_text(text)

    def hashes(self, candidate):
        self.write(candidate)
        with patch.object(batch, "codex_ownership_predicate", return_value="true"), \
             patch.object(migration, "ssh_binding_fragment", return_value="sshBindingsOkay = true; sshPolicy = {}; "), \
             patch.object(migration, "release_defaults_fragment", return_value="releaseDefaultsOkay=true; releaseFlags={}; releaseGroupsNormalized={}; releaseUsersNormalized=v.users; releasePolkitNormalized=v.polkit.extraConfig; releaseSSHPolicy=sshPolicy; "):
            # These synthetic auth fixtures do not constitute the independently
            # composed real NixOS SSH binding proof; that is run separately.
            # The synthetic flake intentionally declares no fetched inputs.
            # Substitute only the already verified public module-cache path;
            # actual lock-node and production expression provenance are tested
            # independently. All auth/state values still come from real Nix.
            expression_old = migration.expression(batch.sensitive_expression, self.before, "nixy-laptop", "old").replace("f.inputs.nixpkgs.outPath", '"/nix/store/zg0fzjv0hmm0g6jdbqcf6b81b4hrqi5p-source"')
            expression_new = migration.expression(batch.sensitive_expression, self.after, "nixy-laptop", "new").replace("f.inputs.nixpkgs.outPath", '"/nix/store/1ffpr0yhnxfkyj467rjjxfzsmc9564qb-source"')
            old = migration.validate_envelope(self.fixture.evaluate(expression_old), "old")
            new = migration.validate_envelope(self.fixture.evaluate(expression_new), "new")
        return old, new

    def test_actual_nix_exact_prefix_and_null_transition_matches_all_protected_hashes(self):
        old, new = self.hashes(self.candidate)
        self.assertEqual(old["normalized_sha256"], new["normalized_sha256"])
        self.assertNotEqual(old["raw_sha256"], new["raw_sha256"])

    def test_no_real_sudo_policy_optin_means_no_acceptance(self):
        with patch.object(migration, "APPROVED_2511_TO_2605_ENV_KEEP", False):
            with self.assertRaisesRegex(migration.MigrationError, "explicitly approved"):
                migration.verify(batch.sensitive_expression, self.before, self.after, "nixy-laptop", None, None)

    def test_non_null_new_ssh_fields_fail_actual_nix_assertion(self):
        for key, value in [("AcceptEnv", ["LANG"]), ("Banner", "/private/banner")]:
            candidate = copy.deepcopy(self.candidate)
            candidate["services"]["openssh"]["settings"][key] = value
            with self.subTest(key=key), self.assertRaises(subprocess.CalledProcessError):
                self.hashes(candidate)

    def test_missing_shared_or_unknown_null_ssh_fields_fail_actual_nix_assertion(self):
        for operation in ["missing", "unknown"]:
            candidate = copy.deepcopy(self.candidate)
            settings = candidate["services"]["openssh"]["settings"]
            if operation == "missing":
                settings.pop("Ciphers")
            else:
                settings["UnreviewedNull"] = None
            with self.subTest(operation=operation), self.assertRaises(subprocess.CalledProcessError):
                self.hashes(candidate)

    def test_actual_nix_rejects_other_sudo_prefix(self):
        for prefix in ["Defaults env_keep+=NIXOS_NO_CHECK\n", "Defaults env_keep+=NIXOS_NO_CHECK\n\n\n", "Defaults env_keep+=ANOTHER\n\n"]:
            candidate = copy.deepcopy(self.candidate)
            candidate["security"]["sudo"]["extraConfig"] = prefix + self.config["security"]["sudo"]["extraConfig"]
            with self.subTest(prefix=prefix):
                try:
                    old, new = self.hashes(candidate)
                except subprocess.CalledProcessError:
                    continue
                self.assertNotEqual(old["normalized_sha256"], new["normalized_sha256"])

    def test_other_sudo_directive_or_rule_change_is_not_normalized(self):
        for operation in ["extra-directive", "rule", "wheel-password", "disable"]:
            candidate = copy.deepcopy(self.candidate)
            sudo = candidate["security"]["sudo"]
            if operation == "extra-directive":
                sudo["extraConfig"] += "Defaults !authenticate\n"
            elif operation == "rule":
                sudo["extraRules"] = [{"users": ["example"], "commands": [{"command": "ALL", "options": ["NOPASSWD"]}]}]
            elif operation == "wheel-password":
                sudo["wheelNeedsPassword"] = False
            else:
                sudo["enable"] = False
            with self.subTest(operation=operation):
                old, new = self.hashes(candidate)
                self.assertNotEqual(old["normalized_sha256"], new["normalized_sha256"])

    def test_shared_ssh_value_is_not_normalized(self):
        candidate = copy.deepcopy(self.candidate)
        candidate["services"]["openssh"]["settings"]["PasswordAuthentication"] = False
        old, new = self.hashes(candidate)
        self.assertNotEqual(old["normalized_sha256"], new["normalized_sha256"])

    def test_users_credentials_nix_polkit_doas_and_state_versions_stay_exact(self):
        mutations = [
            ("system-state", lambda c: c["system"].update(stateVersion="26.05")),
            ("home-state", lambda c: c["home-manager"]["users"]["example"]["home"].update(stateVersion="26.05")),
            ("credential", lambda c: c["users"]["users"]["example"].update(hashedPassword="changed-synthetic")),
            ("groups", lambda c: c["users"]["users"]["example"].update(extraGroups=["wheel", "root"])),
            ("authorized-key", lambda c: c["users"]["users"]["example"]["openssh"]["authorizedKeys"].update(keys=["synthetic-key"])),
            ("mutable-users", lambda c: c["users"].update(mutableUsers=True)),
            ("nix-trust", lambda c: c["nix"]["settings"].update({"trusted-users": ["root", "example"]})),
            ("polkit", lambda c: c["security"]["polkit"].update(extraConfig="synthetic-policy-change")),
            ("doas", lambda c: c["security"]["doas"].update(enable=True)),
        ]
        for name, change in mutations:
            candidate = copy.deepcopy(self.candidate)
            change(candidate)
            with self.subTest(name=name):
                old, new = self.hashes(candidate)
                self.assertNotEqual(old["normalized_sha256"], new["normalized_sha256"])

    def test_vault_socket_scope_and_native_agent_environment_stay_exact(self):
        for field in ["socket", "share", "environment"]:
            candidate = copy.deepcopy(self.candidate)
            service = candidate["home-manager"]["users"]["example"]["systemd"]["user"]["services"]["weasel-proton-infra-ssh-agent"]["Service"]
            if field == "environment":
                service["Environment"] = ["DBUS_SESSION_BUS_ADDRESS=changed-fixture"]
            else:
                service["ExecStart"][0] += " --changed-" + field
            with self.subTest(field=field):
                old, new = self.hashes(candidate)
                self.assertNotEqual(old["normalized_sha256"], new["normalized_sha256"])

    def test_root_service_command_and_environment_change_fail_original_assertion(self):
        for field in ["command", "environment"]:
            candidate = copy.deepcopy(self.candidate)
            service = candidate["systemd"]["services"]["weasel-update-activate"]
            if field == "command":
                service["serviceConfig"]["ExecStart"] = "/mutable/command"
            else:
                service["environment"] = {"NIXOS_NO_CHECK": "1"}
            with self.subTest(field=field), self.assertRaises(subprocess.CalledProcessError):
                self.hashes(candidate)

    def test_normal_batch_has_no_migration_exception(self):
        self.write(self.candidate)
        with patch.object(batch, "codex_ownership_predicate", return_value="true"):
            with self.assertRaisesRegex(batch.BatchError, "authentication"):
                batch.verify_sensitive_state(self.before, self.after, self.fixture.evaluate)



class PackageOutputIdentityTests(unittest.TestCase):
    """Production identity projection in a private store, no package build."""
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix="migration-package-identity-test-")
        self.addCleanup(self.temporary.cleanup)
        self.store=Path(self.temporary.name)/"store"

    def evaluate(self,change):
        library="/nix/store/zg0fzjv0hmm0g6jdbqcf6b81b4hrqi5p-source/lib"
        if not Path(library).is_dir():self.skipTest("Verified public Nix library unavailable")
        expression=('let cleanPkgs.lib = import '+json.dumps(library)+'; '
                    'output = { type="derivation"; drvPath="/public/exact.drv"; outPath="/public/exact-out"; '
                    'outputName="out"; outputSpecified=true; }; '
                    'trusted = output // { outputSpecified=false; outputs=["out"]; out=output; }; '
                    'actual = '+change+'; '+migration.package_identity_fragment()+
                    'in builtins.toJSON { ok=samePackage actual trusted; sameDrv=actual.drvPath==trusted.drvPath; }')
        result=subprocess.run([shutil.which("nix"),"eval","--store",str(self.store),"--offline","--raw","--impure",
                               "--no-write-lock-file","--option","allow-import-from-derivation","false","--expr",expression],
                              capture_output=True,check=True,timeout=15)
        return json.loads(result.stdout)

    def test_exact_plain_output_identity_passes(self):
        self.assertEqual(self.evaluate("trusted"),{"ok":True,"sameDrv":True})

    def reject_same_drv(self,change):
        value=self.evaluate(change)
        self.assertTrue(value["sameDrv"],"Fixture must retain the legitimate drvPath")
        self.assertFalse(value["ok"])

    def test_default_out_path_spoof_with_original_drv_and_out_fails(self):
        self.reject_same_drv('trusted // { outPath="/public/fake-out"; }')

    def test_default_to_string_spoof_with_original_drv_out_path_and_out_fails(self):
        self.reject_same_drv('trusted // { __toString = self: "/public/fake-coercion"; }')

    def test_selected_out_spoof_with_original_default_and_drv_fails(self):
        self.reject_same_drv('trusted // { out=output // { outPath="/public/fake-out"; }; }')

    def test_selected_out_to_string_spoof_with_original_paths_fails(self):
        self.reject_same_drv('trusted // { out=output // { __toString=self: "/public/fake-out-coercion"; }; }')

    def test_get_bin_or_get_lib_shadow_output_fails(self):
        for name in ["bin","lib","dev","man","doc","info","debug"]:
            with self.subTest(name=name):
                self.reject_same_drv('trusted // { '+name+'=output // { outPath="/public/shadow"; }; }')

    def test_changed_output_name_or_specified_semantics_fails(self):
        for change in ['trusted // { outputName="bin"; }','trusted // { outputSpecified=true; }',
                       'trusted // { out=output // { outputSpecified=false; }; }']:
            with self.subTest(change=change):self.reject_same_drv(change)

    def test_declared_output_inventory_or_selected_drv_change_fails(self):
        for change in ['trusted // { outputs=["out" "bin"]; bin=output; }',
                       'trusted // { out=output // { drvPath="/public/alternate.drv"; }; }']:
            with self.subTest(change=change):self.reject_same_drv(change)

    def test_every_sensitive_selected_package_and_etc_uses_identity(self):
        fragment=migration.ssh_binding_fragment("old")
        for text in ['samePackage sshCfg.package cleanPkgs.openssh','samePackage c.systemd.package cleanPkgs.systemd',
                     'samePackage host.pkgs.xauth cleanPkgs.xauth','samePackage c.system.build.etc trustedEtc.system.build.etc']:
            self.assertIn(text,fragment)
        self.assertEqual(fragment.count('systemd.package = cleanPkgs.systemd;'),2)
        self.assertNotIn('systemd.package = c.systemd.package;',fragment)


class EffectiveEtcTargetTests(unittest.TestCase):
    """Actual pure Nix target logic in a private evaluation store.

    The trusted etc entries below are explicit synthetic public fixtures. This
    exercises the production target fragment without any host/package module
    evaluation, daemon access, compiler, credential or SSH process.
    """
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix="migration-etc-target-test-")
        self.addCleanup(self.temporary.cleanup)
        self.store=Path(self.temporary.name)/"store"
        self.trusted={}
        for name in ["ssh/sshd_config","ssh/moduli","ssh/ssh_config","ssh/ssh_known_hosts",
                     "ssh/authorized_keys.d/example","ssh/authorized_principals.d/example",
                     "systemd/system","systemd/system.conf","pam.d/sshd"]:
            self.trusted[name]={"enable":True,"target":name,"mode":"symlink","uid":0,"gid":0,
                                "user":"+0","group":"+0","source":"/public/fixture-"+name.replace("/","-")}
        self.trusted["ssh/authorized_keys.d/example"]["mode"]="0444"
        self.trusted["ssh/authorized_principals.d/example"]["mode"]="0444"
        self.public_key_file=Path(self.temporary.name)/"fixture-public-authorized-key"
        self.public_key_file.write_text("ssh-ed25519 synthetic-public-fixture-one\n");self.public_key_file.chmod(0o600)
        self.users={"example":{"name":"example","openssh":{"authorizedKeys":{"keys":["synthetic-public-inline-key"],
                    "keyFiles":[str(self.public_key_file)]},"authorizedPrincipals":["synthetic-principal"]}}}

    def evaluate(self,entries):
        library="/nix/store/zg0fzjv0hmm0g6jdbqcf6b81b4hrqi5p-source/lib"
        if not Path(library).is_dir():self.skipTest("Verified public Nix library unavailable")
        expression=('let cleanPkgs.lib = import '+json.dumps(library)+'; '
                    'select = names: attrs: builtins.intersectAttrs (builtins.listToAttrs '
                    '(map (name: { inherit name; value = null; }) names)) attrs; '
                    'c.environment.etc = builtins.fromJSON '+json.dumps(json.dumps(entries))+'; '
                    'c.users.users = builtins.fromJSON '+json.dumps(json.dumps(self.users))+'; '
                    'trustedEtcEntries = builtins.fromJSON '+json.dumps(json.dumps(self.trusted))+'; '+
                    migration.etc_binding_fragment()+
                    'in builtins.toJSON { ok = etcTargetsOkay; '
                    'auth_sha256 = builtins.hashString "sha256" (builtins.toJSON sshAuthRoles); '
                    'auth_data_sha256 = sshAuthDataSha256; }')
        result=subprocess.run([shutil.which("nix"),"eval","--store",str(self.store),"--offline","--raw","--impure",
                               "--no-write-lock-file","--option","allow-import-from-derivation","false","--expr",expression],
                              capture_output=True,check=True,timeout=15)
        return json.loads(result.stdout)

    def test_exact_effective_entries_pass_with_opaque_authorization_hash(self):
        result=self.evaluate(self.trusted);self.assertTrue(result["ok"])
        self.assertRegex(result["auth_sha256"],"^[0-9a-f]{64}$")
        self.assertNotIn("/public",json.dumps(result))

    def test_named_config_redirect_and_differently_named_effective_shadow_fail(self):
        entries=copy.deepcopy(self.trusted)
        entries["ssh/sshd_config"]["target"]="ssh/reviewed-unused"
        entries["renamed-unreviewed"]={**self.trusted["ssh/sshd_config"],"source":"/public/fake-config"}
        self.assertFalse(self.evaluate(entries)["ok"])

    def test_every_reviewed_entry_target_enable_mode_or_ownership_change_fails(self):
        for name in self.trusted:
            for key,value in [("target","other/unused"),("enable",False),("mode","0777"),
                              ("uid",1000),("gid",1000),("user","example"),("group","example")]:
                entries=copy.deepcopy(self.trusted);entries[name][key]=value
                with self.subTest(name=name,key=key):self.assertFalse(self.evaluate(entries)["ok"])

    def test_other_entry_cannot_shadow_config_manager_unit_or_authorization_paths(self):
        for target in ["ssh/sshd_config","ssh/authorized_keys.d/example","ssh/authorized_principals.d/example",
                       "systemd/system","systemd/system/sshd.service","systemd/system/sshd.service.d/override.conf",
                       "systemd/system.conf","systemd/system.conf.d/override.conf","pam.d/sshd"]:
            entries=copy.deepcopy(self.trusted)
            entries["differently-named"]={**self.trusted["ssh/sshd_config"],"target":target,"source":"/public/fake"}
            with self.subTest(target=target):self.assertFalse(self.evaluate(entries)["ok"])

    def test_protected_parent_directory_cannot_redirect_children(self):
        for target in ["ssh","systemd","pam.d"]:
            entries=copy.deepcopy(self.trusted)
            entries["parent-alias"]={**self.trusted["ssh/sshd_config"],"target":target,"source":"/public/foreign-directory"}
            with self.subTest(target=target):self.assertFalse(self.evaluate(entries)["ok"])

    def test_noncanonical_targets_fail_before_installation(self):
        for target in ["/ssh/sshd_config","ssh//sshd_config","./ssh/sshd_config","else/../ssh/sshd_config",
                       "ssh/./sshd_config","ssh/sshd_config/","", "../unrelated"]:
            entries=copy.deepcopy(self.trusted)
            entries["alias"]={**self.trusted["ssh/sshd_config"],"target":target}
            with self.subTest(target=target):self.assertFalse(self.evaluate(entries)["ok"])

    def test_duplicate_unrelated_enabled_targets_fail_too(self):
        entries=copy.deepcopy(self.trusted)
        for name in ["one","two"]:entries[name]={**self.trusted["ssh/sshd_config"],"target":"unrelated/config"}
        self.assertFalse(self.evaluate(entries)["ok"])

    def test_copy_metadata_sidecars_cannot_shadow_protected_outputs(self):
        for target in ["ssh/authorized_keys.d/example.mode","ssh/authorized_keys.d/example.uid",
                       "systemd/system.mode","systemd/system.uid","systemd/system.gid",
                       "systemd/system.conf.mode","pam.d/sshd.mode"]:
            entries=copy.deepcopy(self.trusted)
            entries["metadata-shadow"]={**self.trusted["ssh/sshd_config"],"target":target}
            with self.subTest(target=target):self.assertFalse(self.evaluate(entries)["ok"])

    def test_unrelated_canonical_entry_remains_possible(self):
        entries=copy.deepcopy(self.trusted)
        entries["unrelated"]={**self.trusted["ssh/sshd_config"],"target":"unrelated/canonical-config"}
        self.assertTrue(self.evaluate(entries)["ok"])

    def test_effective_authorization_source_change_fails_even_if_role_hash_is_same(self):
        original=self.evaluate(self.trusted)
        for name in ["ssh/authorized_keys.d/example","ssh/authorized_principals.d/example"]:
            entries=copy.deepcopy(self.trusted);entries[name]["source"]="/public/changed-auth-fixture"
            with self.subTest(name=name):
                changed=self.evaluate(entries);self.assertFalse(changed["ok"])
                self.assertEqual(changed["auth_sha256"],original["auth_sha256"])

    def test_independently_matched_changed_generated_auth_paths_preserve_explicit_roles(self):
        original=self.evaluate(self.trusted)
        for name in ["ssh/authorized_keys.d/example","ssh/authorized_principals.d/example"]:
            self.trusted[name]["source"]="/public/new-stdenv-output-"+name.replace("/","-")
        candidate=self.evaluate(self.trusted)
        self.assertTrue(candidate["ok"])
        self.assertEqual(candidate["auth_sha256"],original["auth_sha256"])

    def test_authorized_public_file_contents_are_hashed_opaquely(self):
        original=self.evaluate(self.trusted)
        self.public_key_file.write_text("ssh-ed25519 synthetic-public-fixture-two\n")
        changed=self.evaluate(self.trusted)
        self.assertNotEqual(changed["auth_data_sha256"],original["auth_data_sha256"])
        self.assertNotIn("synthetic",json.dumps(changed))
        self.assertNotIn(str(self.public_key_file),json.dumps(changed))

    def test_declared_key_principal_or_username_payload_changes_hash(self):
        original=self.evaluate(self.trusted);before=copy.deepcopy(self.users)
        for field in ["key","principal","username"]:
            self.users=copy.deepcopy(before)
            if field=="key":self.users["example"]["openssh"]["authorizedKeys"]["keys"]=["changed-public-inline-fixture"]
            elif field=="principal":self.users["example"]["openssh"]["authorizedPrincipals"]=["changed-principal-fixture"]
            else:self.users["example"]["name"]="different-example"
            with self.subTest(field=field):self.assertNotEqual(self.evaluate(self.trusted)["auth_data_sha256"],original["auth_data_sha256"])
        self.users=before

    def test_manager_and_sshd_pam_direct_sources_cannot_be_replaced(self):
        for name in ["systemd/system.conf","pam.d/sshd"]:
            entries=copy.deepcopy(self.trusted);entries[name]["source"]="/public/unreviewed-policy"
            with self.subTest(name=name):self.assertFalse(self.evaluate(entries)["ok"])


class GeneratedSSHClientExtraTests(unittest.TestCase):
    """Force the exact production client predicate/projection using pure Nix."""
    def setUp(self):
        self.library="/nix/store/zg0fzjv0hmm0g6jdbqcf6b81b4hrqi5p-source/lib"
        if not Path(self.library).is_dir():self.skipTest("Verified immutable public Nix library unavailable")
        self.private=tempfile.TemporaryDirectory(prefix="client-extra-policy-test-",dir="/tmp")
        self.addCleanup(self.private.cleanup)
        self.paths={"libvirt":"/nix/store/"+"a"*32+"-libvirt", "gnupg":"/nix/store/"+"b"*32+"-gnupg",
                    "bash":"/nix/store/"+"c"*32+"-bash"}

    def stanzas(self,paths=None):
        paths=paths or self.paths
        return (migration.CLIENT_LIBVIRT_EXTRA.replace("${cleanPkgs.libvirt}",paths["libvirt"]),
                migration.CLIENT_GPG_EXTRA.replace("${cleanPkgs.gnupg}",paths["gnupg"]).replace("${cleanPkgs.runtimeShell}",paths["bash"]+"/bin/sh"))

    def evaluate(self,text,*,paths=None,libvirt=True,gpg=True,libvirt_expr="cleanPkgs.libvirt",
                 gnupg_expr="cleanPkgs.gnupg",bash_expr="cleanPkgs.bash",shell_expr="cleanPkgs.runtimeShell",unused_throw=False,contextual=False,legacy_count=False):
        paths=paths or self.paths
        # appendContext validates registered output derivations even in a pure fixture.
        # Use immutable existing context anchors; package identities remain synthetic.
        context_drvs={"libvirt":"/nix/store/y0hg4lm0nnh1ag4w26qk6ml1w0kl9va9-libvirt-11.7.0.drv",
                      "gnupg":"/nix/store/b6xmy0klfqqvw6bqd25zsz101qpcbjqi-gnupg-2.4.8.drv",
                      "bash":"/nix/store/v9limmbzkvjcdrfijq5ig760yg5gvza6-bash-5.3p3.drv"}
        if contextual and not all(Path(x).is_file() for x in context_drvs.values()):
            self.skipTest("Existing registered context anchors unavailable; no derivation may be built by this fixture")
        context_drv=lambda key:context_drvs[key] if contextual else paths[key]+".drv"
        value=lambda x:json.dumps(x).replace("${",r"\${")
        pkgs=("{ lib = import "+value(self.library)+"; libvirt = "+("throw \"unused libvirt\"" if unused_throw else "package "+value(paths["libvirt"]))+
              "; gnupg = "+("throw \"unused gnupg\"" if unused_throw else "package "+value(paths["gnupg"]))+
              "; bash = "+("throw \"unused bash\"" if unused_throw else "package "+value(paths["bash"]))+
              "; runtimeShell = "+("throw \"unused shell\"" if unused_throw else "contextualize "+value(context_drv("bash"))+" "+value(paths["bash"]+"/bin/sh"))+"; }")
        fragment=migration.client_extra_fragment()
        if legacy_count:
            fixed='clientCount = stanza: builtins.div (builtins.stringLength sshClient.extraConfig - builtins.stringLength (builtins.replaceStrings [stanza] [""] sshClient.extraConfig)) (builtins.stringLength stanza); '
            assert fragment.count(fixed)==1
            fragment=fragment.replace(fixed,'clientCount = stanza: builtins.length (cleanPkgs.lib.splitString stanza sshClient.extraConfig) - 1; ')
        proof=(' context_proof = { libvirt=builtins.hasAttr '+value(context_drv('libvirt'))+' (builtins.getContext clientLibvirtStanza); '
               'gnupg=builtins.hasAttr '+value(context_drv('gnupg'))+' (builtins.getContext clientGpgStanza); '
               'shell=builtins.hasAttr '+value(context_drv('bash'))+' (builtins.getContext clientGpgStanza); }; '
               'counts = { libvirt=clientCount clientLibvirtStanza; gnupg=clientCount clientGpgStanza; }; ' if contextual else '')
        context_map='if path == '+value(paths['libvirt'])+' then '+value(context_drv('libvirt'))+' else if path == '+value(paths['gnupg'])+' then '+value(context_drv('gnupg'))+' else '+value(context_drv('bash'))
        expr=('let contextDrv = path: '+context_map+'; contextualize = drv: path: if '+value(contextual)+' then builtins.appendContext path { ${drv} = { outputs=["out"]; }; } else path; '
              'output = path: { type="derivation"; drvPath=contextDrv path; outPath=contextualize (contextDrv path) path; outputName="out"; outputSpecified=false; }; '
              'package = path: let out=output path; in out // { outputs=["out"]; inherit out; }; '
              'cleanPkgs = '+pkgs+'; host.pkgs = cleanPkgs // { bash='+bash_expr+'; runtimeShell='+shell_expr+'; }; '
              'c.virtualisation.libvirtd = { enable='+value(libvirt)+'; sshProxy=true; package='+libvirt_expr+'; }; '
              'c.programs.gnupg = { agent={ enable='+value(gpg)+'; enableSSHSupport=true; }; package='+gnupg_expr+'; }; '
              'sshClient.extraConfig = '+value(text)+'; '+migration.package_identity_fragment()+fragment+
              'in assert clientBindingsOkay; builtins.toJSON { flags=clientFlags; text=clientNormalized; '+proof+' }')
        store_args=(["--extra-experimental-features","read-only-local-store","--store","local?read-only=true"] if contextual
                    else ["--store",str(Path(self.private.name)/"store")])
        args=[shutil.which("nix"),"eval",*store_args,"--raw","--impure","--offline",
              "--no-write-lock-file","--max-jobs","0","--builders","","--option","substitute","false",
              "--option","allow-import-from-derivation","false","--expr",expr]
        result=subprocess.run(args,capture_output=True,check=True,timeout=15)
        return json.loads(result.stdout)

    def test_real_output_context_patterns_count_and_normalize_for_laptop_and_desktop(self):
        a,g=self.stanzas();auth="Host *\n  AddKeysToAgent yes\n"
        for gpg in [False,True]:
            with self.subTest(gpg=gpg):
                text=auth+a+(g if gpg else "")
                contextual=self.evaluate(text,gpg=gpg,contextual=True)
                self.assertEqual(contextual.pop("context_proof"),{"libvirt":True,"gnupg":True,"shell":True})
                self.assertEqual(contextual.pop("counts"),{"libvirt":1,"gnupg":int(gpg)})
                self.assertEqual(contextual,self.evaluate(text,gpg=gpg))

    def test_previous_regex_count_rejects_real_libvirt_and_gpg_output_context(self):
        a,g=self.stanzas()
        for text,libvirt,gpg in [(a,True,False),(g,False,True)]:
            with self.subTest(libvirt=libvirt),self.assertRaises(subprocess.CalledProcessError) as caught:
                self.evaluate(text,libvirt=libvirt,gpg=gpg,contextual=True,legacy_count=True)
            self.assertIn(b"not allowed to refer to a store path",caught.exception.stderr)

    def test_contextual_patterns_still_reject_duplicates_and_modified_arguments(self):
        a,g=self.stanzas()
        for text in [a+a+g,a+g+g,(a+g).replace("proxy.conf\n","proxy.conf extra\n"),
                     (a+g).replace("--quiet updatestartuptty","--quiet arbitrary-command")]:
            with self.subTest(length=len(text)),self.assertRaises(subprocess.CalledProcessError):
                self.evaluate(text,contextual=True)

    def test_laptop_libvirt_and_desktop_gpg_canonical_paths_project_equal(self):
        changed={k:v.replace(v[11:43],"d"*32) for k,v in self.paths.items()}
        for gpg in [False,True]:
            a=self.stanzas();z=self.stanzas(changed);author="Host *\n  AddKeysToAgent yes\n\n"
            self.assertEqual(self.evaluate(author+a[0]+(a[1] if gpg else ""),gpg=gpg),
                             self.evaluate(author+z[0]+(z[1] if gpg else ""),paths=changed,gpg=gpg))

    def test_modified_include_or_match_target_arguments_and_predicate_reject(self):
        a,g=self.stanzas();text=a+g
        mutations=[text.replace("30-libvirt-ssh-proxy.conf","evil.conf"),text.replace("proxy.conf\n","proxy.conf extra\n"),
                   text.replace("Match host * exec","Match host root exec"),text.replace("--quiet updatestartuptty","--quiet arbitrary-command"),
                   text.replace("/bin/sh -c","/bin/sh -e -c"),text.replace("gpg-connect-agent","gpgconf")]
        for changed in mutations:
            with self.subTest(changed=changed[-50:]),self.assertRaises(subprocess.CalledProcessError):self.evaluate(changed)

    def test_duplicate_generated_stanzas_reject(self):
        a,g=self.stanzas()
        for text in [a+a+g,a+g+g]:
            with self.subTest(length=len(text)),self.assertRaises(subprocess.CalledProcessError):self.evaluate(text)

    def test_stanza_and_authored_remainder_order_changes_remain_different(self):
        a,g=self.stanzas();auth="Host *\n  AddKeysToAgent yes\n"
        before=self.evaluate(auth+a+g)
        self.assertNotEqual(before,self.evaluate(auth+g+a))
        self.assertNotEqual(before,self.evaluate(a+auth+g))

    def test_authored_arguments_and_unrelated_store_paths_are_not_normalized(self):
        a,g=self.stanzas();auth="Host *\n  AddKeysToAgent yes\nInclude /nix/store/"+"e"*32+"-unrelated/custom.conf\n"
        before=self.evaluate(auth+a+g)
        self.assertIn("-unrelated/custom.conf",before["text"])
        for changed in [auth.replace("AddKeysToAgent yes","AddKeysToAgent no"),auth.replace("e"*32,"f"*32)]:
            with self.subTest(changed=changed[-40:]):self.assertNotEqual(before,self.evaluate(changed+a+g))

    def test_disabled_copied_stanza_is_retained_raw_and_switches_differ(self):
        a,g=self.stanzas()
        disabled=self.evaluate(a+g,libvirt=False,gpg=False)
        self.assertEqual(disabled["text"],a+g)
        self.assertNotEqual(disabled,self.evaluate(a+g))
        self.assertNotEqual(disabled,self.evaluate("",libvirt=False,gpg=False))

    def test_unused_off_host_packages_are_not_forced(self):
        self.assertEqual(self.evaluate("Host *\n  AddKeysToAgent yes\n",libvirt=False,gpg=False,unused_throw=True)["text"],
                         "Host *\n  AddKeysToAgent yes\n")

    def test_role_marker_impersonation_is_rejected(self):
        for text in ["<exact-canonical-libvirt-client-include>\n","<exact-canonical-gnupg-client-match>\n"]:
            with self.subTest(text=text),self.assertRaises(subprocess.CalledProcessError):self.evaluate(text,libvirt=False,gpg=False)

    def test_same_drvpath_spoofed_output_coercion_and_selected_outputs_reject(self):
        a,g=self.stanzas()
        mutations=['cleanPkgs.P // { outPath="/mutable/output"; }',
                   'cleanPkgs.P // { __toString = _: "/mutable/coercion"; }',
                   'cleanPkgs.P // { out=cleanPkgs.P.out // { outPath="/mutable/selected"; }; }']
        for field,arg in [("libvirt","libvirt_expr"),("gnupg","gnupg_expr"),("bash","bash_expr")]:
            for changed in mutations:
                with self.subTest(field=field,changed=changed),self.assertRaises(subprocess.CalledProcessError):
                    self.evaluate(a+g,**{arg:changed.replace(".P","."+field)})

    def test_runtime_shell_value_and_coercion_are_bound(self):
        a,g=self.stanzas()
        for changed in ['"/mutable/sh"','{ __toString = _: cleanPkgs.runtimeShell; }']:
            with self.subTest(changed=changed),self.assertRaises(subprocess.CalledProcessError):self.evaluate(a+g,shell_expr=changed)

    def test_switch_types_are_strict(self):
        a,g=self.stanzas()
        with self.assertRaises(subprocess.CalledProcessError):self.evaluate(a+g,libvirt="true")


class CanonicalReleaseServiceDefaultTests(unittest.TestCase):
    """Pure fixture of exact production predicates/projection, no daemon/build.

    Only independent eval-config composition is replaced with a documented
    canonical synthetic config. Real locked composition is a fresh host gate.
    """
    def setUp(self):
        self.private=tempfile.TemporaryDirectory(prefix="release-default-fixture-",dir="/tmp")
        self.addCleanup(self.private.cleanup)
        self.path=Path(self.private.name)
        self.paths={name:str(self.path/name) for name in ["fwupd","wpa","shadow"]}
        for root in [self.path/"fwupd/lib/systemd/system",self.path/"selected"]:root.mkdir(parents=True)
        self.vendor="[Service]\nUser=fwupd-refresh\nDynamicUser=no\nExecStart=/canonical/fwupdmgr refresh\n"
        (self.path/"fwupd/lib/systemd/system/fwupd-refresh.service").write_text(self.vendor)
        (self.path/"selected/fwupd-refresh.service").write_text(self.vendor)
        self.user={"uid":None,"isNormalUser":False,"isSystemUser":True,"home":"/var/empty","homeMode":"700",
                   "createHome":False,"extraGroups":[],"hashedPassword":None,"hashedPasswordFile":None,"password":None,
                   "initialPassword":None,"initialHashedPassword":None,"openssh":{"authorizedKeys":{"keys":[],"keyFiles":[]},"authorizedPrincipals":[]},
                   "shell":"canonical-shadow"}
        self.author="// Authored policy must remain exact\npolkit.addRule(function(a, s) { return polkit.Result.NOT_HANDLED; });\n"

    def config(self,side,*,wireless=True,fwupd=True,locales=None,nm=False):
        locales=["C.UTF-8/UTF-8","en_US.UTF-8/UTF-8"] if locales is None else locales
        archive=str(self.path/"locales")+"-"+hashlib.sha256(json.dumps(locales).encode()).hexdigest()[:12]
        if nm:wireless=side=="new"
        users={"root":{"password":"synthetic-existing-root","openssh":{}}}
        groups={"wheel":{"gid":1,"members":["synthetic-human"]}}
        units={}
        if fwupd:
            users["fwupd-refresh"]=self.user|{"name":"fwupd-refresh","group":"fwupd-refresh"};groups["fwupd-refresh"]={"name":"fwupd-refresh","gid":None,"members":[]}
            units["fwupd-refresh.service"]={"enable":True,"text":"[Service]\nStateDirectory=fwupd\n",
                                             "unit":str(self.path/"canonical-refresh-unit")}
        if wireless:
            units["wpa_supplicant.service"]={"enable":True,"text":"[Service]\nExecStart=/canonical/wpa\n",
                                            "unit":str(self.path/"canonical-wpa-unit")}
            if side=="new":
                users["wpa_supplicant"]=self.user|{"name":"wpa_supplicant","group":"wpa_supplicant"};groups["wpa_supplicant"]={"name":"wpa_supplicant","gid":None,"members":[]}
        return {"users":{"users":users,"groups":groups},"networking":{"wireless":{"enable":wireless,"dbusControlled":True,
                **({"autoDetectInterfaces":not nm} if side=="new" else {}),"interfaces":[],"userControlled":{"enable":False,"group":"wheel"} if side=="old" else False,
                "enableHardening":True,"networks":{},"secretsFile":None,"extraConfigFiles":[],"extraConfig":""},
                "networkmanager":{"enable":nm,"unmanaged":[],"wifi":{"backend":"wpa_supplicant"}}},
                "i18n":{"supportedLocales":locales,"glibcLocales":"canonical-locales"},
                "services":{"fwupd":{"enable":fwupd}},"systemd":{"units":units,"globalEnvironment":{"LOCALE_ARCHIVE":archive+"/lib/locale/locale-archive","TZDIR":"/canonical/tzdata/share/zoneinfo"}},
                "environment":{"etc":{"systemd/system":{"source":str(self.path/"selected")}}}}

    def evaluate(self,side,*,change=None,text=None,wireless=True,fwupd=True,fwupd_expr="cleanPkgs.fwupd",wpa_expr="cleanPkgs.wpa_supplicant",shell_expr=None,locales=None,archive_expr=None,nm=False,nm_expr="cleanPkgs.networkmanager"):
        canonical=self.config(side,wireless=wireless,fwupd=fwupd,locales=locales,nm=nm);actual=copy.deepcopy(canonical)
        if change:change(actual)
        text=self.author+("\n"+migration.FWUPD_REFRESH_RULE if side=="new" and fwupd else "") if text is None else text
        fragment=migration.release_defaults_fragment(side)
        start=fragment.index("trustedReleaseDefaults = ");stop=fragment.index("releaseUser = ",start)
        fragment=fragment[:start]+"trustedReleaseDefaults = canonical; "+fragment[stop:]
        j=lambda x:json.dumps(x).replace("${",r"\${")
        archive=canonical['systemd']['globalEnvironment']['LOCALE_ARCHIVE'].removesuffix('/lib/locale/locale-archive')
        archive_expr='localePackage' if archive_expr is None else archive_expr
        shell=('if user.shell == "canonical-shadow" then cleanPkgs.shadow else user.shell' if shell_expr is None else shell_expr)
        expr=('let output = path: { type="derivation"; drvPath=path+".drv"; outPath=path; outputName="out"; outputSpecified=false; }; '
              'package = path: let out=output path; in out // { outputs=["out"]; inherit out; }; '
              'cleanPkgs = { lib = import "/nix/store/zg0fzjv0hmm0g6jdbqcf6b81b4hrqi5p-source/lib"; '
              'networkmanager=package '+j(str(self.path/'networkmanager'))+'; fwupd=package '+j(self.paths['fwupd'])+'; wpa_supplicant=package '+j(self.paths['wpa'])+'; shadow=(package '+j(self.paths['shadow'])+') // { shellPath="/bin/nologin"; }; }; '
              'localePackage=package '+j(archive)+'; '
              'raw = builtins.fromJSON '+j(json.dumps(actual))+'; canonicalRaw = builtins.fromJSON '+j(json.dumps(canonical))+'; '
              'convert = value: value // { i18n = value.i18n // { glibcLocales=localePackage; }; users = value.users // { users = builtins.mapAttrs (_: user: user // { '
              'shell = if user.shell or "" == "canonical-shadow" then cleanPkgs.shadow else user.shell or null; }) value.users.users; }; }; '
              'canonical = convert canonicalRaw; c = (convert raw) // { networking=raw.networking // { networkmanager=raw.networking.networkmanager // { package='+nm_expr+'; }; }; i18n=raw.i18n // { glibcLocales='+archive_expr+'; }; services = raw.services // { fwupd = raw.services.fwupd // { package='+fwupd_expr+'; }; }; '
              'systemd = raw.systemd // { packages = [cleanPkgs.fwupd]; }; '
              'users = raw.users // { users = builtins.mapAttrs (_: user: user // { shell='+shell+'; }) raw.users.users; }; }; '
              'host.pkgs.wpa_supplicant='+wpa_expr+'; '
              'select = names: value: builtins.intersectAttrs (builtins.listToAttrs (map (name: { inherit name; value=null; }) names)) value; '
              'hash = value: builtins.hashString "sha256" (builtins.toJSON value); '
              'v = { users = builtins.mapAttrs (_: user: select ["uid" "isNormalUser" "isSystemUser" "home" "group" "extraGroups" '
              '"hashedPassword" "hashedPasswordFile" "password" "initialPassword" "initialHashedPassword" "openssh"] user) c.users.users; '
              'polkit.extraConfig='+j(text)+'; }; '
              'sshAuthData = builtins.mapAttrs (_: user: user.openssh) c.users.users; '
              'sshPolicy = { auth_data_sha256=hash sshAuthData; unchanged="retained"; }; '
              +migration.package_identity_fragment()+fragment+
              'in assert releaseDefaultsOkay; builtins.toJSON { users=releaseUsersNormalized; polkit=releasePolkitNormalized; '
              'auth=releaseSSHPolicy; flags=releaseFlags; groups=releaseGroupsNormalized; }')
        r=subprocess.run([shutil.which('nix'),'eval','--store',str(self.path/'store'),'--raw','--impure','--offline','--no-write-lock-file',
             '--max-jobs','0','--builders','','--option','substitute','false','--option','allow-import-from-derivation','false','--expr',expr],
             check=True,capture_output=True,timeout=15)
        return json.loads(r.stdout)

    def test_exact_two_official_deltas_project_equal_and_off_hosts_remain_lazy(self):
        for wireless,fwupd in [(True,True),(True,False),(False,True),(False,False)]:
            with self.subTest(wireless=wireless,fwupd=fwupd):
                self.assertEqual(self.evaluate('old',wireless=wireless,fwupd=fwupd),self.evaluate('new',wireless=wireless,fwupd=fwupd))

    def test_old_absent_autodetect_and_new_canonical_default_are_accepted(self):
        self.assertNotIn("autoDetectInterfaces",self.config("old")["networking"]["wireless"])
        self.assertIs(self.config("new")["networking"]["wireless"]["autoDetectInterfaces"],True)
        for enabled in [True,False]:
            with self.subTest(enabled=enabled):
                self.assertEqual(self.evaluate("old",wireless=enabled),self.evaluate("new",wireless=enabled))

    def test_new_autodetect_nondefault_and_invalid_types_reject_even_disabled(self):
        for enabled in [True,False]:
            for value in [False,"true",1,None,{}]:
                with self.subTest(enabled=enabled,value=value),self.assertRaises(subprocess.CalledProcessError):
                    self.evaluate("new",wireless=enabled,change=lambda c,v=value:c["networking"]["wireless"].__setitem__("autoDetectInterfaces",v))
            with self.subTest(enabled=enabled,missing=True),self.assertRaises(subprocess.CalledProcessError):
                self.evaluate("new",wireless=enabled,change=lambda c:c["networking"]["wireless"].pop("autoDetectInterfaces"))

    def test_autodetect_not_seeded_or_shared_and_old_projection_never_reads_it(self):
        for side in ["old","new"]:
            fragment=migration.release_defaults_fragment(side)
            flags=fragment[fragment.index("releaseFlags = "):fragment.index("releaseControlOkay = ")]
            seed=fragment[fragment.index("trustedReleaseDefaults = "):fragment.index("releaseUser = ")]
            self.assertNotIn("autoDetectInterfaces",flags)
            self.assertNotIn("autoDetectInterfaces",seed)
        self.assertNotIn("autoDetectInterfaces",migration.release_defaults_fragment("old"))
        new=migration.release_defaults_fragment("new")
        self.assertIn("rw.autoDetectInterfaces == trustedReleaseDefaults.networking.wireless.autoDetectInterfaces",new)

    def test_default_and_german_locale_compositions_project_equal(self):
        for locales in [["C.UTF-8/UTF-8","en_US.UTF-8/UTF-8"],["C.UTF-8/UTF-8","en_US.UTF-8/UTF-8","de_DE.UTF-8/UTF-8"]]:
            with self.subTest(locales=locales):
                self.assertEqual(self.evaluate("old",locales=locales),self.evaluate("new",locales=locales))
        self.assertNotEqual(self.evaluate("old"),self.evaluate("new",locales=["C.UTF-8/UTF-8","en_US.UTF-8/UTF-8","de_DE.UTF-8/UTF-8"]))

    def test_locale_list_shape_and_members_are_strict(self):
        for locales in [None,"all",{},[True],[1],[None],[{}]]:
            with self.subTest(locales=locales),self.assertRaises(subprocess.CalledProcessError):
                self.evaluate("new",change=lambda c,v=locales:c["i18n"].__setitem__("supportedLocales",v))

    def test_locale_archive_identity_and_complete_environment_are_bound(self):
        for archive in ['localePackage // { outPath="/mutable/archive"; }','localePackage // { __toString = _: "/mutable/archive"; }',
                        'localePackage // { out=localePackage.out // { outPath="/mutable/selected"; }; }']:
            with self.subTest(archive=archive),self.assertRaises(subprocess.CalledProcessError):
                self.evaluate("new",archive_expr=archive)
        for name,value in [("LOCALE_ARCHIVE","/mutable/archive"),("TZDIR","/mutable/zoneinfo"),("LD_PRELOAD","/mutable/code")]:
            with self.subTest(name=name),self.assertRaises(subprocess.CalledProcessError):
                self.evaluate("new",change=lambda c,n=name,v=value:c["systemd"]["globalEnvironment"].__setitem__(n,v))
        with self.assertRaises(subprocess.CalledProcessError):
            self.evaluate("new",change=lambda c:c["systemd"]["globalEnvironment"].pop("LOCALE_ARCHIVE"))
        fragment=migration.release_defaults_fragment("new")
        seed=fragment[fragment.index("trustedReleaseDefaults = "):fragment.index("releaseUser = ")]
        self.assertIn("i18n.supportedLocales = releaseFlags.supportedLocales",seed)
        self.assertNotIn("globalEnvironment",seed)
        self.assertNotIn("glibcLocales",seed)

    def test_exact_networkmanager_wpa_coupled_transition_projects_equal(self):
        old=self.config("old",nm=True);new=self.config("new",nm=True)
        self.assertIs(old["networking"]["wireless"]["enable"],False)
        self.assertIs(new["networking"]["wireless"]["enable"],True)
        self.assertIs(new["networking"]["wireless"]["dbusControlled"],True)
        self.assertIs(new["networking"]["wireless"]["autoDetectInterfaces"],False)
        for fwupd in [True,False]:
            with self.subTest(fwupd=fwupd):self.assertEqual(self.evaluate("old",nm=True,fwupd=fwupd),self.evaluate("new",nm=True,fwupd=fwupd))

    def test_coupled_wireless_defaults_and_user_grants_cannot_be_overridden(self):
        for side,name,value in [("old","enable",True),("new","enable",False),("new","dbusControlled",False),("new","autoDetectInterfaces",True),
                                ("new","autoDetectInterfaces",None),("new","userControlled",True),("new","networks",{"injected":{}})]:
            with self.subTest(side=side,name=name),self.assertRaises(subprocess.CalledProcessError):
                self.evaluate(side,nm=True,change=lambda c,n=name,v=value:c["networking"]["wireless"].__setitem__(n,v))
        for field,value in [("password","synthetic-password"),("group","wheel"),("extraGroups",["wheel"])]:
            with self.subTest(field=field),self.assertRaises(subprocess.CalledProcessError):
                self.evaluate("new",nm=True,change=lambda c,n=field,v=value:c["users"]["users"]["wpa_supplicant"].__setitem__(n,v))
        with self.assertRaises(subprocess.CalledProcessError):
            self.evaluate("new",nm=True,change=lambda c:c["systemd"]["units"]["wpa_supplicant.service"].__setitem__("text","[Service]\nUser=root\n"))

    def test_networkmanager_selectors_shape_and_package_identity_are_strict(self):
        for name,value in [("enable","true"),("unmanaged",{}),("unmanaged",[1]),("unmanaged",[None])]:
            with self.subTest(name=name),self.assertRaises(subprocess.CalledProcessError):
                self.evaluate("new",nm=True,change=lambda c,n=name,v=value:c["networking"]["networkmanager"].__setitem__(n,v))
        for backend in [None,True,"unknown",{}]:
            with self.subTest(backend=backend),self.assertRaises(subprocess.CalledProcessError):
                self.evaluate("new",nm=True,change=lambda c,v=backend:c["networking"]["networkmanager"]["wifi"].__setitem__("backend",v))
        for expr in ['cleanPkgs.networkmanager // { outPath="/mutable/output"; }','cleanPkgs.networkmanager // { __toString = _: "/mutable/coercion"; }',
                     'cleanPkgs.networkmanager // { out=cleanPkgs.networkmanager.out // { outPath="/mutable/selected"; }; }']:
            with self.subTest(expr=expr),self.assertRaises(subprocess.CalledProcessError):self.evaluate("new",nm=True,nm_expr=expr)

    def test_changed_selectors_cannot_reuse_coupled_flag_normalization(self):
        for change in [lambda c:c["networking"]["networkmanager"].__setitem__("enable",False),
                       lambda c:c["networking"]["networkmanager"].__setitem__("unmanaged",["interface-name:eth0"]),
                       lambda c:c["networking"]["networkmanager"]["wifi"].__setitem__("backend","iwd")]:
            with self.subTest(change=change):
                self.assertNotEqual(self.evaluate("old",nm=True),self.evaluate("new",nm=True,change=change))
        outside=self.evaluate("old")
        self.assertNotEqual(outside,self.evaluate("new",change=lambda c:c["networking"]["networkmanager"]["wifi"].__setitem__("backend","iwd")))
        for side in ["old","new"]:
            f=migration.release_defaults_fragment(side)
            self.assertIn("networking.networkmanager = lib.mkIf releaseNMMode",f)
            self.assertIn('"enable" "dbusControlled"] trustedReleaseDefaults.networking.wireless',f)

    def test_wpa_password_key_principal_normal_user_uid_and_shell_overrides_reject(self):
        mutations=[('password','synthetic-new-password'),('uid',0),('isNormalUser',True),('isSystemUser',False),('shell','/bin/bash'),
                   ('group','wheel'),('extraGroups',['wheel']),('hashedPasswordFile','/synthetic/credentials'),
                   ('openssh',{'authorizedKeys':{'keys':['synthetic-key'],'keyFiles':[]},'authorizedPrincipals':[]}),
                   ('openssh',{'authorizedKeys':{'keys':[],'keyFiles':[]},'authorizedPrincipals':['synthetic-principal']})]
        for name,value in mutations:
            with self.subTest(name=name),self.assertRaises(subprocess.CalledProcessError):
                self.evaluate('new',change=lambda c,n=name,v=value:c['users']['users']['wpa_supplicant'].__setitem__(n,v))

    def test_group_member_gid_and_static_human_refresh_account_reject(self):
        for account in ['wpa_supplicant','fwupd-refresh']:
            for name,value in [('members',['synthetic-human']),('gid',0)]:
                with self.subTest(account=account,name=name),self.assertRaises(subprocess.CalledProcessError):
                    self.evaluate('new',change=lambda c,a=account,n=name,v=value:c['users']['groups'][a].__setitem__(n,v))
        with self.assertRaises(subprocess.CalledProcessError):
            self.evaluate('new',change=lambda c:c['users']['users']['fwupd-refresh'].__setitem__('isNormalUser',True))

    def test_user_controlled_hardening_or_canonical_dormant_group_changes_reject(self):
        for side,name,value in [('old','userControlled',{'enable':True,'group':'wheel'}),('old','userControlled',{'enable':False,'group':'network'}),
                                ('new','userControlled',True),('new','enableHardening',False)]:
            with self.subTest(side=side,name=name),self.assertRaises(subprocess.CalledProcessError):
                self.evaluate(side,change=lambda c,n=name,v=value:c['networking']['wireless'].__setitem__(n,v))

    def test_shared_flags_and_all_remaining_account_group_policy_stay_exact(self):
        before=self.evaluate('old')
        for change in [lambda c:c['networking']['wireless'].__setitem__('dbusControlled',False),
                       lambda c:c['users']['users']['root'].__setitem__('password','changed'),
                       lambda c:c['users']['groups']['wheel'].__setitem__('members',[]),
                       lambda c:c['users']['users'].__setitem__('unexpected-system-user',{'openssh':{},'shell':'/bin/false'})]:
            with self.subTest(change=change):self.assertNotEqual(before,self.evaluate('new',change=change))

    def test_exact_rule_only_at_tail_once_and_authored_remainder_preserved(self):
        r=migration.FWUPD_REFRESH_RULE;good=self.author+'\n'+r
        bad=[self.author+'\n'+r+r,r+'\n'+self.author,good+'\n',good.replace('get-remotes','install'),
             good.replace('subject.user == "fwupd-refresh"','subject.user == "synthetic-human"'),
             good.replace('polkit.Result.YES','polkit.Result.AUTH_ADMIN'),self.author+'X'+r]
        for text in bad:
            with self.subTest(text=text[-40:]),self.assertRaises(subprocess.CalledProcessError):self.evaluate('new',text=text)
        self.assertNotEqual(self.evaluate('old'),self.evaluate('new',text=good.replace('Authored policy','Changed policy')))

    def test_package_output_spoofs_and_unit_or_vendor_shadow_reject(self):
        for arg in ['fwupd_expr','wpa_expr']:
            for suffix in [' // { outPath="/mutable/output"; }',' // { __toString = _: "/mutable/coercion"; }']:
                with self.subTest(arg=arg),self.assertRaises(subprocess.CalledProcessError):
                    self.evaluate('new',**{arg:('cleanPkgs.fwupd' if arg=='fwupd_expr' else 'cleanPkgs.wpa_supplicant')+suffix})
        for unit in ['wpa_supplicant.service','fwupd-refresh.service']:
            with self.subTest(unit=unit),self.assertRaises(subprocess.CalledProcessError):
                self.evaluate('new',change=lambda c,u=unit:c['systemd']['units'][u].__setitem__('text','[Service]\nUser=root\n'))
        (self.path/'selected/fwupd-refresh.service').write_text(self.vendor.replace('DynamicUser=no','DynamicUser=yes'))
        with self.assertRaises(subprocess.CalledProcessError):self.evaluate('new')

    def test_effective_account_group_names_and_shadow_shell_path_are_bound(self):
        for kind in ["users","groups"]:
            for account in ["wpa_supplicant","fwupd-refresh"]:
                with self.subTest(kind=kind,account=account),self.assertRaises(subprocess.CalledProcessError):
                    self.evaluate("new",change=lambda c,k=kind,a=account:c["users"][k][a].__setitem__("name","synthetic-human-alias"))
        with self.assertRaises(subprocess.CalledProcessError):
            self.evaluate("new",shell_expr='cleanPkgs.shadow // { shellPath="/bin/bash"; }')

    def test_general_factory_and_existing_constants_do_not_gain_release_defaults(self):
        import inspect
        self.assertNotIn('release_defaults_fragment',inspect.getsource(migration.general_expression))
        for side in ['old','new']:
            self.assertEqual(len(migration.TRANSITION[side]['modules']),2)
            self.assertEqual(len(migration.RELEASE_DEFAULT_MODULE_HASHES[side]),4)


class GeneratedNixExpressionSyntaxTests(unittest.TestCase):
    def test_every_policy_expression_parses_without_evaluating_source_or_starting_builds(self):
        parser=shutil.which("nix-instantiate")
        if parser is None:self.skipTest("Nix parser unavailable")
        source=Path("/tmp/synthetic-declared-source-only")
        expressions=[migration.expression(batch.sensitive_expression,source,"nixy-laptop",side)
                     for side in ["old","new"]]
        expressions.append(migration.general_expression(batch.sensitive_expression,source,"nixy-laptop",
                                                        migration.TRANSITION["old"]["locked"],"25.11"))
        for expression in expressions:
            result=subprocess.run([parser,"--parse","--expr",expression],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
            self.assertEqual(result.returncode,0,result.stderr.decode())


class PairedCallsiteTests(unittest.TestCase):
    def test_both_real_callers_bind_a_fixed_executor_not_request_evidence(self):
        for filename, function, method in [("weasel-update-prepare.py", "prepare_batch", "command"),
                                            ("weasel-update-activate.py", "process_request", "user")]:
            tree = ast.parse((ROOT / "scripts" / filename).read_text())
            scope = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == function)
            calls = [n for n in ast.walk(scope) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "verify_sensitive_state"]
            self.assertEqual(len(calls), 1)
            executor = next(v.value for v in calls[0].keywords if v.arg == "migration_executor")
            self.assertIsInstance(executor, ast.Lambda)
            self.assertEqual([v.arg for v in executor.args.args], ["name", "args"])
            self.assertIsInstance(executor.body, ast.Call)
            called = executor.body.func.id if isinstance(executor.body.func, ast.Name) else executor.body.func.attr
            self.assertEqual(called, method)
            evaluator = calls[0].args[2]
            self.assertIsInstance(evaluator, ast.Lambda)
            factory = [n for n in ast.walk(evaluator.body) if isinstance(n, ast.Call)
                       and isinstance(n.func, ast.Attribute) and n.func.attr == "sensitive_eval_args"]
            self.assertEqual(len(factory), 1)
            self.assertEqual(factory[0].func.value.id, "batch")
            self.assertEqual(factory[0].args[0].id, evaluator.args.args[0].arg)


if __name__ == "__main__":
    unittest.main()
