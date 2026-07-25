# Matching Policy

## Precedence

Generate candidates deterministically in this order:

1. Exact ISSN, ISSN-L, provider source ID, or stable venue ID.
2. Exact normalized canonical title.
3. Exact registered alias or acronym.
4. Normalized token or prefix match.
5. Derived acronym match.
6. Fuzzy candidate generation.

Normalize Unicode, case, punctuation, whitespace, and ampersand/`and` only for
comparison. Preserve the source spelling in artifacts and reports.

## Identity boundaries

- Do not merge a conference series with a year/location-specific instance.
- Do not merge renamed journals without identifier or title-history evidence.
- Do not merge acronym collisions by score alone.
- Do not treat a journal and similarly named proceedings series as one venue.
- Cross-source identity requires a shared strong identifier or corroborating
  sponsor, publisher, official-domain, and title-history evidence. Exact
  title/type may join title-only records only when that title group has at most
  one strong-identifier component. Disjoint ISSN/eISSN sets stay separate and
  produce an identity-conflict warning; an identifier-free row never bridges
  them.

Return `matched_field`, `match_method`, score, confidence, and ambiguity group
for every candidate. Fuzzy scores order candidates; they do not prove identity.

### Identity resolution (delivery)

- **Unique exact-identifier, exact-title, or exact-alias** → `match_status=matched`
  and `resolved_venue_id` set, even if weaker fuzzy candidates remain.
- **Two or more distinct exact-tier venue IDs** → `ambiguous`.
- **Fuzzy-only multi-match**, or short acronym (compact length ≤ 5) with only
  fuzzy hits → `ambiguous` (never auto-pick).
- **Full candidate list** is always written to `matches.jsonl`. Report/stdout
  may show only top-K (`--max-candidates`, default 12) unless
  `--include-all-candidates`.

### Venue type filter

`--venue-type` (repeatable) filters loaded venues before matching (e.g.
`journal`, `conference`).

### Collision table

Built-in `registry/collisions.json` emits warnings for known confusable
acronyms (ISAAC/ISSAC/ISCA, JIP vs ITP/ILP). Warnings do not drop candidates.

### Journal path

ICORE is conference-only. Journal-shaped queries without non-ICORE journal
observations emit a `journal-path:` warning and mark incomplete analysis.

When multiple candidates remain without unique exact-tier resolution, present a
numbered list. Require `--venue-id` before proof capture unless delivery already
resolved a unique exact-tier venue.
