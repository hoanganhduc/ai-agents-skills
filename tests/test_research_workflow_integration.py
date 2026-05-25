from __future__ import annotations

import unittest
from pathlib import Path

from installer.ai_agents_skills.manifest import REPO_ROOT


DEEP_RESEARCH = REPO_ROOT / "canonical" / "skills" / "deep-research-workflow"
CROSS_AGENT = REPO_ROOT / "canonical" / "skills" / "cross-agent-delegation"
TEMPLATES = REPO_ROOT / "canonical" / "templates"


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


if __name__ == "__main__":
    unittest.main()
