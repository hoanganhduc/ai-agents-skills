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


if __name__ == "__main__":
    unittest.main()
