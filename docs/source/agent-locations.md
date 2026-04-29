# Agent Locations

The installer detects agent homes first. If an agent home is absent, that agent
is skipped and its target-specific files are not planned.

| Agent | Home | Skill target | Instruction file |
|---|---|---|---|
| Codex | `~/.codex` | `~/.codex/skills/<skill>/` | `~/.codex/AGENTS.md` |
| Claude | `~/.claude` | `~/.claude/skills/<skill>/` | `~/.claude/CLAUDE.md` |
| DeepSeek | `~/.deepseek` | `~/.deepseek/skills/<skill>/` | `~/.deepseek/AGENTS.md` |

Optional or compatibility skill locations:

| Agent | Optional location | Meaning |
|---|---|---|
| Codex | `~/.agents/skills` | Optional workspace/local target where supported; not the default global target. |
| DeepSeek | `~/.agents/skills`, `./skills` | Workspace-local locations that may shadow global DeepSeek skills. |

Optional artifact-class target directories:

| Agent | Personas | Templates | Commands | Tool shims |
|---|---|---|---|---|
| Codex | `~/.codex/agents` | `~/.codex/templates` | `~/.codex/commands` | `~/.codex/tools` |
| Claude | `~/.claude/agents` | `~/.claude/templates` | `~/.claude/commands` | `~/.claude/tools` |
| DeepSeek | `~/.deepseek/agents` | `~/.deepseek/templates` | `~/.deepseek/commands` | `~/.deepseek/tools` |

These optional artifact classes are intentionally not installed by default.
Instruction docs target each agent's `instructions` directory. Entrypoint
aliases target Claude commands, but Codex and DeepSeek receive reference docs
under `instructions/entrypoints` because equivalent slash-command loading is
not assumed.

They require explicit profiles because commands, personas, hooks, and tool
shims can affect behavior more broadly than a normal skill directory.

Instruction files are modified through managed marker blocks only. Uninstall
and rollback remove only those managed blocks and managed files.
