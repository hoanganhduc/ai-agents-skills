from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from installer.ai_agents_skills.runtime import RUNTIME_SOURCE_ROOT

WORKSPACE = RUNTIME_SOURCE_ROOT / "workspace"
EXAMPLE = WORKSPACE / "config" / "research-compute.example.toml"


class ResearchComputeBootstrapTests(unittest.TestCase):
    """The `bootstrap` subcommand must work on a bare machine: generate the
    per-install config from the example if absent, and never clobber one that
    already exists (so it cannot fight a config another tool manages)."""

    def _make_workspace(self, tmp: Path) -> Path:
        ws = tmp / "ws"
        (ws / "config").mkdir(parents=True)
        shutil.copy(EXAMPLE, ws / "config" / "research-compute.example.toml")
        return ws

    def _run_cli(self, ws: Path, *args: str) -> dict:
        env = dict(os.environ)
        env["OPENCLAW_WORKSPACE"] = str(ws)
        env["PYTHONPATH"] = os.pathsep.join([str(WORKSPACE), env.get("PYTHONPATH", "")])
        # Run from the canonical source tree without writing __pycache__ into it,
        # so the runtime-inventory "only candidate sources" invariant stays intact.
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        proc = subprocess.run(
            [sys.executable, "-m", "research_compute", *args],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def _run_bootstrap(self, ws: Path) -> dict:
        return self._run_cli(ws, "bootstrap", "--no-auth")

    def _install_example_config(
        self,
        ws: Path,
        *,
        routing_order: str | None = None,
        omit_routing_order: bool = False,
    ) -> None:
        text = EXAMPLE.read_text(encoding="utf-8")
        marker = 'routing_order = ["local", "kaggle", "modal", "hetzner", "gha"]'
        self.assertIn(marker, text)
        if omit_routing_order:
            text = text.replace(
                marker + "\n",
                "",
            )
            self.assertNotIn(marker, text)
        elif routing_order is not None:
            text = text.replace(
                marker,
                f"routing_order = {routing_order}",
            )
            self.assertNotIn(marker, text)
        (ws / "config" / "research-compute.toml").write_text(text, encoding="utf-8")

    def test_generates_config_if_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = self._make_workspace(Path(tmp))
            config_path = ws / "config" / "research-compute.toml"
            self.assertFalse(config_path.exists())

            result = self._run_bootstrap(ws)

            self.assertTrue(result["ok"])
            self.assertTrue(result["config"]["generated"])
            self.assertTrue(config_path.exists())

            text = config_path.read_text(encoding="utf-8")
            self.assertIn("[gha]", text)
            self.assertNotIn("example-install", text)
            self.assertEqual(result["config"]["install_id"], socket.gethostname() or "research-compute")
            self.assertIn(result["config"]["platform"], {"linux", "macos", "windows"})

    def test_does_not_clobber_existing_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = self._make_workspace(Path(tmp))
            self._run_bootstrap(ws)
            again = self._run_bootstrap(ws)
            self.assertFalse(again["config"]["generated"])
            self.assertIn("left unchanged", again["config"].get("reason", ""))

    def test_doctor_warns_when_routing_order_deviates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = self._make_workspace(Path(tmp))
            configured = ["local", "modal", "hetzner", "gha"]
            recommended = ["local", "kaggle", "modal", "hetzner", "gha"]
            self._install_example_config(ws, routing_order=json.dumps(configured))

            result = self._run_cli(ws, "doctor")

            self.assertTrue(result["ok"])
            self.assertEqual(result["routing_order"], configured)
            self.assertEqual(
                result["warnings"],
                [
                    {
                        "code": "routing_order_deviation",
                        "message": (
                            "Configured routing_order differs from the recommended priority; "
                            "the planner will honor the configured order."
                        ),
                        "configured": configured,
                        "recommended": recommended,
                    }
                ],
            )

    def test_doctor_has_no_warning_for_recommended_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = self._make_workspace(Path(tmp))
            self._install_example_config(ws, omit_routing_order=True)

            result = self._run_cli(ws, "doctor")

            self.assertTrue(result["ok"])
            self.assertEqual(
                result["routing_order"],
                ["local", "kaggle", "modal", "hetzner", "gha"],
            )
            self.assertEqual(result["warnings"], [])

    def test_doctor_distinguishes_invalid_routing_order_from_valid_deviation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = self._make_workspace(Path(tmp))
            configured = ["modal", "local"]
            self._install_example_config(ws, routing_order=json.dumps(configured))

            result = self._run_cli(ws, "doctor")

            self.assertTrue(result["ok"])
            self.assertEqual(result["routing_order"], configured)
            self.assertEqual(
                [warning["code"] for warning in result["warnings"]],
                ["routing_order_invalid"],
            )
            self.assertIn("must start with local", result["warnings"][0]["error"])

    def test_doctor_reports_non_array_routing_order_without_crashing(self) -> None:
        for configured in ("7", '"local"'):
            with self.subTest(configured=configured), tempfile.TemporaryDirectory() as tmp:
                ws = self._make_workspace(Path(tmp))
                self._install_example_config(ws, routing_order=configured)

                result = self._run_cli(ws, "doctor")

                self.assertTrue(result["ok"])
                self.assertEqual(
                    [warning["code"] for warning in result["warnings"]],
                    ["routing_order_invalid"],
                )
                self.assertIn("must be an array", result["warnings"][0]["error"])

    def test_gh_probe_failure_does_not_crash_bootstrap(self) -> None:
        """A `gh` probe that times out/errors (seen intermittently on Windows
        runners) must not take the whole bootstrap down: config generation and
        the other sections must still succeed."""
        # Importing from canonical/runtime/ in-process must not write __pycache__
        # there (it would break the runtime-inventory "only candidate sources"
        # invariant); other tests sidestep this by running in a subprocess.
        prev_dont_write = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        sys.path.insert(0, str(WORKSPACE))
        try:
            import research_compute.cli as cli
        finally:
            sys.path.remove(str(WORKSPACE))
            sys.dont_write_bytecode = prev_dont_write
        with tempfile.TemporaryDirectory() as tmp:
            ws = self._make_workspace(Path(tmp))
            real_run = cli.subprocess.run

            def fake_run(cmd, *args, **kwargs):
                if list(cmd[:2]) == ["gh", "auth"]:
                    raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 20))
                return real_run(cmd, *args, **kwargs)

            cli.subprocess.run = fake_run
            try:
                summary = cli.command_bootstrap(
                    config_path=ws / "config" / "research-compute.toml",
                    root=ws,
                    install_deps=False,
                    auth=True,
                )
            finally:
                cli.subprocess.run = real_run

        self.assertTrue(summary["config"]["generated"])
        self.assertIn("probe_error", summary["gh"])


if __name__ == "__main__":
    unittest.main()
