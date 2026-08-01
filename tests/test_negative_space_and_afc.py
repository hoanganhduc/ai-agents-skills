"""Tests for negative_space.v1 permanence and anti-false-consensus gates."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = (
    REPO_ROOT
    / "canonical"
    / "runtime"
    / "skills"
    / "autonomous-research-loop-runtime"
)
sys.path.insert(0, str(RUNTIME_DIR))

import anti_false_consensus as afc  # noqa: E402
import goal_focus as gf  # noqa: E402
import negative_space as ns  # noqa: E402


def _registry_with_approaches(
    *,
    a_status: str = "eligible",
    b_status: str = "eligible",
    a_reopen: str = "",
) -> dict:
    approaches = {
        "approach-a": {
            "id": "approach-a",
            "campaign_id": "camp-1",
            "status": a_status,
            "estimates": {"goal_resolution": {"lower": 2, "upper": 3}},
        },
        "approach-b": {
            "id": "approach-b",
            "campaign_id": "camp-1",
            "status": b_status,
            "estimates": {"goal_resolution": {"lower": 1, "upper": 2}},
        },
    }
    if a_reopen:
        approaches["approach-a"]["reopen_condition"] = a_reopen
    return {
        "schema_version": "approach_registry.v2",
        "registry_revision": 1,
        "campaigns": {
            "camp-1": {
                "status": "eligible",
                "approaches": approaches,
            }
        },
    }


class NegativeSpaceUnitTest(unittest.TestCase):
    def test_mechanism_fingerprint_normalizes_whitespace_case(self) -> None:
        a = ns.mechanism_fingerprint("Try Fixed-Point Argument")
        b = ns.mechanism_fingerprint("  try   fixed-point   argument  ")
        self.assertEqual(a, b)
        self.assertTrue(a.startswith("sha256:"))

    def test_build_entry_requires_evidence_or_absent_reason(self) -> None:
        with self.assertRaises(ValueError):
            ns.build_entry(
                kind="blocked_route",
                mechanism_text="m",
                failure_summary="f",
                reopen_condition="new mechanism",
            )
        row = ns.build_entry(
            kind="blocked_route",
            mechanism_text="m",
            failure_summary="f",
            reopen_condition="new mechanism",
            evidence_absent_reason="none",
        )
        self.assertEqual(row["schema_version"], ns.NEGATIVE_SPACE_SCHEMA)
        self.assertEqual(row["status"], "open")

    def test_append_and_open_filter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            entry = ns.build_entry(
                kind="failed_exploration",
                mechanism_text="enumeration n<=4",
                failure_summary="no counterexample; strategy exhausted",
                reopen_condition="new pruning mechanism",
                approach_id="approach-a",
                campaign_id="camp-1",
                evidence_ids=["E1"],
            )
            ns.append_entries(root, [entry])
            self.assertTrue((root / ns.NEGATIVE_SPACE_REL).is_file())
            open_rows = ns.open_entries(root, approach_id="approach-a")
            self.assertEqual(len(open_rows), 1)
            self.assertEqual(
                ns.approach_blocked_by_negative_space(root, "approach-a").split(":")[0],
                "negative_space_open",
            )
            self.assertEqual(ns.approach_blocked_by_negative_space(root, "approach-b"), "")

    def test_wording_only_reopen_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            entry = ns.build_entry(
                kind="blocked_route",
                mechanism_text="fixed point",
                failure_summary="failed",
                reopen_condition="new mechanism",
                approach_id="approach-a",
                evidence_ids=["E1"],
            )
            ns.append_entries(root, [entry])
            ok, reason = ns.can_reopen_with_mechanism(
                root,
                approach_id="approach-a",
                new_mechanism_text="Fixed Point",
                different_family_review_fingerprint="rev-1",
            )
            self.assertFalse(ok)
            self.assertEqual(reason, "wording_only_reopen")
            ok2, reason2 = ns.can_reopen_with_mechanism(
                root,
                approach_id="approach-a",
                new_mechanism_text="spectral method",
                different_family_review_fingerprint="",
            )
            self.assertFalse(ok2)
            self.assertEqual(reason2, "missing_different_family_review")
            ok3, reason3 = ns.can_reopen_with_mechanism(
                root,
                approach_id="approach-a",
                new_mechanism_text="spectral method",
                different_family_review_fingerprint="rev-df",
            )
            self.assertTrue(ok3)
            self.assertEqual(reason3, "ok")

    def test_validate_blocked_without_row_errors_in_enforce(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = _registry_with_approaches(
                a_status="blocked", a_reopen="new mechanism required"
            )
            report = ns.validate_negative_space(root, registry, enforce=True)
            self.assertEqual(report["status"], "error")
            self.assertTrue(
                any("without a negative_space row" in e for e in report["errors"])
            )

    def test_selection_filters_open_negative_space(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = _registry_with_approaches()
            entry = ns.build_entry(
                kind="blocked_route",
                mechanism_text="dead end A",
                failure_summary="exhausted",
                reopen_condition="new mechanism",
                approach_id="approach-a",
                evidence_ids=["E1"],
            )
            ns.append_entries(root, [entry])
            eligible, excluded = gf._eligible_approaches(registry, run_dir=root)
            ids = {row["id"] for row in eligible}
            self.assertNotIn("approach-a", ids)
            self.assertIn("approach-b", ids)
            self.assertTrue(
                any(
                    row["approach_id"] == "approach-a"
                    and "negative_space_open" in row["reason"]
                    for row in excluded
                )
            )

    def test_no_delete_api_and_historical_rows_kept(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            e1 = ns.build_entry(
                kind="dead_end",
                mechanism_text="m1",
                failure_summary="f1",
                reopen_condition="r1",
                approach_id="a1",
                evidence_ids=["E1"],
            )
            ns.append_entries(root, [e1])
            closed, new = ns.supersession_rows(
                old_entry=e1,
                new_mechanism_text="m2",
                failure_summary="still exploring",
                reopen_condition="r2",
                different_family_review_fingerprint="fp-df",
                evidence_ids=["E2"],
            )
            ns.rewrite_ledger(root, [closed, new])
            rows = ns.load_negative_space(root)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["status"], "superseded")
            self.assertEqual(rows[1]["status"], "open")
            self.assertFalse(hasattr(ns, "delete_entry"))


class AntiFalseConsensusUnitTest(unittest.TestCase):
    def test_evidence_delta_and_wording_only(self) -> None:
        prior = {"evidence_ids": ["E1"], "summary": "draft v1"}
        current = {"evidence_ids": ["E1"], "summary": "draft v1 polished"}
        ok, reason, delta = afc.allows_review_round_progress(prior, current)
        self.assertFalse(ok)
        self.assertIn(reason, {"wording_only_convergence", "empty_evidence_delta"})
        self.assertEqual(delta["new_evidence_ids"], [])

        current2 = {"evidence_ids": ["E1", "E2"], "summary": "draft v2"}
        ok2, reason2, delta2 = afc.allows_review_round_progress(prior, current2)
        self.assertTrue(ok2)
        self.assertEqual(reason2, "ok")
        self.assertEqual(delta2["new_evidence_ids"], ["E2"])

    def test_bankable_requires_df_or_machine(self) -> None:
        ok, reason = afc.bankable_review_ok(
            {"different_family": False, "summary": "lgtm from three models"},
            accepted=True,
        )
        self.assertFalse(ok)
        ok2, _ = afc.bankable_review_ok(
            {"different_family": True, "verdict": "pass"},
            accepted=True,
        )
        self.assertTrue(ok2)
        ok3, _ = afc.bankable_review_ok(
            {"different_family": False, "machine_check_passed": True},
            accepted=True,
        )
        self.assertTrue(ok3)
        ok4, reason4 = afc.bankable_review_ok(
            {"providers": ["a", "b"], "different_family": False},
            accepted=True,
        )
        self.assertFalse(ok4)
        self.assertIn(reason4, {"multi_llm_lgtm_not_bank", "missing_different_family_or_machine_check"})

    def test_rounds_exhausted_and_forbid_persist(self) -> None:
        self.assertTrue(afc.review_rounds_exhausted(3, 3))
        self.assertFalse(afc.review_rounds_exhausted(2, 3))
        self.assertTrue(
            afc.forbid_persist_until_approve(
                "We will continue until all reviewers approve the report."
            )
        )
        self.assertFalse(afc.forbid_persist_until_approve("bound-and-escalate after 3 rounds"))

    def test_residual_uncertainty_and_escalate_payload(self) -> None:
        labels = afc.residual_uncertainty_labels(
            load_bearing_claims=[
                {"claim_id": "C1", "status": "supported"},
                {"claim_id": "C2", "status": "disputed"},
            ],
            unfinished_claim_ids=["C1"],
            open_negative_space_ids=["ns-1"],
        )
        self.assertIn("unfinished:C1", labels)
        self.assertIn("disputed:C2", labels)
        self.assertIn("negative_space:ns-1", labels)
        payload = afc.escalate_unfinished_payload(
            reason="review_rounds_exhausted",
            residual_uncertainty=labels,
            rounds_used=3,
            max_rounds=3,
            open_negative_space=[{"entry_id": "ns-1", "approach_id": "a", "failure_summary": "x"}],
        )
        self.assertEqual(payload["status"], "unfinished")
        self.assertFalse(payload["bankable"])

    def test_synthesis_erases_disagreement(self) -> None:
        self.assertTrue(
            afc.synthesis_erases_disagreement(
                ["lemma-2 quantifier gap"],
                "All claims are established.",
                residual_labels=[],
            )
        )
        self.assertFalse(
            afc.synthesis_erases_disagreement(
                ["lemma-2 quantifier gap"],
                "All claims are established.",
                residual_labels=["unfinished:C2"],
            )
        )


class GoalFocusNegativeSpaceIntegrationTest(unittest.TestCase):
    def test_validate_goal_focus_surfaces_ns_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = gf.default_goal_contract("g", ["sc"])
            registry = _registry_with_approaches(
                a_status="blocked", a_reopen="new mechanism"
            )
            plan = gf.default_current_plan(
                goal_revision=1,
                registry_revision=1,
                mode="enforce",
            )
            plan["state"] = "needs_replan"
            plan["enforcement_mode"] = "enforce"
            (root / "goal_contract.json").write_text(
                json.dumps(contract, indent=2) + "\n", encoding="utf-8"
            )
            (root / "approach_registry.json").write_text(
                json.dumps(registry, indent=2) + "\n", encoding="utf-8"
            )
            (root / "current_plan.json").write_text(
                json.dumps(plan, indent=2) + "\n", encoding="utf-8"
            )
            (root / "direction_decisions.jsonl").write_text("", encoding="utf-8")
            result = gf.validate_goal_focus(root, require_enabled=True)
            self.assertEqual(result["status"], "error")
            joined = " ".join(result.get("errors") or [])
            self.assertIn("negative_space", joined)


if __name__ == "__main__":
    unittest.main()
