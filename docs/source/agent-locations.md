# Agent Locations

The installer detects agent homes first. If an agent home is absent, that agent
is skipped and its target-specific files are not planned.

Use this page when checking where files will be installed or why one agent was
skipped. The paths below are target locations, not source locations. Canonical
source content stays in this repository under `canonical/` and `manifest/`.

| Agent | Home | Skill target | Instruction file |
|---|---|---|---|
| Codex | `~/.codex` | `~/.codex/skills/<skill>/` | `~/.codex/AGENTS.md` |
| Claude | `~/.claude` | `~/.claude/skills/<skill>/` | `~/.claude/CLAUDE.md` |
| DeepSeek | `~/.deepseek` | `~/.deepseek/skills/<skill>/` | `~/.deepseek/AGENTS.md` |
| Copilot | `~/.copilot` | `~/.copilot/skills/<skill>/` | not modified |
| OpenCode | `~/.config/opencode` | `~/.config/opencode/skills/<skill>/` | `~/.config/opencode/AGENTS.md` |
| Antigravity | `~/.gemini/antigravity-cli` | `~/.gemini/antigravity-cli/skills/<skill>.md` | `~/.gemini/GEMINI.md` |
| OpenClaw | `~/.openclaw` | `~/.openclaw/skills/<skill>/` | not modified |

Optional or compatibility skill locations:

| Agent | Optional location | Meaning |
|---|---|---|
| Codex | `~/.agents/skills` | Optional workspace/local target where supported; not the default global target. |
| DeepSeek | `~/.agents/skills`, `./skills` | Workspace-local locations that may shadow global DeepSeek skills. |
| Copilot | `~/.agents/skills` | Compatibility location reported but not used as the primary target. |
| OpenCode | `~/.claude/skills`, `~/.agents/skills` | Compatibility locations reported but not used as the primary write target. |
| Antigravity | `.agents/skills`, `~/.gemini/skills` | Workspace and Gemini compatibility locations reported but not used as the global write target. |

Optional artifact-class target directories:

| Agent | Personas | Templates | Commands | Tool shims |
|---|---|---|---|---|
| Codex | `~/.codex/agents` | `~/.codex/templates` | `~/.codex/commands` | `~/.codex/tools` |
| Claude | `~/.claude/agents` | `~/.claude/templates` | `~/.claude/commands` | `~/.claude/tools` |
| DeepSeek | `~/.deepseek/agents` | `~/.deepseek/templates` | `~/.deepseek/commands` | `~/.deepseek/tools` |
| Copilot | `~/.copilot/agents` | not supported | not supported | not supported |
| OpenCode | `~/.config/opencode/agents` | `~/.config/opencode/templates` | `~/.config/opencode/commands` | `~/.config/opencode/tools` |
| Antigravity | `~/.gemini/antigravity-cli/plugins/ai-agents-skills/agents` | `~/.gemini/antigravity-cli/plugins/ai-agents-skills/templates` | `~/.gemini/antigravity-cli/skills/<alias>.md` | `~/.gemini/antigravity-cli/plugins/ai-agents-skills/tools` |
| OpenClaw | not supported | not supported | not supported | not supported |

Rendered artifact behavior differs by agent:

| Artifact | Codex | Claude | DeepSeek | Copilot | OpenCode | Antigravity | OpenClaw |
|---|---|---|---|---|---|---|---|
| Skill file in auto mode | Reference adapter by default. | Symlink to canonical skill when supported. | Reference adapter by default. | Reference adapter in `~/.copilot/skills`. | Copied native `SKILL.md` plus support files. | Flat Markdown reference adapter in `~/.gemini/antigravity-cli/skills`. | Copy-only in fake roots for eligible `SKILL.md` files. |
| Persona | TOML custom-agent file. | Markdown subagent file. | Reference prompt. | `.agent.md` custom-agent profile. | Markdown subagent file. | Plugin-scoped Markdown agent definition. | Not supported. |
| Entrypoint alias | Reference doc under `instructions/entrypoints`. | Command file. | Reference doc under `instructions/entrypoints`. | Not supported by this installer target. | Command file. | Flat Markdown global skill alias. | Not supported. |
| Management notice | Managed block in `AGENTS.md`. | Managed block in `CLAUDE.md`. | Managed block in `AGENTS.md`. | Not supported; Copilot instruction files are not modified. | Managed block in `AGENTS.md`. | Managed block in `~/.gemini/GEMINI.md`. | Not supported; OpenClaw instruction files are not modified. |

Instruction docs target each agent's `instructions` or rules directory.
Entrypoint aliases target Claude and OpenCode commands and Antigravity global
Markdown skill aliases, but Codex and DeepSeek receive reference docs under
`instructions/entrypoints` because equivalent slash-command loading is not
assumed.

Self-improvement records are workspace data, not agent-home source files.
`self-improving-agent` writes or reviews `.learnings/` entries in the current
workspace, then proposes canonical changes against this repository checkout.
The generated target files should be updated through `plan` and `install`
after the canonical repo change has been verified.

Copilot is included in default target detection when `~/.copilot` exists.
Existing repository-level Copilot files under `.github/` do not activate the
personal Copilot target. The installer reports repository Copilot surfaces in
precheck metadata, but the home-root install path writes only
`~/.copilot/skills` and optional `~/.copilot/agents` files.

OpenCode is included in default target detection when `~/.config/opencode`
exists. Project `.opencode/` directories are project-local and do not activate
the global OpenCode target. The installer writes global OpenCode skills, rules,
agents, commands, templates, instruction docs, and declared tool/plugin
artifacts under `~/.config/opencode`.

Antigravity is included in default target detection when
`~/.gemini/antigravity-cli` exists. Project `.agents/` directories are
workspace-local and do not activate the global Antigravity target. The
installer writes flat global Markdown skills, managed global context, and the
managed `ai-agents-skills` plugin payload under
`~/.gemini/antigravity-cli/plugins/ai-agents-skills`, including no-op MCP,
hook, and settings scaffolds.

OpenClaw is included in default target detection when an eligible `.openclaw`
fake-root home exists, and remains fake-root-only before native target
evidence. `precheck --json --agents openclaw` still reports the `.openclaw`
home shape and current gates for targeted checks, but real `.openclaw` writes,
runtime-backed skills, support files, symlink/reference modes, and
instruction-file edits remain blocked until the OpenClaw install-target plan
requirements are met.

These optional artifact classes are intentionally not installed by default.
They require explicit artifact selection because commands, personas, hooks, and
tool shims can affect behavior more broadly than a normal skill directory.

Instruction files are modified through managed marker blocks only. Uninstall
and rollback remove only recorded managed blocks and managed files; surrounding
user text is outside installer ownership.
The optional `management-notice:repo-management` artifact is also a managed
block in the instruction file; it does not replace existing user instructions.

Related pages: [Architecture](architecture.md), [Installation](installation.md),
[Optional Artifacts](artifacts.md), [Verification](verification.md).
