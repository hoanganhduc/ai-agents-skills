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

When multiple candidates remain, present a numbered list. Require the selected
candidate or observation ID before proof capture.
