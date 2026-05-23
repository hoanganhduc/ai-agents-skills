# Manager-Worker Research Review

Use this template when a provider manager should split a research review into
bounded child-worker tasks.

## Manager Contract

| Field | Value |
|---|---|
| Manager role | Provider-specific research manager |
| Child depth | one level only |
| Child model | same provider, same resolved model, same thinking level |
| Child limit | configured `max_child_workers_per_manager` |
| Child outputs | result-packet style summaries |

## Manager Output

- task partition summary
- child task packet refs or inline child task summaries
- child result refs
- accepted findings
- rejected findings
- unresolved findings
- limitations and blocked checks

## Child Worker Rules

- Use only the assigned scope and input refs.
- Do not spawn further agents.
- Do not edit files unless the parent assigned a write target.
- Cite evidence refs for each substantive claim.
- Mark incomplete scope as `incomplete analysis`.

## Fallback

If same-model child dispatch is unavailable, the manager returns proposed child
task packets and does not launch child workers. The parent orchestrator decides
whether to dispatch those packets another way.
