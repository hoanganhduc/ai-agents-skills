# Delivery Verification Gate

Use this as the last gate before claiming a code, config, or automation change
done, fixed, or working — the engineering and general-task analog of
`research-verification-gate`. Run it for any non-trivial change; skip it only for
genuinely trivial, behavior-neutral edits.

## Required checks

1. **Prove it (seen-to-fail).** Name the behavior change in one falsifiable
   sentence — what is true after that was false before. Run a check (a test, a
   command, a reproduction) that would FAIL without the change: see it fail, then
   pass. A check that never failed proves nothing, and "seems right" is not done.
2. **No regression (baseline then delta).** Capture the before-state of the
   observable you are changing, confirm the delta is what you intended, and run the
   narrowest meaningful surrounding checks to confirm nothing adjacent regressed.
3. **Review (delegate, multi-axis).** For a non-trivial change, get a fresh-context
   review rather than self-approving: correctness via `code-reviewer`, boundaries
   and untrusted input via `security-reviewer`, evidence and tests via
   `test-reviewer`. Approve when the change clearly improves overall health, even if
   it is not perfect.

## Output contract

Produce a short visible section titled `Verify Check`:

- `Status` — `READY` or `NOT READY`
- `Proof` — the check that was seen to fail, then pass
- `No regression` — before vs. after, and the adjacent checks run
- `Review` — the verdict, or the persona(s) delegated to
- `Gaps` — anything still blocking, or checks skipped and why

## Guardrails

- never claim done on "seems right" — show the proof
- a check that did not fail before the change is not proof
- do not downgrade a real blocker into a caveat
- match the project's existing tooling and conventions; keep the gate proportionate
