# Codex Model Tiers Template

Copy this file to `MODEL_TIERS.md` and adjust it if you want to tune the default mappings.

- name: STRONG_REASONER
  recommended_model: gpt-5.4
  default_reasoning_effort: high
  fallback_models:
    - gpt-5.2
    - gpt-5.3-codex
  speed: medium
  cost: high
  best_for:
    - judge
    - synthesizer
    - correctness reviewer
    - referee
    - formal critique
  notes:
    - use xhigh for the hardest proof or reduction tasks

- name: BALANCED_MODEL
  recommended_model: gpt-5.3-codex
  default_reasoning_effort: medium
  fallback_models:
    - gpt-5.4-mini
    - gpt-5.2
  speed: medium
  cost: medium
  best_for:
    - planner
    - review
    - research synthesis
    - edge-case analysis
  notes:
    - raise to high when the role is specialist but not lead verifier

- name: FAST_MODEL
  recommended_model: gpt-5.3-codex-spark
  default_reasoning_effort: low
  fallback_models:
    - gpt-5.4-mini
  speed: fast
  cost: low
  best_for:
    - scouting
    - brainstorming
    - clarity review
    - lightweight summarization
  notes:
    - prefer gpt-5.4-mini when you need slightly stronger reasoning without paying for a full lead model

## Suggested profile mappings

- `math-heavy`
  - lead verifier / referee: `gpt-5.4` `xhigh`
  - secondary deep roles: `gpt-5.3-codex` `high`
  - lightweight support: `gpt-5.4-mini` `medium`

- `premium`
  - lead judge or synthesizer: `gpt-5.4` `high`
  - supporting reviewers: `gpt-5.3-codex` `medium`
  - scout or clarity support: `gpt-5.4-mini` `medium`

- `balanced`
  - lead roles: `gpt-5.3-codex` `medium`
  - support roles: `gpt-5.4-mini` `medium`
  - fast roles: `gpt-5.3-codex-spark` `low`

- `budget`
  - lead roles: `gpt-5.4-mini` `medium`
  - support roles: `gpt-5.3-codex-spark` `low`

## Time guidance

Use these rough estimates for planning:

- `STRONG_REASONER`: 2-4 minutes per role
- `BALANCED_MODEL`: 1-2 minutes per role
- `FAST_MODEL`: 30-90 seconds per role
