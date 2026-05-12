# Local Library Profiles

Zotero and Calibre are local-library workflows, but local paths are not
authoritative just because they were discovered. The installer separates
candidate discovery from profile selection so runtime caches, mounted Windows
paths, stale sync folders, and malformed SQLite files are not silently used for
mutation.

Use the read-only audit before configuring or changing library-backed skills:

```bash
make library-profile-audit ARGS="--profile library --json"
```

Mounted Windows profiles can be inspected from Linux, but that evidence is
degraded for native Windows execution:

```bash
make library-profile-audit ARGS="--root /windows/Users/... --platform windows --system-profile windows-mounted --json"
```

System profiles:

| Profile | Executor | Path dialect | Default mutation stance |
|---|---|---|---|
| `linux-local` | Linux | POSIX | Dry-run only until selected in profile config |
| `windows-mounted` | Linux inspecting a mounted Windows home | POSIX `/windows/...` | Read-only by default |
| `windows-native` | Native Windows shell | Windows paths | Requires native Windows verification |

Path authority rules:

1. Discovery lists candidates only.
2. Validation records evidence for each candidate.
3. A path becomes authoritative only after explicit profile selection.
4. CLI overrides may select a profile or read-only candidate, but mutation
   targets must match the selected profile unless the exact path is confirmed
   for that run.
5. If no authoritative local database exists, the profile is
   `local-db-missing`; mutation is blocked or downgraded to remote-only
   behavior where that is safe.

Zotero validation checks:

- SQLite schema readability and item count
- SQLite quick check when safe
- local `storage/` directory presence
- optional Better BibTeX database presence
- cloud-backed, mounted, cache, or malformed classification

Zotero default mutation is API/WebDAV based, not direct SQLite:

1. local DB/storage diagnostic preflight
2. Translation Server metadata resolution when metadata is needed
3. Zotero API mutation bound to explicit library scope
4. WebDAV sync for attachment changes
5. API/WebDAV/local diagnostic verification

Direct Zotero SQLite writes are expert repair only. They require app-closed
checks, DB/WAL/SHM/storage backups, copied working DBs, transaction journals,
and explicit confirmation.

Calibre validation checks:

- `metadata.db` quick check and book count
- runtime/cache root denial
- author/book file-tree consistency
- symlink, mount, and cloud-backed classification
- library fingerprinting before classifying duplicates as aliases, same, or
  divergent libraries

Calibre writes prefer a detected `calibredb` or `calibredb.exe` backend with an
explicit library path. Guarded direct SQLite is fallback only. Windows-mounted
or cloud-backed Calibre libraries are read-only from Linux unless explicitly
opted in with backup, locking, dry-run, and post-write verification.

Target integration rules:

- Choose one canonical repo per run and record its path plus commit/hash.
- Generate an inventory of every selected Codex, Claude, and DeepSeek home
  before writing.
- Store generated profile manifests with checksums/version stamps in selected
  target homes.
- Keep secrets and credentials outside this repo.
- Put safety gates in the shared runtime/core so adapters cannot bypass them.
