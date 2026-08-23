"""Tests for the default scripted force-loop kit."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
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
RUNTIME_PY = FORCE_LOOP.parent / "autonomous_research_loop_runtime.py"

# Patching os.name to "nt" must not change which concrete Path flavor
# production code instantiates: Python 3.12 bakes the flavor guard into
# WindowsPath.__new__ at import time, so a simulated-Windows Path() on
# POSIX yields a WindowsPath whose re-instantiation (relative_to,
# with_segments) raises NotImplementedError; 3.13 dropped that guard and
# hides the mismatch. Pinning the host's real flavor keeps the simulation
# to branch decisions only — on native Windows the pin is an identity.
_NATIVE_PATH = type(Path())


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

    @unittest.skipUnless(os.name == "posix", "native Windows force-loop policy must be loaded by PowerShell")
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

    def test_projected_source_rejects_a_different_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            environ = {
                self.env.WINDOWS_PROJECTION_ENV: "AAS_AUTOLOOP_NOTIFY",
                self.env.WINDOWS_PROJECTION_SOURCE_ENV: str(root / "other.env"),
                "AAS_AUTOLOOP_NOTIFY": "auto",
            }
            with self.assertRaises(self.env.EnvLoadError) as raised:
                self.env.load_projected_env(
                    source_path=root / "host-policy.env", environ=environ
                )
            self.assertIn("came from a different file", str(raised.exception))

    @unittest.skipUnless(os.name == "posix", "needs unprivileged symlink creation")
    def test_projected_source_accepts_another_route_to_the_same_file(self) -> None:
        """The loader and this module reach one policy by two different routes.

        Load-LoopEnv.ps1 declares the path `[System.IO.Path]::GetFullPath`
        returns, which expands an 8.3 short component, while `os.path.abspath`
        leaves it alone.  Comparing the two textually refused a correct
        --policy-file.  A symlinked parent is the same class of mismatch in a
        form POSIX can build without privilege.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            secure = root / "secure"
            secure.mkdir()
            (root / "link").symlink_to(secure, target_is_directory=True)
            policy = secure / "host-policy.env"
            policy.write_text("AAS_AUTOLOOP_NOTIFY=auto\n", encoding="utf-8")
            environ = {
                self.env.WINDOWS_PROJECTION_ENV: "AAS_AUTOLOOP_NOTIFY",
                self.env.WINDOWS_PROJECTION_SOURCE_ENV: str(
                    root / "link" / "host-policy.env"
                ),
                "AAS_AUTOLOOP_NOTIFY": "auto",
            }
            got = self.env.load_projected_env(source_path=policy, environ=environ)
            self.assertEqual(got, {"AAS_AUTOLOOP_NOTIFY": "auto"})

    @unittest.skipIf(os.name == "posix", "8.3 short names are a Windows volume feature")
    def test_projected_source_accepts_a_windows_short_path_route(self) -> None:
        import ctypes

        with tempfile.TemporaryDirectory() as tmp:
            policy = Path(tmp) / "host-policy.env"
            policy.write_text("AAS_AUTOLOOP_NOTIFY=auto\n", encoding="utf-8")
            buffer = ctypes.create_unicode_buffer(1024)
            written = ctypes.windll.kernel32.GetShortPathNameW(str(policy), buffer, 1024)
            short = buffer.value if written else ""
            if not short or short == str(policy):
                self.skipTest("8.3 short-name creation is disabled on this volume")
            environ = {
                self.env.WINDOWS_PROJECTION_ENV: "AAS_AUTOLOOP_NOTIFY",
                # The loader declares the long form; Python keeps the short one.
                self.env.WINDOWS_PROJECTION_SOURCE_ENV: str(policy),
                "AAS_AUTOLOOP_NOTIFY": "auto",
            }
            got = self.env.load_projected_env(source_path=Path(short), environ=environ)
            self.assertEqual(got, {"AAS_AUTOLOOP_NOTIFY": "auto"})


class ApplyDefaultsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.apply = _load(
            "force_loop_apply", FORCE_LOOP / "apply_force_loop_defaults.py"
        )

    @staticmethod
    def _seed_plan(loop: Path, mode: str = "enforce") -> Path:
        """Stand in for the plan `goal-focus init/migrate` establishes.

        The kit reads `current_plan.enforcement_mode` and never writes it, so a
        loop that has not been through Goal Focus has no enforced plan to find.
        """

        loop.mkdir(parents=True, exist_ok=True)
        plan = loop / "current_plan.json"
        plan.write_text(
            json.dumps({"enforcement_mode": mode}, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return plan

    def test_windows_write_republishes_the_policy_projection(self) -> None:
        """A first bootstrap must see the defaults it just wrote.

        On native Windows `load_env_file` returns the projection
        Load-LoopEnv.ps1 took at process start, not the file, so the
        `verify_effective` call that follows the write read a pre-write view:
        run 1 reported `host policy missing AAS_AUTOLOOP_GOAL_PRIORITY=on`
        while an otherwise identical run 2 passed.
        """
        import load_loop_env

        with tempfile.TemporaryDirectory() as tmp:
            loop = Path(tmp) / "loop"
            loop.mkdir()
            policy = Path(tmp) / "secure" / "host-policy.env"
            with mock.patch.dict(os.environ, {}, clear=False), mock.patch.object(
                self.apply.os, "name", "nt"
            ), mock.patch.object(self.apply, "Path", _NATIVE_PATH), mock.patch.object(
                load_loop_env, "Path", _NATIVE_PATH
            ):
                dest = self.apply.write_host_env_defaults(loop, "formal", policy)
                got = load_loop_env.load_env_file(dest, forbidden_root=loop)
            self.assertEqual(got["AAS_AUTOLOOP_GOAL_PRIORITY"], "on")
            self.assertEqual(got["AAS_AUTOLOOP_NOTIFY"], "auto")
            self.assertEqual(got["AAS_AUTOLOOP_FORMAL_POLICY"], "on")
            self.assertEqual(got["AAS_AUTOLOOP_FORMAL_TYPECHECK"], "1")

    def test_windows_reprojection_drops_a_key_the_write_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loop = Path(tmp) / "loop"
            loop.mkdir()
            policy = Path(tmp) / "secure" / "host-policy.env"
            with mock.patch.dict(
                os.environ, {"AAS_AUTOLOOP_FORMAL_TYPECHECK": "1"}, clear=False
            ), mock.patch.object(self.apply.os, "name", "nt"), mock.patch.object(
                self.apply, "Path", _NATIVE_PATH
            ):
                self.apply.write_host_env_defaults(loop, "general", policy)
                manifest = os.environ["AAS_FORCE_LOOP_POLICY_PROJECTED"]
                self.assertNotIn("AAS_AUTOLOOP_FORMAL_TYPECHECK", os.environ)
            self.assertEqual(
                manifest,
                "AAS_AUTOLOOP_FORMAL_POLICY,AAS_AUTOLOOP_GOAL_PRIORITY,"
                "AAS_AUTOLOOP_NOTIFY",
            )

    @unittest.skipUnless(os.name == "posix", "reprojection is the native Windows path")
    def test_posix_write_leaves_the_process_environment_alone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loop = Path(tmp) / "loop"
            loop.mkdir()
            policy = Path(tmp) / "secure" / "host-policy.env"
            with mock.patch.dict(os.environ, {}, clear=False):
                self.apply.write_host_env_defaults(loop, "formal", policy)
                self.assertNotIn("AAS_FORCE_LOOP_POLICY_PROJECTED", os.environ)
                self.assertNotIn("AAS_AUTOLOOP_GOAL_PRIORITY", os.environ)


    @unittest.skipUnless(os.name == "posix", "native Windows force-loop policy must be loaded by PowerShell")
    def test_an_explicit_title_renames_a_loop_that_already_has_one(self) -> None:
        """``--research-title`` on an existing loop has to rename it.

        ``apply_notify_identity`` wrote the three identity aliases with
        ``setdefault``, so on a loop whose ``notify.json`` already carried them
        the operator's title was resolved and then discarded.
        ``apply_standing_orders`` assigns the same field directly, so the run
        finished ``ok`` with the two surfaces disagreeing -- and
        ``resolve_loop_notify_identity`` reads ``notify.json`` first, so the
        stale one is the one that wins.
        """

        old = "Token sliding on split graphs"
        new = "Token jumping on chordal graphs"
        arl = _load("force_loop_arl_identity", RUNTIME_PY)
        with tempfile.TemporaryDirectory() as tmp:
            loop = Path(tmp) / "loop"
            policy = Path(tmp) / "host-policy.env"
            self._seed_plan(loop)
            (loop / "notify.json").write_text(
                json.dumps(
                    {
                        "research_title": old,
                        "notify_title": old,
                        "display_name": old,
                        "body_profile": "operator_full",
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )

            result = self.apply.apply_defaults(
                loop, profile="general", policy_file=policy, research_title=new
            )

            self.assertTrue(result["ok"], result)
            self.assertEqual(result["notify_identity"]["research_title"], new)
            notify = json.loads((loop / "notify.json").read_text(encoding="utf-8"))
            for alias in ("research_title", "notify_title", "display_name"):
                self.assertEqual(notify[alias], new, alias)
            state = json.loads((loop / "loop_state.json").read_text(encoding="utf-8"))
            self.assertEqual(
                state["standing_orders"]["notify"]["research_title"], new
            )
            identity = arl.resolve_loop_notify_identity(loop)
            self.assertEqual(identity["title"], new)
            self.assertEqual(identity["slug"], "token-jumping-on-chordal-graphs")

    @unittest.skipUnless(os.name == "posix", "native Windows force-loop policy must be loaded by PowerShell")
    def test_without_an_explicit_title_the_existing_file_is_untouched(self) -> None:
        """Only a supplied title renames anything.

        With no ``--research-title`` the guard below the assignment leaves an
        existing ``notify.json`` unwritten, so the check is byte equality, not
        the aliases: those are filled in the returned view alone.
        """

        old = "Token sliding on split graphs"
        with tempfile.TemporaryDirectory() as tmp:
            loop = Path(tmp) / "loop"
            policy = Path(tmp) / "host-policy.env"
            self._seed_plan(loop)
            path = loop / "notify.json"
            before = json.dumps({"research_title": old}, sort_keys=True) + "\n"
            path.write_text(before, encoding="utf-8")
            result = self.apply.apply_defaults(
                loop, profile="general", policy_file=policy
            )
            self.assertTrue(result["ok"], result)
            self.assertEqual(path.read_text(encoding="utf-8"), before)
            self.assertEqual(result["notify_identity"]["research_title"], old)

    @unittest.skipUnless(os.name == "posix", "native Windows force-loop policy must be loaded by PowerShell")
    def test_formal_apply_has_enforce_hard_notify(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loop = Path(tmp) / "loop"
            policy = Path(tmp) / "host-policy.env"
            self._seed_plan(loop)
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
            self.assertTrue(result["current_plan_enforced"], result)
            errors = self.apply.verify_effective(loop, "formal", policy)
            self.assertEqual(errors, [])

    @unittest.skipUnless(os.name == "posix", "native Windows force-loop policy must be loaded by PowerShell")
    def test_general_skips_formal_on(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loop = Path(tmp) / "loop"
            policy = Path(tmp) / "host-policy.env"
            self._seed_plan(loop)
            result = self.apply.apply_defaults(loop, profile="general", policy_file=policy)
            self.assertTrue(result["ok"], result)
            self.assertFalse((loop / "formal" / "formal_policy.json").is_file())
            env_text = policy.read_text(encoding="utf-8")
            self.assertIn("AAS_AUTOLOOP_FORMAL_POLICY=off", env_text)

    @unittest.skipUnless(os.name == "posix", "native Windows force-loop policy must be loaded by PowerShell")
    def test_apply_defaults_fails_closed_without_an_established_plan(self) -> None:
        """No plan means no enforce, and the kit must not manufacture one.

        Writing `enforcement_mode` here would break the decision-row binding
        that makes the field authoritative, so the only correct outcome is a
        reported error naming the operator's escalation path.
        """

        with tempfile.TemporaryDirectory() as tmp:
            loop = Path(tmp) / "loop"
            policy = Path(tmp) / "host-policy.env"
            result = self.apply.apply_defaults(loop, profile="formal", policy_file=policy)
            self.assertFalse(result["ok"], result)
            self.assertFalse(result["current_plan_enforced"], result)
            self.assertFalse((loop / "current_plan.json").exists())
            self.assertTrue(
                any("current_plan.json is missing" in error for error in result["errors"]),
                result,
            )
            # The non-plan pins still land, so a later escalation completes the loop.
            self.assertTrue((loop / "goal_priority.json").is_file())

    @unittest.skipUnless(os.name == "posix", "native Windows force-loop policy must be loaded by PowerShell")
    def test_apply_defaults_never_escalates_a_non_enforce_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loop = Path(tmp) / "loop"
            policy = Path(tmp) / "host-policy.env"
            plan = self._seed_plan(loop, mode="monitor")
            before = plan.read_bytes()
            result = self.apply.apply_defaults(loop, profile="general", policy_file=policy)
            self.assertFalse(result["ok"], result)
            self.assertFalse(result["current_plan_enforced"], result)
            self.assertEqual(plan.read_bytes(), before)
            self.assertTrue(
                any("goal-focus set-mode" in error for error in result["errors"]),
                result,
            )

    @unittest.skipUnless(os.name == "posix", "native Windows force-loop policy must be loaded by PowerShell")
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

    @unittest.skipUnless(os.name == "posix", "native Windows force-loop policy must be loaded by PowerShell")
    def test_safe_legacy_policy_is_migrated_without_byte_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            loop = root / "loop"
            driver = loop / "driver"
            driver.mkdir(parents=True)
            shadow = driver / "force_loop.env"
            shadow.write_text(
                "AAS_AUTOLOOP_FORMAL_POLICY=off\n",
                encoding="utf-8",
            )
            policy = root / "host-policy.env"
            self._seed_plan(loop)

            result = self.apply.apply_defaults(
                loop, profile="general", policy_file=policy
            )

            self.assertTrue(result["ok"], result)
            self.assertFalse(shadow.exists())
            self.assertFalse(
                (driver / "force_loop_pin_backups" / "force_loop.env").exists()
            )
            migrated = policy.read_text(encoding="utf-8")
            self.assertIn("AAS_AUTOLOOP_FORMAL_POLICY=off", migrated)

    @unittest.skipUnless(os.name == "posix", "native Windows force-loop policy must be loaded by PowerShell")
    def test_legacy_compute_lane_pin_is_never_migrated(self) -> None:
        """A loop-local lane pin would silently widen the accepted secret set.

        The lane selection decides which credential names the launcher will
        project, so it is host authority: migrating it from an agent-writable
        loop file would let the loop choose its own secret allowlist.
        """

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            loop = root / "loop"
            driver = loop / "driver"
            driver.mkdir(parents=True)
            shadow = driver / "force_loop.env"
            original = (
                "AAS_FORCE_LOOP_COMPUTE_LANES=hetzner\n"
                "AAS_AUTOLOOP_FORMAL_POLICY=off\n"
            )
            shadow.write_text(original, encoding="utf-8")
            policy = root / "host-policy.env"

            with self.assertRaisesRegex(ValueError, "compute lanes") as raised:
                self.apply.apply_defaults(loop, profile="general", policy_file=policy)

            self.assertIn("AAS_FORCE_LOOP_COMPUTE_LANES", str(raised.exception))
            self.assertEqual(shadow.read_text(encoding="utf-8"), original)
            self.assertFalse(policy.exists())
            self.assertFalse((loop / "goal_priority.json").exists())

    @unittest.skipUnless(os.name == "posix", "native Windows force-loop policy must be loaded by PowerShell")
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
            # The ancestor gate requires an owner-controlled interpreter chain;
            # sys.executable is a group-writable hostedtoolcache build on CI
            # runners, so prefer the OS interpreter (the test never executes it).
            executable = Path("/usr/bin/python3")
            if not executable.is_file():
                executable = Path(sys.executable)
            bound = self.proc.bind_child_command(
                [str(executable.resolve()), str(script), "argument"]
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

    @unittest.skipUnless(os.name == "posix", "descriptor binding is POSIX-only")
    def test_child_binding_rejection_names_the_offending_path(self) -> None:
        """A rejected binding must be fixable without a manual stat walk.

        A group-writable checkout is the common way ``force-loop start`` fails,
        so the error names the exact path, its mode, and the chmod that clears
        it, for both an ancestor directory and the command file itself.
        """

        executable = Path("/usr/bin/python3")
        if not executable.is_file():
            executable = Path(sys.executable)
        interpreter = str(executable.resolve())
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            root.chmod(0o700)

            loose = root / "loose"
            # ``mkdir(mode=...)`` is masked by the process umask, so a runner
            # at the usual 022 would create 0755 and never trip the gate the
            # rest of this test asserts on. ``chmod`` sets the mode outright.
            loose.mkdir()
            loose.chmod(0o775)
            script = loose / "child.py"
            script.write_text("x = 1\n", encoding="utf-8")
            script.chmod(0o700)
            with self.assertRaises(ValueError) as ancestor_ctx:
                self.proc.bind_child_command([interpreter, str(script)])
            ancestor_message = str(ancestor_ctx.exception)
            self.assertIn(str(loose), ancestor_message)
            self.assertIn("0775", ancestor_message)
            self.assertIn(f"chmod go-w {loose}", ancestor_message)

            tight = root / "tight"
            tight.mkdir(mode=0o700)
            loose_script = tight / "child.py"
            loose_script.write_text("x = 1\n", encoding="utf-8")
            loose_script.chmod(0o664)
            with self.assertRaises(ValueError) as file_ctx:
                self.proc.bind_child_command([interpreter, str(loose_script)])
            file_message = str(file_ctx.exception)
            self.assertIn(str(loose_script), file_message)
            self.assertIn("0664", file_message)
            self.assertIn(f"chmod go-w {loose_script}", file_message)

    @unittest.skipUnless(os.name == "posix", "the ARL drive supervisor is pinned to /bin/bash")
    def test_supervisor_is_pinned_to_bin_bash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            supervisor = parent / "arl_drive_supervisor.sh"
            supervisor.write_text("exit 0\n", encoding="utf-8")
            supervisor.chmod(0o700)
            # `--loop-tag` is what lets `stop`/`replace` match this supervisor
            # by loop instead of killing every supervisor on the host.
            self.assertEqual(
                self.proc.build_supervisor_command(
                    pack_parent=parent,
                    loop_dir=parent / "loop",
                ),
                ["/bin/bash", str(supervisor), "--loop-tag", str(parent / "loop")],
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
        # run_force_loop.ps1 dot-sources the loader and exits 127 without it,
        # so an unshipped loader would make every Windows launch unstartable.
        loader = next(
            f for f in files if f["source"].endswith("force-loop/Load-LoopEnv.ps1")
        )
        self.assertEqual(loader["platforms"], ["windows"])
        self.assertEqual(loader["newline"], ps1["newline"])

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
    @unittest.skipUnless(os.name == "posix", "the force-loop start path uses the POSIX policy loader and /bin/bash supervisor")
    def test_cli_apply_and_smoke(self) -> None:
        cli = _load("force_loop_cli", FORCE_LOOP / "force_loop_cli.py")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            loop = root / "loop"
            policy = root / "host-policy.env"
            # `apply-defaults` reads enforce from the plan and never writes it,
            # so the loop must come from `bootstrap`, which inits Goal Focus.
            rc = cli.main(
                [
                    "bootstrap",
                    "--loop",
                    str(loop),
                    "--root",
                    str(root),
                    "--profile",
                    "formal",
                    "--goal",
                    "smoke the force-loop kit",
                    "--success-criteria",
                    "apply-defaults and smoke both exit 0",
                    "--policy-file",
                    str(policy),
                    "--no-backup",
                ]
            )
            self.assertEqual(rc, 0)
            plan = json.loads(
                (loop / "current_plan.json").read_text(encoding="utf-8")
            )
            self.assertEqual(plan["enforcement_mode"], "enforce")
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

    @unittest.skipUnless(os.name == "posix", "the force-loop start path uses the POSIX policy loader and /bin/bash supervisor")
    def test_readme_documents_the_policy_file_requirement_per_command(self) -> None:
        """The command block must not advertise a command that cannot run.

        ``status`` and ``replace`` fail before doing any work without a policy
        path, so documenting them alongside ``stop`` sends an operator into an
        avoidable SystemExit.
        """

        cli = _load("force_loop_cli", FORCE_LOOP / "force_loop_cli.py")
        readme = (FORCE_LOOP / "README.md").read_text(encoding="utf-8")
        block = readme.split("```text", 1)[1].split("```", 1)[0]
        documented = {
            line.split()[1]: line
            for line in block.splitlines()
            if line.startswith("force-loop ")
        }
        self.assertEqual(
            set(documented),
            {
                "bootstrap",
                "apply-defaults",
                "start",
                "replace",
                "status",
                "stop",
                "drain",
                "smoke",
            },
        )
        for command, line in sorted(documented.items()):
            with self.subTest(command=command), tempfile.TemporaryDirectory() as tmp:
                loop = Path(tmp) / "loop"
                loop.mkdir()
                with mock.patch.dict(os.environ):
                    os.environ.pop(cli.POLICY_FILE_ENV, None)
                    try:
                        cli.main([command, "--loop", str(loop)])
                    except SystemExit as exc:
                        needs_policy = "--policy-file" in str(exc)
                    except Exception:  # noqa: BLE001 - any other fault is unrelated
                        needs_policy = False
                    else:
                        needs_policy = False
                self.assertEqual(
                    needs_policy,
                    "--policy-file ABS_PATH" in line,
                    f"README line for {command} disagrees with the CLI: {line}",
                )

    @unittest.skipUnless(os.name == "posix", "the force-loop start path uses the POSIX policy loader and /bin/bash supervisor")
    def test_bootstrap_pins_compute_policy_before_goal_focus_init(self) -> None:
        """Goal Focus resolves `current_plan.compute_policy` during init.

        A pin written after init leaves the plan allowlist empty against a
        non-empty structured allowlist, and `goal-focus validate` then fails
        permanently — so a loop bootstrapped in the wrong order can never smoke.
        """

        cli = _load("force_loop_cli_order", FORCE_LOOP / "force_loop_cli.py")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            # Correct order, as `bootstrap` performs it.
            good = root / "good"
            good_policy = root / "good-policy.env"
            self.assertEqual(
                cli.main(
                    [
                        "bootstrap", "--loop", str(good), "--root", str(root),
                        "--profile", "general", "--goal", "ordering check",
                        "--success-criteria", "smoke exits 0",
                        "--policy-file", str(good_policy), "--no-backup",
                    ]
                ),
                0,
            )
            plan = json.loads((good / "current_plan.json").read_text(encoding="utf-8"))
            allowed = set(plan.get("compute_policy", {}).get("allowed_services") or ())
            pinned = set(
                json.loads((good / "compute_policy.json").read_text(encoding="utf-8"))
                .get("policy", {})
                .get("backends")
                or ()
            )
            self.assertTrue(allowed, plan.get("compute_policy"))
            self.assertTrue(allowed <= pinned, (allowed, pinned))

            # Reversed order: init with no pin, then pin. This is what the
            # bootstrap ordering exists to prevent.
            bad = root / "bad"
            init = subprocess.run(
                [
                    sys.executable, str(RUNTIME_PY), "init", "--dir", str(bad),
                    "--goal", "ordering check", "--success-criteria", "smoke exits 0",
                    "--goal-focus-mode", "enforce",
                ],
                capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
            )
            self.assertEqual(init.returncode, 0, init.stderr or init.stdout)
            cli.apply_compute_policy(bad, "general")
            validate = subprocess.run(
                [sys.executable, str(RUNTIME_PY), "goal-focus", "validate", "--dir", str(bad)],
                capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
            )
            self.assertNotEqual(validate.returncode, 0, validate.stdout)
            self.assertIn("compute_policy", validate.stdout)

    @unittest.skipUnless(os.name == "posix", "the force-loop start path uses the POSIX policy loader and /bin/bash supervisor")
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

    @unittest.skipUnless(os.name == "posix", "the force-loop start path uses the POSIX policy loader and /bin/bash supervisor")
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

    @staticmethod
    def _ps_key_list(launcher: str, variable: str) -> set[str]:
        """Read the key names out of a `$Name = @("A", "B")` assignment."""

        block = launcher.split(f"${variable} = @(", 1)[1].split(")", 1)[0]
        return set(re.findall(r'"([A-Z][A-Z0-9_]*)"', block))

    @staticmethod
    def _ps_hashtable(launcher: str, variable: str) -> dict[str, set[str]]:
        """Read a `$Name = @{ "k" = @("A") ... }` map of lowercase keys."""

        body = launcher.split(f"${variable} = @{{", 1)[1]
        depth, end = 1, 0
        for index, char in enumerate(body):
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    end = index
                    break
        body = body[:end]
        entries: dict[str, set[str]] = {}
        for name, values in re.findall(
            r'"([a-z][a-z0-9_]*)"\s*=\s*@\(([^)]*)\)', body, re.DOTALL
        ):
            entries[name] = set(re.findall(r'"([A-Z][A-Z0-9_]*)"', values))
        return entries

    def test_powershell_launcher_uses_managed_runner_and_strict_secret_projection(self) -> None:
        launcher = (FORCE_LOOP / "run_force_loop.ps1").read_text(encoding="utf-8")
        python_launch = launcher.rindex("& $PythonRunner")
        resolve_launch = launcher.index("-ResolveOnly")

        # The PowerShell launcher is the Windows half of a contract whose POSIX
        # half lives in force_loop_cli.py.  Pin the tables against each other so
        # a lane or provider added on one platform cannot be silently missing
        # on the other.
        expected = {
            "AAS_COMPUTE_SECRETS_FILE": (
                "ComputeAllowed",
                set(self.cli.COMPUTE_SECRET_KEYS),
            ),
            "AAS_PROVIDER_SECRETS_FILE": (
                "ProviderAllowed",
                set(self.cli.PROVIDER_SECRET_KEYS),
            ),
        }
        for pointer, (allow_variable, expected_keys) in expected.items():
            # The call is wrapped over several lines with backtick
            # continuations, so match the parameter rather than a one-line call.
            import_call = f'-PointerEnv "{pointer}"'
            clear_call = f"Remove-Item Env:{pointer}"
            self.assertIn(import_call, launcher)
            self.assertIn(clear_call, launcher)
            self.assertLess(resolve_launch, launcher.index(import_call))
            self.assertLess(launcher.index(import_call), python_launch)
            self.assertLess(launcher.index(clear_call), python_launch)
            call = launcher.split(import_call, 1)[1].split("\n\n", 1)[0]
            self.assertIn(f"-AllowedKeys ${allow_variable}", call)
            self.assertIn("-ExportKeys ", call)
            self.assertEqual(
                self._ps_key_list(launcher, allow_variable), expected_keys
            )

        lanes = self._ps_hashtable(launcher, "ComputeLaneKeys")
        self.assertEqual(
            lanes, {name: set(keys) for name, keys in self.cli.COMPUTE_LANE_KEYS.items()}
        )
        # Every lane key must also be admissible, or a selected lane would be
        # exported and then rejected by the allowlist.
        self.assertEqual(
            set().union(*lanes.values()), set(self.cli.COMPUTE_SECRET_KEYS)
        )

        providers = self._ps_hashtable(launcher, "ProviderKeyMap")
        self.assertEqual(
            providers,
            {name: set(keys) for name, keys in self.cli.PROVIDER_KEY_MAP.items()},
        )
        self.assertTrue(
            set().union(*providers.values()) <= set(self.cli.PROVIDER_SECRET_KEYS)
        )

        self.assertIn("run_python.ps1", launcher)
        self.assertIn("AAS_RUNTIME_SCRIPT", launcher)
        self.assertNotIn("& $Python (Join-Path", launcher)

    def test_powershell_launcher_scrubs_every_posix_scrubbed_name(self) -> None:
        """The two launchers are one contract: an ambient token is never authority.

        A name scrubbed on POSIX but left standing on Windows would let an
        exported token reach the CLI on one platform only, which is exactly the
        ambient-authority path the pointer files exist to close.
        """

        posix = (FORCE_LOOP / "run_force_loop.sh").read_text(encoding="utf-8")
        # The unconditional scrub is the header block, before the first
        # function definition; the later block is the non-credential scrub.
        header = posix.split("export PATH=", 1)[0]
        scrubbed = set()
        for line in header.splitlines():
            if not line.startswith("unset "):
                continue
            scrubbed.update(re.findall(r"\b[A-Z][A-Z0-9_]+\b", line[len("unset "):]))
        # Shell-only names with no Windows analogue, plus the two pointers the
        # POSIX wrapper unsets only because it already stashed them in locals —
        # PowerShell reads `$env:` in place and clears them in the
        # non-credential branch instead.
        exempt = {
            "AAS_COMPUTE_SECRETS_FILE",
            "AAS_PROVIDER_SECRETS_FILE",
            "BASH_ENV",
            "CDPATH",
            "ENV",
            "GLOBIGNORE",
        }
        exempt.update(name for name in scrubbed if name.startswith(("LD_", "DYLD_")))

        launcher = (FORCE_LOOP / "run_force_loop.ps1").read_text(encoding="utf-8")
        # Only the unconditional scrub counts: names removed inside the
        # `if ($CredentialBearingLaunch)` branch are not parity.
        unconditional = launcher.split("if ($CredentialSubcommand -and (", 1)[0]
        block = unconditional.split("foreach ($ScrubKey in @(", 1)[1].split("\n))", 1)[0]
        windows = set(re.findall(r'"([A-Z][A-Z0-9_]*)"', block))

        self.assertTrue(
            (scrubbed - exempt) <= windows,
            sorted((scrubbed - exempt) - windows),
        )

    def test_powershell_require_trusted_is_gated_on_the_credential_subcommand(self) -> None:
        """An ambient token must not make `status` unstartable.

        `AAS_RUNTIME_REQUIRE_TRUSTED` makes `run_python.ps1` demand a pinned
        digest and signer thumbprint, so latching it on any subcommand that
        merely sees a token in the environment breaks read-only operation on
        every host that carries no pin.
        """

        launcher = (FORCE_LOOP / "run_force_loop.ps1").read_text(encoding="utf-8")
        expression = launcher.split("$CredentialBearingLaunch = [bool](", 1)[1]
        expression = expression.split(")\n", 1)[0]
        self.assertIn("$CredentialSubcommand -and", expression)
        self.assertIn("AAS_COMPUTE_SECRETS_FILE", expression)
        self.assertIn("AAS_PROVIDER_SECRETS_FILE", expression)
        # The pre-fix launcher latched on any ambient provider token.
        self.assertNotIn("GITHUB_TOKEN", expression)

        latch = launcher.split("if ($CredentialBearingLaunch) {", 1)[1].split("}", 1)[0]
        self.assertIn("AAS_RUNTIME_REQUIRE_TRUSTED", latch)

    def test_windows_interpreter_pins_are_documented_where_they_are_required(self) -> None:
        """A hard requirement with no documented name is an unstartable launch.

        `run_python.ps1` exits 127 when a trusted launch carries no digest and
        signer pin, so either the names are documented for operators or nothing
        may require them.
        """

        pins = ("AAS_WINDOWS_PYTHON_SHA256", "AAS_WINDOWS_PYTHON_SIGNER_THUMBPRINT")
        runner = (
            REPO / "canonical" / "runtime" / "runners" / "run_python.ps1"
        ).read_text(encoding="utf-8")
        documented = {
            pin
            for pin in pins
            for path in REPO.glob("canonical/**/*.md")
            if pin in path.read_text(encoding="utf-8")
        }
        for pin in pins:
            with self.subTest(pin=pin):
                if pin not in runner:
                    continue
                self.assertIn(pin, documented)

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


@unittest.skipUnless(shutil.which("pwsh"), "PowerShell launcher behaviour needs pwsh")
class PowerShellLaunchTests(unittest.TestCase):
    """Execute the Windows launcher under pwsh rather than reading its source.

    Only the platform-neutral half runs here: the native secret loader is a
    Win32 P/Invoke, so a credential import is stubbed out and the assertions
    stay on the launcher's own gating.
    """

    RUNNERS = REPO / "canonical" / "runtime" / "runners"

    def _stage(self, root: Path, *, stub_runner: bool) -> Path:
        skill = root / "workspace" / "skills" / "autonomous-research-loop-runtime"
        force_loop = skill / "force-loop"
        force_loop.mkdir(parents=True)
        for name in ("run_force_loop.ps1", "Load-LoopEnv.ps1"):
            shutil.copy2(FORCE_LOOP / name, force_loop / name)
        (force_loop / "force_loop_cli.py").write_text("", encoding="utf-8")
        runner = root / "run_python.ps1"
        if stub_runner:
            # -ResolveOnly must still answer with a real interpreter or the
            # launcher exits 127 before it ever reaches the final invocation.
            runner.write_text(
                "param([switch]$ResolveOnly)\n"
                f'if ($ResolveOnly) {{ Write-Output "{sys.executable}"; exit 0 }}\n'
                'Write-Output ("REQUIRE_TRUSTED=[" +'
                ' [string]$env:AAS_RUNTIME_REQUIRE_TRUSTED + "]")\n'
                "exit 0\n",
                encoding="utf-8",
            )
            # The real loader opens no-follow Win32 handles, so a POSIX host
            # cannot run it; the launcher's gating is what is under test.
            (root / "load_secret_env.ps1").write_text(
                "function Import-AasSecretEnvFile {\n"
                "    param([string]$PointerEnv, [string[]]$AllowedKeys,"
                " [string[]]$ExportKeys)\n"
                "}\n",
                encoding="utf-8",
            )
        else:
            shutil.copy2(self.RUNNERS / "run_python.ps1", runner)
        return force_loop / "run_force_loop.ps1"

    @staticmethod
    def _pwsh(launcher: Path, arguments: list[str], env: dict[str, str]):
        return subprocess.run(
            ["pwsh", "-NoProfile", "-File", str(launcher), *arguments],
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            env=env,
            timeout=120,
        )

    def test_resolve_only_writes_exactly_one_capturable_line(self) -> None:
        """`@(& $Runner -ResolveOnly)` captures the success stream, not the console.

        A resolver that writes straight to the console handle is captured as
        zero lines, so the launcher sees an empty path and refuses every
        credential-bearing start with "requires a trusted resolved Python".
        """

        runner = self.RUNNERS / "run_python.ps1"
        with tempfile.TemporaryDirectory() as tmp:
            # A console-handle write reaches the same stdout pipe as the
            # capture, so record what `@(...)` actually collected out of band.
            observed = Path(tmp) / "captured.txt"
            env = {
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "HOME": tmp,
                "AAS_RUNTIME_PYTHON": sys.executable,
            }
            # The runner probes the interpreter's version before it resolves,
            # and a Windows python.exe cannot start without this OS metadata,
            # so without it the probe fails and the resolver exits 127 with
            # nothing on the success stream. Same set the credential path in
            # run_skill.ps1 preserves.
            for name in (
                "SystemRoot", "WINDIR", "ComSpec", "PATHEXT", "TEMP", "TMP",
                "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "LOCALAPPDATA",
                "APPDATA", "PROGRAMDATA", "SystemDrive", "NUMBER_OF_PROCESSORS",
            ):
                value = os.environ.get(name)
                if value:
                    env[name] = value
            completed = subprocess.run(
                [
                    "pwsh",
                    "-NoProfile",
                    "-Command",
                    f'$lines = @(& "{runner}" -ResolveOnly);'
                    f' Set-Content -LiteralPath "{observed}"'
                    ' -Value ("{0}`n{1}" -f $lines.Count, ($lines -join "|"))',
                ],
                check=False,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                env=env,
                timeout=120,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            captured = observed.read_text(encoding="utf-8").splitlines()
            # The resolver reports every refusal on the error stream, so carry
            # it into the failure message: a bare `['0', '']` names no cause.
            resolver_stderr = completed.stderr

        self.assertEqual(captured, ["1", sys.executable], resolver_stderr)

        # A console-handle write would satisfy an interactive eyeball and fail
        # every capture, so keep the emitter itself pinned.
        body = runner.read_text(encoding="utf-8")
        emit = body.split("if ($ResolveOnly) {", 1)[1].split("}", 1)[0]
        self.assertIn("Write-Output $python", emit)
        self.assertNotIn("[Console]::Out.WriteLine", emit)

    def test_require_trusted_latches_only_for_credential_subcommands(self) -> None:
        """The latch turns an unpinned host into an unstartable one.

        `status` must survive an ambient `GITHUB_TOKEN`, and a pointer alone
        must not latch on a subcommand that forwards no credential.
        """

        cases = (
            (["status"], {"GITHUB_TOKEN": "ambient"}, ""),
            (["stop"], {"AAS_PROVIDER_SECRETS_FILE": None}, ""),
            (["start", "--provider", "codex"], {"AAS_PROVIDER_SECRETS_FILE": None}, "1"),
            (
                ["replace", "--provider", "codex"],
                {"AAS_PROVIDER_SECRETS_FILE": None},
                "1",
            ),
        )
        for arguments, overrides, expected in cases:
            with self.subTest(arguments=arguments), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                launcher = self._stage(root, stub_runner=True)
                pointer = root / "provider.env"
                pointer.write_text("OPENAI_API_KEY=probe\n", encoding="utf-8")
                pointer.chmod(0o600)
                env = {
                    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                    "HOME": str(root),
                }
                for name, value in overrides.items():
                    env[name] = str(pointer) if value is None else value

                completed = self._pwsh(launcher, arguments, env)

                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertIn(
                    f"REQUIRE_TRUSTED=[{expected}]",
                    completed.stdout,
                    completed.stdout,
                )


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


class OperatorPinPlumbingTests(unittest.TestCase):
    """Operator-pin delivery fixes: start-env prefix parity, modal lane
    vocabulary, and apply-defaults idempotence for the lanes key."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.cli = _load("force_loop_cli_operator_pins", FORCE_LOOP / "force_loop_cli.py")
        cls.defaults = _load(
            "apply_force_loop_defaults_operator_pins",
            FORCE_LOOP / "apply_force_loop_defaults.py",
        )

    @unittest.skipUnless(os.name == "posix", "native Windows force-loop policy must be loaded by PowerShell")
    def test_start_env_retains_autoloop_operator_pins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = root / "host-policy.env"
            policy.write_text(
                "AAS_AUTOLOOP_GOAL_PRIORITY=on\nAAS_AUTOLOOP_NOTIFY=auto\n",
                encoding="utf-8",
            )
            policy.chmod(0o600)
            ambient = {
                "AAS_AUTOLOOP_PROVIDER_TRANSPORT": "trusted-local",
                "AAS_AUTOLOOP_ATTESTED_SHA256_CLAUDE": "deadbeef",
                "AAS_AUTOLOOP_COMPUTE_WORKSPACE": "/data/workspace",
                "AAS_AUTOLOOP_EXTERNAL_NOTIFY_EGRESS": "allow",
                "AAS_AUTOLOOP_NOTIFY": "off",
                "OPENAI_API_KEY": "must-not-cross",
                "LD_PRELOAD": "/tmp/hostile.so",
            }
            with mock.patch.dict(os.environ, ambient, clear=True):
                child = self.cli._load_start_env(root / "loop", policy)
            self.assertEqual(child["AAS_AUTOLOOP_PROVIDER_TRANSPORT"], "trusted-local")
            self.assertEqual(child["AAS_AUTOLOOP_ATTESTED_SHA256_CLAUDE"], "deadbeef")
            self.assertEqual(child["AAS_AUTOLOOP_COMPUTE_WORKSPACE"], "/data/workspace")
            self.assertEqual(child["AAS_AUTOLOOP_EXTERNAL_NOTIFY_EGRESS"], "allow")
            # The protected host policy still overrides ambient values.
            self.assertEqual(child["AAS_AUTOLOOP_NOTIFY"], "auto")
            self.assertNotIn("OPENAI_API_KEY", child)
            self.assertNotIn("LD_PRELOAD", child)

    def test_compute_lanes_accept_kaggle_and_modal(self) -> None:
        keys = self.cli._compute_keys(
            {"AAS_FORCE_LOOP_COMPUTE_LANES": "kaggle,modal"}
        )
        self.assertEqual(
            keys,
            frozenset(
                {
                    "KAGGLE_API_TOKEN",
                    "KAGGLE_CONFIG_DIR",
                    "MODAL_TOKEN_ID",
                    "MODAL_TOKEN_SECRET",
                }
            ),
        )

    @unittest.skipUnless(os.name == "posix", "native Windows force-loop policy must be loaded by PowerShell")
    def test_apply_defaults_rerun_preserves_compute_lanes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "loop"
            run_dir.mkdir()
            policy = root / "policy.env"
            self.defaults.write_host_env_defaults(
                run_dir,
                "formal",
                policy,
                migrated_policy={"AAS_FORCE_LOOP_COMPUTE_LANES": "kaggle,modal"},
            )
            self.defaults.write_host_env_defaults(run_dir, "formal", policy)
            body = policy.read_text(encoding="utf-8")
            self.assertIn("AAS_FORCE_LOOP_COMPUTE_LANES=kaggle,modal", body)
            self.assertIn("AAS_AUTOLOOP_GOAL_PRIORITY=on", body)

class PinnedFailoverConfigIsPrivateAtCreationTests(unittest.TestCase):
    """The pinned failover config is private from the instant it exists.

    ``_write_pinned_failover`` copies every key of ``failover.json`` --
    ``research_title`` among them, which names the unpublished topic the loop is
    driving -- and then narrows the result to 0600, so the destination is
    deliberately owner-private. The temporary it renames into place was created
    by ``Path.write_text``, which honours the umask, so under the ordinary 022
    the whole payload sat in a group- and world-readable file first and a reader
    who opened it inside that window kept a descriptor no later chmod revokes.

    Sampling the mode after the call cannot see this -- the narrowing has
    already run -- which is why the window survived review. The probes below
    therefore sample at creation, wrapping the two primitives that can create
    the payload (``Path.write_text`` and ``tempfile.mkstemp``) plus the rename
    that publishes it, so the assertion holds whichever one the writer uses.
    """

    @staticmethod
    def _failover(loop: Path) -> None:
        loop.mkdir(parents=True, exist_ok=True)
        (loop / "failover.json").write_text(
            json.dumps(
                {
                    "schema_version": "failover.v1",
                    "research_title": "unpublished topic",
                    "primary_order": ["claude", "codex"],
                }
            )
            + "\n",
            encoding="utf-8",
        )

    def _pin(self, loop: Path):
        """Return (destination, [(probe, mode) sampled the moment it exists])."""
        cli = _load("force_loop_cli_pin_mode", FORCE_LOOP / "force_loop_cli.py")
        seen: list[tuple[str, str]] = []

        def mode_of(target) -> str:
            return oct(stat.S_IMODE(os.stat(target).st_mode))

        real_write_text = Path.write_text
        real_mkstemp = tempfile.mkstemp
        real_replace = os.replace

        def spy_write_text(self, *args, **kwargs):
            result = real_write_text(self, *args, **kwargs)
            seen.append(("write_text", mode_of(self)))
            return result

        def spy_mkstemp(*args, **kwargs):
            descriptor, name = real_mkstemp(*args, **kwargs)
            seen.append(("mkstemp", oct(stat.S_IMODE(os.fstat(descriptor).st_mode))))
            return descriptor, name

        def spy_replace(src, dst, **kwargs):
            seen.append(("replace", mode_of(src)))
            return real_replace(src, dst, **kwargs)

        previous = os.umask(0o022)
        try:
            # The module attributes are the real ``os``/``tempfile`` modules, so
            # patching them here covers the writer whichever primitive it calls.
            with mock.patch.object(Path, "write_text", spy_write_text), \
                 mock.patch.object(tempfile, "mkstemp", spy_mkstemp), \
                 mock.patch.object(os, "replace", spy_replace):
                destination = cli._write_pinned_failover(loop, "openai")
        finally:
            os.umask(previous)
        return destination, seen

    @unittest.skipUnless(os.name == "posix", "POSIX file modes")
    def test_the_payload_is_never_group_or_world_readable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loop = Path(tmp) / "loop"
            self._failover(loop)
            _destination, seen = self._pin(loop)
        exposed = [probe for probe, mode in seen if int(mode, 8) & 0o077]
        self.assertEqual(exposed, [], f"payload readable by others: {seen}")

    @unittest.skipUnless(os.name == "posix", "POSIX file modes")
    def test_the_destination_is_owner_private(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loop = Path(tmp) / "loop"
            self._failover(loop)
            destination, _seen = self._pin(loop)
            self.assertEqual(
                stat.S_IMODE(destination.stat().st_mode),
                0o600,
                "the pinned config must end owner-private",
            )

    def test_the_probes_reach_the_real_writer(self) -> None:
        """Anchor: probes that never fired would pass the mode test vacuously."""
        with tempfile.TemporaryDirectory() as tmp:
            loop = Path(tmp) / "loop"
            self._failover(loop)
            destination, seen = self._pin(loop)
            payload = json.loads(destination.read_text(encoding="utf-8"))
            self.assertEqual(payload["primary_order"], ["openai"])
            self.assertEqual(payload["research_title"], "unpublished topic")
            self.assertIn("replace", [probe for probe, _mode in seen])
            self.assertGreaterEqual(len(seen), 2, f"probes did not fire: {seen}")


if __name__ == "__main__":
    unittest.main()
