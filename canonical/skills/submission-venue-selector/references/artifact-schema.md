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
- `delivery.json`
- `recommendation.md`

Important statuses:

- reference resolution: `resolved`, `ambiguous`, `unresolved`, `not_a_paper`,
  `excluded`
- delivery: `ready`, `ready-with-caveats`, `not-ready`
- provider: `ok`, `configured_missing`, `skipped`, `rate_limited`,
  `network_failed`, `partial`

Privacy defaults:

- `draft.json` stores a tokenized or relative draft path and hash.
- Raw draft text requires `--retain-draft-text`.
- Provider queries must be recorded in redacted form.
