# Runtime Digest Hardening Specification

## Goal

Make tracked-topic and RSS digest generation safe against malformed local
state, oversized or adversarial upstream responses, cross-origin credential
redirects, and Markdown-as-protocol injection while preserving useful offline
fallback behavior.

## Scope

- In scope:
  - `research-digest-wrapper` scoring, summary limits, Unicode deduplication,
    state validation, topic backups, HTTP response bounds, redirect handling,
    source status, and raw-output trust labeling.
  - `rss-news-digest` feed/config validation, aggregate resource bounds,
    handled-failure seen-state recovery, bounded dedicated ingest ownership, raw-output
    trust labeling, and retry semantics.
  - Structured JSON sidecars for research and RSS digest outputs.
  - `digest-bridge` consumption of validated sidecars instead of Markdown,
    canonical identifier handoff, serialized request state, and retry safety.
  - Presentation normalization for externally supplied digest fields.
  - A repository-gate compatibility correction allowing a first Lean build to
    create its missing `lake-manifest.json` while still detecting mutation of
    any pre-existing manifest.
- Out of scope:
  - Treating the runtime digest itself as final styled research prose.
  - A same-UID hostile filesystem actor racing trusted workspace directories.
  - Crash-atomic multi-artifact generations across abrupt process termination
    or power loss; the implemented publication recovery covers handled errors.
  - Redesigning RSS feed management or topic import/export authorization.
  - Live external-network verification.

## Assumptions

- Paper metadata, feed content, HTTP bodies, redirects, and model output are
  untrusted data.
- The managed runtime and workspace parent directories are trusted.
- `OPENCLAW_S2_API_KEY` may only reach the fixed Semantic Scholar origin and
  must never follow a redirect.
- Markdown is display-only; machine handoff uses bounded, schema-validated
  JSON.
- Raw digest artifacts honestly record `style_applied: false`; a later
  agent-authored user summary owns the full writing-style evidence record.

## Interfaces

- `canonical/runtime/skills/research-digest-wrapper/research_digest.py`
- `canonical/runtime/skills/rss-news-digest/rss_news_digest.py`
- `canonical/runtime/skills/digest-bridge/digest_bridge.py`
- `canonical/runtime/skills/getscipapers-requester/gsp_openclaw_helper.py`
- Their corresponding `SKILL.md` contracts and offline unit tests.
- `tests/test_research_digest_wrapper.py`, `tests/test_rss_news_digest.py`,
  `tests/test_digest_bridge.py`, `tests/test_getscipapers_requester_runtime.py`,
  and `tests/test_lean_gate_scanner.py`.
- `make static-check`, `make docs-check`, `make sanitize-check`, and `make test`.

## Acceptance Criteria

- Valid LLM scores work; malformed scores return the exact deterministic
  fallback; valid scores are clamped to 0–100.
- At most the configured number of selected papers invoke Ollama, while every
  selected paper still receives a summary.
- Unicode-only titles remain distinct; malformed seen, seed, and TF-IDF files
  degrade safely.
- TF-IDF build and load paths have dedicated token, distinct-term, vocabulary,
  sparse-entry, term-length, model-byte, document-count, and producer-semantic
  numeric bounds; the builder does not retain every per-document frequency map.
  Seed and doctor state reads use their dedicated producer-sized limits.
- Atomic writes use unique same-directory stages; managed backup restore rejects
  paths, symlinks, and non-regular entries outside the backup contract; backup
  names do not collide. RSS bootstrap, legacy migration, and pre-restore backup
  admission distinguish a missing current config from an unsafe existing entry,
  preserving and refusing live or broken feed/profile symlinks. RSS feed-backup
  and research topic-backup roots are non-symlink directories across create,
  list, resolve, restore, and rotation, so external matching files cannot be
  redirected or deleted. Both use complete indexes under an explicit total
  directory-entry ceiling, fail closed rather than sample on overflow, and
  require every managed-name entry to be a bounded readable UTF-8 regular file
  with a plausible UTC filename timestamp. Live/broken managed symlinks,
  oversized/unreadable content, and timestamps more than five minutes ahead
  poison the complete index before mutation. Readable malformed recovery bytes
  remain restorable. Retention keeps 50 managed snapshots and makes the newly
  created snapshot mandatory even when within-skew names sort ahead of it.
- Every real remote body is read in an isolated worker under an endpoint-specific
  byte cap; the parent enforces the whole-response wall-clock deadline and
  terminates/reaps the worker on expiry. A slow-drip peer cannot evade the
  deadline by staying inside the idle timeout. Process creation itself retains
  the platform caveat documented by Python's subprocess timeout contract.
  A declared `Content-Length` must exactly match the complete received body.
  Research requests explicitly negotiate identity transfer encoding and reject
  non-identity response encoding before applying that framing check.
- S2 GET, POST, and batch requests disable redirects and never forward the API
  key to another origin.
- Complete discovery failure is nonzero/`ok:false`; partial failure is visible
  but successful-empty discovery remains successful. Every bridge and RSS raw
  summary consumer validates the structured producer outcome before publishing
  or banking an empty result; the research bridge contract requires exactly the
  producer's three fixed source keys and two outcome fields per source. arXiv
  must be attempted, while either Semantic Scholar source may report `skipped`
  when its seed/topic prerequisite is absent.
- RSS rejects empty/malformed feeds, caps each response and the aggregate run,
  reserves aggregate response bytes before parallel opens, and validates exact
  TSV headers and explicit boolean/tag/priority/bounded text values plus the
  complete prospective config before either backup or replacement. Exact and
  tracking-normalized duplicate feed URLs are invalid prospective state.
- RSS releases unread worker reservations after early HTTP failures and charges
  bounded bytes carried by truncated `IncompleteRead` chains; existing as well
  as newly added feeds touched by the current run move to the retained state
  tail. DTD/entity-bearing XML and unrecognized entry-like XML fragments are
  rejected before their recovered entries can be published. A streaming
  precheck also rejects more than 500 entries, 20,000 elements or attributes,
  or 128 levels of nesting before feedparser allocation; entry consumption uses
  a bounded worker window rather than a run-wide copy.
- Every mutating RSS lookup validates the raw target as a bounded absolute
  HTTP(S) URL before canonical matching.
- Research topic imports reject ambiguous headers, invalid explicit
  priority/enabled scalars, and empty replacement sets; add/edit operations use
  the same prospective validation before backup or publication. Normalized
  topic identities must be nonempty, bounded, and unique rather than silently
  dropped, truncated, or merged. Mutating lookup targets use the same bound;
  missing remove targets fail without rewriting configuration. Corpus rebuild
  preflights all three existing output leaves before network access, then
  consumes every BibTeX record and regular-entry field/value through its end,
  rejects inner garbage, and validates nonempty normalized unique keys/titles,
  one-to-one corpus coverage, the model, and artifact sizes before writing any
  replacement file. Deterministic preflight or malformed-input failures preserve
  the bundle, but the three later writes are not a crash-safe cross-file
  transaction. Title-only Semantic Scholar enrichment requires a
  bounded canonical string-title identity match; batch enrichment also requires
  a nonempty string title after arXiv identity binding. Non-string titles preserve
  the local title-only fallback, while malformed optional abstract, paper-ID, or
  year fields fall back fieldwise. Title-only fan-out is limited to 25 requests
  under one five-minute operation deadline. Ordinary Semantic Scholar paper
  rows also require a string title and type-admit optional fields rather than
  stringifying containers.
- RSS owns a bounded dedicated ingest ledger, treats the former shared library
  ledger as read-only optional migration input, and preserves foreign records.
  Dedicated records require the exact three-field schema, unique canonical
  control-clean IDs, and valid UTC producer timestamps; malformed or duplicate
  legacy digest records are skipped during best-effort migration. Existing
  dedicated-ledger directory entries, including broken symlinks, are validated
  and never replaced under a false "missing" classification.
- RSS seen/health state falls back to empty only on true absence. Unsafe,
  oversized, unreadable, or malformed current state is preserved and blocks
  run/doctor publication rather than losing deduplication and health history.
  Duplicate JSON members and over-count seen/feed maps fail closed on admission;
  exact-boundary duplicate seen keys are stably deduplicated newest-first before
  compaction.
  Ledger hits revalidate their owned regular stubs; missing stubs are repaired,
  while foreign or symlink occupants fail without advancing seen state.
- Truncated RSS streams are isolated per feed, while bounded Semantic Scholar
  corpus-enrichment failures or slot-level arXiv identity mismatches retain
  title-only BibTeX fallback entries.
- RSS treats timezone-less parsed dates as UTC and emits UTC ISO timestamps, so
  freshness thresholds and order are invariant across host timezones;
  unrepresentable remote calendar values and dates more than 24 hours in the
  future degrade consistently to an unknown timestamp for scoring, sorting,
  and emitted display.
- RSS special-source keys are fixed-size or hashed before entering state/stub
  ownership, so oversized remote IDs—including within-link-cap numeric
  StackExchange IDs—round-trip without truncation or collision.
  Special arXiv/YouTube/StackExchange IDs require their canonical hosts and
  paths; Stack Exchange identity includes its site host because numeric
  question IDs are site-local. Host-specific tracking removal does not erase
  semantic generic URL fields.
- RSS memory-stub basenames append the full SHA-256 digest of the complete item
  key, so lossy readable slugs cannot turn a short-prefix collision into a
  persistent foreign-owner retry failure.
- Generic RSS fallback keys hash a structured, versioned tuple rather than a
  delimiter-concatenated string, preserving distinct untrusted field boundaries.
- RSS raw-summary input binds each canonical sidecar filename to its exact
  producer `source`, uses strict JSON that rejects duplicate members and
  non-finite constants, and validates item title/link string types before
  output or history publication. The summary digest root and memory/papers root
  are also lstat-admitted directories, preventing link-like redirection of
  summaries, history, or owned stubs. A custom summary output must be a direct
  child of the admitted digest directory and cannot alias canonical producer
  sidecars/Markdown, config, state, or ingest ledgers. Bundled automatic
  run-and-summarize wrappers pass `--no-history`; manual timestamped raw history
  is operator-managed and has no automatic retention contract.
- Every digest-runtime JSON decoder rejects duplicate members and non-standard
  `NaN`/`Infinity` constants before publication or durable mutation.
- Existing RSS memory stubs count as producer-owned only when the complete
  bounded frontmatter, exact digest identity/schema, and fixed body shape match;
  marker-only, truncated, or self-asserted foreign occupants remain untouched
  and fail retryably.
- Digest-owned path admission treats POSIX symlinks and Windows reparse
  points/junctions as link-like in research, RSS, bridge, and helper runtimes.
  Required configured helper storage directories and the fallback storage root
  are admitted before any manifest/watch writes.
- RSS URL canonicalization preserves query-pair structure after tracking-field
  removal and retains raw pair/value spelling and order. Strict-decoding
  failures preserve the untouched query, so encoded delimiters, blank/bare
  fields, invalid octets, or reversed values cannot collide in item/feed identity.
- RSS bounds and type-checks remote title, summary, and description fields
  before bounded profile-term scoring.
- RSS rejects malformed, oversized, invalid-UTF-8, or structurally invalid
  profile state, and rejects a missing explicitly requested profile before
  run/doctor fetch, state save, or output publication. Selected terms are
  normalized once under an aggregate character ceiling and reused for every
  item rather than repeating expensive cleanup per term/item pair.
- Producer output leaves and payload sizes are checked before remote work or
  publication, and bridge-visible sidecars are published last. On a handled
  final-sidecar failure, research restores exact prior seen bytes/absence; RSS
  restores exact prior sidecar generation and state bytes/absence, surfacing
  rollback failure. These producer guarantees do not claim crash recovery or
  protection from same-UID races. The bridge request ledger advances only after
  its required external handoff succeeds and retains the complete bounded
  producer maximum.
- Research seen-state uses producer-sized canonical keys, explicit record/byte
  bounds, compact UTF-8 JSON, and newest-history retention under the same cap
  used by its reader. Post-NFKC/casefold expansion beyond the key ceiling uses
  an idempotent tagged SHA-256 representation of the complete normalized title
  rather than truncation. Only the persisted-ledger boundary admits that tag;
  source titles always canonicalize and cannot forge the stored hash namespace.
  Only true absence starts empty; unsafe, unreadable,
  oversized, or invalid-JSON current state is preserved and blocks network and
  artifact publication. Duplicate JSON members are invalid. Readable top-level
  state must be an object within the record cap, while individual record
  normalization remains tolerant. Retained seen dates later than the current
  UTC day are ignored so hostile future state cannot evict the current run at
  saturation. Remote admission, corpus build dates, seen-state production, and
  run dates use the same UTC day independent of the host timezone. Remote arXiv and Semantic Scholar
  dates beyond one day of clock skew are likewise treated as unknown and cannot
  win equal-score crowding or selection through a fabricated future year.
- Present current or legacy research topic configuration is authoritative:
  malformed or unreadable state fails closed, an explicitly empty valid file
  remains empty, and only genuinely missing files activate built-in defaults.
  Ordinary run/list/incremental operations do not replace that state. Explicit
  replace-import and managed restore remain recovery paths: they validate the
  replacement and back up readable malformed current bytes before publishing;
  unsafe or unreadable current files still block recovery. A broken current or
  legacy link is admitted as existing authoritative state and cannot bypass the
  pre-replacement backup/read gate.
- GetSciPapers watch create/list/update operations share a cross-platform lock;
  corrupt, unsafe, oversized, or over-count state is preserved and fails the
  bridge handoff before its DOI is banked. IDs are collision-resistant and
  unique, while configured relative storage remains anchored to the config
  file across different caller working directories. The ledger byte budget
  admits a complete 3,000-record bridge batch at bounded worst-case label and
  identifier sizes, including multibyte labels. Every record is validated
  against the complete baseline identity, service, status, timestamp, counter,
  and sent-file schema; optional deadline and note-history fields are bounded
  and type-checked before any mutation or bridge acknowledgment. DOI/ISBN/search
  identities must be canonical, stored keys must match the exact structured or
  legacy identity, and updated/deadline/check/note timestamps must remain in
  their declared temporal order. Creation, update, last-check, and note-event
  times more than five minutes ahead are rejected, while a within-skew future
  record remains mutable through monotonic timestamp advancement; deadlines
  remain future-capable. Helper and
  bridge apply the same single-line/control-clean string and timestamp bounds,
  including to unrelated records in the shared watch ledger.
- Watch keys hash an unambiguous structured identity and require exact identity
  before reuse; matching legacy keys migrate without admitting their delimiter
  collision shape. Exact-identity `found` watches are reused as successful on a
  partial-batch retry, while `failed`/`expired` records may be recreated.
- GetSciPapers manifest JSON and DOI-list outputs use unique atomic stages and
  cannot follow planted predictable-stage or final-destination symlinks. Their
  basenames use a full SHA-256 kind/source digest so retained history is not
  selected by a 48-bit prefix.
- GetSciPapers manifest source reads, lines, items, metadata-resolution fan-out,
  and output are bounded while accepting the bridge's complete 3,000-item
  batch; its 2 MiB source limit admits 3,000 unique 500-character ASCII DOI
  identifiers, and overlong inline input cannot fail in filesystem probing
  first. DOI grammars reject Unicode `IGNORECASE` lookalikes before
  canonicalization. The bridge uses the same 16 MiB response/artifact ceiling
  and a separate compact 2 MiB request-ledger read/write ceiling, so that exact
  batch survives reload and is not reissued.
- GetSciPapers file/stdin interpretation occurs only at explicit source
  boundaries; direct resolver queries and nested manifest lines remain literal
  and cannot disclose the contents of a path they happen to name.
- Durability-bearing helper verbs reject invalid explicit config and
  unavailable configured storage instead of rerouting to a temporary fallback;
  a genuinely missing workspace-default config is treated as unconfigured,
  while live/broken symlinks or non-regular entries are invalid rather than
  absent. Bridge watch creation stops after its
  first failed child.
- The bridge treats helper output as an acknowledgment proposal, not proof: it
  concurrently drains stdout/stderr under endpoint-specific caps, stops
  overproducing children, verifies the bounded regular manifest artifact and each strict watch record
  through the helper's durable ledger before committing bridge state. The
  final record must remain live or have reached `found`; a concurrent
  `failed`/`expired` transition keeps the request retryable, and both immediate
  and final acknowledgment must retain the requested `services: ["all"]`.
  Retrying a later batch failure reuses an earlier `found` identity rather than
  opening redundant monitoring.
- Helper watch stores reject non-standard JSON constants before mutation and
  duplicate object members before mutation, and serialize with strict
  finite-number JSON, including unknown retained fields. Strict durable config
  rejects those same JSON forms and bounds `telegram_max_bytes` to an integer
  from 1 through 2 GiB.
- Helper metadata discovery is fixed-origin HTTPS with redirects disabled,
  byte- and whole-response-time-bounded closed reads, strict declared-response
  framing, bounded queries/results,
  and tolerant schema handling for Crossref, Google Books, and OpenLibrary. Non-finite JSON numbers,
  invalid DOI/ISBN candidates, and malformed remote score/year/type scalars are
  rejected before ranking or manifest publication. Remote bibliographic text
  is HTML-unescaped, NFKC-normalized, tag/control-cleaned, whitespace-collapsed,
  bounded, and sanitized again through a known-field manifest schema. DOI and ISBN admission is
  ASCII-explicit; ISBN normalization removes only validated single hyphen/space
  separators, never arbitrary upstream characters. Exact structured DOI values
  and whole DOI lines/URLs retain grammar-valid suffix punctuation; only
  embedded free-text extraction applies prose-punctuation disambiguation.
- The bridge declares its GetSciPapers runtime dependency, and install-action
  tests prove a selected bridge installs both runtime skill bodies.
- Research and RSS producers emit bounded `digest-items.v1` sidecars, and the
  bridge refuses missing or invalid sidecars rather than parsing Markdown. RSS
  handoff is restricted to the five canonical producer filenames and matching
  top-level owners. Canonical leaf admission uses directory-entry metadata, so
  broken sidecar symlinks and a symlink/non-directory RSS digest path fail
  closed instead of being mistaken for absent optional inputs.
- Bridge JSON decoding rejects duplicate object members and non-finite
  constants across producer sidecars, helper output, persisted helper artifacts,
  and its owned request ledger. Poisoned ledger bytes are preserved and block
  helper invocation; invalid helper output/artifacts can never advance it.
- arXiv XML discovery is structurally prechecked before feedparser and rejects
  DTD/entities, more than 50 entries, 5,000 elements or attributes, or 64 levels
  of nesting.
- Ollama configuration admits only bounded HTTP(S) URLs with a host, valid port,
  no user information, and no fragment. Invalid endpoints disable calls, IPv6
  is parsed correctly, and doctor output redacts query parameters.
- External display fields are one-line, bounded, control-cleaned, and Markdown
  escaped. Helper JSON metadata is structurally normalized and control-cleaned
  before publication. Artifacts explicitly mark their raw/untrusted role.

## Verification

- Focused offline tests reproduce every listed defect before the fix and pass
  afterward.
- Existing digest, runner, manifest, and target-state tests remain green.
- The real Lean first-build terminal-state test accepts only creation of the
  previously absent manifest; a pre-existing manifest mutation remains drift.
- Repository static, generated-doc, sanitization, and full unit gates pass.
- A fresh-context review finds no blocking correctness, test, or security gap.

## Risks

- Existing Markdown-only digest files require a new digest run before the
  bridge can consume them.
- New atomic files are mode `0600`, intentionally narrowing visibility.
- Disabling redirects can expose an upstream deployment change as a source
  failure; this is preferable to forwarding credentials or following SSRF
  redirects.
