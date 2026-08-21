"""Group integrity and journal-clearing behaviour for Goal-Focus transactions.

A transaction is a group: the compare-and-swap guards that read the loop state
treat the files it names as changing together, so a pass that replaces some of
them and abandons the rest produces a state no guard can detect. Recovery is
what makes that survivable -- an armed journal is replayed until it finishes --
which puts the whole weight on the journal being clearable. The cases here are
about the two ways that broke: a group whose failure was decidable before the
first live write was started anyway and left half-applied, and several failures
that reached the recovery pass outside the one handler able to move an entry
aside, so they re-armed on every later command with nothing in the kit able to
clear them.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
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

POSIX_ONLY = unittest.skipUnless(
    os.name == "posix", "requires POSIX symlink and ownership semantics"
)


def revision(path: Path) -> int:
    return int(json.loads(path.read_text(encoding="utf-8"))["revision"])


def seeded_run(directory: Path) -> Path:
    """Return a run directory holding two revisioned files at revision 1."""

    directory.mkdir(parents=True, exist_ok=True)
    for name in ("a.json", "b.json"):
        (directory / name).write_text(
            json.dumps({"revision": 1}) + "\n", encoding="utf-8"
        )
    return directory


def arm_journal(run: Path, transaction_id: str, **files: object) -> Path:
    """Leave a prepared journal entry behind, the way a crash does."""

    with unittest.TestCase().assertRaises(st.InjectedCrash):
        st.commit_transaction(
            run,
            json_files=dict(files),
            transaction_id=transaction_id,
            crash_after="prepared",
        )
    return run / st.TRANSACTION_DIRNAME / transaction_id


def damage_post_image(tx_dir: Path, target: str) -> None:
    """Corrupt one post-image blob, leaving the rest of the group intact."""

    manifest = json.loads((tx_dir / "manifest.json").read_text(encoding="utf-8"))
    blob = next(
        entry["blob"] for entry in manifest["targets"] if entry["path"] == target
    )
    (tx_dir / "postimages" / blob).write_bytes(b"bit rot\n")


def quarantined_manifests(run: Path) -> list[Path]:
    root = run / st.TRANSACTION_QUARANTINE_DIRNAME
    return sorted(root.glob("*/manifest.json")) if root.exists() else []


class GroupApplicationTests(unittest.TestCase):
    """Nothing decidable up front may be decided after the first live write."""

    @POSIX_ONLY
    def test_a_group_with_an_unwritable_target_applies_none_of_it(self) -> None:
        """A parent that is not a directory is knowable before writing anything.

        The apply pass walks the targets in sorted order, so a run directory
        containing a symlinked subdirectory used to let ``a.json`` land and then
        raise ``NotADirectoryError`` on the second target: the group was half
        applied, and because the failure was an ``OSError`` rather than a
        ``TransactionError`` it also escaped the handler that moves an entry
        aside, so every later command on that run directory raised the same
        thing forever.
        """

        with tempfile.TemporaryDirectory() as tmp:
            run = seeded_run(Path(tmp) / "loop")
            (run / "real").mkdir()
            (run / "nested").symlink_to(run / "real", target_is_directory=True)

            with self.assertRaises(st.TransactionError) as ctx:
                st.commit_transaction(
                    run,
                    json_files={"a.json": {"revision": 2}},
                    text_files={"nested/escaped.bin": "X"},
                    transaction_id="finalize-cand-1",
                )
            self.assertIn("nested/escaped.bin", str(ctx.exception))

            self.assertEqual(revision(run / "a.json"), 1)
            self.assertFalse((run / "real" / "escaped.bin").exists())
            # Nothing was applied, so nothing has to be recovered: the refused
            # commit leaves the run directory exactly as it found it.
            self.assertFalse((run / st.TRANSACTION_DIRNAME).exists())
            self.assertEqual(st.recover_transactions(run), [])

            # And the run directory still works for targets that are writable.
            result = st.commit_transaction(run, json_files={"b.json": {"revision": 2}})
            self.assertEqual(result["status"], "committed")
            self.assertEqual(revision(run / "b.json"), 2)

    @POSIX_ONLY
    def test_a_damaged_post_image_stops_the_group_before_the_first_write(self) -> None:
        """The digest of every post-image is checkable before any of them lands.

        Checking them one at a time meant a damaged blob was found only after
        the earlier targets of the same group had already been replaced, and the
        entry was then quarantined and removed -- so the files it names were
        left disagreeing with no journal remaining to finish the group.
        """

        with tempfile.TemporaryDirectory() as tmp:
            run = seeded_run(Path(tmp) / "loop")
            tx_dir = arm_journal(
                run,
                "finalize-cand-2",
                **{"a.json": {"revision": 2}, "b.json": {"revision": 2}},
            )
            damage_post_image(tx_dir, "b.json")

            with self.assertRaises(st.TransactionQuarantined):
                st.recover_transactions(run)

            self.assertEqual(revision(run / "a.json"), 1)
            self.assertEqual(revision(run / "b.json"), 1)
            self.assertEqual(len(quarantined_manifests(run)), 1)
            self.assertFalse((run / st.TRANSACTION_DIRNAME).exists())
            self.assertEqual(st.recover_transactions(run), [])

    @POSIX_ONLY
    def test_a_target_that_is_a_directory_stops_the_group_before_the_first_write(
        self,
    ) -> None:
        """``os.replace`` onto a directory fails identically on every retry."""

        with tempfile.TemporaryDirectory() as tmp:
            run = seeded_run(Path(tmp) / "loop")
            (run / "c.json").mkdir()

            with self.assertRaises(st.TransactionError) as ctx:
                st.commit_transaction(
                    run,
                    json_files={"a.json": {"revision": 2}, "c.json": {"revision": 2}},
                )
            self.assertIn("c.json", str(ctx.exception))
            self.assertEqual(revision(run / "a.json"), 1)
            self.assertFalse((run / st.TRANSACTION_DIRNAME).exists())

    def test_a_writable_group_still_applies_in_full(self) -> None:
        """Control: deciding first must not stop a group that is applicable."""

        with tempfile.TemporaryDirectory() as tmp:
            run = seeded_run(Path(tmp) / "loop")
            (run / "gone.json").write_text("{}\n", encoding="utf-8")
            result = st.commit_transaction(
                run,
                json_files={"a.json": {"revision": 2}},
                text_files={"deep/nested/new.txt": "made"},
                deletes=["gone.json"],
                expected_revisions={"a.json": ("revision", 1)},
            )
            self.assertEqual(result["status"], "committed")
            self.assertEqual(revision(run / "a.json"), 2)
            self.assertEqual(
                (run / "deep" / "nested" / "new.txt").read_text(encoding="utf-8"), "made"
            )
            self.assertFalse((run / "gone.json").exists())
            self.assertFalse((run / st.TRANSACTION_DIRNAME).exists())

    def test_deleting_a_target_whose_parent_is_gone_is_still_a_noop(self) -> None:
        """Control: an absent delete target must not read as an unusable one.

        The new pass resolves each target's parent chain, and a delete is the
        one case where a missing parent is the answer rather than a problem.
        """

        with tempfile.TemporaryDirectory() as tmp:
            run = seeded_run(Path(tmp) / "loop")
            result = st.commit_transaction(
                run,
                json_files={"a.json": {"revision": 2}},
                deletes=["never/created/at/all.json"],
            )
            self.assertEqual(result["status"], "committed")
            self.assertEqual(revision(run / "a.json"), 2)
            self.assertFalse((run / "never").exists())


class JournalClearingTests(unittest.TestCase):
    """Every failure the recovery pass meets has to be clearable."""

    @POSIX_ONLY
    def test_an_os_level_apply_failure_is_quarantined_not_re_armed(self) -> None:
        """The handler has to cover failures that are not ``TransactionError``.

        It used to catch that class alone, which left every ``os``-level
        failure -- the ones raised from the descriptor walk under a target --
        outside the only escape hatch in the kit. This drives one directly, so
        the coverage is proven rather than inferred from the cases the validate
        pass now catches earlier.
        """

        with tempfile.TemporaryDirectory() as tmp:
            run = seeded_run(Path(tmp) / "loop")
            tx_dir = arm_journal(run, "finalize-cand-3", **{"a.json": {"revision": 2}})
            self.assertTrue(tx_dir.is_dir())

            real_write = st._atomic_write_bytes

            def refuse_live_targets(path: Path, payload: bytes) -> None:
                if st.TRANSACTION_DIRNAME not in Path(path).parts:
                    raise OSError(5, "Input/output error")
                real_write(path, payload)

            with mock.patch.object(st, "_atomic_write_bytes", refuse_live_targets):
                with self.assertRaises(st.TransactionQuarantined):
                    st.recover_transactions(run)

            self.assertFalse(tx_dir.exists())
            self.assertEqual(len(quarantined_manifests(run)), 1)
            self.assertEqual(st.recover_transactions(run), [])
            self.assertEqual(
                st.commit_transaction(run, json_files={"b.json": {"revision": 2}})[
                    "status"
                ],
                "committed",
            )

    @POSIX_ONLY
    def test_a_stray_file_in_the_journal_root_is_quarantined_not_wedged(self) -> None:
        """Anything the journal root picks up must be clearable too.

        The entry-shape check used to run as a pass over the whole journal
        before the handler was entered, so a stray file beside the entries --
        a ``.DS_Store``, a ``Thumbs.db``, an editor swap file, an NFS
        silly-rename -- raised ahead of the escape hatch and re-armed on every
        later command, taking the recoverable transaction beside it down too.
        """

        with tempfile.TemporaryDirectory() as tmp:
            run = seeded_run(Path(tmp) / "loop")
            arm_journal(run, "finalize-cand-4", **{"a.json": {"revision": 2}})
            (run / st.TRANSACTION_DIRNAME / ".DS_Store").write_bytes(b"\x00\x01")

            with self.assertRaises(st.TransactionQuarantined):
                st.recover_transactions(run)

            # The stray is out of the journal and the armed transaction that
            # was stuck behind it finishes on the very next pass.
            recovered = st.recover_transactions(run)
            self.assertEqual(
                [entry["transaction_id"] for entry in recovered], ["finalize-cand-4"]
            )
            self.assertEqual(revision(run / "a.json"), 2)
            self.assertFalse((run / st.TRANSACTION_DIRNAME).exists())

    @POSIX_ONLY
    def test_repeated_quarantine_of_one_transaction_id_stays_flat(self) -> None:
        """Production ids are deterministic, so the same one comes back.

        ``shutil.move`` onto an existing directory moves the source inside it,
        so a destination named only for the transaction id nested the second
        occurrence at ``<id>/<id>/manifest.json`` and failed the third outright
        with ``shutil.Error`` -- an ``OSError``, and so a wedge. The runbook
        also tells an operator to read the manifest at one fixed depth.
        """

        with tempfile.TemporaryDirectory() as tmp:
            run = seeded_run(Path(tmp) / "loop")
            for _ in range(3):
                tx_dir = arm_journal(
                    run, "finalize-cand-1", **{"a.json": {"revision": 9}}
                )
                damage_post_image(tx_dir, "a.json")
                with self.assertRaises(st.TransactionQuarantined):
                    st.recover_transactions(run)

            manifests = quarantined_manifests(run)
            self.assertEqual(len(manifests), 3, manifests)
            quarantine = run / st.TRANSACTION_QUARANTINE_DIRNAME
            self.assertEqual(sorted(quarantine.glob("*/*/manifest.json")), [])
            for manifest in manifests:
                self.assertTrue(
                    manifest.parent.name.startswith("finalize-cand-1-"), manifest
                )
                self.assertEqual(
                    json.loads(manifest.read_text(encoding="utf-8"))["transaction_id"],
                    "finalize-cand-1",
                )
            self.assertEqual(revision(run / "a.json"), 1)


class WindowsOwnershipGateTests(unittest.TestCase):
    """The provenance gate on journal contents has to fire on Windows too."""

    def _read(self, path: Path) -> bytes:
        return st._read_bytes_nofollow(path, require_current_owner=True)

    def test_a_foreign_owner_sid_is_refused(self) -> None:
        """Windows has no ``st_uid`` and no ``os.geteuid``.

        The guard used to read ``hasattr(os, "geteuid") and <uid comparison>``,
        whose first term is false on the only platform that branch serves, so
        neither the recovery pass reading ``manifest.json`` nor the apply pass
        reading a post-image carried any ownership proof there.
        """

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "post.bin"
            target.write_bytes(b"payload")
            with mock.patch.object(st, "_is_windows", lambda: True), mock.patch.object(
                st.os, "name", "nt"
            ), mock.patch.object(
                st,
                "_windows_owner_identity",
                lambda path: ("S-1-5-21-1-2-3-1001", "S-1-5-21-1-2-3-1002"),
            ):
                with self.assertRaises(st.TransactionError) as ctx:
                    self._read(target)
            self.assertIn("not host-owned", str(ctx.exception))

    def test_the_current_users_own_sid_is_accepted(self) -> None:
        """Control: the gate must not refuse a file this process owns."""

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "post.bin"
            target.write_bytes(b"payload")
            with mock.patch.object(st, "_is_windows", lambda: True), mock.patch.object(
                st.os, "name", "nt"
            ), mock.patch.object(
                st,
                "_windows_owner_identity",
                lambda path: ("S-1-5-21-1-2-3-1001", "S-1-5-21-1-2-3-1001"),
            ):
                self.assertEqual(self._read(target), b"payload")

    @POSIX_ONLY
    def test_a_posix_host_simulating_windows_still_compares_uids(self) -> None:
        """The dispatch is on ``os.name``, not on the simulation seam.

        Tests drive the descriptor-free fallbacks on POSIX by pinning
        :func:`state_transaction._is_windows`, and such a run has uids and no
        SIDs. Asking the real platform which model applies is what keeps those
        runs answering with the check they can actually make, instead of
        skipping the gate or reaching for a Win32 call that is not there.
        """

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "post.bin"
            target.write_bytes(b"payload")
            with mock.patch.object(st, "_is_windows", lambda: True):
                self.assertEqual(self._read(target), b"payload")
                with mock.patch.object(
                    st.os, "geteuid", return_value=os.geteuid() + 1
                ):
                    with self.assertRaises(st.TransactionError):
                        self._read(target)

    @unittest.skipUnless(os.name == "nt", "exercises the real Win32 security API")
    def test_the_win32_query_reports_this_process_as_the_owner(self) -> None:
        """On Windows the ctypes body itself has to return a usable answer."""

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "post.bin"
            target.write_bytes(b"payload")
            file_sid, process_sid = st._windows_owner_identity(target)
            self.assertTrue(file_sid.startswith("S-1-"), file_sid)
            self.assertEqual(file_sid, process_sid)
            self.assertEqual(self._read(target), b"payload")


if __name__ == "__main__":
    unittest.main()
