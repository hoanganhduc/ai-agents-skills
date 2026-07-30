"""Crash recovery and concurrency tests for Goal-Focus state transactions."""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import stat
import sys
import tempfile
import threading
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

import state_transaction as st  # noqa: E402


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _revision_writer_process(
    root_text: str,
    winner: str,
    ready: object,
    start: object,
    outcomes: object,
) -> None:
    """Contend on one CAS revision from a distinct interpreter process."""

    ready.put(winner)
    if not start.wait(10):
        outcomes.put((winner, "start_timeout"))
        return
    try:
        st.commit_transaction(
            Path(root_text),
            json_files={
                "current_plan.json": {"plan_revision": 2, "winner": winner}
            },
            expected_revisions={"current_plan.json": ("plan_revision", 1)},
            lock_timeout_seconds=10,
        )
        outcome = "committed"
    except st.RevisionConflict:
        outcome = "conflict"
    except BaseException as exc:  # pragma: no cover - reported to the parent
        outcome = f"error:{type(exc).__name__}:{exc}"
    outcomes.put((winner, outcome))


def _goal_focus_finalization_kwargs(root: Path) -> dict[str, object]:
    """Return the transaction shape used by Goal-Focus candidate finalization."""

    return {
        "json_files": {
            "current_plan.json": {
                "plan_revision": 5,
                "goal_revision": 3,
                "registry_revision": 2,
                "state": "terminal",
            },
            "goal_contract.json": {
                "goal_revision": 3,
                "obligations": [{"id": "obl-1", "status": "satisfied"}],
            },
            "approach_registry.json": {
                "registry_revision": 2,
                "selected": "campaign-a",
            },
            "loop_state.json": {
                "revision": 8,
                "last_iteration": 2,
                "status": "stopped",
            },
            "budget.json": {
                "revision": 4,
                "spent_iterations": 2,
                "spent_tokens": 800,
            },
            ".goal_focus/candidates/candidate-2.json": {
                "candidate_id": "candidate-2",
                "status": "accepted",
                "finalized_at": "2026-07-30T00:00:00Z",
            },
        },
        "jsonl_appends": {
            "iterations.jsonl": [
                {
                    "event_id": "iteration-candidate-2",
                    "candidate_id": "candidate-2",
                    "iteration": 2,
                    "bank_status": "accepted",
                }
            ],
            "direction_decisions.jsonl": [
                {
                    "event_id": "decision-candidate-2",
                    "decision_id": "decision-candidate-2",
                    "decision_type": "result_finalize",
                    "candidate_id": "candidate-2",
                }
            ],
        },
        "deletes": ["iteration_candidate.json"],
        "expected_revisions": {
            "current_plan.json": ("plan_revision", 4),
            "goal_contract.json": ("goal_revision", 2),
            "approach_registry.json": ("registry_revision", 2),
        },
        "expected_absent": [".goal_focus/candidates/candidate-2.json"],
        "expected_hashes": {
            name: _sha256(root / name)
            for name in (
                "current_plan.json",
                "goal_contract.json",
                "approach_registry.json",
                "loop_state.json",
                "budget.json",
                "iterations.jsonl",
                "iteration_candidate.json",
            )
        },
        "transaction_id": "finalize-candidate-2",
    }


def _crash_during_goal_focus_finalization(root_text: str) -> None:
    """Exit abruptly after a partial multi-artifact finalization apply."""

    root = Path(root_text)
    try:
        st.commit_transaction(
            root,
            **_goal_focus_finalization_kwargs(root),
            crash_after="apply:goal_contract.json",
        )
    except st.InjectedCrash:
        os._exit(73)
    os._exit(74)


class TransactionStateMachineTests(unittest.TestCase):
    def test_revisions_and_cas_expectations_require_exact_integers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "current_plan.json"
            for malformed in ("1", 1.0, True, -1):
                with self.subTest(source_revision=malformed):
                    _write_json(target, {"plan_revision": malformed, "value": "old"})
                    with self.assertRaises(st.TransactionError):
                        st.read_revision(target, "plan_revision")

            _write_json(target, {"plan_revision": 1, "value": "old"})
            for malformed in ("1", 1.0, True, -1):
                with self.subTest(expected_revision=malformed), self.assertRaises(
                    st.TransactionError
                ):
                    st.commit_transaction(
                        root,
                        json_files={
                            "current_plan.json": {
                                "plan_revision": 2,
                                "value": "new",
                            }
                        },
                        expected_revisions={
                            "current_plan.json": ("plan_revision", malformed)
                        },
                    )
                self.assertEqual(
                    json.loads(target.read_text(encoding="utf-8"))["value"],
                    "old",
                )

    def test_compare_and_swap_rejects_stale_revision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_json(root / "current_plan.json", {"plan_revision": 2, "value": "old"})
            with self.assertRaises(st.RevisionConflict):
                st.commit_transaction(
                    root,
                    json_files={"current_plan.json": {"plan_revision": 3, "value": "new"}},
                    expected_revisions={"current_plan.json": ("plan_revision", 1)},
                )
            self.assertEqual(json.loads((root / "current_plan.json").read_text())["value"], "old")

    def test_recovery_finishes_all_postimages_after_each_crash_point(self) -> None:
        for crash_after in ("prepared", 1, "after_apply", "committed"):
            with self.subTest(crash_after=crash_after), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                _write_json(root / "current_plan.json", {"plan_revision": 1, "value": "old"})
                _write_json(root / "goal_contract.json", {"goal_revision": 1, "value": "old"})
                with self.assertRaises(st.InjectedCrash):
                    st.commit_transaction(
                        root,
                        json_files={
                            "current_plan.json": {"plan_revision": 2, "value": "new"},
                            "goal_contract.json": {"goal_revision": 2, "value": "new"},
                        },
                        expected_revisions={
                            "current_plan.json": ("plan_revision", 1),
                            "goal_contract.json": ("goal_revision", 1),
                        },
                        crash_after=crash_after,
                    )
                st.recover_transactions(root)
                self.assertEqual(json.loads((root / "current_plan.json").read_text())["value"], "new")
                self.assertEqual(json.loads((root / "goal_contract.json").read_text())["value"], "new")
                self.assertFalse((root / st.TRANSACTION_DIRNAME).exists())

    def test_binary_postimage_recovers_without_text_normalization(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = b"legacy\x00bytes\xff\r\nsecond-line\r\n"
            with self.assertRaises(st.InjectedCrash):
                st.commit_transaction(
                    root,
                    binary_files={"backups/legacy.bin": payload},
                    crash_after="prepared",
                )

            st.recover_transactions(root)
            self.assertEqual((root / "backups" / "legacy.bin").read_bytes(), payload)
            self.assertFalse((root / st.TRANSACTION_DIRNAME).exists())

    def test_jsonl_append_is_idempotent_by_event_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event = {"event_id": "evt-1", "value": 1}
            st.commit_transaction(root, jsonl_appends={"events.jsonl": [event]})
            st.commit_transaction(root, jsonl_appends={"events.jsonl": [event]})
            rows = [json.loads(line) for line in (root / "events.jsonl").read_text().splitlines()]
            self.assertEqual(rows, [event])

    def test_delete_is_recovered_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pending.json").write_text("{}\n", encoding="utf-8")
            with self.assertRaises(st.InjectedCrash):
                st.commit_transaction(
                    root,
                    json_files={"done.json": {"done": True}},
                    deletes=["pending.json"],
                    crash_after=1,
                )
            st.recover_transactions(root)
            self.assertFalse((root / "pending.json").exists())
            self.assertTrue(json.loads((root / "done.json").read_text())["done"])

    def test_expected_absent_conflict_writes_no_partial_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = b'{"candidate_id":"existing"}\n'
            (root / "pending.json").write_bytes(original)

            with self.assertRaises(st.RevisionConflict):
                st.commit_transaction(
                    root,
                    json_files={
                        "pending.json": {"candidate_id": "replacement"},
                        "side-effect.json": {"written": True},
                    },
                    expected_absent=["pending.json"],
                )

            self.assertEqual((root / "pending.json").read_bytes(), original)
            self.assertFalse((root / "side-effect.json").exists())
            self.assertFalse((root / st.TRANSACTION_DIRNAME).exists())

    def test_two_writers_at_same_revision_yield_one_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_json(root / "current_plan.json", {"plan_revision": 1, "winner": "none"})
            outcomes: list[str] = []
            outcome_lock = threading.Lock()

            def writer(name: str) -> None:
                try:
                    st.commit_transaction(
                        root,
                        json_files={"current_plan.json": {"plan_revision": 2, "winner": name}},
                        expected_revisions={"current_plan.json": ("plan_revision", 1)},
                    )
                    outcome = "committed"
                except st.RevisionConflict:
                    outcome = "conflict"
                with outcome_lock:
                    outcomes.append(outcome)

            threads = [threading.Thread(target=writer, args=(name,)) for name in ("A", "B")]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(sorted(outcomes), ["committed", "conflict"])
            self.assertIn(json.loads((root / "current_plan.json").read_text())["winner"], {"A", "B"})

    def test_two_processes_at_same_revision_yield_exactly_one_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_json(root / "current_plan.json", {"plan_revision": 1, "winner": "none"})
            context = multiprocessing.get_context("spawn")
            ready = context.Queue()
            start = context.Event()
            outcomes = context.Queue()
            processes = [
                context.Process(
                    target=_revision_writer_process,
                    args=(str(root), name, ready, start, outcomes),
                )
                for name in ("process-A", "process-B")
            ]
            try:
                for process in processes:
                    process.start()
                self.assertEqual(
                    {ready.get(timeout=15), ready.get(timeout=15)},
                    {"process-A", "process-B"},
                )
                start.set()
                for process in processes:
                    process.join(timeout=15)
                    self.assertFalse(process.is_alive(), "writer process did not exit")
                    self.assertEqual(process.exitcode, 0)
                result_by_writer = dict(outcomes.get(timeout=5) for _ in processes)
            finally:
                start.set()
                for process in processes:
                    if process.is_alive():
                        process.terminate()
                    process.join(timeout=5)
                for channel in (ready, outcomes):
                    channel.close()
                    channel.join_thread()

            self.assertEqual(
                sorted(result_by_writer.values()),
                ["committed", "conflict"],
            )
            final_plan = json.loads((root / "current_plan.json").read_text())
            committed_writer = next(
                name for name, outcome in result_by_writer.items() if outcome == "committed"
            )
            self.assertEqual(final_plan, {"plan_revision": 2, "winner": committed_writer})
            self.assertFalse((root / st.TRANSACTION_DIRNAME).exists())

    def test_process_crash_recovers_goal_focus_finalization_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_json(
                root / "current_plan.json",
                {
                    "plan_revision": 4,
                    "goal_revision": 2,
                    "registry_revision": 2,
                    "state": "active",
                },
            )
            _write_json(
                root / "goal_contract.json",
                {
                    "goal_revision": 2,
                    "obligations": [{"id": "obl-1", "status": "open"}],
                },
            )
            _write_json(
                root / "approach_registry.json",
                {"registry_revision": 2, "selected": "campaign-a"},
            )
            _write_json(
                root / "loop_state.json",
                {"revision": 7, "last_iteration": 1, "status": "running"},
            )
            _write_json(
                root / "budget.json",
                {"revision": 3, "spent_iterations": 1, "spent_tokens": 500},
            )
            _write_json(
                root / "iteration_candidate.json",
                {"candidate_id": "candidate-2", "status": "pending_review"},
            )
            _write_jsonl(
                root / "iterations.jsonl",
                [{"event_id": "iteration-1", "iteration": 1, "bank_status": "accepted"}],
            )
            _write_jsonl(
                root / "direction_decisions.jsonl",
                [{"event_id": "decision-1", "decision_type": "direction_commit"}],
            )
            expected_json = _goal_focus_finalization_kwargs(root)["json_files"]

            context = multiprocessing.get_context("spawn")
            process = context.Process(
                target=_crash_during_goal_focus_finalization,
                args=(str(root),),
            )
            process.start()
            process.join(timeout=20)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
                self.fail("crashing finalizer process did not exit")
            self.assertEqual(process.exitcode, 73)
            self.assertTrue((root / st.TRANSACTION_DIRNAME).exists())
            self.assertTrue(
                (root / ".goal_focus" / "candidates" / "candidate-2.json").exists()
            )
            self.assertTrue((root / "iteration_candidate.json").exists())
            self.assertEqual(
                json.loads((root / "goal_contract.json").read_text())["goal_revision"],
                3,
            )
            self.assertEqual(
                json.loads((root / "loop_state.json").read_text())["last_iteration"],
                1,
            )
            self.assertEqual(
                len((root / "iterations.jsonl").read_text().splitlines()),
                1,
            )

            recovered = st.recover_transactions(root)
            self.assertEqual(
                recovered,
                [
                    {
                        "transaction_id": "finalize-candidate-2",
                        "previous_phase": "applying",
                        "status": "recovered",
                    }
                ],
            )
            self.assertEqual(st.recover_transactions(root), [])

            for name, expected in expected_json.items():
                with self.subTest(name=name):
                    self.assertEqual(json.loads((root / name).read_text()), expected)
            iterations = [
                json.loads(line) for line in (root / "iterations.jsonl").read_text().splitlines()
            ]
            decisions = [
                json.loads(line)
                for line in (root / "direction_decisions.jsonl").read_text().splitlines()
            ]
            self.assertEqual([row["event_id"] for row in iterations], ["iteration-1", "iteration-candidate-2"])
            self.assertEqual([row["event_id"] for row in decisions], ["decision-1", "decision-candidate-2"])
            self.assertFalse((root / "iteration_candidate.json").exists())
            self.assertFalse((root / st.TRANSACTION_DIRNAME).exists())

    def test_target_cannot_escape_loop_directory(self) -> None:
        for unsafe in (
            "../escape.txt",
            r"\escape.txt",
            r"C:escape.txt",
            r"C:\escape.txt",
            r"\\server\share\escape.txt",
        ):
            with self.subTest(path=unsafe), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                with self.assertRaises(st.TransactionError):
                    st.commit_transaction(root, text_files={unsafe: "bad"})

    def test_transaction_id_cannot_escape_or_nest_the_journal(self) -> None:
        for unsafe in (
            "../escaped-transaction",
            "nested/transaction",
            r"\escaped-transaction",
            r"C:escaped-transaction",
            "x" * 129,
        ):
            with self.subTest(transaction_id=unsafe), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                root = base / "loop"
                with self.assertRaises(st.TransactionError):
                    st.commit_transaction(
                        root,
                        text_files={"inside.txt": "must not write"},
                        transaction_id=unsafe,
                    )
                self.assertFalse((root / "inside.txt").exists())
                self.assertFalse((base / "escaped-transaction").exists())

    def test_recovery_rejects_absolute_or_nested_postimage_blob_paths(self) -> None:
        for blob_value in ("ABSOLUTE", "../outside.bin", "nested/blob.bin"):
            with self.subTest(blob=blob_value), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                root = base / "loop"
                root.mkdir()
                outside = base / "outside.bin"
                payload = b"attacker-selected postimage\n"
                outside.write_bytes(payload)
                tx_dir = root / st.TRANSACTION_DIRNAME / "forged"
                (tx_dir / "postimages").mkdir(parents=True)
                if os.name == "posix":
                    (root / st.TRANSACTION_DIRNAME).chmod(0o700)
                    tx_dir.chmod(0o700)
                    (tx_dir / "postimages").chmod(0o700)
                selected_blob = str(outside) if blob_value == "ABSOLUTE" else blob_value
                _write_json(
                    tx_dir / "manifest.json",
                    {
                        "schema_version": "goal_focus_transaction.v1",
                        "transaction_id": "forged",
                        "phase": "prepared",
                        "targets": [
                            {
                                "path": "victim.txt",
                                "blob": selected_blob,
                                "sha256": hashlib.sha256(payload).hexdigest(),
                                "delete": False,
                            }
                        ],
                    },
                )
                if os.name == "posix":
                    (tx_dir / "manifest.json").chmod(0o600)

                with self.assertRaisesRegex(
                    st.TransactionError,
                    "safe relative path|single safe filename|one safe bounded filename",
                ):
                    st.recover_transactions(root)

                self.assertFalse((root / "victim.txt").exists())
                self.assertEqual(outside.read_bytes(), payload)

    @unittest.skipUnless(os.name == "posix", "requires POSIX symlink semantics")
    def test_symlinked_target_parent_cannot_escape_loop_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "loop"
            outside = base / "outside"
            root.mkdir()
            outside.mkdir()
            (root / "nested").symlink_to(outside, target_is_directory=True)

            with self.assertRaises((st.TransactionError, OSError)):
                st.commit_transaction(
                    root,
                    text_files={"nested/escaped.bin": "must not escape"},
                )
            self.assertFalse((outside / "escaped.bin").exists())

            victim = outside / "victim.bin"
            victim.write_text("keep", encoding="utf-8")
            with self.assertRaises((st.TransactionError, OSError)):
                st.commit_transaction(root, deletes=["nested/victim.bin"])
            self.assertEqual(victim.read_text(encoding="utf-8"), "keep")


class DescriptorFreeDirectoryChainTests(unittest.TestCase):
    """Windows validates directory chains instead of opening descriptors.

    ``os.open`` refuses a directory on Windows, so the chain walk used there is
    descriptor-free and is exercised directly here. Pinning ``os.name`` is not an
    option: ``pathlib`` dispatches on it and cannot build a ``WindowsPath`` on a
    POSIX host.
    """

    def test_chain_creates_components_and_rejects_a_symlinked_component(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            st._ensure_directory_chain_by_lstat(root / "journal" / "post", create=True)
            self.assertTrue((root / "journal" / "post").is_dir())
            self.assertEqual(
                stat.S_IMODE((root / "journal" / "post").stat().st_mode), 0o700
            )

            link = root / "link"
            link.symlink_to(root / "journal")
            with self.assertRaises(st.TransactionError):
                st._ensure_directory_chain_by_lstat(link / "post")

    def test_missing_component_without_create_still_raises_file_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                st._ensure_directory_chain_by_lstat(Path(tmp) / "absent")

    def test_a_file_in_the_chain_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            regular = Path(tmp) / "regular"
            regular.write_text("payload", encoding="utf-8")
            with self.assertRaises(st.TransactionError):
                st._ensure_directory_chain_by_lstat(regular / "post", create=True)


if __name__ == "__main__":
    unittest.main()
