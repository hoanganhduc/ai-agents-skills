# Research job presets

These presets compose existing workflows. They are job briefs, not a new
scheduler or permission to execute commands, spend money, publish, or notify.
The parent records the selected preset and effective policy before dispatch.

## Independent choices

| Choice | Values and default |
|---|---|
| Purpose | `research`, `review`, `lean-formalize`; research/review do not implicitly request formalization |
| Executor | Current host tools or a backend permitted by the task's compute policy |
| Artifact format | Ordinary Lean/Lake by default; Lax only for an explicitly selected Lax artifact job |
| Network | Existing jobs default off; dependency setup may explicitly request Internet within host policy |
| Assurance | The actual required checks and independence; unavailable requirements remain unmet |

The job brief names its owner, input/version hashes, target inventory, template,
executor capabilities, allowed actions, budget, timeout/retry bounds, evidence
outputs and required validators. It records which existing policy supplied each
binding choice. A template, result packet or model response cannot grant authority.

## Research

Use `research-scope-brief`, `deep-research-sources`, `deep-research-analysis`,
`deep-research-report` and the existing evidence/delivery gates. Formalization
candidates may be recorded without launching a formal lane. Completion means
supported scoped claims, inspected sources and explicit unresolved gaps.

## Review

Select the relevant existing AGD template. First-pass reviewers receive the
same frozen source and the context needed to interpret it, without other
reviewers' verdicts. The parent records whether context/filesystem separation
is actually enforced. A validator checks findings against the original source;
each original finding has one disposition and new findings remain candidates
until checked. Disagreements remain evidence questions, not majority votes.

Agent names, sessions, actual model families and quota pools are different
identities. Independent sessions sharing a family can produce separate reviews
when the selected assurance allows it; they cannot pass a different-family gate.
Resuming a reviewer is continuity, not a new independent assessment. Missing
backend identity stays unknown. Existing pre-dispatch, direction and acceptance
gates retain their requirements through fallback and recovery.

## Lean formalization

Use `informal-to-lean-formalization-runbook`, the existing skeleton helper and
`lean-strict-verification-gate`. Search pinned Mathlib first. Lax discovery is
optional; verify any Lax dependency actually reused independently. A normal Lake
project can use `lakefile.lean` and its own reviewed Lean/Mathlib pins. It need not
be a clean public Git repository or use Lax namespaces, annotations or layout.

Keep draft status and open obligations visible. A successful build certifies
only the checked formal scope; use axiom auditing and separately requested
kernel replay for their respective guarantees. Supporting an informal paper
claim also requires a bound correspondence review. Missing tools, interrupted
setup and failed compilation do not establish that the theorem is false.

Local or authorized Kaggle CPU execution uses the same verifier. A Kaggle job
may request Internet to prepare dependencies; pin the setup script and record
the actual toolchain and dependency hashes. The kernel's Internet setting is
not a setup-only sandbox. Remote results require host acquisition of the exact
execution/version and bound formal evidence; a job-written PASS is insufficient.
Do not relabel remote evidence as local or claim Lax/Docker isolation from a
native shell run. Setup, build, audit, replay and correspondence remain separate.

For later Lax delivery, use the existing `lax-paper-workflow` operation
`from-existing-lean`: selected read-only source, reviewed export, adaptation,
independent verification and readiness. A changed definition/environment needs
renewed checks. Existing Lax jobs retain their stronger gates. Lax and Zenodo
publication remain separate operations.

## Shared recovery policy

The existing parent dispatch IDs, CAS/journal, resource runner and budget
reservation mechanisms own recovery. Excluded/empty rosters never regain
defaults. Hard exhaustion has no automatic retry; throttling uses bounded
cooldown. New unattended phases default to at most three attempts unless a
stricter existing cap applies; restart/provider rotation does not reset them.
Deterministic errors need a correction, not an identical retry.

Use the selected workflow's timeout controls and checkpoint conventions; clamp
new dispatches to remaining authorized time, including bounded cleanup. A longer
next invocation needs progress evidence and remaining budget. Silence alone is
not a hang. Verify cleanup before replacing a worker; retain uncertainty when
remote execution may still be active. Reconcile uncertain acceptance before
resubmission and keep unknown spending reserved.

User stop/pause survives restart. Late results may record evidence or reconcile
spending but cannot resume work or commit under a successor attempt. Cancellation
before the host effect-dispatch point prevents the action; cancellation after it
requires reconciliation and cannot promise the remote service did nothing.

Report process, artifact, review, formal and publication outcomes separately.
Raw logs are bounded/private; sanitized projections never carry secret bytes
through encoded fields. Recovery appends evidence without rewriting old failure.
