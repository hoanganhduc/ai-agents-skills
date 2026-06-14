# MODEL_TIERS.md

This is the Codex model routing policy for `agent-group-discuss`
`codex_spawned` participants. Use this file for actual Codex-spawned role
assignment after performing the runtime freshness check below.
`MODEL_TIERS.example.md` is only a template.

External CLI participants use AGD-owned capability profiles and adapter probes,
not this model-tier table.

## Runtime Freshness Check

Before showing a multi-agent run plan or calling `spawn_agent`, compare this
catalog with the models currently exposed by the active Codex runtime/tool
definitions. Treat the active runtime as the source of truth.

Required steps:

1. Inspect the current available `spawn_agent` model list from the active tool
   definition or runtime-provided model catalog.
2. Identify the strongest available frontier model and the strongest available
   coding-specialized model.
3. If a newer appropriate model is available than the default named in this
   file, use the newer model in the run plan and record the override in
   `state.json`.
4. If working in this repository and the checked-in catalog is stale, update
   this file before launching the multi-agent run.
5. If the runtime list cannot be inspected, say so in the plan and use the
   checked-in defaults as fallbacks.

Do not assume that the model names below are permanently latest. They are
checked-in fallbacks plus examples of how to map discovered runtime models to
roles.

## Reasoning level classification

| Level | Description | Suitable for |
|-------|-------------|-------------|
| R4 | Deep multi-step reasoning, formal proofs, adversarial critique | theorem verification, correctness review, PSPACE reductions, final refereeing |
| R3 | Strong structured reasoning | planning, synthesis, algorithm design, structured review |
| R2 | Solid general reasoning | edge-case review, specialist analysis, support roles |
| R1 | Fast summarization and lightweight exploration | scouting, brainstorming, clarity review |

## Codex model catalog

| Model | Reasoning | Speed | Best for | Reasoning effort |
|-------|-----------|-------|----------|------------------|
| latest available frontier model | R4 | medium | lead verifier, judge, referee, proof-heavy roles | `low` to `xhigh` |
| `gpt-5.4` | R4 fallback | medium | fallback lead verifier, judge, referee, proof-heavy roles | `low` to `xhigh` |
| `gpt-5.2` | R3 | medium | long-running synthesis, planning, stable fallback lead roles | `low` to `xhigh` |
| `gpt-5.3-codex` | R3 | medium | structured analysis, algorithmic reasoning, implementation-aware review | `low` to `xhigh` |
| `gpt-5.4-mini` | R2 | fast | support reviewer, edge-case pass, clarity and bounded analysis | `low` to `xhigh` |
| `gpt-5.3-codex-spark` | R1 | very fast | scouting, lightweight summarization, cheap exploratory passes | `low` to `xhigh` |

Runtime examples observed in recent Codex sessions include `gpt-5.5` as a
frontier model above `gpt-5.4`. If `gpt-5.5` or a later frontier model is
available, prefer it for R4 roles unless a task-specific constraint justifies a
different model.

## Hard override for research tasks

For research, proof, manuscript-correctness, or other high-stakes mathematical review tasks:

- every role, including scouts, support reviewers, managers, and child workers
  -> latest available frontier model with the highest available reasoning level
  (currently `xhigh` in Codex runtimes that expose it)

Use cheaper profiles only if the user explicitly asks for them.

## Profiles

### math-heavy

| Tier | Model | Reasoning effort | Est. time per role |
|------|-------|------------------|--------------------|
| `STRONG_REASONER` | latest available frontier model | `xhigh` | 3-5 min |
| `BALANCED_MODEL` | latest available frontier model | `high` | 3-5 min |
| `FAST_MODEL` | latest available frontier model | `medium` | 2-4 min |

### premium

| Tier | Model | Reasoning effort | Est. time per role |
|------|-------|------------------|--------------------|
| `STRONG_REASONER` | latest available frontier model | `high` | 2-4 min |
| `BALANCED_MODEL` | `gpt-5.2` | `high` | 2-3 min |
| `FAST_MODEL` | `gpt-5.4-mini` | `medium` | 1-2 min |

### balanced

| Tier | Model | Reasoning effort | Est. time per role |
|------|-------|------------------|--------------------|
| `STRONG_REASONER` | `gpt-5.2` | `high` | 2-3 min |
| `BALANCED_MODEL` | `gpt-5.4-mini` | `medium` | 1-2 min |
| `FAST_MODEL` | `gpt-5.3-codex-spark` | `low` | 30-90s |

### budget

| Tier | Model | Reasoning effort | Est. time per role |
|------|-------|------------------|--------------------|
| `STRONG_REASONER` | `gpt-5.4-mini` | `medium` | 1-2 min |
| `BALANCED_MODEL` | `gpt-5.3-codex-spark` | `low` | 30-90s |
| `FAST_MODEL` | `gpt-5.3-codex-spark` | `low` | 15-60s |

## Task-to-profile heuristic

| Task signal | Recommended profile |
|-------------|---------------------|
| formal proof, theorem, PSPACE, NP-hard, correctness verification, manuscript correctness | `math-heavy` |
| research paper review, algorithm design, critical decision | `premium` |
| general discussion, code review, brainstorming, exploration | `balanced` |
| quick sanity check, opinion gathering, lightweight summary | `budget` |

## Role-to-tier mapping

| Role | Tier | Reasoning need |
|------|------|---------------|
| planner | `STRONG_REASONER` | must decompose complex tasks correctly |
| judge / synthesizer / referee | `STRONG_REASONER` | must weigh competing arguments and preserve correctness |
| correctness reviewer / critic / falsifier / adversary / auditor | `STRONG_REASONER` | must catch subtle logical and mathematical errors |
| advocate / edge-case reviewer / hypothesis generator / repair agent | `BALANCED_MODEL` | solid reasoning for specific angles |
| pragmatist / clarity reviewer / scout / brainstormer | `FAST_MODEL` | speed and breadth over depth |
