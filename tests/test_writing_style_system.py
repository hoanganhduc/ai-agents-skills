from __future__ import annotations

import json
import re
import unittest
from collections import Counter
from pathlib import Path

from installer.ai_agents_skills.manifest import REPO_ROOT, load_manifests
from installer.ai_agents_skills.render import render_artifact_content


INSTRUCTIONS = REPO_ROOT / "canonical" / "instructions"
SCHEMAS = REPO_ROOT / "canonical" / "schemas" / "writing-style"


def load_json(rel_path: str) -> dict:
    return json.loads((REPO_ROOT / rel_path).read_text(encoding="utf-8"))


def heading_slugs(text: str) -> set[str]:
    slugs: set[str] = set()
    for line in text.splitlines():
        if not line.startswith("#"):
            continue
        heading = line.lstrip("#").strip()
        slug = re.sub(r"[^\w\s-]", "", heading.lower())
        slug = re.sub(r"\s+", "-", slug).strip("-")
        slugs.add(slug)
    return slugs


class WritingStyleSystemTests(unittest.TestCase):
    def test_policy_and_overlay_indexes_resolve_to_markdown_anchors(self) -> None:
        policy = load_json("canonical/instructions/writing-style-settings.index.json")
        overlay = load_json("canonical/instructions/math-manuscript-style.index.json")

        self.assertEqual(policy["schema_version"], "writing-style.policy-index.v1")
        self.assertEqual(overlay["schema_version"], "writing-style.overlay-index.v1")

        policy_text = (REPO_ROOT / policy["policy_ref"]).read_text(encoding="utf-8")
        overlay_text = (REPO_ROOT / overlay["overlay_ref"]).read_text(encoding="utf-8")
        policy_slugs = heading_slugs(policy_text)
        overlay_slugs = heading_slugs(overlay_text)

        policy_ids = {row["id"] for row in policy["requirements"]}
        overlay_ids = {row["id"] for row in overlay["requirements"]}
        self.assertEqual(policy_ids, {f"WS-GEN-{idx:04d}" for idx in range(1, 8)})
        self.assertEqual(overlay_ids, {f"WS-MATH-{idx:04d}" for idx in range(1, 9)})

        for row in policy["requirements"]:
            with self.subTest(requirement=row["id"]):
                self.assertIn(row["markdown_anchor"], policy_slugs)
                self.assertTrue(row["source_ledger_ids"])
                self.assertTrue(row["test_ids"])

        for row in overlay["requirements"]:
            with self.subTest(requirement=row["id"]):
                self.assertIn(row["markdown_anchor"], overlay_slugs)
                self.assertTrue(row["source_ledger_ids"])
                self.assertTrue(row["test_ids"])

    def test_migration_matrix_registry_and_scan_sources_have_no_dangling_ids(self) -> None:
        policy = load_json("canonical/instructions/writing-style-settings.index.json")
        overlay = load_json("canonical/instructions/math-manuscript-style.index.json")
        ledger = load_json("canonical/instructions/writing-style-migration-ledger.json")
        matrix = load_json("canonical/instructions/writing-style-requirements-matrix.json")
        acceptance = load_json("canonical/instructions/writing-style-acceptance-traceability.json")
        registry = load_json("canonical/instructions/writing-style-id-registry.json")
        scan_sources = load_json("canonical/instructions/writing-style-id-scan-sources.json")

        active_ids = {row["id"] for row in policy["requirements"]}
        active_ids |= {row["id"] for row in overlay["requirements"]}
        ledger_ids = {row["edge_id"] for row in ledger["rows"]}
        matrix_ids = {row["id"] for row in matrix["requirements"]}
        acceptance_ids = {row["id"] for row in acceptance["criteria"]}
        rationale_ids = {row["id"] for row in matrix["non_mechanical_rationales"]}

        self.assertEqual(matrix_ids, active_ids)
        self.assertEqual(acceptance_ids, {f"AC-{idx:04d}" for idx in range(1, 43)})

        for row in policy["requirements"] + overlay["requirements"]:
            with self.subTest(requirement=row["id"]):
                self.assertTrue(set(row["source_ledger_ids"]).issubset(ledger_ids))

        for row in ledger["rows"]:
            with self.subTest(edge=row["edge_id"]):
                self.assertTrue(set(row["normative_requirement_ids"]).issubset(active_ids))
                for destination in row["destination_docs"]:
                    self.assertTrue((REPO_ROOT / destination).exists())

        for row in matrix["non_mechanical_rationales"]:
            with self.subTest(rationale=row["id"]):
                self.assertIn(row["requirement_id"], active_ids)

        allocated = set()
        for namespace in registry["namespaces"]:
            allocated.update(namespace["allocated_ids"])
        self.assertTrue(active_ids.issubset(allocated))
        self.assertTrue(ledger_ids.issubset(allocated))
        self.assertTrue(acceptance_ids.issubset(allocated))
        self.assertTrue(rationale_ids.issubset(allocated))

        all_allocated = [item for namespace in registry["namespaces"] for item in namespace["allocated_ids"]]
        duplicates = [item for item, count in Counter(all_allocated).items() if count > 1]
        self.assertEqual(duplicates, [])

        for source in scan_sources["sources"]:
            with self.subTest(source=source["path"]):
                self.assertTrue((REPO_ROOT / source["path"]).exists())
                self.assertTrue(source["id_fields"])

    def test_structured_sidecars_have_matching_schema_files(self) -> None:
        expected = {
            "writing-style-source-universe.json": "source-universe.schema.json",
            "writing-style-id-registry.json": "id-registry.schema.json",
            "writing-style-id-scan-sources.json": "id-scan-sources.schema.json",
            "writing-style-settings.index.json": "policy-index.schema.json",
            "math-manuscript-style.index.json": "overlay-index.schema.json",
            "writing-style-migration-ledger.json": "migration-ledger.schema.json",
            "writing-style-requirements-matrix.json": "requirements-matrix.schema.json",
            "writing-style-acceptance-traceability.json": "acceptance-traceability.schema.json",
            "writing-style-target-propagation.json": "target-propagation.schema.json",
            "writing-style-traceability-mapping.json": "traceability-mapping.schema.json",
        }
        for sidecar, schema in expected.items():
            with self.subTest(sidecar=sidecar):
                self.assertTrue((INSTRUCTIONS / sidecar).exists())
                self.assertTrue((SCHEMAS / schema).exists())

    def test_artifact_profiles_propagate_policy_and_overlay(self) -> None:
        manifests = load_manifests()
        artifact_specs = manifests["artifacts"]["artifacts"]
        profiles = manifests["artifacts"]["artifact_profiles"]

        for doc in ("writing-style-settings", "math-manuscript-style"):
            with self.subTest(doc=doc):
                self.assertIn(doc, artifact_specs["instruction-doc"])
                rendered = render_artifact_content(
                    "instruction-doc",
                    doc,
                    artifact_specs["instruction-doc"][doc],
                    "codex",
                )
                self.assertIn("Managed by ai-agents-skills", rendered)
                self.assertIn("Generated target: codex", rendered)

        for profile in ("writing-workflow", "workflow-instructions", "workflow-artifacts", "serious-research"):
            artifacts = profiles[profile]["artifacts"]
            with self.subTest(profile=profile):
                self.assertIn("instruction-doc:writing-style-settings", artifacts)
                self.assertIn("instruction-doc:math-manuscript-style", artifacts)

    def test_writing_workflows_name_the_shared_style_record_fields(self) -> None:
        workflow_files = [
            "canonical/skills/draft-writing/SKILL.md",
            "canonical/skills/deep-research-workflow/SKILL.md",
            "canonical/skills/research-briefing/SKILL.md",
            "canonical/skills/research-report-reviewer/SKILL.md",
            "canonical/skills/research-verification-gate/SKILL.md",
            "canonical/skills/source-research/SKILL.md",
            "canonical/skills/agent-group-discuss/SKILL.md",
            "canonical/skills/prose/SKILL.md",
            "canonical/skills/paper-review/SKILL.md",
            "canonical/skills/annotated-review/SKILL.md",
            "canonical/skills/research-digest-wrapper/SKILL.md",
            "canonical/skills/rss-news-digest/SKILL.md",
            "canonical/skills/submission-venue-selector/SKILL.md",
            "canonical/skills/autonomous-research-loop/SKILL.md",
        ]
        required_needles = [
            "writing-style-settings.md",
            "math-manuscript-style.md",
            "style_profile_ref",
            "active_overlays",
            "active_requirement_ids",
            "style_applied",
        ]
        for rel_path in workflow_files:
            text = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
            for needle in required_needles:
                with self.subTest(path=rel_path, needle=needle):
                    self.assertIn(needle, text)

    def test_templates_and_router_preserve_shared_style_record_contract(self) -> None:
        for rel_path in (
            "canonical/templates/draft-claim-ledger.md",
            "canonical/templates/draft-revision-map.md",
        ):
            text = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
            with self.subTest(path=rel_path):
                self.assertIn("## Writing Style Record", text)
                self.assertIn("style_profile_ref", text)
                self.assertIn("active_requirement_ids", text)
                self.assertIn("style_applied", text)

        router = (REPO_ROOT / "canonical/instructions/language-style-rules.md").read_text(encoding="utf-8")
        self.assertIn("compatibility router", router)
        self.assertIn("writing-style-settings.md", router)
        self.assertIn("math-manuscript-style.md", router)
        self.assertIn("writing-style-migration-ledger.json", router)

    def test_target_propagation_rows_match_current_supported_targets(self) -> None:
        report = load_json("canonical/instructions/writing-style-target-propagation.json")
        self.assertEqual(report["schema_version"], "writing-style.target-propagation.v1")
        rows = {row["target"]: row for row in report["rows"]}
        self.assertEqual(set(rows), {"codex", "claude", "deepseek", "opencode", "antigravity", "copilot", "openclaw"})

        for target in ("codex", "claude", "deepseek", "opencode", "antigravity"):
            with self.subTest(target=target):
                self.assertEqual(rows[target]["target_status"], "installed")
                self.assertEqual(rows[target]["release_disposition"], "satisfied")

        for target in ("copilot", "openclaw"):
            with self.subTest(target=target):
                self.assertEqual(rows[target]["target_status"], "unsupported")
                self.assertEqual(rows[target]["release_disposition"], "approved-unsupported")

    def test_session_update_rule_requires_pending_record_without_silent_promotion(self) -> None:
        policy = (REPO_ROOT / "canonical/instructions/writing-style-settings.md").read_text(encoding="utf-8")
        pending_schema = load_json("canonical/schemas/writing-style/pending-session-record.schema.json")
        self.assertIn("pending record", policy)
        self.assertIn("Do not promote", policy)
        self.assertIn("approval_state", pending_schema["properties"])


if __name__ == "__main__":
    unittest.main()
