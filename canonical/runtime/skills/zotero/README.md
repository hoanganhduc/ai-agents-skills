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

**Secrets** (`OPENCLAW_SECRETS_FILE` or `AAS_SECRETS_FILE`; by default the runtime looks for `.secrets.json` in the installed runtime workspace):
- `ZOTERO_API_KEY` — from https://www.zotero.org/settings/keys
- `WEBDAV_PASSWORD` — WebDAV apps password
- `GDRIVE_CREDENTIALS` — Google service account JSON string

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
