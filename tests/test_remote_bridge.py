"""Unit tests for remote-bridge (offline, no network)."""

from __future__ import annotations

import copy
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RB = REPO / "canonical" / "runtime" / "skills" / "remote-bridge" / "remote_bridge.py"

# Never write __pycache__ into the canonical runtime tree (inventory CI).
sys.dont_write_bytecode = True


def _subprocess_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("REMOTE_BRIDGE_SECRETS_FILE", None)
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
            res = _run(
                "selftest",
                "--work-dir",
                tmp,
                env={
                    "HOME": tmp,
                    "XDG_CONFIG_HOME": str(Path(tmp) / "config"),
                },
            )
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            data = json.loads(res.stdout)
            self.assertTrue(data.get("ok"))


class RemoteBridgeMailbox(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "state"
        self.env = {
            "AAS_REMOTE_BRIDGE_STATE": str(self.state),
            "HOME": self.tmp.name,
            "XDG_CONFIG_HOME": str(Path(self.tmp.name) / "config"),
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

    def test_request_approval_scrubs_summary_before_persistence_and_output(self) -> None:
        _run(
            "arm",
            "--job",
            "j4",
            "--provider",
            "codex",
            "--cwd",
            self.tmp.name,
            env=self.env,
        )
        configured = "configured-approval-secret-92731"
        common = "credential-fixture-sentinel-53961"
        email = "participant" + chr(64) + "example.invalid"
        phone = "+1 (202) 555-0187"
        secrets = Path(self.tmp.name) / "secrets.json"
        secrets.write_text(
            json.dumps({"zulip": {"api_key": configured}}), encoding="utf-8"
        )
        if os.name == "posix":
            secrets.chmod(0o600)
        summary = (
            f"token={configured}; Bearer {common}; contact {email}; call {phone}"
        )

        res = _run(
            "--secrets-file",
            str(secrets),
            "request-approval",
            "--job",
            "j4",
            "--provider",
            "codex",
            "--tool",
            "Bash",
            "--summary",
            summary,
            "--no-notify",
            env=self.env,
        )

        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        persisted = "\n".join(
            path.read_text(encoding="utf-8")
            for path in self.state.rglob("*.json*")
            if path.is_file()
        )
        combined = res.stdout + res.stderr + persisted
        for sensitive in (configured, common, email, phone):
            self.assertNotIn(sensitive, combined)
        self.assertIn("[REDACTED]", combined)

    def test_handle_command_accepts_bounded_stdin_text(self) -> None:
        arm = _run(
            "arm",
            "--job",
            "j5",
            "--provider",
            "codex",
            "--cwd",
            self.tmp.name,
            env=self.env,
        )
        self.assertEqual(arm.returncode, 0, arm.stdout + arm.stderr)
        completed = subprocess.run(
            [
                sys.executable,
                "-B",
                str(RB),
                "handle-command",
                "--text-stdin",
                "--principal",
                "cli",
                "--allow-local-cli",
            ],
            input="/aas status j5",
            capture_output=True,
            text=True,
            env=_subprocess_env(self.env),
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertTrue(json.loads(completed.stdout).get("ok"))




class RemoteBridgePathSync(unittest.TestCase):
    def _mod(self):
        import importlib.util

        sync_py = REPO / "canonical" / "runtime" / "skills" / "remote-bridge" / "sync_remote_bridge_paths.py"
        spec = importlib.util.spec_from_file_location("rb_sync_test", sync_py)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_all_sync_entrypoints_are_inert_and_preserve_paths(self) -> None:
        mod = self._mod()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host = root / "host_secrets.json"
            ws = root / "ws_secrets.json"
            host.write_text('{"token":"host"}\n', encoding="utf-8")
            ws.write_text('{"token":"ws"}\n', encoding="utf-8")
            host_state = root / "host-state"
            ws_state = root / "ws-state"
            host_state.mkdir()
            ws_state.mkdir()
            (host_state / "state.json").write_bytes(b"host state\n")
            (ws_state / "state.json").write_bytes(b"workspace state\n")
            before = {
                path: path.read_bytes()
                for path in (host, ws, host_state / "state.json", ws_state / "state.json")
            }

            results = [
                mod.sync_secrets_file(host, ws),
                mod.sync_state_trees(host_state, ws_state),
                mod.sync_once(quiet=True),
            ]

            for result in results:
                self.assertFalse(result["ok"])
                self.assertEqual(result["status"], "blocked")
                self.assertFalse(result["paths_inspected"])
                self.assertFalse(result["paths_mutated"])
            self.assertEqual(mod.default_paths(), {})
            for path, payload in before.items():
                self.assertEqual(path.read_bytes(), payload)

    def test_sync_cli_is_blocked_without_touching_configured_paths(self) -> None:
        sync_py = REPO / "canonical" / "runtime" / "skills" / "remote-bridge" / "sync_remote_bridge_paths.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            victim = root / "victim.json"
            victim.write_bytes(b"must remain unchanged\n")
            env = _subprocess_env(
                {
                    "REMOTE_BRIDGE_HOST_SECRETS": str(victim),
                    "REMOTE_BRIDGE_WORKSPACE_SECRETS": str(root / "missing.json"),
                    "AAS_REMOTE_BRIDGE_SYNC": "1",
                }
            )
            result = subprocess.run(
                [sys.executable, "-B", str(sync_py), "--json"],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["error_code"], "bidirectional_sync_retired")
            self.assertEqual(victim.read_bytes(), b"must remain unchanged\n")


class RemoteBridgeOpenClawPublish(unittest.TestCase):
    def test_publisher_is_blocked_without_inspecting_or_mutating_destination(self) -> None:
        pub = REPO / "canonical" / "runtime" / "skills" / "remote-bridge" / "publish_openclaw_adapter.py"
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "aas-remote-bridge"
            dest.mkdir()
            planted = dest / "unrelated.txt"
            planted.write_bytes(b"unrelated user bytes\n")
            before = planted.read_bytes()

            for dry_run in (False, True):
                argv = [sys.executable, "-B", str(pub), "--dest", str(dest), "--json"]
                if dry_run:
                    argv.append("--dry-run")
                res = subprocess.run(
                    argv,
                    capture_output=True,
                    text=True,
                    env=_subprocess_env(),
                    check=False,
                )
                self.assertEqual(res.returncode, 2, res.stdout + res.stderr)
                data = json.loads(res.stdout)
                self.assertFalse(data["ok"])
                self.assertEqual(data["status"], "blocked")
                self.assertFalse(data["destination_inspected"])
                self.assertFalse(data["destination_mutated"])
                self.assertEqual(data["actions"], [])

            self.assertEqual(planted.read_bytes(), before)
            self.assertEqual(sorted(path.name for path in dest.iterdir()), ["unrelated.txt"])

    @unittest.skipUnless(os.name == "posix", "requires POSIX link semantics")
    def test_blocked_publisher_preserves_hostile_destination_entries(self) -> None:
        pub = REPO / "canonical" / "runtime" / "skills" / "remote-bridge" / "publish_openclaw_adapter.py"
        for attack in ("symlink", "hardlink", "directory", "regular"):
            with self.subTest(attack=attack), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                dest = root / "aas-remote-bridge"
                dest.mkdir()
                planted = dest / "SKILL.md"
                victim = root / "victim.txt"
                victim.write_bytes(b"victim remains unchanged\n")
                if attack == "symlink":
                    planted.symlink_to(victim)
                elif attack == "hardlink":
                    os.link(victim, planted)
                elif attack == "directory":
                    planted.mkdir()
                    (planted / "sentinel").write_bytes(b"directory remains\n")
                else:
                    planted.write_bytes(b"unrelated regular bytes\n")
                victim_before = victim.read_bytes()
                planted_inode = os.lstat(planted).st_ino

                res = subprocess.run(
                    [sys.executable, "-B", str(pub), "--dest", str(dest), "--json"],
                    capture_output=True,
                    text=True,
                    env=_subprocess_env(),
                    check=False,
                )

                self.assertEqual(res.returncode, 2, res.stdout + res.stderr)
                self.assertEqual(victim.read_bytes(), victim_before)
                self.assertEqual(os.lstat(planted).st_ino, planted_inode)
                self.assertFalse((dest / ".aas-published.json").exists())

    def test_runtime_upgrade_replaces_old_sync_and_publisher_with_inert_stubs(self) -> None:
        from types import SimpleNamespace

        from installer.ai_agents_skills import runtime as runtime_install
        from installer.ai_agents_skills.apply import base_result
        from installer.ai_agents_skills.manifest import load_manifests

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "home"
            runtime_root = root / ".codex" / "runtime"
            target_dir = runtime_root / "workspace" / "skills" / "remote-bridge"
            target_dir.mkdir(parents=True)
            old_payloads = {
                "sync_remote_bridge_paths.py": b"# old bidirectional sync\n",
                "publish_openclaw_adapter.py": b"# old unsafe publisher\n",
            }
            for name, payload in old_payloads.items():
                (target_dir / name).write_bytes(payload)

            actions = runtime_install.build_runtime_actions(
                root=root,
                manifests=load_manifests(),
                selected_skills=["remote-bridge"],
                agents=[SimpleNamespace(name="codex")],
                runtime_profile="auto",
                runtime_root=runtime_root,
                platform="linux",
                backup_replace=True,
            )
            revocations = {
                Path(action["path"]).name: action
                for action in actions
                if Path(action["path"]).name in old_payloads
            }
            self.assertEqual(set(revocations), set(old_payloads))
            self.assertTrue(
                all(action["operation"] == "backup-replace" for action in revocations.values())
            )

            for name, action in revocations.items():
                result = runtime_install.apply_runtime_file_action(
                    root, "revocation-upgrade", action, base_result("revocation-upgrade", action)
                )
                self.assertTrue(result["applied"])
                self.assertTrue(result["backup"])
                self.assertNotEqual((target_dir / name).read_bytes(), old_payloads[name])

            sync_result = subprocess.run(
                [sys.executable, "-B", str(target_dir / "sync_remote_bridge_paths.py"), "--json"],
                capture_output=True,
                text=True,
                env=_subprocess_env(),
                check=False,
            )
            publisher_result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(target_dir / "publish_openclaw_adapter.py"),
                    "--dest",
                    str(Path(tmp) / "must-not-exist"),
                    "--json",
                ],
                capture_output=True,
                text=True,
                env=_subprocess_env(),
                check=False,
            )
            self.assertEqual(sync_result.returncode, 2, sync_result.stdout)
            self.assertEqual(publisher_result.returncode, 2, publisher_result.stdout)
            self.assertFalse((Path(tmp) / "must-not-exist").exists())


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
                # Build without a literal email token so sanitize-check stays clean.
                "email": "bot" + chr(64) + "example.com",
                "api_key": "k",
                "control_stream": "ops-notify",
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

    def test_explicit_unavailable_telegram_does_not_fall_back_to_zulip(self) -> None:
        from unittest import mock

        mod = self._mod()
        cfg = self._cfg(mod)
        cfg.telegram = {}
        self.assertEqual(
            mod.resolve_notify_channel_order(cfg, requested="telegram"),
            [],
        )
        with mock.patch.object(mod, "zulip_send") as zulip, mock.patch.object(
            mod, "telegram_send"
        ) as telegram:
            results = mod.notify_channels(
                cfg,
                text="must not reroute",
                job_id="j",
                channels=mod.resolve_notify_channel_order(
                    cfg, requested="telegram"
                ),
            )
        self.assertEqual(results, {})
        zulip.assert_not_called()
        telegram.assert_not_called()

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

    def test_strict_zulip_policy_disables_telegram_fallback(self) -> None:
        from unittest import mock

        mod = self._mod()
        cfg = self._cfg(mod)
        strict_env = {"AAS_REMOTE_STRICT_NOTIFY_CHANNEL": "zulip"}

        self.assertEqual(
            mod.resolve_notify_channel_order(
                cfg, requested="zulip", environ=strict_env
            ),
            ["zulip"],
        )
        self.assertEqual(
            mod.resolve_notify_channel_order(
                cfg, requested="telegram", environ=strict_env
            ),
            [],
        )
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
                environ=strict_env,
            )

        self.assertTrue(zs.called)
        self.assertFalse(ts.called)
        self.assertEqual(list(results), ["zulip"])

    def test_invalid_strict_notify_channel_fails_closed(self) -> None:
        mod = self._mod()
        cfg = self._cfg(mod)
        with self.assertRaises(ValueError):
            mod.resolve_notify_channel_order(
                cfg,
                requested="auto",
                environ={"AAS_REMOTE_STRICT_NOTIFY_CHANNEL": "fallback"},
            )

    def test_transport_exception_redacts_unconfigured_bearer_token(self) -> None:
        from unittest import mock

        mod = self._mod()
        cfg = mod.BridgeConfig(
            raw={},
            secrets_path=None,
            default_channel="zulip",
            notify_channels=["zulip"],
            zulip={
                "site": "https://example.invalid",
                "email": "bot" + chr(64) + "example.invalid",
                "api_key": "configured-secret",
                "control_stream": "ops-notify",
            },
        )
        bearer = "credential-fixture-sentinel-53961"
        with mock.patch.object(
            mod, "zulip_send", side_effect=RuntimeError(f"Bearer {bearer}")
        ):
            results = mod.notify_channels(
                cfg,
                text="status",
                job_id="topic",
                channels=["zulip"],
                dry_run=False,
            )
        serialized = json.dumps(results)
        self.assertNotIn(bearer, serialized)
        self.assertIn("[REDACTED]", serialized)

    def test_shared_egress_redacts_approval_text_html_and_topic(self) -> None:
        from unittest import mock

        mod = self._mod()
        cfg = self._cfg(mod)
        configured = "configured-notify-secret-92731"
        common = "credential-fixture-sentinel-53961"
        email = "participant" + chr(64) + "example.invalid"
        phone = "+1 (202) 555-0187"
        cfg.zulip["api_key"] = configured
        text = (
            f"Approval token={configured}; Bearer {common}; contact {email}; "
            f"call {phone}"
        )
        html = f"<b>{configured}</b> Bearer {common} {email} {phone}"
        job = f"approval-{configured}-{email}"

        with mock.patch.object(
            mod, "zulip_send", return_value={"ok": True, "channel": "zulip"}
        ) as zulip, mock.patch.object(
            mod,
            "telegram_send",
            return_value={"ok": True, "channel": "telegram"},
        ) as telegram:
            result = mod.notify_channels(
                cfg,
                text=text,
                html=html,
                job_id=job,
                channels=["zulip", "telegram"],
                stop_on_first_success=False,
            )

        self.assertTrue(result["zulip"]["ok"])
        self.assertTrue(result["telegram"]["ok"])
        outbound = json.dumps(
            [zulip.call_args.kwargs, telegram.call_args.kwargs], sort_keys=True
        )
        for sensitive in (configured, common, email, phone):
            self.assertNotIn(sensitive, outbound)
        self.assertIn("[REDACTED]", outbound)

    def test_shared_zulip_egress_neutralizes_all_mentions(self) -> None:
        from unittest import mock

        mod = self._mod()
        cfg = self._cfg(mod)
        text = (
            "@all @everyone @stream @topic "
            "@**all** @**everyone** @**channel** @**topic** @**Alice**"
        )
        with mock.patch.object(
            mod, "zulip_send", return_value={"ok": True, "channel": "zulip"}
        ) as zulip:
            result = mod.notify_channels(
                cfg,
                text=text,
                job_id="mention-boundary",
                channels=["zulip"],
            )

        self.assertTrue(result["zulip"]["ok"])
        outbound = zulip.call_args.kwargs["content"]
        self.assertNotIn("@", outbound)
        self.assertIn("＠**all**", outbound)
        self.assertIn("＠**Alice**", outbound)

    def test_shared_egress_redactor_failure_calls_no_transport(self) -> None:
        from unittest import mock

        mod = self._mod()
        cfg = self._cfg(mod)
        with mock.patch.object(
            mod,
            "redact_notify_text",
            side_effect=mod.NotifyRedactionError("redactor unavailable"),
        ), mock.patch.object(mod, "zulip_send") as zulip, mock.patch.object(
            mod, "telegram_send"
        ) as telegram:
            with self.assertRaises(mod.NotifyRedactionError):
                mod.notify_channels(
                    cfg,
                    text="approval summary",
                    html="<b>approval summary</b>",
                    job_id="approval-topic",
                    channels=["zulip", "telegram"],
                )

        zulip.assert_not_called()
        telegram.assert_not_called()


class RemoteBridgeStructuredNotify(unittest.TestCase):
    def _mod(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location("aas_remote_bridge_structured", RB)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        return mod

    def _secrets(self, root: Path) -> Path:
        path = root / "secrets.json"
        path.write_text(
            json.dumps(
                {
                    "default_channel": "zulip",
                    "notify_channels": ["zulip", "telegram"],
                    "zulip": {
                        "site": "https://example.zulipchat.com",
                        "email": "bot" + chr(64) + "example.com",
                        "api_key": "not-a-real-key",
                        "control_stream": "ops-notify",
                        "topic_prefix": "job/",
                    },
                    "telegram": {
                        "bot_token": "1:not-a-real-token",
                        "allowed_chat_ids": ["123"],
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        path.chmod(0o600)
        return path

    def test_secrets_file_loads_a_regular_private_leaf(self) -> None:
        mod = self._mod()
        with tempfile.TemporaryDirectory() as tmp:
            valid = Path(tmp) / "valid.json"
            valid.write_text('{"default_channel":"zulip"}\n', encoding="utf-8")
            if os.name == "posix":
                valid.chmod(0o600)
            loaded, loaded_path = mod.load_secrets(str(valid), {})
            self.assertEqual(loaded["default_channel"], "zulip")
            self.assertEqual(loaded_path, str(valid))

    @unittest.skipUnless(os.name == "posix", "requires POSIX mode and link semantics")
    def test_secrets_file_requires_private_single_link_regular_leaf(self) -> None:
        mod = self._mod()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            valid = root / "valid.json"
            valid.write_text('{"default_channel":"zulip"}\n', encoding="utf-8")
            valid.chmod(0o600)

            permissive = root / "permissive.json"
            permissive.write_text("{}\n", encoding="utf-8")
            permissive.chmod(0o644)
            with self.assertRaises(OSError):
                mod.load_secrets(str(permissive), {})

            target = root / "target.json"
            target.write_text("{}\n", encoding="utf-8")
            target.chmod(0o600)
            linked = root / "linked.json"
            os.link(target, linked)
            with self.assertRaises(OSError):
                mod.load_secrets(str(linked), {})

            symlinked = root / "symlinked.json"
            symlinked.symlink_to(valid)
            with self.assertRaises(OSError):
                mod.load_secrets(str(symlinked), {})

    def test_explicit_secrets_sources_are_exclusive_and_never_fall_back(self) -> None:
        from argparse import Namespace
        from unittest import mock

        mod = self._mod()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_home = root / "config"
            base_env = {
                "HOME": str(root / "home"),
                "XDG_CONFIG_HOME": str(config_home),
                # Windows resolves the default secrets file under APPDATA rather
                # than XDG, so the platform's own candidate list places the
                # fixture and the assertion below stays honest on both.
                "APPDATA": str(config_home),
                "AAS_REMOTE_BRIDGE_STATE": str(root / "state"),
            }
            default_path = mod.secrets_candidates(base_env)[0]
            default_dir = default_path.parent
            default_dir.mkdir(parents=True)
            default_dir.chmod(0o700)
            default_path.write_text(
                json.dumps(
                    {
                        "notify_channels": ["zulip"],
                        "zulip": {
                            "site": "https://default.example.invalid",
                            "email": "default" + chr(64) + "example.invalid",
                            "api_key": "default-secret-canary",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            default_path.chmod(0o600)
            explicit = root / "explicit.json"
            explicit.write_text("{}\n", encoding="utf-8")
            explicit.chmod(0o600)
            missing = root / "missing.json"

            loaded, loaded_path = mod.load_secrets(None, dict(base_env))
            self.assertEqual(loaded_path, str(default_path))
            self.assertEqual(loaded["zulip"]["api_key"], "default-secret-canary")

            with self.assertRaisesRegex(OSError, "mutually exclusive"):
                mod.load_secrets(
                    str(explicit),
                    {**base_env, "REMOTE_BRIDGE_SECRETS_FILE": str(missing)},
                )

            args = Namespace(
                secrets_file=None,
                event_json=None,
                text="safe notification",
                html=None,
                job="safe-topic",
                channel="zulip",
                dry_run=True,
            )
            override_env = {
                **base_env,
                "REMOTE_BRIDGE_SECRETS_FILE": str(missing),
                "ZULIP_SITE": "https://fallback.example.invalid",
                "ZULIP_EMAIL": "fallback" + chr(64) + "example.invalid",
                "ZULIP_API_KEY": "fallback-secret-canary",
            }
            with mock.patch.dict(
                mod.os.environ, override_env, clear=True
            ), mock.patch.object(mod, "notify_channels") as send:
                with self.assertRaisesRegex(OSError, "does not exist"):
                    mod.cmd_send(args)
            send.assert_not_called()

    def test_nested_case_variant_secret_keys_are_collected_and_scrubbed(self) -> None:
        mod = self._mod()
        secrets = {
            "authorization": "nested-authorization-canary",
            "client": "nested-client-secret-canary",
            "private": "nested-private-key-canary",
            "session": "nested-session-id-canary",
            "oauth": "nested-oauth-token-canary",
        }
        cfg = mod.BridgeConfig(
            raw={
                "extensions": [
                    {"OAUTH_TOKEN": secrets["oauth"]},
                    {"safe_label": "visible-raw-label"},
                ]
            },
            secrets_path=None,
            zulip={
                "extensions": [
                    {"AUTHORIZATION": secrets["authorization"]},
                    {
                        "clientSecret": secrets["client"],
                        "safe_label": "visible-zulip-label",
                    },
                ]
            },
            telegram={
                "nested": {
                    "PRIVATE_KEY": secrets["private"],
                    "SESSION_ID": secrets["session"],
                    "safe_label": "visible-telegram-label",
                }
            },
        )

        collected = cfg.secret_values()
        self.assertEqual(set(collected), set(secrets.values()))
        rendered = json.dumps(cfg.redacted_view(), sort_keys=True)
        for secret in secrets.values():
            self.assertNotIn(secret, rendered)
        self.assertIn("visible-zulip-label", rendered)
        self.assertIn("visible-telegram-label", rendered)
        self.assertNotIn(
            secrets["oauth"],
            mod.redact_notify_text(" ".join(secrets.values()), cfg),
        )

    def test_main_send_and_arm_never_call_legacy_path_sync(self) -> None:
        from unittest import mock

        mod = self._mod()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            secrets = self._secrets(root)
            legacy_scripts = (
                root
                / ".openclaw"
                / "workspace"
                / "skills"
                / "aas-remote-bridge"
                / "scripts"
            )
            legacy_scripts.mkdir(parents=True)
            (legacy_scripts / "sync_remote_bridge_paths.py").write_text(
                "raise RuntimeError('legacy sync must not be imported')\n",
                encoding="utf-8",
            )
            env = {
                "HOME": str(root),
                "XDG_CONFIG_HOME": str(root / "config"),
                "AAS_REMOTE_BRIDGE_STATE": str(root / "state"),
                "AAS_REMOTE_BRIDGE_SYNC": "1",
            }

            with mock.patch.dict(
                mod.os.environ, env, clear=True
            ), mock.patch.object(
                mod,
                "_maybe_sync_openclaw_workspace_paths",
                side_effect=AssertionError("legacy sync was invoked"),
            ) as sync, redirect_stdout(io.StringIO()):
                send_result = mod.main(
                    [
                        "--secrets-file",
                        str(secrets),
                        "send",
                        "--text",
                        "safe notification",
                        "--dry-run",
                    ]
                )
                arm_result = mod.main(
                    [
                        "arm",
                        "--job",
                        "no-legacy-sync",
                        "--provider",
                        "codex",
                        "--cwd",
                        str(root),
                    ]
                )

            self.assertEqual(send_result, 0)
            self.assertEqual(arm_result, 0)
            sync.assert_not_called()

    def test_raw_send_fails_closed_when_redactor_is_unavailable(self) -> None:
        from argparse import Namespace
        from unittest import mock

        mod = self._mod()
        args = Namespace(
            secrets_file=None,
            event_json=None,
            text="outbound personal record",
            html=None,
            job="research-topic",
            channel="zulip",
            dry_run=True,
        )
        output = io.StringIO()
        with mock.patch.object(
            mod, "load_notify_v2_module", side_effect=RuntimeError("unavailable")
        ), mock.patch.object(mod, "notify_channels") as send, redirect_stdout(output):
            result = mod.cmd_send(args)
        self.assertEqual(result, 1)
        send.assert_not_called()
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["error_code"], "redaction_unavailable")

    def _event(self, mod):
        notify = mod.load_notify_v2_module()
        return notify.build_event(
            event="iteration_ok",
            event_id="evt-bridge-1",
            occurred_at="2026-07-29T12:00:00Z",
            finished_at="2026-07-29T12:00:00Z",
            title="Sample open question",
            topic_slug="sample-open-question",
            goal="Resolve the main open question.",
            completed="Banked a verified obstruction.",
            current="The bridge obligation remains open.",
            plan="Test the next registered direction.",
            iteration_status="success",
            loop_status="running",
            review_status="passed",
            iteration_number=4,
            spent_iterations=4,
            max_iterations=20,
            goal_progress="one bridge obligation discharged",
            executor="Claude",
            driver_agent={"name": "Claude", "model": "claude-fable-5"},
            panel_agents=["Codex", "CodeWhale"],
            other_agents=[
                {
                    "name": "Sage verifier",
                    "provider": "local",
                    "model": "SageMath 10",
                    "role": "certificate verifier",
                }
            ],
            compute=[{"service": "hetzner", "status": "succeeded"}],
        )

    def test_transport_endpoints_require_https_without_credentials_or_redirects(self) -> None:
        from unittest import mock
        from urllib.error import HTTPError

        mod = self._mod()
        for unsafe in (
            "http://example.invalid/api",
            "https://user:password" + chr(64) + "example.invalid/api",
            "https://example.invalid/has space",
        ):
            with self.subTest(unsafe=unsafe), self.assertRaises(ValueError):
                mod._validate_transport_endpoint(unsafe)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AAS_REMOTE_BRIDGE_ALLOW_HTTP_LOCALHOST", None)
            with self.assertRaises(ValueError):
                mod._validate_transport_endpoint("http://localhost:8080/api")
        with mock.patch.dict(
            os.environ, {"AAS_REMOTE_BRIDGE_ALLOW_HTTP_LOCALHOST": "1"}, clear=False
        ):
            self.assertEqual(
                mod._validate_transport_endpoint("http://127.0.0.1:8080/api"),
                "http://127.0.0.1:8080/api",
            )
        handler = mod._RejectTransportRedirects()
        with self.assertRaises(HTTPError):
            handler.redirect_request(
                type("RequestStub", (), {"full_url": "https://example.invalid/a"})(),
                None,
                302,
                "Found",
                {},
                "https://other.invalid/b",
            )

    @staticmethod
    def _send_args(event_path: Path):
        from argparse import Namespace

        return Namespace(
            secrets_file=None,
            event_json=str(event_path),
            text=None,
            html=None,
            job=None,
            channel="zulip",
            dry_run=False,
        )

    def test_event_json_path_uses_markdown_and_stable_zulip_topic(self) -> None:
        mod = self._mod()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            secrets = self._secrets(root)
            event_path = root / "event.json"
            event_path.write_text(json.dumps(self._event(mod)), encoding="utf-8")
            env = {
                "REMOTE_BRIDGE_SECRETS_FILE": str(secrets),
                "AAS_REMOTE_BRIDGE_STATE": str(root / "state"),
                "AAS_REMOTE_BRIDGE_SYNC": "0",
            }
            result = _run(
                "send",
                "--event-json",
                str(event_path),
                "--dry-run",
                env=env,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        data = json.loads(result.stdout)
        payload = data["results"]["zulip"]["payload"]
        self.assertEqual(payload["topic"], "job/sample-open-question")
        self.assertIn("**Goal**", payload["content"])
        self.assertIn("**Completed**", payload["content"])
        self.assertIn("**Driver agent**: Claude (claude-fable-5)", payload["content"])
        self.assertIn("**Panel agents**: Codex; CodeWhale", payload["content"])
        self.assertIn(
            "**Other agents**: Sage verifier (SageMath 10, certificate verifier)",
            payload["content"],
        )
        self.assertEqual(data["delivery"]["channel"], "zulip")
        self.assertFalse(data["delivery"]["delivered"])

    @unittest.skipUnless(os.name == "posix", "event no-follow probes require POSIX")
    def test_event_json_path_rejects_symlink_fifo_and_hardlink(self) -> None:
        mod = self._mod()
        for attack in ("symlink", "fifo", "hardlink"):
            with self.subTest(attack=attack), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                target = root / "target.json"
                target.write_text('{"safe":true}\n', encoding="utf-8")
                event_path = root / "event.json"
                if attack == "symlink":
                    event_path.symlink_to(target)
                elif attack == "fifo":
                    os.mkfifo(event_path)
                else:
                    os.link(target, event_path)

                with self.assertRaisesRegex(ValueError, "single-link regular"):
                    mod.load_event_json(str(event_path))

    def test_event_json_path_rejects_oversize_and_non_utf8_payloads(self) -> None:
        mod = self._mod()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            oversized = root / "oversized.json"
            oversized.write_bytes(b" " * (mod.EVENT_JSON_MAX_BYTES + 1))
            with self.assertRaisesRegex(ValueError, "exceeds 1 MiB"):
                mod.load_event_json(str(oversized))

            invalid = root / "invalid.json"
            invalid.write_bytes(b'{"value":"\xff"}')
            with self.assertRaisesRegex(ValueError, "not UTF-8"):
                mod.load_event_json(str(invalid))

    def test_event_json_stdin_has_an_independent_byte_bound(self) -> None:
        from unittest import mock

        mod = self._mod()
        source = io.StringIO("x" * (mod.EVENT_JSON_MAX_BYTES + 1))
        with mock.patch.object(mod.sys, "stdin", source), self.assertRaisesRegex(
            ValueError, "exceeds 1 MiB"
        ):
            mod.load_event_json("-")

    @unittest.skipUnless(os.name == "posix", "replacement probe uses descriptor reads")
    def test_event_json_path_replacement_during_read_is_rejected(self) -> None:
        from unittest import mock

        mod = self._mod()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event_path = root / "event.json"
            replacement = root / "replacement.json"
            event_path.write_text('{"value":"trusted"}\n', encoding="utf-8")
            replacement.write_text('{"value":"replacement"}\n', encoding="utf-8")
            real_read = mod.os.read
            swapped = False

            def read_then_replace(file_fd: int, count: int) -> bytes:
                nonlocal swapped
                payload = real_read(file_fd, count)
                if not swapped:
                    swapped = True
                    os.replace(replacement, event_path)
                return payload

            with mock.patch.object(mod.os, "read", side_effect=read_then_replace):
                with self.assertRaisesRegex(ValueError, "changed while reading"):
                    mod.load_event_json(str(event_path))

            self.assertEqual(
                json.loads(event_path.read_text(encoding="utf-8"))["value"],
                "replacement",
            )

    @unittest.skipUnless(os.name == "posix", "no-follow lock checks require POSIX")
    def test_notification_lock_rejects_planted_symlink_and_hardlink(self) -> None:
        mod = self._mod()
        for attack in ("symlink", "hardlink"):
            with self.subTest(attack=attack), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                mailbox = mod.Mailbox(root / "state")
                lock = mod.NotificationDeliveryLock("a" * 64, mailbox)
                lock.path.parent.mkdir(parents=True)
                victim = root / "victim.txt"
                original = b"must remain unchanged\n"
                victim.write_bytes(original)
                if attack == "symlink":
                    lock.path.symlink_to(victim)
                else:
                    os.link(victim, lock.path)

                with self.assertRaises(OSError):
                    with lock:
                        self.fail("unsafe planted lock was accepted")

                self.assertEqual(victim.read_bytes(), original)

    @unittest.skipUnless(os.name == "posix", "no-follow registry checks require POSIX")
    def test_notification_registry_rejects_linked_leaf_and_temp_without_victim_write(self) -> None:
        from unittest import mock

        mod = self._mod()
        for location in ("leaf", "temp"):
            for attack in ("symlink", "hardlink"):
                with (
                    self.subTest(location=location, attack=attack),
                    tempfile.TemporaryDirectory() as tmp,
                ):
                    root = Path(tmp)
                    mailbox = mod.Mailbox(root / "state")
                    mailbox.ensure()
                    victim = root / "victim.txt"
                    original = b"must remain unchanged\n"
                    victim.write_bytes(original)
                    registry = mod._notify_delivery_path(mailbox)
                    planted = registry
                    patcher = None
                    if location == "temp":
                        fixed = type("FixedUuid", (), {"hex": "f" * 32})()
                        planted = registry.parent / (
                            f".{registry.name}.{fixed.hex}.tmp"
                        )
                        patcher = mock.patch.object(
                            mod.uuid, "uuid4", return_value=fixed
                        )
                    if attack == "symlink":
                        planted.symlink_to(victim)
                    else:
                        os.link(victim, planted)
                    context = patcher if patcher is not None else mock.patch.object(
                        mod.uuid, "uuid4", wraps=mod.uuid.uuid4
                    )
                    with context, self.assertRaises(OSError):
                        mod._secure_notification_registry_write(
                            mailbox,
                            {"schema_version": "1.0", "deliveries": {}},
                        )
                    self.assertEqual(victim.read_bytes(), original)

    @unittest.skipUnless(os.name == "posix", "no-follow registry checks require POSIX")
    def test_notification_registry_rejects_symlinked_parent(self) -> None:
        mod = self._mod()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state"
            state.mkdir(mode=0o700)
            victim_dir = root / "victim-dir"
            victim_dir.mkdir()
            sentinel = victim_dir / "sentinel.txt"
            sentinel.write_bytes(b"unchanged\n")
            (state / "bridge").symlink_to(victim_dir, target_is_directory=True)
            mailbox = mod.Mailbox(state)

            with self.assertRaises(OSError):
                mod._secure_notification_registry_write(
                    mailbox,
                    {"schema_version": "1.0", "deliveries": {}},
                )

            self.assertEqual(sentinel.read_bytes(), b"unchanged\n")
            self.assertFalse((victim_dir / "notify_deliveries.json").exists())

    @unittest.skipUnless(os.name == "posix", "replacement race probe uses dir-fd replace")
    def test_notification_registry_leaf_replacement_race_does_not_modify_victim(self) -> None:
        from unittest import mock

        mod = self._mod()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mailbox = mod.Mailbox(root / "state")
            mailbox.ensure()
            registry = mod._notify_delivery_path(mailbox)
            registry.write_text(
                '{"schema_version":"1.0","deliveries":{}}\n', encoding="utf-8"
            )
            os.chmod(registry, 0o600)
            victim = root / "victim.txt"
            original = b"must remain unchanged\n"
            victim.write_bytes(original)
            real_replace = mod.os.replace

            def replace_after_swap(src, dst, **kwargs):
                registry.unlink()
                os.link(victim, registry)
                return real_replace(src, dst, **kwargs)

            with mock.patch.object(mod.os, "replace", side_effect=replace_after_swap):
                mod._secure_notification_registry_write(
                    mailbox,
                    {"schema_version": "1.0", "deliveries": {"a": {}}},
                )

            self.assertEqual(victim.read_bytes(), original)
            self.assertEqual(registry.stat().st_nlink, 1)
            self.assertIn('"a"', registry.read_text(encoding="utf-8"))

    def test_direct_delivery_registry_call_does_not_persist_caller_identifiers(self) -> None:
        mod = self._mod()
        with tempfile.TemporaryDirectory() as tmp:
            mailbox = mod.Mailbox(Path(tmp) / "state")
            secret = "notify-secret-sentinel-92731"
            mod.remember_notification_delivery(
                "a" * 64,
                event_id=f"evt-{secret}",
                channel=f"zulip-{secret}",
                mailbox=mailbox,
                retry_fingerprint_value="b" * 64,
            )

            payload = mod._notify_delivery_path(mailbox).read_text(encoding="utf-8")
            self.assertNotIn(secret, payload)
            self.assertNotIn("evt-", payload)
            self.assertIn('"channel": "unknown"', payload)

    def test_event_dry_run_redacts_transport_secrets_from_output(self) -> None:
        mod = self._mod()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            secrets = self._secrets(root)
            event = self._event(mod)
            event["event_id"] = "evt-not-a-real-key"
            event["unrendered_extension"] = {"secret": "not-a-real-key"}
            event["research"]["title"] += " not-a-real-key"
            event["sections"]["goal"] += " 1:not-a-real-token"
            event["sections"]["completed"] += " not-a-real-key"
            event["sections"]["current"] += " 1:not-a-real-token"
            event["sections"]["plan"] += " not-a-real-key"
            event["agents"]["driver"]["agents"][0]["detail"] = "1:not-a-real-token"
            event["compute"]["runs"][0]["job_ref"] = "not-a-real-key"
            event_path = root / "event-secret.json"
            event_path.write_text(json.dumps(event), encoding="utf-8")
            result = _run(
                "send",
                "--event-json",
                str(event_path),
                "--dry-run",
                env={
                    "REMOTE_BRIDGE_SECRETS_FILE": str(secrets),
                    "AAS_REMOTE_BRIDGE_STATE": str(root / "state"),
                    "AAS_REMOTE_BRIDGE_SYNC": "0",
                },
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("not-a-real-key", result.stdout)
        self.assertNotIn("1:not-a-real-token", result.stdout)
        payload = json.loads(result.stdout)["results"]["zulip"]["payload"]
        # Zulip CommonMark: do not backslash-escape brackets around REDACTED
        # (literal \[ looks wrong in the client). Plain [REDACTED] is correct.
        self.assertIn("[REDACTED]", payload["content"])
        self.assertNotIn(r"\[REDACTED\]", payload["content"])
        self.assertIn("Claude", payload["content"])
        self.assertIn("Hetzner", payload["content"])

    def test_raw_notify_dry_run_redacts_personal_data_from_return_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            secrets = self._secrets(root)
            email = "participant" + chr(64) + "example.invalid"
            phone = "+1 (202) 555-0187"
            result = _run(
                "send",
                "--text",
                f"participant_email: {email}; phone: {phone}",
                "--dry-run",
                env={
                    "REMOTE_BRIDGE_SECRETS_FILE": str(secrets),
                    "AAS_REMOTE_BRIDGE_STATE": str(root / "state"),
                    "AAS_REMOTE_BRIDGE_SYNC": "0",
                },
            )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn(email, result.stdout)
        self.assertNotIn(phone, result.stdout)
        self.assertIn("[REDACTED_PII]", result.stdout)

    def test_external_job_topic_is_redacted_and_control_characters_removed(self) -> None:
        mod = self._mod()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            secrets = self._secrets(root)
            event_path = root / "event.json"
            event_path.write_text(json.dumps(self._event(mod)), encoding="utf-8")
            result = _run(
                "send",
                "--event-json",
                str(event_path),
                "--job",
                "topic-not-a-real-key\nforged",
                "--dry-run",
                env={
                    "REMOTE_BRIDGE_SECRETS_FILE": str(secrets),
                    "AAS_REMOTE_BRIDGE_STATE": str(root / "state"),
                    "AAS_REMOTE_BRIDGE_SYNC": "0",
                },
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("not-a-real-key", result.stdout)
        payload = json.loads(result.stdout)["results"]["zulip"]["payload"]
        self.assertNotIn("\n", payload["topic"])
        self.assertIn("[REDACTED]", payload["topic"])

    def test_invalid_event_exception_is_redacted(self) -> None:
        from unittest import mock

        mod = self._mod()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            secrets = self._secrets(root)
            event_path = root / "event.json"
            event_path.write_text(json.dumps(self._event(mod)), encoding="utf-8")
            args = self._send_args(event_path)
            args.secrets_file = str(secrets)
            notify = mod.load_notify_v2_module()
            output = io.StringIO()
            with mock.patch.object(
                notify,
                "ensure_event",
                side_effect=ValueError("invalid not-a-real-key"),
            ), redirect_stdout(output):
                result = mod.cmd_send(args)
        self.assertEqual(result, 1)
        self.assertNotIn("not-a-real-key", output.getvalue())
        self.assertIn("[REDACTED]", output.getvalue())

    def test_event_json_stdin_uses_bounded_telegram_html(self) -> None:
        mod = self._mod()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            secrets = self._secrets(root)
            env = _subprocess_env(
                {
                    "REMOTE_BRIDGE_SECRETS_FILE": str(secrets),
                    "AAS_REMOTE_BRIDGE_STATE": str(root / "state"),
                    "AAS_REMOTE_BRIDGE_SYNC": "0",
                }
            )
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(RB),
                    "send",
                    "--event-json",
                    "-",
                    "--channel",
                    "telegram",
                    "--dry-run",
                ],
                input=json.dumps(self._event(mod)),
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        data = json.loads(result.stdout)
        telegram = data["results"]["telegram"]
        self.assertEqual(telegram["parse_mode"], "HTML")
        self.assertFalse(telegram["html_fallback_to_plain"])
        self.assertIn("<b>", telegram["preview"])

    def test_oversized_raw_html_degrades_before_chunking(self) -> None:
        mod = self._mod()
        cfg = mod.BridgeConfig(
            raw={},
            secrets_path=None,
            telegram={"bot_token": "1:token", "allowed_chat_ids": ["123"]},
        )
        result = mod.telegram_send(
            cfg,
            chat_id="123",
            text="<b>" + ("x" * 8000) + "</b>",
            parse_mode="HTML",
            dry_run=True,
        )
        self.assertIsNone(result["parse_mode"])
        self.assertTrue(result["html_fallback_to_plain"])
        self.assertGreater(result["chunks"], 1)
        self.assertNotIn("<b>", result["preview"])

    def test_dedupe_is_recorded_only_after_confirmed_delivery(self) -> None:
        from argparse import Namespace
        from unittest import mock

        mod = self._mod()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event_path = root / "event.json"
            event_path.write_text(json.dumps(self._event(mod)), encoding="utf-8")
            env = {
                "AAS_REMOTE_BRIDGE_STATE": str(root / "state"),
                "AAS_REMOTE_BRIDGE_SYNC": "0",
            }
            args = Namespace(
                secrets_file=None,
                event_json=str(event_path),
                text=None,
                html=None,
                job=None,
                channel="zulip",
                dry_run=False,
            )
            with mock.patch.dict(os.environ, env, clear=False), mock.patch.object(
                mod,
                "notify_channels",
                return_value={"zulip": {"ok": True, "channel": "zulip"}},
            ) as send, redirect_stdout(io.StringIO()):
                self.assertEqual(mod.cmd_send(args), 0)
                self.assertEqual(mod.cmd_send(args), 0)
            self.assertEqual(send.call_count, 1)

        # A failed transport is retried because no delivery was recorded.
        mod = self._mod()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event_path = root / "event.json"
            event_path.write_text(json.dumps(self._event(mod)), encoding="utf-8")
            env = {
                "AAS_REMOTE_BRIDGE_STATE": str(root / "state"),
                "AAS_REMOTE_BRIDGE_SYNC": "0",
            }
            args = Namespace(
                secrets_file=None,
                event_json=str(event_path),
                text=None,
                html=None,
                job=None,
                channel="zulip",
                dry_run=False,
            )
            with mock.patch.dict(os.environ, env, clear=False), mock.patch.object(
                mod,
                "notify_channels",
                return_value={"zulip": {"ok": False, "error": "offline"}},
            ) as send, redirect_stdout(io.StringIO()):
                self.assertEqual(mod.cmd_send(args), 1)
                self.assertEqual(mod.cmd_send(args), 1)
            self.assertEqual(send.call_count, 2)

    def test_same_event_id_with_changed_body_is_delivered_again(self) -> None:
        from unittest import mock

        mod = self._mod()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event_path = root / "event.json"
            event = self._event(mod)
            event_path.write_text(json.dumps(event), encoding="utf-8")
            env = {
                "AAS_REMOTE_BRIDGE_STATE": str(root / "state"),
                "AAS_REMOTE_BRIDGE_SYNC": "0",
            }
            args = self._send_args(event_path)
            with mock.patch.dict(os.environ, env, clear=False), mock.patch.object(
                mod,
                "notify_channels",
                return_value={"zulip": {"ok": True, "channel": "zulip"}},
            ) as send, mock.patch.object(mod, "_emit"):
                self.assertEqual(mod.cmd_send(args), 0)
                event["sections"]["current"] = (
                    "The same event now reports a materially changed current state."
                )
                event_path.write_text(json.dumps(event), encoding="utf-8")
                self.assertEqual(mod.cmd_send(args), 0)
            self.assertEqual(send.call_count, 2)

    def test_concurrent_identical_event_is_delivered_exactly_once(self) -> None:
        from unittest import mock

        mod = self._mod()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event_path = root / "event.json"
            event_path.write_text(json.dumps(self._event(mod)), encoding="utf-8")
            env = {
                "AAS_REMOTE_BRIDGE_STATE": str(root / "state"),
                "AAS_REMOTE_BRIDGE_SYNC": "0",
            }
            args = self._send_args(event_path)
            entered = threading.Event()

            def send_once(_cfg, **_kwargs):
                entered.set()
                time.sleep(0.08)
                return {"zulip": {"ok": True, "channel": "zulip"}}

            results: list[int] = []
            errors: list[BaseException] = []

            def invoke() -> None:
                try:
                    results.append(mod.cmd_send(args))
                except BaseException as exc:  # pragma: no cover - assertion aid
                    errors.append(exc)

            with mock.patch.dict(os.environ, env, clear=False), mock.patch.object(
                mod, "notify_channels", side_effect=send_once
            ) as send, mock.patch.object(mod, "_emit"):
                first = threading.Thread(target=invoke)
                second = threading.Thread(target=invoke)
                first.start()
                self.assertTrue(entered.wait(timeout=2))
                second.start()
                first.join(timeout=3)
                second.join(timeout=3)

            self.assertFalse(first.is_alive())
            self.assertFalse(second.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(sorted(results), [0, 0])
            self.assertEqual(send.call_count, 1)

    def test_concurrent_timestamp_variant_retries_across_processes_deliver_once(self) -> None:
        mod = self._mod()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first_event = self._event(mod)
            first_event["iteration"]["started_at"] = "2026-07-29T11:58:00Z"
            first_event["compute"]["runs"][0].update(
                {
                    "started_at": "2026-07-29T11:58:10Z",
                    "finished_at": "2026-07-29T11:59:10Z",
                    "duration_seconds": 60,
                }
            )
            retry_event = copy.deepcopy(first_event)
            retry_event["event_id"] = "evt-bridge-1-retry"
            retry_event["occurred_at"] = "2026-07-29T12:02:00Z"
            retry_event["iteration"].update(
                {
                    "started_at": "2026-07-29T11:59:00Z",
                    "finished_at": "2026-07-29T12:02:00Z",
                    "duration_seconds": 180,
                }
            )
            retry_event["compute"]["runs"][0].update(
                {
                    "started_at": "2026-07-29T11:59:20Z",
                    "finished_at": "2026-07-29T12:01:20Z",
                    "duration_seconds": 120,
                }
            )
            notify = mod.load_notify_v2_module()
            self.assertNotEqual(
                notify.delivery_fingerprint(first_event),
                notify.delivery_fingerprint(retry_event),
            )
            self.assertEqual(
                notify.retry_fingerprint(first_event),
                notify.retry_fingerprint(retry_event),
            )

            first_path = root / "first-event.json"
            retry_path = root / "retry-event.json"
            counter_path = root / "transport-calls.txt"
            first_path.write_text(json.dumps(first_event), encoding="utf-8")
            retry_path.write_text(json.dumps(retry_event), encoding="utf-8")
            worker = r"""
import argparse
import importlib.util
import os
from pathlib import Path
import sys
import time

bridge_path = Path(sys.argv[1])
event_path = Path(sys.argv[2])
counter_path = Path(sys.argv[3])
spec = importlib.util.spec_from_file_location("aas_remote_bridge_worker", bridge_path)
assert spec is not None and spec.loader is not None
bridge = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = bridge
spec.loader.exec_module(bridge)

def fake_notify(_cfg, **_kwargs):
    fd = os.open(counter_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, b"send\n")
    finally:
        os.close(fd)
    deadline = time.monotonic() + 0.8
    while time.monotonic() < deadline:
        try:
            if counter_path.read_text(encoding="utf-8").count("send\n") >= 2:
                break
        except OSError:
            pass
        time.sleep(0.01)
    return {"zulip": {"ok": True, "channel": "zulip"}}

bridge.notify_channels = fake_notify
args = argparse.Namespace(
    secrets_file=None,
    event_json=str(event_path),
    text=None,
    html=None,
    job=None,
    channel="zulip",
    dry_run=False,
)
raise SystemExit(bridge.cmd_send(args))
"""
            env = os.environ.copy()
            env.update(
                {
                    "AAS_REMOTE_BRIDGE_STATE": str(root / "state"),
                    "AAS_REMOTE_BRIDGE_SYNC": "0",
                    "PYTHONDONTWRITEBYTECODE": "1",
                }
            )
            processes = [
                subprocess.Popen(
                    [
                        sys.executable,
                        "-B",
                        "-c",
                        worker,
                        str(RB),
                        str(path),
                        str(counter_path),
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=env,
                )
                for path in (first_path, retry_path)
            ]
            outputs = [process.communicate(timeout=5) for process in processes]

            for process, (stdout, stderr) in zip(processes, outputs):
                self.assertEqual(process.returncode, 0, stdout + stderr)
            calls = counter_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(calls, ["send"])


class RemoteBridgeNotifyDirectoryChain(unittest.TestCase):
    """Windows validates notification directories instead of pinning them.

    ``os.open`` refuses a directory on Windows, so that platform walks every
    component with ``lstat``. The walk is exercised directly here: ``os.name``
    cannot be patched, because ``pathlib`` dispatches on it and cannot build a
    ``WindowsPath`` on a POSIX host.
    """

    def _mod(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location("aas_remote_bridge_chain", RB)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        return mod

    def test_missing_components_are_created(self) -> None:
        mod = self._mod()
        with tempfile.TemporaryDirectory() as tmp:
            leaf = Path(tmp) / "bridge" / "notify_locks"
            mod._ensure_real_directory(leaf, create=True)
            self.assertTrue(leaf.is_dir())

    @unittest.skipUnless(os.name == "posix", "POSIX directory mode semantics")
    def test_created_components_are_owner_private(self) -> None:
        mod = self._mod()
        with tempfile.TemporaryDirectory() as tmp:
            leaf = Path(tmp) / "bridge" / "notify_locks"
            mod._ensure_real_directory(leaf, create=True)
            for created in (leaf.parent, leaf):
                self.assertEqual(stat.S_IMODE(created.stat().st_mode), 0o700)

    @unittest.skipUnless(os.name == "posix", "requires POSIX symlink semantics")
    def test_symlinked_component_is_rejected(self) -> None:
        mod = self._mod()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "real").mkdir()
            (root / "link").symlink_to(root / "real", target_is_directory=True)
            with self.assertRaisesRegex(OSError, "notification directory is unsafe"):
                mod._ensure_real_directory(root / "link" / "notify_locks", create=True)

    def test_regular_file_component_is_rejected(self) -> None:
        mod = self._mod()
        with tempfile.TemporaryDirectory() as tmp:
            regular = Path(tmp) / "regular"
            regular.write_text("payload", encoding="utf-8")
            with self.assertRaisesRegex(OSError, "notification directory is unsafe"):
                mod._ensure_real_directory(regular / "notify_locks", create=True)

    def test_missing_component_without_create_is_not_found(self) -> None:
        mod = self._mod()
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                mod._ensure_real_directory(Path(tmp) / "absent" / "leaf", create=False)

    def test_chain_check_leaves_no_open_descriptor(self) -> None:
        mod = self._mod()
        with tempfile.TemporaryDirectory() as tmp:
            leaf = Path(tmp) / "bridge" / "notify_locks"
            probe = os.open(os.devnull, os.O_RDONLY)
            os.close(probe)
            mod._ensure_notify_directory_chain(leaf, create=True)
            self.assertTrue(leaf.is_dir())
            reopened = os.open(os.devnull, os.O_RDONLY)
            os.close(reopened)
            # A leaked pin would shift the lowest free descriptor number.
            self.assertEqual(probe, reopened)


class _MissingLockLeaf:
    """Report the lock leaf as missing for the first ``failures`` opens.

    Two senders that race to create one lock leaf can be told the leaf does not
    exist even though ``O_CREAT`` was requested; macOS does this when a retry
    and its original delivery run at the same moment. That schedule cannot be
    forced from a test, so the injector raises the same ``ENOENT`` the lock has
    to survive and leaves every other open alone.
    """

    def __init__(self, leaf_name: str, *, failures: int) -> None:
        self.leaf_name = leaf_name
        self.remaining = failures
        self.attempts = 0
        self._real_open = os.open

    def __call__(self, path: object, *args: object, **kwargs: object) -> int:
        if os.path.basename(str(path)) == self.leaf_name:
            self.attempts += 1
            if self.remaining > 0:
                self.remaining -= 1
                raise FileNotFoundError(2, "No such file or directory", str(path))
        return self._real_open(path, *args, **kwargs)  # type: ignore[arg-type]


class RemoteBridgeNotificationLockRetry(unittest.TestCase):
    """The delivery lock survives a leaf that transiently reports ENOENT."""

    def _mod(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location("aas_remote_bridge_lock", RB)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        return mod

    def _lock(self, mod, root: Path):
        return mod.NotificationDeliveryLock("a" * 64, mod.Mailbox(root))

    def test_transient_missing_leaf_is_retried_until_the_lock_is_held(self) -> None:
        from unittest import mock

        mod = self._mod()
        with tempfile.TemporaryDirectory() as tmp:
            lock = self._lock(mod, Path(tmp))
            injector = _MissingLockLeaf(lock.path.name, failures=3)
            with mock.patch.object(mod.os, "open", injector):
                with lock:
                    pass
            self.assertEqual(injector.attempts, 4)
            self.assertTrue(lock.path.is_file())

    def test_persistently_missing_leaf_fails_closed_without_leaking(self) -> None:
        from unittest import mock

        mod = self._mod()
        with tempfile.TemporaryDirectory() as tmp:
            lock = self._lock(mod, Path(tmp))
            injector = _MissingLockLeaf(lock.path.name, failures=10**6)
            probe = os.open(os.devnull, os.O_RDONLY)
            os.close(probe)
            with mock.patch.object(mod, "NOTIFY_LOCK_OPEN_TIMEOUT_SECONDS", 0.1):
                with mock.patch.object(mod.os, "open", injector):
                    with self.assertRaisesRegex(
                        OSError, "timed out opening notification lock"
                    ):
                        with lock:
                            pass
            self.assertGreater(injector.attempts, 1)
            reopened = os.open(os.devnull, os.O_RDONLY)
            os.close(reopened)
            # A pinned parent left open by a failed attempt would shift this.
            self.assertEqual(probe, reopened)

    def test_other_open_failures_are_not_retried(self) -> None:
        from unittest import mock

        mod = self._mod()
        with tempfile.TemporaryDirectory() as tmp:
            lock = self._lock(mod, Path(tmp))
            attempts = 0
            real_open = os.open

            def refuse(path: object, *args: object, **kwargs: object) -> int:
                nonlocal attempts
                if os.path.basename(str(path)) == lock.path.name:
                    attempts += 1
                    raise PermissionError(13, "Permission denied", str(path))
                return real_open(path, *args, **kwargs)  # type: ignore[arg-type]

            with mock.patch.object(mod.os, "open", refuse):
                with self.assertRaises(PermissionError):
                    with lock:
                        pass
            self.assertEqual(attempts, 1)


if __name__ == "__main__":
    unittest.main()
