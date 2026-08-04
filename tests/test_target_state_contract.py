import json
import unittest
from pathlib import Path, PurePosixPath

from installer.ai_agents_skills.agents import DEFAULT_AGENT_NAMES


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "manifest" / "target-state.yaml"


class TargetStateContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data = json.loads(MANIFEST.read_text(encoding="utf-8"))

    def test_v3_declares_every_installer_target_once(self) -> None:
        self.assertEqual(self.data["schema"], "ai-agents-skills.target-state.v3")
        self.assertEqual(self.data["schema_version"], 3)
        self.assertEqual(set(self.data["targets"]), set(DEFAULT_AGENT_NAMES))

    def test_contract_contains_no_absolute_or_parent_paths(self) -> None:
        for target_name, target in self.data["targets"].items():
            home = PurePosixPath(target["home"])
            self.assertFalse(home.is_absolute(), target_name)
            self.assertNotIn("..", home.parts, target_name)
            surfaces = target.get("surfaces", [target])
            for authority in (
                authority
                for surface in surfaces
                for authority in surface["credential_authorities"]
            ):
                if authority["kind"] == "environment":
                    self.assertNotIn("/", authority["path"])
                    continue
                path = PurePosixPath(authority["path"])
                self.assertFalse(path.is_absolute(), (target_name, path))
                self.assertNotIn("..", path.parts, (target_name, path))

    def test_each_target_has_cli_runtime_credentials_and_readiness(self) -> None:
        for target_name, target in self.data["targets"].items():
            with self.subTest(target=target_name):
                surfaces = target.get("surfaces", [target])
                self.assertTrue(surfaces)
                for surface in surfaces:
                    self.assertTrue(surface["cli_candidates"])
                    self.assertEqual(surface["version_argv"], ["--version"])
                    self.assertTrue(surface["credential_authorities"])
                self.assertTrue(target["runtime_requirements"])
                self.assertIn("cli-version", target["readiness"])
                self.assertIn("managed-skill-visibility", target["readiness"])

    def test_antigravity_target_distinguishes_agy_and_gemini(self) -> None:
        surfaces = self.data["targets"]["antigravity"]["surfaces"]
        self.assertEqual([surface["id"] for surface in surfaces], ["antigravity", "gemini"])
        self.assertEqual(surfaces[0]["cli_candidates"], ["agy"])
        self.assertEqual(surfaces[1]["cli_candidates"], ["gemini"])

    def test_secret_values_are_forbidden(self) -> None:
        self.assertFalse(self.data["scope"]["credential_values_allowed"])
        serialized = MANIFEST.read_text(encoding="utf-8").lower()
        for forbidden in ("api_key\":", "access_token\":", "refresh_token\":"):
            self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    unittest.main()
