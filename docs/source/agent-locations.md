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

Optional or compatibility skill locations:

| Agent | Optional location | Meaning |
|---|---|---|
| Codex | `~/.agents/skills` | Optional workspace/local target where supported; not the default global target. |
| DeepSeek | `~/.agents/skills`, `./skills` | Workspace-local locations that may shadow global DeepSeek skills. |
| Copilot | `~/.agents/skills` | Compatibility location reported but not used as the primary target. |

Optional artifact-class target directories:

| Agent | Personas | Templates | Commands | Tool shims |
|---|---|---|---|---|
| Codex | `~/.codex/agents` | `~/.codex/templates` | `~/.codex/commands` | `~/.codex/tools` |
| Claude | `~/.claude/agents` | `~/.claude/templates` | `~/.claude/commands` | `~/.claude/tools` |
| DeepSeek | `~/.deepseek/agents` | `~/.deepseek/templates` | `~/.deepseek/commands` | `~/.deepseek/tools` |
| Copilot | `~/.copilot/agents` | not supported | not supported | not supported |

Rendered artifact behavior differs by agent:

| Artifact | Codex | Claude | DeepSeek | Copilot |
|---|---|---|---|---|
| Skill file in auto mode | Reference adapter by default. | Symlink to canonical skill when supported. | Reference adapter by default. | Reference adapter in `~/.copilot/skills`. |
| Persona | TOML custom-agent file. | Markdown subagent file. | Reference prompt. | `.agent.md` custom-agent profile. |
| Entrypoint alias | Reference doc under `instructions/entrypoints`. | Command file. | Reference doc under `instructions/entrypoints`. | Not supported by this installer target. |
| Management notice | Managed block in `AGENTS.md`. | Managed block in `CLAUDE.md`. | Managed block in `AGENTS.md`. | Not supported; Copilot instruction files are not modified. |

Instruction docs target each agent's `instructions` directory. Entrypoint
aliases target Claude commands, but Codex and DeepSeek receive reference docs
under `instructions/entrypoints` because equivalent slash-command loading is
not assumed.

Copilot is explicit-only: `--agents copilot` is required. Existing
repository-level Copilot files under `.github/` do not activate the personal
Copilot target. The installer reports repository Copilot surfaces in precheck
metadata, but the home-root install path writes only `~/.copilot/skills` and
optional `~/.copilot/agents` files.

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
