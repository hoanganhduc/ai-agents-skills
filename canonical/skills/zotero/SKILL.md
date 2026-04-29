---
name: zotero
description: Use when the user asks to send, get, retrieve, find, share, add, or search for a paper. This is the live OpenClaw Zotero workflow adapted for Codex and should take priority over external paper retrieval.
metadata:
  short-description: Zotero-first paper and library management
---

# Zotero

This uses the vendored Codex runtime copy of the Zotero workflow.

## Routing rule

Use this skill first for any paper request involving:

- "send me"
- "get me"
- "retrieve"
- "find"
- "fetch"
- "share"
- "add to Zotero"
- "search my library"
- "my papers"
- "my collections"

Prefer this over `getscipapers_requester` whenever the request involves the user's library.

## Base path

All live commands come from:

- `~/.codex/runtime/workspace/skills/zotero/`

Use the shared Codex runtime runner rather than invoking `run_zot.sh` directly. The runner sets
the vendored workspace path, `PYTHONPATH`, secrets, and workspace-local binaries for the migrated
workflow.

Shared runner:

- `bash ~/.codex/runtime/run_skill.sh`

## Core commands

Use `functions.exec_command`.

Common patterns:

```bash
bash ~/.codex/runtime/run_skill.sh skills/zotero/run_zot.sh --json get "<query>"
```

```bash
bash ~/.codex/runtime/run_skill.sh skills/zotero/run_zot.sh --json get --link "<query>"
```

```bash
bash ~/.codex/runtime/run_skill.sh skills/zotero/run_zot.sh --json get "<query>" --index 0
```

```bash
bash ~/.codex/runtime/run_skill.sh skills/zotero/run_zot.sh add "<DOI or arXiv or URL>" --collection "<name>"
```

```bash
bash ~/.codex/runtime/run_skill.sh skills/zotero/run_zot.sh add "/path/to/file.ext" --collection "<name>"
```

```bash
bash ~/.codex/runtime/run_skill.sh skills/zotero/run_zot.sh update <key> --item-type manuscript
```

```bash
bash ~/.codex/runtime/run_skill.sh skills/zotero/run_zot.sh update <key> --attach-local-file "/path/to/file.pdf"
```

```bash
cd ~/.codex/runtime/workspace && PYTHONPATH="$HOME/.codex/runtime/workspace/.local:$PYTHONPATH" python3 skills/zotero/zot.py search "<query>" --json
```

```bash
cd ~/.codex/runtime/workspace && PYTHONPATH="$HOME/.codex/runtime/workspace/.local:$PYTHONPATH" python3 skills/zotero/zot.py list-collections --tree --json
```

```bash
cd ~/.codex/runtime/workspace && PYTHONPATH="$HOME/.codex/runtime/workspace/.local:$PYTHONPATH" python3 skills/zotero/zot.py notes <key>
```

```bash
cd ~/.codex/runtime/workspace && PYTHONPATH="$HOME/.codex/runtime/workspace/.local:$PYTHONPATH" python3 skills/zotero/zot.py doctor
```

```bash
cd ~/.codex/runtime/workspace && PYTHONPATH="$HOME/.codex/runtime/workspace/.local:$PYTHONPATH" python3 skills/zotero/zot.py sync-cache
```

## Most important behaviors imported from OpenClaw

- Always search Zotero first before trying other retrieval paths.
- For review tasks that need a paper/book, if Zotero does not have it, route next to
  `calibre` before any online retrieval.
- If `get` returns multiple results, show the numbered candidates and ask the user to pick.
- Do not guess the intended paper when results are ambiguous.
- For link sharing, use the Zotero workflow rather than ad hoc file browsing.
- Use `--index` only after showing the user the numbered candidate list.

## Add-paper rules imported from the bot

- Preprints and arXiv papers should end up as `manuscript`, not `preprint`.
- Different versions of the same paper are intentionally allowed unless they are DOI-identical duplicates.
- Do not silently add papers without collection assignment unless the user explicitly says to skip that.
- `add` accepts DOI, arXiv ID, URL, ISBN, or a local file path; keep the collection-assignment workflow in front unless the user explicitly opts out.

## Collection workflow

If the user asks to add a paper and does not specify a collection:

1. List the collection tree.
2. Suggest likely collections.
3. Ask the user to choose one or more.
4. Then run the add command.

## High-value maintenance actions

- `doctor` for end-to-end health checks
- `sync-cache` before heavy library inspection if the cache may be stale
- `notes <key>` when the user wants child-note context for an item
- `update <key> --attach-local-file ...` when a retrieved or local file should be attached to an existing Zotero item

## Fallback rule

Only route to `getscipapers_requester` if:

- the paper is not in Zotero
- the Calibre library also does not satisfy the request when the task is a review
  that needs the document
- the user explicitly wants an external download
- or the Zotero workflow clearly cannot satisfy the request
