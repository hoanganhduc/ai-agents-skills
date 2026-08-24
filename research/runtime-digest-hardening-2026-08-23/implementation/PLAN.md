# Runtime Digest Hardening Task Plan

## Context

The audit reproduced an unconditional LLM-scoring `NameError`, ignored summary
limits, Unicode deduplication loss, wrong-shaped state crashes, a predictable
temporary-file symlink overwrite, unrestricted backup restore, unbounded HTTP
bodies, and S2 API-key forwarding across redirects. `digest-bridge` also parses
untrusted Markdown as a machine protocol. Repository-wide verification also
exposed a Lean strict-gate false positive when the first trusted build creates
the previously absent `lake-manifest.json`.

## Steps

1. Add focused failing tests for the reproduced filesystem, logic, state,
   redirect, size-bound, source-status, and protocol-injection cases.
2. Harden the research digest while preserving deterministic local fallbacks.
3. Emit structured research/RSS sidecars and make the bridge validate them.
4. Normalize display-only external content and document raw/style ownership.
5. Harden handled-failure producer publication, the transactional bridge ledger, and prospective RSS config
   validation, including exact import schemas, dedicated RSS ownership,
   maximum-size, and retry regressions.
6. Correct the narrowly allowed first-build Lean manifest transition.
7. Run focused and repository-wide verification, then fresh-context review.
8. Commit, push, monitor CI, and resume the broader repository audit.

## Decisions

| Decision | Rationale | Status |
|---|---|---|
| Disable all redirects on fixed remote calls | Prevent S2 custom-header leakage and redirect SSRF | accepted |
| Stream and cap decompressed response bytes | Result-count limits do not bound network memory | accepted |
| Isolate whole-response wall time | Parent-enforced worker subprocess deadlines can terminate and reap a peer that continuously drips through socket idle timeouts | accepted |
| Use JSON sidecars as the bridge protocol | Escaping Markdown cannot make it a reliable machine format | accepted |
| Mark runtime digest output raw and unstyled | The runtime cannot honestly manufacture agent style evidence | accepted |
| Restrict only managed backup restore | Arbitrary topic import/export remains an explicit separate API | accepted |
| Admit RSS config by directory entry | Broken feed/profile symlinks must not be mistaken for missing bootstrap or restore targets | accepted |
| Do not add a partial write lock | Concurrent topic transactions are not a declared contract | accepted |
| Serialize bridge request handoffs | Its ledger and the helper watch store form one side-effect boundary | accepted |
| Restrict RSS handoff to five named owners | Arbitrary sibling JSON files are not producer-authorized inputs | accepted |
| Move RSS ingest history to a dedicated ledger | The shared library ledger contains foreign records and is migration input, not RSS-owned state | accepted |
| Validate corpus rebuild before publication | A malformed successful response must not replace the last usable offline artifacts | accepted |
| Lock and bound the helper watch store | Bridge success requires a durable watch that no concurrent helper writer can erase | accepted |
| Reject non-finite watch-store JSON | A poisoned unknown field must fail before a bridge-triggered watch mutation | accepted |
| Atomically publish helper manifests | Predictable stages and direct DOI-list writes can follow planted symlinks | accepted |
| Forbid fallback for durable helper verbs | A banked watch must remain in the configured store used by later pollers | accepted |
| Admit helper config with lstat | A live or broken default-config symlink must fail before durable state mutation, not look absent or be followed | accepted |
| Stop watch creation on first failure | Retrying thousands of doomed 30-second subprocesses violates the bounded handoff | accepted |
| Anchor relative helper paths to config | Producer and poller durability must not depend on caller cwd | accepted |
| Select topic format by extension | Malformed TSV must never fall through to permissive legacy parsing | accepted |
| Admit topic recovery state by directory entry | A broken authoritative legacy link must block replace/restore instead of looking absent | accepted |
| Verify helper persistence before banking | Exit zero plus echoed JSON does not establish a durable handoff | accepted |
| Bound helper metadata origins and bodies | Public resolver input must not follow redirects or allocate unbounded responses | accepted |
| Enforce helper metadata response framing | A short declared body must not become trusted because its prefix is complete JSON | accepted |
| Size the watch ledger to bridge admission | A valid maximum watched batch must not deadlock halfway through durable creation | accepted |
| Declare the bridge runtime dependency | A digest-only install must include the helper that performs its manifest/watch handoff | accepted |
| Revalidate ledger-owned RSS stubs | Ledger presence alone cannot prove a deleted or replaced memory artifact still exists | accepted |
| Bound helper manifest ingestion | Standalone text/file/stdin input must not allocate or fan out without a bridge-compatible ceiling | accepted |
| Reject ambiguous corpus identity | Normalization-empty or duplicate BibTeX keys can silently collapse corpus entries | accepted |
| Bind batch enrichment to arXiv identity | Positional Semantic Scholar rows can be reordered or duplicated | accepted |
| Compact seen history under its read cap | Unicode escaping and unbounded keys can make a writer produce state its reader rejects | accepted |
| Fail closed on invalid research seen state | Replacing unreadable dedup state with an empty run reissues papers and loses history | accepted |
| Reject over-count research seen state | A bounded reader must not erase valid durable history merely because its record count exceeds admission | accepted |
| Fail closed on invalid RSS seen state | Replacing corrupt or unsafe state with an empty run loses deduplication and feed-health history | accepted |
| Interpret files only at source boundaries | Nested literal resolver lines must never reopen local paths and disclose their contents remotely | accepted |
| Treat timezone-less feed dates as UTC | Freshness scores and display timestamps must not change with the host timezone | accepted |
| Hash structured watch identity | Delimiter-bearing search/service fields must not collapse distinct durable watches | accepted |
| Hash oversized RSS special identities | Producer keys must round-trip unchanged through seen and ingest ledgers and remain distinct in stubs | accepted |
| Structure generic RSS fallback identity | Delimiter-bearing remote fields must not collapse distinct items before SHA-256 | accepted |
| Bind RSS sidecar owner to filename | Swapped valid sidecars must not be silently relabeled under another tag | accepted |
| Type-check RSS summary items | Corrupt local JSON objects/lists/scalars must not be coerced into valid-looking source data | accepted |
| Re-encode normalized RSS query pairs | Decoded delimiters must not collapse distinct items or feed rows into one identity | accepted |
| Preserve normalized RSS pair order | Reversed repeated parameters may be semantically distinct opaque feed/item URLs | accepted |
| Require unique canonical feed URLs | Duplicate normalized rows make edit/remove ambiguous and spend fetch budget twice | accepted |
| Validate mutating feed lookup URLs | Oversized tracking-only targets must not alias and mutate a valid stored feed | accepted |
| Bound RSS scoring inputs | Full response-sized or non-text fields must not amplify term scans or abort a run | accepted |
| Validate every stored watch field | A well-shaped top-level ledger cannot make malformed identity, status, timestamp, counter, or history records durable | accepted |
| Recheck durable watch outcomes | A watch that fails or expires during a serial batch must not be banked as a successful handoff | accepted |
| Share the helper/bridge watch schema | Helper-valid unrelated records must not poison later bridge verification | accepted |
| Require the requested watch service | A durable paper watch with no `all` service is not a completed bridge handoff | accepted |
| Reuse found watches across batch retries | All-or-nothing bridge banking can retry an earlier item whose watch already completed successfully | accepted |
| Align bridge/helper manifest byte caps | Every valid 3,000-item/500-character helper artifact must remain bridge-admissible | accepted |
| Bound helper protocol pipes while draining both | `capture_output` can allocate without limit or deadlock on a noisy child before the bridge validates JSON | accepted |
| Treat one terminal newline as protocol framing | The helper's exact-cap JSON artifact remains admissible when its CLI printer appends CRLF/LF | accepted |
| Validate producer outcomes at every consumer | Failed discovery must not be laundered into a successful empty bridge handoff or RSS raw summary | accepted |
| Bind research outcome keys to its fixed producer schema | Invented success sources must not dilute complete failure of every real discovery source | accepted |
| Align producer skip states with bridge validation | Internal empty-topic production emits a legitimate skipped S2 search while arXiv remains mandatory | accepted |
| Reject DTD/entity-bearing feed XML | Feedparser expands internal entities before normalized item bounds apply | accepted |
| Require a recognized feed version | Feedparser can recover entries from a bare XML fragment that is not a feed | accepted |
| Fully consume BibTeX record interiors | Balanced top-level records can still hide malformed fields or trailing gateway text | accepted |
| Bind title-only corpus matches | The first upstream match row cannot be trusted to describe the requested BibTeX title | accepted |
| Type-admit corpus enrichment fields | Container-shaped remote values must not become trusted local title, abstract, ID, or year text | accepted |
| Bind special RSS IDs and tracking rules to hosts | Lookalike URLs and generic `si`/`feature` parameters must not alias canonical sources | accepted |
| Bound every special RSS identity | A within-link-cap numeric StackExchange ID can still exceed the shared state/stub key ceiling | accepted |
| Reject unrepresentable RSS dates | `calendar.timegm` can produce a value that UTC datetime serialization cannot represent | accepted |
| Bind Stack Exchange IDs to their site | Numeric question IDs are site-local and must not collapse math/cs/etc. questions into one owned key | accepted |
| Reject far-future RSS dates consistently | A representable hostile year can win freshness/sorting or leak back through raw display after timestamp rejection | accepted |
| Settle RSS worker reservations by actual bytes | Early status errors should release unread capacity, while truncated partial buffers must still spend the aggregate budget | accepted |
| Refresh touched feed-health order | Newly updated existing feeds must survive newest-tail state compaction at saturation | accepted |
| Make the dedicated RSS ingest ledger strict | Normalizing extra fields, duplicate IDs, controls, or invalid timestamps can silently rewrite corrupt owned state | accepted |
| Admit dedicated RSS ledgers by directory entry | A broken ledger symlink must fail closed instead of being mistaken for an absent file | accepted |
| Size RSS ledger compaction for the run producer | A valid 5,000-item run must fit behind a saturated 50,000-record ledger | accepted |
| Make DOI grammar explicitly ASCII | Unicode `IGNORECASE` lookalikes must not fold distinct URL paths into the same paper request | accepted |
| Separate exact DOI admission from prose extraction | Grammar-valid suffix punctuation is identifier data in metadata and URL paths, but may delimit an embedded prose citation | accepted |
| Make ISBN admission explicitly ASCII and syntax-first | Deleting arbitrary remote characters can collapse malformed metadata into a checksum-valid identifier | accepted |
| Align exact maximum handoff capacity | The real 3,000×500 ASCII DOI source and compact bridge ledger must both survive their next reader | accepted |
| Fail closed on present invalid topics | Ordinary config operations must not turn local errors into defaults, network queries, or implicit replacement | accepted |
| Preserve explicit topic recovery | Validated replace-import and managed restore back up readable malformed state before replacement | accepted |
| Reject invalid explicit topic scalars | Silent defaulting/clamping can activate malformed topics and trigger network work | accepted |
| Require unique bounded topic identity | Empty, overlong, or normalization-colliding add/rename input must not delete or merge rows | accepted |
| Validate mutating topic lookups | Overlong or missing targets must not alias, rewrite, or delete another topic | accepted |
| Validate remote identifier candidates | Malformed DOI/ISBN/non-finite scalar metadata must not become a durable manifest selection | accepted |
| Admit only missing-to-created Lean manifest context | First build is legitimate; changing an existing manifest is still evidence drift | accepted |
| Bound XML structure before feedparser | Body-byte caps do not bound parser object count, attribute fan-out, or nesting | accepted |
| Enforce exact declared body length | A syntactically valid prefix must not make a truncated response trustworthy | accepted |
| Bound corpus title-match operation | Per-call limits do not bound thousands of sequential title-only enrichment requests | accepted |
| Bound TF-IDF construction and load | A byte-valid maximum corpus can amplify into hundreds of megabytes of transient maps or a high-cardinality/semantically invalid model | accepted |
| Size seed and doctor state by producer contract | A broad generic JSON ceiling admits needless allocation beyond these small state shapes | accepted |
| Preflight deterministic producer leaves | Known bad output occupants must fail before network work or partial handled publication | accepted |
| Publish consumer sidecars last and roll state back | Handled final-publication failures must not expose a new completion marker or advance durable deduplication | accepted |
| Narrow producer transaction claims | No crash journal or cross-file lock means abrupt termination and same-UID races remain out of scope | accepted |
| Admit canonical sidecars by directory entry | Broken symlinks must not masquerade as absent optional producer output | accepted |
| Validate semantic watch identity and time order | Shape-valid records with stale hashes, noncanonical identifiers, or impossible chronology are not durable acknowledgments | accepted |
| Parse Ollama URLs structurally | String-splitting mishandles IPv6/ports and diagnostic query output can expose credentials | accepted |
| Hash expanded research dedup keys | Truncating after NFKC/casefold can collapse distinct bounded producer titles | accepted |
| Separate source and stored-key admission | A literal source title must not forge the tagged persisted-hash namespace | accepted |
| Use full digests for owned artifact names | Lossy slugs plus 48-bit prefixes permit practical collision-driven retry denial or history overwrite | accepted |
| Admit writable directory roots with lstat | Summary, memory/papers, and backup roots must not redirect writes or backup retention deletes through symlinks | accepted |
| Fail closed on an explicit RSS profile | Ignoring corrupt or missing requested profile state silently broadens a filtered run | accepted |
| Prepare RSS profile terms once | Per-term cleanup inside per-item scoring turns individually bounded maxima into minutes of CPU | accepted |
| Complete and retain backup indexes | Arbitrary 10,000-entry prefixes can hide the newest restore point and defeat retention | accepted |
| Negotiate identity encoding for research HTTP | Exact response-length framing is meaningful only for the delivered representation, not an implicitly decompressed body | accepted |
| Reject future research dates at durable and remote boundaries | Far-future ledger or source dates can evict current history and win equal-score selection | accepted |
| Use one UTC research calendar | Local-midnight differences must not change source admission, corpus dates, run dates, or retained seen history | accepted |
| Reject duplicate JSON members at owned boundaries | Last-member-wins parsing can validate and then silently rewrite a different durable or producer object | accepted |
| Reject non-finite JSON at every digest decoder | State, sidecars, worker/helper protocols, configuration, and persisted artifacts form strict producer and durable boundaries | accepted |
| Normalize helper bibliographic text twice | Resolver output and mocked candidates must not persist tags, entity tricks, controls, or unknown fields in manifests | accepted |
| Bound strict helper configuration | Duplicate/non-finite config and unbounded `telegram_max_bytes` can alter durable behavior before manifest/watch writes | accepted |
| Reject hostile future watch events | Durable acknowledgments cannot come from the far future, while within-skew records must remain monotonically mutable | accepted |
| Restrict RSS summary output ownership | A direct-child output cannot traverse into or collide with another producer's config, state, sidecar, or ledger | accepted |
| Disable history in automatic RSS wrappers | Unbounded timestamped history is outside automatic retention; manual history remains an operator-managed action | accepted |
| Validate every managed backup entry | A matching symlink, unreadable/oversized file, or far-future filename cannot be silently omitted from restore/retention authority | accepted |
| Make the new backup retention-mandatory | Within-skew future names must not cause the snapshot just created for a mutation to be immediately deleted | accepted |
| Require the complete RSS stub producer shape | Two marker lines do not prove ownership of an existing foreign or truncated artifact | accepted |
| Treat Windows reparse points as link-like | Junctions can redirect owned writes and retention even though `S_ISLNK` is false on Windows | accepted |
| Admit helper configured and fallback storage roots | A symlink or junction at a configured/default root must not redirect manifest or watch persistence | accepted |

## Verification Plan

| Check | Command or method | Expected result |
|---|---|---|
| Focused regressions | `python3 -m unittest tests.test_research_digest_wrapper tests.test_digest_bridge tests.test_rss_news_digest tests.test_getscipapers_requester_runtime tests.test_lean_gate_scanner -v` | all pass offline |
| Runner integration | relevant `test_posix_runtime_runner` cases | credential routing unchanged |
| Static/docs/sanitize | documented Make targets | all green |
| Full suite | `make test` and GitHub Actions | all jobs green |
| Fresh review | decision/security review on final diff | no blocking findings |
