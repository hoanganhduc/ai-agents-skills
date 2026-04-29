---
name: getscipapers-requester
description: Use for external DOI/ISBN/title resolution, manifest creation from pasted text, and paper retrieval when Zotero is not the right path or the user explicitly wants an external download.
metadata:
  short-description: External paper retrieval fallback
---

# GetSciPapers Requester

This is the external retrieval fallback. Do not use it before `zotero` for normal paper/library requests, and for review tasks that need a paper/book do not use it before both `zotero` and `calibre` have been checked.

## When to use

- The paper is not in Zotero
- and, for review tasks, it is also not in Calibre
- The user explicitly says "download"
- The task is DOI/ISBN/title resolution from external sources
- The user pasted many identifiers and wants batch retrieval

## Base path

- `~/.codex/runtime/workspace/skills/getscipapers_requester/`

Use the Codex runtime runner rather than invoking `run_gsp_helper.sh` directly. The runner
sets `OPENCLAW_WORKSPACE`, `PYTHONPATH`, secrets, and workspace-local binaries.

Shared runner:

- `bash ~/.codex/runtime/run_skill.sh`

## Core commands

```bash
bash ~/.codex/runtime/run_skill.sh skills/getscipapers_requester/run_gsp_helper.sh run-getscipapers --timeout 180 -- getpapers --doi <DOI>
```

```bash
bash ~/.codex/runtime/run_skill.sh skills/getscipapers_requester/run_gsp_helper.sh resolve auto "<title>" --best
```

```bash
bash ~/.codex/runtime/run_skill.sh skills/getscipapers_requester/run_gsp_helper.sh make-manifest auto "<text-or-file>"
```

```bash
bash ~/.codex/runtime/run_skill.sh skills/getscipapers_requester/run_gsp_helper.sh doctor
```

## Workflow

1. If DOI/ISBN is available, use it directly.
2. Otherwise resolve from title or text.
3. For many papers, create a manifest first.
4. For large batches, prefer dry-run style validation first.
5. If retrieval fails, report the failure precisely instead of hand-waving.
