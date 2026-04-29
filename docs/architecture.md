# Architecture

The manifests are the source of truth. The installer resolves canonical skills
to per-agent target artifacts and records ownership in a journal. Existing
unmanaged files are skipped by default.

Artifact classes:

| Artifact class | Current behavior |
|---|---|
| `skill-file` | Installs canonical `SKILL.md` into the agent skill directory. |
| `skill-support-file` | Installs canonical references, scripts, assets, templates, and agent notes inside the skill directory. |
| `instruction-block` | Adds or updates a managed block in `AGENTS.md` or `CLAUDE.md` only when the matching skill artifact is installed, adopted, updated, or migrated. |
| `agent-persona` | Reserved optional target class for reviewer/persona files. |
| `template` | Reserved optional target class for research, report, and task templates. |
| `command` | Reserved optional target class for command wrappers such as Claude slash commands. |
| `tool-shim` | Reserved optional target class for DeepSeek or runtime helper tools. |

Codex user-level skills target `~/.codex/skills` in this setup. The optional
`.agents/skills` layout is treated as a compatibility or workspace target when
detected, not as the default global Codex target.
