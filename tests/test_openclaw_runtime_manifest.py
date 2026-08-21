from __future__ import annotations

import json
import sys

sys.dont_write_bytecode = True

import unittest

from installer.ai_agents_skills.openclaw_runtime_target_evidence import build_runtime_target_evidence
from installer.ai_agents_skills.openclaw_runtime_target_manifest import (
    approve_runtime_target_manifest,
    build_openclaw_runtime_target_manifest,
    classify_runtime_files,
    openclaw_runtime_authorization_reason,
    openclaw_runtime_content_id,
    validate_runtime_target_manifest,
)

RP = dict(
    target_realpath="/home" "/u/.openclaw",
    managed_skills_realpath="/home" "/u/.openclaw/skills",
    runtime_realpath="/home" "/u/.local/share/ai-agents-skills/runtime",
)


def _ev(etype: str):
    return build_runtime_target_evidence(
        evidence_type=etype, platform="linux", path_style="posix",
        observed_behavior=f"probed {etype}", checks={"t": etype}, **RP)


def _support_evidence():
    return [_ev(t) for t in ("native-loader", "quiescence-lock", "compatibility-tuple-match", "support-file-pre-state")]


def _runtime_evidence(with_helper: bool):
    base = [_ev(t) for t in (
        "native-loader", "quiescence-lock", "neutral-runtime-root", "runtime-pre-state", "compatibility-tuple-match")]
    return base + ([_ev("helper-invocation")] if with_helper else [])


CLEAN_MD = "# Skill\n\nManaged by ai-agents-skills. Uses $AAS_RUNTIME_ROOT and the broker.\n"


class RuntimeManifestTest(unittest.TestCase):
    def test_classify_routes_and_binds_integrity_hash(self) -> None:
        files = [
            {"relative_path": "x/tool.py", "mode": "0644", "source_sha256": "sha256:aaa"},
            {"relative_path": "x/data.json", "mode": "0644", "source_sha256": "sha256:bbb"},
        ]
        records, routing = classify_runtime_files(files)
        self.assertEqual(routing["x/tool.py"], "s4")
        self.assertEqual(routing["x/data.json"], "s3")
        self.assertEqual({r["relative_path"]: r["source_sha256"] for r in records},
                         {"x/tool.py": "sha256:aaa", "x/data.json": "sha256:bbb"})

    def test_no_evidence_is_blocked(self) -> None:
        reason = openclaw_runtime_authorization_reason(
            action_class="managed-support-file", neutral_skill_md=CLEAN_MD,
            runtime_files=[{"relative_path": "x/data.json"}], evidence_items=[])
        self.assertIn("does not authorize", reason)

    def test_leaky_skill_md_is_blocked(self) -> None:
        reason = openclaw_runtime_authorization_reason(
            action_class="managed-support-file", neutral_skill_md="run /home" "/ubuntu/x\n",
            runtime_files=[{"relative_path": "x/data.json"}], evidence_items=_support_evidence())
        self.assertIn("leaks machine-specific paths", reason)

    def test_executable_requires_helper_invocation(self) -> None:
        files = [{"relative_path": "x/tool.py", "mode": "0644"}]
        # runtime evidence without helper-invocation -> blocked (executable present)
        self.assertIsNotNone(openclaw_runtime_authorization_reason(
            action_class="shared-runtime-file", neutral_skill_md=CLEAN_MD,
            runtime_files=files, evidence_items=_runtime_evidence(with_helper=False)))
        # with helper-invocation -> authorized
        self.assertIsNone(openclaw_runtime_authorization_reason(
            action_class="shared-runtime-file", neutral_skill_md=CLEAN_MD,
            runtime_files=files, evidence_items=_runtime_evidence(with_helper=True)))

    def test_build_manifest_when_authorized(self) -> None:
        files = [{"relative_path": "x/data.json", "mode": "0644", "source_sha256": "sha256:bbb"}]
        manifest = build_openclaw_runtime_target_manifest(
            skill="demo", action_class="managed-support-file", neutral_skill_md=CLEAN_MD,
            runtime_files=files, evidence_items=_support_evidence(),
            source_commit="abc123", created_at="2026-06-20T00:00:00Z", **RP)
        self.assertEqual(manifest["manifest_schema_version"], "openclaw.target-manifest.v3")
        self.assertTrue(manifest["manifest_id"].startswith("target_manifest_"))
        self.assertTrue(manifest["content_id"].startswith("content_"))
        self.assertEqual(manifest["routing"]["x/data.json"], "s3")
        self.assertEqual(manifest["approval"]["review_status"], "unreviewed")

    def test_build_manifest_raises_when_unauthorized(self) -> None:
        with self.assertRaisesRegex(ValueError, "not authorized"):
            build_openclaw_runtime_target_manifest(
                skill="demo", action_class="managed-support-file", neutral_skill_md=CLEAN_MD,
                runtime_files=[{"relative_path": "x/data.json"}], evidence_items=[],
                source_commit="abc", created_at="2026-06-20T00:00:00Z", **RP)

    def test_content_id_is_machine_independent(self) -> None:
        files = [{"relative_path": "x/data.json", "source_sha256": "sha256:bbb"}]
        a = openclaw_runtime_content_id(source_commit="c1", skill="demo", neutral_skill_md=CLEAN_MD, runtime_files=files)
        b = openclaw_runtime_content_id(source_commit="c1", skill="demo", neutral_skill_md=CLEAN_MD, runtime_files=files)
        self.assertEqual(a, b)  # same inputs -> same content_id (no path/HOME inputs)
        c = openclaw_runtime_content_id(source_commit="c2", skill="demo", neutral_skill_md=CLEAN_MD, runtime_files=files)
        self.assertNotEqual(a, c)  # different commit -> different content_id

    def _approved(self) -> dict:
        manifest = build_openclaw_runtime_target_manifest(
            skill="demo", action_class="managed-support-file", neutral_skill_md=CLEAN_MD,
            runtime_files=[{"relative_path": "x/data.json", "mode": "0644", "source_sha256": "sha256:bbb"}],
            evidence_items=_support_evidence(),
            source_commit="abc123", created_at="2026-06-20T00:00:00Z", **RP)
        return approve_runtime_target_manifest(manifest, reviewer="alice", reviewed_at="2026-06-20T01:00:00Z")

    def test_approved_manifest_cannot_be_repointed_at_another_skill(self) -> None:
        # Authorization is re-derived from stored content, so it accepts whatever
        # the file carries. What makes the content trustworthy is that it still
        # hashes to the id the reviewer approved.
        tampered = self._approved()
        tampered["skill"] = "other-skill"
        with self.assertRaisesRegex(ValueError, "content_id does not match"):
            validate_runtime_target_manifest(tampered, require_approved=True)

    def test_approved_manifest_cannot_have_its_file_list_swapped(self) -> None:
        approved = self._approved()
        tampered = json.loads(json.dumps(approved))
        tampered["files"][0]["relative_path"] = "x/evil.json"
        tampered["routing"] = {"x/evil.json": approved["routing"]["x/data.json"]}
        self.assertEqual(tampered["manifest_id"], approved["manifest_id"])
        self.assertEqual(tampered["approval"], approved["approval"])
        with self.assertRaisesRegex(ValueError, "content_id does not match"):
            validate_runtime_target_manifest(tampered, require_approved=True)

    def test_approved_manifest_cannot_have_its_routing_rewritten(self) -> None:
        # content_id covers paths and hashes but not the S3/S4 decision, so the
        # routing gets its own re-derivation.
        tampered = self._approved()
        tampered["routing"]["x/data.json"] = "s4"
        with self.assertRaisesRegex(ValueError, "routing does not match"):
            validate_runtime_target_manifest(tampered, require_approved=True)

    def test_approved_manifest_cannot_have_its_target_paths_rewritten(self) -> None:
        # The realpaths decide where an apply writes, and content_id is
        # deliberately machine-independent, so only manifest_id binds them.
        tampered = self._approved()
        tampered["target_realpath"] = "/home" "/u/.openclaw-attacker"
        with self.assertRaisesRegex(ValueError, "content address does not match manifest_id"):
            validate_runtime_target_manifest(tampered, require_approved=True)

    def test_a_manifest_without_source_commit_is_rejected(self) -> None:
        # content_id is keyed on the source commit; a manifest that omits it
        # would otherwise reach the re-derivation and raise KeyError.
        manifest = self._approved()
        del manifest["source_commit"]
        with self.assertRaisesRegex(ValueError, "missing fields: source_commit"):
            validate_runtime_target_manifest(manifest, require_approved=True)


if __name__ == "__main__":
    unittest.main()
