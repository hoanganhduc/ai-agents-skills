---
name: submission-venue-selector
description: Use when selecting, ranking, or validating submission venues for an existing scholarly manuscript or draft venue shortlist. A deliverable venue recommendation requires comparator-paper evidence for every ranked venue; bibliography overlap and offline placeholders are discovery signals only. Do not use for generic draft review, rewriting, paper retrieval, paper download, Zotero mutation, or one-off venue facts.
metadata:
  short-description: Evidence-gated journal and conference venue selection
---

# Submission Venue Selector

Use this skill to build an evidence-backed venue dossier for a scholarly draft.
It can run a runtime helper that extracts references, builds candidate venues,
collects or records related-paper evidence, scores fit, and writes journal
recommendations with heuristic acceptance-chance intervals.

## No Shallow Shortlist

A venue recommendation is deliverable only when each ranked venue has
comparator-paper evidence. Bibliography overlap, venue reputation, and offline
placeholders are discovery signals, not comparator-paper evidence. If this
evidence is unavailable, output `incomplete analysis`, keep delivery status
`not-ready`, and do not present a final ranked shortlist.

The final report must still list every candidate journal with an estimated
acceptance-chance interval, confidence, calculation class, modifier breakdown,
and caveats. These estimates are heuristics, not predictions or guarantees.
Before writing a deliverable report, load `writing-style-settings.md` and record
the active style profile. For mathematical, TCS, graph-theoretic, Lean, or
LaTeX manuscripts, also load `math-manuscript-style.md`.

## Routing Boundary

Use this skill for:

- "Where should I submit this draft?"
- journal or conference shortlist ranking
- validating an existing venue shortlist
- comparing venue fit against a draft and its bibliography

Do not use this skill for:

- draft rewriting or polishing; use `draft-writing`
- paper retrieval or downloads; use `zotero` and then `getscipapers-requester`
- general paper review; use `paper-review` or `agent-group-discuss`
- one-off venue facts that do not require a draft-fit dossier

## Runtime Helper

POSIX:

```bash
bash ~/.codex/runtime/run_skill.sh \
  skills/submission-venue-selector/run_submission_venue_selector.sh \
  run --dir /path/to/venue-run --draft /path/to/draft.tex --offline
```

This offline command is a smoke/provisional run. It is not a deliverable venue
recommendation unless trusted fixture/cache comparator evidence is provided.

Windows PowerShell:

```powershell
$runtime = if ($env:AAS_RUNTIME_ROOT) { $env:AAS_RUNTIME_ROOT } elseif (Test-Path "$env:USERPROFILE\.codex\runtime") { "$env:USERPROFILE\.codex\runtime" } else { "$env:LOCALAPPDATA\ai-agents-skills\runtime" }
& "$runtime\run_skill.ps1" "skills/submission-venue-selector/run_submission_venue_selector.ps1" run --dir "$env:USERPROFILE\venue-run" --draft "$env:USERPROFILE\drafts\paper.tex" --offline
```

Windows CMD:

```bat
"%USERPROFILE%\.codex\runtime\run_skill.bat" skills/submission-venue-selector/run_submission_venue_selector.bat run --dir "%USERPROFILE%\venue-run" --draft "%USERPROFILE%\drafts\paper.tex" --offline
```

Useful commands:

- `init --dir <workspace> --draft <path>`
- `plan --dir <workspace>`
- `extract --dir <workspace>`
- `privacy-gate --dir <workspace>`
- `providers --check --dir <workspace>`
- `resolve --dir <workspace> [--allow-network --allow-provider <name>]`
- `expand --dir <workspace>`
- `venues --dir <workspace>`
- `recent --dir <workspace>`
- `score --dir <workspace>`
- `report --dir <workspace>`
- `validate --dir <workspace>`
- `run --dir <workspace> --draft <path> --offline` for smoke/provisional output
- `purge --dir <workspace>`
- `smoke`

## Safety Defaults

- Local/offline by default.
- Network requires a prior ok `privacy-gate`, `--allow-network`, and explicit
  `--allow-provider <name>`.
- Downloads, Zotero mutations, and Unpaywall email use are forbidden unless
  explicitly enabled by command flags.
- Raw draft text is not persisted unless `--retain-draft-text` is used.
- Reports separate observed evidence from inferred venue fit and must mark
  placeholder-only output as `incomplete analysis`.
- Acceptance-chance estimates are required in reports, but bare percentages and
  predictive acceptance claims are invalid.
- Final report artifacts should record `style_profile_ref`, `active_overlays`,
  `active_requirement_ids`, and `style_applied`. A bare `style_applied: true`
  value is not enough unless the artifact or workflow ledger also records the
  loaded policy and selected requirements.

## Workflow

1. Start with `init` or `run`.
2. Confirm `privacy-gate` before any live provider calls.
3. Use `providers --check` to record available provider capabilities.
4. Resolve references and derive candidate venues.
5. Score venues with criterion-level evidence IDs and acceptance-chance
   intervals.
6. Run `validate` before treating the recommendation as deliverable.

## Read When Needed

- `references/provider-policy.md`: provider capabilities and network rules.
- `references/artifact-schema.md`: artifact files, IDs, and validation rules.
- `references/scoring-rubric.md`: scoring criteria and delivery statuses.
- `references/report-contract.md`: recommendation report structure.
- `references/privacy-and-network-policy.md`: draft privacy, cache, and mutation boundaries.
