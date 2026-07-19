# Artifact Schema

Every artifact has `schema_version`, stable IDs, UTC timestamps, and explicit
status fields. Validators reject duplicate IDs, broken references, unknown
assertion/freshness values, unsafe paths, and unsupported delivery claims.

## Run artifacts

- `source_registry_snapshot.json`: authority, official domains, access,
  freshness, adapter, capture, caching, and licensing policy.
- `venues.jsonl`: canonical identity, venue type, aliases/acronyms, identifiers,
  publisher/sponsor, and official URL.
- `matches.jsonl`: query, candidate ID, matched field/method, score, confidence,
  and ambiguity group.
- `observations.jsonl`: source, venue, assertion kind, scheme, category or
  collection, value/status, edition/year, official URL, dates, parser, and hash.
- `sources.jsonl`: redacted query, endpoint class, final domain, cache metadata,
  response hash, and freshness status. A cached ICORE access row cannot retain a
  live `verified-current` delivery claim during offline use.
- `proofs.jsonl`: observation, requested URL, actual PDF/PNG final record URLs,
  PDF/PNG and distinct runtime-sidecar paths and hashes, browser/runtime
  versions, strict same-origin attestation, source-specific association adapter,
  actual print settings, full-page measured dimensions/completeness attestation,
  capture time, expected markers, and capture status. The record stays
  `UNVERIFIED`; only a fresh `verify` execution emits the final verdict after
  rebinding the final URLs and actual sidecar metadata to the observation and
  rerunning artifact, completeness, association, and marker checks.
- `delivery.json`: aggregate readiness without upgrading blocked observations.
- `report.md`: human-readable rendering of the validated artifacts.

Store paths relative to the run directory. Write private files atomically and
do not follow symlinks. Never persist credentials, cookies, auth headers,
userinfo, tokenized URLs, raw provider configuration, or licensed bulk data in
portable fixtures.

The hash manifest detects accidental or in-workflow mutation; it is not a
digital signature against an attacker who can rewrite the whole run directory.
Treat same-host processes and the selected output directory as trusted during
capture, and preserve delivered bundles in an access-controlled location.

SCImago- or CiteScore-style categories are separate observation rows under one
venue/source/year. Index membership is never encoded in a quartile field.
