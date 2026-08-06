"""Tests for the default scripted force-loop kit."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
FORCE_LOOP = (
    REPO
    / "canonical"
    / "runtime"
    / "skills"
    / "autonomous-research-loop-runtime"
    / "force-loop"
)


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class LoadLoopEnvTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.env = _load("force_loop_load_env", FORCE_LOOP / "load_loop_env.py")

    def test_parse_exact_policy_keys(self) -> None:
        got = self.env.parse_env_text(
            "AAS_AUTOLOOP_GOAL_PRIORITY=on\n# c\nAAS_AUTOLOOP_NOTIFY=auto\n"
        )
        self.assertEqual(
            got,
            {"AAS_AUTOLOOP_GOAL_PRIORITY": "on", "AAS_AUTOLOOP_NOTIFY": "auto"},
        )

    def test_reject_injection(self) -> None:
        with self.assertRaises(self.env.EnvLoadError):
            self.env.parse_env_text("AAS_AUTOLOOP_NOTIFY=$(whoami)\n")
        with self.assertRaises(self.env.EnvLoadError):
            self.env.parse_env_text("AAS_AUTOLOOP_NOTIFY=`id`\n")
        with self.assertRaises(self.env.EnvLoadError):
            self.env.parse_env_text("bad-key=1\n")
        with self.assertRaises(self.env.EnvLoadError):
            self.env.parse_env_text("NOEQ\n")

    def test_rejects_unknown_duplicate_and_quoted_values(self) -> None:
        for body in (
            "FOO=bar\n",
            "AAS_AUTOLOOP_NOTIFY=auto\nAAS_AUTOLOOP_NOTIFY=on\n",
            'AAS_AUTOLOOP_NOTIFY="auto"\n',
        ):
            with self.subTest(body=body), self.assertRaises(self.env.EnvLoadError):
                self.env.parse_env_text(body)

    def test_crlf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "e.env"
            p.write_bytes(b"AAS_AUTOLOOP_GOAL_PRIORITY=on\r\nAAS_AUTOLOOP_NOTIFY=auto\r\n")
            p.chmod(0o600)
            got = self.env.load_env_file(p)
            self.assertEqual(got["AAS_AUTOLOOP_NOTIFY"], "auto")

    def test_rejects_policy_inside_loop_and_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            loop = root / "loop"
            loop.mkdir()
            policy = loop / "policy.env"
            policy.write_text("AAS_AUTOLOOP_NOTIFY=auto\n", encoding="utf-8")
            policy.chmod(0o600)
            with self.assertRaises(self.env.EnvLoadError):
                self.env.load_env_file(policy, forbidden_root=loop)
            outside = root / "outside.env"
            outside.write_text("AAS_AUTOLOOP_NOTIFY=auto\n", encoding="utf-8")
            outside.chmod(0o600)
            link = root / "linked.env"
            link.symlink_to(outside)
            with self.assertRaises(self.env.EnvLoadError):
                self.env.load_env_file(link, forbidden_root=loop)

    @unittest.skipUnless(os.name == "posix", "descriptor mutation checks are POSIX-only")
    def test_rejects_policy_mutated_during_descriptor_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            policy = Path(tmp) / "host-policy.env"
            policy.write_text("AAS_AUTOLOOP_NOTIFY=auto\n", encoding="utf-8")
            policy.chmod(0o600)
            original_read = self.env.os.read
            mutated = False

            def racing_read(fd: int, size: int) -> bytes:
                nonlocal mutated
                chunk = original_read(fd, size)
                if chunk and not mutated:
                    mutated = True
                    policy.write_text(
                        "AAS_AUTOLOOP_NOTIFY=on\nAAS_AUTOLOOP_GOAL_PRIORITY=on\n",
                        encoding="utf-8",
                    )
                    policy.chmod(0o600)
                return chunk

            with mock.patch.object(self.env.os, "read", side_effect=racing_read):
                with self.assertRaises(self.env.EnvLoadError) as raised:
                    self.env.load_env_file(policy)
            self.assertIn("changed while reading", str(raised.exception))


class ApplyDefaultsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.apply = _load(
            "force_loop_apply", FORCE_LOOP / "apply_force_loop_defaults.py"
        )

    def test_formal_apply_has_enforce_hard_notify(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loop = Path(tmp) / "loop"
            policy = Path(tmp) / "host-policy.env"
            result = self.apply.apply_defaults(loop, profile="formal", policy_file=policy)
            self.assertTrue(result["ok"], result)
            gp = json.loads((loop / "goal_priority.json").read_text(encoding="utf-8"))
            self.assertTrue(gp["enabled"])
            self.assertEqual(gp["discipline_mode"], "hard")
            state = json.loads((loop / "loop_state.json").read_text(encoding="utf-8"))
            so = state["standing_orders"]
            self.assertEqual(so["goal_focus"]["mode"], "enforce")
            self.assertEqual(so["goal_priority"]["discipline_mode"], "hard")
            self.assertIn(so["notify"]["mode"], {"auto", "on"})
            formal = json.loads(
                (loop / "formal" / "formal_policy.json").read_text(encoding="utf-8")
            )
            self.assertEqual(formal["policy"], "on")
            self.assertTrue(formal["typecheck"])
            env_text = policy.read_text(encoding="utf-8")
            self.assertIn("AAS_AUTOLOOP_GOAL_PRIORITY=on", env_text)
            self.assertIn("AAS_AUTOLOOP_NOTIFY=auto", env_text)
            self.assertFalse((loop / "driver" / "force_loop.env").exists())
            self.assertEqual(policy.stat().st_mode & 0o777, 0o600)
            errors = self.apply.verify_effective(loop, "formal", policy)
            self.assertEqual(errors, [])

    def test_general_skips_formal_on(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loop = Path(tmp) / "loop"
            policy = Path(tmp) / "host-policy.env"
            result = self.apply.apply_defaults(loop, profile="general", policy_file=policy)
            self.assertTrue(result["ok"], result)
            self.assertFalse((loop / "formal" / "formal_policy.json").is_file())
            env_text = policy.read_text(encoding="utf-8")
            self.assertIn("AAS_AUTOLOOP_FORMAL_POLICY=off", env_text)

    def test_legacy_credential_shadow_fails_before_any_campaign_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            loop = root / "loop"
            driver = loop / "driver"
            driver.mkdir(parents=True)
            shadow = driver / "force_loop.env"
            secret = "shadow-secret-value-92731"
            original = f"TELEGRAM_BOT_TOKEN={secret}\n"
            shadow.write_text(original, encoding="utf-8")
            policy = root / "host-policy.env"

            with self.assertRaisesRegex(ValueError, "credential-capable") as raised:
                self.apply.apply_defaults(loop, profile="general", policy_file=policy)

            self.assertNotIn(secret, str(raised.exception))
            self.assertEqual(shadow.read_text(encoding="utf-8"), original)
            self.assertFalse(policy.exists())
            self.assertFalse((loop / "goal_priority.json").exists())
            self.assertFalse((driver / "force_loop_pin_backups").exists())

    def test_safe_legacy_policy_is_migrated_without_byte_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            loop = root / "loop"
            driver = loop / "driver"
            driver.mkdir(parents=True)
            shadow = driver / "force_loop.env"
            shadow.write_text(
                "AAS_FORCE_LOOP_COMPUTE_LANES=local\n"
                "AAS_AUTOLOOP_FORMAL_POLICY=off\n",
                encoding="utf-8",
            )
            policy = root / "host-policy.env"

            result = self.apply.apply_defaults(
                loop, profile="general", policy_file=policy
            )

            self.assertTrue(result["ok"], result)
            self.assertFalse(shadow.exists())
            self.assertFalse(
                (driver / "force_loop_pin_backups" / "force_loop.env").exists()
            )
            migrated = policy.read_text(encoding="utf-8")
            self.assertIn("AAS_FORCE_LOOP_COMPUTE_LANES=local", migrated)

    def test_any_legacy_backup_shadow_requires_manual_removal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            loop = root / "loop"
            backup_shadow = (
                loop
                / "driver"
                / "force_loop_pin_backups"
                / "force_loop.env"
            )
            backup_shadow.parent.mkdir(parents=True)
            original = "AAS_AUTOLOOP_NOTIFY=auto\n"
            backup_shadow.write_text(original, encoding="utf-8")
            policy = root / "host-policy.env"

            with self.assertRaisesRegex(ValueError, "backup shadow"):
                self.apply.apply_defaults(loop, profile="general", policy_file=policy)

            self.assertEqual(backup_shadow.read_text(encoding="utf-8"), original)
            self.assertFalse(policy.exists())
            self.assertFalse((loop / "goal_priority.json").exists())

    def test_verify_fails_when_defaults_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loop = Path(tmp) / "empty"
            loop.mkdir()
            errors = self.apply.verify_effective(loop, "formal", Path(tmp) / "missing.env")
            self.assertTrue(errors)


class ProcessBackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.proc = _load("force_loop_process", FORCE_LOOP / "force_loop_process.py")

    def test_default_backend_is_foreground(self) -> None:
        self.assertEqual(self.proc.select_backend(None), "foreground")
        self.assertEqual(self.proc.select_backend("auto"), "foreground")

    def test_systemd_not_selected_when_unavailable(self) -> None:
        # On hosts without user systemd this must raise rather than silently claim it.
        if not self.proc.systemd_user_available():
            with self.assertRaises(ValueError):
                self.proc.select_backend("systemd")

    def test_status_snapshot_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loop = Path(tmp) / "loop"
            policy = Path(tmp) / "host-policy.env"
            loop.mkdir()
            snap = self.proc.status_snapshot(loop)
            self.assertIn("default_backend", snap)
            self.assertEqual(snap["default_backend"], "foreground")
            self.assertIn("matched_pids", snap)

    @unittest.skipUnless(os.name == "posix", "descriptor binding is POSIX-only")
    def test_child_binding_survives_script_path_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "child.py"
            script.write_text("ORIGINAL = True\n", encoding="utf-8")
            script.chmod(0o700)
            bound = self.proc.bind_child_command(
                [str(Path(sys.executable).resolve()), str(script), "argument"]
            )
            try:
                replacement = root / "replacement.py"
                replacement.write_text("REPLACED = True\n", encoding="utf-8")
                replacement.chmod(0o700)
                os.replace(replacement, script)
                self.assertEqual(
                    Path(bound.argv[1]).read_text(encoding="utf-8"),
                    "ORIGINAL = True\n",
                )
                self.assertEqual(bound.pass_fds, tuple(sorted(bound.pass_fds)))
                self.assertEqual(len(bound.pass_fds), 2)
            finally:
                bound.close()

    def test_supervisor_is_pinned_to_bin_bash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            supervisor = parent / "arl_drive_supervisor.sh"
            supervisor.write_text("exit 0\n", encoding="utf-8")
            supervisor.chmod(0o700)
            self.assertEqual(
                self.proc.build_supervisor_command(
                    pack_parent=parent,
                    loop_dir=parent / "loop",
                    project_root=parent,
                ),
                ["/bin/bash", str(supervisor)],
            )


class RuntimeYamlInstallTests(unittest.TestCase):
    def test_force_loop_files_registered(self) -> None:
        data = json.loads(
            (REPO / "manifest" / "runtime.yaml").read_text(encoding="utf-8")
        )
        files = data["skills"]["autonomous-research-loop-runtime"]["files"]
        sources = {f["source"] for f in files}
        self.assertIn(
            "skills/autonomous-research-loop-runtime/force-loop/force_loop_cli.py",
            sources,
        )
        self.assertIn(
            "skills/autonomous-research-loop-runtime/force-loop/run_force_loop.sh",
            sources,
        )
        # Platform tags present on python module
        cli = next(
            f
            for f in files
            if f["source"].endswith("force-loop/force_loop_cli.py")
        )
        for plat in ("linux", "macos", "windows", "wsl"):
            self.assertIn(plat, cli["platforms"])
        sh = next(
            f for f in files if f["source"].endswith("force-loop/run_force_loop.sh")
        )
        self.assertNotIn("windows", sh["platforms"])
        ps1 = next(
            f for f in files if f["source"].endswith("force-loop/run_force_loop.ps1")
        )
        self.assertEqual(ps1["platforms"], ["windows"])

    def test_artifact_and_recommended_templates(self) -> None:
        skills = json.loads(
            (REPO / "manifest" / "skills.yaml").read_text(encoding="utf-8")
        )
        arts = json.loads(
            (REPO / "manifest" / "artifacts.yaml").read_text(encoding="utf-8")
        )
        self.assertIn(
            "arl-scripted-force-loop", arts["artifacts"]["template"]
        )
        for slug in (
            "autonomous-research-loop",
            "autonomous-research-loop-runtime",
        ):
            rec = skills["skills"][slug]["recommended_templates"]
            self.assertIn("arl-scripted-force-loop", rec)
        profiles = arts["artifact_profiles"]
        self.assertIn(
            "template:arl-scripted-force-loop",
            profiles["workflow-templates"]["artifacts"],
        )


class CliSmokeTests(unittest.TestCase):
    def test_cli_apply_and_smoke(self) -> None:
        cli = _load("force_loop_cli", FORCE_LOOP / "force_loop_cli.py")
        with tempfile.TemporaryDirectory() as tmp:
            loop = Path(tmp) / "loop"
            policy = Path(tmp) / "host-policy.env"
            rc = cli.main(
                [
                    "apply-defaults",
                    "--loop",
                    str(loop),
                    "--profile",
                    "formal",
                    "--policy-file",
                    str(policy),
                    "--no-backup",
                ]
            )
            self.assertEqual(rc, 0)
            rc = cli.main([
                "smoke", "--loop", str(loop), "--profile", "formal",
                "--policy-file", str(policy),
            ])
            self.assertEqual(rc, 0)

    def test_start_environment_scrubs_hostile_startup_hooks_and_credentials(self) -> None:
        cli = _load("force_loop_cli_scrub", FORCE_LOOP / "force_loop_cli.py")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = root / "host-policy.env"
            policy.write_text(
                "AAS_AUTOLOOP_GOAL_PRIORITY=on\nAAS_AUTOLOOP_NOTIFY=auto\n",
                encoding="utf-8",
            )
            policy.chmod(0o600)
            hostile = {
                "BASH_ENV": "/tmp/hostile.sh",
                "ENV": "/tmp/hostile.sh",
                "PYTHONPATH": "/tmp/hostile-python",
                "PYTHONHOME": "/tmp/hostile-home",
                "LD_PRELOAD": "/tmp/hostile.so",
                "NODE_OPTIONS": "--require=/tmp/hostile.js",
                "OPENAI_API_KEY": "must-not-cross",
                "HCLOUD_TOKEN": "must-not-cross",
                "PATH": "/tmp/hostile-bin",
            }
            with mock.patch.dict(os.environ, hostile, clear=True):
                child = cli._load_start_env(root / "loop", policy)
            for key in hostile:
                if key != "PATH":
                    self.assertNotIn(key, child)
            self.assertEqual(child["PATH"], "/usr/bin:/bin")
            self.assertEqual(child["SHELL"], "/bin/bash")

    def test_start_binds_before_credentials_for_foreground_and_detach(self) -> None:
        cli = _load("force_loop_cli_ordering", FORCE_LOOP / "force_loop_cli.py")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            loop = root / "loop"
            loop.mkdir()
            policy = root / "policy.env"
            policy.write_text("AAS_AUTOLOOP_NOTIFY=auto\n", encoding="utf-8")
            policy.chmod(0o600)
            for backend in ("foreground", "posix_detach"):
                with self.subTest(backend=backend):
                    events: list[str] = []
                    binding = SimpleNamespace(
                        argv=["/proc/self/fd/7", "/proc/self/fd/8"],
                        pass_fds=(7, 8),
                        close=lambda: events.append("close"),
                    )
                    args = SimpleNamespace(
                        loop=str(loop),
                        root=str(root),
                        policy_file=str(policy),
                        profile="formal",
                        backend=backend,
                        detach=False,
                        provider="openai",
                        panel=None,
                        drive_only=True,
                        formal_typecheck=True,
                        skip_defaults_check=False,
                        drive_extra=[],
                    )

                    def bind(_argv: list[str]):
                        events.append("bind")
                        return binding

                    def load(env: dict[str, str], *, provider: str | None):
                        self.assertEqual(provider, "openai")
                        events.append("credentials")
                        return env

                    def run(*_args, **kwargs):
                        self.assertEqual(kwargs["pass_fds"], (7, 8))
                        events.append("run")
                        return 0

                    runner_patch = (
                        mock.patch.object(cli, "run_foreground", side_effect=run)
                        if backend == "foreground"
                        else mock.patch.object(cli, "run_posix_detach", side_effect=run)
                    )
                    with (
                        mock.patch.object(cli, "verify_effective", return_value=[]),
                        mock.patch.object(cli, "select_backend", return_value=backend),
                        mock.patch.object(
                            cli,
                            "build_drive_command",
                            return_value=[str(Path(sys.executable).resolve()), str(loop / "drive.py")],
                        ),
                        mock.patch.object(cli, "bind_child_command", side_effect=bind),
                        mock.patch.object(cli, "_load_selected_credentials", side_effect=load),
                        runner_patch,
                    ):
                        self.assertEqual(cli.cmd_start(args), 0)
                    self.assertEqual(events, ["bind", "credentials", "run", "close"])


@unittest.skipIf(os.name == "nt", "Windows secret pointers use the native launcher")
class ComputeSecretsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cli = _load("force_loop_cli_compute_secrets", FORCE_LOOP / "force_loop_cli.py")

    def _private_file(self, root: Path, body: str, name: str = "compute.env") -> Path:
        path = root / name
        path.write_text(body, encoding="utf-8")
        path.chmod(0o600)
        return path

    def _policy(self, root: Path, *, lanes: str | None = None) -> Path:
        policy = root / "host-policy.env"
        body = "AAS_AUTOLOOP_GOAL_PRIORITY=on\nAAS_AUTOLOOP_NOTIFY=auto\nAAS_AUTOLOOP_FORMAL_POLICY=off\n"
        if lanes:
            body += f"AAS_FORCE_LOOP_COMPUTE_LANES={lanes}\n"
        policy.write_text(body, encoding="utf-8")
        policy.chmod(0o600)
        return policy

    def _load_selected(self, root: Path, *, provider: str | None = None) -> dict[str, str]:
        loop = root / "loop"
        policy = self._policy(root, lanes="hetzner")
        child = self.cli._load_start_env(loop, policy)
        return self.cli._load_selected_credentials(child, provider=provider)

    def test_start_env_loads_only_allowlisted_compute_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            loop = root / "loop"
            secrets = self._private_file(root, "HCLOUD_TOKEN=hetzner-secret\nHCLOUD_SSH_KEYS=research-key,second-key\n")
            with mock.patch.dict(
                os.environ,
                {"AAS_COMPUTE_SECRETS_FILE": str(secrets)},
                clear=True,
            ):
                child = self._load_selected(root)

            self.assertEqual(child["HCLOUD_TOKEN"], "hetzner-secret")
            self.assertEqual(child["HCLOUD_SSH_KEYS"], "research-key,second-key")
            self.assertNotIn("KAGGLE_API_TOKEN", child)
            self.assertNotIn("AAS_COMPUTE_SECRETS_FILE", child)

    def test_compute_pointer_replaces_instead_of_augmenting_ambient_authority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            secrets = self._private_file(root, "HCLOUD_TOKEN=restored-hetzner\n")
            with mock.patch.dict(
                os.environ,
                {
                    "AAS_COMPUTE_SECRETS_FILE": str(secrets),
                    "HCLOUD_TOKEN": "stale-hcloud",
                    "KAGGLE_API_TOKEN": "stale-kaggle",
                },
                clear=True,
            ):
                child = self._load_selected(root)

            self.assertEqual(child["HCLOUD_TOKEN"], "restored-hetzner")
            self.assertNotIn("KAGGLE_API_TOKEN", child)
            self.assertNotIn("AAS_COMPUTE_SECRETS_FILE", child)

    def test_start_env_normalizes_and_validates_strict_notify_channel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.dict(
                os.environ,
                {"AAS_REMOTE_STRICT_NOTIFY_CHANNEL": " TeLeGrAm "},
                clear=True,
            ):
                child = self.cli._load_start_env(root / "loop", self._policy(root))
            self.assertEqual(child["AAS_REMOTE_STRICT_NOTIFY_CHANNEL"], "telegram")

            with mock.patch.dict(
                os.environ,
                {"AAS_REMOTE_STRICT_NOTIFY_CHANNEL": "fallback"},
                clear=True,
            ), self.assertRaises(SystemExit) as raised:
                self.cli._load_start_env(root / "loop", self._policy(root))
            self.assertIn("must be zulip, telegram, or empty", str(raised.exception))

    def test_start_env_rejects_unknown_keys_without_echoing_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            loop = root / "loop"
            secrets = self._private_file(root, "ZULIP_API_KEY=must-not-leak\n")
            with mock.patch.dict(
                os.environ,
                {"AAS_COMPUTE_SECRETS_FILE": str(secrets)},
                clear=True,
            ), self.assertRaises(SystemExit) as raised:
                child = self.cli._load_start_env(loop, self._policy(root, lanes="hetzner"))
                self.cli._load_selected_credentials(child, provider=None)

            message = str(raised.exception)
            self.assertIn("unsupported key ZULIP_API_KEY", message)
            self.assertNotIn("must-not-leak", message)

    def test_start_env_rejects_pointer_from_loop_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            loop = root / "loop"
            driver = loop / "driver"
            driver.mkdir(parents=True)
            secrets = self._private_file(root, "HCLOUD_TOKEN=must-not-load\n")
            (driver / "force_loop.env").write_text(
                f"AAS_COMPUTE_SECRETS_FILE={secrets}\n", encoding="utf-8"
            )

            with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(
                SystemExit
            ) as raised:
                self.cli._load_start_env(loop, self._policy(root))

            self.assertIn("legacy loop-local", str(raised.exception))
            self.assertNotIn("must-not-load", str(raised.exception))

    def test_start_env_rejects_compute_values_from_loop_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loop = Path(tmp) / "loop"
            (loop / "driver").mkdir(parents=True)
            (loop / "driver" / "force_loop.env").write_text(
                "HCLOUD_TOKEN=must-not-load\n", encoding="utf-8"
            )
            with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(
                SystemExit
            ) as raised:
                self.cli._load_start_env(loop, self._policy(Path(tmp)))
            self.assertIn("legacy loop-local", str(raised.exception))
            self.assertNotIn("must-not-load", str(raised.exception))

    def test_start_env_rejects_relative_compute_secrets_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loop = Path(tmp) / "loop"
            with mock.patch.dict(
                os.environ,
                {"AAS_COMPUTE_SECRETS_FILE": "relative/compute.env"},
                clear=True,
            ), self.assertRaises(SystemExit) as raised:
                child = self.cli._load_start_env(loop, self._policy(Path(tmp), lanes="hetzner"))
                self.cli._load_selected_credentials(child, provider=None)

            self.assertIn("must name an absolute path", str(raised.exception))

    @unittest.skipUnless(os.name == "posix", "POSIX ownership and symlink checks")
    def test_start_env_rejects_public_or_symlinked_secret_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            loop = root / "loop"
            secrets = self._private_file(root, "HCLOUD_TOKEN=must-not-leak\n")
            secrets.chmod(0o640)
            with mock.patch.dict(
                os.environ,
                {"AAS_COMPUTE_SECRETS_FILE": str(secrets)},
                clear=True,
            ), self.assertRaises(SystemExit) as public_error:
                child = self.cli._load_start_env(loop, self._policy(root, lanes="hetzner"))
                self.cli._load_selected_credentials(child, provider=None)
            self.assertIn("owner-private", str(public_error.exception))
            self.assertNotIn("must-not-leak", str(public_error.exception))

            secrets.chmod(0o600)
            link = root / "compute-link.env"
            link.symlink_to(secrets)
            with mock.patch.dict(
                os.environ,
                {"AAS_COMPUTE_SECRETS_FILE": str(link)},
                clear=True,
            ), self.assertRaises(SystemExit) as link_error:
                child = self.cli._load_start_env(loop, self._policy(root, lanes="hetzner"))
                self.cli._load_selected_credentials(child, provider=None)
            self.assertNotIn("must-not-leak", str(link_error.exception))


@unittest.skipIf(os.name == "nt", "Windows secret pointers use the native launcher")
class ProviderSecretsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cli = _load("force_loop_cli_provider_secrets", FORCE_LOOP / "force_loop_cli.py")

    def _private_file(self, root: Path, body: str) -> Path:
        path = root / "provider.env"
        path.write_text(body, encoding="utf-8")
        path.chmod(0o600)
        return path

    def _policy(self, root: Path) -> Path:
        policy = root / "host-policy.env"
        policy.write_text(
            "AAS_AUTOLOOP_GOAL_PRIORITY=on\nAAS_AUTOLOOP_NOTIFY=auto\nAAS_AUTOLOOP_FORMAL_POLICY=off\n",
            encoding="utf-8",
        )
        policy.chmod(0o600)
        return policy

    def _load_selected(self, root: Path, *, provider: str) -> dict[str, str]:
        child = self.cli._load_start_env(root / "loop", self._policy(root))
        return self.cli._load_selected_credentials(child, provider=provider)

    def test_start_env_loads_allowlisted_provider_secrets_over_inherited_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            secrets = self._private_file(root, "OPENAI_API_KEY=restored-openai\n")
            with mock.patch.dict(
                os.environ,
                {
                    "AAS_PROVIDER_SECRETS_FILE": str(secrets),
                    "OPENAI_API_KEY": "stale-openai",
                },
                clear=True,
            ):
                child = self._load_selected(root, provider="openai")

            self.assertEqual(child["OPENAI_API_KEY"], "restored-openai")
            self.assertNotIn("ANTHROPIC_API_KEY", child)
            self.assertNotIn("AAS_PROVIDER_SECRETS_FILE", child)

    def test_provider_pointer_scrubs_unrestored_ambient_provider_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            secrets = self._private_file(root, "OPENAI_API_KEY=restored-openai\n")
            with mock.patch.dict(
                os.environ,
                {
                    "AAS_PROVIDER_SECRETS_FILE": str(secrets),
                    "OPENAI_API_KEY": "stale-openai",
                    "KIMI_API_KEY": "stale-kimi",
                },
                clear=True,
            ):
                child = self._load_selected(root, provider="openai")

            self.assertEqual(child["OPENAI_API_KEY"], "restored-openai")
            self.assertNotIn("KIMI_API_KEY", child)
            self.assertNotIn("AAS_PROVIDER_SECRETS_FILE", child)

    def test_start_env_rejects_invalid_provider_files_without_echoing_values(self) -> None:
        cases = {
            "unknown": "NOT_A_PROVIDER_KEY=must-not-leak\n",
            "duplicate": "OPENAI_API_KEY=must-not-leak\nOPENAI_API_KEY=again\n",
            "empty": "OPENAI_API_KEY=\n",
        }
        for label, body in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                secrets = self._private_file(root, body)
                with mock.patch.dict(
                    os.environ,
                    {"AAS_PROVIDER_SECRETS_FILE": str(secrets)},
                    clear=True,
                ), self.assertRaises(SystemExit) as raised:
                    child = self.cli._load_start_env(root / "loop", self._policy(root))
                    self.cli._load_selected_credentials(child, provider="openai")

                self.assertNotIn("must-not-leak", str(raised.exception))

    def test_provider_pointer_is_launcher_only_absolute_and_owner_private(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            loop = root / "loop"
            (loop / "driver").mkdir(parents=True)
            secrets = self._private_file(root, "OPENAI_API_KEY=must-not-leak\n")
            (loop / "driver" / "force_loop.env").write_text(
                f"AAS_PROVIDER_SECRETS_FILE={secrets}\n", encoding="utf-8"
            )
            with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(
                SystemExit
            ) as loop_error:
                self.cli._load_start_env(loop, self._policy(root))
            self.assertIn("legacy loop-local", str(loop_error.exception))

            cases = ("relative/provider.env", f" {secrets}")
            for pointer in cases:
                with self.subTest(pointer=pointer), mock.patch.dict(
                    os.environ,
                    {"AAS_PROVIDER_SECRETS_FILE": pointer},
                    clear=True,
                ), self.assertRaises(SystemExit) as raised:
                    child = self.cli._load_start_env(root / "other-loop", self._policy(root))
                    self.cli._load_selected_credentials(child, provider="openai")
                self.assertNotIn("must-not-leak", str(raised.exception))

            if os.name == "posix":
                secrets.chmod(0o640)
                with mock.patch.dict(
                    os.environ,
                    {"AAS_PROVIDER_SECRETS_FILE": str(secrets)},
                    clear=True,
                ), self.assertRaises(SystemExit) as public_error:
                    child = self.cli._load_start_env(root / "public-loop", self._policy(root))
                    self.cli._load_selected_credentials(child, provider="openai")
                self.assertIn("owner-private", str(public_error.exception))

                secrets.chmod(0o600)
                link = root / "provider-link.env"
                link.symlink_to(secrets)
                with mock.patch.dict(
                    os.environ,
                    {"AAS_PROVIDER_SECRETS_FILE": str(link)},
                    clear=True,
                ), self.assertRaises(SystemExit) as link_error:
                    child = self.cli._load_start_env(root / "link-loop", self._policy(root))
                    self.cli._load_selected_credentials(child, provider="openai")
                self.assertNotIn("must-not-leak", str(link_error.exception))

    def test_start_env_rejects_provider_values_from_loop_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loop = Path(tmp) / "loop"
            (loop / "driver").mkdir(parents=True)
            (loop / "driver" / "force_loop.env").write_text(
                "OPENAI_API_KEY=must-not-load\n", encoding="utf-8"
            )
            with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(
                SystemExit
            ) as raised:
                self.cli._load_start_env(loop, self._policy(Path(tmp)))
            self.assertIn("legacy loop-local", str(raised.exception))
            self.assertNotIn("must-not-load", str(raised.exception))


class WindowsNativeSecretLoaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cli = _load(
            "force_loop_cli_windows_secret_loader",
            FORCE_LOOP / "force_loop_cli.py",
        )

    def test_powershell_launcher_uses_managed_runner_and_strict_secret_projection(self) -> None:
        launcher = (FORCE_LOOP / "run_force_loop.ps1").read_text(encoding="utf-8")
        python_launch = launcher.rindex("& $PythonRunner")
        resolve_launch = launcher.index("-ResolveOnly")

        expected = {
            "AAS_COMPUTE_SECRETS_FILE": {
                "HCLOUD_TOKEN",
                "HCLOUD_SSH_KEYS",
                "KAGGLE_API_TOKEN",
                "KAGGLE_CONFIG_DIR",
            },
            "AAS_PROVIDER_SECRETS_FILE": {
                "ANTHROPIC_API_KEY",
                "ANTHROPIC_AUTH_TOKEN",
                "CLAUDE_API_KEY",
                "CLAUDE_CODE_OAUTH_TOKEN",
                "COPILOT_GITHUB_TOKEN",
                "COPILOT_PROVIDER_API_KEY",
                "COPILOT_PROVIDER_BEARER_TOKEN",
                "DEEPSEEK_API_KEY",
                "GEMINI_API_KEY",
                "GH_TOKEN",
                "GITHUB_TOKEN",
                "GOOGLE_API_KEY",
                "GROK_API_KEY",
                "KIMI_API_KEY",
                "MOONSHOT_API_KEY",
                "OPENAI_API_KEY",
                "OPENCODE_API_KEY",
                "XAI_API_KEY",
            },
        }
        for pointer, expected_keys in expected.items():
            import_call = (
                f'Import-AasSecretEnvFile -PointerEnv "{pointer}"'
            )
            clear_call = f"Remove-Item Env:{pointer}"
            self.assertIn(import_call, launcher)
            self.assertIn(clear_call, launcher)
            self.assertLess(resolve_launch, launcher.index(import_call))
            self.assertLess(launcher.index(import_call), python_launch)
            self.assertLess(launcher.index(clear_call), python_launch)
            allowlist_block = launcher.split(import_call, 1)[1].split(")", 1)[0]
            actual_keys = set(
                re.findall(r'"([A-Z][A-Z0-9_]*)"', allowlist_block)
            )
            self.assertEqual(actual_keys, expected_keys)
            export_block = launcher.split(import_call, 1)[1].split(") -ExportKeys @(", 1)[1].split(")", 1)[0]
            exported_keys = set(
                re.findall(r'"([A-Z][A-Z0-9_]*)"', export_block)
            )
            self.assertEqual(exported_keys, expected_keys)
        self.assertIn("run_python.ps1", launcher)
        self.assertIn("AAS_RUNTIME_SCRIPT", launcher)
        self.assertNotIn("& $Python (Join-Path", launcher)

    def test_python_refuses_windows_pointer_fallback(self) -> None:
        loop = Path("loop")
        unused_policy = Path.cwd() / "unused-policy.env"
        for pointer in (
            "AAS_COMPUTE_SECRETS_FILE",
            "AAS_PROVIDER_SECRETS_FILE",
        ):
            with self.subTest(pointer=pointer), mock.patch.object(
                self.cli.os,
                "name",
                "nt",
            ), mock.patch.dict(
                os.environ,
                {pointer: r"C:\private\restored.env"},
                clear=True,
            ), self.assertRaises(SystemExit) as raised:
                self.cli._load_start_env(loop, unused_policy)

            message = str(raised.exception)
            self.assertIn("run_force_loop.ps1", message)
            self.assertNotIn("restored.env", message)


@unittest.skipUnless(sys.platform.startswith("linux"), "systemd backend is Linux-only")
class SystemdEnvironmentHandoffTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cli = _load(
            "force_loop_cli_systemd_environment",
            FORCE_LOOP / "force_loop_cli.py",
        )

    def test_systemd_uses_private_environment_file_without_secret_argv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime_dir = root / "runtime"
            runtime_dir.mkdir(mode=0o700)
            loop = root / "project" / "loop"
            loop.mkdir(parents=True)
            provider_secret = "provider secret=#1\\part"
            compute_secret = "compute-secret=2"
            unrelated_secret = "must-not-cross-boundary"
            env = {
                "XDG_RUNTIME_DIR": str(runtime_dir),
                "PATH": "/usr/bin:/bin",
                "LOOP_DIR": "wrong-loop",
                "PROJECT_ROOT": "wrong-root",
                "AAS_RUNTIME_ROOT": "/opt/aas/runtime",
                "AAS_AUTOLOOP_EXTERNAL_PANEL_EGRESS": "allow",
                "AAS_REMOTE_STRICT_NOTIFY_CHANNEL": "zulip",
                "OPENAI_API_KEY": provider_secret,
                "KAGGLE_API_TOKEN": compute_secret,
                "UNRELATED_SECRET": unrelated_secret,
            }
            completed = SimpleNamespace(returncode=0)

            with (
                mock.patch.object(
                    self.cli.shutil,
                    "which",
                    side_effect=lambda name, **_kwargs: f"/usr/bin/{name}",
                ),
                mock.patch.object(
                    self.cli.subprocess,
                    "run",
                    return_value=completed,
                ) as run,
            ):
                rc = self.cli._start_systemd_user(
                    ["/usr/bin/python3", "supervisor.py"],
                    loop=loop,
                    root=loop.parent,
                    env=env,
                )

            self.assertEqual(rc, 0)
            command = run.call_args.args[0]
            command_text = "\n".join(command)
            self.assertNotIn(provider_secret, command_text)
            self.assertNotIn(compute_secret, command_text)
            self.assertNotIn(unrelated_secret, command_text)
            self.assertNotIn("--setenv", command)
            self.assertTrue(Path(command[0]).is_absolute())
            self.assertEqual(Path(command[0]).name, "systemd-run")

            environment_property = next(
                item
                for item in command
                if item.startswith("--property=EnvironmentFile=")
            )
            environment_path = Path(environment_property.split("=", 2)[2])
            self.assertTrue(environment_path.is_file())
            self.assertEqual(environment_path.stat().st_mode & 0o777, 0o600)
            body = environment_path.read_text(encoding="utf-8")
            self.assertIn(f'LOOP_DIR="{loop}"', body)
            self.assertIn(f'PROJECT_ROOT="{loop.parent}"', body)
            self.assertIn('AAS_RUNTIME_ROOT="/opt/aas/runtime"', body)
            self.assertIn(
                'AAS_AUTOLOOP_EXTERNAL_PANEL_EGRESS="allow"',
                body,
            )
            self.assertIn('AAS_REMOTE_STRICT_NOTIFY_CHANNEL="zulip"', body)
            self.assertNotIn("OPENAI_API_KEY", body)
            self.assertNotIn("KAGGLE_API_TOKEN", body)
            self.assertNotIn(provider_secret, body)
            self.assertNotIn(compute_secret, body)
            self.assertNotIn("UNRELATED_SECRET", body)
            self.assertNotIn(unrelated_secret, body)

            cleanup_property = next(
                item
                for item in command
                if item.startswith("--property=ExecStopPost=")
            )
            self.assertIn(str(environment_path), cleanup_property)

            launcher_env = run.call_args.kwargs["env"]
            self.assertEqual(launcher_env["PATH"], "/usr/bin:/bin")
            self.assertNotIn("OPENAI_API_KEY", launcher_env)
            self.assertNotIn("KAGGLE_API_TOKEN", launcher_env)
            environment_path.unlink()

    def test_systemd_rejects_invalid_strict_notify_channel_before_submission(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime_dir = root / "runtime"
            runtime_dir.mkdir(mode=0o700)
            loop = root / "loop"
            loop.mkdir()
            env = {
                "XDG_RUNTIME_DIR": str(runtime_dir),
                "AAS_REMOTE_STRICT_NOTIFY_CHANNEL": "fallback",
            }

            with (
                mock.patch.object(
                    self.cli.shutil,
                    "which",
                    side_effect=lambda name, **_kwargs: f"/usr/bin/{name}",
                ),
                mock.patch.object(self.cli.subprocess, "run") as run,
            ):
                rc = self.cli._start_systemd_user(
                    ["/usr/bin/python3", "supervisor.py"],
                    loop=loop,
                    root=root,
                    env=env,
                )

            self.assertEqual(rc, 2)
            run.assert_not_called()
            self.assertEqual(list(runtime_dir.rglob("*.env")), [])

    def test_failed_systemd_submission_removes_environment_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime_dir = root / "runtime"
            runtime_dir.mkdir(mode=0o700)
            loop = root / "loop"
            loop.mkdir()
            env = {
                "XDG_RUNTIME_DIR": str(runtime_dir),
                "LOOP_DIR": str(loop),
                "PROJECT_ROOT": str(root),
                "OPENAI_API_KEY": "not-left-on-disk",
            }
            completed = SimpleNamespace(returncode=1)

            with (
                mock.patch.object(
                    self.cli.shutil,
                    "which",
                    side_effect=lambda name, **_kwargs: f"/usr/bin/{name}",
                ),
                mock.patch.object(
                    self.cli.subprocess,
                    "run",
                    return_value=completed,
                ) as run,
            ):
                rc = self.cli._start_systemd_user(
                    ["/usr/bin/false"],
                    loop=loop,
                    root=root,
                    env=env,
                )

            self.assertEqual(rc, 1)
            command = run.call_args.args[0]
            environment_property = next(
                item
                for item in command
                if item.startswith("--property=EnvironmentFile=")
            )
            environment_path = Path(environment_property.split("=", 2)[2])
            self.assertFalse(environment_path.exists())
            leftovers = list(runtime_dir.rglob("*.env"))
            self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
