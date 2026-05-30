---
name: submission-venue-selector
description: Use when selecting, ranking, or validating submission venues for an existing scholarly manuscript or draft venue shortlist. Do not use for generic draft review, rewriting, paper retrieval, paper download, Zotero mutation, or one-off venue facts.
metadata:
  short-description: Automated journal and conference venue selection
---

# Submission Venue Selector

Use this skill to build an evidence-backed venue dossier for a scholarly draft.
It can run a runtime helper that extracts references, builds candidate venues,
collects or records related-paper evidence, scores fit, and writes a ranked
recommendation.

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
- `resolve --dir <workspace> [--allow-network]`
- `expand --dir <workspace>`
- `venues --dir <workspace>`
- `recent --dir <workspace>`
- `score --dir <workspace>`
- `report --dir <workspace>`
- `validate --dir <workspace>`
- `run --dir <workspace> --draft <path> --offline`
- `purge --dir <workspace>`
- `smoke`

## Safety Defaults

- Local/offline by default.
- Network requires `--allow-network` and optional `--allow-provider <name>`.
- Downloads, Zotero mutations, and Unpaywall email use are forbidden unless
  explicitly enabled by command flags.
- Raw draft text is not persisted unless `--retain-draft-text` is used.
- Reports separate observed evidence from inferred venue fit.

## Workflow

1. Start with `init` or `run`.
2. Confirm `privacy-gate` before any live provider calls.
3. Use `providers --check` to record available provider capabilities.
4. Resolve references and derive candidate venues.
5. Score venues with evidence-linked criteria.
6. Run `validate` before treating the recommendation as deliverable.

## Read When Needed

- `references/provider-policy.md`: provider capabilities and network rules.
- `references/artifact-schema.md`: artifact files, IDs, and validation rules.
- `references/scoring-rubric.md`: scoring criteria and delivery statuses.
- `references/report-contract.md`: recommendation report structure.
- `references/privacy-and-network-policy.md`: draft privacy, cache, and mutation boundaries.
