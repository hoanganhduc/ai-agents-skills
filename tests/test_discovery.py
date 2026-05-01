from __future__ import annotations

import os
import shlex
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from installer.ai_agents_skills.discovery import (
    candidates_for_platform,
    discover_python_package,
    discover_tool,
    split_command,
    substrate_for,
)
from installer.ai_agents_skills.manifest import load_manifests


class DiscoveryTests(unittest.TestCase):
    def test_python_runtime_discovery_checks_capabilities(self) -> None:
        manifests = load_manifests()
        spec = manifests["dependencies"]["tools"]["python-runtime"]
        result = discover_tool("python-runtime", spec, "linux")
        self.assertIn(result["status"], {"ok", "degraded"})
        self.assertIn("capabilities", result)
        self.assertIn("venv", result["capabilities"])
        self.assertIn("scope", result)

    def test_macos_uses_posix_candidate_fallbacks(self) -> None:
        candidates = {"linux": ["python3"], "windows": ["python.exe"]}
        self.assertEqual(candidates_for_platform(candidates, "macos"), ["python3"])

    def test_wsl_sage_candidate_is_degraded_not_windows_package(self) -> None:
        manifests = load_manifests()
        spec = manifests["dependencies"]["tools"]["sage-runtime"]
        result = discover_tool("sage-runtime", spec, "windows")
        if result["status"] == "missing":
            checked = result.get("checked", [])
            self.assertTrue(any(item.get("scope") == "wsl" for item in checked))
        else:
            self.assertIn(result["scope"], {"wsl", "wsl-local", "wsl-rootfs", "user-local", "system", "repo-local"})

    def test_windows_target_posix_command_is_wsl_substrate(self) -> None:
        self.assertEqual(substrate_for("windows", "/usr/bin/python"), "wsl")
        self.assertEqual(substrate_for("windows", "C:\\Python312\\python.exe"), "windows-native")

    def test_windows_path_lookup_is_degraded_from_non_windows_host(self) -> None:
        if sys.platform.startswith("win"):
            self.skipTest("host can inspect native Windows PATH")
        spec = {"candidates": {"windows": ["pdflatex.exe"]}}
        result = discover_tool("tex-runtime", spec, "windows")
        self.assertEqual(result["status"], "degraded")
        self.assertEqual(result["checked"][0]["status"], "unverified")

    def test_windows_drive_python_runtime_detects_mounted_system_install(self) -> None:
        if sys.platform.startswith("win"):
            self.skipTest("host-native Windows does not use mounted drive mapping")
        with tempfile.TemporaryDirectory() as tmp:
            drive = Path(tmp)
            root = drive / "Users" / "alice"
            python = drive / "Python310" / "python.exe"
            python.parent.mkdir(parents=True)
            root.mkdir(parents=True)
            python.write_text("native windows executable placeholder", encoding="utf-8")
            spec = {"candidates": {"windows": ["C:\\Python3*\\python.exe"]}}
            result = discover_tool("python-runtime", spec, "windows", root)
            self.assertEqual(result["status"], "degraded")
            self.assertIn("Python310", result["command"])
            self.assertFalse(result["capabilities"]["host-executable"])

    def test_windows_tex_runtime_detects_mounted_texlive_install(self) -> None:
        if sys.platform.startswith("win"):
            self.skipTest("host-native Windows does not use mounted drive mapping")
        with tempfile.TemporaryDirectory() as tmp:
            drive = Path(tmp)
            root = drive / "Users" / "alice"
            engine = drive / "texlive" / "2024" / "bin" / "windows" / "pdflatex.exe"
            engine.parent.mkdir(parents=True)
            root.mkdir(parents=True)
            engine.write_text("native windows executable placeholder", encoding="utf-8")
            spec = {"candidates": {"windows": ["C:\\texlive\\*\\bin\\windows\\pdflatex.exe"]}}
            result = discover_tool("tex-runtime", spec, "windows", root)
            self.assertEqual(result["status"], "degraded")
            self.assertIn("texlive", result["command"])
            self.assertFalse(result["capabilities"]["host-executable"])

    def test_python_runtime_prefers_target_root_relative_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            python = root / ".venv" / "bin" / "python"
            python.parent.mkdir(parents=True)
            shutil.copy2(sys.executable, python)
            python.chmod(0o755)
            spec = {
                "candidates": {
                    "linux": ["./.venv/bin/python", "python3"],
                },
            }
            result = discover_tool("python-runtime", spec, "linux", root)
            self.assertIn(result["status"], {"ok", "degraded"})
            executable = shlex.split(str(result["command"]), posix=os.name != "nt")[0].strip("'\"")
            self.assertTrue(Path(executable).resolve().is_relative_to(root.resolve()))
            self.assertEqual(result["scope"], "user-local")

    def test_split_command_strips_windows_executable_quotes(self) -> None:
        command = r"'C:\Users\agent\AppData\Local\Temp\.venv\Scripts\python.exe' --version"
        parts = split_command(command, windows_host=True)
        self.assertEqual(parts[0], r"C:\Users\agent\AppData\Local\Temp\.venv\Scripts\python.exe")
        self.assertEqual(parts[1], "--version")

    def test_windows_python_package_can_be_detected_from_mounted_venv(self) -> None:
        if sys.platform.startswith("win"):
            self.skipTest("host-native Windows does not use mounted drive mapping")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            python = root / ".venv-docling" / "Scripts" / "python.exe"
            package = root / ".venv-docling" / "Lib" / "site-packages" / "docling"
            python.parent.mkdir(parents=True)
            python.write_text("native windows executable placeholder", encoding="utf-8")
            package.mkdir(parents=True)
            result = discover_python_package(
                "docling-python-package",
                "docling",
                None,
                platform="windows",
                root=root,
                python_candidates=[".venv-docling\\Scripts\\python.exe"],
            )
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["detection"], "site-packages")
            self.assertTrue(result["checked"])

    def test_windows_python_package_can_be_detected_from_system_site_packages(self) -> None:
        if sys.platform.startswith("win"):
            self.skipTest("host-native Windows does not use mounted drive mapping")
        with tempfile.TemporaryDirectory() as tmp:
            drive = Path(tmp)
            root = drive / "Users" / "alice"
            package = drive / "Python310" / "Lib" / "site-packages" / "feedparser"
            package.mkdir(parents=True)
            root.mkdir(parents=True)
            result = discover_python_package(
                "feedparser-python-package",
                "feedparser",
                None,
                platform="windows",
                root=root,
                python_candidates=[],
                site_candidates=["C:\\Python3*\\Lib\\site-packages"],
            )
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["detection"], "site-packages")
            self.assertIn("Python310", result["site_package"])

    def test_windows_sage_detects_mounted_wsl_rootfs_candidate(self) -> None:
        if sys.platform.startswith("win"):
            self.skipTest("host-native Windows does not use mounted drive mapping")
        with tempfile.TemporaryDirectory() as tmp:
            drive = Path(tmp)
            root = drive / "Users" / "alice"
            sage = (
                root
                / "AppData"
                / "Local"
                / "Packages"
                / "Ubuntu"
                / "LocalState"
                / "rootfs"
                / "usr"
                / "bin"
                / "sage"
            )
            sage.parent.mkdir(parents=True)
            sage.write_text("#!/bin/sh\n", encoding="utf-8")
            spec = {
                "candidates": {
                    "windows": ["wsl-rootfs:%LOCALAPPDATA%\\Packages\\*\\LocalState\\rootfs\\usr\\bin\\sage"]
                }
            }
            result = discover_tool("sage-runtime", spec, "windows", root)
            self.assertEqual(result["status"], "degraded")
            self.assertEqual(result["scope"], "wsl-rootfs")
            self.assertTrue(result["capabilities"]["sage-path-present"])

    def test_windows_sage_detects_current_wsl_local_candidate(self) -> None:
        if sys.platform.startswith("win"):
            self.skipTest("host-native Windows does not expose a local WSL filesystem")
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            sage = home / "sage" / "sage"
            sage.parent.mkdir(parents=True)
            sage.write_text("#!/bin/sh\nprintf 'SageMath version 10.4\\n'\n", encoding="utf-8")
            sage.chmod(0o755)
            spec = {"candidates": {"windows": [f"wsl-local:{sage}"]}}
            result = discover_tool("sage-runtime", spec, "windows", home)
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["scope"], "wsl-local")
            self.assertIn("SageMath", result["version"])

    def test_windows_sage_reports_wsl_vhdx_as_degraded_inspection_gap(self) -> None:
        if sys.platform.startswith("win"):
            self.skipTest("host-native Windows does not use mounted drive mapping")
        with tempfile.TemporaryDirectory() as tmp:
            drive = Path(tmp)
            root = drive / "Users" / "alice"
            vhdx = (
                root
                / "AppData"
                / "Local"
                / "Packages"
                / "Ubuntu"
                / "LocalState"
                / "ext4.vhdx"
            )
            vhdx.parent.mkdir(parents=True)
            vhdx.write_bytes(b"placeholder")
            spec = {
                "candidates": {
                    "windows": ["wsl-vhdx:%LOCALAPPDATA%\\Packages\\*\\LocalState\\ext4.vhdx"]
                }
            }
            result = discover_tool("sage-runtime", spec, "windows", root)
            self.assertEqual(result["status"], "degraded")
            self.assertEqual(result["scope"], "wsl-vhdx")
            self.assertFalse(result["capabilities"]["sage-inspection"])

    def test_python_package_can_be_detected_from_root_relative_site_packages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / ".codex" / ".local" / "lib" / "python3.10" / "site-packages" / "feedparser"
            package.mkdir(parents=True)
            result = discover_python_package(
                "feedparser-python-package",
                "feedparser",
                None,
                platform="linux",
                root=root,
                python_candidates=[],
                site_candidates=["~/.codex/.local/lib/python*/site-packages"],
            )
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["detection"], "site-packages")


if __name__ == "__main__":
    unittest.main()
