---
name: vnthuquan
description: Use when the user explicitly wants Vietnam Thu Quan / vnthuquan / vietnamthuquan.eu ebook discovery, metadata, mirror checks, categories, formats, archive inspection, or controlled downloads through the local vnthuquan package.
metadata:
  short-description: Vietnam Thu Quan ebook workflows
---

# vnthuquan

Use this skill only for Vietnam Thu Quan site-specific ebook work:

- search Vietnam Thu Quan books
- list categories, formats, latest books, authors, ranked lists, or category
  contents
- inspect book metadata and shareable source links
- check mirrors and wrapper/package health
- inspect the wrapper-managed download archive
- controlled dry-run/executed downloads, queue execution, validation, failure
  recovery, and guarded Calibre handoff

## Routing Boundary

- Do not use this skill as the first route for generic papers, DOI, ISBN, or
  book retrieval. Follow the library-first routing rules: Zotero first, then
  Calibre or getscipapers as appropriate.
- Do not replace the `calibre` skill for local library management. Use Calibre
  only after a `vnthuquan` download has been validated or when the user
  explicitly asks for Calibre integration.

## Runtime

Use the Codex runtime runner rather than invoking the wrapper directly:

```bash
bash ~/.codex/runtime/run_skill.sh skills/vnthuquan/run_vnthuquan.sh diagnose --json
```

Runtime wrapper:

- `~/.codex/runtime/workspace/skills/vnthuquan/run_vnthuquan.sh`

Detailed workflows live in:

- `~/.codex/skills/vnthuquan/references/workflows.md`

## Current Phase

Phase 5 is cross-target discovery, controlled downloads, queue management,
validation, Calibre dry-run handoff, and guarded Calibre write integration. It
includes:

- `diagnose`
- `doctor`
- `mirrors list`
- `mirrors check`
- `config path`
- `config show`
- `categories list`
- `categories show`
- `formats`
- `list ...`
- `search ...`
- `show ...`
- `archive path`
- `archive list`
- `completion ...`
- download dry-run by default
- download execution only with `--execute --yes`
- queue manifest creation
- queue execution only with `execute-queue ... --yes`
- failed-queue retry manifest creation
- local file validation
- Calibre dry-run handoff for validated EPUB/PDF files
- Calibre writes only through
  `add-to-calibre PATH --execute --yes --duplicates-reviewed`

## Examples

```bash
bash ~/.codex/runtime/run_skill.sh skills/vnthuquan/run_vnthuquan.sh search "Kim Dung" --json
```

```bash
bash ~/.codex/runtime/run_skill.sh skills/vnthuquan/run_vnthuquan.sh categories list --json
```

```bash
bash ~/.codex/runtime/run_skill.sh skills/vnthuquan/run_vnthuquan.sh show --title "Mưa Đỏ" --links --json
```

```bash
bash ~/.codex/runtime/run_skill.sh skills/vnthuquan/run_vnthuquan.sh mirrors check --json
```

```bash
bash ~/.codex/runtime/run_skill.sh skills/vnthuquan/run_vnthuquan.sh download --title "Mưa Đỏ" --format epub --dry-run --json
```

```bash
bash ~/.codex/runtime/run_skill.sh skills/vnthuquan/run_vnthuquan.sh queue --query "Kim Dung" --limit 5 --format epub --json
```

```bash
bash ~/.codex/runtime/run_skill.sh skills/vnthuquan/run_vnthuquan.sh add-to-calibre PATH --dry-run --json
```

```bash
bash ~/.codex/runtime/run_skill.sh skills/vnthuquan/run_vnthuquan.sh add-to-calibre PATH --execute --yes --duplicates-reviewed --json
```

## Safety Rules

- Prefer `--json` for machine-readable output.
- If multiple books match, show numbered candidates and ask the user to choose.
- Keep wrapper config, cache, and archive state under the target-local Codex
  state directory.
- Do not mutate package default config unless the user explicitly asks for that
  after seeing the target path.
- Do not execute downloads or queue manifests unless the command includes the
  wrapper confirmation flag required for that operation.
- Do not perform Calibre writes unless a dry-run has been reviewed, duplicate
  candidates have been checked, and the command includes `--execute --yes
  --duplicates-reviewed`. Add `--allow-duplicate` only when duplicate
  candidates are intentionally being accepted as a separate Calibre entry.
