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
| `agent-persona` | Optional reviewer/persona files. Codex receives TOML custom agents, Claude receives Markdown subagents, and DeepSeek receives reference prompts. |
| `template` | Optional research, report, specification, and task templates. |
| `instruction-doc` | Optional workflow reference documents installed outside skill folders. |
| `entrypoint-alias` | Optional quick-action aliases. Claude receives command files; Codex and DeepSeek receive reference documents. |
| `command` | Reserved optional target class for direct command wrappers. |
| `tool-shim` | Reserved optional target class for DeepSeek or runtime helper tools. |

Codex user-level skills target `~/.codex/skills` in this setup. The optional
`.agents/skills` layout is treated as a compatibility or workspace target when
detected, not as the default global Codex target.
