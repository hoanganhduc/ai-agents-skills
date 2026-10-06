"""Force-loop defaults preserve operator authority and publish a coherent group."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

PACK = Path(__file__).resolve().parents[1] / "canonical/runtime/skills/autonomous-research-loop-runtime"
sys.path.insert(0, str(PACK / "force-loop"))
sys.path.insert(0, str(PACK))
import apply_force_loop_defaults as defaults
import force_loop_cli as cli
from load_loop_env import parse_env_text, EnvLoadError
import state_transaction
import load_loop_env as loader


class PolicyDefaultsTests(unittest.TestCase):
    def setUp(self):
        env_patch = mock.patch.dict(os.environ)
        env_patch.start()
        self.addCleanup(env_patch.stop)
        for key in loader.POLICY_KEYS | {loader.WINDOWS_PROJECTION_ENV, loader.WINDOWS_PROJECTION_SOURCE_ENV}:
            os.environ.pop(key, None)

    def projection_snapshot(self):
        keys = loader.POLICY_KEYS | {loader.WINDOWS_PROJECTION_ENV, loader.WINDOWS_PROJECTION_SOURCE_ENV}
        return {key: os.environ[key] for key in keys if key in os.environ}

    def project_policy(self, policy: Path, values: dict[str, str]):
        os.environ.update(values)
        os.environ[loader.WINDOWS_PROJECTION_ENV] = ",".join(sorted(values))
        os.environ[loader.WINDOWS_PROJECTION_SOURCE_ENV] = str(policy)

    def simulate_windows(self):
        # Patch only these modules: changing global os.name breaks pathlib.
        windows_os = SimpleNamespace(**vars(os))
        windows_os.name = "nt"
        for module in (defaults, loader):
            os_patch = mock.patch.object(module, "os", windows_os)
            os_patch.start()
            self.addCleanup(os_patch.stop)

    def test_windows_existing_policy_retains_authority_and_projects_real_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            loop, policy = self.seed(Path(tmp))
            with policy.open("a", encoding="utf-8") as handle:
                handle.write("AAS_FORCE_LOOP_COMPUTE_LANES=kaggle\nAAS_AUTOLOOP_FORMAL_EXECUTION_BACKEND=kaggle-cpu\n")
            values = parse_env_text(policy.read_text(encoding="utf-8"))
            self.project_policy(policy, values)
            self.simulate_windows()
            result = defaults.apply_defaults(loop, policy_file=policy)
            self.assertTrue(result["ok"], result)
            written = parse_env_text(policy.read_text(encoding="utf-8"))
            self.assertEqual(loader.load_env_file(policy), written)
            self.assertEqual(os.environ[loader.WINDOWS_PROJECTION_SOURCE_ENV], str(policy))
            for key, value in values.items():
                self.assertEqual(written[key], value)
            self.assertEqual(defaults.verify_effective(loop, "formal", policy), [])

    def test_windows_new_policy_projects_only_after_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            loop, policy = self.seed(Path(tmp))
            policy.unlink()
            for key in loader.POLICY_KEYS | {loader.WINDOWS_PROJECTION_ENV, loader.WINDOWS_PROJECTION_SOURCE_ENV}:
                os.environ.pop(key, None)
            self.simulate_windows()
            before = self.projection_snapshot()
            commit = state_transaction.commit_transaction
            def assert_unprojected(run_dir, **kwargs):
                self.assertEqual(self.projection_snapshot(), before)
                return commit(run_dir, **kwargs)
            with mock.patch.object(state_transaction, "commit_transaction", side_effect=assert_unprojected):
                result = defaults.apply_defaults(loop, policy_file=policy)
            self.assertTrue(result["ok"], result)
            self.assertEqual(os.environ[loader.WINDOWS_PROJECTION_SOURCE_ENV], str(policy))
            self.assertEqual(loader.load_env_file(policy), parse_env_text(policy.read_text(encoding="utf-8")))

    def test_windows_invalid_or_stale_authority_is_rejected_without_mutation(self):
        self.simulate_windows()
        for invalid in ("missing", "wrong-source", "invalid-value", "stale"):
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as tmp:
                loop, policy = self.seed(Path(tmp))
                values = parse_env_text(policy.read_text(encoding="utf-8"))
                self.project_policy(policy, values)
                if invalid == "missing":
                    os.environ.pop(loader.WINDOWS_PROJECTION_ENV)
                elif invalid == "wrong-source":
                    os.environ[loader.WINDOWS_PROJECTION_SOURCE_ENV] = str(policy.with_name("other.env"))
                elif invalid == "invalid-value":
                    os.environ["AAS_AUTOLOOP_NOTIFY"] = "$(unsafe)"
                else:
                    os.environ["AAS_AUTOLOOP_NOTIFY"] = "auto"
                before_env = self.projection_snapshot()
                before_files = {str(p.relative_to(Path(tmp))): p.read_bytes() for p in Path(tmp).rglob("*") if p.is_file()}
                with self.assertRaises(ValueError):
                    defaults.apply_defaults(loop, policy_file=policy)
                self.assertEqual(self.projection_snapshot(), before_env)
                self.assertEqual(before_files, {str(p.relative_to(Path(tmp))): p.read_bytes() for p in Path(tmp).rglob("*") if p.is_file()})

    def test_windows_rejected_stage_preserves_projection(self):
        self.simulate_windows()
        for existing in (True, False):
            with self.subTest(existing=existing), tempfile.TemporaryDirectory() as tmp:
                loop, policy = self.seed(Path(tmp))
                (loop / "current_plan.json").unlink()
                self.project_policy(policy, parse_env_text(policy.read_text(encoding="utf-8")))
                host_before = policy.read_bytes() if existing else None
                if not existing:
                    policy.unlink()
                before = self.projection_snapshot()
                result = defaults.apply_defaults(loop, policy_file=policy)
                self.assertFalse(result["ok"])
                self.assertEqual(self.projection_snapshot(), before)
                self.assertEqual(policy.read_bytes() if policy.exists() else None, host_before)

    def test_oversized_staged_policy_is_rejected_before_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            loop, policy = self.seed(Path(tmp))
            body = "AAS_AUTOLOOP_FORMAL_REMOTE_REQUEST=/" + "a" * (loader.MAX_POLICY_BYTES - 80) + "\n"
            policy.write_text(body, encoding="utf-8")
            if os.name != "posix":
                self.project_policy(policy, parse_env_text(body))
            before = {str(p.relative_to(Path(tmp))): p.read_bytes() for p in Path(tmp).rglob("*") if p.is_file()}
            with mock.patch.object(state_transaction, "commit_transaction") as commit:
                with self.assertRaisesRegex(ValueError, "size limit"):
                    defaults.apply_defaults(loop, policy_file=policy)
            commit.assert_not_called()
            self.assertEqual(before, {str(p.relative_to(Path(tmp))): p.read_bytes() for p in Path(tmp).rglob("*") if p.is_file()})

    def test_windows_failed_transactions_preserve_projection(self):
        self.simulate_windows()
        for failure_at in (1, 2, 3):
            with self.subTest(failure_at=failure_at), tempfile.TemporaryDirectory() as tmp:
                loop, policy = self.seed(Path(tmp))
                self.project_policy(policy, parse_env_text(policy.read_text(encoding="utf-8")))
                before = self.projection_snapshot()
                host_before = policy.read_bytes()
                commit = state_transaction.commit_transaction
                calls = 0
                def fail_commit(run_dir, **kwargs):
                    nonlocal calls
                    calls += 1
                    self.assertEqual(self.projection_snapshot(), before)
                    if calls == failure_at:
                        raise OSError("injected transaction failure")
                    return commit(run_dir, **kwargs)
                with mock.patch.object(state_transaction, "commit_transaction", side_effect=fail_commit):
                    with self.assertRaises(OSError):
                        defaults.apply_defaults(loop, policy_file=policy)
                self.assertEqual(calls, failure_at)
                self.assertEqual(self.projection_snapshot(), before)
                self.assertEqual(policy.read_bytes(), host_before)

    def test_formal_backend_projection_is_explicit_and_scalar(self):
        values = parse_env_text("AAS_AUTOLOOP_FORMAL_EXECUTION_BACKEND=kaggle-cpu\nAAS_AUTOLOOP_FORMAL_REMOTE_REQUEST=/host/request.json\n")
        self.assertEqual(values["AAS_AUTOLOOP_FORMAL_EXECUTION_BACKEND"], "kaggle-cpu")
        for body in ("AAS_AUTOLOOP_FORMAL_EXECUTION_BACKEND=local,kaggle-cpu\n", "AAS_AUTOLOOP_FORMAL_REMOTE_REQUEST=relative.json\n"):
            with self.assertRaises(EnvLoadError):
                parse_env_text(body)

    def test_bootstrap_bad_authority_does_not_create_loop(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = root / "policy.env"
            policy.write_text("UNKNOWN_KEY=value\n", encoding="utf-8")
            policy.chmod(0o600)
            loop = root / "not-created"
            args = cli.build_parser().parse_args(["bootstrap", "--loop", str(loop),
                "--policy-file", str(policy), "--goal", "goal", "--success-criteria", "criteria"])
            self.assertEqual(cli.cmd_bootstrap(args), 2)
            self.assertFalse(loop.exists())

    def test_explicit_stop_remains_effective_on_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            loop, policy = self.seed(root)
            (loop / "STOP_REQUESTED").unlink()
            args = cli.build_parser().parse_args(["stop", "--loop", str(loop)])
            with mock.patch.object(cli, "stop_loop_processes", return_value=[]), mock.patch.object(cli, "status_snapshot", return_value={}):
                self.assertEqual(cli.cmd_stop(args), 0)
            start = cli.build_parser().parse_args(["start", "--loop", str(loop), "--policy-file", str(policy)])
            with mock.patch.object(cli, "run_foreground") as launch:
                self.assertEqual(cli.cmd_start(start), 1)
            launch.assert_not_called()
            self.assertTrue((loop / "STOP_REQUESTED").exists())

    def test_missing_plan_creates_neither_loop_nor_host_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            loop = root / "missing-loop"
            policy = root / "missing-host-parent/policy.env"
            result = defaults.apply_defaults(loop, policy_file=policy)
            self.assertFalse(result["ok"])
            self.assertFalse(loop.exists())
            self.assertFalse(policy.parent.exists())

    def test_interrupted_host_publication_retains_recoverable_intent(self):
        with tempfile.TemporaryDirectory() as tmp:
            loop, policy = self.seed(Path(tmp), mode="enforce")
            original = state_transaction.commit_transaction
            def fail_host_publish(run_dir, **kwargs):
                if Path(run_dir) == policy.parent and policy.name in kwargs.get("binary_files", {}):
                    raise OSError("injected host publication interruption")
                return original(run_dir, **kwargs)
            with mock.patch.object(state_transaction, "commit_transaction", side_effect=fail_host_publish):
                with self.assertRaises(OSError):
                    defaults.apply_defaults(loop, policy_file=policy)
            pending = policy.parent / f".{policy.name}.defaults-pending.json"
            self.assertTrue(pending.exists())
            result = defaults.apply_defaults(loop, policy_file=policy)
            self.assertTrue(result["ok"], result)
            self.assertFalse(pending.exists())

    def test_run_group_replays_after_interrupted_transaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            loop, policy = self.seed(Path(tmp), mode="enforce")
            original = state_transaction.commit_transaction
            def interrupt_run(run_dir, **kwargs):
                if Path(run_dir) == loop:
                    kwargs["crash_after"] = 1
                return original(run_dir, **kwargs)
            with mock.patch.object(state_transaction, "commit_transaction", side_effect=interrupt_run):
                with self.assertRaises(state_transaction.InjectedCrash):
                    defaults.apply_defaults(loop, policy_file=policy)
            state_transaction.recover_transactions(loop)
            self.assertTrue(defaults.apply_defaults(loop, policy_file=policy)["ok"])
            self.assertEqual(json.loads((loop / "loop_state.json").read_text(encoding="utf-8"))["status"], "paused")

    def seed(self, root: Path, mode: str = "monitor") -> tuple[Path, Path]:
        loop = root / "loop"
        loop.mkdir()
        (loop / "current_plan.json").write_text(json.dumps({"enforcement_mode": mode}), encoding="utf-8")
        state = {"status": "paused", "standing_orders": {
            "goal_focus": {"mode": mode}, "goal_priority": {"enabled": False, "discipline_mode": "soft"},
            "notify": {"mode": "off"}, "panel": {"enabled": False, "providers": []},
            "compute": {"backends": ["local"], "forbidden_services": ["modal", "hetzner"]},
        }}
        (loop / "loop_state.json").write_text(json.dumps(state), encoding="utf-8")
        (loop / "compute_policy.json").write_text(json.dumps({"backends": ["local"], "forbidden_services": ["modal", "hetzner"]}), encoding="utf-8")
        (loop / "goal_priority.json").write_text(json.dumps({"enabled": False, "discipline_mode": "soft"}), encoding="utf-8")
        (loop / "STOP_REQUESTED").write_text("operator stop", encoding="utf-8")
        policy = root / "policy.env"
        policy.write_text("AAS_AUTOLOOP_NOTIFY=off\nAAS_AUTOLOOP_FORMAL_POLICY=off\nAAS_AUTOLOOP_GOAL_PRIORITY=off\n", encoding="utf-8")
        policy.chmod(0o600)
        if os.name != "posix":
            self.project_policy(policy, parse_env_text(policy.read_text(encoding="utf-8")))
        return loop, policy

    def test_existing_policy_and_stop_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            loop, policy = self.seed(Path(tmp))
            before = {name: (loop / name).read_bytes() for name in ("current_plan.json", "compute_policy.json", "STOP_REQUESTED")}
            result = defaults.apply_defaults(loop, profile="formal", policy_file=policy)
            state = json.loads((loop / "loop_state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["standing_orders"]["goal_focus"]["mode"], "monitor")
            self.assertEqual(state["standing_orders"]["notify"]["mode"], "off")
            self.assertFalse(state["standing_orders"]["goal_priority"]["enabled"])
            self.assertEqual(state["status"], "paused")
            self.assertEqual(state["standing_orders"]["panel"], {"enabled": False, "providers": []})
            for name, data in before.items():
                self.assertEqual((loop / name).read_bytes(), data)
            self.assertIn("AAS_AUTOLOOP_FORMAL_POLICY=off", policy.read_text(encoding="utf-8"))
            self.assertIn("AAS_AUTOLOOP_NOTIFY=off", policy.read_text(encoding="utf-8"))

    def test_invalid_host_policy_does_not_mutate_loop(self):
        with tempfile.TemporaryDirectory() as tmp:
            loop, policy = self.seed(Path(tmp))
            policy.write_text("UNSUPPORTED=value\n", encoding="utf-8")
            before = {str(p.relative_to(loop)): p.read_bytes() for p in loop.rglob("*") if p.is_file()}
            with self.assertRaises(ValueError):
                defaults.apply_defaults(loop, policy_file=policy)
            self.assertEqual(before, {str(p.relative_to(loop)): p.read_bytes() for p in loop.rglob("*") if p.is_file()})

    def test_formal_extensions_and_zero_credit_survive(self):
        with tempfile.TemporaryDirectory() as tmp:
            loop, policy = self.seed(Path(tmp))
            (loop / "formal").mkdir()
            explicit = {"policy": "off", "typecheck": False, "force_credits": 0, "lax_request": "/host/selected.json", "project": "native"}
            (loop / "formal/formal_policy.json").write_text(json.dumps(explicit), encoding="utf-8")
            defaults.apply_defaults(loop, profile="formal", policy_file=policy)
            actual = json.loads((loop / "formal/formal_policy.json").read_text(encoding="utf-8"))
            for key, value in explicit.items():
                self.assertEqual(actual[key], value)

    def test_concurrent_state_change_is_not_lost(self):
        with tempfile.TemporaryDirectory() as tmp:
            loop, policy = self.seed(Path(tmp), mode="enforce")
            commit = state_transaction.commit_transaction
            def racing_commit(run_dir, **kwargs):
                state_path = loop / "loop_state.json"
                state = json.loads(state_path.read_text(encoding="utf-8"))
                state["operator_note"] = "concurrent change"
                state_path.write_text(json.dumps(state), encoding="utf-8")
                return commit(run_dir, **kwargs)
            with mock.patch.object(state_transaction, "commit_transaction", side_effect=racing_commit):
                with self.assertRaises(state_transaction.RevisionConflict):
                    defaults.apply_defaults(loop, policy_file=policy)
            self.assertEqual(json.loads((loop / "loop_state.json").read_text(encoding="utf-8"))["operator_note"], "concurrent change")


if __name__ == "__main__":
    unittest.main()
