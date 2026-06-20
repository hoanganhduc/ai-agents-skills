---
name: research-digest-wrapper
description: Use when the user wants a local research digest from tracked topics or wants to manage tracked research topics.
metadata:
  short-description: Local research digest from tracked topics
---

# Research Digest Wrapper


## Windows Runtime Commands

On native Windows, use the managed Windows runner and the native runtime command target. For Codex-only installs the runtime is usually `%USERPROFILE%\.codex\runtime`; for multi-agent installs it is usually `%LOCALAPPDATA%\ai-agents-skills\runtime`. Set `$runtime` to the installed runtime root, then run:

```powershell
$runtime = if ($env:AAS_RUNTIME_ROOT) { $env:AAS_RUNTIME_ROOT } elseif (Test-Path "$env:USERPROFILE\.codex\runtime") { "$env:USERPROFILE\.codex\runtime" } else { "$env:LOCALAPPDATA\ai-agents-skills\runtime" }
& "$runtime\run_skill.bat" "skills/research-digest-wrapper/run_research_digest.bat" <args>
```

POSIX examples below use `run_skill.sh` and `.sh` command targets; use the Windows command target above on native Windows.

## Base path

- `~/.codex/runtime/workspace/skills/research-digest-wrapper/`

Use the Codex runtime runner rather than invoking the digest script directly.

Shared runner:

- `bash ~/.codex/runtime/run_skill.sh`

## Use cases

- run my research digest
- list tracked topics
- add or edit tracked topics
- doctor the digest setup

## Core execution

```bash
bash ~/.codex/runtime/run_skill.sh skills/research-digest-wrapper/run_research_digest.sh <COMMAND AND ARGS>
```

## Common actions

- `run`
- `run --tag TAG --min-priority N`
- `run --use-llm-scoring --use-llm-summary`
- `list-topics`
- `add-topic "<name>" --tag TAG --priority N`
- `edit-topic "<name>" --tag TAG --priority N`
- `disable-topic "<name>"` / `enable-topic "<name>"`
- `remove-topic "<name>"`
- `backup-topics --reason "REASON"`
- `list-topic-backups`
- `restore-topic-backup <backup-name>`
- `export-topics --output /tmp/topics.tsv`
- `import-topics /tmp/topics.tsv`
- `doctor`
- `rebuild-corpus`

Verified example shapes:

```bash
bash ~/.codex/runtime/run_skill.sh skills/research-digest-wrapper/run_research_digest.sh run --tag graph-theory --min-priority 3
```

```bash
bash ~/.codex/runtime/run_skill.sh skills/research-digest-wrapper/run_research_digest.sh add-topic "Token sliding" --tag reconfiguration --priority 5
```

## After execution

Read and summarize:

- `~/.codex/runtime/workspace/data/research/alerts/digests/latest-digest.md`

Tracked topics live at:

- `~/.codex/runtime/workspace/data/research/alerts/topics.tsv`

## Writing Style Gate

For any user-facing digest summary, load `writing-style-settings.md` before
writing. If the digest item or synthesis is mathematical, TCS, graph-theoretic,
Lean-related, or LaTeX manuscript prose, also load `math-manuscript-style.md`.
Stored digest summaries should record `style_profile_ref`, `active_overlays`,
`active_requirement_ids`, and `style_applied`; do not accept a bare
`style_applied: true` assertion as sufficient evidence.
