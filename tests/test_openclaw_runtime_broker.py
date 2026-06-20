from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import hashlib
import tempfile
import unittest
from pathlib import Path

import os

from installer.ai_agents_skills.openclaw_runtime_broker import (
    AgentToken,
    BrokerState,
    broker_authorize,
    build_child_env,
    env_var_allowed,
    exec_plan,
    handle_run_request,
    verify_before_exec,
)


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _state(tmp: Path):
    target_rel = "skills/demo/tool.py"
    target = tmp / target_rel
    target.parent.mkdir(parents=True, exist_ok=True)
    body = b"print('demo')\n"
    target.write_bytes(body)
    state = BrokerState(
        runtime_root=tmp,
        tokens={"tok-A": AgentToken(agent="main", allowed={("demo", "run")})},
        commands={("demo", "run"): {"target_rel": target_rel, "expected_sha256": _sha(body)}},
    )
    return state, target


class BrokerEnvTest(unittest.TestCase):
    def test_env_allowlist_and_denylist(self) -> None:
        for ok in ("PATH", "HOME", "LANG", "LC_ALL", "PYTHONPATH", "AAS_RUNTIME_ROOT", "TMPDIR"):
            self.assertTrue(env_var_allowed(ok), ok)
        for bad in ("OPENCLAW_SECRETS_FILE", "ZULIP_EMAIL", "AAS_BROKER_TOKEN", "ANTHROPIC_API_KEY",
                    "OPENROUTER_BASE_URL", "GETSCIPAPERS_TOKEN", "AWS_SECRET_ACCESS_KEY", "RANDOM_PASSWORD"):
            self.assertFalse(env_var_allowed(bad), bad)

    def test_build_child_env_filters(self) -> None:
        parent = {"PATH": "/usr/bin", "HOME": "/h", "OPENCLAW_SECRETS_FILE": "/x", "ZULIP_EMAIL": "a@b",
                  "AAS_BROKER_TOKEN": "secret", "PYTHONDONTWRITEBYTECODE": "1"}
        child = build_child_env(parent)
        self.assertEqual(set(child), {"PATH", "HOME", "PYTHONDONTWRITEBYTECODE"})


class BrokerAuthTest(unittest.TestCase):
    def test_authorize(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state, _ = _state(Path(tmp))
            self.assertEqual(broker_authorize("tok-A", "demo", "run", state=state), ("main", None))
            self.assertEqual(broker_authorize("nope", "demo", "run", state=state)[0], None)
            agent, reason = broker_authorize("tok-A", "demo", "other", state=state)
            self.assertIsNone(agent)
            self.assertIn("not authorized", reason)


class BrokerVerifyTest(unittest.TestCase):
    def test_verify_matches_and_tamper_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state, target = _state(Path(tmp))
            ok, reason = verify_before_exec("demo", "run", state=state)
            self.assertIsNotNone(ok)
            self.assertIsNone(reason)
            # tamper the on-disk target -> verify-before-exec must refuse
            target.write_bytes(b"print('PWNED')\n")
            ok, reason = verify_before_exec("demo", "run", state=state)
            self.assertIsNone(ok)
            self.assertIn("verify-before-exec failed", reason)

    def test_missing_target_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state, target = _state(Path(tmp))
            target.unlink()
            self.assertIn("missing", verify_before_exec("demo", "run", state=state)[1])


class BrokerRequestTest(unittest.TestCase):
    def test_happy_path_returns_verified_plan_with_filtered_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state, target = _state(Path(tmp))
            resp = handle_run_request(
                {"token": "tok-A", "skill": "demo", "command": "run", "args": ["--flag", "v"]},
                state=state,
                parent_env={"PATH": "/usr/bin", "OPENCLAW_SECRETS_FILE": "/x"},
            )
            self.assertEqual(resp["status"], "ok")
            self.assertEqual(resp["agent"], "main")
            self.assertEqual(resp["argv"], [str(target.resolve()), "--flag", "v"])
            self.assertNotIn("OPENCLAW_SECRETS_FILE", resp["env"])
            self.assertIn("PATH", resp["env"])

    def test_bad_token_denied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state, _ = _state(Path(tmp))
            resp = handle_run_request({"token": "bad", "skill": "demo", "command": "run"}, state=state, parent_env={})
            self.assertEqual(resp["status"], "denied")

    def test_tamper_refused_through_handler(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state, target = _state(Path(tmp))
            target.write_bytes(b"evil\n")
            resp = handle_run_request({"token": "tok-A", "skill": "demo", "command": "run"}, state=state, parent_env={})
            self.assertEqual(resp["status"], "refused")

    def test_bad_args_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state, _ = _state(Path(tmp))
            resp = handle_run_request(
                {"token": "tok-A", "skill": "demo", "command": "run", "args": "not-a-list"}, state=state, parent_env={})
            self.assertEqual(resp["status"], "error")


class BrokerExecTest(unittest.TestCase):
    def test_exec_plan_runs_verified_target_via_interpreter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            target_rel = "skills/demo/tool.py"
            target = tmp / target_rel
            target.parent.mkdir(parents=True, exist_ok=True)
            body = b"import sys; print('HELLO', sys.argv[1] if len(sys.argv) > 1 else '')\n"
            target.write_bytes(body)
            state = BrokerState(
                runtime_root=tmp,
                tokens={"tok": AgentToken(agent="main", allowed={("demo", "run")})},
                commands={("demo", "run"): {"target_rel": target_rel,
                                            "expected_sha256": _sha(body), "interpreter": sys.executable}},
            )
            plan = handle_run_request(
                {"token": "tok", "skill": "demo", "command": "run", "args": ["WORLD"]},
                state=state, parent_env={"PATH": os.environ.get("PATH", "")})
            self.assertEqual(plan["status"], "ok")
            result = exec_plan(plan, timeout=30)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["exit_code"], 0)
            self.assertIn("HELLO WORLD", result["stdout"])

    def test_exec_plan_passes_through_denied(self) -> None:
        denied = {"status": "denied", "code": "unauthorized"}
        self.assertEqual(exec_plan(denied), denied)


if __name__ == "__main__":
    unittest.main()
