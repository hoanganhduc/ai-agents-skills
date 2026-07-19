---
name: venue-ranking-evidence
description: Use when identifying a journal, conference, or proceedings series from a partial name, acronym, alias, ISSN, or source ID; preserving source-specific rank, quartile, metric, classification, membership, or coverage observations; or proving that the public ICORE detail page displayed one ICORE claim. ICORE alone has built-in live edition discovery, currentness verification, and browser proof. Nine other built-ins accept authorized normalized imports that cannot establish latest status; Conference Ranks is legacy. Return every plausible match and never conflate index membership with ranking.
---

# Venue Ranking Evidence

Resolve venues, report source-specific observations, and preserve official-page
proof. Use the runtime for deterministic matching, provenance, and artifacts;
use judgment only to explain ambiguity and provider limitations.

The built-in live/freshness/proof path is ICORE-only. CCF, SCImago, Scopus,
Web of Science Master Journal List, JCR, JUFO, the Norwegian Register, DOAJ,
and Conference Ranks accept authorized normalized CSV/JSON imports only. Those
imports remain `currentness-unconfirmed`; Conference Ranks is additionally
`secondary-legacy` and must never be presented as latest.

## Required workflow

1. Run `doctor` when runtime or browser readiness is unknown.
2. Run `sources list` or `sources show` to confirm source capabilities, access,
   freshness semantics, and proof policy.
3. Run `lookup` with the user's text and requested sources. Return every
   plausible match with its match method; never silently choose an acronym or
   fuzzy collision.
4. Separate observations by source, assertion kind, scheme, category or
   collection, and metric year or edition. Never summarize a venue as merely
   “Q1”, “ranked”, “Scopus”, or “WoS”.
5. If multiple matches remain and proof is requested, show a numbered list and
   obtain an explicit selection before running `proof`.
6. Run `proof` only when the source has a reviewed proof-association adapter and
   a public access class, then run `verify`. Treat the bundle as proved only
   when verification returns `VERIFIED`. The reviewed proof adapter covers only
   the public unauthenticated ICORE detail page; the other nine source IDs are
   authorized normalized import-only and proof-ineligible.
7. Report freshness, access, and evidence gaps. Use `incomplete analysis` when
   material requested sources remain unchecked or blocked.

Read these references as needed:

- `references/source-policy.md` before live lookup, refresh, or source addition.
- `references/matching-policy.md` for ambiguous identities or aliases.
- `references/artifact-schema.md` when inspecting or consuming run artifacts.
- `references/proof-contract.md` before capturing or validating proof.
- `references/privacy-licensing-policy.md` for authenticated, subscription, or
  restricted providers.

## Runtime

POSIX:

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" \
  skills/venue-ranking-evidence/run_venue_ranking_evidence.sh \
  lookup --dir /path/to/run --query "Theoretical Computer Science" --offline
```

Windows PowerShell:

```powershell
& "$env:AAS_RUNTIME_ROOT\run_skill.ps1" `
  "skills/venue-ranking-evidence/run_venue_ranking_evidence.ps1" `
  lookup --dir "$env:TEMP\venue-ranking-run" --query "TCS" --offline
```

Windows CMD:

```bat
"%AAS_RUNTIME_ROOT%\run_skill.bat" "skills/venue-ranking-evidence/run_venue_ranking_evidence.bat" smoke
```

The equivalent PowerShell runner may target
`skills/venue-ranking-evidence/run_venue_ranking_evidence.ps1`.

The POSIX wrapper honors `VENUE_RANKING_EVIDENCE_PYTHON`, then
`AAS_RUNTIME_PYTHON`, before falling back to `python3` and `python`. Browser
proof capture requires Chromium, Chrome, or Edge. Proof marker verification
also requires Poppler's `pdftotext` on `PATH` (`poppler-utils` on Debian/Ubuntu;
Poppler via Homebrew on macOS; or a Poppler distribution exposing
`pdftotext.exe` on Windows). Run `doctor` to inspect both prerequisites.

The built-in live bulk, edition-freshness, and browser-proof adapters cover only
ICORE. The other nine built-in entries describe authority and access policy and
accept an authorized normalized CSV/JSON interchange through `--data-file`;
they have no live query, raw-provider parser, latest-edition verifier, or proof
adapter. Use a reviewed user descriptor with explicit field mappings for another
authorized export layout. `--records-file` is reserved for synthetic fixtures.

Useful verbs:

- `doctor`
- `sources list|show|check`
- `sources validate|add --descriptor <file> --registry-dir <dir>`
- `lookup --dir <run> --query <text> [--source <id> ...]`
- `lookup ... --registry-dir <dir> --data-file <source-id>=<csv-or-json>`
- `proof --dir <run> --observation-id <id>`
- `report --dir <run>`
- `verify --dir <run>`
- `cache status|refresh|purge [--cache-dir <private-cache>]`
- `purge --dir <run>`
- `smoke`

Live operations require both `--allow-network` and an explicit
`--allow-source <id>`. Only a successful live ICORE discovery/export can produce
`verified-current`. Offline ICORE cache reads never claim currentness: rows from
the cache's formerly current edition are `currentness-unconfirmed`, while rows
whose own edition is historical remain `verified-historical`. Declarative
imports for every other source are also `currentness-unconfirmed`.

## Output contract

For each candidate, show:

- canonical title, venue type, identifiers, aliases, and match rationale;
- one row per source observation, including assertion kind, scheme, category or
  collection, value/status, edition or metric year, official URL, and freshness;
- warnings for historical, legacy, stale, ambiguous, authenticated, or blocked
  evidence;
- proof bundle path and verification verdict when requested.

Index membership, collection coverage, quartile, percentile, impact metric,
conference class, and national publication level are different assertion kinds.
Do not convert or compare their values as though they share one scale.

## Proof rules

Browser Print-to-PDF represents browser print rendering, not a pixel-identical
screen. Preserve the raw official-page PDF, a full-page PNG screen reference
with measured-dimension completeness attestation, separate PDF/PNG runtime
sidecars, and a manifest entry. The manifest must copy actual final URLs,
browser/runtime versions, media/page settings, output dimensions, and capture
settings from those sidecars rather than assume them. `proof` reports `captured`
or `capture-incomplete`; only the subsequent `verify` command can return
`VERIFIED`. Never inject a cover sheet into the official-page PDF.

Do not call a login screen, CAPTCHA, access-denied response, blank render,
skeleton page, missing expected marker, or stale unsupported cache “proof”. Do
not bypass access controls or persist credentials or cookies. The current
runtime does not consume any user session: proof is limited to the public
unauthenticated ICORE detail page. Fail closed when Chromium would run
with `--no-sandbox`; use an unprivileged, sandbox-capable browser environment.
Venue proof enables strict same-origin interception for the main document and
all subresources, comparing scheme, canonical hostname, and effective port.
Authenticated or licensed browser-profile capture is deliberately not
implemented; report the applicable source as blocked instead of implying that
an existing browser session will be reused.

## Source extension boundary

Permit user-added declarative CSV or JSON sources only after descriptor
validation. Descriptors may declare mappings, official HTTPS domains, edition
semantics, and display markers; they may not claim a built-in reviewed proof
association adapter or name arbitrary Python imports,
subprocesses, JavaScript, shell commands, or credential material. New live HTML
adapters require reviewed built-in code and fixtures.

For a registered declarative source, pass each authorized local export as
`--data-file source-id=/path/to/export.csv` (or JSON). Imported rows remain
`currentness-unconfirmed`; no current declarative descriptor can establish
latest status or enable proof. A filename, user-supplied year, descriptor
`may_claim_latest` value, or retrieval timestamp is not a latest-data proof.
Built-in `user-export` sources expect normalized columns named after the
artifact fields: required `canonical_title` and `value`, with optional
`venue_id`, `venue_type`, `aliases`, `issn`, `eissn`, `provider_id`,
`assertion_kind`, `scheme`, `category`, `collection`, `edition`, `metric_year`,
and `official_url`.
