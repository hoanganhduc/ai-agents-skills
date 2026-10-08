# Verified artifact publication

A research cycle completed its workers, host checks and independent review,
then failed before ledger append because a 7,359,321-byte JSON artifact exceeded
the custom driver's 2 MiB publication limit. Two retries reused the same
completed results and hit the same limit. Changing or splitting the checked
file would invalidate its evidence hashes and references.

## Scope and decision

The canonical `publication_scan` helper retains the 2 MiB default for ordinary
text and permits up to 16 MiB when a caller supplies a verified SHA-256 digest.
The entire file remains subject to UTF-8, NUL/binary, credential and exact
secret-value checks. The helper uses bounded reads and rejects changed files.
The existing 16,000,000-byte JSONL ledger rule remains separate.

The caller owns evidence admission. In the research driver, only private host
receipts can supply the larger-file digest. Each scan revalidates the route's
full artifact manifest, check-source hashes and evidence hashes. The larger-file
allowance additionally requires a nonempty passed-check record. Matching small
artifacts from abandoned failed attempts retain the ordinary limit; those
receipts do not authorize larger files. Public worker reports cannot authorize
larger files. Publication manifest hashing is also bounded by the hard cap.
The same admission runs before ledger append and before publication, including
publication recovery. A modified file remains rejected even if it becomes
smaller than the ordinary limit.

This is byte-level publication admission, not proof verification. Mathematical
acceptance, model assignments, budgets and credential isolation stay separate.
The change does not modify already checked scientific artifacts or receipts.

## Tasks and acceptance

- [x] Add the bounded canonical scanner and positive/negative regressions.
- [x] Integrate private-receipt admission in both publication paths.
- [x] Observe the oversized-evidence regression fail before the change.
- [x] Check forged receipts, mutations, secrets, binary data and the hard cap.
- [x] Verify local Git publication and recovery without new model calls.
- [x] Review the trust boundary and the exact installer mutation sets.
- [x] Propagate through both managed runtime roots and push source changes.
- [x] Bank and push the completed cycle without changing its evidence.
- [x] Resume the next research iteration and verify notification delivery.

Linux is the local execution platform. Other operating systems require their
own execution evidence. Existing unrelated checkout changes must be preserved;
the source change is developed in an isolated worktree and propagated through
reviewed installer plans.
