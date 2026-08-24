---
name: rss-news-digest
description: Use when the user wants RSS-based research/news digests, feed management, or feed health checks.
metadata:
  short-description: RSS digests and feed management
---

# RSS News Digest


## Windows Runtime Commands

On native Windows, use the managed Windows runner and the native runtime command target. Set `$runtime` to the installed runtime root. Multi-agent installs usually use `%LOCALAPPDATA%\ai-agents-skills\runtime`. Then run:

```powershell
$runtime = if ($env:AAS_RUNTIME_ROOT) { $env:AAS_RUNTIME_ROOT } else { "$env:LOCALAPPDATA\ai-agents-skills\runtime" }
& "$runtime\run_skill.ps1" "skills/rss-news-digest/run_and_summarize.ps1" <args>
& "$runtime\run_skill.ps1" "skills/rss-news-digest/rss_news_digest.py" <args>
```

POSIX examples below use `run_skill.sh` and `.sh` command targets; use the Windows command target above on native Windows.

## Base path

- `$AAS_RUNTIME_WORKSPACE/skills/rss-news-digest/`

Use the managed runtime runner rather than invoking the RSS script directly.

Shared runner:

- `bash "${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh"`

## Use cases

- get the research RSS digest
- get jobs/events/general/video digests
- list/search/add/edit/disable feeds
- run feed doctor/health checks

## Core execution

```bash
bash "${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh" skills/rss-news-digest/run_rss_news_digest.sh <COMMAND AND ARGS>
```

## Common actions

- `run --tag research`
- `run --all-tags`
- `summarize-sidecars` (raw, sidecar-derived top-item view; not final synthesis)
- `run --tag jobs --max-items 20 --per-feed-limit 5`
- `list-feeds`
- `add-feed "<URL>" --tag research --priority 5`
- `edit-feed "<URL>" --tag research --priority 5`
- `disable-feed "<URL>"` / `enable-feed "<URL>"`
- `remove-feed "<URL>"`
- `backup-feeds --reason "REASON"`
- `list-backups`
- `restore-feeds-backup <managed-backup-name>`
- `export-feeds-tsv --output /tmp/feeds.tsv`
- `import-feeds-tsv /tmp/feeds.tsv`
- `doctor`
- `search-feeds "<query>"`

Verified example shapes:

```bash
bash "${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh" skills/rss-news-digest/run_rss_news_digest.sh run --tag research --max-items 25 --per-feed-limit 5
```

```bash
bash "${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh" skills/rss-news-digest/run_rss_news_digest.sh add-feed "https://example.com/rss.xml" --tag research --priority 5
```

## After execution

If a digest is produced, read the Markdown path reported by the command output
and summarize the top items for the user. Each `rss-<tag>.md` display artifact
has an adjacent `rss-<tag>.json` `digest-items.v1` sidecar for machine handoff.
Summary-sidecar loading binds each fixed filename to the matching top-level
`source` (`rss-<tag>` or `rss-all`) and requires string item titles/links before
any summary/history write. It also requires a consistent successful producer
`run_status`; an all-failed or otherwise unsuccessful sidecar cannot be
laundered into a successful empty summary. Feed responses are fetched without
redirects under a bounded byte limit, and a declared `Content-Length` must
exactly match the complete body actually read. Real fetches run in isolated workers that
the parent terminates and reaps under a whole-response wall-clock deadline,
independent of the socket idle timeout, so repeated small chunks cannot extend a
fetch indefinitely. Process creation itself may remain platform-dependent.

The Markdown, JSON sidecar, and memory stubs are raw external-source artifacts
with `artifact_role: raw_external_digest` and `style_applied: false`. Do not
parse Markdown as a machine protocol or treat these artifacts as the final
user-facing synthesis.

A run where every attempted feed fails exits nonzero; partial feed failure is
reported as degraded. Feed response bytes and total items are bounded across
the run; the aggregate response-byte reservation is shared across parallel
workers and is acquired before another response is opened. Early status errors
release unread reservations, while bounded partial bytes from truncated worker
responses are charged to the shared run budget. Before fetching, the producer
validates the digest directory, every required digest/sidecar leaf, and the
state leaf. It serializes all digest, sidecar, and state payloads before the
first digest write and publishes bridge-visible sidecars last. A handled
publication failure restores the exact prior sidecar generation and state bytes
or absence; rollback failure is explicit. These retry guarantees cover handled
command failures, not abrupt termination, power loss, or same-UID races, because
there is no cross-file crash journal or transaction lock. Existing seen/health
state is read only from a bounded regular non-link-like JSON file; unsafe,
oversized, or malformed state—including duplicate JSON object members and
non-standard `NaN`/`Infinity` constants—is preserved and blocks run/doctor
publication, while only true absence starts empty. An ingest-ledger hit is not
an unconditional skip: its corresponding owned regular memory stub is checked,
a missing stub is rebuilt, and a foreign or link-like occupant fails retryably.
An existing stub is owned only when its complete bounded frontmatter, exact
digest identity and schema, and fixed body shape match this producer; a marker-
only, truncated, or self-asserted foreign file is preserved and fails retryably.
Summary limits of 0, 1, or 2 characters are honored exactly without adding an
ellipsis beyond the requested bound. Feed add/edit operations
validate the complete prospective config before creating a backup or replacing
the current file. Bootstrap, legacy migration, and backup restore distinguish a
genuinely absent config from any existing directory entry; unsafe live or
broken feed/profile symlinks are refused and preserved. Feed TSV imports require exact, unpadded, unique managed
headers and a `url` column; malformed replacement input cannot create a backup
or alter the current config. Exact or tracking-normalized duplicate feed URLs
are rejected as one ambiguous canonical identity before backup or replacement.
Missing optional fields use documented inference,
but nonempty explicit values must use a recognized boolean, a managed tag, an
integer priority from 0 through 10, and bounded kind/notes text. Add and edit
operations use the same range validation; edits never clamp invalid priorities
into a different accepted value. Add/edit/remove/enable/disable validate their
lookup URL as a bounded absolute HTTP(S) URL before tracking normalization, so
an oversized tracking-only suffix cannot alias and mutate a stored feed.
Profile JSON is a bounded, strictly shaped map of canonical names to bounded
text terms. A present malformed, oversized, invalid-UTF-8, or otherwise invalid
profile file fails closed. Terms are case-folded and deduplicated once at
admission under a 50,000-character aggregate ceiling; per-item scoring reuses
that prepared tuple rather than repeating Unicode/HTML/regex normalization.
When `--profile` names no configured profile, both
`run` and `doctor` stop before feed fetching, state saving, or digest output
instead of silently running without the requested filter.

Truncated or malformed HTTP response streams are isolated as errors for their
individual feeds. Other feeds may still produce a degraded successful run; the
broken response cannot abort the entire multi-feed command. XML containing DTD
or entity declarations, more than 500 entries, 20,000 elements or attributes,
or 128 levels of nesting is rejected by a streaming structural precheck before
feed parsing, and feedparser must recognize a feed version even when it recovers
entry-like fragments. Parsed entries are consumed through a bounded worker
window rather than copied into an unbounded run-wide list. Remote title,
summary, and description fields are type-checked and bounded before profile-term
scoring, so an oversized or non-text field cannot amplify matching work or abort
the run.
Timezone-less RFC feed dates are interpreted as UTC, and published timestamps
are emitted in UTC, so freshness and ordering do not depend on the host zone.
Calendar values outside the platform's representable UTC range are treated as
unknown rather than aborting the feed. Timestamps farther than 24 hours in the
future are also normalized to unknown, receive no freshness bonus, emit no
published timestamp, and cannot win sorting through a hostile future date.
Special-source identities that exceed the shared state/stub key ceiling fall
back to a fixed digest of the complete entry identity; they are never truncated
into unstable state or colliding memory-stub IDs. arXiv, YouTube, and
StackExchange IDs are extracted only from their exact recognized hosts and path
shapes under ASCII-explicit path grammars; Unicode case-fold lookalikes and
hostile lookalike URLs or titles use the structured fallback instead.
Stack Exchange question identity includes the canonical site host because
numeric question IDs are site-local; oversized host-plus-ID identities hash the
complete candidate before entering state, stubs, or ingest history.
Generic fallback identities
hash a structured, versioned tuple including source kind, ID, canonical link,
and title, so delimiter-bearing fields cannot serialize to the same key.
URL canonicalization removes configured tracking parameters with conservative
raw-query filtering. Retained segments keep their original structure and order,
and strict-decoding failures preserve the untouched query; encoded delimiters,
blank/bare fields, invalid octets, and reversed repeated values therefore cannot
collapse during item deduplication or feed merge/edit lookup. Host-specific
tracking keys such as YouTube `si`/`feature` are removed only on recognized
YouTube hosts and remain semantic on generic feed URLs.

RSS state and ingest history have independent byte and record bounds. Existing
seen and feed maps that exceed their admission counts fail closed rather than
being silently compacted on read. At the exact boundary, repeated seen keys are
stable-deduplicated newest-first before retention so duplicates cannot evict
distinct history. Digest
ingest ownership lives at
`$AAS_RUNTIME_WORKSPACE/data/research/rss/ingested.json`; the former shared
`data/library/ingested.json` is read only as an optional one-time migration
source, and only its `source: digest` records are admitted. An invalid optional
legacy ledger is skipped with a warning, while a corrupt dedicated RSS ledger
fails closed; this includes live or broken symlink directory entries, which are
never replaced as if the ledger were absent. Dedicated JSON rejects duplicate
object members before any stub or ledger mutation. Dedicated records use exactly
`source`, `id`, and `processed_at`,
with unique canonical control-clean IDs and valid UTC producer timestamps.
Malformed or duplicate digest records in optional legacy input are skipped
rather than weakening the dedicated format. The shared library ledger is never
rewritten by this skill.
The fixed `memory` and `memory/papers` directory entries are admitted with
`lstat` before any stub or ledger work. A link-like papers root—including a
Windows reparse point or junction—is a retryable stub failure and cannot
redirect an owned Markdown write.
Memory-stub filenames retain a readable bounded slug but append the full
SHA-256 digest of the complete canonical item key. Distinct valid source IDs
therefore cannot be forced into one owned stub through a short hash prefix.
Bounded feed-health retention keeps the newest entries, and every feed touched
by the current run is moved to that retained tail even when its state key
predates the stale history around it.

`run_and_summarize.sh` and `run_and_summarize.ps1` invoke
`summarize-sidecars --no-history`; they never parse the Markdown digest and
automatic runs update only `last-summary.md`. That artifact is explicitly
marked `artifact_role: raw_external_digest` and `style_applied: false`.
An operator may invoke `summarize-sidecars` without `--no-history` to create a
timestamped raw history file, but that manual history is operator-managed and
has no automatic retention policy.
Canonical sidecar admission is based on directory entries, not target
existence: live or broken symlinks and non-regular optional/aggregate sidecar
occupants are errors rather than silently absent inputs. The summary command
also admits the digest-directory entry itself before reading or writing, so a
live or broken directory symlink cannot redirect `last-summary.md`, a custom
output, or timestamped history. A custom output must be a direct child of that
admitted directory and cannot alias a canonical RSS sidecar/Markdown pair,
feed/profile config, state, or ingest ledger; collision or parent traversal is
rejected before any output/history mutation.

The feed-backup root is likewise admitted as a real non-symlink directory for
create, list, resolve, restore, and retention rotation. An unsafe root fails
closed before any matching external backup can be written or unlinked. The
complete directory index is bounded at 10,000 total entries and fails closed on
overflow rather than sampling an arbitrary prefix. Every managed-name entry
must be a bounded readable UTF-8 regular file with a plausible UTC filename
timestamp no more than five minutes ahead; live/broken symlinks,
oversized/unreadable content, and far-future names poison the complete index.
Recovery snapshots may contain readable malformed config bytes. Only the 50
newest managed feed snapshots are retained, while the just-created snapshot is
mandatory even when within-skew future names sort ahead.

All owned RSS path admissions use the same link-like predicate: POSIX symlinks
and Windows reparse points/junctions are rejected before summary, stub, state,
or retention work, so a platform-specific link cannot redirect those effects.

## Writing Style Gate

For any user-facing RSS digest summary, load `writing-style-settings.md` before
writing. If the digest item or synthesis is mathematical, TCS, graph-theoretic,
Lean-related, or LaTeX manuscript prose, also load `math-manuscript-style.md`.
The later agent-authored stored summary should record `style_profile_ref`, `policy_hash`,
`active_overlays`, `active_requirement_ids`, and `style_applied`; do not
accept a bare
`style_applied: true` assertion as sufficient evidence.
