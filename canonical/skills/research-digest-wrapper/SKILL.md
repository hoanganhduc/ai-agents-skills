---
name: research-digest-wrapper
description: Use when the user wants a local research digest from tracked topics or wants to manage tracked research topics.
metadata:
  short-description: Local research digest from tracked topics
---

# Research Digest Wrapper


## Windows Runtime Commands

On native Windows, use the managed Windows runner and the native runtime command target. Set `$runtime` to the installed runtime root. Multi-agent installs usually use `%LOCALAPPDATA%\ai-agents-skills\runtime`. Then run:

```powershell
$runtime = if ($env:AAS_RUNTIME_ROOT) { $env:AAS_RUNTIME_ROOT } else { "$env:LOCALAPPDATA\ai-agents-skills\runtime" }
& "$runtime\run_skill.ps1" "skills/research-digest-wrapper/research_digest.py" <args>
```

POSIX examples below use `run_skill.sh` and `.sh` command targets; use the Windows command target above on native Windows.

## Base path

- `$AAS_RUNTIME_WORKSPACE/skills/research-digest-wrapper/`

Use the managed runtime runner rather than invoking the digest script directly.

Shared runner:

- `bash "${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh"`

## Use cases

- run my research digest
- list tracked topics
- add or edit tracked topics
- doctor the digest setup

## Core execution

```bash
bash "${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh" skills/research-digest-wrapper/run_research_digest.sh <COMMAND AND ARGS>
```

## Common actions

- `run`
- `run --tag TAG --min-priority N`
- `run --use-llm-scoring --use-llm-summary`
- `list-topics`
- `add-topic "<name>" --tag TAG --priority N`
- `edit-topic "<name>" --tag TAG --priority N`
- `disable-topic "<name>"` / `enable-topic "<name>"`
- `remove-topic "<name>"`
- `backup-topics --reason "REASON"`
- `list-topic-backups`
- `restore-topic-backup <backup-name>`
- `export-topics --output /tmp/topics.tsv`
- `import-topics /tmp/topics.tsv`
- `doctor`
- `rebuild-corpus`

With LLM summaries enabled, at most
`OPENCLAW_RESEARCH_MAX_LLM_SUMMARIES` highest-ranked papers invoke Ollama.
Every remaining selected paper still receives the deterministic local summary.

Verified example shapes:

```bash
bash "${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh" skills/research-digest-wrapper/run_research_digest.sh run --tag graph-theory --min-priority 3
```

```bash
bash "${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh" skills/research-digest-wrapper/run_research_digest.sh add-topic "Token sliding" --tag reconfiguration --priority 5
```

## After execution

Read and summarize:

- `$AAS_RUNTIME_WORKSPACE/data/research/alerts/digests/latest-digest.md`

The runtime also writes the machine-readable handoff:

- `$AAS_RUNTIME_WORKSPACE/data/research/alerts/digests/latest-digest.json`

The Markdown and JSON files are raw external-source artifacts. They explicitly
record `artifact_role: raw_external_digest` and `style_applied: false`; do not
treat either as the final user-facing synthesis or parse the Markdown as a
machine protocol. A run where every attempted discovery source fails exits
nonzero with `ok: false`; a partial source failure remains successful with
`degraded: true` and structured source status. Before remote work, the producer
validates every deterministic digest/state output leaf and decodes the existing
state log. It serializes and byte-checks all replacement payloads before the
first artifact write, advances seen-state only after the other required writes,
and publishes the bridge-consumed sidecar last. If that final publication
raises a handled error, it restores the exact pre-run seen bytes or exact
absence; a rollback failure is itself reported. These retry guarantees cover
handled command failures. Abrupt process termination, power loss, and same-UID
filesystem races are outside this contract because there is no cross-file
crash journal or transaction lock.

The seen-paper ledger uses producer-sized canonical title keys, bounded record
and UTF-8 byte budgets, compact non-ASCII JSON, and newest-history retention.
When NFKC/casefold expands a valid title beyond the key ceiling, the complete
normalized identity is represented by a tagged SHA-256 key rather than
truncated. Only persisted-ledger admission recognizes that tagged namespace;
untrusted source titles always undergo canonicalization, so a literal tag-shaped
title cannot impersonate another title's hash. The saved form round-trips
idempotently through persisted state.
It cannot write a state file larger than its own next-read limit. Only true
absence starts an empty ledger; unsafe, unreadable, oversized, or invalid-JSON
current state is preserved and blocks network/output work. Readable regular JSON
must be a bounded object; individual records retain the documented tolerant
field normalization, but duplicate object members, top-level shape, and
record-count overflow fail closed. Producer dates later than the current UTC
day are not admitted into retained seen history, so
a saturated future-dated local ledger cannot evict the current run's required
deduplication keys. Remote admission, corpus build dates, seen-state production,
and run dates all use that same UTC day rather than the host-local calendar.

Tracked topics live at:

- `$AAS_RUNTIME_WORKSPACE/data/research/alerts/topics.tsv`

Topic TSV imports require an exact, unpadded, unique subset of the managed
headers and must include `topic`; nonblank priority values must be integers from
0 through 10 and nonblank enabled values must be recognized booleans. Malformed
or header-only replacement input is rejected before any backup or write, and
add/edit commands apply the same prospective validation. Topic identities must
remain nonempty, within the 500-character bound, and unique after normalization;
they are never silently dropped, truncated, or merged. Edit/remove/enable/disable
lookup targets use the same strict identity bound, and a missing remove target
fails without rewriting state. The `.tsv`
extension always selects this strict parser for imports and restores; legacy
one-topic-per-line input is
accepted only from an explicit `.txt` file. A present current or legacy topic
file is authoritative: malformed or unreadable state makes run/list and
incremental topic commands fail before network access, backup, or replacement,
while an explicitly empty valid file remains empty. Explicit
`import-topics --replace` and `restore-topic-backup` are recovery operations:
they validate the replacement first and preserve readable malformed current
bytes in a managed backup before replacement. Unsafe or unreadable current files
still block recovery because they cannot be backed up; a broken current or
legacy link is existing authoritative state, never an absent-file shortcut.
Defaults apply only when
both files are absent.
The managed topic-backup root is admitted with `lstat` before listing,
restoring, or creating a backup. A live or broken symlink or a non-directory
entry fails closed; backup writes are never redirected through that root.
Native Windows reparse points such as directory junctions are link-like for
this and every other owned file/directory admission.
Backup indexing scans the complete directory only within a 10,000-entry
ceiling and fails closed on overflow instead of selecting an arbitrary prefix.
Every managed-name entry must be a bounded readable UTF-8 regular file with a
plausible UTC filename timestamp no more than five minutes ahead; a live or
broken symlink, oversized/unreadable content, or far-future name poisons the
complete index before mutation. Recovery snapshots remain byte-preserving and
need not parse as valid topics. After each successful snapshot, only the 50
newest managed topic backups are retained, with the just-created snapshot
retained even when within-skew future names sort ahead; default restore
therefore selects from a complete newest-first index.
`rebuild-corpus` validates all three existing output leaves before network work,
then validates the downloaded BibTeX, the nonempty derived corpus, the TF-IDF
model, and every artifact byte limit before writing any replacement. Malformed
remote data and deterministic leaf-preflight failures therefore leave the last
usable offline corpus intact. The three later writes are not a crash-safe or
cross-file transaction; a filesystem failure or abrupt termination during
publication can leave a mixed generation. BibTeX is
consumed structurally through the end of every record and field value; balanced
record wrappers cannot hide HTML, malformed assignments, or trailing tokens.
A title-only Semantic Scholar match is admitted only when its bounded canonical
string title equals the BibTeX title; non-string or mismatched titles retain the
local title-only fallback. Batch rows also require a nonempty string title after
their arXiv identity matches. Optional abstract/paper-ID/year fields are admitted
only under their expected scalar types and otherwise fall back fieldwise.
A bounded
Semantic Scholar enrichment failure degrades to the validated BibTeX title-only
entry instead of aborting the offline corpus fallback. Title-only enrichment is
limited to 25 requests and a shared five-minute operation deadline; exhausted
capacity falls back locally instead of starting more calls. Batch enrichment is
accepted only when each returned `externalIds.ArXiv` canonically matches the
requested arXiv identity in that response slot. BibTeX keys and titles
must remain nonempty after external-text normalization, normalized keys must be
unique, and the derived corpus must preserve exactly one row for every accepted
entry before publication. Ordinary Semantic Scholar discovery rows likewise
require a nonempty string title and type-admit their URL, abstract, authors,
external IDs, and publication date instead of stringifying containers.
Remote publication dates later than a one-day clock-skew allowance are not
admitted: Semantic Scholar keeps the paper with blank display/date order, while
arXiv entries without an admissible recent date are skipped. Equal-score future
rows therefore cannot crowd current work out by date sorting.
TF-IDF construction rejects more than 1,000,000 source tokens, 100,000 distinct
terms, a 50,000-term vocabulary, or 150,000 retained sparse entries. Terms are
bounded to 3,000 characters and the model has a dedicated 16 MiB read/write
limit. The builder re-tokenizes one document at a time after vocabulary
selection instead of retaining every document-frequency map; the reader
requires the exact model schema, sorted unique vocabulary, matching numeric
maps, bounded document count, producer-possible positive IDF/centroid ranges,
and finite values before use. Seed state has its own 256 KiB and
100-record admission limits, and doctor reads digest state under the digest
state limit rather than the broader generic JSON ceiling.
XML discovery responses containing DTD or entity declarations, more than 50
entries, 5,000 elements or attributes, or 64 levels of nesting are rejected by
a streaming structural precheck before feed parsing. Declared `Content-Length`
must equal the complete body actually read. Real network reads run in an
isolated worker that the parent terminates and reaps under a whole-response
wall-clock deadline, in addition to connect/read timeouts and byte caps, so a
slow-drip peer cannot keep a source request alive indefinitely. As with Python's
subprocess timeout contract, process creation itself may not be interruptible on
every host platform.
Research HTTP requests explicitly negotiate `Accept-Encoding: identity` and
reject an unexpected non-identity `Content-Encoding`. Exact `Content-Length`
framing therefore compares the delivered identity body rather than Requests'
transparent decoded bytes against a compressed wire length.

The optional Ollama endpoint must be a bounded HTTP(S) URL with a host, valid
port, no user information, and no fragment. Invalid endpoints disable probing
and requests without crashing; doctor output omits query parameters so embedded
tokens are not displayed. IPv6 and default HTTP/HTTPS ports are parsed from the
validated URL rather than split heuristically.

## Writing Style Gate

For any user-facing digest summary, load `writing-style-settings.md` before
writing. If the digest item or synthesis is mathematical, TCS, graph-theoretic,
Lean-related, or LaTeX manuscript prose, also load `math-manuscript-style.md`.
The later agent-authored stored summary should record `style_profile_ref`, `policy_hash`,
`active_overlays`, `active_requirement_ids`, and `style_applied`; do not
accept a bare
`style_applied: true` assertion as sufficient evidence.
