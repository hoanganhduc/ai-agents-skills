# Failure Recovery Discipline

What to do when a check, test, build, or claim fails — for any task. A failure is
information, not a wall; recover deliberately instead of papering over it.

## The loop

1. **Stop and read the actual failure.** Quote the real error or the specific check
   that failed. Do not retry blindly or assume the cause.
2. **Isolate.** Narrow to the smallest reproducing case or the single failing
   component, and confirm what changed since it last passed.
3. **Hypothesize, one cause at a time.** State the most likely cause and what would
   confirm it before changing anything.
4. **Apply the minimal fix.** Change only what the hypothesis requires; do not bundle
   unrelated edits.
5. **Re-verify against the original check.** The same check that failed must now
   pass. If it does not, the hypothesis was wrong — return to step 3; do not stack
   another guess on top.

## When a claim fails verification

When a verification step contradicts a claim — `research-verification-gate` returns
NOT READY, a source cross-check fails, a test refutes a "fixed" behavior — downgrade
the claim immediately and disclose it. Do not silently soften a blocker into a
caveat or present an unverified claim as established.

## Guardrails

- never retry the same action expecting a different result without a new hypothesis
- one hypothesis and one minimal change per cycle
- a failure you cannot explain is not resolved by a workaround that hides it
