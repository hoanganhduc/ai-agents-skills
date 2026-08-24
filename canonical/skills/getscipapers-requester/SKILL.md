---
name: getscipapers-requester
description: Use for external DOI/ISBN/title resolution, manifest creation from pasted text, and paper retrieval after the local library-first workflow does not satisfy the request or the user explicitly opts out of library use.
metadata:
  short-description: External paper retrieval fallback
---

# GetSciPapers Requester


## Windows Runtime Commands

On native Windows, use the managed Windows runner and the native runtime command target. Set `$runtime` to the installed runtime root. Multi-agent installs usually use `%LOCALAPPDATA%\ai-agents-skills\runtime`. Then run:

```powershell
$runtime = if ($env:AAS_RUNTIME_ROOT) { $env:AAS_RUNTIME_ROOT } else { "$env:LOCALAPPDATA\ai-agents-skills\runtime" }
& "$runtime\run_skill.ps1" "skills/getscipapers_requester/gsp_openclaw_helper.py" <args>
```

POSIX examples below use `run_skill.sh` and `.sh` command targets; use the Windows command target above on native Windows.

This is the external retrieval fallback. Do not use it before `zotero` for
normal paper/library requests, and for review tasks that need a paper/book do
not use it before both `zotero` and `calibre` have been checked. "Download" by
itself does not bypass the library-first workflow; bypass only when the user
explicitly says not to check/use the library or confirms outside retrieval after
the local miss/ambiguity is shown.

## When to use

- The paper is not in Zotero
- and, for review tasks, it is also not in Calibre
- The user explicitly says not to check/use the library, or confirms external
  retrieval after the library-first result is reported
- The task is DOI/ISBN/title resolution from external sources
- The user pasted many identifiers and wants batch retrieval

## Base path

- `$AAS_RUNTIME_WORKSPACE/skills/getscipapers_requester/`

Use the managed runtime runner rather than invoking `run_gsp_helper.sh` directly. The runner
sets `OPENCLAW_WORKSPACE`, `PYTHONPATH`, secrets, and workspace-local binaries.

Shared runner:

- `bash "${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh"`

## First-time setup

Before the core commands work, provision the dedicated venv once. This creates
`~/.getscipapers_venv` and installs the getscipapers fork from its default
branch. The run scripts then export `GETSCIPAPERS_BIN` to the venv binary.

```bash
bash "${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh" skills/getscipapers_requester/run_gsp_setup.sh setup
```

On native Windows, use the Windows runner and command target:

```powershell
& "$runtime\run_skill.ps1" "skills/getscipapers_requester/run_gsp_setup.py" setup
```

## Core commands

```bash
bash "${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh" skills/getscipapers_requester/run_gsp_helper.sh run-getscipapers --timeout 180 -- getpapers --doi <DOI>
```

```bash
bash "${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh" skills/getscipapers_requester/run_gsp_helper.sh resolve auto "<title>" --best
```

```bash
bash "${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh" skills/getscipapers_requester/run_gsp_helper.sh make-manifest auto "<text-or-file>"
```

```bash
bash "${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh" skills/getscipapers_requester/run_gsp_helper.sh doctor
```

## Z-Library and DJVU fallback

Use the zlib backend only as an external fallback for books or papers after the
library-first route has not satisfied the request, or when the user explicitly
asks for zlib/external retrieval. For review tasks, check `zotero` and then
`calibre` first unless the user explicitly opts out.

Before relying on the backend, check the local command help:

```bash
getscipapers zlib --help
```

Search before downloading:

```bash
getscipapers zlib --no-proxy --search "<title-or-isbn>" --search-limit 10
```

Download only after an exact match is established or the user selects a
numbered candidate. Do not print zlib configuration values or credentials; when
diagnosing configuration, report only file existence and key names. If the
downloaded file is DJVU, do not attach it to Zotero automatically unless the
user explicitly asks for that attachment. For "send it to me" requests, verify
the file first and send the verified file directly through the requested
channel.

For DJVU verification and reading, prefer DjVuLibre tools:

```bash
sudo apt-get install -y djvulibre-bin
djvused -e n book.djvu
djvudump book.djvu | sed -n '1,80p'
djvutxt book.djvu book.txt
djvutxt -page=<page> book.djvu page.txt
ddjvu -format=tiff -page=<page> book.djvu page.tiff
```

Use `djvudump` for structure, `djvused -e n` for page count, `djvutxt` or
`djvused -e 'print-pure-txt'` for hidden text, and `ddjvu` for rendering or
conversion. If Calibre is useful for conversion or metadata, use it as a
fallback rather than the first verifier; on hosts with user-site Python package
conflicts, run it with:

```bash
PYTHONNOUSERSITE=1 ebook-convert book.djvu book.txt
```

If the DJVU has no usable hidden text layer, render pages with `ddjvu` and then
run OCR with `tesseract` or `ocrmypdf` when available. Avoid treating `mutool`,
PIL, or generic ImageMagick probing as primary DJVU verification paths; they may
fail or hang depending on local delegates. ImageMagick is still useful after a
page has been rendered to TIFF/PNG.

## Workflow

1. If DOI/ISBN is available, use it directly.
2. Otherwise resolve from title or text.
3. When title/text resolution returns multiple plausible matches, show the
   numbered candidates with title, authors, and year when available, then wait
   for the user's selected index before using `--best`, retrieval, attachment,
   send, or review steps. Exact DOI/ISBN requests do not need this
   disambiguation.
4. For many papers, create a manifest first.
5. For large batches, prefer dry-run style validation first.
6. If retrieval fails, report the failure precisely instead of hand-waving.

Watch state is a bounded, fail-closed local transaction. `create-watch`,
`list-watches`, and `update-watch` share one cross-platform lock and use unique
atomic replacements for `watches.json`. A corrupt, oversized, symlinked, or
wrong-shaped watch store is reported without being reset or overwritten; repair
or restore that state before retrying the handoff. Watch IDs include the full
structured canonical watch key, require exact identity before reuse, and are
made unique inside the locked transaction. Matching delimiter-era keys are
migrated in place; collision-shaped nonmatching records are never reused.
An exact-identity `found` record is already successful and is reused without
reopening monitoring; `failed` or `expired` identities remain eligible for a
new watch on retry.
Stores with missing or duplicate IDs are rejected. Every stored record must
also satisfy the complete baseline watch schema, including identity fields,
service list, known status, timestamps, bounded counters, and sent-file hashes;
optional deadline and note history fields are type- and range-checked too.
Stored DOI, ISBN, or search identity must already be in its exact canonical
grammar; `watch_key` must equal either the structured identity hash or its exact
legacy predecessor. `updated_at` cannot precede `created_at`, and deadlines,
last-check times, and note timestamps must remain within the record's declared
lifetime. Store JSON rejects duplicate object members and non-standard
constants such as `NaN`, and never serializes them, including in otherwise
forward-compatible unknown fields. Creation, update, last-check, and note-event
timestamps more than five minutes ahead of the current clock are rejected;
accepted within-skew future records remain mutable by advancing event
timestamps monotonically rather than moving them backward. The
byte budget admits the bridge's full 3,000-item producer maximum even with
bounded 500-code-point non-ASCII labels; individual label, identifier,
initial/last/history note, and service fields remain bounded, and history
records require valid timestamp/note shapes. Persisted string fields reject
line breaks and Unicode control/format/surrogate characters, so helper-created
state remains consumable by the bridge's matching schema.

Generated manifest JSON and DOI-list files use unique same-directory atomic
stages. Pre-existing staging or destination symlinks are replaced as path
entries and are never followed to an external target. Final manifest basenames
use the full SHA-256 digest of kind plus source, so a short-prefix collision
cannot overwrite another retained manifest generation.

`make-manifest` bounded-reads inline, stdin, or regular non-symlink input and
admits at most 3,000 lines/items under a 2 MiB source ceiling, matching the
bridge producer maximum when every DOI reaches the 500-character ASCII grammar
limit. Unicode case-insensitive lookalikes are rejected rather than folded into
another DOI. It caps
metadata-resolution lines and output bytes separately, so bulk free text cannot
fan out into unbounded requests or artifacts. Overlong inline text is validated
as text rather than being mistaken for a filesystem path.

Only `extract` and `make-manifest` interpret their top-level source argument as
stdin or a file. `resolve` queries and individual lines inside an already-read
manifest source are always literal text; a title that resembles a readable
local path is never reopened or sent as that file's contents.

Durability-bearing `make-manifest` and watch verbs parse an explicitly selected
config strictly and require their configured manifest or state directory. They
never switch those outputs to the temporary fallback root; an invalid config or
unavailable configured directory is a nonzero handoff failure. The launcher
exports a workspace-default config when the POSIX `-f` probe sees a regular
file target; that probe may follow a live symlink, while a broken symlink is not
exported. Every durability-bearing verb then performs strict directory-entry
admission and rejects live/broken config symlinks and non-regular entries before
durable state changes. Non-durable verbs retain the legacy permissive config
reader and may use fallback settings; they do not establish manifest/watch
durability. Relative paths inside an accepted strict config resolve against
that config file's directory so producers and pollers do not depend on their
current working directory. Strict config JSON rejects duplicate members and
non-finite constants even in unknown fields; `telegram_max_bytes` must be an
integer from 1 through 2 GiB. Invalid environment overrides use the bounded
default only on non-durable configuration paths.
Every required configured storage directory is then admitted by directory
entry after create/existence, including the `state_dir` that lexically anchors
a manifest directory. Configured and fallback live/broken symlinks, Windows
reparse points such as junctions, and non-directory occupants are never
followed for manifest or watch writes.

Crossref, Google Books, and OpenLibrary metadata requests stay on their exact
documented HTTPS origins, refuse redirects, close every response, and stream
under a fixed byte cap. Declared response framing is enforced, so a truncated
body cannot be accepted merely because its prefix is valid JSON. Real requests
run in isolated workers that the parent
terminates and reaps under a whole-response wall-clock deadline. The deadline
is independent of the socket idle timeout, so a slow-drip response cannot keep
a resolver alive indefinitely; process creation itself may remain
platform-dependent. Search queries and returned candidate counts are
bounded, and malformed response shapes degrade without unbounded iteration.
Metadata JSON rejects non-standard non-finite constants; candidate DOIs use an
ASCII-only grammar, and ISBNs permit only ASCII digits/`X` separated by single
hyphens or spaces before checksum validation. Remote score, year, and type
scalars are bounded before ranking or manifest publication. Remote title,
author, venue, publisher, and type text is HTML-unescaped, NFKC-normalized,
tag-stripped, control/format/surrogate-cleaned, whitespace-collapsed, and
bounded before candidate output. Selected and ranked summaries are sanitized
again through their known-field schema at manifest publication, so a mocked or
future resolver cannot persist extra or structurally unsafe metadata. Exact DOI fields,
bare DOI lines, and DOI URLs retain every grammar-valid suffix character;
free-text extraction alone applies surrounding-prose punctuation heuristics.
