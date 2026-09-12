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

## Manuscript Level

Venue fit has two sides. The criteria above score how well a venue matches the
draft; this section scores how strong the draft is, because a tier is chosen by
comparing the two. Adapted from Terence Tao, "Submit to an appropriate journal".

Score the draft against four groups. The point is the count, not a total: a
draft answering most of these well can be considered for a top-tier venue, and
a draft answering only a few belongs at a lower tier.

Correctness:

- are the arguments easy to check, with enough detail and accurate statements
- is the structure modular, with the work carried by stated lemmas
- are prior results cited accurately, and is notation consistent with the
  literature
- do sanity checks or near-counterexamples show the result is sharp
- is there heuristic or numerical evidence that the result is plausible
- has it been proofread as a final draft rather than a working one

Novelty:

- are the results new, and do they advance or settle something open
- are new techniques introduced, and would they carry to other problems
- does the work confirm, extend, or challenge conventional wisdom
- does it clarify a connection between areas, or between existing methods
- does it open a direction or pose a question worth pursuing

Professionalism:

- is the prose grammatical and readable
- does the paper follow the standard structure of its field
- are citations complete and fair
- is rigorous argument clearly separated from speculation

Presentation:

- are the main results stated plainly and early
- is the motivation given, and the organization visible
- is it accessible to a reasonably broad audience rather than only to
  specialists in the exact sub-problem
- does it stay on its stated goal
- would a reader enjoy reading it

Record the level assessment as evidence like any other score component, with the
groups that were weak named explicitly. An unstated level assessment is the
usual reason a shortlist skews one tier too high.

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
