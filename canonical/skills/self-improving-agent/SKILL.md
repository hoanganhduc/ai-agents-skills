---
name: self-improving-agent
description: Use when a task fails, a user corrects the assistant, a capability is missing, or a recurring better pattern should be logged and considered for canonical ai-agents-skills integration.
metadata:
  short-description: Log durable learnings and propose repo integration plans
---

# Self Improving Agent

Use this skill after failures, corrections, missing capabilities, or recurring
better patterns. The durable default is repo-first: reusable lessons should be
captured locally, then considered for a general change to this
`ai-agents-skills` checkout, normally `~/ai-agents-skills`.

## When to use

- a command or operation fails unexpectedly
- the user corrects you
- the user asks for a capability that does not exist yet
- a recurring better approach is discovered
- a workflow or routing rule should become durable across agents, settings, or
  operating systems
- a learning affects one or more installed skills, optional artifacts,
  manifests, runtime helpers, docs, tests, or installer behavior

## Targets

Local task notes:

- `.learnings/LEARNINGS.md`
- `.learnings/ERRORS.md`
- `.learnings/FEATURE_REQUESTS.md`

Canonical integration targets:

- `canonical/skills/<skill>/SKILL.md` for reusable skill behavior
- `canonical/runtime/` for portable helper behavior
- `manifest/` for installability, profiles, dependencies, artifacts, and
  runtime smoke contracts
- `installer/ai_agents_skills/docs.py` plus generated `README.md` and `docs/`
  for generated docs
- `docs/source/index.md` and `docs/source/overview.md` for manual docs-site
  landing pages
- `tests/` for focused regression coverage

Codex, Claude, DeepSeek, Copilot, Grok, and other agent homes are runtime
install targets, not the primary place for reusable fixes. Update them through
the installer after the canonical repo change is planned, reviewed, and
verified.

`~/.openclaw/workspace/skills/*` (including dual-route adapters such as
`aas-remote-bridge`) is also an install product. Edit
`canonical/skills/` and `canonical/runtime/` first, then publish or install.
Never land durable skill logic only under OpenClaw.

## Install-target model

- Codex, Claude, DeepSeek, Copilot, and OpenCode are normal skill targets declared in
  `manifest/skills.yaml`.
- Copilot can receive skill adapters and supported personas, but not
  instruction blocks, templates, or command artifacts unless the installer
  gains that support.
- OpenClaw is explicit-only and fake-root-only before native target evidence.
  Real `.openclaw` writes, instruction blocks, symlink/reference modes,
  unclassified support files, and runtime-backed skills remain blocked until
  the OpenClaw install-target gates are satisfied.

When a learning mentions "all targets", state this matrix explicitly and do
not claim Copilot or OpenClaw support beyond the current installer contract.

## OS and substrate model

Treat OS coverage as a matrix, not a slogan:

- Linux, macOS, and WSL use POSIX runtime runners.
- Native Windows uses PowerShell/CMD runtime runners.
- Git Bash/MSYS may run POSIX helpers, but the installer target shape is still
  Windows unless the check is deliberately scoped otherwise.
- A mounted Windows profile inspected from Linux or WSL is not proof that a
  native Windows agent can execute the same paths.

Reusable learnings should say which OS/substrate was inspected, which was
inferred, and which remains unverified.

## Workflow

1. Classify the event:
   - failure -> `ERRORS.md`
   - correction / better pattern -> `LEARNINGS.md`
   - missing capability -> `FEATURE_REQUESTS.md`
2. If the `.learnings/` files do not exist yet, create them from the templates
   in `assets/`.
3. Append a concise structured entry.
4. If the lesson is reusable, add a Canonical Integration Plan section. Include
   related skills/settings, affected install targets, affected OS/substrates,
   proposed repo files, docs generation, tests, and blocked limits.
5. Suggest the integration plan before editing canonical files. Do not mutate
   the repo, installed agent homes, or runtime files without an explicit user
   request or an already-approved implementation task.
6. When approved, implement the smallest canonical repo change that solves the
   reusable problem, regenerate docs, and run focused verification.

## Reminder loop

The reminder loop is manual unless a caller wraps it in their own automation:

1. after a command failure, user correction, or unexpected workaround, pause
   before the final reply
2. ask whether the event created a reusable lesson, error pattern, or missing
   capability note
3. if yes, log it immediately instead of trusting memory to preserve it later
4. if the lesson affects global workflow, routing, skills, settings, install
   targets, or OS behavior, propose a canonical repo integration plan

## Trigger checklist

Use this skill especially when any of these happened:

- a command failed in a way you did not predict
- the user corrected a factual or workflow mistake
- you discovered a non-obvious workaround or gotcha
- you almost said a capability was unavailable before checking local evidence
- the same pain point has appeared multiple times and should be made durable
- a helper command, runtime wrapper, or doc example works for one OS or target
  but not another

## Portable runtime helpers

The target-neutral helpers are installed through the shared runtime when this
skill is selected with runtime profile `auto` or `full`. Prefer the managed
runtime runner over paths inside an agent skill directory, because reference
install mode intentionally does not copy support files.

POSIX example:

```bash
runtime="${AAS_RUNTIME_ROOT:?Set AAS_RUNTIME_ROOT to the installed runtime root}"
bash "$runtime/run_skill.sh" \
  skills/self-improving-agent/run_self_improving_agent.sh review-pending
```

Windows PowerShell example:

```powershell
$runtime = if ($env:AAS_RUNTIME_ROOT) { $env:AAS_RUNTIME_ROOT } else { "$env:LOCALAPPDATA\ai-agents-skills\runtime" }
& "$runtime\run_skill.ps1" "skills/self-improving-agent/run_self_improving_agent.ps1" review-pending
```

Windows CMD example:

```bat
"%AAS_RUNTIME_ROOT%\run_skill.bat" skills/self-improving-agent/run_self_improving_agent.bat review-pending
```

Common helper commands:

```bash
bash "$runtime/run_skill.sh" \
  skills/self-improving-agent/run_self_improving_agent.sh review-pending --high-only
```

```bash
bash "$runtime/run_skill.sh" \
  skills/self-improving-agent/run_self_improving_agent.sh check-command-safety \
  "git push --force origin main"
```

```bash
some_command 2>&1 | bash "$runtime/run_skill.sh" \
  skills/self-improving-agent/run_self_improving_agent.sh detect-common-errors
```

```bash
bash "$runtime/run_skill.sh" \
  skills/self-improving-agent/run_self_improving_agent.sh integration-plan \
  --summary "Make paper-review routing durable across agents" \
  --skill paper-review \
  --target codex --target claude --target deepseek \
  --os linux --os windows
```

## Suggested verification

For a learning-only entry, inspect the entry and mark unresolved target/OS
claims as unverified. For behavior-affecting repo changes, choose the narrowest
relevant checks, commonly:

```bash
make docs
make test
make runtime-smoke ARGS="--skills self-improving-agent"
make fake-root-lifecycle ARGS="--skill self-improving-agent --platform-shape all"
```

Run native Windows checks with `make.bat` on Windows before claiming native
PowerShell/CMD execution. Linux-hosted `--platform-shape windows` checks are
useful install-shape evidence, not native execution evidence.

## Read only when needed

- `assets/LEARNINGS.md`
- `assets/ERRORS.md`
- `assets/FEATURE_REQUESTS.md`
- `references/examples.md` for compact sample entries

## Rules

- Keep entries short, concrete, and actionable.
- Do not write durable learning entries during a report-only, diagnosis-only,
  review-only, or investigation-only request unless the user explicitly asks
  for persistence. Report the lesson or proposed integration plan first, then
  wait for permission to modify files.
- Prefer canonical repo changes over one-off edits in agent homes.
- Separate inspected, inferred, and blocked target/OS coverage.
- Do not depend on OpenClaw hooks, session-spawn tools, or local runtime state
  unless the specific task explicitly requires them.
- Treat helper commands as convenience tools; the durable skill behavior is the
  structured learning plus canonical integration plan.
