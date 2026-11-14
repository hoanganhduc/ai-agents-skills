# Open Math OS Models

The catalog below was read from `GET /v1/models` on 2026-11-07. The live catalog is authoritative: `ask` checks the model id and the effort against it before every send, and `models` shows the current list.

| Model id | Reasoning efforts | Notes |
|---|---|---|
| `deepseek/deepseek-v4.1-flash` | low, high, max | context 1M, images |
| `moonshotai/kimi-k3.1-pro` | low, high, max | context 1M, images |
| `z-ai/glm-5.3-flash` | low, high, max | context 1M, images |
| `z-ai/glm-5.3` | low, high, max | text only |
| `meta/muse-spark-open` | low, medium, high, xhigh, max | context 1M, images |
| `mistralai/mistral-large-4-0` | low, medium, high | context 1M, images |
| `nvidia/nemotron-3-super-120b-a12b` | low, medium | text only, context 262K |

## Output budget

The client sends `max_completion_tokens` only when `--max-tokens N` gives it. It has no default per effort (user decision, 2026-11-13).

## What the first trial showed

On 2026-11-05, five models answered three textbook problems at effort high: the spanning-tree count of the wheel W6, and Ore-type Hamiltonicity with thresholds n and n - 1. All 15 conclusions were correct, using 36,470 tokens in total. Latency ranged from about 4 seconds (DeepSeek V4.1 Flash) to 68 seconds (Kimi K3).

These problems are likely in the training data. The trial shows that the API path works. It does not measure research-level ability.

## Status

Provisional, 2026-11-05, unverified gateway. No Open Math model counts toward an independence requirement.
