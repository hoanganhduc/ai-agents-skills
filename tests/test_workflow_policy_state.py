"""Portable parent-owned catalogue, lifecycle and task-recovery regressions."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PACK = Path(__file__).resolve().parents[1] / "canonical/runtime/skills/autonomous-research-loop-runtime"
sys.path.insert(0, str(PACK))
import workflow_state as workflow
import state_transaction as transactions


class WorkflowStateTests(unittest.TestCase):
    def catalog(self, root):
        (root / "paper.tex").write_text("First line\n\\label{lem:test}\nProof\n", encoding="utf-8")
        return workflow.build_source_catalog(root, [{"source_id": "paper", "path": "paper.tex", "kind": "tex", "aliases": ["draft"]}])

    def test_aliases_resolve_explicit_coordinates_and_reject_source_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog = self.catalog(root)
            ref = {"source_id": "draft", "locator": {"kind": "tex_label", "label": "lem:test"}}
            resolved = workflow.resolve_source_ref(catalog, ref, root)
            self.assertEqual(resolved["source_id"], "paper")
            self.assertEqual(resolved["coordinate_status"], "checked")
            (root / "paper.tex").write_text("changed", encoding="utf-8")
            with self.assertRaises(workflow.WorkflowError):
                workflow.resolve_source_ref(catalog, ref, root)

    def test_ambiguous_alias_and_untyped_locator_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog = self.catalog(root)
            with self.assertRaises(workflow.WorkflowError):
                workflow.build_source_catalog(root, [
                    {"source_id": "a", "path": "paper.tex", "kind": "tex", "aliases": ["same"]},
                    {"source_id": "b", "path": "paper.tex", "kind": "tex", "aliases": ["same"]}])
            with self.assertRaises(workflow.WorkflowError):
                workflow.resolve_source_ref(catalog, {"source_id": "paper", "locator": "page 2 or line 2"}, root)

    def stream(self, attempt="attempt-1", response="the exact final"):
        kinds = [("start", {}), ("diagnostic", {"code": "reconnecting"}),
                 ("final", {"text": response}), ("end", {})]
        return b"\n".join(json.dumps({"schema_version": "host_lifecycle_event.v1", "seq": index,
            "attempt_id": attempt, "event": event, **extra}).encode() for index, (event, extra) in enumerate(kinds)) + b"\n"

    def test_reconnect_requires_complete_exact_final_and_order(self):
        good = self.stream()
        self.assertTrue(workflow.validate_lifecycle_stream(good, attempt_id="attempt-1", response="the exact final")["complete"])
        for bad in (good.rsplit(b"\n", 2)[0], good.replace(b'"seq": 2', b'"seq": 4'),
                    good.replace(b'"reconnecting"', b'"fatal_error"'), good.replace(b'the exact final', b'another final')):
            with self.subTest(bad=bad):
                self.assertFalse(workflow.validate_lifecycle_stream(bad, attempt_id="attempt-1", response="the exact final")["complete"])
        self.assertEqual(workflow.validate_lifecycle_stream(None, attempt_id="attempt-1", response="x")["status"], "unverified")

    def tasks(self):
        return [{"task_id": "draft", "phase": "produce", "wave": 0, "depends_on": []},
                {"task_id": "review", "phase": "review", "wave": 1, "depends_on": ["draft"]}]

    def test_dependencies_stop_and_unique_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow.initialize_workflow(root, self.tasks())
            with self.assertRaises(workflow.WorkflowError):
                workflow.claim_task(root, "review", owner_id="owner", request_id="early")
            first = workflow.claim_task(root, "draft", owner_id="owner", request_id="claim-1")
            repeat = workflow.claim_task(root, "draft", owner_id="owner", request_id="claim-1")
            self.assertEqual(first["attempt_id"], repeat["attempt_id"])
            self.assertFalse(first["process_managed"])
            with self.assertRaises(workflow.WorkflowError):
                workflow.claim_task(root, "draft", owner_id="other", request_id="claim-2")
            (root / "STOP_REQUESTED").write_text("stop", encoding="utf-8")
            with self.assertRaises(workflow.WorkflowError):
                workflow.claim_task(root, "draft", owner_id="owner", request_id="claim-1")
            self.assertEqual(workflow.workflow_readiness(root)["status"], "stopped")

    def test_readiness_of_missing_run_is_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "missing"
            self.assertEqual(workflow.workflow_readiness(root)["status"], "uninitialized")
            self.assertFalse(root.exists())

    def test_claim_recovers_committed_attempt_after_interruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow.initialize_workflow(root, self.tasks())
            original = workflow.commit_transaction
            def crash(*args, **kwargs):
                return original(*args, **kwargs, crash_after=1)
            with mock.patch.object(workflow, "commit_transaction", side_effect=crash):
                with self.assertRaises(transactions.InjectedCrash):
                    workflow.claim_task(root, "draft", owner_id="owner", request_id="same")
            resumed = workflow.claim_task(root, "draft", owner_id="owner", request_id="same")
            self.assertEqual(resumed["attempt"], 1)

    def test_stop_arriving_at_claim_cas_prevents_ownership(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow.initialize_workflow(root, self.tasks())
            original = workflow.commit_transaction
            def stop_before_commit(*args, **kwargs):
                (root / "STOP_REQUESTED").write_text("operator", encoding="utf-8")
                return original(*args, **kwargs)
            with mock.patch.object(workflow, "commit_transaction", side_effect=stop_before_commit):
                with self.assertRaises(workflow.WorkflowError):
                    workflow.claim_task(root, "draft", owner_id="owner", request_id="request")
            state = json.loads((root / workflow.STATE_PATH).read_text(encoding="utf-8"))
            self.assertEqual(state["tasks"]["draft"]["attempt"], 0)

    def admission_fixture(self, root):
        import panel_parent
        catalogue = self.catalog(root)
        ref = {"source_id": "paper", "locator": {"kind": "line", "start": 1, "end": 1}}
        contract = {"schema_version": "workflow_host_contract.v1", "source_root": str(root),
                    "source_catalog": catalogue, "source_refs": [ref],
                    "original_finding_ids": ["F1"], "require_stream": True}
        input_sha = hashlib.sha256(b"parent prompt").hexdigest()
        tasks = self.tasks()
        tasks[0].update(input_sha256=input_sha, host_contract=contract,
            required_assurances=["source_coverage", "finding_coverage", "stream_complete"])
        workflow.initialize_workflow(root, tasks)
        claim = workflow.claim_task(root, "draft", owner_id="parent", request_id="request-1")
        response = "The source supports the original observation at the specified location."
        context = {"dispatch_id": "dispatch-1", "attempt_id": claim["attempt_id"],
                   "workflow_contract_sha256": claim["contract_sha256"],
                   "owner_id": "parent", "host_task_id": "native-task-1", "host_session_id": "session-1",
                   "input_sha256": input_sha, "transport": "host-native", "source_refs": [ref],
                   "lifecycle_stream": self.stream(claim["attempt_id"], response)}
        receipt = {key: context[key] for key in ("dispatch_id", "attempt_id", "owner_id", "host_task_id", "host_session_id", "input_sha256", "transport")}
        receipt.update(schema_version="host_panel_receipt.v1", output_sha256=hashlib.sha256(response.encode()).hexdigest(),
            review_dispositions={"dispositions": [{"finding_id": "F1", "status": "confirmed"}],
                                 "new_findings": [{"finding_id": "F2", "status": "candidate"}]})
        return context, receipt, response, contract

    def test_actual_host_admission_requires_exact_coverage(self):
        import panel_parent
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            context, receipt, response, contract = self.admission_fixture(root)
            result = panel_parent.admit_host_panel_result(receipt, host_context=context,
                phase="produce", response=response, host_contract=contract,
                required_assurances=["source_coverage", "finding_coverage", "stream_complete"])
            self.assertTrue(result["assurance_pass"], result)
            receipt["review_dispositions"]["dispositions"].append({"finding_id": "F1", "status": "rejected"})
            failed = panel_parent.admit_host_panel_result(receipt, host_context=context,
                phase="produce", response=response, host_contract=contract)
            self.assertFalse(failed["admitted"], failed)

    def test_missing_host_contract_does_not_fabricate_coverage(self):
        import panel_parent
        with tempfile.TemporaryDirectory() as tmp:
            context, receipt, response, _ = self.admission_fixture(Path(tmp))
            result = panel_parent.admit_host_panel_result(receipt, host_context=context,
                phase="produce", response=response, required_assurances=["source_coverage"])
            self.assertFalse(result["assurance_pass"])
            self.assertEqual(result["contract_validation"]["status"], "unverified")

    def test_result_commit_recovery_is_exactly_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            context, receipt, response, _ = self.admission_fixture(root)
            original = workflow.commit_transaction
            def crash(*args, **kwargs):
                return original(*args, **kwargs, crash_after=1)
            with mock.patch.object(workflow, "commit_transaction", side_effect=crash):
                with self.assertRaises(transactions.InjectedCrash):
                    workflow.record_host_result(root, "draft", receipt=receipt, response=response, host_context=context)
            retry = workflow.record_host_result(root, "draft", receipt=receipt, response=response, host_context=context)
            self.assertEqual(retry["status"], "already_recorded")
            events = [json.loads(line) for line in (root / workflow.EVENT_PATH).read_text(encoding="utf-8").splitlines()]
            self.assertEqual(sum(e["event_id"].startswith("result-") for e in events), 1)
            self.assertIn("review", workflow.workflow_readiness(root)["ready_tasks"])

    def test_late_result_reconciles_without_resuming_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            context, receipt, response, _ = self.admission_fixture(root)
            (root / "STOP_REQUESTED").write_text("operator stop", encoding="utf-8")
            result = workflow.record_host_result(root, "draft", receipt=receipt, response=response, host_context=context)
            self.assertEqual(result["status"], "stopped")
            self.assertTrue((root / "STOP_REQUESTED").exists())
            self.assertEqual(workflow.workflow_readiness(root)["ready_tasks"], [])

    def test_old_attempt_cannot_complete_new_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            context, receipt, response, _ = self.admission_fixture(root)
            workflow.release_incomplete(root, "draft", owner_id="parent", attempt_id=context["attempt_id"])
            workflow.claim_task(root, "draft", owner_id="replacement", request_id="request-2")
            with self.assertRaises(workflow.WorkflowError):
                workflow.record_host_result(root, "draft", receipt=receipt, response=response, host_context=context)

    def test_worker_cannot_weaken_the_parent_contract_in_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            context, receipt, response, _ = self.admission_fixture(root)
            path = root / workflow.STATE_PATH
            state = json.loads(path.read_text(encoding="utf-8"))
            state["tasks"]["draft"]["host_contract"] = None
            state["tasks"]["draft"]["required_assurances"] = []
            state["tasks"]["draft"]["contract_sha256"] = workflow._task_contract_digest(state["tasks"]["draft"])
            path.write_text(json.dumps(state), encoding="utf-8")
            with self.assertRaises(workflow.WorkflowError):
                workflow.record_host_result(root, "draft", receipt=receipt, response=response, host_context=context)


if __name__ == "__main__":
    unittest.main()
