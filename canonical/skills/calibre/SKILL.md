---
name: calibre
description: Use when the user wants to search, retrieve, send, add, update, sync, export, convert, or clean books from the vendored Codex Calibre library runtime.
metadata:
  short-description: Calibre library management via Codex runtime
---

# Calibre

This uses the vendored Codex runtime copy of the Calibre workflow.

## When to use

- search the Calibre library
- retrieve or send an ebook
- retrieve by book ID or disambiguation index
- add a new book file
- update book metadata or tags
- add or remove tags, and list shelves
- sync or doctor the library
- remove a book with dry-run support
- export metadata or convert formats
- clean staging files

## Routing boundary

- Prefer this skill for explicit Calibre library operations and ebook workflows.
- Do not use this in place of `zotero` for generic "find/get/share/download a paper, DOI, ISBN, or book" requests; the Claude/OpenClaw top-level router handles those with Zotero first.
- For review tasks that require locating a paper or book and the user did not
  supply the file/path, use Calibre immediately after Zotero and before any
  online retrieval.
- If Zotero does not satisfy a generic retrieval request and the user wants an outside download, use `getscipapers_requester` first, then return to `calibre` if the resulting file should be added to the ebook library.

## Base path

- `~/.codex/runtime/workspace/skills/calibre/`

Use the Codex runtime runner rather than invoking `run_cal.sh` directly.

Shared runner:

- `bash ~/.codex/runtime/run_skill.sh`

## Core commands

Use `functions.exec_command`.

```bash
bash ~/.codex/runtime/run_skill.sh skills/calibre/run_cal.sh search "<query>" [--format epub] [--tag fiction] [--limit 50] [--series "Series Name"]
```

```bash
bash ~/.codex/runtime/run_skill.sh skills/calibre/run_cal.sh get "<query>" [--format pdf] [--send "telegram:CHAT_ID"]
```

```bash
bash ~/.codex/runtime/run_skill.sh skills/calibre/run_cal.sh get --id 42 [--send "zulip:Research:books"]
```

```bash
bash ~/.codex/runtime/run_skill.sh skills/calibre/run_cal.sh get "ring" --index 0 [--send "telegram:CHAT_ID"]
```

```bash
bash ~/.codex/runtime/run_skill.sh skills/calibre/run_cal.sh add /path/book.epub [--isbn 9780140449136] [--title "X" --author "Y"] [--dry-run]
```

```bash
bash ~/.codex/runtime/run_skill.sh skills/calibre/run_cal.sh update --id 42 --title "X" --author "Y" --tags "a,b" --year 1965 --publisher "P" [--series "S" --series-index 1 --isbn 9780441013593]
```

```bash
bash ~/.codex/runtime/run_skill.sh skills/calibre/run_cal.sh add-tag --id 42 --tag "to-read"
```

```bash
bash ~/.codex/runtime/run_skill.sh skills/calibre/run_cal.sh remove-tag --id 42 --tag "to-read"
```

```bash
bash ~/.codex/runtime/run_skill.sh skills/calibre/run_cal.sh list-shelves [--tags|--series|--publishers]
```

```bash
bash ~/.codex/runtime/run_skill.sh skills/calibre/run_cal.sh sync [--force] [--progress]
```

```bash
bash ~/.codex/runtime/run_skill.sh skills/calibre/run_cal.sh remove "query" [--dry-run]
```

```bash
bash ~/.codex/runtime/run_skill.sh skills/calibre/run_cal.sh remove --id 42 [--dry-run]
```

```bash
bash ~/.codex/runtime/run_skill.sh skills/calibre/run_cal.sh convert --id 42 --to epub [--from pdf]
```

```bash
bash ~/.codex/runtime/run_skill.sh skills/calibre/run_cal.sh export --id 42 [--format bibtex]
```

```bash
bash ~/.codex/runtime/run_skill.sh skills/calibre/run_cal.sh doctor
```

```bash
bash ~/.codex/runtime/run_skill.sh skills/calibre/run_cal.sh clean
```

## Important behaviors

- If `get` returns multiple matches, show candidates and ask the user to pick instead of guessing.
- Prefer dry-run before destructive operations like `remove`.
- Do not assume Calibre host dependencies such as `ebook-convert` are present; use `doctor` when conversion health matters.
- Sending books uses the OpenClaw file-sending path from the library workflow.
- `--send` uses `channel:target` syntax such as `telegram:CHAT_ID`, `zulip:Stream:topic`, `googlechat:SPACE`, or `whatsapp:PHONE`.
- `add --isbn` enriches metadata from Open Library before the library write.
- `update --tags` replaces the full tag set; use `add-tag` and `remove-tag` for incremental changes.
- Run `sync` at the start of a session if the library may have changed from Calibre desktop or another device.
- Use `sync --progress` when pulling `metadata.db` may take time. Progress is
  emitted as JSON lines on stderr so stdout remains the final JSON result.

## Operational model

- The library is Google-Drive-backed and reads/writes `metadata.db` directly; no `calibredb` binary is required for normal operations.
- Book files are downloaded to staging on demand rather than stored permanently in the workspace.
- After write operations, the updated `metadata.db` is pushed back to Drive and the local cache is refreshed.
- A file lock protects `metadata.db` from concurrent write conflicts.
- If Drive is unavailable, search can fall back to the last known local cache.
- `clean` removes staged files older than 24 hours.
