"""Crash recovery and concurrency tests for Goal-Focus state transactions."""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import shutil
import stat
import sys
import tempfile
import threading
import traceback
import unittest
from pathlib import Path
from unittest import mock

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


class _VanishingLockDirectory:
    """Remove the run directory just before the lock file is opened.

    ``O_CREAT`` recreates a removed lock file, so a missing parent directory is
    the one cause of ``ENOENT`` a test can stage without patching the kernel.
    Dropping the directory under the already-open directory descriptor
    reproduces that race with a real kernel error instead of a synthetic
    exception.
    """

    def __init__(self, run_dir: Path, *, every_attempt: bool = False) -> None:
        self.run_dir = run_dir
        self.every_attempt = every_attempt
        self.attempts = 0
        self._real_open = os.open

    def __call__(self, path: object, *args: object, **kwargs: object) -> int:
        if str(path).endswith(st.LOCK_FILENAME):
            self.attempts += 1
            if self.every_attempt or self.attempts == 1:
                os.rmdir(self.run_dir)
        return self._real_open(path, *args, **kwargs)  # type: ignore[arg-type]


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
            self.assertEqual(json.loads((root / "current_plan.json").read_text(encoding="utf-8"))["value"], "old")

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
                self.assertEqual(json.loads((root / "current_plan.json").read_text(encoding="utf-8"))["value"], "new")
                self.assertEqual(json.loads((root / "goal_contract.json").read_text(encoding="utf-8"))["value"], "new")
                self.assertFalse((root / st.TRANSACTION_DIRNAME).exists())

    def test_unrecoverable_journal_is_quarantined_not_wedged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_json(root / "current_plan.json", {"plan_revision": 1, "value": "old"})
            with self.assertRaises(st.InjectedCrash):
                st.commit_transaction(
                    root,
                    json_files={"current_plan.json": {"plan_revision": 2, "value": "new"}},
                    expected_revisions={"current_plan.json": ("plan_revision", 1)},
                    crash_after="committed",
                )
            # An out-of-band write after the commit point breaks the digest
            # proof the recovery pass validates, which is exactly the signal
            # that must be preserved rather than retried forever.
            _write_json(root / "current_plan.json", {"plan_revision": 2, "value": "tampered"})

            with self.assertRaises(st.TransactionQuarantined) as ctx:
                st.recover_transactions(root)
            self.assertIn(st.TRANSACTION_QUARANTINE_DIRNAME, str(ctx.exception))
            self.assertFalse((root / st.TRANSACTION_DIRNAME).exists())

            quarantine = root / st.TRANSACTION_QUARANTINE_DIRNAME
            manifests = sorted(quarantine.glob("*/manifest.json"))
            self.assertEqual(len(manifests), 1, manifests)
            self.assertIn("targets", json.loads(manifests[0].read_text(encoding="utf-8")))

            # The loop is not wedged: recovery is clean and commits resume.
            self.assertEqual(st.recover_transactions(root), [])
            st.commit_transaction(
                root,
                json_files={"current_plan.json": {"plan_revision": 3, "value": "next"}},
                expected_revisions={"current_plan.json": ("plan_revision", 2)},
            )
            plan = json.loads((root / "current_plan.json").read_text(encoding="utf-8"))
            self.assertEqual(plan["value"], "next")

    def test_oversized_postimage_fails_without_wedging_the_journal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # The cap is patched rather than met with a real 64 MB payload: the
            # check reads the module constant, so the boundary is the same one.
            with mock.patch.object(st, "MAX_TRANSACTION_BYTES", 1024):
                with self.assertRaises(st.TransactionError) as ctx:
                    st.commit_transaction(
                        root, text_files={"iterations.jsonl": "x" * 2048}
                    )
            self.assertIn("exceeds", str(ctx.exception))
            self.assertNotIsInstance(ctx.exception, st.TransactionQuarantined)
            self.assertFalse((root / st.TRANSACTION_DIRNAME).exists())
            self.assertFalse((root / "iterations.jsonl").exists())

            st.commit_transaction(root, json_files={"small.json": {"ok": True}})
            self.assertTrue(
                json.loads((root / "small.json").read_text(encoding="utf-8"))["ok"]
            )

    def test_a_ledger_past_the_read_cap_fails_before_the_journal_is_armed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            row = json.dumps({"event_id": "seed", "pad": "x" * 4096}, sort_keys=True)
            count = (st.MAX_TRANSACTION_BYTES // (len(row) + 1)) + 2
            (root / "events.jsonl").write_text(
                "".join(f"{row}\n" for _ in range(count)), encoding="utf-8"
            )

            with self.assertRaises(st.TransactionError):
                st.commit_transaction(
                    root, jsonl_appends={"events.jsonl": [{"event_id": "evt-1"}]}
                )
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
            rows = [json.loads(line) for line in (root / "events.jsonl").read_text(encoding="utf-8").splitlines()]
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
            self.assertTrue(json.loads((root / "done.json").read_text(encoding="utf-8"))["done"])

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
            names = ("A", "B")
            barrier = threading.Barrier(len(names), timeout=30)

            def writer(name: str) -> None:
                try:
                    barrier.wait()
                    st.commit_transaction(
                        root,
                        json_files={"current_plan.json": {"plan_revision": 2, "winner": name}},
                        expected_revisions={"current_plan.json": ("plan_revision", 1)},
                    )
                    outcome = "committed"
                except st.RevisionConflict:
                    outcome = "conflict"
                except BaseException as exc:  # pragma: no cover - reported, not swallowed
                    # The stack, not just the type: an unexpected outcome here is
                    # a race, so the one run that reproduces it has to carry
                    # enough to locate the call that failed.
                    outcome = f"error:{type(exc).__name__}:{exc}\n{traceback.format_exc()}"
                with outcome_lock:
                    outcomes.append(outcome)

            threads = [threading.Thread(target=writer, args=(name,)) for name in names]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(sorted(outcomes), ["committed", "conflict"])
            self.assertIn(json.loads((root / "current_plan.json").read_text(encoding="utf-8"))["winner"], {"A", "B"})

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
            final_plan = json.loads((root / "current_plan.json").read_text(encoding="utf-8"))
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
                json.loads((root / "goal_contract.json").read_text(encoding="utf-8"))["goal_revision"],
                3,
            )
            self.assertEqual(
                json.loads((root / "loop_state.json").read_text(encoding="utf-8"))["last_iteration"],
                1,
            )
            self.assertEqual(
                len((root / "iterations.jsonl").read_text(encoding="utf-8").splitlines()),
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
                    self.assertEqual(json.loads((root / name).read_text(encoding="utf-8")), expected)
            iterations = [
                json.loads(line) for line in (root / "iterations.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            decisions = [
                json.loads(line)
                for line in (root / "direction_decisions.jsonl").read_text(encoding="utf-8").splitlines()
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


class LoopLockVanishedPathTests(unittest.TestCase):
    """A writer must not leak ``FileNotFoundError`` from lock acquisition.

    Concurrent writers observed this on macOS CI: the loser of a race saw
    ``ENOENT`` for ``.goal_focus.lock`` instead of the revision conflict the
    state machine promises. The race is reproduced here deterministically by
    dropping the run directory between the directory walk and the lock open.
    """

    def test_lock_acquisition_retries_when_the_run_directory_vanishes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "loop"
            vanish = _VanishingLockDirectory(root)
            with mock.patch.object(st.os, "open", vanish):
                with st.LoopLock(root, timeout_seconds=5):
                    pass
            self.assertEqual(vanish.attempts, 2)
            self.assertTrue((root / st.LOCK_FILENAME).is_file())

    def test_lock_acquisition_times_out_when_the_directory_keeps_vanishing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "loop"
            vanish = _VanishingLockDirectory(root, every_attempt=True)
            with mock.patch.object(st.os, "open", vanish):
                with self.assertRaises(st.LockTimeout) as caught:
                    with st.LoopLock(root, timeout_seconds=0.05):
                        pass
            self.assertIsInstance(caught.exception.__cause__, FileNotFoundError)
            self.assertGreaterEqual(vanish.attempts, 1)

    def test_lock_acquisition_does_not_retry_other_open_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "loop"
            attempts: list[str] = []
            real_open = os.open

            def refuse(path: object, *args: object, **kwargs: object) -> int:
                if str(path).endswith(st.LOCK_FILENAME):
                    attempts.append(str(path))
                    raise PermissionError("loop lock open refused")
                return real_open(path, *args, **kwargs)  # type: ignore[arg-type]

            with mock.patch.object(st.os, "open", refuse):
                with self.assertRaises(PermissionError):
                    with st.LoopLock(root, timeout_seconds=5):
                        pass
            self.assertEqual(len(attempts), 1)


class DescriptorFreeDirectoryChainTests(unittest.TestCase):
    """Windows validates directory chains instead of opening descriptors.

    ``os.open`` refuses a directory on Windows, so the chain walk used there is
    descriptor-free and is exercised directly here. Pinning ``os.name`` is not an
    option: ``pathlib`` dispatches on it and cannot build a ``WindowsPath`` on a
    POSIX host.
    """

    def test_chain_creates_every_missing_component(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            st._ensure_directory_chain_by_lstat(root / "journal" / "post", create=True)
            self.assertTrue((root / "journal" / "post").is_dir())

    @unittest.skipUnless(os.name == "posix", "requires POSIX directory modes")
    def test_created_components_are_private(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "journal" / "post"
            st._ensure_directory_chain_by_lstat(target, create=True)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)

    @unittest.skipUnless(os.name == "posix", "requires POSIX symlink semantics")
    def test_a_symlinked_component_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            st._ensure_directory_chain_by_lstat(root / "journal" / "post", create=True)
            link = root / "link"
            link.symlink_to(root / "journal")
            with self.assertRaisesRegex(
                st.TransactionError, "not a real directory"
            ):
                st._ensure_directory_chain_by_lstat(link / "post")

    def test_missing_component_without_create_still_raises_file_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                st._ensure_directory_chain_by_lstat(Path(tmp) / "absent")

    def test_a_component_another_writer_created_first_is_tolerated(self) -> None:
        """The chain is walked before the loop lock exists, so it must lose races.

        The lock file lives inside the chain, so two writers starting in one
        fresh run directory both walk it unserialised and one of them is told
        the component already exists. ``_open_directory_nofollow`` tolerates
        exactly this on POSIX.
        """

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real_mkdir = os.mkdir

            def losing_mkdir(path, mode=0o777, **kwargs):
                real_mkdir(path, mode, **kwargs)
                raise FileExistsError(17, "File exists")

            with mock.patch.object(st.os, "mkdir", losing_mkdir):
                st._ensure_directory_chain_by_lstat(root / "run", create=True)
            self.assertTrue((root / "run").is_dir())

    def test_a_file_in_the_chain_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            regular = Path(tmp) / "regular"
            regular.write_text("payload", encoding="utf-8")
            with self.assertRaises(st.TransactionError):
                st._ensure_directory_chain_by_lstat(regular / "post", create=True)


def _denies_once(real, calls):
    """Wrap ``real`` so its first call fails the way Windows reports contention."""

    def wrapper(*args, **kwargs):
        calls.append(args)
        if len(calls) == 1:
            raise PermissionError(13, "Permission denied")
        return real(*args, **kwargs)

    return wrapper


class WindowsSharingRetryTests(unittest.TestCase):
    """Windows reports a held handle as a permission failure, not as contention.

    ``ERROR_ACCESS_DENIED`` is what the platform returns both for a real
    access-control denial and for a file some other handle still holds, and the
    scanner that opens every freshly renamed file supplies such a handle without
    a second writer of ours. These drive the Windows fallbacks on the host
    platform through :func:`state_transaction._is_windows`.
    """

    def test_a_transient_denial_while_replacing_does_not_fail_the_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            st, "_is_windows", lambda: True
        ):
            root = Path(tmp)
            _write_json(root / "current_plan.json", {"plan_revision": 1, "winner": "none"})
            calls: list[tuple] = []
            with mock.patch.object(st.os, "replace", _denies_once(os.replace, calls)):
                result = st.commit_transaction(
                    root,
                    json_files={"current_plan.json": {"plan_revision": 2, "winner": "A"}},
                    expected_revisions={"current_plan.json": ("plan_revision", 1)},
                )
            self.assertEqual(result["status"], "committed")
            self.assertEqual(
                json.loads((root / "current_plan.json").read_text(encoding="utf-8")),
                {"plan_revision": 2, "winner": "A"},
            )

    def test_a_transient_denial_while_removing_the_journal_does_not_fail_the_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            st, "_is_windows", lambda: True
        ):
            root = Path(tmp)
            _write_json(root / "current_plan.json", {"plan_revision": 1})
            calls: list[tuple] = []
            with mock.patch.object(st.shutil, "rmtree", _denies_once(shutil.rmtree, calls)):
                result = st.commit_transaction(
                    root,
                    json_files={"current_plan.json": {"plan_revision": 2}},
                    expected_revisions={"current_plan.json": ("plan_revision", 1)},
                )
            self.assertEqual(result["status"], "committed")
            self.assertFalse((root / st.TRANSACTION_DIRNAME).exists())

    def test_a_transient_denial_is_survived_by_the_wrapped_write_steps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            st, "_is_windows", lambda: True
        ):
            target = Path(tmp) / "journal" / "payload.bin"
            calls: list[tuple] = []
            with mock.patch.object(st.os, "replace", _denies_once(os.replace, calls)):
                st._atomic_write_bytes(target, b"payload")
            self.assertEqual(target.read_bytes(), b"payload")

            calls = []
            with mock.patch.object(st.os, "lstat", _denies_once(os.lstat, calls)):
                self.assertIsNotNone(st._lstat_nofollow(target))

            calls = []
            with mock.patch.object(Path, "read_bytes", _denies_once(Path.read_bytes, calls)):
                self.assertEqual(st._read_bytes_nofollow(target), b"payload")

            calls = []
            with mock.patch.object(Path, "unlink", _denies_once(Path.unlink, calls)):
                st._unlink_nofollow(target)
            self.assertFalse(target.exists())

    def test_a_denial_that_never_clears_is_still_raised(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            st, "_is_windows", lambda: True
        ), mock.patch.object(st, "WINDOWS_SHARING_RETRY_SECONDS", 0.05):
            def always_denies(*args, **kwargs):
                raise PermissionError(13, "Permission denied")

            with mock.patch.object(st.os, "replace", always_denies):
                with self.assertRaises(PermissionError):
                    st._atomic_write_bytes(Path(tmp) / "payload.bin", b"payload")

    def test_a_transient_windows_denial_while_opening_the_lock_is_retried(self) -> None:
        """Opening the lock has the same ambiguous denial as later file steps."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "loop"
            attempts: list[str] = []
            real_open = os.open

            def deny_once(path: object, *args: object, **kwargs: object) -> int:
                if str(path).endswith(st.LOCK_FILENAME):
                    attempts.append(str(path))
                    if len(attempts) == 1:
                        raise PermissionError("transient loop lock sharing denial")
                return real_open(path, *args, **kwargs)  # type: ignore[arg-type]

            with mock.patch.object(st, "_is_windows", lambda: True), mock.patch.object(
                st.os, "open", deny_once
            ):
                with st.LoopLock(root, timeout_seconds=5):
                    pass
            self.assertEqual(len(attempts), 2)

    def test_a_transient_windows_raw_bootstrap_denial_closes_and_retries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "loop"
            writes: list[int] = []
            closes: list[int] = []
            real_write = os.write
            real_close = os.close

            def deny_write_once(file_fd: int, payload: bytes) -> int:
                writes.append(file_fd)
                if len(writes) == 1:
                    raise PermissionError("transient raw bootstrap sharing denial")
                return real_write(file_fd, payload)

            def close_then_complain_once(file_fd: int) -> None:
                closes.append(file_fd)
                real_close(file_fd)
                if len(closes) == 1:
                    raise OSError("cleanup error must not mask bootstrap denial")

            with mock.patch.object(st, "_is_windows", lambda: True), mock.patch.object(
                st.os, "write", side_effect=deny_write_once
            ), mock.patch.object(
                st.os, "close", side_effect=close_then_complain_once
            ):
                with st.LoopLock(root, timeout_seconds=5):
                    pass
            self.assertEqual(len(writes), 2)
            self.assertEqual(len(closes), 1)
            self.assertEqual(closes[0], writes[0])

    def test_a_persistent_windows_lock_open_denial_stops_at_the_deadline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "loop"
            attempts: list[str] = []
            clock = [0.0]
            real_open = os.open

            def deny(path: object, *args: object, **kwargs: object) -> int:
                if str(path).endswith(st.LOCK_FILENAME):
                    attempts.append(str(path))
                    raise PermissionError("persistent loop lock denial")
                return real_open(path, *args, **kwargs)  # type: ignore[arg-type]

            def advance(seconds: float) -> None:
                clock[0] += seconds

            with mock.patch.object(st, "_is_windows", lambda: True), mock.patch.object(
                st.os, "open", deny
            ), mock.patch.object(
                st.time, "monotonic", side_effect=lambda: clock[0]
            ), mock.patch.object(st.time, "sleep", side_effect=advance):
                with self.assertRaises(st.LockTimeout) as raised:
                    with st.LoopLock(root, timeout_seconds=0.05):
                        pass
            self.assertIsInstance(raised.exception.__cause__, PermissionError)
            self.assertEqual(len(attempts), 3)

    def test_posix_lock_open_denial_is_not_retried(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "loop"
            attempts: list[str] = []
            real_open = os.open

            def deny(path: object, *args: object, **kwargs: object) -> int:
                if str(path).endswith(st.LOCK_FILENAME):
                    attempts.append(str(path))
                    raise PermissionError("loop lock open refused")
                return real_open(path, *args, **kwargs)  # type: ignore[arg-type]

            with mock.patch.object(st, "_is_windows", lambda: False), mock.patch.object(
                st.os, "open", deny
            ):
                with self.assertRaises(PermissionError):
                    with st.LoopLock(root, timeout_seconds=5):
                        pass
            self.assertEqual(len(attempts), 1)

    def test_posix_does_not_retry_a_denial(self) -> None:
        """On POSIX ``EACCES`` is a decision, so retrying it would only stall."""

        calls: list[tuple] = []

        def denies(*args, **kwargs):
            calls.append(args)
            raise PermissionError(13, "Permission denied")

        with mock.patch.object(st, "_is_windows", lambda: False):
            with self.assertRaises(PermissionError):
                st._tolerate_windows_sharing(denies)
        self.assertEqual(len(calls), 1)



if __name__ == "__main__":
    unittest.main()
