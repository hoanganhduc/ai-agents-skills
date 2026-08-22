from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from installer.ai_agents_skills.runtime import RUNTIME_SOURCE_ROOT
from tests import os_child_env


RUNNERS = RUNTIME_SOURCE_ROOT / "runners"
PROBE = RUNNERS / "credential_projection_probe.py"
CHECKER = RUNNERS / "credential_projection_check.py"


class CredentialProjectionProbeTests(unittest.TestCase):
    def _installed_probe(self, root: Path) -> tuple[Path, Path]:
        runtime = root / "runtime"
        runners = runtime / "runners"
        runners.mkdir(parents=True, mode=0o700)
        probe = runners / PROBE.name
        checker = runners / CHECKER.name
        loader = runtime / "load_secret_env.py"
        shutil.copy2(PROBE, probe)
        shutil.copy2(CHECKER, checker)
        shutil.copy2(RUNNERS / "load_secret_env.py", loader)
        for path in (probe, checker, loader):
            path.chmod(0o600)
        return probe, checker

    def _run(self, secret_text: str, *args: str, pointer: str = "AAS_PROVIDER_SECRETS_FILE"):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            probe, checker = self._installed_probe(root)
            secret = root / "authority.env"
            secret.write_text(secret_text, encoding="utf-8")
            secret.chmod(0o600)
            env = {
                **os_child_env(),
                "PATH": os.environ.get("PATH", ""),
                pointer: str(secret),
                # Ambient cross-lane material must never reach the checker.
                "OPENAI_API_KEY": "ambient-not-authority",
                "HCLOUD_TOKEN": "ambient-compute",
            }
            return subprocess.run(
                [
                    sys.executable,
                    "-I",
                    str(probe),
                    *args,
                    "--checker",
                    str(checker),
                ],
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )

    def test_copilot_authority_projects_one_declared_key_only(self) -> None:
        result = self._run(
            "COPILOT_GITHUB_TOKEN=fixture-token\n",
            "--lane", "provider",
            "--provider-profile", "copilot",
            "--expect-key", "COPILOT_GITHUB_TOKEN",
        )
        if os.name == "nt":
            # Native Windows must fail closed to the PowerShell authority engine.
            self.assertEqual(result.returncode, 3, result.stderr)
            self.assertEqual(
                result.stdout.strip(),
                "FAIL lane=provider reason=native-windows-requires-powershell-loader",
            )
            self.assertNotIn("fixture-token", result.stdout + result.stderr)
            return
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "PASS lane=provider")
        self.assertEqual(result.stderr, "")
        self.assertNotIn("fixture-token", result.stdout + result.stderr)

    def test_help_exits_successfully_without_failure_record(self) -> None:
        result = subprocess.run(
            [sys.executable, "-I", str(PROBE), "--help"],
            env={**os_child_env(), "PATH": os.environ.get("PATH", "")},
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage:", result.stdout)
        self.assertNotIn("FAIL", result.stdout + result.stderr)

    @unittest.skipIf(os.name == "nt", "probe fails closed first: reason=native-windows-requires-powershell-loader")
    def test_copilot_authority_rejects_unrelated_provider_assignment(self) -> None:
        result = self._run(
            "COPILOT_GITHUB_TOKEN=fixture-token\nOPENAI_API_KEY=must-reject\n",
            "--lane", "provider",
            "--provider-profile", "copilot",
            "--expect-key", "COPILOT_GITHUB_TOKEN",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "FAIL lane=provider reason=authority-rejected")
        self.assertEqual(result.stderr, "")
        self.assertNotIn("must-reject", result.stdout + result.stderr)

    @unittest.skipIf(os.name == "nt", "probe fails closed first: reason=native-windows-requires-powershell-loader")
    def test_broad_arl_provider_authority_remains_a_separate_profile(self) -> None:
        result = self._run(
            "OPENAI_API_KEY=fixture-openai\n",
            "--lane", "provider",
            "--provider-profile", "arl",
            "--expect-key", "OPENAI_API_KEY",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), "PASS lane=provider")

    def test_compute_lane_uses_only_compute_pointer(self) -> None:
        result = self._run(
            "HCLOUD_TOKEN=fixture-hcloud\n",
            "--lane", "compute",
            "--expect-key", "HCLOUD_TOKEN",
            pointer="AAS_COMPUTE_SECRETS_FILE",
        )
        if os.name == "nt":
            # Native Windows must fail closed to the PowerShell authority engine.
            self.assertEqual(result.returncode, 3, result.stderr)
            self.assertEqual(
                result.stdout.strip(),
                "FAIL lane=compute reason=native-windows-requires-powershell-loader",
            )
            self.assertNotIn("fixture-hcloud", result.stdout + result.stderr)
            return
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), "PASS lane=compute")

    @unittest.skipIf(os.name == "nt", "probe fails closed first: reason=native-windows-requires-powershell-loader")
    def test_checker_must_be_the_fixed_installed_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            probe, _checker = self._installed_probe(root)
            secret = root / "authority.env"
            secret.write_text("COPILOT_GITHUB_TOKEN=fixture\n", encoding="utf-8")
            secret.chmod(0o600)
            fake = root / "check.py"
            fake.write_text("print('PASS lane=provider')\n", encoding="utf-8")
            env = {"AAS_PROVIDER_SECRETS_FILE": str(secret)}
            result = subprocess.run(
                [
                    sys.executable, "-I", str(probe), "--lane", "provider",
                    "--provider-profile", "copilot", "--expect-key",
                    "COPILOT_GITHUB_TOKEN", "--checker", str(fake),
                ],
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "FAIL lane=provider reason=untrusted-checker")

    def test_native_windows_probe_cannot_bypass_powershell_authority_engine(self) -> None:
        source = PROBE.read_text(encoding="utf-8")
        self.assertIn("sys.dont_write_bytecode = True", source)
        self.assertIn('if os.name == "nt"', source)
        self.assertIn("native-windows-requires-powershell-loader", source)

    def test_manifest_installs_probe_checker_and_root_strict_loader(self) -> None:
        manifest = json.loads(
            (Path(__file__).parents[1] / "manifest" / "runtime.yaml").read_text(
                encoding="utf-8"
            )
        )
        entries = {entry["target"]: entry for entry in manifest["runners"]}
        targets = set(entries)
        self.assertIn("load_secret_env.py", targets)
        self.assertIn("runners/credential_projection_probe.py", targets)
        self.assertIn("runners/credential_projection_check.py", targets)
        for target in (
            "load_secret_env.py",
            "runners/credential_projection_probe.py",
            "runners/credential_projection_check.py",
        ):
            self.assertNotIn("windows", entries[target]["platforms"])


if __name__ == "__main__":
    unittest.main()
