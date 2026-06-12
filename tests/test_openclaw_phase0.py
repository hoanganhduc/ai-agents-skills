from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

from installer.ai_agents_skills.docs import generate_docs
from installer.ai_agents_skills.manifest import REPO_ROOT, load_manifests


SCHEMA_DIR = REPO_ROOT / "manifest" / "schema" / "openclaw"
FIXTURE_CATALOG = REPO_ROOT / "tests" / "fixtures" / "openclaw" / "adversarial-fixtures.json"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def object_schemas(node: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        if node.get("type") == "object":
            found.append(node)
        for value in node.values():
            found.extend(object_schemas(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(object_schemas(item))
    return found


class OpenClawPhase0SchemaTests(unittest.TestCase):
    expected_schemas = {
        "inventory.schema.json",
        "denylist.schema.json",
        "redaction.schema.json",
        "alias.schema.json",
        "evidence.schema.json",
        "apply-manifest.schema.json",
        "target-support-file.schema.json",
        "target-evidence.schema.json",
        "target-manifest.schema.json",
    }

    def test_schema_files_are_present_versioned_and_strict(self) -> None:
        self.assertEqual(
            {path.name for path in SCHEMA_DIR.glob("*.schema.json")},
            self.expected_schemas,
        )
        for schema_name in self.expected_schemas:
            with self.subTest(schema=schema_name):
                schema = load_json(SCHEMA_DIR / schema_name)
                self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
                self.assertIn("$id", schema)
                self.assertIn("title", schema)
                self.assertEqual(schema["type"], "object")
                self.assertFalse(schema["additionalProperties"])
                for object_schema in object_schemas(schema):
                    self.assertIn("additionalProperties", object_schema)
                    self.assertFalse(object_schema["additionalProperties"])

    def test_inventory_schema_is_sanitized_and_non_installable_by_default(self) -> None:
        schema = load_json(SCHEMA_DIR / "inventory.schema.json")
        properties = schema["properties"]
        self.assertEqual(properties["content_read_policy"]["const"], "deny-by-default")
        self.assertFalse(properties["contains_raw_paths"]["const"])
        self.assertTrue(properties["source_root"]["properties"]["explicit_input"]["const"])
        denied = properties["denied_categories"]["items"]["properties"]
        self.assertEqual(set(denied["read_policy"]["enum"]), {"not-opened", "lstat-only"})

    def test_apply_manifest_schema_forbids_recompute_and_requires_action_ids(self) -> None:
        schema = load_json(SCHEMA_DIR / "apply-manifest.schema.json")
        policy = schema["properties"]["apply_policy"]["properties"]
        self.assertTrue(policy["no_recompute"]["const"])
        self.assertTrue(policy["fail_closed_on_drift"]["const"])
        self.assertTrue(policy["content_addressed"]["const"])
        action = schema["properties"]["actions"]["items"]
        self.assertIn("action_id", action["required"])
        self.assertIn("precondition", action["required"])
        self.assertIn("rollback_refs", action["required"])

    def test_evidence_schema_distinguishes_fixture_and_native_evidence(self) -> None:
        schema = load_json(SCHEMA_DIR / "evidence.schema.json")
        evidence_types = set(schema["properties"]["evidence_type"]["enum"])
        platforms = set(schema["properties"]["platform"]["enum"])
        self.assertIn("fixture-only", evidence_types)
        self.assertIn("native-loader", evidence_types)
        self.assertIn("high-fidelity-loader", evidence_types)
        self.assertIn("wsl-mounted-windows", platforms)
        self.assertIn("ci-container", platforms)

    def test_target_support_file_schema_requires_compatibility_metadata(self) -> None:
        schema = load_json(SCHEMA_DIR / "target-support-file.schema.json")
        support_file = schema["properties"]["files"]["items"]
        required = set(support_file["required"])
        self.assertIn("artifact_class", required)
        self.assertIn("execution_role", required)
        self.assertIn("compatibility", required)
        self.assertIn("helper_evidence_required", required)
        compatibility_required = set(support_file["properties"]["compatibility"]["required"])
        self.assertEqual(
            compatibility_required,
            {
                "platform",
                "path_style",
                "shell_family",
                "wrapper_runtime_class",
                "newline_policy",
                "mode_policy",
            },
        )

    def test_fixture_catalog_covers_required_phase0_risks(self) -> None:
        catalog = load_json(FIXTURE_CATALOG)
        source_fixture_ids = {item["fixture_id"] for item in catalog["source_root_fixtures"]}
        self.assertIn("absent-root", source_fixture_ids)
        self.assertIn("sensitive-root", source_fixture_ids)
        self.assertIn("path-trap-root", source_fixture_ids)
        self.assertIn("malformed-root", source_fixture_ids)

        env_cases = set(catalog["environment_cases"])
        self.assertIn("OPENCLAW_WORKSPACE", env_cases)
        self.assertIn("DEEPSEEK_CONFIG", env_cases)
        self.assertIn("workspace-dot-env", env_cases)

        path_cases = set(catalog["path_style_cases"])
        self.assertIn("windows-unc", path_cases)
        self.assertIn("wsl-mounted-windows", path_cases)
        self.assertIn("unicode-confusable", path_cases)

        failure_cases = set(catalog["failure_injection_cases"])
        self.assertIn("failure-after-each-action", failure_cases)
        self.assertIn("outside-root-write-attempt", failure_cases)

        self.assertEqual(
            set(catalog["ci_tiers"]),
            {"pr_blocking", "release_blocking", "scheduled"},
        )

    def test_generated_docs_include_phase0_boundaries(self) -> None:
        generate_docs(load_manifests())
        text = (REPO_ROOT / "docs" / "openclaw-integration-plan.md").read_text(encoding="utf-8")
        self.assertIn("Phase 0 was contracts only", text)
        self.assertIn("The current implemented gate is Phase 5", text)
        self.assertIn("inert persistence checks", text)
        self.assertIn("Fake-root evidence proves isolation only", text)
        self.assertIn("DeepSeek remains reference-only", text)
        self.assertIn("native loader evidence proves another", text)
        self.assertIn("manifest/schema/openclaw/apply-manifest.schema.json", text)

    def test_generated_openclaw_install_target_plan_keeps_safety_contract(self) -> None:
        generate_docs(load_manifests())
        root_doc = REPO_ROOT / "docs" / "openclaw-install-target-plan.md"
        source_doc = REPO_ROOT / "docs" / "source" / "openclaw-install-target-plan.md"
        text = root_doc.read_text(encoding="utf-8")
        self.assertEqual(text, source_doc.read_text(encoding="utf-8"))

        def section(start_heading, end_heading):
            start = text.index(start_heading)
            end = text.index(end_heading, start + len(start_heading)) if end_heading else len(text)
            return " ".join(text[start:end].split())

        decision = section("## Decision\n", "\n## Target Policy\n")
        target_policy = section("## Target Policy\n", "\n## No-Go Surfaces\n")
        code_design = section("## Code Design Notes\n", "\n## Implementation Issue Breakdown\n")
        implementation_issues = section("## Implementation Issue Breakdown\n", "\n## Required Tests\n")
        required_tests = section("## Required Tests\n", "\n## Acceptance Criteria\n")
        acceptance = section("## Acceptance Criteria\n", None)

        self.assertIn("Normal installer copy-mode may be exercised in fake roots only", decision)
        self.assertIn("The implemented v2 real-system gate can copy only", decision)
        self.assertIn("Native loader evidence is necessary but not sufficient for broader real writes", decision)
        self.assertIn("normal installer flows must not plan or apply any write under a real `.openclaw` tree", target_policy)
        self.assertIn("the only implemented real-system exception", target_policy)
        self.assertIn("v1 target-evidence and target-manifest records remain non-actionable", target_policy)
        self.assertIn("shared runtime-root writes are also fake-root-only", target_policy)
        self.assertIn("`.openclaw/ai-agents-skills/...` is a candidate quarantined namespace only", target_policy)
        self.assertIn("it is not part of the v2 real-system write exception", target_policy)
        self.assertIn("target-support-file.schema.json", target_policy)
        self.assertIn("unclassified OpenClaw support files fail closed", target_policy)
        self.assertIn("compatibility tuple matches every target dimension", target_policy)
        self.assertIn("canonical realpath", target_policy)
        self.assertIn("must not hardcode Codex runtime paths", target_policy)
        self.assertIn("That does not imply real OpenClaw loader support", code_design)
        self.assertIn("openclaw_target_gate.py", code_design)
        self.assertIn("authorizes_real_writes: false", code_design)
        self.assertIn("openclaw.apply-manifest.v1` source/import manifests are rejected", code_design)
        self.assertIn("openclaw_target_apply.py", code_design)
        self.assertIn("Normal `plan`, `install`, `uninstall`, and `rollback` still use the Phase 1", code_design)
        self.assertIn("default OpenClaw registry entry", implementation_issues)
        self.assertIn("default detection includes eligible fake-root `.openclaw` homes", required_tests)
        self.assertIn("target-evidence.schema.json", required_tests)
        self.assertIn("target-manifest.schema.json", required_tests)
        self.assertIn("Real-system v2 target evidence and manifest gate for skill files", implementation_issues)
        self.assertIn("table-driven no-go fixtures cover every category", required_tests)
        self.assertIn("compatibility-tuple-filtered runtime and support-file actions", required_tests)
        self.assertIn("planner/runtime behavior tests reject Codex runtime paths", required_tests)
        self.assertIn("real `.openclaw` write actions outside the v2 skill-file gate", required_tests)
        self.assertIn("openclaw.evidence.v1` source/import evidence cannot approve", required_tests)
        self.assertIn("authorizing Phase 2 records", required_tests)
        self.assertIn("v2 target manifests require approval before apply", required_tests)
        self.assertIn("docs/source OpenClaw install-target plan output matches", required_tests)
        self.assertIn("must fail closed unless OpenClaw is stopped, locked, or otherwise quiescent", required_tests)
        self.assertIn("real-system OpenClaw writes are possible only through approved v2", acceptance)
        self.assertIn("all other real `.openclaw` writes", acceptance)
        self.assertIn("OpenClaw participates in default target discovery", acceptance)
        self.assertIn("dedicated real-system runtime approval gate", acceptance)
        self.assertNotIn("except for optional inert documentation", text)
        self.assertNotIn("future inert docs/templates may live only under", text)
        self.assertNotIn("optional docs/templates only under", text)
        self.assertNotIn("before native OpenClaw target evidence", text)
        self.assertNotIn("After native loader evidence: OpenClaw `auto` may resolve to `copy`", text)
        self.assertNotIn("real active-loader copy is allowed only after the native loader evidence gate", text)


if __name__ == "__main__":
    unittest.main()
