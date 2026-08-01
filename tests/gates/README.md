# Seeded-defect corpus for research gates

Each fixture in this directory is a synthetic research artifact carrying exactly
one known, labelled defect (or none, for the clean control), with the expected
delivery verdict recorded. The corpus exists to make the research gates
*falsifiable*: a gate that cannot catch a poisoned fixture, or that rejects the
clean control, is miscalibrated.

## What CI enforces today (offline, deterministic)

`tests/test_research_gates.py` validates:

1. **Corpus integrity** — every fixture parses, carries exactly one
   `defect_class` from the taxonomy below, the declared defect is actually
   present in the artifact (per-class deterministic checks), and the
   `expected_verdict` is coherent (`NOT READY` for poisoned, `READY` for the
   clean control). The clean control must trip none of the poison checks.
2. **Gate contracts** — each core gate skill (`research-briefing`,
   `decision-doubt-loop`, `research-report-reviewer`,
   `research-verification-gate`) still declares its output contract
   (verdict vocabulary, required sections, style-profile fields), and its
   referenced checklist file exists.
3. **Coverage mapping** — every defect class names the gates expected to catch
   it; those gate files must contain the corresponding contract vocabulary.
   Classes with no covering gate are recorded explicitly as uncovered.

## What CI cannot enforce today

The gates are prompt-convention skills; no offline test can prove an agent
running the gate catches the poison. When a gate (or a future runtime checker)
is executed against this corpus, record the run and per-fixture verdicts so
false-pass and false-fail rates become measurable. The
`post_hoc_observed_power` class currently has no covering gate by design — it
tracks the repo's verified statistical-rigor gap and should be wired to the
proposed `study-design-and-power` skill if that lands.

## Defect taxonomy

| defect_class | poison |
|---|---|
| `citation_fabrication` | claim cites a source absent from the evidence list |
| `unchecked_scope` | material scope uninspected, no `incomplete analysis` marker |
| `post_hoc_observed_power` | post-hoc observed power justifies a conclusion |
| `retracted_source` | claim rests on a retracted source |
| `venue_ranking_without_comparator` | ranked venues without comparator-paper evidence |
| `unsupported_citation` | existing source presented as supporting when it is not |
| `undisclosed_truncation` | conclusion rests on a load-bearing source read only in part, with no disclosure |
| `false_consensus_persist_until_approve` | review continues past the round cap solely to chase unanimous approval |
| `review_round_wording_only_delta` | follow-up round is wording-only yet treated as progress |
| `erased_disagreement_synthesis` | prior disputes erased without residual uncertainty labels |
| `multi_llm_lgtm_not_bank` | multi-LLM LGTM used as banked support without different-family or machine check |
| `halt_without_disclose` | review halt without residual uncertainty / negative-space disclosure |
| `none` | clean control (expected verdict `READY`) |

## Fixture schema

- `fixture_id` — file stem.
- `defect_class` — one taxonomy value.
- `defect_label` — one-line human description of the poison.
- `target_gates` — gates expected to catch this class (empty when uncovered).
- `coverage_note` — optional, required when `target_gates` is empty.
- `artifact` — the synthetic research artifact under test.
- `expected_verdict` — `READY` or `NOT READY`.
- `expected_signals` — what a correct gate should report seeing.
