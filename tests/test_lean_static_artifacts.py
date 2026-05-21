from __future__ import annotations

import unittest
from pathlib import Path

from installer.ai_agents_skills.manifest import REPO_ROOT, load_manifests


class LeanStaticArtifactTests(unittest.TestCase):
    def text(self, path: str) -> str:
        return (REPO_ROOT / path).read_text(encoding="utf-8")

    def test_audit_checklist_contains_required_fields(self) -> None:
        text = self.text("canonical/templates/lean-repo-audit-checklist.md")
        required = [
            "license",
            "provenance",
            "Lean version",
            "Mathlib revision",
            "lockfile",
            "CI",
            "secrets",
            "credential",
            "endpoint",
            "publish/deploy",
            "compute risk",
            "do_not_run_commands",
            "GPL-3.0-or-later compatibility conclusion",
        ]
        for item in required:
            with self.subTest(item=item):
                self.assertIn(item, text)
        self.assertIn("Remote scanning is out of scope by default", text)
        self.assertNotIn("git clone", text)

    def test_artifact_manifest_entries_are_portable(self) -> None:
        manifests = load_manifests()
        templates = manifests["artifacts"]["artifacts"]["template"]
        for name in ("lean-repo-audit-checklist", "lean-verifier-policy"):
            with self.subTest(name=name):
                spec = templates[name]
                self.assertEqual(spec["supported_agents"], ["codex", "claude", "deepseek"])
                self.assertTrue((REPO_ROOT / "canonical" / "templates" / spec["source"]).is_file())

    def test_axle_adapter_is_disabled_by_construction(self) -> None:
        text = self.text("canonical/skills/lean-axle-adapter/SKILL.md")
        required = [
            "No default endpoint activation",
            "No credential lookup",
            "No MCP config mutation",
            "No implicit package install",
            "No background server",
            "No live AXLE calls",
            "disabled by construction",
        ]
        for item in required:
            with self.subTest(item=item):
                self.assertIn(item, text)
        for forbidden in ("api_key =", "OPENAI_API_KEY", "AXLE_TOKEN", "mcpServers"):
            self.assertNotIn(forbidden, text)

    def test_no_external_runner_overclaiming(self) -> None:
        combined = "\n".join(
            [
                self.text("canonical/skills/lean-axle-adapter/SKILL.md"),
                self.text("canonical/templates/lean-verifier-policy.md"),
            ]
        )
        self.assertIn("does not imply that", combined)
        self.assertIn("does not run Lean", combined)
        self.assertIn("does not imply theorem-intent match", combined)
        self.assertNotIn("Claude executed", combined)
        self.assertNotIn("Copilot executed", combined)
        self.assertNotIn("DeepSeek executed", combined)
        self.assertNotIn("SafeVerify executed", combined)

    def test_verifier_policy_trust_model_fields(self) -> None:
        text = self.text("canonical/templates/lean-verifier-policy.md")
        required = [
            "Trust Tier Enum",
            "Conjunctive Claim Table",
            "Two-Axis Trust State",
            "Theorem-Intent Review Schema",
            "Allowed-Axiom Policy",
            "Sorry Policy",
            "Unsafe Declaration Policy",
            "Cross-Tier Environment Invariant",
            "full transitive import/dependency closure",
            "resolved Mathlib commit",
            "theorem-intent review record hash",
            "Comparator is inactive",
            "T2_AXLE_ACCEPTED",
            "does not imply that",
        ]
        for item in required:
            with self.subTest(item=item):
                self.assertIn(item, text)

    def test_openclaw_runtime_mcp_credential_and_vendoring_boundaries(self) -> None:
        combined = "\n".join(
            [
                self.text("canonical/skills/lean-axle-adapter/SKILL.md"),
                self.text("canonical/templates/lean-verifier-policy.md"),
            ]
        )
        for forbidden in ("real `.openclaw` behavior", "AXLE runtime script", "mcpServers", "credential config =", "vendored verifier code"):
            self.assertNotIn(forbidden, combined)
        self.assertIn("No MCP config mutation", combined)
        self.assertIn("No credential lookup", combined)

    def test_copilot_is_not_added_to_template_supported_agents(self) -> None:
        manifests = load_manifests()
        templates = manifests["artifacts"]["artifacts"]["template"]
        for name in ("lean-repo-audit-checklist", "lean-verifier-policy"):
            self.assertNotIn("copilot", templates[name]["supported_agents"])


if __name__ == "__main__":
    unittest.main()
