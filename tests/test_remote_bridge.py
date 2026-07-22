"""Unit tests for remote-bridge (offline, no network)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RB = REPO / "canonical" / "runtime" / "skills" / "remote-bridge" / "remote_bridge.py"


def _run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    base = os.environ.copy()
    if env:
        base.update(env)
    return subprocess.run(
        [sys.executable, str(RB), *args],
        capture_output=True,
        text=True,
        env=base,
        check=False,
    )


class RemoteBridgeSelftest(unittest.TestCase):
    def test_selftest_ok(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            res = _run("selftest", "--work-dir", tmp)
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            data = json.loads(res.stdout)
            self.assertTrue(data.get("ok"))


class RemoteBridgeMailbox(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "state"
        self.env = {
            "AAS_REMOTE_BRIDGE_STATE": str(self.state),
            "REMOTE_BRIDGE_SECRETS_FILE": str(Path(self.tmp.name) / "missing.json"),
        }

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_arm_status_cas_inbox(self) -> None:
        res = _run(
            "arm",
            "--job",
            "j1",
            "--provider",
            "codex",
            "--cwd",
            self.tmp.name,
            env=self.env,
        )
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        res = _run("status", env=self.env)
        data = json.loads(res.stdout)
        self.assertEqual(data["count"], 1)

        res = _run(
            "request-approval",
            "--job",
            "j1",
            "--provider",
            "codex",
            "--tool",
            "Bash",
            "--args-json",
            '{"command":"true"}',
            "--summary",
            "true",
            "--no-notify",
            env=self.env,
        )
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        req = json.loads(res.stdout)["request"]
        rid = req["request_id"]

        res = _run(
            "handle-command",
            "--text",
            f"/aas approve {rid}",
            "--principal",
            "cli",
            "--allow-local-cli",
            env=self.env,
        )
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

        # second approve is already resolved
        res = _run(
            "handle-command",
            "--text",
            f"/aas deny {rid}",
            "--principal",
            "cli",
            "--allow-local-cli",
            env=self.env,
        )
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertTrue(json.loads(res.stdout).get("reply", {}).get("already_resolved"))

        res = _run("instruct", "--job", "j1", "--text", "next step please", env=self.env)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        # peek must not drain
        res = _run("format-inbox", "--job", "j1", "--peek", env=self.env)
        data = json.loads(res.stdout)
        self.assertIn("next step please", data.get("block", ""))
        res = _run("format-inbox", "--job", "j1", "--consume", env=self.env)
        data = json.loads(res.stdout)
        self.assertIn("next step please", data.get("block", ""))
        res = _run("format-inbox", "--job", "j1", "--consume", env=self.env)
        data = json.loads(res.stdout)
        self.assertEqual(data.get("block") or "", "")

    def test_parse_rejects_aasfoo(self) -> None:
        res = _run(
            "handle-command",
            "--text",
            "/aasfoo approve x",
            "--principal",
            "cli",
            env=self.env,
        )
        self.assertNotEqual(res.returncode, 0)
        data = json.loads(res.stdout)
        self.assertEqual(data.get("error_code"), "not_aas")

    def test_empty_allowlist_fail_closed(self) -> None:
        _run(
            "arm",
            "--job",
            "j3",
            "--provider",
            "grok",
            "--cwd",
            self.tmp.name,
            env=self.env,
        )
        res = _run(
            "handle-command",
            "--text",
            "/aas status",
            "--principal",
            "user-1",
            env=self.env,
        )
        self.assertNotEqual(res.returncode, 0)
        self.assertEqual(json.loads(res.stdout).get("error_code"), "forbidden")

    def test_truncated_request_approval(self) -> None:
        _run(
            "arm",
            "--job",
            "j2",
            "--provider",
            "grok",
            "--cwd",
            self.tmp.name,
            env=self.env,
        )
        res = _run(
            "request-approval",
            "--job",
            "j2",
            "--tool",
            "Bash",
            "--truncated",
            "--no-notify",
            env=self.env,
        )
        self.assertNotEqual(res.returncode, 0)
        self.assertEqual(json.loads(res.stdout).get("error_code"), "truncated_input")


class RemoteBridgeDigest(unittest.TestCase):
    def test_digest_stable(self) -> None:
        import importlib.util

        spec = importlib.util.spec_from_file_location("aas_remote_bridge_test", RB)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        a = mod.action_digest(
            provider="grok",
            job_id="j",
            workspace_root="/tmp/ws",
            tool="Bash",
            args={"command": "ls"},
            nonce="n",
        )
        b = mod.action_digest(
            provider="grok",
            job_id="j",
            workspace_root="/tmp/ws",
            tool="Bash",
            args={"command": "ls"},
            nonce="n",
        )
        self.assertEqual(a, b)
        c = mod.action_digest(
            provider="grok",
            job_id="j",
            workspace_root="/tmp/ws",
            tool="Bash",
            args={"command": "pwd"},
            nonce="n",
        )
        self.assertNotEqual(a, c)


if __name__ == "__main__":
    unittest.main()
