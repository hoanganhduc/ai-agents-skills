from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from installer.ai_agents_skills.discovery import discover_python_package, discover_tool, substrate_for
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

    def test_wsl_sage_candidate_is_degraded_not_windows_package(self) -> None:
        manifests = load_manifests()
        spec = manifests["dependencies"]["tools"]["sage-runtime"]
        result = discover_tool("sage-runtime", spec, "windows")
        if result["status"] == "missing":
            checked = result.get("checked", [])
            self.assertTrue(any(item.get("scope") == "wsl" for item in checked))
        else:
            self.assertIn(result["scope"], {"wsl", "user-local", "system", "repo-local"})

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
            self.assertTrue(str(result["command"]).startswith(str(root)))
            self.assertEqual(result["scope"], "user-local")

    def test_windows_python_package_can_be_detected_from_mounted_venv(self) -> None:
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
