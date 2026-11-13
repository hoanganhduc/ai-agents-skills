from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from installer.ai_agents_skills.manifest import REPO_ROOT
from installer.ai_agents_skills.manifest import load_manifests
from installer.ai_agents_skills.selectors import resolve_artifacts, resolve_skills

# The computation-evidence tests load runtime modules in this process.
sys.dont_write_bytecode = True

DEEP_RESEARCH = REPO_ROOT / "canonical" / "skills" / "deep-research-workflow"
CROSS_AGENT = REPO_ROOT / "canonical" / "skills" / "cross-agent-delegation"
TEMPLATES = REPO_ROOT / "canonical" / "templates"
RUNTIME_WORKSPACE = REPO_ROOT / "canonical" / "runtime" / "workspace"
DEEP_RESEARCH_RUNTIME = REPO_ROOT / "canonical" / "runtime" / "skills" / "deep-research-workflow" / "deep_research_workflow.py"
GRAPH_VERIFIER_RUNTIME = REPO_ROOT / "canonical" / "runtime" / "skills" / "graph-verifier" / "graph_verifier.py"
SAGE_COUNTEREXAMPLE_TEMPLATE = (
    REPO_ROOT / "canonical" / "runtime" / "skills" / "sagemath" / "templates" / "counterexample_search.sage"
)
RESEARCH_COMPUTE_RUNNER = RUNTIME_WORKSPACE / "research_compute" / "template_runner.py"


class Args:
    skill = None
    skills = None
    profile = None
    exclude = None
    no_skills = False
    artifact = None
    artifacts = None
    artifact_profile = None
    exclude_artifact = None


class ResearchWorkflowIntegrationDocTests(unittest.TestCase):
    def read(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")

    def validate_research_dir(self, research_dir: Path, *args: str) -> tuple[int, dict]:
        env = {**os.environ, "AAS_RUNTIME_WORKSPACE": str(RUNTIME_WORKSPACE)}
        completed = subprocess.run(
            [sys.executable, str(DEEP_RESEARCH_RUNTIME), "validate", "--dir", str(research_dir), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            check=False,
        )
        return completed.returncode, json.loads(completed.stdout)

    def write_jsonl(self, path: Path, rows: list[dict]) -> None:
        path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")

    def write_minimal_delivery(
        self,
        research_dir: Path,
        *,
        decision: str = "not-ready",
        report_ref: str = "",
        guard_output_ids: list[str] | None = None,
    ) -> None:
        blockers = [] if decision == "ready" else [{"blocker_id": "B1", "description": "not complete"}]
        gaps = [] if decision == "ready" else ["not complete"]
        (research_dir / "delivery.json").write_text(
            json.dumps({
                "decision": decision,
                "report_ref": report_ref,
                "checked_at": "2026-05-28T00:00:00Z",
                "guard_output_ids": [] if guard_output_ids is None else guard_output_ids,
                "blockers": blockers,
                "gaps": gaps,
                "caveats": [],
            }),
            encoding="utf-8",
        )

    def paper_source_with_library_check(self, source: dict) -> dict:
        source = dict(source)
        source.update({
            "library_check_tool": "zotero",
            "library_checked_at": "2026-05-28T00:00:00Z",
            "library_check_ref": "library/zotero-S1.json",
        })
        return source

    def write_finalizable_v2_support(self, research_dir: Path) -> None:
        (research_dir / "library").mkdir(exist_ok=True)
        (research_dir / "library" / "zotero-S1.json").write_text("{}\n", encoding="utf-8")
        (research_dir / "model").mkdir(exist_ok=True)
        (research_dir / "model" / "catalog.json").write_text("{}\n", encoding="utf-8")
        self.write_jsonl(
            research_dir / "guards.jsonl",
            [
                {
                    "guard_output_id": "G1",
                    "guard": "EvidenceGuard",
                    "status": "pass",
                    "claim_or_scope_ref": "C1",
                    "source_ids": [],
                    "evidence_ids": ["E-REPORT"],
                    "inspected_artifacts": ["report.md"],
                    "gap": "",
                    "blocking": False,
                    "recommended_action": "none",
                },
                {
                    "guard_output_id": "G2",
                    "guard": "VerifyGuard",
                    "status": "pass",
                    "claim_or_scope_ref": "C1",
                    "source_ids": [],
                    "evidence_ids": ["E-REPORT"],
                    "inspected_artifacts": ["report.md"],
                    "gap": "",
                    "blocking": False,
                    "recommended_action": "none",
                },
            ],
        )
        (research_dir / "model_freshness.json").write_text(
            json.dumps({
                "schema_version": "deep-research.model-freshness.v1",
                "resolved_model": "test-frontier",
                "resolved_thinking": "xhigh",
                "model_catalog_source": "test",
                "model_catalog_ref": "model/catalog.json",
                "freshness_checked_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                "model_freshness_max_age_seconds": 86400,
                "provider_cli_version": "not-applicable",
                "provider_cli_status": "not_applicable",
                "freshness_source": "test",
            }),
            encoding="utf-8",
        )

    def report_evidence(self) -> dict:
        return {
            "schema_version": "deep-research.evidence.v2",
            "evidence_id": "E-REPORT",
            "evidence_type": "report",
            "source_ids": ["S1"],
            "claim_ids": ["C1"],
            "artifact_ref": "report.md",
            "summary": "Checked report artifact.",
            "inspection_status": "checked",
            "redaction_status": "safe",
            "sensitivity_class": "public",
            "created_at": "2026-05-28T00:00:00Z",
            "limitations": [],
        }

    def make_structured_dir(self, tmp: str, *, v2: bool = False, formal: bool = False) -> Path:
        research_dir = Path(tmp) / "research"
        research_dir.mkdir()
        self.write_jsonl(research_dir / "sources.jsonl", [])
        self.write_jsonl(research_dir / "claims.jsonl", [])
        self.write_jsonl(research_dir / "guards.jsonl", [])
        self.write_minimal_delivery(research_dir)
        if v2:
            (research_dir / "research_schema.json").write_text(
                json.dumps({"schema_version": "deep-research.run.v2"}),
                encoding="utf-8",
            )
            self.write_jsonl(research_dir / "evidence.jsonl", [])
        if formal:
            formal_dir = research_dir / "formal"
            (formal_dir / "input").mkdir(parents=True)
            (formal_dir / "output").mkdir()
            (formal_dir / "final").mkdir()
            self.write_jsonl(formal_dir / "formal_targets.jsonl", [])
            self.write_jsonl(formal_dir / "statement_equivalence_reviews.jsonl", [])
        return research_dir

    def test_deep_research_runtime_templates_match_canonical_templates(self) -> None:
        for name in ("deep-research-sources.md", "deep-research-analysis.md", "deep-research-report.md"):
            self.assertEqual(
                (TEMPLATES / name).read_text(encoding="utf-8"),
                (RUNTIME_WORKSPACE / "templates" / name).read_text(encoding="utf-8"),
                name,
            )

    def test_compute_workflow_guidance_covers_default_order_and_lane_guards(self) -> None:
        routing = self.read(
            REPO_ROOT / "canonical" / "instructions" / "compute-offload-routing.md"
        )
        self.assertIn("local > Kaggle > Modal > Hetzner > GitHub Actions", routing)

        workflow_templates = (
            "autonomous-research-loop-runbook.md",
            "autonomous-research-loop-portfolio-runbook.md",
            "engineering-delivery-loop-runbook.md",
            "cross-agent-adversarial-review.md",
            "informal-to-lean-formalization-runbook.md",
        )
        required_guard_terms = (
            "Kaggle GPU-hours",
            "Modal USD",
            "Hetzner EUR",
            "Hetzner teardown",
            "GitHub Actions minutes",
        )
        for name in workflow_templates:
            text = self.read(TEMPLATES / name)
            with self.subTest(template=name):
                self.assertIn("local > Kaggle > Modal > Hetzner > GitHub Actions", text)
                self.assertIn("custom configured order is honored", text)
                self.assertIn("all permitted lanes", text)
                self.assertIn("explicit backend override", text)
                for term in required_guard_terms:
                    self.assertIn(term, text)
                self.assertNotIn("local/Modal/GitHub Actions", text)
                self.assertNotIn("Modal/GitHub Actions credit-gated", text)

    def test_deep_research_skill_links_quality_guard_reference(self) -> None:
        skill_text = self.read(DEEP_RESEARCH / "SKILL.md")
        guard_text = self.read(DEEP_RESEARCH / "references" / "research-quality-guards.md")
        self.assertIn("references/research-quality-guards.md", skill_text)
        for guard in ("ScopeGuard", "EvidenceGuard", "VerifyGuard", "BudgetGuard", "RegressionGuard"):
            self.assertIn(guard, guard_text)
        for field in (
            "guard_output_id",
            "claim_or_scope_ref",
            "source_ids",
            "evidence_ids",
            "inspected_artifacts",
            "recommended_action",
        ):
            self.assertIn(field, guard_text)
        self.assertIn("supported `pass` or `warn` output must include", guard_text)
        self.assertIn("Do not collapse guard results into a single aggregate score", guard_text)

    def test_deep_research_skill_says_which_claim_a_counterexample_is_recorded_against(self) -> None:
        """A counterexample is recorded with refutes against a claim that the predicate fails, never the claim it refutes."""
        text = " ".join(self.read(DEEP_RESEARCH / "SKILL.md").split())
        for sentence in (
            "is recorded with `--relation refutes` against a claim that states that the predicate fails on the checked domain",
            "never against the claim it refutes",
        ):
            with self.subTest(sentence=sentence):
                self.assertIn(sentence, text)

    def test_scope_brief_contains_goap_fields_and_blocked_start_gate(self) -> None:
        text = self.read(TEMPLATES / "research-scope-brief.md")
        for field in ("goal_state", "preconditions", "candidate_actions", "success_conditions", "blocked_by"):
            self.assertIn(field, text)
        self.assertIn("multi-step, delegated, ambiguous, blocked, or", text)
        self.assertIn("`blocked_by` is empty or resolved", text)
        self.assertIn("run decision must be `blocked`", text)

    def test_runbook_contains_parent_policy_iteration_and_failure_contracts(self) -> None:
        text = self.read(TEMPLATES / "research-workflow-runbook.md")
        for field in (
            "resolved_model",
            "resolved_thinking",
            "model_policy_source",
            "resolved_at",
            "policy_ref",
            "budget_owner",
            "spent_tokens",
            "spent_usd",
            "depth_used",
            "hops_used",
            "budget_spent",
        ):
            self.assertIn(field, text)
        for field in (
            "iteration_id",
            "guard_output_id",
            "continue",
            "accept",
            "revise",
            "reject",
            "blocked",
            "budget_exhausted",
            "max_iterations",
            "plateau",
        ):
            self.assertIn(field, text)
        self.assertIn("scope_change_required` is not a termination reason", text)
        self.assertIn("Budget state stays in the parent runbook", text)

    def test_verification_checklist_tracks_guard_outputs_without_scores(self) -> None:
        text = self.read(TEMPLATES / "research-verification-checklist.md")
        self.assertIn("Guard Output Summary", text)
        self.assertIn("`guard_output_id`", text)
        self.assertIn("Supported `pass` or `warn` guard outputs cite", text)
        self.assertIn("No aggregate research quality score replaces guard findings", text)

    def test_cross_agent_docs_capture_budget_model_and_secret_rules(self) -> None:
        integration = self.read(CROSS_AGENT / "references" / "research-workflow-integration.md")
        task_contract = self.read(CROSS_AGENT / "references" / "task-packet-contract.md")
        result_contract = self.read(CROSS_AGENT / "references" / "result-packet-contract.md")

        for text in (integration, task_contract):
            self.assertIn("same_resolved_model", text)
            self.assertIn("parent_required_highest_available", text)
            self.assertIn("max_depth", text)
            self.assertIn("max_hops", text)
            self.assertIn("max_tokens", text)
            self.assertIn("max_usd", text)
            self.assertIn("budget_policy_ref", text)
            self.assertIn("parent_budget_owner", text)
        for text in (task_contract, result_contract):
            self.assertIn("Reject these key classes", text)
            self.assertIn("resolved_model", text)
            self.assertIn("resolved_thinking", text)
            self.assertIn("Secret-like key regex", text)
            self.assertIn("secret-like string values", text)
            self.assertIn("Bearer <token-like value>", text)
        self.assertIn("smoke-as-contract", integration)

    def test_serious_research_profile_resolves_enforcement_bundle(self) -> None:
        manifests = load_manifests()
        args = Args()
        args.profile = "serious-research"
        skills = set(resolve_skills(args, manifests))
        expected = {
            "research-briefing",
            "autonomous-research-loop",
            "autonomous-research-loop-runtime",
            "deep-research-workflow",
            "source-research",
            "research-report-reviewer",
            "research-verification-gate",
            "zotero",
            "calibre",
            "getscipapers-requester",
            "paper-lookup",
            "submission-venue-selector",
            "venue-ranking-evidence",
            "docling",
            "database-lookup",
            "paper-review",
            "agent-group-discuss",
            "prose",
            "model-router",
            "cross-agent-delegation",
            "get-available-resources",
            "formal-skeleton-helper",
            "workspace-rearranger",
            "intent-interview",
            "decision-doubt-loop",
            "source-grounded-decisions",
            "adversarial-boundary-gate",
            "behavior-preserving-cleanup",
        }
        self.assertEqual(skills, expected)

        args.artifact_profile = "serious-research"
        artifacts = set(resolve_artifacts(args, manifests))
        self.assertIn(("template", "research-workflow-runbook"), artifacts)
        self.assertIn(("template", "deep-research-sources"), artifacts)
        self.assertIn(("instruction-doc", "cross-provider-delegation"), artifacts)

    def test_deep_research_structured_runtime_init_and_validate(self) -> None:
        env = {**os.environ, "AAS_RUNTIME_WORKSPACE": str(RUNTIME_WORKSPACE)}
        with tempfile.TemporaryDirectory() as tmp:
            init = subprocess.run(
                [
                    sys.executable,
                    str(DEEP_RESEARCH_RUNTIME),
                    "init",
                    "--structured",
                    "--dir",
                    tmp,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                check=False,
            )
            self.assertEqual(init.returncode, 0, init.stderr)
            research_dir = Path(tmp) / "research"
            for name in (
                "sources.md",
                "analysis.md",
                "report.md",
                "sources.jsonl",
                "claims.jsonl",
                "guards.jsonl",
                "delivery.json",
            ):
                self.assertTrue((research_dir / name).is_file(), name)
            self.assertTrue((research_dir / "delegation").is_dir())

            validate = subprocess.run(
                [sys.executable, str(DEEP_RESEARCH_RUNTIME), "validate", "--dir", str(research_dir)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                check=False,
            )
            self.assertEqual(validate.returncode, 0, validate.stderr)
            payload = json.loads(validate.stdout)
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["checked"]["delivery"], 1)

    def test_invalid_formal_init_does_not_create_partial_workspace(self) -> None:
        env = {**os.environ, "AAS_RUNTIME_WORKSPACE": str(RUNTIME_WORKSPACE)}
        invalid_options = (
            ("--formal",),
            ("--structured", "--formal"),
        )
        for options in invalid_options:
            with self.subTest(options=options), tempfile.TemporaryDirectory() as tmp:
                init = subprocess.run(
                    [
                        sys.executable,
                        str(DEEP_RESEARCH_RUNTIME),
                        "init",
                        "--dir",
                        tmp,
                        *options,
                    ],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=env,
                    check=False,
                )
                self.assertNotEqual(init.returncode, 0)
                self.assertFalse((Path(tmp) / "research").exists())

    def test_init_preflights_conflicts_before_writing_any_output(self) -> None:
        env = {**os.environ, "AAS_RUNTIME_WORKSPACE": str(RUNTIME_WORKSPACE)}
        with tempfile.TemporaryDirectory() as tmp:
            research_dir = Path(tmp) / "research"
            research_dir.mkdir()
            (research_dir / "analysis.md").write_text("keep me\n", encoding="utf-8")

            init = subprocess.run(
                [sys.executable, str(DEEP_RESEARCH_RUNTIME), "init", "--dir", tmp],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                check=False,
            )

            self.assertNotEqual(init.returncode, 0)
            self.assertEqual((research_dir / "analysis.md").read_text(encoding="utf-8"), "keep me\n")
            self.assertFalse((research_dir / "sources.md").exists())
            self.assertFalse((research_dir / "report.md").exists())

    def test_init_refuses_symlink_outputs_even_with_force(self) -> None:
        env = {**os.environ, "AAS_RUNTIME_WORKSPACE": str(RUNTIME_WORKSPACE)}
        with tempfile.TemporaryDirectory() as tmp:
            research_dir = Path(tmp) / "research"
            research_dir.mkdir()
            outside = Path(tmp) / "outside.md"
            outside.write_text("keep me\n", encoding="utf-8")
            try:
                (research_dir / "sources.md").symlink_to(outside)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"symlink creation unavailable: {exc}")

            init = subprocess.run(
                [sys.executable, str(DEEP_RESEARCH_RUNTIME), "init", "--dir", tmp, "--force"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                check=False,
            )

            self.assertNotEqual(init.returncode, 0)
            self.assertEqual(outside.read_text(encoding="utf-8"), "keep me\n")
            self.assertFalse((research_dir / "analysis.md").exists())
            self.assertFalse((research_dir / "report.md").exists())

    def test_deep_research_v2_formal_init_and_validate(self) -> None:
        env = {**os.environ, "AAS_RUNTIME_WORKSPACE": str(RUNTIME_WORKSPACE)}
        with tempfile.TemporaryDirectory() as tmp:
            init = subprocess.run(
                [
                    sys.executable,
                    str(DEEP_RESEARCH_RUNTIME),
                    "init",
                    "--structured",
                    "--schema-version",
                    "2",
                    "--formal",
                    "--dir",
                    tmp,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                check=False,
            )
            self.assertEqual(init.returncode, 0, init.stderr)
            research_dir = Path(tmp) / "research"
            for name in (
                "research_schema.json",
                "evidence.jsonl",
                "formal/formal_targets.jsonl",
                "formal/statement_equivalence_reviews.jsonl",
                "formal/artifacts/remote/axle",
                "formal/README.md",
            ):
                self.assertTrue((research_dir / name).exists(), name)

            code, payload = self.validate_research_dir(research_dir)
            self.assertEqual(code, 0, payload)
            self.assertEqual(payload["checked"]["schema_version"], 2)

    def test_v2_schema_artifact_is_parsed_and_version_checked(self) -> None:
        invalid_schemas = (
            ("{not json\n", "JSON_INVALID"),
            (json.dumps({"schema_version": "deep-research.run.v99"}), "RESEARCH_SCHEMA_VERSION_INVALID"),
        )
        for schema_text, expected_code in invalid_schemas:
            with self.subTest(expected_code=expected_code), tempfile.TemporaryDirectory() as tmp:
                research_dir = self.make_structured_dir(tmp, v2=True)
                (research_dir / "research_schema.json").write_text(schema_text, encoding="utf-8")

                code, payload = self.validate_research_dir(research_dir)
                self.assertEqual(code, 1)
                self.assertIn(expected_code, {error["code"] for error in payload["errors"]})

    def test_dangling_v2_or_formal_markers_cannot_downgrade_validation(self) -> None:
        for marker in ("research_schema.json", "formal"):
            with self.subTest(marker=marker), tempfile.TemporaryDirectory() as tmp:
                research_dir = self.make_structured_dir(tmp)
                self.write_jsonl(
                    research_dir / "sources.jsonl",
                    [{"source_id": "S1", "source": "Verified source", "source_type": "web", "library_status": "[IN_LIBRARY]"}],
                )
                self.write_jsonl(
                    research_dir / "claims.jsonl",
                    [{"claim_id": "C1", "claim": "Supported claim.", "source_ids": ["S1"], "evidence_ids": [], "status": "supported"}],
                )
                (research_dir / "report.md").write_text("Clean report.\n", encoding="utf-8")
                self.write_minimal_delivery(research_dir, decision="ready", report_ref="report.md")
                try:
                    (research_dir / marker).symlink_to(research_dir / "missing-marker", target_is_directory=marker == "formal")
                except (OSError, NotImplementedError) as exc:
                    self.skipTest(f"symlink creation unavailable: {exc}")

                code, payload = self.validate_research_dir(research_dir)
                self.assertEqual(code, 1)
                self.assertIn("MISSING_FILE", {error["code"] for error in payload["errors"]})

    def test_deep_research_v1_unresolved_evidence_ids_remain_compatible_until_v2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            research_dir = self.make_structured_dir(tmp)
            self.write_jsonl(
                research_dir / "claims.jsonl",
                [{
                    "claim_id": "C1",
                    "claim": "A legacy claim with local evidence notes.",
                    "source_ids": [],
                    "evidence_ids": ["local-note"],
                    "status": "supported",
                }],
            )
            code, payload = self.validate_research_dir(research_dir)
            self.assertEqual(code, 0, payload)

            (research_dir / "research_schema.json").write_text(
                json.dumps({"schema_version": "deep-research.run.v2"}),
                encoding="utf-8",
            )
            self.write_jsonl(research_dir / "evidence.jsonl", [])
            code, payload = self.validate_research_dir(research_dir)
            self.assertEqual(code, 1)
            self.assertIn("UNKNOWN_EVIDENCE_ID", {error["code"] for error in payload["errors"]})

    def test_deep_research_selftest_reports_named_positive_and_negative_scenarios(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(DEEP_RESEARCH_RUNTIME), "selftest"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["schema_version"], "deep-research.selftest.v1")
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["positive_count"], 4)
        self.assertEqual(payload["negative_count"], 6)
        self.assertEqual(
            {item["name"] for item in payload["scenarios"]},
            {
                "v2_ready_success",
                "v2_ready_failure",
                "v2_ready_with_caveats_success",
                "v2_ready_with_caveats_failure",
                "agd_evidence_success",
                "agd_evidence_failure",
                "weak_computation_failure",
                "formal_promotion_success",
                "formal_promotion_failure",
                "artifact_ref_path_safety",
            },
        )
        self.assertTrue(all(item["passed"] for item in payload["scenarios"]))

    def test_v2_agd_result_validation_uses_evidence_type_not_id_prefix_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            research_dir = self.make_structured_dir(tmp, v2=True)
            agd = {
                "schema_version": "deep-research.evidence.v2",
                "evidence_id": "E1",
                "evidence_type": "agd_result",
                "source_ids": [],
                "claim_ids": [],
                "artifact_ref": "delegation/parsed/participant.json",
                "summary": "AGD result.",
                "inspection_status": "checked",
                "redaction_status": "redacted",
                "sensitivity_class": "private",
                "created_at": "2026-05-28T00:00:00Z",
                "limitations": [],
                "agd_participant_id": "participant-1",
                "agd_round": "1",
                "agd_packet_ref": "delegation/parsed/participant.json",
                "validation_status": "pending",
                "parent_validation_owner": "parent",
            }
            self.write_jsonl(research_dir / "evidence.jsonl", [agd])
            code, payload = self.validate_research_dir(research_dir)
            self.assertEqual(code, 1)
            self.assertIn("AGD_EVIDENCE_NOT_PARENT_VALIDATED", {error["code"] for error in payload["errors"]})

            agd["evidence_type"] = "other"
            agd["evidence_id"] = "E-AGD-1"
            agd["validation_status"] = "parent_validated"
            self.write_jsonl(research_dir / "evidence.jsonl", [agd])
            code, payload = self.validate_research_dir(research_dir)
            self.assertEqual(code, 1)
            self.assertIn("AGD_EVIDENCE_TYPE_REQUIRED", {error["code"] for error in payload["errors"]})

    def test_v2_finalizable_delivery_rejects_invalid_or_stale_model_freshness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            research_dir = self.make_structured_dir(tmp, v2=True)
            self.write_jsonl(
                research_dir / "sources.jsonl",
                [self.paper_source_with_library_check({
                    "source_id": "S1",
                    "source": "Verified paper",
                    "source_type": "paper",
                    "library_status": "[IN_LIBRARY]",
                })],
            )
            self.write_jsonl(
                research_dir / "claims.jsonl",
                [{
                    "claim_id": "C1",
                    "claim": "Supported claim.",
                    "source_ids": ["S1"],
                    "evidence_ids": ["E-REPORT"],
                    "status": "supported",
                }],
            )
            self.write_jsonl(research_dir / "evidence.jsonl", [self.report_evidence()])
            self.write_finalizable_v2_support(research_dir)
            (research_dir / "report.md").write_text("Clean report.\n", encoding="utf-8")
            self.write_minimal_delivery(research_dir, decision="ready", report_ref="report.md", guard_output_ids=["G1", "G2"])

            freshness = json.loads((research_dir / "model_freshness.json").read_text(encoding="utf-8"))
            freshness["model_freshness_max_age_seconds"] = True
            (research_dir / "model_freshness.json").write_text(json.dumps(freshness), encoding="utf-8")
            code, payload = self.validate_research_dir(research_dir)
            self.assertEqual(code, 1)
            self.assertIn("MODEL_FRESHNESS_MAX_AGE_INVALID", {error["code"] for error in payload["errors"]})

            freshness["model_freshness_max_age_seconds"] = 86400
            freshness["freshness_checked_at"] = "2020-01-01T00:00:00Z"
            (research_dir / "model_freshness.json").write_text(json.dumps(freshness), encoding="utf-8")
            code, payload = self.validate_research_dir(research_dir)
            self.assertEqual(code, 1)
            self.assertIn("MODEL_FRESHNESS_STALE", {error["code"] for error in payload["errors"]})

    def test_finalizable_delivery_requires_valid_checked_at(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            research_dir = self.make_structured_dir(tmp)
            self.write_jsonl(
                research_dir / "sources.jsonl",
                [{"source_id": "S1", "source": "Verified source", "source_type": "web", "library_status": "[IN_LIBRARY]"}],
            )
            self.write_jsonl(
                research_dir / "claims.jsonl",
                [{"claim_id": "C1", "claim": "Supported claim.", "source_ids": ["S1"], "evidence_ids": [], "status": "supported"}],
            )
            (research_dir / "report.md").write_text("Clean report.\n", encoding="utf-8")
            self.write_minimal_delivery(research_dir, decision="ready", report_ref="report.md")
            delivery = json.loads((research_dir / "delivery.json").read_text(encoding="utf-8"))
            delivery["checked_at"] = "2026-05-28"
            (research_dir / "delivery.json").write_text(json.dumps(delivery), encoding="utf-8")

            code, payload = self.validate_research_dir(research_dir)
            self.assertEqual(code, 1)
            self.assertIn("TIMESTAMP_INVALID", {error["code"] for error in payload["errors"]})

    def test_validator_reports_invalid_utf8_as_structured_error(self) -> None:
        env = {**os.environ, "AAS_RUNTIME_WORKSPACE": str(RUNTIME_WORKSPACE)}
        with tempfile.TemporaryDirectory() as tmp:
            research_dir = self.make_structured_dir(tmp)
            (research_dir / "sources.jsonl").write_bytes(b"\xff\xfe\n")
            validate = subprocess.run(
                [sys.executable, str(DEEP_RESEARCH_RUNTIME), "validate", "--dir", str(research_dir)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                check=False,
            )

            self.assertEqual(validate.returncode, 1)
            payload = json.loads(validate.stdout)
            self.assertEqual(payload["status"], "failed")
            self.assertIn("FILE_ENCODING_INVALID", {error["code"] for error in payload["errors"]})

    def test_deep_research_validator_rejects_ready_delivery_with_gaps(self) -> None:
        env = {**os.environ, "AAS_RUNTIME_WORKSPACE": str(RUNTIME_WORKSPACE)}
        with tempfile.TemporaryDirectory() as tmp:
            research_dir = Path(tmp) / "research"
            research_dir.mkdir()
            (research_dir / "sources.jsonl").write_text("", encoding="utf-8")
            (research_dir / "claims.jsonl").write_text("", encoding="utf-8")
            (research_dir / "guards.jsonl").write_text("", encoding="utf-8")
            (research_dir / "delivery.json").write_text(
                json.dumps({
                    "decision": "ready",
                    "report_ref": "report.md",
                    "checked_at": "2026-05-26T00:00:00Z",
                    "guard_output_ids": [],
                    "blockers": [],
                    "gaps": ["unchecked evidence"],
                    "caveats": [],
                }),
                encoding="utf-8",
            )
            validate = subprocess.run(
                [sys.executable, str(DEEP_RESEARCH_RUNTIME), "validate", "--dir", str(research_dir)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                check=False,
            )
            self.assertEqual(validate.returncode, 1)
            payload = json.loads(validate.stdout)
            self.assertEqual(payload["status"], "failed")
            self.assertIn("READY_WITH_GAPS", {error["code"] for error in payload["errors"]})

    def test_ready_delivery_checks_report_and_only_relevant_unverified_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            research_dir = self.make_structured_dir(tmp)
            self.write_jsonl(
                research_dir / "sources.jsonl",
                [
                    {"source_id": "S1", "source": "Unverified lead", "source_type": "web", "library_status": "[UNVERIFIED]"},
                    {"source_id": "S2", "source": "Verified paper", "source_type": "paper", "library_status": "[IN_LIBRARY]"},
                ],
            )
            self.write_jsonl(
                research_dir / "claims.jsonl",
                [{
                    "claim_id": "C1",
                    "claim": "The relevant claim uses the verified source.",
                    "source_ids": ["S2"],
                    "evidence_ids": [],
                    "status": "supported",
                }],
            )
            (research_dir / "report.md").write_text("Clean report.\n", encoding="utf-8")
            self.write_minimal_delivery(research_dir, decision="ready", report_ref="report.md")
            code, payload = self.validate_research_dir(research_dir)
            self.assertEqual(code, 0, payload)

            (research_dir / "report.md").write_text("TODO: resolve this.\n", encoding="utf-8")
            code, payload = self.validate_research_dir(research_dir)
            self.assertEqual(code, 1)
            self.assertIn("READY_REPORT_TODO", {error["code"] for error in payload["errors"]})

    def test_v2_formal_target_promotion_requires_matching_equivalence_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            research_dir = self.make_structured_dir(tmp, v2=True, formal=True)
            self.write_jsonl(
                research_dir / "sources.jsonl",
                [self.paper_source_with_library_check({
                    "source_id": "S1",
                    "source": "Formal source",
                    "source_type": "manuscript",
                    "library_status": "[IN_LIBRARY]",
                })],
            )
            self.write_jsonl(
                research_dir / "claims.jsonl",
                [{"claim_id": "C1", "claim": "Formal claim", "source_ids": ["S1"], "evidence_ids": ["E1"], "status": "supported"}],
            )
            self.write_jsonl(
                research_dir / "evidence.jsonl",
                [{
                    "schema_version": "deep-research.evidence.v2",
                    "evidence_id": "E1",
                    "evidence_type": "formal_check",
                    "source_ids": ["S1"],
                    "claim_ids": ["C1"],
                    "artifact_ref": "formal/final/proof.lean",
                    "summary": "Local Lean check metadata.",
                    "inspection_status": "checked",
                    "redaction_status": "safe",
                    "sensitivity_class": "public",
                    "created_at": "2026-05-28T00:00:00Z",
                    "limitations": [],
                    "verification_source": "local_lean",
                }],
            )
            self.write_jsonl(
                research_dir / "formal" / "formal_targets.jsonl",
                [{
                    "schema_version": "deep-research.formal-target.v1",
                    "formal_target_id": "FT1",
                    "claim_ids": ["C1"],
                    "source_ids": ["S1"],
                    "informal_statement_ref": "sources/S1.md#theorem",
                    "lean_statement_ref": "formal/final/proof.lean",
                    "artifact_stage": "final_candidate",
                    "lean_check_status": "typechecked",
                    "placeholder_status": "no_active_placeholders",
                    "trust_base_status": "accepted_trust_base",
                    "statement_relation_status": "equivalent_reviewed",
                    "review_status": "reviewed_by_lead",
                    "claim_support_status": "supports_claim_after_equivalence_review",
                    "formal_check_requirement": "optional",
                    "toolchain": "lean 4",
                    "mathlib": "recorded",
                    "verification_evidence_ids": ["E1"],
                    "statement_equivalence_review_ids": [],
                }],
            )
            code, payload = self.validate_research_dir(research_dir)
            self.assertEqual(code, 1)
            self.assertIn("FORMAL_PROMOTION_REVIEW_ROW_MISSING", {error["code"] for error in payload["errors"]})

    def test_statement_review_rejects_empty_attestations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            research_dir = self.make_structured_dir(tmp, v2=True, formal=True)
            empty_fields = (
                "reviewer",
                "compared_definitions",
                "hypothesis_deltas",
                "quantifier_deltas",
                "conclusion_deltas",
                "boundary_cases",
                "limitations",
                "encoding_assumptions",
            )
            review = {
                "schema_version": "deep-research.statement-equivalence-review.v1",
                "statement_equivalence_review_id": "SER1",
                "formal_target_id": "FT1",
                "review_status": "reviewed_by_lead",
                "relation_status": "equivalent_reviewed",
                "informal_statement_ref": "sources/S1.md#theorem",
                "lean_statement_ref": "formal/final/proof.lean",
                **{field: [] for field in empty_fields},
            }
            self.write_jsonl(research_dir / "formal" / "statement_equivalence_reviews.jsonl", [review])

            code, payload = self.validate_research_dir(research_dir)
            self.assertEqual(code, 1)
            field_errors = [error for error in payload["errors"] if error["code"] == "STATEMENT_REVIEW_FIELD_REQUIRED"]
            for field in empty_fields:
                self.assertTrue(any(field in error["message"] for error in field_errors), field)

    def test_v2_valid_formal_promotion_with_statement_review_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            research_dir = self.make_structured_dir(tmp, v2=True, formal=True)
            self.write_jsonl(
                research_dir / "sources.jsonl",
                [self.paper_source_with_library_check({
                    "source_id": "S1",
                    "source": "Formal source",
                    "source_type": "manuscript",
                    "library_status": "[IN_LIBRARY]",
                })],
            )
            self.write_jsonl(
                research_dir / "claims.jsonl",
                [{
                    "claim_id": "C1",
                    "claim": "Formal claim",
                    "source_ids": ["S1"],
                    "evidence_ids": ["E1", "E-AXLE-1"],
                    "status": "supported",
                }],
            )
            self.write_jsonl(
                research_dir / "evidence.jsonl",
                [
                    self.report_evidence(),
                    {
                        "schema_version": "deep-research.evidence.v2",
                        "evidence_id": "E1",
                        "evidence_type": "formal_check",
                        "source_ids": ["S1"],
                        "claim_ids": ["C1"],
                        "artifact_ref": "formal/final/proof.lean",
                        "summary": "Local Lean check metadata.",
                        "inspection_status": "checked",
                        "redaction_status": "safe",
                        "sensitivity_class": "public",
                        "created_at": "2026-05-28T00:00:00Z",
                        "limitations": [],
                        "verification_source": "local_lean",
                    },
                    {
                        "schema_version": "deep-research.evidence.v2",
                        "evidence_id": "E-AXLE-1",
                        "evidence_type": "axle_remote_check",
                        "source_ids": ["S1"],
                        "claim_ids": ["C1"],
                        "artifact_ref": "formal/artifacts/remote/axle/E-AXLE-1.json",
                        "summary": "Supplemental AXLE remote result.",
                        "inspection_status": "checked",
                        "redaction_status": "redacted",
                        "sensitivity_class": "private",
                        "created_at": "2026-05-28T00:00:00Z",
                        "limitations": ["remote result is supplemental only"],
                        "tool_name": "axiom-axle-mcp",
                        "tool_version": "0.3.3",
                        "endpoint": "https://mcp.axiommath.ai/mcp",
                        "operation": "check",
                        "payload_hash": "sha256:test",
                        "input_encoding_ref": "formal/input/C1.json",
                        "result_status": "passed",
                        "expiry": "2026-12-31T00:00:00Z",
                    },
                ],
            )
            review = {
                "schema_version": "deep-research.statement-equivalence-review.v1",
                "statement_equivalence_review_id": "SER1",
                "formal_target_id": "FT1",
                "reviewer": "lead",
                "review_status": "reviewed_by_lead",
                "relation_status": "equivalent_reviewed",
                "informal_statement_ref": "sources/S1.md#theorem",
                "lean_statement_ref": "formal/final/proof.lean",
                "compared_definitions": "same definitions",
                "hypothesis_deltas": "none",
                "quantifier_deltas": "none",
                "conclusion_deltas": "none",
                "boundary_cases": "none",
                "limitations": "none",
                "encoding_assumptions": "simple finite graph",
            }
            self.write_jsonl(research_dir / "formal" / "statement_equivalence_reviews.jsonl", [review])
            self.write_jsonl(
                research_dir / "formal" / "formal_targets.jsonl",
                [{
                    "schema_version": "deep-research.formal-target.v1",
                    "formal_target_id": "FT1",
                    "claim_ids": ["C1"],
                    "source_ids": ["S1"],
                    "informal_statement_ref": "sources/S1.md#theorem",
                    "lean_statement_ref": "formal/final/proof.lean",
                    "artifact_stage": "final_candidate",
                    "lean_check_status": "typechecked",
                    "placeholder_status": "no_active_placeholders",
                    "trust_base_status": "accepted_trust_base",
                    "statement_relation_status": "equivalent_reviewed",
                    "review_status": "reviewed_by_lead",
                    "claim_support_status": "supports_claim_after_equivalence_review",
                    "formal_check_requirement": "optional",
                    "toolchain": "lean 4",
                    "mathlib": "recorded",
                    "verification_evidence_ids": ["E1", "E-AXLE-1"],
                    "statement_equivalence_review_ids": ["SER1"],
                }],
            )
            self.write_finalizable_v2_support(research_dir)
            (research_dir / "report.md").write_text("Clean report.\n", encoding="utf-8")
            self.write_minimal_delivery(research_dir, decision="ready", report_ref="report.md", guard_output_ids=["G1", "G2"])
            code, payload = self.validate_research_dir(research_dir)
            self.assertEqual(code, 0, payload)

            evidence_rows = [
                json.loads(line)
                for line in (research_dir / "evidence.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            next(row for row in evidence_rows if row["evidence_id"] == "E1")["inspection_status"] = "unchecked"
            self.write_jsonl(research_dir / "evidence.jsonl", evidence_rows)
            code, payload = self.validate_research_dir(research_dir)
            self.assertEqual(code, 1)
            self.assertIn("LOCAL_FORMAL_CHECK_REQUIRED_FOR_PROMOTION", {error["code"] for error in payload["errors"]})

    def test_v2_axle_remote_check_cannot_promote_without_local_formal_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            research_dir = self.make_structured_dir(tmp, v2=True, formal=True)
            self.write_jsonl(
                research_dir / "claims.jsonl",
                [{"claim_id": "C1", "claim": "Formal claim", "source_ids": [], "evidence_ids": ["E-AXLE-1"], "status": "supported"}],
            )
            self.write_jsonl(
                research_dir / "evidence.jsonl",
                [{
                    "schema_version": "deep-research.evidence.v2",
                    "evidence_id": "E-AXLE-1",
                    "evidence_type": "axle_remote_check",
                    "source_ids": [],
                    "claim_ids": ["C1"],
                    "artifact_ref": "formal/artifacts/remote/axle/E-AXLE-1.json",
                    "summary": "AXLE remote result.",
                    "inspection_status": "checked",
                    "redaction_status": "redacted",
                    "sensitivity_class": "private",
                    "created_at": "2026-05-28T00:00:00Z",
                    "limitations": ["remote result is supplemental only"],
                    "tool_name": "axiom-axle-mcp",
                    "tool_version": "0.3.3",
                    "endpoint": "https://axle.axiommath.ai",
                    "operation": "check",
                    "payload_hash": "sha256:test",
                    "input_encoding_ref": "formal/input/C1.json",
                    "result_status": "passed",
                    "expiry": "2026-12-31T00:00:00Z",
                }],
            )
            review = {
                "schema_version": "deep-research.statement-equivalence-review.v1",
                "statement_equivalence_review_id": "SER1",
                "formal_target_id": "FT1",
                "reviewer": "lead",
                "review_status": "reviewed_by_lead",
                "relation_status": "equivalent_reviewed",
                "informal_statement_ref": "sources/S1.md#theorem",
                "lean_statement_ref": "formal/final/proof.lean",
                "compared_definitions": "same definitions",
                "hypothesis_deltas": "none",
                "quantifier_deltas": "none",
                "conclusion_deltas": "none",
                "boundary_cases": "none",
                "limitations": "none",
                "encoding_assumptions": "simple finite graph",
            }
            self.write_jsonl(research_dir / "formal" / "statement_equivalence_reviews.jsonl", [review])
            self.write_jsonl(
                research_dir / "formal" / "formal_targets.jsonl",
                [{
                    "schema_version": "deep-research.formal-target.v1",
                    "formal_target_id": "FT1",
                    "claim_ids": ["C1"],
                    "source_ids": [],
                    "informal_statement_ref": "sources/S1.md#theorem",
                    "lean_statement_ref": "formal/final/proof.lean",
                    "artifact_stage": "final_candidate",
                    "lean_check_status": "typechecked",
                    "placeholder_status": "no_active_placeholders",
                    "trust_base_status": "accepted_trust_base",
                    "statement_relation_status": "equivalent_reviewed",
                    "review_status": "reviewed_by_lead",
                    "claim_support_status": "supports_claim_after_equivalence_review",
                    "formal_check_requirement": "optional",
                    "toolchain": "remote AXLE",
                    "mathlib": "recorded",
                    "verification_evidence_ids": ["E-AXLE-1"],
                    "statement_equivalence_review_ids": ["SER1"],
                }],
            )
            code, payload = self.validate_research_dir(research_dir)
            self.assertEqual(code, 1)
            self.assertIn("LOCAL_FORMAL_CHECK_REQUIRED_FOR_PROMOTION", {error["code"] for error in payload["errors"]})

    def test_v2_expired_axle_remote_check_is_valid_context_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            research_dir = self.make_structured_dir(tmp, v2=True)
            self.write_jsonl(
                research_dir / "claims.jsonl",
                [{"claim_id": "C1", "claim": "Context claim", "source_ids": [], "evidence_ids": ["E-AXLE-OLD"], "status": "provisional"}],
            )
            self.write_jsonl(
                research_dir / "evidence.jsonl",
                [{
                    "schema_version": "deep-research.evidence.v2",
                    "evidence_id": "E-AXLE-OLD",
                    "evidence_type": "axle_remote_check",
                    "source_ids": [],
                    "claim_ids": ["C1"],
                    "artifact_ref": "formal/artifacts/remote/axle/E-AXLE-OLD.json",
                    "summary": "Expired AXLE result retained for context only.",
                    "inspection_status": "checked",
                    "redaction_status": "redacted",
                    "sensitivity_class": "private",
                    "created_at": "2026-05-01T00:00:00Z",
                    "limitations": ["expired; context only"],
                    "tool_name": "axiom-axle-mcp",
                    "tool_version": "0.3.3",
                    "endpoint": "https://axle.axiommath.ai",
                    "operation": "check",
                    "payload_hash": "sha256:test",
                    "input_encoding_ref": "formal/input/C1.json",
                    "result_status": "expired",
                    "expiry": "2026-05-02T00:00:00Z",
                }],
            )
            code, payload = self.validate_research_dir(research_dir)
            self.assertEqual(code, 0, payload)

    def test_v2_lean_declaration_search_is_context_evidence_and_cannot_promote_without_local_formal_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            research_dir = self.make_structured_dir(tmp, v2=True, formal=True)
            self.write_jsonl(
                research_dir / "claims.jsonl",
                [{"claim_id": "C1", "claim": "Formal claim", "source_ids": [], "evidence_ids": ["E-LEANEXPLORE-1"], "status": "supported"}],
            )
            self.write_jsonl(
                research_dir / "evidence.jsonl",
                [{
                    "schema_version": "deep-research.evidence.v2",
                    "evidence_id": "E-LEANEXPLORE-1",
                    "evidence_type": "lean_declaration_search",
                    "source_ids": [],
                    "claim_ids": ["C1"],
                    "artifact_ref": "formal/artifacts/search/leanexplore/E-LEANEXPLORE-1.json",
                    "summary": "LeanExplore declaration search result.",
                    "inspection_status": "checked",
                    "redaction_status": "safe",
                    "sensitivity_class": "public",
                    "created_at": "2026-05-28T00:00:00Z",
                    "limitations": ["declaration retrieval only"],
                    "tool_name": "lean-explore-mcp",
                    "tool_version": "manual",
                    "backend": "local",
                    "operation": "search",
                    "query": "finite tree leaf theorem",
                    "payload_hash": "sha256:test",
                    "input_encoding_ref": "formal/input/C1.json",
                    "result_status": "found",
                }],
            )
            code, payload = self.validate_research_dir(research_dir)
            self.assertEqual(code, 0, payload)

            review = {
                "schema_version": "deep-research.statement-equivalence-review.v1",
                "statement_equivalence_review_id": "SER1",
                "formal_target_id": "FT1",
                "reviewer": "lead",
                "review_status": "reviewed_by_lead",
                "relation_status": "equivalent_reviewed",
                "informal_statement_ref": "sources/S1.md#theorem",
                "lean_statement_ref": "formal/final/proof.lean",
                "compared_definitions": "same definitions",
                "hypothesis_deltas": "none",
                "quantifier_deltas": "none",
                "conclusion_deltas": "none",
                "boundary_cases": "none",
                "limitations": "none",
                "encoding_assumptions": "simple finite graph",
            }
            self.write_jsonl(research_dir / "formal" / "statement_equivalence_reviews.jsonl", [review])
            self.write_jsonl(
                research_dir / "formal" / "formal_targets.jsonl",
                [{
                    "schema_version": "deep-research.formal-target.v1",
                    "formal_target_id": "FT1",
                    "claim_ids": ["C1"],
                    "source_ids": [],
                    "informal_statement_ref": "sources/S1.md#theorem",
                    "lean_statement_ref": "formal/final/proof.lean",
                    "artifact_stage": "final_candidate",
                    "lean_check_status": "typechecked",
                    "placeholder_status": "no_active_placeholders",
                    "trust_base_status": "accepted_trust_base",
                    "statement_relation_status": "equivalent_reviewed",
                    "review_status": "reviewed_by_lead",
                    "claim_support_status": "supports_claim_after_equivalence_review",
                    "formal_check_requirement": "optional",
                    "toolchain": "lean declaration search",
                    "mathlib": "recorded",
                    "verification_evidence_ids": ["E-LEANEXPLORE-1"],
                    "statement_equivalence_review_ids": ["SER1"],
                }],
            )
            code, payload = self.validate_research_dir(research_dir)
            self.assertEqual(code, 1)
            self.assertIn("LOCAL_FORMAL_CHECK_REQUIRED_FOR_PROMOTION", {error["code"] for error in payload["errors"]})

    def test_v2_weak_computation_cannot_be_only_ready_claim_support(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            research_dir = self.make_structured_dir(tmp, v2=True)
            self.write_jsonl(
                research_dir / "claims.jsonl",
                [{"claim_id": "C1", "claim": "A universal graph claim", "source_ids": [], "evidence_ids": ["E1"], "status": "supported"}],
            )
            self.write_jsonl(
                research_dir / "evidence.jsonl",
                [{
                    "schema_version": "deep-research.evidence.v2",
                    "evidence_id": "E1",
                    "evidence_type": "computation",
                    "source_ids": [],
                    "claim_ids": ["C1"],
                    "artifact_ref": "checks/sample.json",
                    "summary": "Sampled graph check.",
                    "inspection_status": "checked",
                    "redaction_status": "safe",
                    "sensitivity_class": "public",
                    "created_at": "2026-05-28T00:00:00Z",
                    "limitations": ["sampled only"],
                    "tool_name": "graph-verifier",
                    "tool_version": "test",
                    "input_encoding_ref": "checks/input.json",
                    "checked_domain": "graphs up to n=5 sample",
                    "graph_model_assumptions": "simple finite graph",
                    "resource_bounds": "1s",
                    "result_status": "partial",
                    "coverage_status": "sampled",
                    "enumeration_method": "sample",
                    "timeout_status": "completed",
                }],
            )
            (research_dir / "report.md").write_text("Clean report.\n", encoding="utf-8")
            self.write_minimal_delivery(research_dir, decision="ready", report_ref="report.md")
            code, payload = self.validate_research_dir(research_dir)
            self.assertEqual(code, 1)
            self.assertIn("READY_CLAIM_RELIES_ONLY_ON_WEAK_COMPUTATION", {error["code"] for error in payload["errors"]})


if __name__ == "__main__":
    unittest.main()


class OpenGaussEvidenceTests(unittest.TestCase):
    def test_opengauss_run_evidence_validator(self) -> None:
        import importlib.util
        from pathlib import Path as P

        path = P(__file__).resolve().parents[1] / "canonical" / "runtime" / "skills" / "deep-research-workflow" / "deep_research_workflow.py"
        spec = importlib.util.spec_from_file_location("drw_og", path)
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)
        self.assertIn("opengauss_run", mod.EVIDENCE_TYPES)

        errors: list = []
        row = {
            "tool_name": "opengauss",
            "run_id": "og-1",
            "workflow": "prove",
            "result_status": "success",
            "input_encoding_ref": "formal/input/C1.json",
            "payload_hash": "sha256:test",
            "limitations": ["provenance only; not formal_check"],
        }
        mod.validate_opengauss_run_evidence(row, P("evidence.jsonl"), errors, 1)
        self.assertEqual(errors, [])

        bad = dict(row)
        bad["tool_name"] = "other"
        errors2: list = []
        mod.validate_opengauss_run_evidence(bad, P("evidence.jsonl"), errors2, 1)
        self.assertTrue(any(e["code"] == "OPENGAUSS_RUN_TOOL_INVALID" for e in errors2))


class DeliveryCheckVerbTests(unittest.TestCase):
    """delivery-check runs validate, then checks the delivery decision, required formal targets and input stability."""

    @classmethod
    def setUpClass(cls) -> None:
        import importlib.util

        spec = importlib.util.spec_from_file_location("drw_delivery_check", DEEP_RESEARCH_RUNTIME)
        assert spec is not None and spec.loader is not None
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)

    def make_run(self, tmp: str, builder) -> Path:
        root = Path(tmp) / "research"
        root.mkdir(parents=True)
        builder(root)
        return root

    def run_cli(self, root: Path) -> tuple[int, dict]:
        env = {**os.environ, "AAS_RUNTIME_WORKSPACE": str(RUNTIME_WORKSPACE)}
        completed = subprocess.run(
            [sys.executable, str(DEEP_RESEARCH_RUNTIME), "delivery-check", "--dir", str(root)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            check=False,
        )
        return completed.returncode, json.loads(completed.stdout)

    def add_required_statement_only_target(self, root: Path) -> None:
        target = self.mod.formal_target(verification_evidence_ids=[])
        target.update({
            "artifact_stage": "stub",
            "placeholder_status": "placeholders_allowed_for_stub",
            "trust_base_status": "not_scanned",
            "statement_relation_status": "not_reviewed",
            "review_status": "not_reviewed",
            "claim_support_status": "supports_formal_statement_only",
            "formal_check_requirement": "required_for_delivery",
            "lean_statement_ref": "formal/stub/ft1.lean",
            "statement_equivalence_review_ids": [],
        })
        self.mod.write_jsonl_rows(root / "formal" / "formal_targets.jsonl", [target])
        self.mod.write_jsonl_rows(root / "formal" / "statement_equivalence_reviews.jsonl", [])

    def test_ready_run_prints_ready_and_exits_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_run(tmp, self.mod.build_v2_ready_success)
            code, payload = self.run_cli(root)
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["verdict"], "READY")
        self.assertEqual(payload["codes"], [])
        self.assertEqual(payload["input_digest_before"], payload["input_digest_after"])

    def test_invalid_run_is_not_ready_with_validate_codes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_run(tmp, self.mod.build_v2_ready_failure)
            expected = {error["code"] for error in self.mod.validate_directory(root)["errors"]}
            code, payload = self.run_cli(root)
        self.assertEqual(code, 1)
        self.assertEqual(payload["verdict"], "NOT READY")
        self.assertTrue(expected)
        self.assertTrue(expected <= set(payload["codes"]), payload["codes"])

    def test_valid_not_ready_run_is_not_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_run(tmp, self.mod.write_minimal_v2_not_ready)
            validate = self.mod.validate_directory(root)
            code, payload = self.run_cli(root)
        self.assertEqual(validate["status"], "ok", validate["errors"])
        self.assertEqual(code, 1)
        self.assertEqual(payload["verdict"], "NOT READY")
        self.assertIn("DELIVERY_DECISION_NOT_FINALIZABLE", payload["codes"])

    def test_required_statement_only_target_passes_validate_but_not_delivery_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_run(tmp, self.mod.build_v2_ready_success)
            self.add_required_statement_only_target(root)
            validate = self.mod.validate_directory(root)
            code, payload = self.run_cli(root)
        # Regression check: validate keeps accepting this run; only the verb refuses it.
        self.assertEqual(validate["status"], "ok", validate["errors"])
        self.assertEqual(code, 1)
        self.assertEqual(payload["verdict"], "NOT READY")
        self.assertIn("DELIVERY_REQUIRED_FORMAL_TARGET_NOT_PROMOTED", payload["codes"])
        self.assertEqual(payload["unpromoted_required_formal_targets"], ["FT1"])

    def test_input_changed_during_check_is_not_ready(self) -> None:
        mod = self.mod
        original = mod.validate_directory

        def validate_then_edit(root, *args, **kwargs):
            result = original(root, *args, **kwargs)
            (root / "report.md").write_text("Edited during the check.\n", encoding="utf-8")
            return result

        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_run(tmp, mod.build_v2_ready_success)
            mod.validate_directory = validate_then_edit
            try:
                result = mod.delivery_check(root)
            finally:
                mod.validate_directory = original
        self.assertEqual(result["verdict"], "NOT READY")
        self.assertIn("DELIVERY_INPUT_CHANGED", result["codes"])
        self.assertNotEqual(result["input_digest_before"], result["input_digest_after"])

    def test_delivery_check_hashes_delegation_packets(self) -> None:
        """validate reads each delegation/*.json packet, so delivery-check hashes the packets before and after validate.

        A packet changed after validate returns makes the run NOT READY, and
        input_files lists the packet. An unchanged packet keeps the run READY,
        a regression check that held before packets were hashed.
        """
        packet = {
            "schema_version": "cross-agent-delegation.task.v1",
            "packet_id": "pkt-1",
            "recipient_profile": {"profile_id": "reviewer", "profile_version": "v1", "execution_status": "reference_only"},
            "intent": "Review the claim.",
            "requested_actions": ["review"],
            "side_effects": {"writes_files": False, "external_service_posts": False, "network_calls": False, "subprocesses": False},
            "context_policy": {"forward_raw_chat": False, "forward_system_instructions": False},
            "confirmation_requirement": "parent_decides_outside_packet",
            "failure_policy": "block",
        }
        original = self.mod.validate_directory

        def validate_then_append(root: Path, *args, **kwargs) -> dict:
            result = original(root, *args, **kwargs)
            with (root / "delegation" / "task-1.json").open("ab") as handle:
                handle.write(b"\n")
            return result

        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_run(tmp, self.mod.build_v2_ready_success)
            self.mod.write_json(root / "delegation" / "task-1.json", packet)
            unchanged = self.mod.delivery_check(root)
            with mock.patch.object(self.mod, "validate_directory", side_effect=validate_then_append):
                changed = self.mod.delivery_check(root)
        self.assertEqual((unchanged["verdict"], unchanged["codes"]), ("READY", []), unchanged)
        self.assertEqual(unchanged["checked"]["delegation_packets"], 1)
        self.assertIn("delegation/task-1.json", [name for name, _ in unchanged["input_files"]])
        self.assertEqual((changed["verdict"], changed["codes"]), ("NOT READY", ["DELIVERY_INPUT_CHANGED"]), changed)
        self.assertIn("delegation/task-1.json", [name for name, _ in changed["input_files"]])

    def make_v1_ready_run(self, root: Path) -> None:
        """A finalizable v1 run: validate does not read model_freshness.json here."""
        self.mod.build_v2_ready_success(root)
        (root / "research_schema.json").unlink()
        (root / "evidence.jsonl").write_text("", encoding="utf-8")

    def test_input_that_cannot_be_hashed_is_not_ready(self) -> None:
        mod = self.mod
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_run(tmp, self.make_v1_ready_run)
            baseline = mod.delivery_check(root)
            original = mod.file_sha256
            mod.file_sha256 = lambda path: "unreadable" if path.name == "model_freshness.json" else original(path)
            try:
                unreadable = mod.delivery_check(root)
            finally:
                mod.file_sha256 = original
            (root / "model_freshness.json").unlink()
            (root / "model_freshness.json").mkdir()
            validate = mod.validate_directory(root)
            not_regular = mod.delivery_check(root)
        self.assertEqual(baseline["verdict"], "READY", baseline["codes"])
        self.assertEqual(validate["status"], "ok", validate["errors"])
        for result in (unreadable, not_regular):
            self.assertEqual(result["verdict"], "NOT READY")
            self.assertIn("DELIVERY_INPUT_UNREADABLE", result["codes"])

    def test_cyclic_report_ref_gives_a_structured_not_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            not_ready = self.make_run(tmp, self.mod.write_minimal_v2_not_ready)
            delivery = json.loads((not_ready / "delivery.json").read_text(encoding="utf-8"))
            delivery["report_ref"] = "report.md"
            self.mod.write_json(not_ready / "delivery.json", delivery)
            ready = Path(tmp) / "ready" / "research"
            ready.mkdir(parents=True)
            self.mod.build_v2_ready_success(ready)
            (ready / "report.md").unlink()
            try:
                os.symlink("report.md", not_ready / "report.md")
                os.symlink("report.md", ready / "report.md")
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"symlinks unavailable: {exc}")
            results = [self.run_cli(not_ready), self.run_cli(ready)]
        for code, payload in results:
            self.assertEqual(code, 1, payload)
            self.assertEqual(payload["verdict"], "NOT READY")

    def test_an_input_linked_to_an_over_long_name_gives_a_report_not_a_traceback(self) -> None:
        """A symbolic link to a 300-character name, as a delegation packet or a hashed input, gives NOT READY.

        Path.is_file() and Path.exists() on such a link raise OSError
        (ENAMETOOLONG) on Python 3.12 on Linux. The input snapshot, which runs
        before validate, raised it: from listing delegation/*.json and from
        hashing guards.jsonl. The snapshot now records the delegation directory
        or the file as unreadable, validate's own crash on the same link stays
        DELIVERY_VALIDATE_CRASHED, and the CLI prints its JSON and exits 1.
        Each case is skipped where symbolic links cannot be created or where
        is_file() does not raise for the link.
        """
        expected = ("NOT READY", ["DELIVERY_INPUT_UNREADABLE", "DELIVERY_VALIDATE_CRASHED"])
        for link_name, marker in (("delegation/p.json", "<delegation>"), ("guards.jsonl", "guards.jsonl")):
            with self.subTest(link_name), tempfile.TemporaryDirectory() as tmp:
                root = self.make_run(tmp, self.mod.build_v2_ready_success)
                link = root / link_name
                if link.exists():
                    link.unlink()
                try:
                    os.symlink("a" * 300, link)
                except (OSError, NotImplementedError) as exc:
                    self.skipTest(f"symlinks unavailable: {exc}")
                try:
                    link.is_file()
                except OSError:
                    pass
                else:
                    self.skipTest("Path.is_file() does not raise for a link to an over-long name here")
                result = self.mod.delivery_check(root)
                self.assertEqual((result["verdict"], result["codes"]), expected, result)
                self.assertIn([marker, "unreadable"], result["input_files"])
                env = {**os.environ, "AAS_RUNTIME_WORKSPACE": str(RUNTIME_WORKSPACE)}
                completed = subprocess.run(
                    [sys.executable, str(DEEP_RESEARCH_RUNTIME), "delivery-check", "--dir", str(root)],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=env,
                    check=False,
                )
                try:
                    payload = json.loads(completed.stdout)
                except json.JSONDecodeError:
                    self.fail(f"delivery-check printed no JSON (exit {completed.returncode}): {completed.stderr.strip()[-400:]}")
                self.assertEqual((completed.returncode, payload["verdict"], payload["codes"]), (1, *expected), payload)


SAGE_STUB_VERSION = "SageMath version 0.0 (test stub)"
# True on every simple graph: the run passes and is bounded_complete.
SAGE_CLEAN_CONJECTURE = "G.size() <= G.order() * (G.order() - 1) // 2"
# Raises on every edgeless graph: the run is partial, with evaluation errors.
SAGE_RAISING_CONJECTURE = "1 / G.size() > 0"
# False on every complete graph with an edge: the run finds counterexamples.
SAGE_REFUTED_CONJECTURE = "G.size() == 0"
COMPUTATION_BLOCK_FIELDS = (
    "tool_name",
    "tool_version",
    "input_encoding_ref",
    "checked_domain",
    "graph_model_assumptions",
    "resource_bounds",
    "enumeration_method",
    "timeout_status",
    "result_status",
    "coverage_status",
    "evaluated_predicate",
)


class StubSageGraph:
    """The Sage graph methods counterexample_search.sage and the conjectures above call."""

    def __init__(self, order: int, complete: bool) -> None:
        self._order = order
        self._edges = [(u, v) for u in range(order) for v in range(u + 1, order)] if complete else []

    def order(self) -> int:
        return self._order

    def size(self) -> int:
        return len(self._edges)

    def edges(self, labels: bool = True) -> list:
        return [(u, v, None) if labels else (u, v) for u, v in self._edges]

    def chromatic_number(self) -> int:
        return self._order if self._edges else 1

    def clique_number(self) -> int:
        return self._order if self._edges else 1


def stub_sage_graphs(n: int):
    """Stub ``graphs(n)``: the edgeless graph, and the complete graph when n >= 2."""
    yield StubSageGraph(n, complete=False)
    if n >= 2:
        yield StubSageGraph(n, complete=True)


def sage_counterexample_output(conjecture: str, n_max: int = 5) -> str:
    """What counterexample_search.sage prints when run as Python with stub ``graphs()`` and ``version()``.

    No Sage starts; the template's own counting and its computation_evidence block run as written.
    """
    code = compile(SAGE_COUNTEREXAMPLE_TEMPLATE.read_text(encoding="utf-8"), str(SAGE_COUNTEREXAMPLE_TEMPLATE), "exec")
    namespace = {"__name__": "__main__", "graphs": stub_sage_graphs, "version": lambda: SAGE_STUB_VERSION}
    stdout = io.StringIO()
    environment = {"SAGE_PARAM_N_MAX": str(n_max), "SAGE_PARAM_CONJECTURE": conjecture}
    with mock.patch.dict(os.environ, environment):
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
            exec(code, namespace)
    return stdout.getvalue()


def load_runtime_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AddComputationEvidenceVerbTests(unittest.TestCase):
    """add-computation-evidence turns one helper result into one computation row bound to its bytes.

    The row copies the result's computation_evidence block, and the run keeps a
    copy of the file. validate re-hashes the copy and compares the row with the
    block. A ready claim counts the row only when its relation matches the
    result and the block came from a research_compute broker envelope.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = load_runtime_module("drw_add_computation_evidence", DEEP_RESEARCH_RUNTIME)
        cls.runner = load_runtime_module("research_compute_template_runner", RESEARCH_COMPUTE_RUNNER)

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def not_ready_run(self, name: str = "run", *, cites: str = "E-COMP-1") -> Path:
        """A valid not-ready v2 run with source S1 and a claim C1 that cites ``cites``."""
        root = self.tmp / name / "research"
        root.mkdir(parents=True)
        self.mod.write_minimal_v2_not_ready(root)
        self.mod.write_jsonl_rows(root / "sources.jsonl", [self.mod.paper_source()])
        self.mod.write_jsonl_rows(root / "claims.jsonl", [self.mod.claim_row(source_ids=[], evidence_ids=[cites])])
        return root

    def ready_run(self, name: str = "run", *, decision: str = "ready") -> Path:
        """A finalizable run whose claim C1 has no source_ids and rests on E-COMP-1 alone."""
        root = self.tmp / name / "research"
        root.mkdir(parents=True)
        self.mod.write_base_v2_run(root, decision=decision, claim_evidence_ids=["E-COMP-1"], source_ids=[])
        return root

    def result_file(self, name: str, text: str) -> Path:
        path = self.tmp / name
        path.write_bytes(text.encode("utf-8"))
        return path

    def graph_verifier_output(self, name: str, payload: dict | None) -> str:
        """What graph-verifier prints for ``payload``, or for no input when ``payload`` is None."""
        if importlib.util.find_spec("networkx") is None:
            self.skipTest("graph-verifier needs networkx")
        args = [sys.executable, str(GRAPH_VERIFIER_RUNTIME)]
        if payload is not None:
            path = self.result_file(f"{name}-input.json", json.dumps(payload))
            args += ["--input", str(path)]
        completed = subprocess.run(
            args, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False, timeout=120
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return completed.stdout

    def broker_envelope(self, result: dict) -> str:
        """The record a research_compute job leaves when its Python payload returns ``result``."""
        job = {"job_id": "job-test-1", "template": "python-v1", "payload": {"python_source": f"RESULT = {result!r}"}}
        envelope = self.runner.execute_manifest(job, resource_class="cpu-small")
        return json.dumps(envelope, indent=2, sort_keys=True) + "\n"

    def run_runtime(self, *args: str) -> subprocess.CompletedProcess:
        env = {**os.environ, "AAS_RUNTIME_WORKSPACE": str(RUNTIME_WORKSPACE)}
        return subprocess.run(
            [sys.executable, str(DEEP_RESEARCH_RUNTIME), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            check=False,
        )

    def run_verb(self, *args: str) -> tuple[int, dict]:
        completed = self.run_runtime(*args)
        try:
            return completed.returncode, json.loads(completed.stdout)
        except json.JSONDecodeError:
            self.fail(f"{args[0]} printed no JSON (exit {completed.returncode}): {completed.stderr.strip()[-400:]}")

    def add(self, root: Path, result: Path, *extra: str, claim_id: str = "C1", relation: str = "asserts") -> tuple[int, dict]:
        return self.run_verb(
            "add-computation-evidence",
            "--dir",
            str(root),
            "--result",
            str(result),
            "--claim-id",
            claim_id,
            "--relation",
            relation,
            *extra,
        )

    def delivery_check(self, root: Path) -> tuple[int, dict]:
        return self.run_verb("delivery-check", "--dir", str(root))

    def evidence_rows(self, root: Path) -> list[dict]:
        text = (root / "evidence.jsonl").read_text(encoding="utf-8")
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    def tree(self, root: Path) -> tuple[bytes, list[str]]:
        """evidence.jsonl's bytes and every path in the run: what a refusal leaves alone."""
        paths = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))
        return (root / "evidence.jsonl").read_bytes(), paths

    def validate_codes(self, root: Path) -> set[str]:
        return {error["code"] for error in self.mod.validate_directory(root)["errors"]}

    def hand_typed_row(self, **fields) -> dict:
        """An exhaustive, passed computation row typed by hand: no claim_relation, no payload_hash."""
        row = {
            "schema_version": "deep-research.evidence.v2",
            "evidence_id": "E-COMP-1",
            "evidence_type": "computation",
            "source_ids": [],
            "claim_ids": ["C1"],
            "artifact_ref": "checks/sample.json",
            "summary": "Hand-typed graph check.",
            "inspection_status": "checked",
            "redaction_status": "safe",
            "sensitivity_class": "public",
            "created_at": "2026-05-28T00:00:00Z",
            "limitations": [],
            "tool_name": "graph-verifier",
            "tool_version": "networkx test",
            "input_encoding_ref": "sha256:" + "0" * 64,
            "checked_domain": "the one graph in the input file (3 vertices, 3 edges)",
            "graph_model_assumptions": "simple undirected graph",
            "resource_bounds": "none",
            "enumeration_method": "none: each property is computed directly",
            "timeout_status": "completed",
            "result_status": "passed",
            "coverage_status": "exhaustive",
            "exhaustiveness_argument": "Every expected value was computed on the whole graph.",
        }
        row.update(fields)
        return row

    def test_a_sage_result_is_copied_hashed_and_validates(self) -> None:
        root = self.not_ready_run()
        result = self.result_file("sage.json", sage_counterexample_output(SAGE_RAISING_CONJECTURE))
        source = result.read_bytes()
        block = json.loads(source)["computation_evidence"]
        self.assertEqual((block["result_status"], block["coverage_status"]), ("partial", "partial"))
        digest = "sha256:" + hashlib.sha256(source).hexdigest()
        started = datetime.now(timezone.utc).replace(microsecond=0)

        code, out = self.add(root, result)

        self.assertEqual(code, 0, out)
        self.assertEqual(
            out,
            {
                "schema_version": "deep-research.add-computation-evidence.v1",
                "status": "ok",
                "codes": [],
                "errors": [],
                "evidence_id": "E-COMP-1",
                "artifact_ref": "artifacts/computation/E-COMP-1.json",
                "payload_hash": digest,
                "verification_source": "submitted_file",
                "claim_relation": "asserts",
            },
        )
        self.assertEqual((root / "artifacts" / "computation" / "E-COMP-1.json").read_bytes(), source)
        [row] = self.evidence_rows(root)
        self.assertEqual(
            {key: row[key] for key in COMPUTATION_BLOCK_FIELDS},
            {key: block[key] for key in COMPUTATION_BLOCK_FIELDS},
        )
        self.assertEqual(row["evaluated_predicate"], SAGE_RAISING_CONJECTURE)
        self.assertNotIn("exhaustiveness_argument", row)
        self.assertEqual(row["summary"], block["outcome"])
        self.assertEqual(row["limitations"], block["limitations"])
        self.assertEqual(
            {
                key: row[key]
                for key in (
                    "schema_version",
                    "evidence_id",
                    "evidence_type",
                    "source_ids",
                    "claim_ids",
                    "artifact_ref",
                    "payload_hash",
                    "verification_source",
                    "claim_relation",
                    "inspection_status",
                    "redaction_status",
                    "sensitivity_class",
                )
            },
            {
                "schema_version": "deep-research.evidence.v2",
                "evidence_id": "E-COMP-1",
                "evidence_type": "computation",
                "source_ids": [],
                "claim_ids": ["C1"],
                "artifact_ref": "artifacts/computation/E-COMP-1.json",
                "payload_hash": digest,
                "verification_source": "submitted_file",
                "claim_relation": "asserts",
                "inspection_status": "unchecked",
                "redaction_status": "not_reviewed",
                "sensitivity_class": "unknown",
            },
        )
        self.assertRegex(row["created_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        created = datetime.strptime(row["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        self.assertLessEqual(started, created)
        self.assertLessEqual(created, datetime.now(timezone.utc))
        validation = self.mod.validate_directory(root)
        self.assertEqual(validation["status"], "ok", validation["errors"])

        # A second result takes the next free id; the ledger keeps its bytes and gains one line.
        before = (root / "evidence.jsonl").read_bytes()
        code, out = self.add(root, result)
        self.assertEqual((code, out["evidence_id"]), (0, "E-COMP-2"), out)
        after = (root / "evidence.jsonl").read_bytes()
        self.assertTrue(after.startswith(before))
        self.assertEqual(len(after.splitlines()), len(before.splitlines()) + 1)
        self.assertEqual((root / "artifacts" / "computation" / "E-COMP-2.json").read_bytes(), source)
        validation = self.mod.validate_directory(root)
        self.assertEqual(validation["status"], "ok", validation["errors"])

    def test_validate_rehashes_and_compares_the_payload(self) -> None:
        """validate re-reads the copy behind a computation row that carries payload_hash.

        The AXLE subtest is a regression check that already held before the
        verb existed: a request hash on a row of another type is not re-hashed.
        """
        result = self.result_file("sage.json", sage_counterexample_output(SAGE_RAISING_CONJECTURE))

        def copy_of(root: Path) -> Path:
            return root / "artifacts" / "computation" / "E-COMP-1.json"

        def edit_copy(root: Path) -> None:
            copy_of(root).write_bytes(copy_of(root).read_bytes() + b" ")

        def delete_copy(root: Path) -> None:
            copy_of(root).unlink()

        def widen_row(root: Path) -> None:
            rows = self.evidence_rows(root)
            rows[0].update({"coverage_status": "bounded_complete", "exhaustiveness_argument": "Every graph was checked."})
            self.mod.write_jsonl_rows(root / "evidence.jsonl", rows)

        cases = {
            "an edited copy": (edit_copy, "COMPUTATION_PAYLOAD_HASH_MISMATCH"),
            "a deleted copy": (delete_copy, "COMPUTATION_PAYLOAD_HASH_MISMATCH"),
            "a row edited from partial to bounded_complete": (widen_row, "COMPUTATION_ROW_DIVERGES_FROM_PAYLOAD"),
        }
        for index, (label, (tamper, expected)) in enumerate(cases.items()):
            with self.subTest(label):
                root = self.not_ready_run(f"case-{index}")
                code, out = self.add(root, result)
                self.assertEqual(code, 0, out)
                self.assertEqual(self.validate_codes(root), set())
                tamper(root)
                codes = self.validate_codes(root)
                self.assertIn(expected, codes)
                self.assertNotIn("UNKNOWN_FIELD", codes)
                if expected == "COMPUTATION_ROW_DIVERGES_FROM_PAYLOAD":
                    self.assertNotIn("COMPUTATION_PAYLOAD_HASH_MISMATCH", codes)

        with self.subTest("an AXLE row keeps its request hash"):
            root = self.not_ready_run("axle", cites="E-AXLE-1")
            self.mod.write_jsonl_rows(root / "evidence.jsonl", [self.mod.axle_evidence()])
            self.assertEqual(self.mod.axle_evidence()["payload_hash"], "sha256:test")
            self.assertEqual(self.validate_codes(root), set())

    def test_a_result_without_exactly_one_block_is_refused(self) -> None:
        """Anything but exactly one readable computation_evidence block is refused, and nothing is written."""
        sage = json.loads(sage_counterexample_output(SAGE_CLEAN_CONJECTURE))
        directory = self.tmp / "a-directory"
        directory.mkdir()

        def no_verdict_envelope() -> Path:
            text = self.graph_verifier_output("no-input", None)
            self.assertEqual(json.loads(text)["verdict"], "no_verdict")
            return self.result_file("no-verdict.json", text)

        def symlink_to_a_result() -> Path:
            link = self.tmp / "link.json"
            try:
                os.symlink(self.result_file("target.json", json.dumps(sage)), link)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"symlinks unavailable: {exc}")
            return link

        def not_utf8() -> Path:
            path = self.tmp / "latin-1.json"
            path.write_bytes(b'{"note": "caf\xe9"}')
            return path

        cases = {
            "an execute_manifest envelope with no block": (
                lambda: self.result_file("no-block.json", self.broker_envelope({"answer": 42})),
                "NO_COMPUTATION_EVIDENCE_BLOCK",
            ),
            "two blocks": (
                lambda: self.result_file(
                    "two-blocks.json", json.dumps({"computation_evidence": sage["computation_evidence"], "result": sage})
                ),
                "NO_COMPUTATION_EVIDENCE_BLOCK",
            ),
            "a graph-verifier no_verdict envelope": (no_verdict_envelope, "NO_COMPUTATION_EVIDENCE_BLOCK"),
            "malformed JSON": (lambda: self.result_file("malformed.json", '{"computation_evidence": {'), "NO_COMPUTATION_EVIDENCE_BLOCK"),
            "a JSON list": (lambda: self.result_file("list.json", json.dumps([sage])), "NO_COMPUTATION_EVIDENCE_BLOCK"),
            "a missing --result": (lambda: self.tmp / "absent.json", "COMPUTATION_RESULT_UNREADABLE"),
            "a directory": (lambda: directory, "COMPUTATION_RESULT_UNREADABLE"),
            "bytes that are not UTF-8": (not_utf8, "COMPUTATION_RESULT_UNREADABLE"),
            "a symbolic link": (symlink_to_a_result, "COMPUTATION_RESULT_UNREADABLE"),
        }
        for index, (label, (make, expected)) in enumerate(cases.items()):
            with self.subTest(label):
                result = make()
                root = self.not_ready_run(f"case-{index}")
                before = self.tree(root)
                code, out = self.add(root, result)
                self.assertEqual((code, out["status"]), (1, "refused"), out)
                self.assertIn(expected, out["codes"])
                self.assertEqual(self.tree(root), before)

    def test_a_contradicting_relation_is_refused(self) -> None:
        """asserts with a failed result, or refutes with a passed one, is refused and writes nothing."""
        triangle = {"edges": [[0, 1], [1, 2], [2, 0]], "expected": {"n": 3, "connected": True}}
        cases = {
            "a Sage counterexample recorded as asserts": (
                lambda: self.result_file("refuted.json", sage_counterexample_output(SAGE_REFUTED_CONJECTURE)),
                "asserts",
                "failed",
            ),
            "a graph-verifier pass recorded as refutes": (
                lambda: self.result_file("passed.json", self.graph_verifier_output("triangle", triangle)),
                "refutes",
                "passed",
            ),
        }
        for index, (label, (make, relation, status)) in enumerate(cases.items()):
            with self.subTest(label):
                result = make()
                self.assertEqual(json.loads(result.read_bytes())["computation_evidence"]["result_status"], status)
                root = self.not_ready_run(f"case-{index}")
                before = self.tree(root)
                code, out = self.add(root, result, relation=relation)
                self.assertEqual((code, out["status"]), (1, "refused"), out)
                self.assertEqual(out["codes"], ["COMPUTATION_RELATION_CONTRADICTS_RESULT"])
                self.assertEqual(self.tree(root), before)

        with self.subTest("the same counterexample recorded as refutes is accepted"):
            root = self.not_ready_run("refutes")
            result = self.result_file("refuted.json", sage_counterexample_output(SAGE_REFUTED_CONJECTURE))
            code, out = self.add(root, result, relation="refutes")
            self.assertEqual((code, out["claim_relation"]), (0, "refutes"), out)

    def test_only_matching_broker_rows_support_a_ready_claim(self) -> None:
        """C1 rests on one computation row; only a matching row from a broker envelope makes it READY.

        The hand-typed row is the case that was READY before this change.
        """
        triangle = [[0, 1], [1, 2], [2, 0]]
        passed = self.graph_verifier_output("triangle", {"edges": triangle, "expected": {"n": 3, "connected": True}})
        dump = self.graph_verifier_output("dump", {"edges": triangle})
        self.assertEqual(json.loads(passed)["computation_evidence"]["coverage_status"], "exhaustive")
        self.assertEqual(json.loads(dump)["computation_evidence"]["coverage_status"], "partial")
        weak_results = {
            "a graph-verifier property dump": self.result_file("dump.json", dump),
            "a Sage result with evaluation errors": self.result_file(
                "errors.json", sage_counterexample_output(SAGE_RAISING_CONJECTURE)
            ),
            "an exhaustive passed block in a submitted file": self.result_file("submitted.json", passed),
        }
        for index, (label, result) in enumerate(weak_results.items()):
            with self.subTest(label):
                root = self.ready_run(f"weak-{index}")
                code, out = self.add(root, result)
                self.assertEqual(code, 0, out)
                code, check = self.delivery_check(root)
                self.assertEqual((code, check["verdict"]), (1, "NOT READY"), check)
                self.assertEqual(check["codes"], ["READY_CLAIM_RELIES_ONLY_ON_WEAK_COMPUTATION"])

        with self.subTest("a hand-typed exhaustive row with no relation"):
            root = self.ready_run("hand-typed")
            self.mod.write_jsonl_rows(root / "evidence.jsonl", [self.mod.report_evidence(), self.hand_typed_row()])
            code, check = self.delivery_check(root)
            self.assertEqual((code, check["verdict"]), (1, "NOT READY"), check)
            self.assertEqual(check["codes"], ["READY_CLAIM_RELIES_ONLY_ON_WEAK_COMPUTATION"])

        with self.subTest("control: the same block inside an execute_manifest envelope"):
            root = self.ready_run("broker")
            envelope = self.result_file("envelope.json", self.broker_envelope(json.loads(passed)))
            code, out = self.add(root, envelope)
            self.assertEqual((code, out["verification_source"]), (0, "research_compute_envelope"), out)
            code, check = self.delivery_check(root)
            self.assertEqual((code, check["verdict"], check["codes"]), (0, "READY", []), check)

    def test_a_bounded_claim_needs_a_caveat_naming_the_bound(self) -> None:
        root = self.ready_run(decision="ready-with-caveats")
        sage = json.loads(sage_counterexample_output(SAGE_CLEAN_CONJECTURE))
        block = sage["computation_evidence"]
        self.assertEqual((block["result_status"], block["coverage_status"]), ("passed", "bounded_complete"))
        code, out = self.add(root, self.result_file("envelope.json", self.broker_envelope(sage)))
        self.assertEqual((code, out["verification_source"]), (0, "research_compute_envelope"), out)

        caveats = {
            "no caveat": ([], False),
            "a caveat that names the claim but not the bound": (["C1 rests on a computation."], False),
            "a caveat that names the claim and the bound": ([f"C1 is checked only on {block['checked_domain']}."], True),
        }
        for label, (caveat_list, ready) in caveats.items():
            with self.subTest(label):
                delivery = json.loads((root / "delivery.json").read_text(encoding="utf-8"))
                delivery["caveats"] = caveat_list
                self.mod.write_json(root / "delivery.json", delivery)
                code, check = self.delivery_check(root)
                if ready:
                    self.assertEqual((code, check["verdict"], check["codes"]), (0, "READY", []), check)
                    self.assertEqual(self.validate_codes(root), set())
                else:
                    self.assertEqual((code, check["verdict"]), (1, "NOT READY"), check)
                    self.assertEqual(check["codes"], ["READY_BOUNDED_COMPUTATION_WITHOUT_CAVEAT"])

    def test_metadata_flags_and_relation_values(self) -> None:
        rows = {
            "claim_relation maybe": (
                self.hand_typed_row(claim_relation="maybe", evaluated_predicate="G is connected"),
                "COMPUTATION_CLAIM_RELATION_INVALID",
            ),
            "claim_relation as a list": (
                self.hand_typed_row(claim_relation=["asserts"], evaluated_predicate="G is connected"),
                "COMPUTATION_CLAIM_RELATION_INVALID",
            ),
            "claim_relation without evaluated_predicate": (
                self.hand_typed_row(claim_relation="asserts"),
                "COMPUTATION_EVIDENCE_FIELD_REQUIRED",
            ),
        }
        for index, (label, (row, expected)) in enumerate(rows.items()):
            with self.subTest(label):
                run = self.not_ready_run(f"row-{index}")
                self.mod.write_jsonl_rows(run / "evidence.jsonl", [row])
                self.assertEqual(self.validate_codes(run), {expected})

        root = self.not_ready_run(cites="E-SAGE-1")
        result = self.result_file("sage.json", sage_counterexample_output(SAGE_CLEAN_CONJECTURE))
        block = json.loads(result.read_bytes())["computation_evidence"]
        with self.subTest("the override flags are applied"):
            code, out = self.add(
                root,
                result,
                "--evidence-id",
                "E-SAGE-1",
                "--source-id",
                "S1",
                "--inspection-status",
                "checked",
                "--redaction-status",
                "safe",
                "--sensitivity-class",
                "public",
                "--created-at",
                "2026-11-09T12:00:00Z",
                "--limitation",
                "the graphs came from a stub generator",
            )
            self.assertEqual(code, 0, out)
            [row] = self.evidence_rows(root)
            self.assertEqual(
                {
                    key: row[key]
                    for key in (
                        "evidence_id",
                        "artifact_ref",
                        "source_ids",
                        "inspection_status",
                        "redaction_status",
                        "sensitivity_class",
                        "created_at",
                    )
                },
                {
                    "evidence_id": "E-SAGE-1",
                    "artifact_ref": "artifacts/computation/E-SAGE-1.json",
                    "source_ids": ["S1"],
                    "inspection_status": "checked",
                    "redaction_status": "safe",
                    "sensitivity_class": "public",
                    "created_at": "2026-11-09T12:00:00Z",
                },
            )
            self.assertEqual(row["limitations"], [*block["limitations"], "the graphs came from a stub generator"])
            self.assertEqual(row["exhaustiveness_argument"], block["exhaustiveness_argument"])
            self.assertEqual(self.validate_codes(root), set())

        refusals = {
            "a --created-at that is not a timestamp": (("--created-at", "yesterday"), "C1", "TIMESTAMP_INVALID"),
            "an unknown claim": ((), "C9", "UNKNOWN_CLAIM_ID"),
            "a claim id of the wrong form": ((), "claim-1", "CLAIM_ID_INVALID"),
            "an unknown source": (("--source-id", "S9"), "C1", "UNKNOWN_SOURCE_ID"),
            "an evidence id already in use": (("--evidence-id", "E-SAGE-1"), "C1", "DUPLICATE_ID"),
            "an evidence id of the wrong form": (("--evidence-id", "comp-1"), "C1", "EVIDENCE_ID_INVALID"),
        }
        for label, (extra, claim_id, expected) in refusals.items():
            with self.subTest(label):
                before = self.tree(root)
                code, out = self.add(root, result, *extra, claim_id=claim_id)
                self.assertEqual((code, out["status"]), (1, "refused"), out)
                self.assertEqual(out["codes"], [expected])
                self.assertEqual(self.tree(root), before)

        with self.subTest("a relation other than asserts or refutes is a usage error"):
            before = self.tree(root)
            completed = self.run_runtime(
                "add-computation-evidence",
                "--dir",
                str(root),
                "--result",
                str(result),
                "--claim-id",
                "C1",
                "--relation",
                "maybe",
            )
            self.assertEqual(completed.returncode, 2, completed.stderr)
            self.assertIn("invalid choice: 'maybe'", completed.stderr)
            self.assertEqual(self.tree(root), before)

    def call_verb_in_process(self, root: Path, result: Path) -> tuple[int, dict]:
        """Run the verb in this process, so a test can replace one of its steps."""
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = self.mod.main(
                ["add-computation-evidence", "--dir", str(root), "--result", str(result), "--claim-id", "C1", "--relation", "asserts"]
            )
        return code, json.loads(stdout.getvalue())

    def test_the_row_and_its_copy_are_written_together_or_not_at_all(self) -> None:
        """The ledger gains one whole line; a write that cannot finish leaves the run as it was."""
        result = self.result_file("sage.json", sage_counterexample_output(SAGE_RAISING_CONJECTURE))

        with self.subTest("a last ledger line without a newline"):
            root = self.not_ready_run("no-newline")
            before = json.dumps(self.hand_typed_row(evidence_id="E-HAND-1"), sort_keys=True).encode("utf-8")
            (root / "evidence.jsonl").write_bytes(before)
            code, out = self.add(root, result)
            self.assertEqual(code, 0, out)
            self.assertTrue((root / "evidence.jsonl").read_bytes().startswith(before + b"\n"))
            self.assertEqual([row["evidence_id"] for row in self.evidence_rows(root)], ["E-HAND-1", "E-COMP-1"])
            self.assertEqual(self.validate_codes(root), set())

        with self.subTest("a symbolic link in place of the artifacts directory"):
            root = self.not_ready_run("linked-artifacts")
            elsewhere = self.tmp / "elsewhere"
            elsewhere.mkdir()
            try:
                os.symlink(elsewhere, root / "artifacts", target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"symlinks unavailable: {exc}")
            before = self.tree(root)
            code, out = self.add(root, result)
            self.assertEqual((code, out["codes"]), (1, ["COMPUTATION_EVIDENCE_NOT_WRITABLE"]), out)
            self.assertEqual(self.tree(root), before)
            self.assertEqual(list(elsewhere.iterdir()), [])

        with self.subTest("a ledger write that fails"):
            root = self.not_ready_run("failed-write")
            before = self.tree(root)
            with mock.patch.object(self.mod, "append_line_atomically", side_effect=OSError("disk full")):
                code, out = self.call_verb_in_process(root, result)
            self.assertEqual((code, out["codes"]), (1, ["COMPUTATION_EVIDENCE_NOT_WRITABLE"]), out)
            self.assertEqual(self.tree(root), before)

        with self.subTest("a ledger that changes while the row is prepared"):
            root = self.not_ready_run("changed-ledger")
            concurrent = (json.dumps(self.hand_typed_row(evidence_id="E-HAND-2"), sort_keys=True) + "\n").encode("utf-8")
            original = self.mod.append_line_atomically

            def append_after_another_writer(path: Path, before: bytes, line: bytes) -> None:
                path.write_bytes(before + concurrent)
                original(path, before, line)

            with mock.patch.object(self.mod, "append_line_atomically", side_effect=append_after_another_writer):
                code, out = self.call_verb_in_process(root, result)
            self.assertEqual((code, out["codes"]), (1, ["COMPUTATION_EVIDENCE_NOT_WRITABLE"]), out)
            self.assertEqual((root / "evidence.jsonl").read_bytes(), concurrent)
            self.assertEqual(sorted(path.name for path in root.iterdir()), sorted(
                ["research_schema.json", "sources.jsonl", "claims.jsonl", "guards.jsonl", "evidence.jsonl", "delivery.json", "delegation"]
            ))

    def passed_triangle_check(self) -> dict:
        """graph-verifier's result for a triangle with two expected values: passed and exhaustive."""
        triangle = {"edges": [[0, 1], [1, 2], [2, 0]], "expected": {"n": 3, "connected": True}}
        return json.loads(self.graph_verifier_output("triangle", triangle))

    def ready_broker_run(self, name: str, result: dict) -> Path:
        """A ready run whose C1 rests only on ``result``, recorded through the verb from a broker envelope."""
        root = self.ready_run(name)
        envelope = self.result_file(f"{name}-envelope.json", self.broker_envelope(result))
        code, out = self.add(root, envelope)
        self.assertEqual((code, out["verification_source"]), (0, "research_compute_envelope"), out)
        return root

    def test_delivery_check_hashes_the_copy_behind_each_payload_hash(self) -> None:
        """validate re-reads the copy behind a payload_hash row, so delivery-check hashes it before and after validate.

        A copy changed after validate returns makes the run NOT READY, and
        input_files lists the copy. A copy that cannot be hashed, here a
        symbolic link to itself, gives DELIVERY_INPUT_UNREADABLE.
        """
        payload = "artifacts/computation/E-COMP-1.json"
        check = self.passed_triangle_check()
        original = self.mod.validate_directory

        def validate_then_append(root: Path, *args, **kwargs) -> dict:
            result = original(root, *args, **kwargs)
            with (root / payload).open("ab") as handle:
                handle.write(b"\n")
            return result

        with self.subTest("an unchanged copy"):
            result = self.mod.delivery_check(self.ready_broker_run("unchanged", check))
            self.assertEqual((result["verdict"], result["codes"]), ("READY", []), result)
            self.assertIn(payload, [name for name, _ in result["input_files"]])

        with self.subTest("a copy changed after validate returns"):
            root = self.ready_broker_run("changed", check)
            with mock.patch.object(self.mod, "validate_directory", side_effect=validate_then_append):
                result = self.mod.delivery_check(root)
            self.assertEqual((result["verdict"], result["codes"]), ("NOT READY", ["DELIVERY_INPUT_CHANGED"]), result)
            self.assertIn(payload, [name for name, _ in result["input_files"]])

        with self.subTest("a copy replaced by a symbolic link to itself"):
            root = self.ready_broker_run("looped", check)
            (root / payload).unlink()
            try:
                os.symlink("E-COMP-1.json", root / payload)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"symlinks unavailable: {exc}")
            result = self.mod.delivery_check(root)
            self.assertEqual(result["verdict"], "NOT READY", result)
            self.assertIn("DELIVERY_INPUT_UNREADABLE", result["codes"])

    def test_an_evidence_line_nested_too_deeply_gives_a_report_not_a_traceback(self) -> None:
        """delivery-check reads evidence.jsonl for the payload copies before validate runs.

        A ledger it cannot parse adds no copy, and validate's crash on it is
        reported as DELIVERY_VALIDATE_CRASHED, as on the slice head.
        """
        root = self.ready_run()
        with (root / "evidence.jsonl").open("a", encoding="utf-8") as handle:
            handle.write('{"nested": ' + "[" * 100000 + "]" * 100000 + "}\n")
        result = self.mod.delivery_check(root)
        self.assertEqual((result["verdict"], result["codes"]), ("NOT READY", ["DELIVERY_VALIDATE_CRASHED"]), result)

    def test_an_evidence_integer_over_the_digit_limit_gives_a_report_not_a_traceback(self) -> None:
        """json raises a plain ValueError for an integer longer than Python's digit limit.

        The payload scan treats that ledger like any other it cannot parse: it
        adds no copy, and validate's crash on it is reported as
        DELIVERY_VALIDATE_CRASHED, as on the slice head.
        """
        limit = getattr(sys, "get_int_max_str_digits", lambda: 0)()
        if not limit:
            self.skipTest("this Python has no limit on integer digits")
        root = self.ready_run()
        with (root / "evidence.jsonl").open("a", encoding="utf-8") as handle:
            handle.write('{"big": ' + "1" * (limit + 1) + "}\n")
        result = self.mod.delivery_check(root)
        self.assertEqual((result["verdict"], result["codes"]), ("NOT READY", ["DELIVERY_VALIDATE_CRASHED"]), result)

    def test_a_ledger_with_an_unparseable_line_adds_no_payload_copy(self) -> None:
        """A malformed evidence.jsonl adds no copy to the input files, not even for its valid rows.

        Its own digest still covers it, and validate reports the bad line.
        """
        payload = "artifacts/computation/E-COMP-1.json"
        root = self.ready_broker_run("partly-malformed", self.passed_triangle_check())
        self.assertIn(payload, [name for name, _ in self.mod.delivery_input_digests(root)])
        with (root / "evidence.jsonl").open("a", encoding="utf-8") as handle:
            handle.write("{not json\n")
        names = [name for name, _ in self.mod.delivery_input_digests(root)]
        self.assertNotIn(payload, names)
        self.assertIn("evidence.jsonl", names)
        result = self.mod.delivery_check(root)
        self.assertEqual(result["verdict"], "NOT READY", result)
        self.assertIn("JSONL_INVALID", result["codes"])

    def test_a_status_that_is_not_a_string_gets_codes_not_a_traceback(self) -> None:
        """A list or object in result_status or coverage_status gets its code; nothing raises.

        Before this fix the verb ended with a TypeError and printed no JSON,
        and validate raised TypeError on such a row. The validate crash
        already existed at 60ca4a4, before the verb.
        """
        sage = json.loads(sage_counterexample_output(SAGE_CLEAN_CONJECTURE))
        cases = {
            "result_status as a list": ("result_status", ["passed"], "COMPUTATION_RESULT_STATUS_INVALID"),
            "coverage_status as an object": ("coverage_status", {}, "COMPUTATION_COVERAGE_STATUS_INVALID"),
        }
        for index, (label, (field, value, expected)) in enumerate(cases.items()):
            with self.subTest(label):
                block = {**sage["computation_evidence"], field: value}
                result = self.result_file(f"status-{index}.json", json.dumps({**sage, "computation_evidence": block}))
                root = self.not_ready_run(f"case-{index}")
                before = self.tree(root)
                code, out = self.add(root, result)
                self.assertEqual((code, out["status"]), (1, "refused"), out)
                self.assertEqual(out["codes"], sorted(["COMPUTATION_EVIDENCE_FIELD_REQUIRED", expected]))
                self.assertEqual(self.tree(root), before)

        with self.subTest("validate on a row typed by hand"):
            root = self.not_ready_run("validate")
            self.mod.write_jsonl_rows(
                root / "evidence.jsonl", [self.hand_typed_row(result_status=["passed"], coverage_status={})]
            )
            self.assertEqual(
                self.validate_codes(root),
                {"COMPUTATION_EVIDENCE_FIELD_REQUIRED", "COMPUTATION_RESULT_STATUS_INVALID", "COMPUTATION_COVERAGE_STATUS_INVALID"},
            )

    def test_a_broker_row_counts_only_when_its_result_supports_its_relation(self) -> None:
        """Regression check: a partial result recorded as asserts stays weak, though exhaustive and from a broker envelope.

        The verb accepts it, since only a failed result contradicts asserts, so
        the delivery rule's relation match alone keeps C1 from READY.
        """
        check = self.passed_triangle_check()
        block = check["computation_evidence"]
        block["result_status"] = "partial"
        self.assertEqual(block["coverage_status"], "exhaustive")
        self.assertTrue(block["exhaustiveness_argument"])
        code, result = self.delivery_check(self.ready_broker_run("partial", check))
        self.assertEqual((code, result["verdict"]), (1, "NOT READY"), result)
        self.assertEqual(result["codes"], ["READY_CLAIM_RELIES_ONLY_ON_WEAK_COMPUTATION"])

    def test_a_row_without_payload_hash_never_counts(self) -> None:
        """Regression check: a hand-typed row naming a broker envelope and a matching relation stays weak."""
        root = self.ready_run()
        row = self.hand_typed_row(
            verification_source="research_compute_envelope", claim_relation="asserts", evaluated_predicate="G is connected"
        )
        self.mod.write_jsonl_rows(root / "evidence.jsonl", [self.mod.report_evidence(), row])
        code, result = self.delivery_check(root)
        self.assertEqual((code, result["verdict"]), (1, "NOT READY"), result)
        self.assertEqual(result["codes"], ["READY_CLAIM_RELIES_ONLY_ON_WEAK_COMPUTATION"])

    def test_validate_compares_provenance_summary_and_limitations_with_the_copy(self) -> None:
        """Regression check: each edit leaves a row that would count, and only the comparison with the copy refuses it."""
        check = self.passed_triangle_check()
        limitation = check["computation_evidence"]["limitations"][0]
        submitted = self.result_file("submitted.json", json.dumps(check))
        envelope = self.result_file("envelope.json", self.broker_envelope(check))

        def relabel(row: dict) -> None:
            row["verification_source"] = "research_compute_envelope"

        def edit_summary(row: dict) -> None:
            row["summary"] = "Checked by hand."

        def drop_limitation(row: dict) -> None:
            row["limitations"].remove(limitation)

        cases = {
            "a submitted_file row relabeled research_compute_envelope": (submitted, relabel),
            "an edited summary": (envelope, edit_summary),
            "a block limitation removed from the row": (envelope, drop_limitation),
        }
        for index, (label, (result_path, edit)) in enumerate(cases.items()):
            with self.subTest(label):
                root = self.ready_run(f"case-{index}")
                code, out = self.add(root, result_path)
                self.assertEqual(code, 0, out)
                rows = self.evidence_rows(root)
                edit(rows[-1])
                self.mod.write_jsonl_rows(root / "evidence.jsonl", rows)
                code, result = self.delivery_check(root)
                self.assertEqual((code, result["verdict"]), (1, "NOT READY"), result)
                self.assertEqual(result["codes"], ["COMPUTATION_ROW_DIVERGES_FROM_PAYLOAD"])

    def test_only_a_completed_job_record_with_the_block_under_result_is_an_envelope(self) -> None:
        """Regression check: each record below has every key of a research_compute job record, yet reads as submitted_file."""
        envelope = json.loads(self.broker_envelope(self.passed_triangle_check()))
        top_level = {**envelope, "result": dict(envelope["result"])}
        top_level["computation_evidence"] = top_level["result"].pop("computation_evidence")
        records = {
            "a job record whose status is not completed": {**envelope, "status": "failed"},
            "a job record whose block sits at the top level": top_level,
        }
        for index, (label, record) in enumerate(records.items()):
            with self.subTest(label):
                root = self.ready_run(f"case-{index}")
                code, out = self.add(root, self.result_file(f"record-{index}.json", json.dumps(record)))
                self.assertEqual((code, out["verification_source"]), (0, "submitted_file"), out)
                code, result = self.delivery_check(root)
                self.assertEqual((code, result["codes"]), (1, ["READY_CLAIM_RELIES_ONLY_ON_WEAK_COMPUTATION"]), result)


    def test_no_agent_cap_the_verb_and_validate_read_the_result_whole(self) -> None:
        """Both reads of a computation result pass no size limit (user decision, 2026-11-13)."""
        result = self.result_file("sage.json", sage_counterexample_output(SAGE_RAISING_CONJECTURE))
        seen: list[object] = []
        real = self.mod.read_regular_file

        def spy(path: Path, limit: int | None = None) -> bytes:
            seen.append(limit)
            return real(path, limit)

        root = self.not_ready_run("no-cap")
        with mock.patch.object(self.mod, "read_regular_file", spy):
            code, out = self.call_verb_in_process(root, result)
            self.assertEqual(code, 0, out)
            self.assertEqual(self.validate_codes(root), set())
        self.assertGreaterEqual(len(seen), 2)
        self.assertEqual(set(seen), {None})
        self.assertFalse(hasattr(self.mod, "COMPUTATION_RESULT_MAX_BYTES"))

    def test_no_agent_cap_a_stamp_copy_is_read_whole(self) -> None:
        """A stamp copy past the old 64 KiB is read, not refused (user decision, 2026-11-13)."""
        seen: list[object] = []
        real = self.mod.read_regular_file

        def spy(path: Path, limit: int | None = None) -> bytes:
            seen.append(limit)
            return real(path, limit)

        with tempfile.TemporaryDirectory() as tmp:
            stamp = Path(tmp) / "stamp.json"
            stamp.write_text(json.dumps({"padding": "x" * 70_000}), encoding="utf-8")
            row = {
                "artifact_ref": "stamp.json",
                "payload_hash": "sha256:" + hashlib.sha256(stamp.read_bytes()).hexdigest(),
                "reviewer_family": "anthropic",
            }
            errors: list[dict] = []
            with mock.patch.object(self.mod, "read_regular_file", spy):
                self.mod.bound_stamp_family(
                    row, "artifact_ref", "payload_hash", "reviewer_family", stamp,
                    Path(tmp) / "evidence.jsonl", errors, 1,
                )
        self.assertEqual(seen, [None])
        self.assertFalse(any("cannot be read" in error["message"] for error in errors), errors)
        self.assertFalse(hasattr(self.mod, "STAMP_COPY_MAX_BYTES"))


DISPATCH_STAMP_SCHEMA_VERSION = "external-agent-dispatch-stamp.v1"
# A stand-in claude CLI for delegate-agent: it reads the prompt and answers each
# call with one result envelope built from the nonce the dispatcher exports.
FAKE_PROVIDER_CLI_PY = """\
import json
import os
import sys

sys.stdin.read()
nonce = os.environ["AAS_DELEGATION_RESULT_NONCE"]
print("AAS_RESULT_JSON_START_" + nonce)
print(json.dumps({
    "status": "ok",
    "findings": [{"id": "F1", "summary": "re-derived", "evidence_refs": ["task"]}],
    "limitations": [],
    "warnings": [],
}))
print("AAS_RESULT_JSON_END_" + nonce)
print(os.environ["AAS_DELEGATION_FINAL_MARKER"])
"""


class AgdVerdictEvidenceTests(unittest.TestCase):
    """agd_result rows: verdict kinds, stamp copies bound on both sides, and the referee-only delivery rule.

    A side is bound when its ref and payload hash name a copy of a
    delegate-agent stamp whose model_family is that side's family; the reviewer
    side also needs family_source launch_stamp. independent_rederivation needs
    both sides bound to two different verified families. A supported v2 claim
    that rests only on panel judgement is not ready.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = load_runtime_module("drw_agd_verdicts", DEEP_RESEARCH_RUNTIME)

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def ready_run(self, name: str, *, cites: tuple[str, ...] = ("E-AGD-1",), source_ids: tuple[str, ...] = ()) -> Path:
        """A ready v2 run whose claim C1 cites ``cites`` and ``source_ids``."""
        root = self.tmp / name / "research"
        root.mkdir(parents=True)
        self.mod.write_base_v2_run(root, decision="ready", claim_evidence_ids=list(cites), source_ids=list(source_ids))
        return root

    def not_ready_run(self, name: str) -> Path:
        """A not-ready v2 run whose claim C1 cites E-AGD-1, so only the row checks apply."""
        root = self.tmp / name / "research"
        root.mkdir(parents=True)
        self.mod.write_minimal_v2_not_ready(root)
        self.mod.write_jsonl_rows(root / "sources.jsonl", [self.mod.paper_source()])
        self.mod.write_jsonl_rows(root / "claims.jsonl", [self.mod.claim_row(source_ids=[], evidence_ids=["E-AGD-1"])])
        return root

    def copy_file(self, root: Path, ref: str, data: bytes) -> str:
        """Write ``data`` at ``ref`` in the run; return the payload hash of those bytes."""
        path = root / ref
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return "sha256:" + hashlib.sha256(data).hexdigest()

    def stamp_copy(self, root: Path, participant_id: str, provider: str, family: str) -> tuple[str, str]:
        """Copy a stamp, written as delegate-agent writes one, into the run; return (ref, payload hash)."""
        stamp = {
            "schema_version": DISPATCH_STAMP_SCHEMA_VERSION,
            "run_id": "20261110-000000-0123abcd",
            "participant_id": participant_id,
            "provider": provider,
            "model_family": family,
            "unverified_by": [],
            "stamped_at": "2026-11-10T00:00:00Z",
        }
        ref = f"artifacts/stamps/20261110-000000-0123abcd/{participant_id}.json"
        data = (json.dumps(stamp, indent=2, sort_keys=True) + "\n").encode("utf-8")
        return ref, self.copy_file(root, ref, data)

    def agd_row(self, **fields) -> dict:
        row = self.mod.agd_evidence("E-AGD-1", "parent_validated")
        row["claim_ids"] = ["C1"]
        row.update(fields)
        return row

    def bound_row(
        self,
        root: Path,
        reviewer: tuple[str, str, str] = ("claude-external-1", "claude", "anthropic"),
        producer: tuple[str, str, str] = ("codex-1", "codex", "openai"),
        **fields,
    ) -> dict:
        """An independent_rederivation row whose sides are bound to stamp copies of (participant, provider, family)."""
        artifact_ref, payload_hash = self.stamp_copy(root, *reviewer)
        producer_ref, producer_hash = self.stamp_copy(root, *producer)
        row = self.agd_row(
            verdict_kind="independent_rederivation",
            family_source="launch_stamp",
            reviewer_family=reviewer[2],
            artifact_ref=artifact_ref,
            payload_hash=payload_hash,
            producer_family=producer[2],
            producer_artifact_ref=producer_ref,
            producer_payload_hash=producer_hash,
        )
        row.update(fields)
        return row

    def weak_computation_row(self) -> dict:
        """A sampled computation row typed by hand: it never counts for delivery."""
        return {
            "schema_version": "deep-research.evidence.v2",
            "evidence_id": "E-COMP-1",
            "evidence_type": "computation",
            "source_ids": [],
            "claim_ids": ["C1"],
            "artifact_ref": "checks/sample.json",
            "summary": "Sampled graph check.",
            "inspection_status": "checked",
            "redaction_status": "safe",
            "sensitivity_class": "public",
            "created_at": "2026-05-28T00:00:00Z",
            "limitations": ["sampled only"],
            "tool_name": "graph-verifier",
            "tool_version": "test",
            "input_encoding_ref": "checks/input.json",
            "checked_domain": "graphs up to n=5 sample",
            "graph_model_assumptions": "simple finite graph",
            "resource_bounds": "1s",
            "result_status": "partial",
            "coverage_status": "sampled",
            "enumeration_method": "sample",
            "timeout_status": "completed",
        }

    def write_evidence(self, root: Path, *rows: dict) -> None:
        self.mod.write_jsonl_rows(root / "evidence.jsonl", [self.mod.report_evidence(), *rows])

    def codes(self, root: Path) -> set[str]:
        return {error["code"] for error in self.mod.validate_directory(root)["errors"]}

    def agd_codes(self, root: Path) -> tuple[set[str], set[str]]:
        """The AGD_* codes of validate, and all of its codes."""
        codes = self.codes(root)
        return {code for code in codes if code.startswith("AGD_")}, codes

    def test_verified_families_match_the_dispatcher(self) -> None:
        """validate counts the families the dispatcher's provider table verifies, which equals panel_parent's."""
        from installer.ai_agents_skills.delegation import VERIFIED_FAMILIES

        self.assertEqual(set(self.mod.VERIFIED_FAMILIES), set(VERIFIED_FAMILIES))

    def test_referee_only_support_is_not_ready(self) -> None:
        """A ready claim resting only on panel judgement fails validate and delivery-check.

        Judgement is any agd_result row but independent_rederivation, a row
        written before verdict_kind existed included, alone or beside
        uncounted computation rows. The last case is a regression check: a
        source beside the row carries the claim, as before.
        """
        cases = {
            "referee_accepted": ([self.agd_row(verdict_kind="referee_accepted")], ("E-AGD-1",)),
            "consensus": ([self.agd_row(verdict_kind="consensus")], ("E-AGD-1",)),
            "a row without verdict_kind": ([self.agd_row()], ("E-AGD-1",)),
            "referee_accepted beside a weak computation row": (
                [self.agd_row(verdict_kind="referee_accepted"), self.weak_computation_row()],
                ("E-AGD-1", "E-COMP-1"),
            ),
        }
        for index, (name, (rows, cites)) in enumerate(cases.items()):
            with self.subTest(name):
                root = self.ready_run(f"referee-{index}", cites=cites)
                self.write_evidence(root, *rows)
                codes = self.codes(root)
                self.assertIn("READY_CLAIM_RELIES_ONLY_ON_REFEREE_JUDGEMENT", codes)
                self.assertNotIn("READY_CLAIM_RELIES_ONLY_ON_WEAK_COMPUTATION", codes)
                self.assertNotIn("UNKNOWN_FIELD", codes)
                result = self.mod.delivery_check(root)
                self.assertEqual(result["verdict"], "NOT READY", result)
                self.assertIn("READY_CLAIM_RELIES_ONLY_ON_REFEREE_JUDGEMENT", result["codes"])

        with self.subTest("regression check: a source beside the row carries the claim"):
            root = self.ready_run("referee-with-source", source_ids=("S1",))
            self.write_evidence(root, self.agd_row())
            result = self.mod.delivery_check(root)
            self.assertEqual((result["verdict"], result["codes"]), ("READY", []), result)

    def test_only_positive_support_beside_a_referee_row_carries_a_ready_claim(self) -> None:
        """Beside a referee verdict, a ready claim without source_ids needs a row of positive support.

        Positive support is a formal_check, counted computation or source_note
        row, a bound independent_rederivation, or a row of another type whose
        source_ids lists a source. A report, other, guard or formal_statement
        row without sources no longer clears the rule. The other cases are
        regression checks that held before: each kind of positive support keeps
        the claim READY; a referee row or an uncounted computation row stays
        judgement whatever sources it lists; a claim citing no agd_result row
        is outside the rule.
        """
        referee = self.agd_row(verdict_kind="referee_accepted")

        def plain_row(evidence_type: str, source_ids: list[str]) -> dict:
            """E-PLAIN-1, a row of ``evidence_type`` for C1 with only the common fields."""
            row = self.mod.report_evidence()
            row.update(
                evidence_id="E-PLAIN-1", evidence_type=evidence_type, source_ids=source_ids, artifact_ref="checks/sample.json"
            )
            return row

        def evidence(*rows: dict):
            return lambda root: self.write_evidence(root, *rows)

        def referee_and_counted_computation(root: Path) -> None:
            """The verdict, then E-COMP-1: a Sage result for C1 recorded from a research_compute envelope, which counts."""
            self.write_evidence(root, referee)
            runner = load_runtime_module("research_compute_runner_referee", RESEARCH_COMPUTE_RUNNER)
            sage = json.loads(sage_counterexample_output(SAGE_CLEAN_CONJECTURE))
            job = {"job_id": "job-referee-1", "template": "python-v1", "payload": {"python_source": f"RESULT = {sage!r}"}}
            envelope = root.parent / "envelope.json"
            envelope.write_text(json.dumps(runner.execute_manifest(job, resource_class="cpu-small")) + "\n", encoding="utf-8")
            report = self.mod.add_computation_evidence(
                SimpleNamespace(
                    dir=str(root),
                    result=str(envelope),
                    claim_id="C1",
                    relation="asserts",
                    source_id=[],
                    evidence_id=None,
                    inspection_status="checked",
                    redaction_status="safe",
                    sensitivity_class="public",
                    created_at=None,
                    limitation=[],
                )
            )
            self.assertEqual((report["status"], report["evidence_id"]), ("ok", "E-COMP-1"), report)
            row = json.loads((root / "evidence.jsonl").read_text(encoding="utf-8").splitlines()[-1])
            self.assertTrue(self.mod.is_counted_computation_evidence(row), row)

        referee_only = ("NOT READY", ["READY_CLAIM_RELIES_ONLY_ON_REFEREE_JUDGEMENT"])
        for evidence_type in ("report", "other", "guard", "formal_statement"):
            with self.subTest(f"{evidence_type} row without sources beside the verdict"):
                root = self.ready_run(f"plain-{evidence_type}", cites=("E-AGD-1", "E-PLAIN-1"))
                self.write_evidence(root, referee, plain_row(evidence_type, []))
                self.assertIn("READY_CLAIM_RELIES_ONLY_ON_REFEREE_JUDGEMENT", self.codes(root))
                result = self.mod.delivery_check(root)
                self.assertEqual((result["verdict"], result["codes"]), referee_only, result)

        formal_check = self.mod.formal_check_evidence()
        formal_check.update(source_ids=[], claim_ids=["C1"])
        weak_with_source = self.weak_computation_row()
        weak_with_source["source_ids"] = ["S1"]
        cases = {
            "the stock report row, which cites S1": (("E-AGD-1", "E-REPORT"), evidence(referee), ("READY", [])),
            "a source_note row without source_ids": (
                ("E-AGD-1", "E-PLAIN-1"),
                evidence(referee, plain_row("source_note", [])),
                ("READY", []),
            ),
            "an other row that cites S1": (("E-AGD-1", "E-PLAIN-1"), evidence(referee, plain_row("other", ["S1"])), ("READY", [])),
            "a formal_check row without source_ids": (("E-AGD-1", "E1"), evidence(referee, formal_check), ("READY", [])),
            "a counted computation row": (("E-AGD-1", "E-COMP-1"), referee_and_counted_computation, ("READY", [])),
            "a bound independent_rederivation": (
                ("E-AGD-1", "E-AGD-2"),
                lambda root: self.write_evidence(root, referee, self.bound_row(root, evidence_id="E-AGD-2")),
                ("READY", []),
            ),
            "a referee row that cites S1, alone": (
                ("E-AGD-1",),
                evidence(self.agd_row(verdict_kind="referee_accepted", source_ids=["S1"])),
                referee_only,
            ),
            "an uncounted computation row that cites S1": (("E-AGD-1", "E-COMP-1"), evidence(referee, weak_with_source), referee_only),
            "a claim citing no agd_result row": (("E-PLAIN-1",), evidence(plain_row("report", [])), ("READY", [])),
        }
        for index, (name, (cites, build, expected)) in enumerate(cases.items()):
            with self.subTest(f"regression check: {name}"):
                root = self.ready_run(f"positive-{index}", cites=cites)
                build(root)
                result = self.mod.delivery_check(root)
                self.assertEqual((result["verdict"], result["codes"]), expected, result)

    def test_bound_different_verified_families_pass(self) -> None:
        """Two bound stamp copies of different verified families let independent_rederivation carry a ready claim."""
        with self.subTest("an anthropic reviewer and an openai producer"):
            root = self.ready_run("bound")
            self.write_evidence(root, self.bound_row(root))
            validation = self.mod.validate_directory(root)
            self.assertEqual(validation["status"], "ok", validation["errors"])
            result = self.mod.delivery_check(root)
            self.assertEqual((result["verdict"], result["codes"]), ("READY", []), result)

        with self.subTest("a declared referee verdict naming both families validates"):
            root = self.not_ready_run("declared")
            self.write_evidence(
                root,
                self.agd_row(
                    verdict_kind="referee_rejected",
                    family_source="declared",
                    reviewer_family="anthropic",
                    producer_family="unverified",
                ),
            )
            self.assertEqual(self.codes(root), set())

    def test_independence_and_binding_failures(self) -> None:
        """Each unverified family or broken binding gives its own code, never UNKNOWN_FIELD.

        Each case's AGD codes are compared whole, so no case passes on another failure.
        """

        def bound(**fields):
            return lambda root: self.bound_row(root, **fields)

        def without(*names: str):
            def build(root: Path) -> dict:
                row = self.bound_row(root)
                for name in names:
                    del row[name]
                return row

            return build

        def declared_producer(root: Path) -> dict:
            row = without("producer_artifact_ref", "producer_payload_hash")(root)
            row["producer_family"] = "deepseek"
            return row

        def removed_copy(root: Path) -> dict:
            row = self.bound_row(root)
            (root / row["producer_artifact_ref"]).unlink()
            return row

        def edited_copy(root: Path) -> dict:
            row = self.bound_row(root)
            with (root / row["artifact_ref"]).open("ab") as handle:
                handle.write(b" ")
            return row

        def replaced_copy(data: bytes):
            def build(root: Path) -> dict:
                row = self.bound_row(root)
                row["payload_hash"] = self.copy_file(root, row["artifact_ref"], data)
                return row

            return build

        def oversized_copy(root: Path) -> dict:
            row = self.bound_row(root)
            data = (root / row["artifact_ref"]).read_bytes() + b" " * (64 * 1024)
            row["payload_hash"] = self.copy_file(root, row["artifact_ref"], data)
            return row

        unverified = {"AGD_INDEPENDENCE_UNVERIFIED_FAMILY"}
        anthropic_producer = ("claude-external-2", "claude", "anthropic")
        cases = [
            ("a bound kimi stamp", bound(reviewer=("kimi-external-1", "kimi", "unverified"), producer=anthropic_producer), unverified),
            ("a bound copilot stamp", bound(reviewer=("copilot-external-1", "copilot", "unverified"), producer=anthropic_producer), unverified),
            ("producer_family changed under a genuine reviewer stamp", declared_producer, unverified),
            ("producer_family changed against its bound stamp", bound(producer_family="deepseek"), {"AGD_STAMP_FAMILY_MISMATCH", *unverified}),
            (
                "both families declared",
                lambda root: self.agd_row(
                    verdict_kind="independent_rederivation",
                    family_source="declared",
                    reviewer_family="anthropic",
                    producer_family="openai",
                ),
                unverified,
            ),
            ("a stamped reviewer whose family_source is declared", bound(family_source="declared"), unverified),
            ("equal verified families", bound(producer=anthropic_producer), unverified),
            ("a missing stamp copy", removed_copy, {"AGD_STAMP_HASH_MISMATCH", *unverified}),
            ("an edited stamp copy", edited_copy, {"AGD_STAMP_HASH_MISMATCH", *unverified}),
            ("half a producer pair", without("producer_payload_hash"), {"AGD_STAMP_HASH_MISMATCH", *unverified}),
            ("launch_stamp without payload_hash", without("payload_hash"), {"AGD_STAMP_HASH_MISMATCH", *unverified}),
            ("a copy past the old 64 KiB cap", oversized_copy, set()),
            ("a copy that is not a stamp", replaced_copy(b'{"status": "ok"}\n'), {"AGD_STAMP_INVALID", *unverified}),
            (
                "a stamp with a repeated key",
                replaced_copy(
                    b'{"schema_version": "external-agent-dispatch-stamp.v1", '
                    b'"model_family": "xai", "model_family": "anthropic"}\n'
                ),
                {"AGD_STAMP_INVALID", *unverified},
            ),
            (
                "a stamp whose model_family is not a string",
                replaced_copy(b'{"schema_version": "external-agent-dispatch-stamp.v1", "model_family": ["anthropic"]}\n'),
                {"AGD_STAMP_INVALID", *unverified},
            ),
            ("a reviewer_family that differs from its stamp", bound(reviewer_family="xai"), {"AGD_STAMP_FAMILY_MISMATCH", *unverified}),
            (
                "host_verified without a host record",
                lambda root: self.agd_row(verdict_kind="referee_accepted", family_source="host_verified", reviewer_family="anthropic"),
                {"AGD_HOST_VERIFIED_UNAVAILABLE"},
            ),
            ("an unknown family_source", lambda root: self.agd_row(family_source="self_report"), {"AGD_FAMILY_SOURCE_INVALID"}),
            (
                "a family_source that is not a string",
                lambda root: self.agd_row(family_source={"source": "launch_stamp"}),
                {"AGD_FAMILY_SOURCE_INVALID"},
            ),
            ("an unknown verdict_kind", lambda root: self.agd_row(verdict_kind="lgtm"), {"AGD_VERDICT_KIND_INVALID"}),
            (
                "a verdict_kind that is not a string",
                lambda root: self.agd_row(verdict_kind=["independent_rederivation"]),
                {"AGD_VERDICT_KIND_INVALID"},
            ),
        ]
        for index, (name, build, expected) in enumerate(cases):
            with self.subTest(name):
                root = self.not_ready_run(f"binding-{index}")
                self.write_evidence(root, build(root))
                agd_codes, codes = self.agd_codes(root)
                self.assertEqual(agd_codes, expected, codes)
                self.assertNotIn("UNKNOWN_FIELD", codes)

        with self.subTest("a symbolic link in place of the copy"):
            root = self.not_ready_run("binding-symlink")
            row = self.bound_row(root)
            copy = root / row["artifact_ref"]
            moved = copy.with_name("moved.json")
            copy.rename(moved)
            try:
                os.symlink(moved.name, copy)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"symlinks unavailable: {exc}")
            self.write_evidence(root, row)
            agd_codes, codes = self.agd_codes(root)
            self.assertEqual(agd_codes, {"AGD_STAMP_HASH_MISMATCH", *unverified}, codes)

        with self.subTest("a producer copy that is a symbolic link to itself"):
            root = self.not_ready_run("binding-loop")
            row = self.bound_row(root)
            copy = root / row["producer_artifact_ref"]
            copy.unlink()
            try:
                os.symlink(copy.name, copy)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"symlinks unavailable: {exc}")
            self.write_evidence(root, row)
            agd_codes, codes = self.agd_codes(root)
            self.assertTrue({"AGD_STAMP_HASH_MISMATCH", *unverified} <= agd_codes, codes)
            self.assertNotIn("UNKNOWN_FIELD", codes)

    def test_an_over_long_producer_ref_is_reported_not_raised(self) -> None:
        """A producer_artifact_ref whose file name is too long leaves the producer side unbound, with a reported error.

        With stamps/ present, Path.exists() on stamps/<300 a>.json raises
        OSError (ENAMETOOLONG) on Python 3.12 on Linux, and validate raised it.
        Where the platform does not raise for such a name, the copy cannot be
        read instead; there the test still asserts that validate and
        delivery-check return the same codes without an exception.
        """
        root = self.not_ready_run("over-long-producer")
        (root / "stamps").mkdir()
        self.write_evidence(root, self.bound_row(root, producer_artifact_ref="stamps/" + "a" * 300 + ".json"))
        errors = self.mod.validate_directory(root)["errors"]
        agd_errors = [error for error in errors if error["code"].startswith("AGD_")]
        self.assertEqual(
            {error["code"] for error in agd_errors}, {"AGD_STAMP_HASH_MISMATCH", "AGD_INDEPENDENCE_UNVERIFIED_FAMILY"}, errors
        )
        mismatch = " ".join(error["message"] for error in agd_errors if error["code"] == "AGD_STAMP_HASH_MISMATCH")
        self.assertIn("producer_payload_hash", mismatch)
        self.assertIn("the producer side declared", " ".join(error["message"] for error in agd_errors))
        self.assertNotIn("UNKNOWN_FIELD", {error["code"] for error in errors})
        result = self.mod.delivery_check(root)
        self.assertEqual(result["verdict"], "NOT READY")
        self.assertNotIn("DELIVERY_VALIDATE_CRASHED", result["codes"])
        self.assertLessEqual({"AGD_STAMP_HASH_MISMATCH", "AGD_INDEPENDENCE_UNVERIFIED_FAMILY"}, set(result["codes"]))

    def test_dispatcher_stamp_unverified_end_to_end(self) -> None:
        """A claude dispatch under a command override or an endpoint variable stamps unverified; its copy never counts.

        The command override names the stand-in CLI; with the endpoint
        variable, command discovery finds it instead. The participant's ok
        status is a regression check: it held before stamps existed.
        """
        from installer.ai_agents_skills.delegation_dispatch import dispatch_external_agents

        home = self.tmp / "dispatch"
        home.mkdir()
        script = home / "fake_provider.py"
        script.write_text(FAKE_PROVIDER_CLI_PY, encoding="utf-8")
        command = f'"{sys.executable}" "{script}"'
        cases = {
            "AAS_CLAUDE_DISPATCH_COMMAND": {"AAS_CLAUDE_DISPATCH_COMMAND": command},
            "ANTHROPIC_BASE_URL": {"AAS_CLAUDE_DISPATCH_COMMAND": "", "ANTHROPIC_BASE_URL": "https://gateway.invalid/anthropic"},
        }
        for variable, env in cases.items():
            with self.subTest(variable):
                args = SimpleNamespace(
                    root=home,
                    platform=None,
                    task="Re-derive the claim.",
                    task_file=None,
                    providers=None,
                    provider="claude",
                    resolved_model=None,
                    resolved_thinking=None,
                    run_dir=None,
                    max_providers=1,
                    research=False,
                    dry_run=False,
                    allow_external_cli=True,
                    role="reviewer",
                    template=None,
                    timeout=60,
                    exclude_family=None,
                )
                with (
                    mock.patch.dict(os.environ, env),
                    mock.patch(
                        "installer.ai_agents_skills.delegation.discover_tool",
                        lambda name, *_args: {"logical_name": name, "status": "missing"},
                    ),
                    mock.patch(
                        "installer.ai_agents_skills.delegation_dispatch.discover_tool",
                        lambda name, *_args: {"logical_name": name, "status": "ok", "command": command},
                    ),
                ):
                    result = dispatch_external_agents(args, load_manifests())
                participant = result["participants"][0]
                self.assertEqual(participant["status"], "ok", participant["validation"])
                stamp_bytes = (Path(result["run_dir"]) / participant["stamp_ref"]).read_bytes()
                stamp = json.loads(stamp_bytes)
                self.assertEqual(stamp["model_family"], "unverified")
                self.assertIn(variable, stamp["unverified_by"])

                root = self.not_ready_run(f"dispatched-{variable}")
                reviewer_ref = f"artifacts/stamps/{result['run_id']}/claude-external-1.json"
                producer_ref, producer_hash = self.stamp_copy(root, "claude-external-2", "claude", "anthropic")
                row = self.agd_row(
                    verdict_kind="independent_rederivation",
                    family_source="launch_stamp",
                    reviewer_family="unverified",
                    artifact_ref=reviewer_ref,
                    payload_hash=self.copy_file(root, reviewer_ref, stamp_bytes),
                    producer_family="anthropic",
                    producer_artifact_ref=producer_ref,
                    producer_payload_hash=producer_hash,
                )
                self.write_evidence(root, row)
                agd_codes, codes = self.agd_codes(root)
                self.assertEqual(agd_codes, {"AGD_INDEPENDENCE_UNVERIFIED_FAMILY"}, codes)
                self.assertNotIn("UNKNOWN_FIELD", codes)

                # Claiming the provider's table family for that stamp does not help.
                row["reviewer_family"] = "anthropic"
                self.write_evidence(root, row)
                agd_codes, codes = self.agd_codes(root)
                self.assertEqual(agd_codes, {"AGD_STAMP_FAMILY_MISMATCH", "AGD_INDEPENDENCE_UNVERIFIED_FAMILY"}, codes)

    def test_delivery_check_hashes_stamp_copies(self) -> None:
        """validate re-reads both stamp copies, so delivery-check hashes them before and after validate.

        Either copy changed after validate returns makes the run NOT READY,
        and input_files lists both copies.
        """
        for field in ("artifact_ref", "producer_artifact_ref"):
            with self.subTest(field):
                root = self.ready_run(f"digest-{field}")
                row = self.bound_row(root)
                self.write_evidence(root, row)
                baseline = self.mod.delivery_check(root)
                self.assertEqual((baseline["verdict"], baseline["codes"]), ("READY", []), baseline)
                listed = [name for name, _ in baseline["input_files"]]
                self.assertIn(row["artifact_ref"], listed)
                self.assertIn(row["producer_artifact_ref"], listed)
                original = self.mod.validate_directory

                def validate_then_append(run: Path, *args, ref: str = row[field], **kwargs) -> dict:
                    result = original(run, *args, **kwargs)
                    with (run / ref).open("ab") as handle:
                        handle.write(b"\n")
                    return result

                with mock.patch.object(self.mod, "validate_directory", side_effect=validate_then_append):
                    result = self.mod.delivery_check(root)
                self.assertEqual((result["verdict"], result["codes"]), ("NOT READY", ["DELIVERY_INPUT_CHANGED"]), result)
