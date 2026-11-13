"""Tests for negative_space.v1 permanence and anti-false-consensus gates."""

from __future__ import annotations

import hashlib
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

    def _refuted(self, root: Path, approach_id: str = "approach-a") -> dict:
        entry = ns.build_entry(
            kind="falsified_hypothesis",
            mechanism_text="counterexample at n=7",
            failure_summary="refuted",
            reopen_condition="new mechanism",
            approach_id=approach_id,
            evidence_ids=["E1"],
        )
        ns.append_entries(root, [entry])
        return entry

    def test_a_real_supersession_clears_the_approach_and_validates(self) -> None:
        # The complement of the two tests below: a supersession that names a row
        # actually in the ledger still retires the old row and reopens nothing.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            entry = self._refuted(root)
            closed, new = ns.supersession_rows(
                old_entry=entry,
                new_mechanism_text="different mechanism",
                failure_summary="still exploring",
                reopen_condition="r2",
                different_family_review_fingerprint="fp-df",
                evidence_ids=["E2"],
            )
            ns.rewrite_ledger(root, [closed, new])

            self.assertEqual(ns.dangling_supersessions(ns.load_negative_space(root)), [])
            report = ns.validate_negative_space(root)
            self.assertEqual(report["status"], "ok", report["errors"])

    def test_a_supersession_naming_no_row_keeps_the_approach_blocked(self) -> None:
        # Flipping a refuted row to superseded is how an approach becomes
        # attemptable again. Nothing checked that the named successor existed,
        # so a retirement pointing at a row that was never written read exactly
        # like a real one and handed the approach back.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            entry = self._refuted(root)
            retired = dict(entry)
            retired["status"] = "superseded"
            retired["superseded_by"] = "ns-does-not-exist"
            retired["closed_at"] = ns.utc_now()
            ns.rewrite_ledger(root, [retired])

            reason = ns.approach_blocked_by_negative_space(root, "approach-a")
            self.assertTrue(reason.startswith("negative_space_dangling_supersession:"))
            self.assertIn(entry["entry_id"], reason)

            report = ns.validate_negative_space(root)
            self.assertEqual(report["status"], "error")
            self.assertTrue(
                any("superseded by unknown entry_id" in err for err in report["errors"]),
                report["errors"],
            )

            eligible, excluded = gf._eligible_approaches(
                _registry_with_approaches(), run_dir=root
            )
            self.assertNotIn("approach-a", {row["id"] for row in eligible})
            self.assertTrue(
                any(
                    row["approach_id"] == "approach-a"
                    and "negative_space_dangling_supersession" in row["reason"]
                    for row in excluded
                )
            )

    def test_a_row_superseded_by_itself_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            entry = self._refuted(root)
            retired = dict(entry)
            retired["status"] = "superseded"
            retired["superseded_by"] = entry["entry_id"]
            retired["closed_at"] = ns.utc_now()
            ns.rewrite_ledger(root, [retired])

            report = ns.validate_negative_space(root)
            self.assertEqual(report["status"], "error")
            self.assertTrue(
                any("superseded by itself" in err for err in report["errors"]),
                report["errors"],
            )
            self.assertTrue(
                ns.approach_blocked_by_negative_space(root, "approach-a").startswith(
                    "negative_space_dangling_supersession:"
                )
            )

    def test_an_open_row_still_blocks_under_its_own_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._refuted(root)
            self.assertTrue(
                ns.approach_blocked_by_negative_space(root, "approach-a").startswith(
                    "negative_space_open:"
                )
            )
            self.assertEqual(
                ns.approach_blocked_by_negative_space(root, "approach-b"), ""
            )


class NegativeSpaceWriterTests(unittest.TestCase):
    """Who wrote a row decides whether it blocks; long rows are cut only where rendered."""

    @staticmethod
    def _row(
        entry_id: str,
        *,
        approach_id: str = "approach-a",
        mechanism: str = "fixed point",
        **fields: object,
    ) -> dict:
        # Extra fields are set on the built row rather than passed to
        # build_entry, so a value build_entry would refuse still reaches the
        # function under test.
        row = ns.build_entry(
            kind="dead_end",
            mechanism_text=mechanism,
            failure_summary="failed",
            reopen_condition="new mechanism",
            approach_id=approach_id,
            evidence_ids=["E1"],
            entry_id=entry_id,
        )
        row.update(fields)
        return row

    def test_worker_row_is_advisory_while_host_and_legacy_rows_block(self) -> None:
        """A worker's dead-end row never makes its approach ineligible, open or dangling.

        The row without a writer (as every row on disk was written before the
        writer field) and the host row are regression checks: both blocked by
        approach id before the change and still do. A writer that is anything
        other than exactly "worker" blocks as well.
        """
        worker = {"writer": "worker", "review_status": "unreviewed"}
        dangling = {
            "status": "superseded",
            "superseded_by": "ns-missing",
            "closed_at": "2026-11-10T00:00:00Z",
        }
        cases = {
            "worker row": ([self._row("ns-1", **worker)], ""),
            "worker row, dangling supersession": (
                [self._row("ns-1", **worker, **dangling)],
                "",
            ),
            "row without writer (regression check)": (
                [self._row("ns-1")],
                "negative_space_open:ns-1",
            ),
            "host row (regression check)": (
                [self._row("ns-1", writer="host")],
                "negative_space_open:ns-1",
            ),
            "writer that is not exactly worker": (
                [self._row("ns-1", writer=["worker"], review_status="unreviewed")],
                "negative_space_open:ns-1",
            ),
            "host row, dangling supersession": (
                [self._row("ns-1", writer="host", **dangling)],
                "negative_space_dangling_supersession:ns-1",
            ),
        }
        for label, (rows, expected) in cases.items():
            with self.subTest(label), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                ns.rewrite_ledger(root, rows)
                self.assertEqual(
                    ns.approach_blocked_by_negative_space(root, "approach-a"), expected
                )
                eligible, _ = gf._eligible_approaches(
                    _registry_with_approaches(), run_dir=root
                )
                self.assertEqual(
                    "approach-a" in {row["id"] for row in eligible}, expected == ""
                )

    def test_duplicate_open_fingerprint_errors_only_between_host_rows(self) -> None:
        """Two open host rows with one mechanism on one approach are an error; a worker row makes it a warning.

        The host-and-host and legacy-and-host cases are regression checks: they
        were errors before the change.
        """
        worker = {"writer": "worker", "review_status": "unreviewed"}
        fingerprint = ns.mechanism_fingerprint("fixed point")
        cases = {
            "host and host (regression check)": ({"writer": "host"}, {"writer": "host"}, True),
            "row without writer and host (regression check)": ({}, {"writer": "host"}, True),
            "host and worker": ({"writer": "host"}, worker, False),
            "worker and worker": (worker, worker, False),
        }
        for label, (first, second, is_error) in cases.items():
            with self.subTest(label), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                ns.rewrite_ledger(
                    root,
                    [
                        self._row("ns-1", **first),
                        self._row("ns-2", mechanism="  Fixed   POINT ", **second),
                    ],
                )
                report = ns.validate_negative_space(root)
                errors = [
                    item
                    for item in report["errors"]
                    if "duplicate open mechanism_fingerprint" in item
                ]
                warnings = [
                    item
                    for item in report["warnings"]
                    if "duplicate open mechanism_fingerprint" in item
                ]
                if is_error:
                    self.assertEqual(
                        errors,
                        [
                            "duplicate open mechanism_fingerprint for approach "
                            f"approach-a: {fingerprint}"
                        ],
                    )
                    self.assertEqual(warnings, [])
                else:
                    self.assertEqual(report["status"], "ok", report["errors"])
                    self.assertEqual(errors, [])
                    self.assertEqual(
                        warnings,
                        [
                            "duplicate open mechanism_fingerprint for approach "
                            f"approach-a involving a worker row: {fingerprint}"
                        ],
                    )

    def test_validate_entry_refuses_bad_writer_review_and_basis(self) -> None:
        """Each row field takes only its listed values; a value that is not a string is refused, never raised on.

        The "valid rows" subtest is a regression check: those rows had no
        errors before the fields were checked either.
        """
        cases = {
            "writer admin": ({"writer": "admin"}, "writer is invalid"),
            "writer as a list": ({"writer": ["worker"]}, "writer is invalid"),
            "writer as null": ({"writer": None}, "writer is invalid"),
            "worker row without review_status": (
                {"writer": "worker"},
                "worker rows require review_status",
            ),
            "review_status as a list": (
                {"writer": "worker", "review_status": ["reviewed"]},
                "review_status is invalid",
            ),
            "checker_basis host_checked": (
                {"checker_basis": "host_checked"},
                "checker_basis is invalid",
            ),
            "checker_basis as an object": (
                {"checker_basis": {"basis": "self_reported"}},
                "checker_basis is invalid",
            ),
        }
        for label, (fields, message) in cases.items():
            with self.subTest(label):
                self.assertIn(message, ns.validate_entry(self._row("ns-1", **fields)))
        with self.subTest("valid rows"):
            self.assertEqual(ns.validate_entry(self._row("ns-1")), [])
            self.assertEqual(
                ns.validate_entry(
                    self._row(
                        "ns-1",
                        writer="worker",
                        review_status="reviewed",
                        checker_basis="self_reported",
                    )
                ),
                [],
            )
        with self.subTest("build_entry stores the fields only when given"):
            common = {
                "kind": "falsified_hypothesis",
                "mechanism_text": "m",
                "failure_summary": "f",
                "reopen_condition": "r",
                "evidence_ids": ["E1"],
            }
            plain = ns.build_entry(**common)
            self.assertFalse({"writer", "review_status", "checker_basis"} & set(plain))
            worker = ns.build_entry(
                **common,
                writer="worker",
                review_status="unreviewed",
                checker_basis="self_reported",
            )
            self.assertEqual(
                (worker["writer"], worker["review_status"], worker["checker_basis"]),
                ("worker", "unreviewed", "self_reported"),
            )
            for bad in (
                {"writer": "admin"},
                {"writer": "worker"},
                {"writer": "host", "checker_basis": "host_checked"},
            ):
                with self.assertRaises(ValueError):
                    ns.build_entry(**common, **bad)

    def test_long_row_keeps_fingerprint_and_renders_cut_marker(self) -> None:
        """The cut marker exists only in rendered text; storage and fingerprints keep _clean's value.

        A 12,000-character mechanism is stored as its first 10,000 characters
        with the fingerprint of that text, as before the change (a regression
        check: a marker added inside _clean would change both). Rendering shows
        each field whole and every line break inside a field as the two
        characters \\n, so a row is one line; summarize_open still cuts the
        mechanism and failure fields at 200 characters with a visible marker.
        """
        mechanism = "Colour-step " * 999 + "Colour-step-"
        self.assertEqual(len(mechanism), 12_000)
        row = ns.build_entry(
            kind="dead_end",
            mechanism_text=mechanism,
            failure_summary="failed",
            reopen_condition="new mechanism",
            approach_id="approach-a",
            evidence_ids=["E1"],
        )
        stored = mechanism[:10_000]
        self.assertEqual(stored, stored.strip())
        normalized = " ".join(stored.lower().split())
        self.assertEqual(row["mechanism_text"], stored)
        self.assertEqual(
            row["mechanism_fingerprint"],
            "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        )

        line = ns.render_row_line(row)
        self.assertTrue(line.startswith("| "), line)
        self.assertEqual(len(line.splitlines()), 1)
        self.assertIn(f"mechanism: {stored};", line)
        self.assertEqual(ns.clip_for_render("short"), "short")
        self.assertEqual(
            ns.clip_for_render("x" * 205, 200), "x" * 200 + " [cut: 5 characters omitted]"
        )

        broken = dict(
            row,
            mechanism_text="first\nsecond\r\nthird\rfourth fifth\x85sixth",
            writer="worker",
            review_status="unreviewed",
        )
        line = ns.render_row_line(broken)
        self.assertEqual(len(line.splitlines()), 1)
        self.assertIn(
            "mechanism: first\\nsecond\\nthird\\nfourth\\nfifth\\nsixth", line
        )
        self.assertIn("writer worker", line)
        self.assertIn("review unreviewed", line)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ns.append_entries(root, [row])
            summary = ns.summarize_open(root)
            self.assertEqual(len(summary), 1)
            self.assertEqual(
                summary[0]["mechanism_text"],
                stored[:200] + " [cut: 9800 characters omitted]",
            )
            self.assertEqual(
                ns.load_negative_space(root)[0]["mechanism_text"], stored
            )

    def test_a_ledger_line_json_cannot_decode_is_reported_as_invalid_json(self) -> None:
        """json raises RecursionError on deep nesting; the reader reports it as invalid JSON.

        A RecursionError escaped every caller that handles a bad ledger as a
        ValueError, so validation and the eligibility check crashed. An
        integer longer than the digit limit already raised ValueError, so that
        case is a regression check of the error type.
        """
        cases = {
            "deep nesting": ("[" * 100_000 + "]" * 100_000, "invalid JSON on negative_space line 1"),
            "integer past the digit limit (regression check)": ('{"n": ' + "1" * 5_000 + "}", ""),
        }
        for label, (payload, message) in cases.items():
            with self.subTest(label), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                path = ns.negative_space_path(root)
                path.parent.mkdir(parents=True)
                path.write_text(payload + "\n", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, message):
                    ns.load_negative_space(root)
                report = ns.validate_negative_space(root)
                self.assertEqual(report["status"], "error")
                with self.assertRaises(ValueError):
                    gf._eligible_approaches(_registry_with_approaches(), run_dir=root)


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


class RenderCutsOnlyWithAPreexistingLimitTests(unittest.TestCase):
    """Rendering cuts no field unless the caller passes a limit (user decision, 2026-11-13).

    The 200-character render cut was a number the agent chose in N5, and it is
    gone. summarize_open keeps its cut of mechanism_text and failure_summary at
    200 characters: that cut predates the Open Math work.
    """

    @staticmethod
    def _row(**fields: object) -> dict:
        values = dict(
            kind="dead_end",
            mechanism_text="mechanism",
            failure_summary="failure",
            reopen_condition="a new invariant",
            approach_id="approach-a",
            evidence_ids=["E1"],
        )
        values.update(fields)
        return ns.build_entry(**values)

    def test_no_agent_cap_clip_for_render_cuts_only_with_a_limit(self) -> None:
        self.assertFalse(hasattr(ns, "RENDER_FIELD_LIMIT"))
        self.assertEqual(ns.clip_for_render("x" * 5_000), "x" * 5_000)
        self.assertEqual(
            ns.clip_for_render("x" * 205, 200), "x" * 200 + " [cut: 5 characters omitted]"
        )

    def test_no_agent_cap_render_row_shows_each_field_whole(self) -> None:
        mechanism, failure = "M" * 3_000, "F" * 2_500
        line = ns.render_row_line(self._row(mechanism_text=mechanism, failure_summary=failure))
        self.assertIn(f"mechanism: {mechanism};", line)
        self.assertIn(f"failure: {failure};", line)
        self.assertNotIn("[cut:", line)

    def test_no_agent_cap_summarize_open_keeps_only_its_older_cut(self) -> None:
        """Regression check: summarize_open cut these two fields at 200 characters before N5."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ns.append_entries(root, [self._row(mechanism_text="M" * 300, failure_summary="F" * 300)])
            (summary,) = ns.summarize_open(root)
        self.assertEqual(summary["mechanism_text"], "M" * 200 + " [cut: 100 characters omitted]")
        self.assertEqual(summary["failure_summary"], "F" * 200 + " [cut: 100 characters omitted]")


if __name__ == "__main__":
    unittest.main()
