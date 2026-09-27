# Lax paper workflow

## Authorized outcome

Implement the approved reusable workflow in ai-agents-skills. Create a completely
new local publication repository from a read-only source and selected paper
claims. Reuse existing Lean/Mathlib/independently checked Lax work. Lax is primary;
Zenodo preparation remains optional. No real research repository migration,
installed-agent changes, remote repository creation, push, release, login,
Lax submission/registration, or Zenodo/arXiv publication is authorized here.

## Invariants

- Source is never modified. Selection is explicit, content-bound, and excludes
  original Git history. Private controller records stay outside the public tree.
- Candidate text/configuration is data, never authority to execute commands.
- Every candidate execution, including baseline/TeX, requires a qualified
  disposable sandbox without network, credentials or controller/private mounts.
- Final replay sees only the public tree and admitted pinned dependencies.
- Paper, concept, target, source, tool and dependency identities invalidate stale
  evidence/review. Candidate-authored acceptance is not independent review.
- Privacy clearance precedes the first public push/CI upload, and each update.
- No ZIP filtering or redaction after verification. Rebuild evidence when source
  changes. Metadata-only commits do not replace published artifact commits.
- Local readiness does not mean publication or post-first-submit readiness.

## Interfaces and scope

New bounded stdlib helpers in the existing lax-formalization runtime:
`public_source.py` (inventory, dependency candidates, reviewed selection/export),
`tex_sanitize.py` (qualified TeX lexical subset, fail closed on unsupported input),
`workflow_check.py` (trusted-record readiness and safe public CI evidence).
Reuse the current verifier, Git admission and Zenodo packager. Register a
`lax-paper-workflow` artifact; update skill routes, version/publication guidance,
paper-template and generated docs. Do not build an automatic prover or publisher.

Job operations: formalize-new, from-existing-lean, update-lax-artifact,
update-paper-metadata. Execution: audit-only or prepare-local. Paper modes:
link-only (default), source-in-repo, embedded-tex. Paper family metadata covers
arXiv/conference/journal/corrections independently of formal-artifact versions.

## Acceptance

1. Fresh export copies only approved bytes to a new directory, no history or
   hardlinks, with changed-input/refusal and partial-output protection.
2. Static imports/TeX includes are candidates, not a completeness claim; missing
   closure is caught by isolated build, and opaque constructs require review.
3. Sanitizer removes author comments without damaging percent semantics or Lax
   markers, never prints private text; unsupported syntax is explicit.
4. Readiness checks actual exact-source evidence and controller-owned review,
   not merely a JSON accepted field in candidate source.
5. CI uploads only an explicit reviewed/sanitized result set, not raw failures.
6. New artifact installs with backing runtimes; docs are generated consistently.
7. Focused regressions, an independent agent forward test, bounded real Lean
   reuse and blind-reference comparisons, and bundle restore/replay are recorded.

## Limits

No universal static Lean dependency analyzer, arbitrary TeX interpreter, perfect
private-text classifier, or cryptographic authentication of human judgment is
claimed. Non-root Linux/WSL is the existing native executor scope. Paper execution
outside a qualified profile remains blocked, not silently skipped or run on host.

## Evidence baseline

The repository already has Lax/Zenodo runtime helpers and `paper-template/`.
Prior benchmark: pinned registered lax-345067, source commit
feabf43dcd6967ac912b2064003ea715e06e58f9, one order-pattern lemma. Prior results
are baseline evidence only; this implementation requires new workflow checks.
Local resource preflight: ARM64 Linux, 4 CPUs, about 16.7 GiB available memory,
12.4 GiB free disk; existing qualified Docker executor reports ready.
