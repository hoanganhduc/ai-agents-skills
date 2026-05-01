from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from installer.ai_agents_skills.cli import main
from installer.ai_agents_skills.openclaw_evidence import build_evidence, native_support_summary, validate_evidence


CAPTURED_AT = "2026-05-01T00:02:00Z"


class OpenClawEvidenceTests(unittest.TestCase):
    def test_fixture_evidence_is_content_addressed_and_does_not_claim_native_support(self) -> None:
        evidence = build_evidence(
            evidence_type="fixture-only",
            agent="deepseek",
            platform="ci-container",
            install_mode="reference",
            path_style="posix",
            shell="none",
            observed_behavior="fixture generated reference docs only",
            limitations=["not a native DeepSeek loader test"],
            captured_at=CAPTURED_AT,
        )

        self.assertTrue(evidence["evidence_id"].startswith("evidence_"))
        validate_evidence(evidence)
        summary = native_support_summary([evidence])
        self.assertEqual(summary["native_support_claims"], [])
        self.assertIn("deepseek", summary["reference_only_without_native_evidence"])

    def test_native_evidence_requires_agent_version(self) -> None:
        with self.assertRaisesRegex(ValueError, "agent_version"):
            build_evidence(
                evidence_type="native-loader",
                agent="codex",
                platform="linux",
                install_mode="reference",
                path_style="posix",
                observed_behavior="native loader saw reference skill",
                limitations=[],
                captured_at=CAPTURED_AT,
            )

    def test_native_evidence_enables_scoped_claim_only(self) -> None:
        evidence = build_evidence(
            evidence_type="native-loader",
            agent="codex",
            agent_version="codex-test-1",
            platform="linux",
            install_mode="reference",
            path_style="posix",
            shell="bash",
            observed_behavior="native loader resolved reference skill from explicit fake target",
            limitations=["single fake target only"],
            captured_at=CAPTURED_AT,
        )

        summary = native_support_summary([evidence])

        self.assertEqual(summary["native_support_claims"], ["codex:linux:reference:posix"])
        self.assertIn("claude", summary["reference_only_without_native_evidence"])
        self.assertIn("deepseek", summary["reference_only_without_native_evidence"])

    def test_cli_record_and_validate_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            evidence_path = Path(tmp) / "evidence.json"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(
                    [
                        "--json",
                        "openclaw-record-evidence",
                        "--evidence-type",
                        "fixture-only",
                        "--evidence-agent",
                        "claude",
                        "--evidence-platform",
                        "ci-container",
                        "--install-mode",
                        "symlink",
                        "--path-style",
                        "posix",
                        "--observed-behavior",
                        "fixture symlink metadata only",
                        "--limitation",
                        "not native loader evidence",
                        "--captured-at",
                        CAPTURED_AT,
                    ]
                )
            self.assertEqual(exit_code, 0)
            evidence = json.loads(output.getvalue())
            evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")

            validate_output = io.StringIO()
            with contextlib.redirect_stdout(validate_output):
                exit_code = main(
                    [
                        "--json",
                        "openclaw-validate-evidence",
                        "--evidence",
                        str(evidence_path),
                    ]
                )
            self.assertEqual(exit_code, 0)
            summary = json.loads(validate_output.getvalue())
            self.assertEqual(summary["status"], "evidence-recorded")
            self.assertEqual(summary["evidence_count"], 1)


if __name__ == "__main__":
    unittest.main()
