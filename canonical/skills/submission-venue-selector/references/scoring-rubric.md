# Scoring Rubric

Ranking is advisory. Do not claim acceptance probability or prestige unless the
workspace contains explicit evidence for that claim.

Hard gates run before ranking:

- venue type is allowed by the selection plan
- venue is not classified as a repository/preprint host unless explicitly
  allowed
- venue has enough identity evidence to distinguish it from aliases or
  conference acronyms
- every deliverable ranked venue has comparator-paper evidence from
  provider/cache/fixture provenance

Soft criteria:

- bibliography venue overlap
- recent related-paper evidence
- scope and article-type fit
- topic similarity
- current submission-policy evidence
- evidence completeness

Every score component must cite evidence IDs. The `recent_related_papers`
criterion must cite comparator-paper evidence IDs and must score zero when only
bibliography overlap, venue identity, or offline placeholders are available.
Sparse comparator evidence should lower confidence and may downgrade delivery to
`ready-with-caveats` or `not-ready`; absent comparator evidence makes the
recommendation non-deliverable.

Tie handling:

- prefer stronger current-source evidence
- prefer higher resolved-reference coverage
- otherwise preserve deterministic alphabetical order
