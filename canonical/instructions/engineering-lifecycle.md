# Engineering Lifecycle

Use this lightweight lifecycle for nontrivial engineering work:

1. Spec: define goal, scope, assumptions, interfaces, and acceptance criteria.
2. Investigate: before planning edits to existing code, config, workflows,
   generated artifacts, manuscripts, or prose, inspect the relevant local
   instructions, target files, adjacent call sites or interfaces, existing tests
   or verification commands, and current behavior where practical. For new prose
   such as a blog post, also inspect prior posts, templates, house style, and
   supplied examples before drafting. If material context remains unchecked,
   say `incomplete analysis` and list what remains unchecked before recommending
   or editing.
3. Plan: list concrete steps, out-of-scope work, change risk, and verification
   before editing.
4. Tasks: keep a short checklist when work spans multiple files or phases.
5. Implement: make scoped changes that match the existing project.
6. Verify: run the narrowest meaningful checks and report skipped checks. For a
   non-trivial change, run the delivery-verification-gate — prove the change with a
   check seen to fail, confirm no regression, and get a fresh-context review.

Do not use this lifecycle as ceremony for trivial one-line tasks. When a task
is simple, say why a lightweight path is enough and still verify the changed
behavior when possible.
