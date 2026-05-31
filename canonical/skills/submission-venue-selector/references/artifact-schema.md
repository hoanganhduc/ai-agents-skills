# Artifact Schema

Artifacts are JSON or JSONL with stable IDs, `schema_version`, status fields,
and evidence provenance. Validators reject missing required fields, duplicate
IDs, broken cross references, unsupported enums, and unsupported report claims.

Core artifacts:

- `run_status.json`
- `selection_plan.json`
- `draft.json`
- `references.jsonl`
- `papers.jsonl`
- `sources.jsonl`
- `queries.jsonl`
- `provider_status.json`
- `evidence.jsonl`
- `claims.jsonl`
- `guards.jsonl`
- `venues.jsonl`
- `venue_profiles.jsonl`
- `recent_papers.jsonl`
- `scores.jsonl`
- `scorecards.jsonl`
- `base_rate_sources.jsonl`
- `chance_estimates.jsonl`
- `delivery.json`
- `recommendation.md`

Important statuses:

- reference resolution: `resolved`, `ambiguous`, `unresolved`, `not_a_paper`,
  `excluded`
- delivery: `ready`, `ready-with-caveats`, `not-ready`
- provider: `ok`, `configured_missing`, `skipped`, `rate_limited`,
  `network_failed`, `partial`, `unsupported`

`recent_papers.jsonl` comparator records must include durable provenance:

- `venue_id`
- `title`
- `year`
- `provider`
- `provider_work_id`
- `venue_source_id`
- `source_ids`
- `query_id`
- `evidence_ids`
- `sampling_method`
- `year_window`
- `total_hits`
- `evidence_level`
- `article_type`
- `exclusion_status`
- `topic_distance_rationale`
- `inspection_scope`
- `current_as_of`

Allowed evidence levels are `metadata_only`, `abstract_inspected`, and
`full_text_inspected`. Full-text status must not be inferred from metadata.
Metadata-only records are discovery evidence and must not produce `ready`
delivery.

`scorecards.jsonl` stores 0-4 anchored ordinal criteria, fit bands, risk flags,
support status, evidence IDs, and comparator counts. It does not store
calibrated acceptance probabilities.

`base_rate_sources.jsonl` stores the acceptance-rate source used for each venue:
official journal/publisher statistics when available, publisher/field priors,
configured priors, or a broad fallback heuristic interval.

`chance_estimates.jsonl` stores the final required acceptance-chance interval
for each venue. Records must include source class, base-rate interval, modifier
intervals, final interval, confidence, caveats, and a note that the estimate is
heuristic rather than predictive.

Privacy defaults:

- `draft.json` stores a tokenized or relative draft path and hash.
- Raw draft text requires `--retain-draft-text`.
- Provider queries must be recorded in redacted form.
- `sources.jsonl` must not persist raw query URLs, credentials, auth headers,
  emails, or raw draft text.
