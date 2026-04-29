---
name: research-digest-wrapper
description: Use when the user wants a local research digest from tracked topics or wants to manage tracked research topics.
metadata:
  short-description: Local research digest from tracked topics
---

# Research Digest Wrapper

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
