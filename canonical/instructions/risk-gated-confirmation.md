# Risk-Gated Confirmation

Use this gate before executing work whose side effects, size, or ambiguity make
silent continuation unsafe. This is the explicit approval layer that sits after
planning and before action.

## Classification

Classify each request before substantive action:

- `trivial`: direct answer or one clearly reversible local step.
- `normal`: bounded work with clear intent and low side effects.
- `risk-gated`: work that meets any threshold below.

When a task is `risk-gated`, showing a plan is not approval. Pause and wait for
explicit approval before executing.

## Risk-Gated Thresholds

Require explicit approval when any condition applies:

- edits 3 or more files, or the expected diff is over 100 changed lines
- deletes, renames, moves, rewrites, or broadly reformats files
- changes public APIs, schemas, prompts, root instructions, installer behavior,
  CI, dependencies, permissions, auth, secrets handling, runtime paths, or
  provider configuration
- writes outside the current repo/workspace or to shared user-global locations
- sends, publishes, posts, uploads, emails, messages, shares files, or triggers
  external notifications
- mutates Zotero, Calibre, OpenClaw, cloud storage, package managers, remote
  services, or other shared state
- retrieves paywalled or external documents outside the local-library-first
  path
- starts/stops services, runs background jobs, or invokes external compute
- changes behavior where rollback or containment is unclear
- the user's intent, target, success criterion, or acceptable side effect is
  unclear

## Approval Contract

For risk-gated work, show:

- `Why gated`: threshold(s) that apply
- `Planned scope`: files, directories, commands, targets, or services
- `Expected effect`: behavior or state that will change
- `Out of scope`: related work that will not be done
- `Verification`: checks to run before completion
- `Rollback/containment`: how unintended effects are limited

Ask for explicit confirmation using exact approval wording:

```text
Reply exactly `PROCEED <short-scope>` to approve, or revise the scope.
```

Non-answers, vague approvals, silence, and unrelated messages are not approval.

## Skipping The Gate

If you skip this gate, state why the task is not risk-gated. For example:

- only one local read-only command
- one-line docs typo with no behavior change
- user explicitly requested analysis only

Do not skip this gate merely because the work is routine, because the user is in
a hurry, or because a previous unrelated task was approved.
