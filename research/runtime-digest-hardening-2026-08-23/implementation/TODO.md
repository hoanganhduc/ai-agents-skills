# Runtime Digest Hardening Tasks

- [x] Confirm scope and threat assumptions.
- [x] Inspect skill docs, launchers, complete implementations, consumers, and
      adjacent patterns.
- [x] Reproduce the LLM, filesystem, Unicode, state, cap, and redirect defects.
- [x] Add focused failing tests.
- [x] Implement research-digest fixes.
- [x] Implement structured research/RSS sidecars and bridge validation.
- [x] Make producer publication recover from handled failures and make the
      request ledger transactional and bounded at the full producer maximum.
- [x] Validate prospective RSS add/edit state before backup or replacement.
- [x] Refuse and preserve unsafe existing feed/profile entries during bootstrap,
      legacy migration, and backup restore instead of treating broken links as absent.
- [x] Enforce exact topic/feed import schemas before backup or replacement.
- [x] Reject invalid explicit RSS boolean, tag, priority, kind, and notes values
      instead of coercing them during import.
- [x] Bound RSS state and move ingest ownership to a dedicated ledger with
      read-only legacy migration.
- [x] Treat only true RSS seen-state absence as empty; preserve and refuse
      unsafe, oversized, or malformed owned state before run/doctor publication.
- [x] Restrict bridge RSS inputs to canonical producer-owned sidecars.
- [x] Validate every corpus rebuild artifact before publishing replacements.
- [x] Isolate truncated RSS streams and preserve corpus title-only fallback on
      bounded enrichment failures.
- [x] Make GetSciPapers watch state bounded, fail-closed, atomic, and locked
      across every helper reader/writer.
- [x] Publish GetSciPapers manifest JSON and DOI lists through unique atomic
      stages without following planted symlinks.
- [x] Fail durable helper verbs on bad explicit config/configured storage and
      stop bridge watch spawning at the first failed child.
- [x] Treat only true default-config absence as unconfigured; reject live,
      broken, or non-regular config entries before any durable helper mutation.
- [x] Make watch IDs unique, anchor relative config paths, preserve config-less
      launcher defaults, and select strict topic parsing by file extension.
- [x] Verify persisted manifest/watch artifacts before bridge banking and bound
      helper metadata HTTP origins, bodies, queries, and result counts.
- [x] Reject truncated declared helper metadata bodies even when the received
      prefix is syntactically complete JSON.
- [x] Align watch-store and bridge-verification byte budgets with the full
      3,000-item non-ASCII producer maximum.
- [x] Declare and test the bridge-to-GetSciPapers runtime install dependency.
- [x] Repair missing ledger-owned RSS stubs and reject foreign/symlink
      occupants without advancing seen state.
- [x] Bound manifest input, lines, items, metadata fan-out, and output while
      preserving the full bridge batch.
- [x] Validate last/history watch notes and normalized unique BibTeX/corpus
      identity before durable publication.
- [x] Match every Semantic Scholar batch row to its requested canonical arXiv
      identity before accepting corpus enrichment.
- [x] Bound seen-paper keys/count/bytes with Unicode-safe newest retention.
- [x] Treat only true research seen-state absence as empty; preserve and refuse
      unsafe, oversized, unreadable, or invalid-JSON dedup state before network/output.
- [x] Reject non-object or over-count research seen state while retaining
      tolerant normalization only for individual records inside a bounded object.
- [x] Separate top-level helper source loading from literal metadata queries to
      prevent nested local-file disclosure.
- [x] Make timezone-less RSS dates and emitted timestamps host-zone invariant.
- [x] Replace delimiter-ambiguous watch hashes with structured identity and
      migrate matching legacy keys without collision reuse.
- [x] Validate the complete stored-watch schema before read, mutation, or
      bridge acknowledgment.
- [x] Reject non-standard JSON constants in retained watch fields before any
      create/update mutation and serialize watch state with strict JSON.
- [x] Reject terminal failed/expired watch transitions at the final durable
      recheck while accepting live or found outcomes.
- [x] Reuse an exact-identity found watch when a later batch item forced a
      bridge retry, without duplicating monitoring or notifications.
- [x] Align helper and bridge string/timestamp validation so unrelated valid
      watch records cannot poison later handoffs.
- [x] Keep RSS special-source keys within the shared state/stub identity bound
      by hashing oversized identities without truncation.
- [x] Preserve encoded query structure in RSS item deduplication and feed
      configuration merge/edit identity.
- [x] Serialize generic RSS fallback identity as a structured versioned tuple
      before hashing.
- [x] Require exact top-level producer source for every fixed RSS sidecar before
      summary or history writes.
- [x] Require string title/link fields in RSS summary sidecar items before
      normalization or publication.
- [x] Preserve repeated query-pair order so reversed values remain distinct.
- [x] Reject exact or tracking-normalized duplicate feed URLs before config
      backup or replacement.
- [x] Validate raw add/edit/remove/enable/disable lookup URLs before canonical
      feed matching.
- [x] Bound and type-check remote RSS scoring fields and profile-term scans.
- [x] Fail closed on present malformed topic configuration and use defaults
      only when current and legacy files are genuinely missing.
- [x] Preserve explicit replace/restore recovery while backing up readable
      malformed topic state before replacement.
- [x] Preserve and refuse broken authoritative current/legacy topic entries
      before replace-import or managed restore can publish a new TSV.
- [x] Reject invalid explicit research priority/enabled values for current,
      imported, added, and edited topic state before network or mutation.
- [x] Reject empty, overlong, or duplicate normalized topic identities across
      complete prospective add/edit/import state.
- [x] Reject overlong edit/remove/enable/disable targets and fail a missing
      remove without writing or backing up state.
- [x] Require exact requested watch services in immediate and durable bridge
      acknowledgments.
- [x] Align the bridge manifest byte ceiling with the helper's full 16 MiB
      3,000-item/500-character output contract.
- [x] Reject invalid DOI/ISBN candidates, non-finite JSON numbers, and malformed
      remote ranking scalars before selection or manifest publication.
- [x] Enforce parent-owned whole-response deadlines with isolated, terminated,
      and reaped HTTP workers for research, RSS, and helper metadata requests.
- [x] Bound and concurrently drain helper stdout/stderr, including exact-cap
      manifest protocol framing and large reused watch acknowledgments.
- [x] Validate research/RSS producer outcomes before bridge handoff and RSS raw
      summary/history publication while preserving successful-empty runs;
      require the research producer's exact three-source schema, mandatory
      arXiv attempt, and prerequisite-aware Semantic Scholar skip states.
- [x] Reject DTD/entity-bearing or unrecognized fragment XML before accepting
      feedparser entries, and fully consume BibTeX record interiors before
      corpus publication.
- [x] Require bounded canonical title identity before title-only Semantic
      Scholar enrichment can replace the local corpus fallback.
- [x] Require string Semantic Scholar titles and type-admit optional enrichment
      fields before they can replace validated local corpus values.
- [x] Bind RSS special-source identities and tracking-key removal to canonical
      hosts, retain touched existing feed health, and settle worker reservations
      with actual partial/error byte counts.
- [x] Hash within-link-cap oversized StackExchange question identities before
      state, stub, and ingest-ledger persistence.
- [x] Treat remote calendar timestamps outside representable UTC range as
      unknown without aborting the feed.
- [x] Fail closed on non-exact, duplicate, control-bearing, or invalid-timestamp
      dedicated RSS ingest records while keeping legacy migration best-effort;
      retain a complete 5,000-item run behind saturated history.
- [x] Treat broken dedicated-ledger symlinks as existing unsafe state without
      replacing the entry, writing stubs, or advancing ingest history.
- [x] Reject Unicode case-fold DOI lookalikes, admit the exact 3,000×500 ASCII
      DOI source through the real helper, and preserve it in a compact,
      separately bounded, reloadable bridge ledger.
- [x] Preserve grammar-valid DOI suffix characters at exact metadata, line,
      and URL boundaries while limiting punctuation heuristics to embedded prose.
- [x] Reject garbage-stripped and Unicode-digit ISBN aliases at direct,
      extracted, and remote-candidate admission boundaries.
- [x] Fix the first-build Lean manifest false positive without admitting
      pre-existing manifest drift.
- [x] Structurally bound research/RSS XML entries, elements, attributes, and
      nesting before feedparser, and consume RSS entries through a bounded window.
- [x] Require exact declared response lengths for research/RSS bodies.
- [x] Bound Semantic Scholar title-only corpus fan-out and its shared deadline;
      type-admit ordinary discovery rows and preflight corpus output leaves.
- [x] Preflight deterministic producer outputs, serialize bounded payloads
      before publication, publish consumer sidecars last, and restore exact
      state/sidecar generations after handled final-publication failures.
- [x] Document that abrupt termination, power loss, same-UID races, and corpus
      cross-file write failure are outside the implemented transaction boundary.
- [x] Reject broken canonical sidecar entries and unsafe RSS digest directories
      at both summary and bridge consumers.
- [x] Enforce semantic watch keys, canonical identifiers, and timestamp order.
- [x] Parse and redact Ollama endpoints safely, including IPv6/default ports.
- [x] Hash the complete expanded research title identity instead of truncating
      post-NFKC/casefold deduplication keys, and admit the tagged form only at
      the persisted-ledger boundary so literal source titles cannot forge it.
- [x] Replace 48-bit RSS stub and helper manifest name suffixes with full
      SHA-256 digests and regress equal legacy-prefix collisions.
- [x] Bound TF-IDF construction/load and seed/doctor state with dedicated
      producer-sized resource and semantic ceilings.
- [x] Semantically validate durable watch identifiers, keys, and chronology
      before bridge banking.
- [x] Refuse summary, memory/papers, RSS backup, and research backup root
      symlinks before reads, writes, or retention deletion.
- [x] Fail requested RSS profiles closed on corrupt state or a missing name
      before run/doctor fetch, state mutation, or output.
- [x] Pre-normalize selected profile terms under one aggregate work ceiling.
- [x] Replace partial backup-directory sampling with complete bounded indexes
      and retain the newest 50 RSS/research snapshots.
- [x] Bind Stack Exchange question keys to their site-local host identity.
- [x] Treat far-future RSS dates as unknown consistently in freshness, sort,
      sidecar, and Markdown display.
- [x] Negotiate identity transfer encoding for research HTTP and reject
      unexpected compressed responses before exact length validation.
- [x] Ignore future research seen records and treat remote arXiv/Semantic
      Scholar dates beyond bounded clock skew as unknown.
- [x] Use the current UTC day for research source admission, corpus/run dates,
      and seen-state production regardless of the host timezone.
- [x] Reject duplicate JSON object members across research, RSS, helper, and
      bridge state/protocol decoders before durable or producer mutation.
- [x] Reject bridge `NaN`/`Infinity` across owned state, sidecars, helper
      acknowledgments, and persisted manifest/watch artifacts without banking.
- [x] Apply duplicate-member and non-finite-constant rejection uniformly to
      every research, RSS, helper, and bridge JSON decoder.
- [x] Normalize and bound remote bibliographic text, then re-sanitize known
      selected/ranked fields immediately before manifest publication.
- [x] Strictly parse durable helper config, including duplicate/non-finite JSON
      and the integer 1-byte-through-2-GiB `telegram_max_bytes` contract.
- [x] Reject far-future durable watch event times while keeping within-skew
      reuse/update timestamps monotonic and deadlines future-capable.
- [x] Restrict RSS summary output to non-colliding direct children of the
      admitted digest directory and disable history in automatic wrappers.
- [x] Treat matching backup symlinks, non-regular entries, invalid UTF-8,
      oversized content, and far-future names as complete-index poison.
- [x] Retain the newly created RSS/research snapshot even when within-skew
      future backup names sort ahead of it.
- [x] Require the complete exact RSS producer stub shape before treating an
      existing artifact as owned; preserve marker-only and truncated occupants.
- [x] Reject Windows reparse points/junctions as link-like across all four
      digest runtimes, including native-Windows RSS junction coverage.
- [x] Admit configured and fallback helper storage roots before manifest/watch
      persistence so links and reparse points cannot redirect owned writes.
- [x] Update skill contracts and trust/style ownership.
- [x] Run focused and repository-wide verification.
- [x] Complete fresh-context review.
- [ ] Commit, push, monitor CI, and record remaining audit scope.
