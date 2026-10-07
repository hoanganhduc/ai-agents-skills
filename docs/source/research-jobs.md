# Research jobs

A job brief chooses purpose, executor, and artifact format independently.
The existing [research job presets](https://github.com/hoanganhduc/ai-agents-skills/blob/main/canonical/templates/research-job-presets.md)
compose current workflows; they are not a new scheduler or permission to spend,
publish, notify, or execute a remote job.

| Choice | Guidance |
|---|---|
| Purpose | `research`, `review`, or `lean-formalize`; research/review do not implicitly request formalization. |
| Executor | Current host tools or a backend permitted by the task's compute policy. |
| Artifact | Ordinary Lean/Lake by default for formalization; Lax only when its artifact format is selected. |
| Network and assurance | Record explicit network permission and required validators; an unavailable required check remains unmet. |

Before dispatch, record owner, exact inputs and versions, allowed actions,
resource/time budgets, attempt limits, evidence outputs, and acceptance gates.
A candidate, template, or provider reply cannot grant authority. Independent
sessions are not automatically independent model families; resuming a reviewer
continues that review rather than creating a new independent assessment.

## Bounded recovery

- Inspect quota and remaining time before dispatch. Hard exhaustion has no
  automatic retry; throttling uses bounded cooldown. New unattended phases
  default to at most three attempts unless a stricter existing cap applies.
  Restart or provider rotation does not reset accounting.
- Correct deterministic errors before retrying. Extend time only with progress
  evidence and remaining authorization; silence alone does not prove a hang.
- Check cleanup before replacing a worker. If remote acceptance or spending is
  uncertain, reconcile the existing attempt and keep unknown spending reserved;
  do not resubmit automatically.
- Preserve stop/pause across restarts. Late results can supply evidence or
  reconcile accounting but cannot resume work under a successor attempt.
- Keep pending formal verification pending. The existing native remote path
  uses exit 19 for pending and 21 for incomplete; both preserve the candidate
  and stop automatic producer failover. Resume the bound verification attempt
  using the workflow's documented operation.

Use the [autonomous-loop runbook](https://github.com/hoanganhduc/ai-agents-skills/blob/main/canonical/templates/autonomous-research-loop-runbook.md)
and [native remote Lean reference](https://github.com/hoanganhduc/ai-agents-skills/blob/main/canonical/skills/autonomous-research-loop-runtime/references/native-lean-remote.md)
for the existing dispatch, journal, checkpoint, and resumption mechanisms.
Report process completion, artifact quality, review acceptance, formal evidence,
and publication as separate outcomes. Preserve original failure evidence during
recovery. For installer and wrapper failures, start with
[Troubleshooting](troubleshooting.md); for proof evidence, use
[Lean formalization](lean-formalization.md).
