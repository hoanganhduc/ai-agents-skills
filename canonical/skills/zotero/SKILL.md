---
name: zotero
description: Use when the user asks to send, get, retrieve, find, share, add, or search for a paper. This is the live OpenClaw Zotero workflow adapted for Codex and should take priority over external paper retrieval.
metadata:
  short-description: Zotero-first paper and library management
---

# Zotero


## Windows Runtime Commands

On native Windows, use the managed Windows runner and the native runtime command target. Set `$runtime` to the installed runtime root. Multi-agent installs usually use `%LOCALAPPDATA%\ai-agents-skills\runtime`. Then run:

```powershell
$runtime = if ($env:AAS_RUNTIME_ROOT) { $env:AAS_RUNTIME_ROOT } else { "$env:LOCALAPPDATA\ai-agents-skills\runtime" }
& "$runtime\run_skill.bat" "skills/zotero/run_zot.bat" <args>
```

POSIX examples below use `run_skill.sh` and `.sh` command targets; use the Windows command target above on native Windows.

This uses the managed ai-agents-skills runtime copy of the Zotero workflow.

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

Prefer this over `getscipapers-requester` whenever the request involves the user's library.

## Base path

All live commands come from:

- `$AAS_RUNTIME_WORKSPACE/skills/zotero/`

Use the managed runtime runner rather than invoking `run_zot.sh` directly. The runner sets
the vendored workspace path, `PYTHONPATH`, secrets, and workspace-local binaries for the migrated
workflow.

Shared runner:

- `bash "$AAS_RUNTIME_ROOT/run_skill.sh"`

## Local Library Profile Gate

Do not assume Zotero settings, database, storage, Translation Server, or
WebDAV paths. Before library-changing work, run or rely on a profile-aware
audit from the canonical installer:

```bash
cd ~/ai-agents-skills && make library-profile-audit ARGS="--profile library --json"
```

The audit is read-only. Discovery does not make a path authoritative. A Zotero
database/storage path becomes mutation-eligible only after validation and
explicit profile selection.

Supported system profiles:

- `linux-local`
- `windows-mounted` for Linux-side inspection of `/windows/Users/...`
- `windows-native` for native Windows execution

Mounted Windows and cloud-backed SQLite databases are read-only by default from
Linux. If no local Zotero database is found, mark the profile
`local-db-missing`; do not create a database and do not use runtime caches as
authoritative state. Remote-only Zotero API/WebDAV workflows may continue only
when credentials are configured and the result is labeled remote-only.

Default Zotero mutation must use this order:

1. selected local DB/storage diagnostic preflight
2. Translation Server metadata resolution when metadata is needed
3. Zotero API mutation bound to explicit library scope
4. WebDAV sync for attachment-affecting changes
5. API/WebDAV/local diagnostic verification

Direct `zotero.sqlite` writes are expert repair only. They require Zotero to be
closed, DB/WAL/SHM/storage backups, a copied working DB, integrity checks
before and after, a transaction journal, and explicit confirmation.

Storage checks must report local `storage/`, linked files, API attachment
records, and WebDAV zips separately.

Read-only local access is allowed when it is labeled correctly:

- normal `search` uses the Zotero API first and remains the source of truth
- `search --local-db` may inspect discovered `zotero.sqlite` candidates in
  read-only mode for offline/diagnostic use
- local DB results are degraded when SQLite integrity checks are not clean and
  must not be used as the only evidence that Zotero lacks an item
- `get` may return a PDF from local Zotero `storage/` before falling back to
  WebDAV, because this is read-only and uses the API attachment key
- use `get --no-local-storage` when WebDAV retrieval needs to be forced

## Translation Server

The local Translation Server should be available at:

- `http://localhost:1969`

This system uses a locally owned GHCR image built from the fork:

- repo: `https://github.com/hoanganhduc/translation-server`
- image: `ghcr.io/hoanganhduc/translation-server:latest`
- container: `zotero-translation-server`
- port mapping: `1969:1969`
- restart policy: `unless-stopped`

Do not assume the Docker Hub image is usable on this host. On this AMD64 Linux
system, `zotero/translation-server:latest` pulled as ARM64 and failed with
`exec format error`. Prefer the GHCR image above unless the host-specific image
support has been rechecked.

Status checks:

```bash
docker ps --filter name=zotero-translation-server --format '{{.Names}} {{.Image}} {{.Status}} {{.Ports}}'
```

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:1969/
```

`404` from the root endpoint is acceptable. For a functional metadata smoke
test, POST a DOI or URL to `/web` and confirm Zotero JSON is returned.

```bash
curl -s -X POST -H 'Content-Type: text/plain' --data 'https://doi.org/10.1038/nphys1170' http://localhost:1969/web
```

When starting or repairing the local server, prefer the runtime helper:

```bash
bash $AAS_RUNTIME_WORKSPACE/skills/zotero/scripts/start-translation-server.sh
```

Then run `doctor` before metadata-dependent add/update workflows. A reachable
Translation Server is preferred. If it is unreachable, DOI/arXiv/ISBN metadata
may still resolve through the direct fallback when runtime dependencies are
installed, and generic URL metadata may use the configured WSL helper path.
Treat other failed `doctor` checks as blockers unless the user explicitly asks
for a degraded diagnostic path:

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" skills/zotero/run_zot.sh doctor
```

## Core commands

Use `functions.exec_command`.

Common patterns:

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" skills/zotero/run_zot.sh --json get "<query>"
```

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" skills/zotero/run_zot.sh --json get --link "<query>"
```

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" skills/zotero/run_zot.sh --json get "<query>" --index 0
```

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" skills/zotero/run_zot.sh add "<DOI or arXiv or URL>" --collection "<name>"
```

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" skills/zotero/run_zot.sh add "/path/to/file.ext" --collection "<name>"
```

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" skills/zotero/run_zot.sh update <key> --item-type manuscript
```

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" skills/zotero/run_zot.sh update <key> --attach-file "/path/to/file.pdf"
```

```bash
cd "$AAS_RUNTIME_WORKSPACE" && PYTHONPATH="$AAS_RUNTIME_WORKSPACE/.local:${PYTHONPATH:-}" python3 skills/zotero/zot.py search "<query>" --json
```

```bash
cd "$AAS_RUNTIME_WORKSPACE" && PYTHONPATH="$AAS_RUNTIME_WORKSPACE/.local:${PYTHONPATH:-}" python3 skills/zotero/zot.py search --local-db "<query>" --json
```

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" skills/zotero/run_zot.sh --json get "<query>" --no-local-storage
```

```bash
cd "$AAS_RUNTIME_WORKSPACE" && PYTHONPATH="$AAS_RUNTIME_WORKSPACE/.local:${PYTHONPATH:-}" python3 skills/zotero/zot.py list-collections --tree --json
```

```bash
cd "$AAS_RUNTIME_WORKSPACE" && PYTHONPATH="$AAS_RUNTIME_WORKSPACE/.local:${PYTHONPATH:-}" python3 skills/zotero/zot.py notes <key>
```

```bash
cd "$AAS_RUNTIME_WORKSPACE" && PYTHONPATH="$AAS_RUNTIME_WORKSPACE/.local:${PYTHONPATH:-}" python3 skills/zotero/zot.py doctor
```

```bash
cd "$AAS_RUNTIME_WORKSPACE" && PYTHONPATH="$AAS_RUNTIME_WORKSPACE/.local:${PYTHONPATH:-}" python3 skills/zotero/zot.py sync-cache
```

## Most important behaviors imported from OpenClaw

- Always search Zotero first before trying other retrieval paths.
- For chapters, `@incollection` entries, proceedings articles, and older
  no-DOI sources, search at both levels before reporting an absence: the
  chapter title/author and the parent container title/editor/ISBN. Clearly
  distinguish "standalone chapter item not found" from "parent book or
  proceedings item found."
- For review tasks that need a paper/book, if Zotero does not have it, route next to
  `calibre` before any online retrieval.
- If `get` returns multiple results, show the numbered candidates and ask the user to pick.
- Do not guess the intended paper when results are ambiguous.
- Prefer the default `get` local-storage check before WebDAV. Treat it as a
  read-only file lookup, not a DB mutation.
- Use `search --local-db` only as an explicit offline/diagnostic fallback; label
  malformed SQLite results as degraded.
- For link sharing, use the Zotero workflow rather than ad hoc file browsing.
- Use `--index` only after showing the user the numbered candidate list.
- For library-mutating or outward-facing actions (`add`, `update`, attachment
  changes, trash/delete/remove, sync-affecting operations, `send`, or share),
  run the relevant dry-run/preview/search first when available, state the exact
  item/library/collection/channel to be affected, and get explicit confirmation
  unless the user's latest request already approved that exact action.

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
- `update <key> --attach-file ...` when a retrieved or local file should be attached to an existing Zotero item

## Fallback rule

Only route to `getscipapers-requester` if:

- the paper is not in Zotero; for chapters and `@incollection` entries, this
  means both the chapter-level query and the parent book/proceedings query
  failed or cannot provide the needed text
- the Calibre library also does not satisfy the request when the task is a review
  that needs the document
- the user explicitly says not to check/use the library, or confirms external
  retrieval after the library-first result is reported
- or the Zotero workflow clearly cannot satisfy the request
