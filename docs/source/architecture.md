# Architecture

This page explains how the repository turns one canonical skill catalog into
agent-specific files for Codex, Claude, DeepSeek, and explicit adapter targets
such as GitHub Copilot.

The manifests are the source of truth:

- `manifest/skills.yaml` defines canonical skill names, descriptions,
  supported agents, dependencies, and aliases.
- `manifest/profiles.yaml` defines selectable skill bundles.
- `manifest/artifacts.yaml` defines optional templates, personas,
  instruction docs, entrypoints, and management notices.
- `manifest/runtime.yaml` defines portable runtime runners and runtime-backed
  skill files that may be copied into a local runtime root.
- `manifest/delegation.yaml` defines cross-provider delegation policy,
  research model requirements, active/reference providers, and nested worker
  limits. The `delegate-agent` CLI consumes this policy for parent-owned live
  external CLI dispatch after explicit opt-in and run-specific probes.
- `manifest/dependencies.yaml` and `manifest/system-dependencies.yaml` define
  logical tools and sanitized maintainer-system dependency observations.

The primary manifests are JSON-compatible YAML files loaded and validated by
`installer/ai_agents_skills/manifest.py`. The JSON Schemas under
`manifest/schema/openclaw/` are for the gated OpenClaw integration pipeline,
not the primary installer manifest format.

The installer resolves those manifests into per-agent target artifacts and
records ownership in `.ai-agents-skills/state.json` under the selected root.
Existing unmanaged files are skipped by default.

Install flow:

1. Resolve selected skills and artifacts from `--skill`, `--skills`,
   `--profile`, `--artifact`, `--artifacts`, or `--artifact-profile`.
2. Detect available agent homes under the selected `--root`.
3. Resolve the requested install mode, then apply per-agent loader
   compatibility before linking, referencing, or copying canonical skill bodies
   into each supported target format.
4. Add managed instruction blocks only for skills or artifacts that are
   installed, adopted, migrated, updated, or already managed.
5. Add root-scoped `runtime-file` actions for selected runtime-backed skills
   according to `--runtime-profile` and `--runtime-root`.
6. Record hashes, source paths, install modes, and ownership metadata for
   verification, uninstall, and rollback.

Artifact classes:

| Artifact class | Current behavior |
|---|---|
| `skill-file` | Default `auto` mode links Claude skill files to canonical `SKILL.md`. Codex, DeepSeek, and Copilot skill files resolve to reference adapters because symlinked skill loading is not assumed for those targets. Explicit reference and copy modes are available for all agents; Copilot symlink mode is blocked until loader evidence exists. |
| `skill-support-file` | Symlinks canonical references, scripts, assets, templates, and agent notes when the effective skill install remains symlinked; copied in copy mode; skipped in reference mode. |
| `instruction-block` | Adds or updates a managed block in `AGENTS.md` or `CLAUDE.md` only when the matching skill artifact is installed, adopted, updated, or migrated. |
| `management-notice` | Optional top-level managed block explaining that this repo is the source and local agent homes are runtime targets. |
| `agent-persona` | Optional reviewer/persona files. Codex receives TOML custom agents, Claude receives Markdown subagents, Copilot receives `.agent.md` custom-agent profiles, and DeepSeek receives reference prompts. |
| `template` | Optional research, report, specification, and task templates. |
| `instruction-doc` | Optional workflow reference documents installed outside skill folders. |
| `entrypoint-alias` | Optional quick-action aliases. Claude receives command files; Codex and DeepSeek receive reference documents. |
| `runtime-file` | Root-scoped copied runtime runners and skill helper files. Runtime files are never installed as per-agent skills and are verified by transformed source hash, newline policy, mode, and secret scan. |
| `command` | Reserved optional target class for direct command wrappers. |
| `tool-shim` | Reserved optional target class for DeepSeek or runtime helper tools. |

Target rendering is intentionally adapter-heavy where native behavior has not
been proven. Codex personas are TOML custom-agent files, Claude personas are
Markdown subagents, Copilot personas are `.agent.md` custom-agent profiles,
and DeepSeek personas are reference prompts. Claude entrypoint aliases are
command files, while Codex and DeepSeek entrypoint aliases are reference
documents under `instructions/entrypoints`.

Copilot is included in default target detection when `~/.copilot` exists.
Existing repository-level `.github/*` files do not activate the personal
Copilot target. The installer currently writes personal Copilot skill adapters
to `~/.copilot/skills/<skill>/SKILL.md` and optional personal custom-agent
profiles to `~/.copilot/agents/*.agent.md`. Repository Copilot surfaces such as
`.github/skills`, `.github/agents`,
`.github/copilot-instructions.md`, and `.github/instructions/**/*.instructions.md`
are reported in precheck metadata but are not written by the home-root
installer path.

Codex user-level skills target `~/.codex/skills` in this setup. The optional
`.agents/skills` layout is treated as a compatibility or workspace target when
detected, not as the default global Codex target.

Portable runtime runners accept workspace-relative skill commands only. They
set runtime-specific environment variables such as `AAS_RUNTIME_ROOT`,
`AAS_RUNTIME_WORKSPACE`, and `AAS_SECRETS_FILE`, and set OpenClaw-compatible
variables only to Codex-owned runtime paths. Absolute paths and `..` traversal
are rejected by the runtime wrappers.

Safety boundary:

- auth files, API keys, provider config, session logs, downloaded libraries,
  and local runtime state are not managed by this repo
- runtime configs, databases, caches, downloaded documents, symlinks, and
  persistence-oriented scripts are denied by runtime inventory and source gates
- unmanaged user files are skipped unless `--adopt`, `--backup-replace`, or
  `--migrate` is selected explicitly
- uninstall and rollback require confirmation when applied and affect only
  recorded managed artifact paths and managed marker blocks

Related pages: [Installation](installation.md), [Agent Locations](agent-locations.md),
[Verification](verification.md), [Uninstall And Rollback](uninstall-rollback.md).
