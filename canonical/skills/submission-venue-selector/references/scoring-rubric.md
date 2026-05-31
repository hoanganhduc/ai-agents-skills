# Scoring Rubric

Ranking is advisory. Acceptance chances must be reported as transparent
heuristic intervals with a calculation breakdown, never as predictions or
guarantees.

Hard gates run before ranking:

- venue type is allowed by the selection plan
- venue is not classified as a repository/preprint host unless explicitly
  allowed
- venue has enough identity evidence to distinguish it from aliases or
  conference acronyms
- every deliverable ranked venue has comparator-paper evidence from
  provider/cache/fixture provenance
- every final report has an acceptance-chance interval for each listed venue
  with a base-rate source class and modifier breakdown

Scorecard criteria use 0-4 anchored ordinal scores, not calibrated percentage
weights:

- venue/topic fit
- comparator-pattern fit
- scope and article-type fit
- evidence completeness
- presentation and discourse-norm alignment when full text supports it

Every score component must cite evidence IDs. Comparator-pattern fit must score
zero when only bibliography overlap, venue identity, or offline placeholders are
available. Metadata-only comparator records are discovery evidence; they may
support provisional or caveated output but must not produce `ready`. Sparse
comparator evidence should lower confidence and may downgrade delivery to
`ready-with-caveats` or `not-ready`; absent comparator evidence makes the
recommendation non-deliverable.

Fit bands:

- `strong fit`
- `plausible fit`
- `evidence-limited`
- `not-ready/excluded`

Only order venues within a band when evidence coverage is comparable and the
dominance ordering is stable; otherwise preserve banded output.

Acceptance-chance intervals:

- base rates come from official statistics, publisher/field priors, configured
  priors, or broad fallback heuristics
- comparator papers affect venue-fit and submission-readiness modifiers, not
  the base rate
- fallback heuristics must be low confidence
- no bare percentage may appear without the interval calculation
