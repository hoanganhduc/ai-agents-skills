from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import json
import unittest
from pathlib import Path

from installer.ai_agents_skills.manifest import REPO_ROOT
from installer.ai_agents_skills.openclaw_runtime_target_classify import (
    build_support_file_metadata,
    classify_support_file,
    support_file_routing,
)

SCHEMA = json.loads(
    (Path(REPO_ROOT) / "manifest" / "schema" / "openclaw" / "target-support-file.schema.json").read_text("utf-8")
)
_FILE_SCHEMA = SCHEMA["properties"]["files"]["items"]
_FILE_PROPS = _FILE_SCHEMA["properties"]
_FILE_REQUIRED = set(_FILE_SCHEMA["required"])


def _enum(field: str):
    prop = _FILE_PROPS[field]
    if "enum" in prop:
        return prop["enum"]
    if prop.get("type") == "array":
        return prop.get("items", {}).get("enum")
    return None


def assert_record_schema_valid(rec: dict) -> None:
    # additionalProperties: false
    extra = set(rec) - set(_FILE_PROPS)
    assert not extra, f"unknown fields in record: {extra}"
    assert _FILE_REQUIRED <= set(rec), f"missing required: {_FILE_REQUIRED - set(rec)}"
    for field in ("artifact_class", "execution_role", "wrapper_runtime_class", "newline_policy", "mode_policy", "file_type"):
        assert rec[field] in _enum(field), f"{field}={rec[field]!r} not in enum"
    for item in rec["platforms"]:
        assert item in _enum("platforms"), item
    for item in rec["shell_families"]:
        assert item in _enum("shell_families"), item
    comp = rec["compatibility"]
    assert set(comp) == set(_FILE_PROPS["compatibility"]["properties"]), comp


class ClassifyTest(unittest.TestCase):
    def test_routing_decisions(self) -> None:
        self.assertEqual(classify_support_file("x/secrets.example.json")["decision"], "deny")
        self.assertEqual(classify_support_file("x/tool.py")["decision"], "s4")
        self.assertEqual(classify_support_file("x/run_skill.sh")["decision"], "s4")
        self.assertEqual(classify_support_file("x/run_skill.sh")["execution_role"], "runtime-wrapper")
        self.assertEqual(classify_support_file("x/helper.sh", mode="0664", has_shebang=True)["decision"], "s4")
        self.assertEqual(classify_support_file("x/data.json")["decision"], "s3")
        self.assertFalse(classify_support_file("x/data.json")["helper_evidence_required"])
        self.assertTrue(classify_support_file("x/tool.py")["helper_evidence_required"])
        self.assertEqual(classify_support_file("x/blob.bin", file_type="binary")["decision"], "binary-blocked")

    def test_unclassified_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "unclassified"):
            classify_support_file("x/mystery.xyz")

    def test_generated_doc_is_schema_valid_and_strips_decision(self) -> None:
        doc = build_support_file_metadata(
            "demo",
            [
                {"relative_path": "demo/tool.py"},
                {"relative_path": "demo/run_skill.sh", "mode": "0755"},
                {"relative_path": "demo/data.json"},
                {"relative_path": "demo/secrets.example.json"},
            ],
        )
        self.assertEqual(doc["schema_version"], "openclaw.target-support-file.v1")
        self.assertEqual(doc["skill"], "demo")
        self.assertEqual(len(doc["files"]), 4)
        for rec in doc["files"]:
            self.assertNotIn("decision", rec)  # stripped (additionalProperties:false)
            assert_record_schema_valid(rec)
        routes = [support_file_routing(r) for r in doc["files"]]
        self.assertEqual(routes, ["s4", "s4", "s3", "skip"])

    def test_generator_fail_closed_on_unclassified(self) -> None:
        with self.assertRaises(ValueError):
            build_support_file_metadata("demo", [{"relative_path": "demo/weird.xyz"}])


if __name__ == "__main__":
    unittest.main()
