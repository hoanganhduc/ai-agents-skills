---
name: self-improving-agent
description: Use when a task fails, a user corrects the assistant, a capability is missing, or a recurring better pattern should be logged for future improvement in Codex.
metadata:
  short-description: Log durable learnings and recurring failures
---

# Self Improving Agent

Use this skill after failures, corrections, missing capabilities, or recurring better patterns.

## When to use

- a command or operation fails unexpectedly
- the user corrects you
- the user asks for a capability that does not exist yet
- a recurring better approach is discovered
- a workflow or routing rule should be promoted into durable Codex memory

## Targets

- `.learnings/LEARNINGS.md`
- `.learnings/ERRORS.md`
- `.learnings/FEATURE_REQUESTS.md`

Promote broadly useful lessons into:

- `~/.codex/memories/`
- relevant files under `~/.codex/skills/`
- `~/.codex/AGENTS.md` when the lesson changes global workflow or routing

## Workflow

1. Classify the event:
   - failure -> `ERRORS.md`
   - correction / better pattern -> `LEARNINGS.md`
   - missing capability -> `FEATURE_REQUESTS.md`
2. If the `.learnings/` files do not exist yet, create them from the templates in `assets/`.
3. Append a concise structured entry.
4. If the lesson is durable and broadly useful, promote it into Codex memory or skill docs.

## Codex-adapted reminder loop

Codex does not use Claude-style automatic hooks here, so run this reminder loop manually:

1. after a command failure, user correction, or unexpected workaround, pause before the final reply
2. ask whether the event created a reusable lesson, error pattern, or missing-capability note
3. if yes, log it immediately instead of trusting memory to preserve it later
4. if the lesson affects global workflow or routing, promote it into `~/.codex/memories/`, a relevant `SKILL.md`, or `~/.codex/AGENTS.md`

## Trigger checklist

Use this skill especially when any of these happened:

- a command failed in a way you did not predict
- the user corrected a factual or workflow mistake
- you discovered a non-obvious workaround or gotcha
- you almost said a capability was unavailable before checking local evidence
- the same pain point has appeared multiple times and should be made durable

## Pending-learnings review

If the workspace already has a `.learnings/` directory, review pending items there before repeating known risky work.

Typical lightweight checks:

- pending entries
- high-priority entries
- repeated patterns that should now be promoted into Codex memory or docs

Helper script:

```bash
bash ~/.codex/skills/self_improving_agent/scripts/review_pending.sh
```

High-priority-only view:

```bash
bash ~/.codex/skills/self_improving_agent/scripts/review_pending.sh --high-only
```

Explicit workspace or `.learnings` directory:

```bash
bash ~/.codex/skills/self_improving_agent/scripts/review_pending.sh /path/to/workspace
```

## Manual helpers inspired by Claude hooks

Lightweight manual helpers are available when you want Claude-style reminders
without automatic hooks:

Safety-check a shell command:

```bash
bash ~/.codex/skills/self_improving_agent/scripts/check_command_safety.sh "git push --force origin main"
```

Scan command output for common failure markers:

```bash
some_command 2>&1 | bash ~/.codex/skills/self_improving_agent/scripts/detect_common_errors.sh
```

## Read only when needed

- `assets/LEARNINGS.md`
- `assets/ERRORS.md`
- `assets/FEATURE_REQUESTS.md`
- `references/examples.md` for compact sample entries

## Rules

- Keep entries short, concrete, and actionable.
- Prefer updating Codex-facing docs over keeping important lessons only in transient replies.
- Do not depend on OpenClaw hooks, session-spawn tools, or a runtime package for this skill.
- Treat this as a Codex-native manual workflow, not as a hook-emulation system.
