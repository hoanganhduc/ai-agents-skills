# Task Plan: Consolidated Writing And Review Policy

## Context

The repository already has a mature writing-policy index, claim/revision
templates, review routes, V1 delegation packets, and final gates. The change
fills the remaining prose-policy gaps while reusing those mechanisms.

## Steps

1. Add failing regressions for the new policy IDs, artifacts, routing,
   confidentiality boundaries, review recommendation contract, and retirement
   of compatibility documents.
2. Consolidate claim-preserving and compatibility-router content into the
   general/math/graph policies and existing writing workflow.
3. Add the MathSciNet/zbMATH overlay and the thin writing-review template.
4. Extend existing writing/review consumers without changing packet schemas or
   granting reviewers edit authority.
5. Update manifests and all existing writing-policy bookkeeping artifacts.
6. Regenerate hashes and documentation; run focused and full verification.
7. Run fresh-context reviews and correct material findings.

## Decisions

| Decision | Rationale | Status |
|---|---|---|
| Keep writing and review workflows independent | They have different authority and output contracts | accepted |
| Compose them through one template | The missing piece is orchestration, not another gate | accepted |
| Reuse V1 task/result packets | The existing closed contract already carries inert refs and advisory findings | accepted |
| Treat current venue rules as authoritative | Historical books and examples may be dated or subjective | accepted |
| Keep code-writing separate | Scientific prose and source-code style are distinct domains | accepted |
| Fail closed on restricted review provenance | Disclosure cannot be undone after a tool opens the item | accepted |

## Verification Plan

| Check | Command or method | Expected result |
|---|---|---|
| Pre-change regression | focused writing-style tests | new cases fail before implementation |
| Policy sync | `python tools/sync_writing_policy.py --check` | no hash drift |
| Focused tests | writing-style, installer, delegation suites | pass |
| Generated docs | docs generation and docs check | clean generated outputs |
| Full regression | repository test target | pass |
| Independent review | fresh code/test/security reviewers | no unresolved material finding |
