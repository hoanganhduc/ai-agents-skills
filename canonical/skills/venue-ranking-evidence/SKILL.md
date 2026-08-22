---
name: venue-ranking-evidence
description: Use when identifying a journal, conference, or proceedings series from a partial name, acronym, alias, ISSN, or source ID; preserving source-specific rank, quartile, metric, classification, membership, or coverage observations; or proving that the public ICORE detail page displayed one ICORE claim. Live bulk paths are ICORE (edition-verified) and DOAJ (public CSV, currentness-unconfirmed). Other built-ins accept authorized normalized imports only; Conference Ranks is legacy. Return every plausible match and never conflate index membership with ranking.
---

# Venue Ranking Evidence

Resolve venues, report source-specific observations, and preserve official-page
proof. Use the runtime for deterministic matching, provenance, and artifacts;
use judgment only to explain ambiguity and provider limitations.

**Live paths:** ICORE (conference ranks, edition discovery, optional browser proof)
and DOAJ (public journal membership CSV; live or import; always
`currentness-unconfirmed`; proof-ineligible). CCF, SCImago, Scopus, Web of Science
Master Journal List, JCR, JUFO, the Norwegian Register, and Conference Ranks
accept authorized normalized CSV/JSON imports only. Imports remain
`currentness-unconfirmed`. Conference Ranks is `secondary-legacy` and must never
be presented as latest.

## Required workflow

1. Run `doctor` when runtime or browser readiness is unknown.
2. Run `sources list|show|check` to confirm capabilities, optional `--data-file`
   preflight, and deferred-source notes.
3. Run `lookup` with the user's text and requested sources. Inspect the delivery
   matrix (`status`, `match_status`, `source_coverage_status`,
   `incomplete_analysis`, `requested_sources` / `satisfied_sources` /
   `missing_or_blocked`). Never treat `ready` as multi-source completeness unless
   every requested source is satisfied.
4. Separate observations by source, assertion kind, scheme, category or
   collection, and metric year or edition. Never summarize a venue as merely
   “Q1”, “ranked”, “Scopus”, or “WoS”.
5. Unique exact-identifier / exact-title / exact-alias identity resolves even when
   weaker fuzzy candidates remain; fuzzy-only short acronyms stay ambiguous.
   Full candidates remain in `matches.jsonl` (display may be top-K).
6. If proof is requested and identity is ambiguous, obtain an explicit
   `--venue-id` selection first. Unique exact-tier resolution may auto-select for
   proof.
7. Run `proof` only for ICORE public detail pages with the reviewed association
   adapter, then `verify`. DOAJ and imports are proof-ineligible.
8. Report freshness, access, and evidence gaps. Use incomplete analysis when
   material requested sources remain blocked or identity is unresolved.

Read these references as needed:

- `references/source-policy.md` before live lookup, refresh, or source addition.
- `references/matching-policy.md` for ambiguous identities or aliases.
- `references/artifact-schema.md` when inspecting or consuming run artifacts.
- `references/proof-contract.md` before capturing or validating proof.
- `references/privacy-licensing-policy.md` for authenticated, subscription, or
  restricted providers.
- `references/deferred-roadmap.md` for Phase D public-live and licensed-API work.

## Runtime

POSIX:

```bash
bash "${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh" \
  skills/venue-ranking-evidence/run_venue_ranking_evidence.sh \
  lookup --dir /path/to/run --query "ISAAC" --source icore --offline
```

Windows PowerShell:

```powershell
$runtime = if ($env:AAS_RUNTIME_ROOT) { $env:AAS_RUNTIME_ROOT } else { "$env:LOCALAPPDATA\ai-agents-skills\runtime" }
& "$runtime\run_skill.ps1" `
  "skills/venue-ranking-evidence/run_venue_ranking_evidence.ps1" `
  lookup --dir "$env:TEMP\venue-ranking-run" --query "TCS" --offline
```

On Windows, use the PowerShell command above; CMD runtime entrypoints are not published.

The POSIX wrapper honors `VENUE_RANKING_EVIDENCE_PYTHON`, then
`AAS_RUNTIME_PYTHON`, before falling back to `python3` and `python`. Browser
proof capture requires Chromium, Chrome, or Edge. Proof marker verification
also requires Poppler's `pdftotext` on `PATH`. Run `doctor` to inspect both.

Useful verbs:

- `doctor`
- `sources list|show|check`
- `sources check --source <id> --data-file <id>=<path>` (import preflight)
- `sources validate|add --descriptor <file> --registry-dir <dir>`
- `lookup --dir <run> --query <text> [--source <id> ...]`
- `lookup ... --data-file <source-id>=<csv-or-json>`
- `lookup ... --venue-type journal|conference|...` (repeatable)
- `lookup ... --max-candidates N` / `--include-all-candidates`
- `proof --dir <run> --observation-id <id> [--venue-id <id>]`
- `report|verify|purge --dir <run>`
- `cache status|refresh|purge`
- `smoke`

Live operations require both `--allow-network` and `--allow-source <id>`.

| Source | Live | Freshness when live | Proof |
|---|---|---|---|
| `icore` | yes (`icore-csv`) | `verified-current` / historical | yes (detail page) |
| `doaj` | yes (`doaj-csv` public export) | always `currentness-unconfirmed` | no |
| other built-ins | no | import `currentness-unconfirmed` | no |

Offline ICORE cache demotes formerly-current rows to `currentness-unconfirmed`.

## Output contract

`lookup` prints and writes `delivery.json` with:

- `status`: `ready` only when identity is resolved (`match_status=matched`), every
  **requested** source is satisfied (`source_coverage_status` complete or
  not-requested), and analysis is not incomplete.
- `match_status`: `matched` | `ambiguous` | `unmatched`
- `source_coverage_status`: `complete` | `partial` | `empty` | `not-requested`
- `incomplete_analysis`: true when coverage is partial/empty, identity is
  ambiguous, or a journal-path gap applies
- `requested_sources` / `satisfied_sources` / `missing_or_blocked`
- `total_candidates` / `displayed_candidates` / `resolved_venue_id` /
  `ambiguity_requires_selection`

For each candidate, show title, type, identifiers, aliases, match method, and
one observation row per source assertion (kind, scheme, category/collection,
value, edition/year, freshness, official URL). Index membership is not a rank.

## Proof rules

Browser Print-to-PDF is not a pixel-identical screen capture. Preserve raw PDF,
full-page PNG, sidecars, and manifest fields from runtime. Only `verify` can
return `VERIFIED`. No login, CAPTCHA, or browser-profile reuse. Proof is limited
to the public unauthenticated ICORE detail page.

## Source extension boundary

User-added declarative CSV/JSON sources need validated descriptors without
executable hooks or reviewed proof association adapters. New live adapters need
reviewed built-in code and fixtures.

Built-in `user-export` normalized columns: required `canonical_title` and
`value`; optional identity/metric fields as documented in
`references/artifact-schema.md`. DOAJ raw public CSV is accepted via live fetch
or `--data-file doaj=...` through the built-in DOAJ parser.
