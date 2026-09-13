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
8. Generalize the existing writing router using projected canonical hashes and
   target-native loading contracts.
9. Refresh the complete installed writing-consumer inventory and retire the old
   compatibility document through state/signature-gated removal.
10. Run sanitized Codex/Grok native loader checks, reinstall only the approved
    writing scope, and verify the resulting plan is clean.

## Decisions

| Decision | Rationale | Status |
|---|---|---|
| Keep writing and review workflows independent | They have different authority and output contracts | accepted |
| Compose them through one template | The missing piece is orchestration, not another gate | accepted |
| Reuse V1 task/result packets | The existing closed contract already carries inert refs and advisory findings | accepted |
| Treat current venue rules as authoritative | Historical books and examples may be dated or subjective | accepted |
| Keep code-writing separate | Scientific prose and source-code style are distinct domains | accepted |
| Fail closed on restricted review provenance | Disclosure cannot be undone after a tool opens the item | accepted |
| Route inert instruction-doc storage through global context | Installing files alone does not make them model-visible | accepted |
| Keep Grok on native rules loading | Its current CLI inspect output proves the four rule files are global inputs | accepted |
| Refresh managed consumers but do not create absent ones | Upgrade stale copies without broadening the installed capability set | accepted |
| Retire old copies only from exact state/signature evidence | Preserve user edits and keep removal recoverable | accepted |

## Verification Plan

| Check | Command or method | Expected result |
|---|---|---|
| Pre-change regression | focused writing-style tests | new cases fail before implementation |
| Policy sync | `python tools/sync_writing_policy.py --check` | no hash drift |
| Focused tests | writing-style, installer, delegation suites | pass |
| Generated docs | docs generation and docs check | clean generated outputs |
| Full regression | repository test target | pass |
| Independent review | fresh code/test/security reviewers | no unresolved material finding |
| Codex prompt transport | sanitized `codex debug prompt-input` check | router and four routes visible; no retired reference |
| Grok rule discovery | sanitized `grok inspect --json` check | four current rules visible; retired rule absent |
