from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "canonical" / "runtime" / "skills" / "manim-math-animation"
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

from mma import doctor, render, tools  # noqa: E402


class ManimMathAnimationRuntimeTests(unittest.TestCase):
    def test_windows_venv_executable_uses_scripts_exe(self) -> None:
        venv = Path("C:/Users/example/.local/share/manim-math-animation-venv")
        self.assertEqual(
            tools.venv_executable(venv, "manim", platform_name="nt"),
            venv / "Scripts" / "manim.exe",
        )

    @unittest.skipUnless(sys.platform.startswith("win"), "native Windows venv discovery test")
    def test_manim_bin_resolves_configured_windows_venv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            venv = Path(tmp)
            cli = venv / "Scripts" / "manim.exe"
            cli.parent.mkdir(parents=True)
            cli.write_text("placeholder", encoding="utf-8")
            with (
                patch.dict("os.environ", {"MMA_VENV": str(venv)}, clear=False),
                patch.object(tools.shutil, "which", return_value=None),
            ):
                self.assertEqual(Path(render.manim_bin()), cli)

    @unittest.skipUnless(sys.platform.startswith("win"), "native Windows venv discovery test")
    def test_explicit_mma_venv_precedes_active_venv_and_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            configured = root / "configured"
            active = root / "active"
            configured_cli = configured / "Scripts" / "manim.exe"
            active_cli = active / "Scripts" / "manim.exe"
            path_cli = root / "path" / "manim.exe"
            for item in (configured_cli, active_cli, path_cli):
                item.parent.mkdir(parents=True, exist_ok=True)
                item.write_text("placeholder", encoding="utf-8")
            with (
                patch.dict(os.environ, {"MMA_VENV": str(configured)}, clear=False),
                patch.object(tools.sys, "executable", str(active / "Scripts" / "python.exe")),
                patch.object(tools.shutil, "which", return_value=str(path_cli)),
            ):
                resolved = tools.find_executable("manim", prefer_configured_venv=True)

        self.assertEqual(Path(resolved or ""), configured_cli)

    def test_doctor_honors_explicit_tool_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manim = root / ("manim.exe" if os.name == "nt" else "manim")
            ffmpeg = root / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
            manim.write_text("placeholder", encoding="utf-8")
            ffmpeg.write_text("placeholder", encoding="utf-8")
            with patch.dict(os.environ, {"MANIM": str(manim), "FFMPEG": str(ffmpeg)}, clear=False):
                self.assertEqual(Path(doctor._which("manim") or ""), manim)
                self.assertEqual(Path(doctor._which("ffmpeg") or ""), ffmpeg)

    def test_nonzero_tool_version_is_not_available(self) -> None:
        failed = subprocess.CompletedProcess([], 2, stdout="", stderr="broken executable")
        with (
            patch.object(doctor, "_which", return_value="manim"),
            patch.object(doctor.subprocess, "run", return_value=failed),
        ):
            self.assertIsNone(doctor._tool_version("manim"))

    def test_doctor_is_not_ready_without_manim_cli(self) -> None:
        def tool_version(name: str) -> str | None:
            return None if name == "manim" else f"{name} version"

        def which(name: str) -> str | None:
            return "latex" if name == "latex" else None

        with (
            patch.object(doctor, "_tool_version", side_effect=tool_version),
            patch.object(doctor, "_which", side_effect=which),
            patch.object(doctor, "_module", return_value=True),
            patch.object(doctor, "_has_font", return_value=False),
        ):
            report = doctor.collect()

        self.assertIsNone(report["system_tools"]["manim"])
        self.assertIn("manim", report["tool_paths"])
        self.assertFalse(report["ready_for_render"])
        self.assertTrue(any("manim CLI" in note for note in report["notes"]))


if __name__ == "__main__":
    unittest.main()
