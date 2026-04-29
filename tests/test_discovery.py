from __future__ import annotations

import unittest

from installer.ai_agents_skills.discovery import discover_tool, substrate_for
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


if __name__ == "__main__":
    unittest.main()
