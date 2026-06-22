---
name: calibre
description: Use when the user wants to search, retrieve, send, add, update, sync, export, convert, or clean books from the managed Calibre library runtime.
metadata:
  short-description: Calibre library management via managed runtime
---

# Calibre


## Windows Runtime Commands

On native Windows, use the managed Windows runner and the native runtime command target. Set `$runtime` to the installed runtime root. Multi-agent installs usually use `%LOCALAPPDATA%\ai-agents-skills\runtime`. Then run:

```powershell
$runtime = if ($env:AAS_RUNTIME_ROOT) { $env:AAS_RUNTIME_ROOT } else { "$env:LOCALAPPDATA\ai-agents-skills\runtime" }
& "$runtime\run_skill.bat" "skills/calibre/run_cal.bat" <args>
```

POSIX examples below use `run_skill.sh` and `.sh` command targets; use the Windows command target above on native Windows.

This uses the managed ai-agents-skills runtime copy of the Calibre workflow.

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
- Do not use this in place of `zotero` for generic "find/get/share/download a paper, DOI, ISBN, or book" requests; the top-level routing rule handles those with Zotero first.
- For review tasks that require locating a paper or book and the user did not
  supply the file/path, use Calibre immediately after Zotero and before any
  online retrieval.
- If Zotero does not satisfy a generic retrieval request, use
  `getscipapers-requester` only after the user explicitly says not to check/use
  the library or confirms outside retrieval after the library-first result is
  reported. Return to `calibre` only if the resulting file should be added to
  the ebook library.

## Base path

- `$AAS_RUNTIME_WORKSPACE/skills/calibre/`

Use the managed runtime runner rather than invoking `run_cal.sh` directly.

Shared runner:

- `bash "$AAS_RUNTIME_ROOT/run_skill.sh"`

## Local Library Profile Gate

Do not assume Calibre settings, library, or `metadata.db` locations. Before
library-changing work, run or rely on a profile-aware read-only audit from the
canonical installer:

```bash
cd ~/ai-agents-skills && make library-profile-audit ARGS="--profile library --json"
```

Discovery only lists candidates. It must distinguish authoritative Calibre
libraries from runtime caches before any add, update, remove, convert, or sync
operation. If no authoritative local Calibre database is found, mark the
profile `local-db-missing`; do not create a library and do not treat cache DBs
as writable libraries.

Supported system profiles:

- `linux-local`
- `windows-mounted` for Linux-side inspection of `/windows/Users/...`
- `windows-native` for native Windows execution

Calibre candidate validation must check `metadata.db`, quick-check status, book
count, author/book file-tree consistency, canonical real path, symlink/mount or
cloud-backed classification, and runtime-cache roots. Runtime caches are never
mutation-authoritative even when their book count matches the real library.

Writes prefer a detected `calibredb` or `calibredb.exe` backend with an
explicit library path. Guarded direct SQLite is fallback only and requires
backup, lock, selected library root, and explicit warning. Windows-mounted or
cloud-backed Calibre libraries are read-only from Linux unless the profile
explicitly opts in after dry-run review.

## Core commands

Use `functions.exec_command`.

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" skills/calibre/run_cal.sh search "<query>" [--format epub] [--tag fiction] [--limit 50] [--series "Series Name"]
```

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" skills/calibre/run_cal.sh get "<query>" [--format pdf] [--send "telegram:CHAT_ID"]
```

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" skills/calibre/run_cal.sh get --id 42 [--send "zulip:Research:books"]
```

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" skills/calibre/run_cal.sh get "ring" --index 0 [--send "telegram:CHAT_ID"]
```

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" skills/calibre/run_cal.sh add /path/book.epub [--isbn 9780140449136] [--title "X" --author "Y"] [--dry-run]
```

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" skills/calibre/run_cal.sh update --id 42 --title "X" --author "Y" --tags "a,b" --year 1965 --publisher "P" [--series "S" --series-index 1 --isbn 9780441013593]
```

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" skills/calibre/run_cal.sh add-tag --id 42 --tag "to-read"
```

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" skills/calibre/run_cal.sh remove-tag --id 42 --tag "to-read"
```

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" skills/calibre/run_cal.sh list-shelves [--tags|--series|--publishers]
```

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" skills/calibre/run_cal.sh sync [--force] [--progress]
```

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" skills/calibre/run_cal.sh remove "query" [--dry-run]
```

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" skills/calibre/run_cal.sh remove --id 42 [--dry-run]
```

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" skills/calibre/run_cal.sh convert --id 42 --to epub [--from pdf]
```

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" skills/calibre/run_cal.sh export --id 42 [--format bibtex]
```

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" skills/calibre/run_cal.sh doctor
```

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" skills/calibre/run_cal.sh clean
```

## Important behaviors

- If `get` returns multiple matches, show candidates and ask the user to pick instead of guessing.
- Use `--index` only after showing candidates and receiving the user's selected
  index.
- Prefer dry-run before destructive operations like `remove`.
- For library-mutating or outward-facing actions (`add`, `update`, `add-tag`,
  `remove-tag`, `remove`, `convert`, `sync`, `clean`, `get --send`, or share),
  run the relevant dry-run/preview/search first when available, state the exact
  book/library/tag/channel to be affected, and get explicit confirmation unless
  the user's latest request already approved that exact action.
- Do not assume Calibre host dependencies such as `ebook-convert` are present; use `doctor` when conversion health matters.
- Sending books uses the OpenClaw file-sending path from the library workflow.
- `--send` uses `channel:target` syntax such as `telegram:CHAT_ID`, `zulip:Stream:topic`, `googlechat:SPACE`, or `whatsapp:PHONE`.
- `add --isbn` enriches metadata from Open Library before the library write.
- `update --tags` replaces the full tag set; use `add-tag` and `remove-tag` for incremental changes.
- Run `sync` at the start of a session if the library may have changed from Calibre desktop or another device.
- Use `sync --progress` when pulling `metadata.db` may take time. Progress is
  emitted as JSON lines on stderr so stdout remains the final JSON result.

## Operational model

- Library access is profile-selected. Older Google-Drive-backed direct-SQLite
  cache workflows may still exist, but profile-aware local-library validation
  must run before treating any `metadata.db` as authoritative.
- Prefer a detected `calibredb`/`calibredb.exe` backend for authoritative
  library writes; direct SQLite is a guarded fallback only.
- Book files are downloaded to staging on demand rather than stored permanently in the workspace.
- After write operations, the updated `metadata.db` is pushed back to Drive and the local cache is refreshed.
- A file lock protects `metadata.db` from concurrent write conflicts.
- If Drive is unavailable, search can fall back to the last known local cache.
- `clean` removes staged files older than 24 hours.
