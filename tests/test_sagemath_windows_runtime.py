from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SageMathWindowsRuntimeTests(unittest.TestCase):
    def test_wsl_invocation_supports_this_windows_and_propagates_failures(self) -> None:
        wrapper = (ROOT / "canonical" / "runtime" / "skills" / "sagemath" / "run_sage.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn("$wslArguments = @()", wrapper)
        self.assertIn("if ($env:AAS_SAGE_WSL_DISTRO)", wrapper)
        self.assertIn('$wslArguments += @("-d", $env:AAS_SAGE_WSL_DISTRO)', wrapper)
        self.assertIn("& $wsl.Source @wslArguments", wrapper)
        self.assertIn("Sage timeout must be a positive integer", wrapper)
        self.assertIn("Convert-ToWslPath", wrapper)
        self.assertIn('$wslPathArgument = $WindowsPath.Replace("\\", "/")', wrapper)
        self.assertIn("wslpath -a $wslPathArgument", wrapper)
        self.assertIn("exit $exitCode", wrapper)
        self.assertNotIn("Invoke-Expression", wrapper)

    def test_windows_runtime_and_skill_route_sage_through_powershell(self) -> None:
        manifest = (ROOT / "manifest" / "runtime.yaml").read_text(encoding="utf-8")
        skill = (ROOT / "canonical" / "skills" / "sagemath" / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn('"source": "skills/sagemath/run_sage.ps1"', manifest)
        self.assertIn('"target": "workspace/skills/sagemath/run_sage.ps1"', manifest)
        self.assertIn('run_skill.ps1" "skills/sagemath/run_sage.ps1', skill)
        self.assertFalse((ROOT / "canonical" / "runtime" / "skills" / "sagemath" / "run_sage.bat").exists())

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell wrapper test")
    def test_sage_wrapper_preserves_invalid_input_and_missing_wsl_exit_codes(self) -> None:
        powershell = shutil.which("pwsh") or shutil.which("powershell.exe") or shutil.which("powershell")
        if not powershell:
            self.skipTest("PowerShell executable not found")
        wrapper = ROOT / "canonical" / "runtime" / "skills" / "sagemath" / "run_sage.ps1"
        invalid = subprocess.run(
            [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(wrapper), "--timeout", "0", "print(2)"],
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=30,
        )
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env["PATH"] = tmp
            missing_wsl = subprocess.run(
                [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(wrapper), "print(2)"],
                check=False,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                env=env,
                timeout=30,
            )

        self.assertEqual(invalid.returncode, 2, invalid.stderr)
        self.assertEqual(missing_wsl.returncode, 127, missing_wsl.stderr)


if __name__ == "__main__":
    unittest.main()
