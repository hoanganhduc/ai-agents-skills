# Engineering Lifecycle

Use this lightweight lifecycle for nontrivial engineering work:

1. Spec: define goal, scope, assumptions, interfaces, and acceptance criteria.
2. Plan: list concrete steps and verification before editing.
3. Tasks: keep a short checklist when work spans multiple files or phases.
4. Implement: make scoped changes that match the existing project.
5. Verify: run the narrowest meaningful checks and report skipped checks. For a
   non-trivial change, run the delivery-verification-gate — prove the change with a
   check seen to fail, confirm no regression, and get a fresh-context review.

Do not use this lifecycle as ceremony for trivial one-line tasks. When a task
is simple, say why a lightweight path is enough and still verify the changed
behavior when possible.
