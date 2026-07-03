from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from installer.ai_agents_skills.runtime import RUNTIME_SOURCE_ROOT

SKILL_DIR = RUNTIME_SOURCE_ROOT / "skills" / "send-email"


def _import_send_email():
    prev = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(SKILL_DIR))
    try:
        import send_email  # noqa: PLC0415
        return send_email
    finally:
        sys.path.remove(str(SKILL_DIR))
        sys.dont_write_bytecode = prev


def _write_json(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class SendEmailConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.se = _import_send_email()

    def _run(self, argv: list[str]) -> tuple[int, dict]:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = self.se.main(argv)
        return rc, json.loads(buf.getvalue())

    def test_cli_secrets_file_wins_over_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cli = _write_json(root / "cli.json", {"smtp": {"host": "cli.example"}})
            env = _write_json(root / "env.json", {"smtp": {"host": "env.example"}})
            with mock.patch.dict(os.environ, {"SEND_EMAIL_SECRETS_FILE": str(env)}, clear=True):
                cfg = self.se.load_config(self.se._selftest_namespace(secrets_file=str(cli)))
        self.assertEqual(cfg.host, "cli.example")
        self.assertEqual(cfg.secrets_source, "cli")
        self.assertEqual(cfg.secrets_file, str(cli))

    def test_send_email_env_file_wins_over_platform_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_file = _write_json(root / "mail.json", {"smtp": {"host": "env.example"}})
            default = _write_json(root / "xdg" / "send-email" / "secrets.json",
                                  {"smtp": {"host": "default.example"}})
            with mock.patch.dict(os.environ, {
                "SEND_EMAIL_SECRETS_FILE": str(env_file),
                "XDG_CONFIG_HOME": str(default.parents[1]),
                "HOME": str(root / "home"),
            }, clear=True):
                cfg = self.se.load_config(self.se._selftest_namespace())
        self.assertEqual(cfg.host, "env.example")
        self.assertEqual(cfg.secrets_source, "SEND_EMAIL_SECRETS_FILE")

    def test_runtime_skill_local_file_is_used(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "workspace" / "skills" / "send-email"
            _write_json(skill_dir / "secrets.json", {"smtp": {"host": "skill.example"}})
            with mock.patch.object(self.se, "__file__", str(skill_dir / "send_email.py")):
                with mock.patch.dict(os.environ, {"HOME": str(Path(tmp) / "home")}, clear=True):
                    cfg = self.se.load_config(self.se._selftest_namespace())
        self.assertEqual(cfg.host, "skill.example")
        self.assertEqual(cfg.secrets_source, "runtime_skill_default")

    def test_platform_default_file_is_used(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            default = _write_json(root / "xdg" / "send-email" / "secrets.json",
                                  {"smtp": {"host": "xdg.example"}})
            with mock.patch.dict(os.environ, {
                "XDG_CONFIG_HOME": str(default.parents[1]),
                "HOME": str(root / "home"),
            }, clear=True):
                cfg = self.se.load_config(self.se._selftest_namespace())
        self.assertEqual(cfg.host, "xdg.example")
        self.assertEqual(cfg.secrets_source, "platform_default")

    def test_legacy_shared_file_is_used_only_when_send_email_shaped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shaped = _write_json(root / "shared.json", {"smtp": {"host": "shared.example"}})
            unrelated = _write_json(root / "zotero.json", {"zotero": {"library_id": "1"}})
            with mock.patch.dict(os.environ, {
                "AAS_SECRETS_FILE": str(shaped),
                "HOME": str(root / "home1"),
            }, clear=True):
                cfg = self.se.load_config(self.se._selftest_namespace())
            with mock.patch.dict(os.environ, {
                "AAS_SECRETS_FILE": str(unrelated),
                "HOME": str(root / "home2"),
            }, clear=True):
                ignored = self.se.load_config(self.se._selftest_namespace())
        self.assertEqual(cfg.host, "shared.example")
        self.assertEqual(cfg.secrets_source, "AAS_SECRETS_FILE")
        self.assertIsNone(ignored.host)
        self.assertEqual(ignored.secrets_source, "none")

    def test_explicit_invalid_json_returns_config_error_without_secret_contents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.json"
            bad.write_text('{"smtp": {"password": "CANARY_SECRET"', encoding="utf-8")
            with mock.patch.dict(os.environ, {}, clear=True):
                rc, out = self._run(["show-config", "--secrets-file", str(bad)])
        self.assertEqual(rc, 1)
        self.assertEqual(out["error_code"], "config_error")
        self.assertNotIn("CANARY_SECRET", json.dumps(out))

    def test_show_config_and_accounts_do_not_print_password_or_passphrase(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            secrets = _write_json(Path(tmp) / "secrets.json", {
                "default_account": "work",
                "accounts": {
                    "work": {
                        "host": "smtp.example",
                        "user": "user",
                        "password": "CANARY_PASSWORD",
                        "from": "sender@example",
                        "pgp_passphrase": "CANARY_PASSPHRASE",
                    }
                },
            })
            with mock.patch.dict(os.environ, {}, clear=True):
                show_rc, show = self._run(["show-config", "--secrets-file", str(secrets)])
                accounts_rc, accounts = self._run(["accounts", "--secrets-file", str(secrets)])
        combined = json.dumps([show, accounts])
        self.assertEqual(show_rc, 0)
        self.assertEqual(accounts_rc, 0)
        self.assertTrue(show["password_set"])
        self.assertEqual(accounts["accounts"], ["work"])
        self.assertNotIn("CANARY_PASSWORD", combined)
        self.assertNotIn("CANARY_PASSPHRASE", combined)

    def test_cli_env_and_secret_value_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            secrets = _write_json(Path(tmp) / "secrets.json", {"smtp": {"host": "file.example"}})
            with mock.patch.dict(os.environ, {"SMTP_HOST": "env.example"}, clear=True):
                env_cfg = self.se.load_config(self.se._selftest_namespace(secrets_file=str(secrets)))
                cli_cfg = self.se.load_config(self.se._selftest_namespace(
                    secrets_file=str(secrets), host="cli.example"
                ))
        self.assertEqual(env_cfg.host, "env.example")
        self.assertEqual(cli_cfg.host, "cli.example")

    def test_address_book_prefers_runtime_workspace_over_secrets_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            secrets = _write_json(root / "cfg" / "secrets.json", {"smtp": {"host": "h"}})
            workspace = root / "runtime" / "workspace"
            with mock.patch.dict(os.environ, {
                "SEND_EMAIL_SECRETS_FILE": str(secrets),
                "AAS_RUNTIME_WORKSPACE": str(workspace),
            }, clear=True):
                path = self.se._address_book_path()
        self.assertEqual(path, workspace / ".address-book.json")

    def test_documented_platform_default_candidates(self) -> None:
        with mock.patch.object(self.se.sys, "platform", "darwin"):
            with mock.patch.dict(os.environ, {"HOME": "/Users/u", "XDG_CONFIG_HOME": "/tmp/xdg"},
                                 clear=True):
                mac = [str(path) for path in self.se._platform_default_secret_paths()]
        with mock.patch.object(self.se.sys, "platform", "win32"):
            with mock.patch.dict(os.environ, {
                "APPDATA": "C:/Users/u/AppData/Roaming",
                "LOCALAPPDATA": "C:/Users/u/AppData/Local",
            }, clear=True):
                win = [str(path).replace("\\", "/") for path in self.se._platform_default_secret_paths()]
        self.assertEqual(mac, [
            "/tmp/xdg/send-email/secrets.json",
            "/Users/u/Library/Application Support/send-email/secrets.json",
            "/Users/u/.config/send-email/secrets.json",
        ])
        self.assertEqual(win, [
            "C:/Users/u/AppData/Roaming/send-email/secrets.json",
            "C:/Users/u/AppData/Local/send-email/secrets.json",
        ])


if __name__ == "__main__":
    unittest.main()
