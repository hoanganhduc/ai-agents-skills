from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
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
            "deep-research-workflow",
            "source-research",
            "research-report-reviewer",
            "research-verification-gate",
            "zotero",
            "calibre",
            "getscipapers-requester",
            "paper-lookup",
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


if __name__ == "__main__":
    unittest.main()
