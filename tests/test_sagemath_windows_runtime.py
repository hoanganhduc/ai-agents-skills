from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SageMathWindowsRuntimeTests(unittest.TestCase):
    def test_wsl_invocation_supports_this_windows_and_propagates_failures(self) -> None:
        wrapper = (ROOT / "canonical" / "runtime" / "skills" / "sagemath" / "run_sage.bat").read_text(
            encoding="utf-8"
        )

        self.assertIn("$wslArgs = @()", wrapper)
        self.assertIn("if ($env:AAS_SAGE_WSL_DISTRO)", wrapper)
        self.assertIn("$wslArgs += @('-d', $env:AAS_SAGE_WSL_DISTRO)", wrapper)
        self.assertIn("& wsl.exe @wslArgs", wrapper)
        self.assertIn("Sage timeout must be a positive integer", wrapper)
        self.assertNotIn("wsl.exe -d !AAS_SAGE_WSL_DISTRO! -- timeout", wrapper)
        self.assertNotIn('wsl.exe -d "!AAS_SAGE_WSL_DISTRO!"', wrapper)
        self.assertIn('set "SAGE_RC=!ERRORLEVEL!"', wrapper)
        self.assertIn("endlocal & exit /b %SAGE_RC%", wrapper)


if __name__ == "__main__":
    unittest.main()
