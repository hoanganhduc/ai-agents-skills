from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from installer.ai_agents_skills.manifest import REPO_ROOT
from installer.ai_agents_skills.manifest import load_manifests
from installer.ai_agents_skills.selectors import resolve_artifacts, resolve_skills


DEEP_RESEARCH = REPO_ROOT / "canonical" / "skills" / "deep-research-workflow"
CROSS_AGENT = REPO_ROOT / "canonical" / "skills" / "cross-agent-delegation"
TEMPLATES = REPO_ROOT / "canonical" / "templates"
RUNTIME_WORKSPACE = REPO_ROOT / "canonical" / "runtime" / "workspace"
DEEP_RESEARCH_RUNTIME = REPO_ROOT / "canonical" / "runtime" / "skills" / "deep-research-workflow" / "deep_research_workflow.py"


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
                env=env,
                check=False,
            )
            self.assertEqual(validate.returncode, 0, validate.stderr)
            payload = json.loads(validate.stdout)
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["checked"]["delivery"], 1)

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

    def test_v2_finalizable_delivery_fails_closed_on_stale_model_freshness(self) -> None:
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
            stale = json.loads((research_dir / "model_freshness.json").read_text(encoding="utf-8"))
            stale["freshness_checked_at"] = "2020-01-01T00:00:00Z"
            (research_dir / "model_freshness.json").write_text(json.dumps(stale), encoding="utf-8")
            (research_dir / "report.md").write_text("Clean report.\n", encoding="utf-8")
            self.write_minimal_delivery(research_dir, decision="ready", report_ref="report.md", guard_output_ids=["G1", "G2"])

            code, payload = self.validate_research_dir(research_dir)
            self.assertEqual(code, 1)
            self.assertIn("MODEL_FRESHNESS_STALE", {error["code"] for error in payload["errors"]})

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
