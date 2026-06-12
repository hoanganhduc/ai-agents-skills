from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from installer.ai_agents_skills.openclaw_evidence import build_evidence
from installer.ai_agents_skills.openclaw_target_evidence import (
    build_target_evidence,
    target_evidence_authorizes_real_writes,
    validate_target_evidence,
)
from installer.ai_agents_skills.openclaw_target_gate import openclaw_target_capabilities, openclaw_target_decision
from installer.ai_agents_skills.openclaw_target_manifest import (
    build_diagnostic_target_manifest,
    target_manifest_authorizes_real_writes,
    validate_target_manifest,
)


CAPTURED_AT = "2026-06-12T00:00:00Z"


class OpenClawTargetPhase1Tests(unittest.TestCase):
    def test_target_capabilities_are_non_authorizing(self) -> None:
        capabilities = openclaw_target_capabilities()

        self.assertEqual(capabilities["target"], "openclaw")
        self.assertEqual(capabilities["phase"], "phase1-non-authorizing")
        self.assertEqual(capabilities["real_write_status"], "blocked")
        self.assertFalse(capabilities["real_openclaw_writes_allowed"])
        self.assertFalse(capabilities["approval_eligible"])
        self.assertEqual(capabilities["allowed_surfaces"]["real_system"], [])
        self.assertIn("native-loader", capabilities["required_evidence_classes_before_real_write"])
        self.assertIn("real .openclaw writes", capabilities["no_go_surfaces"])

    def test_gate_blocks_real_system_plan_and_runtime_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("installer.ai_agents_skills.openclaw_target_gate.looks_like_real_system_root", return_value=True):
                plan_decision = openclaw_target_decision(root, operation="plan")
                runtime_decision = openclaw_target_decision(
                    root,
                    operation="runtime",
                    path=root / ".openclaw" / "ai-agents-skills" / "runtime" / "run_skill.sh",
                    agent="runtime",
                )

            self.assertFalse(plan_decision["allowed"])
            self.assertEqual(plan_decision["reason"], "OpenClaw target writes are fake-root only before native target evidence")
            self.assertFalse(runtime_decision["allowed"])
            self.assertIn("OpenClaw runtime writes", runtime_decision["reason"])
            self.assertFalse(runtime_decision["authorizes_real_writes"])
            self.assertFalse(runtime_decision["approval_eligible"])

    def test_gate_allows_fake_root_copy_scope_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = openclaw_target_decision(
                root,
                operation="plan",
                path=root / ".openclaw" / "skills" / "model-router" / "SKILL.md",
                agent="openclaw",
            )

        self.assertTrue(decision["allowed"])
        self.assertEqual(decision["status"], "fake-root-only")
        self.assertFalse(decision["authorizes_real_writes"])

    def test_target_evidence_never_authorizes_real_writes(self) -> None:
        evidence = build_target_evidence(
            evidence_type="native-loader",
            evidence_source="native-probe",
            platform="linux",
            path_style="posix",
            observed_behavior="schema fixture only",
            limitations=["not native OpenClaw execution"],
            captured_at=CAPTURED_AT,
            openclaw_version="openclaw-test",
        )

        validate_target_evidence(evidence)
        self.assertFalse(target_evidence_authorizes_real_writes([evidence]))

        tampered = dict(evidence)
        tampered["authorizes_real_writes"] = True
        with self.assertRaisesRegex(ValueError, "cannot authorize real writes"):
            validate_target_evidence(tampered)

    def test_source_import_evidence_cannot_satisfy_target_evidence(self) -> None:
        source_evidence = build_evidence(
            evidence_type="native-loader",
            agent="codex",
            agent_version="codex-test",
            platform="linux",
            install_mode="reference",
            path_style="posix",
            observed_behavior="source/import evidence fixture",
            limitations=[],
            captured_at=CAPTURED_AT,
        )

        with self.assertRaisesRegex(ValueError, "source/import evidence cannot authorize target writes"):
            validate_target_evidence(source_evidence)

    def test_target_manifest_never_authorizes_real_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest = build_diagnostic_target_manifest(
                target_root=Path(tmp) / ".openclaw",
                created_at=CAPTURED_AT,
                actions=[
                    {
                        "action_id": "target_action_blocked_1",
                        "action_class": "blocked-real-write",
                        "target": {
                            "target_agent": "openclaw",
                            "relative_path": "skills/model-router/SKILL.md",
                        },
                        "diagnostic_only": True,
                        "approval_eligible": False,
                        "writes_real_path": False,
                    }
                ],
            )

        validate_target_manifest(manifest)
        self.assertFalse(target_manifest_authorizes_real_writes(manifest))

        tampered = dict(manifest)
        tampered["approval_eligible"] = True
        with self.assertRaisesRegex(ValueError, "cannot be approval eligible"):
            validate_target_manifest(tampered)

        with self.assertRaisesRegex(ValueError, "cannot be approved"):
            validate_target_manifest(manifest, require_approved=True)

    def test_source_import_apply_manifest_cannot_satisfy_target_manifest(self) -> None:
        with self.assertRaisesRegex(ValueError, "source/import apply manifest cannot authorize target writes"):
            validate_target_manifest({"manifest_schema_version": "openclaw.apply-manifest.v1"})


if __name__ == "__main__":
    unittest.main()
