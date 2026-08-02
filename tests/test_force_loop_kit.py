"""Tests for the default scripted force-loop kit."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

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

    def test_parse_simple(self) -> None:
        got = self.env.parse_env_text("FOO=bar\n# c\nexport BAZ=qux\n")
        self.assertEqual(got, {"FOO": "bar", "BAZ": "qux"})

    def test_reject_injection(self) -> None:
        with self.assertRaises(self.env.EnvLoadError):
            self.env.parse_env_text("X=$(whoami)\n")
        with self.assertRaises(self.env.EnvLoadError):
            self.env.parse_env_text("Y=`id`\n")
        with self.assertRaises(self.env.EnvLoadError):
            self.env.parse_env_text("bad-key=1\n")
        with self.assertRaises(self.env.EnvLoadError):
            self.env.parse_env_text("NOEQ\n")

    def test_quoted_value(self) -> None:
        got = self.env.parse_env_text('A="hello world"\n')
        self.assertEqual(got["A"], "hello world")

    def test_crlf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "e.env"
            p.write_bytes(b"FOO=bar\r\nBAZ=1\r\n")
            got = self.env.load_env_file(p)
            self.assertEqual(got, {"FOO": "bar", "BAZ": "1"})


class ApplyDefaultsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.apply = _load(
            "force_loop_apply", FORCE_LOOP / "apply_force_loop_defaults.py"
        )

    def test_formal_apply_has_enforce_hard_notify(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loop = Path(tmp) / "loop"
            result = self.apply.apply_defaults(loop, profile="formal")
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
            env_text = (loop / "driver" / "force_loop.env").read_text(encoding="utf-8")
            self.assertIn("AAS_AUTOLOOP_GOAL_PRIORITY=on", env_text)
            self.assertIn("AAS_AUTOLOOP_NOTIFY=auto", env_text)
            errors = self.apply.verify_effective(loop, "formal")
            self.assertEqual(errors, [])

    def test_general_skips_formal_on(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loop = Path(tmp) / "loop"
            result = self.apply.apply_defaults(loop, profile="general")
            self.assertTrue(result["ok"], result)
            self.assertFalse((loop / "formal" / "formal_policy.json").is_file())
            env_text = (loop / "driver" / "force_loop.env").read_text(encoding="utf-8")
            self.assertIn("AAS_AUTOLOOP_FORMAL_POLICY=off", env_text)

    def test_verify_fails_when_defaults_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loop = Path(tmp) / "empty"
            loop.mkdir()
            errors = self.apply.verify_effective(loop, "formal")
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
            loop.mkdir()
            snap = self.proc.status_snapshot(loop)
            self.assertIn("default_backend", snap)
            self.assertEqual(snap["default_backend"], "foreground")
            self.assertIn("matched_pids", snap)


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
            rc = cli.main(
                [
                    "apply-defaults",
                    "--loop",
                    str(loop),
                    "--profile",
                    "formal",
                    "--no-backup",
                ]
            )
            self.assertEqual(rc, 0)
            rc = cli.main(["smoke", "--loop", str(loop), "--profile", "formal"])
            self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
