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

# Never write __pycache__ into the canonical runtime tree (inventory CI).
sys.dont_write_bytecode = True


def _subprocess_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if extra:
        env.update(extra)
    return env


def _run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-B", str(RB), *args],
        capture_output=True,
        text=True,
        env=_subprocess_env(env),
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


class RemoteBridgeNotifyFallback(unittest.TestCase):
    """Zulip is primary; Telegram only when Zulip fails (no dual spam)."""

    def _mod(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location("aas_remote_bridge_notify", RB)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        return mod

    def _cfg(self, mod):
        return mod.BridgeConfig(
            raw={},
            secrets_path=None,
            default_channel="zulip",
            notify_channels=["zulip", "telegram"],
            allowed_user_ids=[],
            zulip={
                "site": "https://example.zulipchat.com",
                "email": "bot@example.com",
                "api_key": "k",
                "control_stream": "Research",
                "topic_prefix": "job/",
            },
            telegram={
                "bot_token": "1:token",
                "allowed_chat_ids": ["123"],
            },
        )

    def test_order_is_zulip_then_telegram(self) -> None:
        mod = self._mod()
        cfg = self._cfg(mod)
        for token in (None, "auto", "both", "zulip", "default"):
            order = mod.resolve_notify_channel_order(cfg, requested=token)
            self.assertEqual(order, ["zulip", "telegram"], token)

    def test_telegram_only_when_explicit(self) -> None:
        mod = self._mod()
        cfg = self._cfg(mod)
        self.assertEqual(
            mod.resolve_notify_channel_order(cfg, requested="telegram"),
            ["telegram"],
        )

    def test_stop_on_first_success_skips_telegram(self) -> None:
        from unittest import mock

        mod = self._mod()
        cfg = self._cfg(mod)
        with mock.patch.object(
            mod, "zulip_send", return_value={"ok": True, "channel": "zulip"}
        ) as zs, mock.patch.object(
            mod, "telegram_send", return_value={"ok": True, "channel": "telegram"}
        ) as ts:
            results = mod.notify_channels(
                cfg,
                text="hi",
                job_id="j",
                channels=["zulip", "telegram"],
                stop_on_first_success=True,
            )
        self.assertTrue(zs.called)
        self.assertFalse(ts.called)
        self.assertEqual(list(results.keys()), ["zulip"])

    def test_fallback_to_telegram_when_zulip_fails(self) -> None:
        from unittest import mock

        mod = self._mod()
        cfg = self._cfg(mod)
        with mock.patch.object(
            mod,
            "zulip_send",
            return_value={"ok": False, "channel": "zulip", "error": "boom"},
        ) as zs, mock.patch.object(
            mod, "telegram_send", return_value={"ok": True, "channel": "telegram"}
        ) as ts:
            results = mod.notify_channels(
                cfg,
                text="hi",
                job_id="j",
                channels=["zulip", "telegram"],
                stop_on_first_success=True,
            )
        self.assertTrue(zs.called)
        self.assertTrue(ts.called)
        self.assertTrue(results["telegram"]["ok"])


if __name__ == "__main__":
    unittest.main()
