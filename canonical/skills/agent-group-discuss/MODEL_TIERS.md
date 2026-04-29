# MODEL_TIERS.md

This is the live Codex model catalog for `agent_group_discuss`.
Use this file for actual role assignment. `MODEL_TIERS.example.md` is only a template.

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
| `gpt-5.4` | R4 | medium | lead verifier, judge, referee, proof-heavy roles | `low` to `xhigh` |
| `gpt-5.2` | R3 | medium | long-running synthesis, planning, stable fallback lead roles | `low` to `xhigh` |
| `gpt-5.3-codex` | R3 | medium | structured analysis, algorithmic reasoning, implementation-aware review | `low` to `xhigh` |
| `gpt-5.4-mini` | R2 | fast | support reviewer, edge-case pass, clarity and bounded analysis | `low` to `xhigh` |
| `gpt-5.3-codex-spark` | R1 | very fast | scouting, lightweight summarization, cheap exploratory passes | `low` to `xhigh` |

## Hard override for research tasks

For research, proof, manuscript-correctness, or other high-stakes mathematical review tasks:

- `STRONG_REASONER` -> `gpt-5.4` with `xhigh`
- `BALANCED_MODEL` -> `gpt-5.4` with `high`
- `FAST_MODEL` -> `gpt-5.4` with `medium`

Use cheaper profiles only if the user explicitly asks for them.

## Profiles

### math-heavy

| Tier | Model | Reasoning effort | Est. time per role |
|------|-------|------------------|--------------------|
| `STRONG_REASONER` | `gpt-5.4` | `xhigh` | 3-5 min |
| `BALANCED_MODEL` | `gpt-5.4` | `high` | 3-5 min |
| `FAST_MODEL` | `gpt-5.4` | `medium` | 2-4 min |

### premium

| Tier | Model | Reasoning effort | Est. time per role |
|------|-------|------------------|--------------------|
| `STRONG_REASONER` | `gpt-5.4` | `high` | 2-4 min |
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
