# zot — Headless Zotero CLI

Manage your Zotero library from the command line. Add papers by DOI/arXiv/ISBN/URL, retrieve PDFs via WebDAV, share via Google Drive, organize collections, and export BibTeX.

## Quick Start

```bash
# Search your library
zot search "token sliding"
zot search "Demaine" --bibtex

# Add a paper
zot add 10.4230/LIPIcs.FSTTCS.2025.31 --collection "Reconfiguration"
zot add arXiv:2301.12345 --no-pdf --collection "Graph Theory"

# Preview without creating
zot --dry-run add 10.1093/jcr/ucw010

# Retrieve a paper (WebDAV → local PDF)
zot get "vertex cover P3"
zot get "token sliding" --index 2

# Share via Google Drive link
zot get --link "vertex cover"

# Update existing items
zot update ABC12345 --attach-pdf
zot update ABC12345 --add-collection "Graph Theory" --remove-collection "Auto-cataloged"

# Collections
zot list-collections --tree
zot create-collection "Token Sliding" --parent "Graph Theory"

# Batch operations
zot add --file dois.txt --collection "Batch Import"
zot add --from-manifest manifest.json

# Maintenance
zot doctor
zot sync-cache
zot clean-staging
```

## Architecture

```
DOI/arXiv/ISBN
  → Translation Server when reachable
  → otherwise direct DOI/arXiv/ISBN fallback
Generic URL
  → WSL helper when configured and available
  → otherwise Translation Server /web endpoint
Resolved metadata
  → Duplicate check (DOI-only)
  → PDF download chain (getscipapers → Semantic Scholar → arXiv)
  → PDF verification (magic bytes, page count, aspect ratio, title match)
  → ZotFile rename ({Author}_{Year}_{Title} [Type].pdf)
  → Create attachment item (Zotero API)
  → Store file-sync metadata (md5, mtime)
  → Zip + upload to WebDAV
  → Zotero desktop syncs on next refresh
```

## Components

| Component | Purpose |
|-----------|---------|
| `zot.py` | CLI entry point |
| `lib/config.py` | Config loader (SecretRef-aware) |
| `lib/metadata.py` | Metadata resolver with Translation Server, WSL URL, and direct DOI/arXiv/ISBN fallback paths |
| `lib/zotero_client.py` | pyzotero wrapper (exponential backoff on 429/5xx) |
| `lib/downloader.py` | PDF download chain (branched by input type) |
| `lib/verifier.py` | PDF validation (reject stubs, slides, wrong papers) |
| `lib/renamer.py` | ZotFile pattern engine |
| `lib/webdav.py` | WebDAV upload/download (Zotero zip format) |
| `lib/gdrive.py` | Google Drive scoped search + share links |
| `lib/cache.py` | Local metadata cache (offline search fallback) |
| `lib/doctor.py` | Health checks for all components |

## Configuration

**Zotero secrets** (`AAS_ZOTERO_SECRETS_FILE`, or the dedicated
`~/.config/ai-agents-skills/zotero-secrets.json` default):
- `ZOTERO_API_KEY` — from https://www.zotero.org/settings/keys
- `WEBDAV_PASSWORD` — WebDAV apps password
- `GDRIVE_CREDENTIALS` — Google service account JSON string
- `SEMANTIC_SCHOLAR_API_KEY` — optional Semantic Scholar Graph API key; the
  managed runtime can project it from `AAS_SKILL_SECRETS_FILE`. Environment
  values override the private secrets-file fallback. Never put this key in
  `skills/zotero/config.json`.
- Agent-facing Telegram, Zulip, and other delivery requests all enter the
  authenticated host queue described below. These wrappers never read Remote
  Bridge, OpenClaw, or generic runtime credentials. Channel credentials remain
  exclusively behind the host-worker boundary.

**Authenticated host queue authority** is a separate AAS-native capability
file. Its default host path is
`~/.config/ai-agents-skills/file-delivery-queue.json`. The narrow queue producer
may receive an explicit `AAS_FILE_DELIVERY_SECRETS_FILE` pointer; the host worker
rejects caller-supplied pointers and always resolves this canonical HOME path.
The JSON object has exactly these keys:
`version` (currently `1`), `hmac_key_hex` (64 lowercase hexadecimal digits),
`allowed` (channel-to-exact-target allowlists), `max_job_age_seconds`,
`max_media_bytes`, `replay_ledger_dir`, `replay_retention_seconds`, and
`max_replay_entries`. The replay-ledger field has the single
portable value `aas-host-state:file-delivery-replay`; it resolves relative to
the home containing the restored canonical authority. The resulting replay
ledger location is `~/.local/state/ai-agents-skills/file-delivery-replay`, mode
`0700`, and it must remain outside every agent-writable runtime workspace. The
authority file is mode `0600` and is a backed-up host authority; the replay
ledger is backed-up continuity state. Create a new authority only when an
explicit channel/target allowlist is available—otherwise queue readiness is
`NOT_CONFIGURED`. This file is deliberately unrelated to OpenClaw's
`.openclaw/workspace/.config/file-delivery/secrets.json`, which has a different
schema and must never be copied or projected into the AAS queue authority.
During a cross-home restore, a legacy absolute replay path may be rewritten
transactionally only when it equals the old canonical default; every other
field, including the HMAC key and allowlists, must remain byte-equivalent after
parsing. Arbitrary legacy paths or a conflict with an existing new-home
authority are technical failures, not merge candidates.

The authority also declares `replay_retention_seconds` and
`max_replay_entries`. Retention must be at least
`max_job_age_seconds + 60` and no more than seven days. Before admitting a new
marker, the worker takes the private ledger lock and removes only well-formed
markers whose `used_at + replay_retention_seconds` is strictly before the
current time; fresh, malformed, or changing markers are retained. If the
remaining marker count reaches `max_replay_entries`, delivery fails closed
instead of growing backup state. The bound is 100–100000 entries.

Delivery requests are bounded JSON objects supplied on producer stdin; channel,
recipient, caption, and media path are never accepted in producer argv. Media is
accepted only from the three descriptor-walked runtime roots
`data/exports`, `data/research/zotero/staging`, and `data/calibre/staging`.
Arbitrary workspace paths and host paths are rejected. The host worker binds a
fixed root-controlled OpenClaw entry plus `/usr/bin/node`, passes a minimal
environment, and gives the final in-process OpenClaw adapter its delivery record
through bounded stdin. Agent-controlled `PATH` entries and mutable user launchers
are not candidates, and delivery metadata never appears in the child cmdline.

**Config** (`skills/zotero/config.json`):
- `zotero_user_id` — numeric user ID
- `webdav_url`, `webdav_user` — WebDAV endpoint
- `gdrive_folder_id` — Google Drive folder for Zotero PDFs
- `zotfile_pattern` — PDF rename pattern (default: `{%a_}{%y_}{%t} {[%T]}`)
- `translation_server` — Translation Server URL for DOI/arXiv/ISBN and generic URL metadata
- `wsl_translation_distro` — WSL distro used for URL metadata fallback (default: `Ubuntu-24.04`)
- `wsl_translation_repo` — WSL-local translation-server source checkout for URL metadata fallback (default: `~/zotero-translation-server`)

## Dependency behavior

`lib/metadata.py` can be imported and can detect input types without
`requests`, which keeps offline and unit checks lightweight. Live metadata
lookups for DOI/arXiv/ISBN/URL still require `requests` from
`requirements.txt`. CLI startup imports `pyzotero` through
`lib/zotero_client.py`, so operational CLI use requires the runtime
dependencies to be installed.

## Windows runtime note

For generic URLs, the runtime tries the WSL helper route first when it is
available, then falls back to the configured Translation Server `/web` endpoint.
The WSL route uses `scripts/wsl_url_translate.sh` and a WSL-local source checkout
at `~/zotero-translation-server`.

The Docker-based translation-server path in this skill directory is kept only as a legacy/optional
path. It is not required for the Windows runtime wrapper.

## Testing

```bash
# From the repository root: unit + mocked tests (no credentials needed)
python3 -m unittest tests.test_zotero_webdav_metadata -v

# Full repository test suite
make test
```

## Cron Jobs

Run `scripts/setup-cron.sh` to install:
- **Watch poller** — every 4 hours, auto-attaches PDFs when watches find them
- **Cache sync** — daily at 3am, pulls full library to local cache

## Automation

```bash
# Auto-catalog papers from research/RSS digests
python3 scripts/auto-catalog.py --source all --min-score 80

# Poll watches and attach found PDFs
python3 scripts/watch-poller.py
```
