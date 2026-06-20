# Architecture

This page explains how the repository turns one canonical skill catalog into
agent-specific files for Codex, Claude, DeepSeek, GitHub Copilot, OpenCode,
and Antigravity CLI.

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
| `skill-file` | Default `auto` mode links Claude skill files to canonical `SKILL.md`. Codex, DeepSeek, and Copilot skill files resolve to reference adapters because symlinked skill loading is not assumed for those targets. OpenCode and Antigravity copy the full canonical skill body and support files by default; Antigravity writes flat global Markdown files under `~/.gemini/antigravity-cli/skills/<skill>.md`. Explicit reference and copy modes are available for all agents; Copilot symlink mode is blocked until loader evidence exists. |
| `skill-support-file` | Symlinks canonical references, scripts, assets, templates, and agent notes when the effective skill install remains symlinked; copied in copy mode; skipped in reference mode. |
| `instruction-block` | Adds or updates a managed block in `AGENTS.md` or `CLAUDE.md` only when the matching skill artifact is installed, adopted, updated, or migrated. |
| `management-notice` | Optional top-level managed block explaining that this repo is the source and local agent homes are runtime targets. |
| `agent-persona` | Optional reviewer/persona files. Codex receives TOML custom agents, Claude and OpenCode receive Markdown subagents, Antigravity receives plugin-scoped Markdown agent definitions, Copilot receives `.agent.md` custom-agent profiles, and DeepSeek receives reference prompts. |
| `template` | Optional research, report, specification, and task templates. |
| `instruction-doc` | Optional workflow reference documents installed outside skill folders. |
| `entrypoint-alias` | Optional quick-action aliases. Claude and OpenCode receive command files; Antigravity receives flat global Markdown skill aliases; Codex and DeepSeek receive reference documents. |
| `plugin` | Antigravity receives a managed `ai-agents-skills` plugin marker and payload directory when Antigravity artifacts are installed. |
| `mcp-config` | Antigravity receives a no-op plugin-scoped `mcp_config.json` scaffold with an empty `mcpServers` map. |
| `hook-config` | Antigravity receives a no-op plugin-scoped `hooks.json` scaffold. |
| `settings-file` | Antigravity receives a sparse no-op `settings.json` scaffold in its global CLI home. |
| `settings-hook-merge` | When the autonomous-research-loop runtime is installed for Claude, one managed `hooks.Stop` entry (tagged `_managedBy`/`_id`) is idempotently merged into `~/.claude/settings.json` and removed on uninstall; user-authored hooks are preserved. |
| `runtime-file` | Root-scoped copied runtime runners and skill helper files. Runtime files are never installed as per-agent skills and are verified by transformed source hash, newline policy, mode, and secret scan. |
| `command` | Reserved optional target class for direct command wrappers. |
| `tool-shim` | Reserved optional target class for DeepSeek or runtime helper tools. |

Target rendering is intentionally adapter-heavy where native behavior has not
been proven. Codex personas are TOML custom-agent files, Claude and OpenCode
personas are Markdown subagents, Antigravity personas are plugin-scoped
Markdown agent definitions, Copilot personas are `.agent.md` custom-agent
profiles, and DeepSeek personas are reference prompts. Claude and OpenCode
entrypoint aliases are command files, Antigravity entrypoint aliases are flat
global Markdown skill aliases, and Codex and DeepSeek entrypoint aliases are
reference documents under `instructions/entrypoints`.

Copilot is included in default target detection when `~/.copilot` exists.
Existing repository-level `.github/*` files do not activate the personal
Copilot target. The installer currently writes personal Copilot skill adapters
to `~/.copilot/skills/<skill>/SKILL.md` and optional personal custom-agent
profiles to `~/.copilot/agents/*.agent.md`. Repository Copilot surfaces such as
`.github/skills`, `.github/agents`,
`.github/copilot-instructions.md`, and `.github/instructions/**/*.instructions.md`
are reported in precheck metadata but are not written by the home-root
installer path.

OpenCode is included in default target detection when `~/.config/opencode`
exists. The installer writes user-global OpenCode skills, rules, agents,
commands, templates, and instruction docs under `~/.config/opencode`. Project
`.opencode/` directories remain project-local and do not activate the global
install target.

Antigravity is included in default target detection when
`~/.gemini/antigravity-cli` exists. The installer writes flat global Markdown
skills under `~/.gemini/antigravity-cli/skills/`, managed global context blocks
under `~/.gemini/GEMINI.md`, and the managed `ai-agents-skills` plugin payload
under `~/.gemini/antigravity-cli/plugins/ai-agents-skills/`. Project-local
`.agents/` directories remain project/workspace-local and do not activate the
global Antigravity target.

Codex user-level skills target `~/.codex/skills` in this setup. The optional
`.agents/skills` layout is treated as a compatibility or workspace target when
detected, not as the default global Codex target.

Portable runtime runners accept workspace-relative skill commands only. They
set runtime-specific environment variables such as `AAS_RUNTIME_ROOT`,
`AAS_RUNTIME_WORKSPACE`, and `AAS_SECRETS_FILE`. OpenCode, Antigravity, and
other non-Codex targets use the neutral shared `ai-agents-skills` runtime root
unless a runtime root is supplied explicitly. Absolute paths and `..` traversal
are rejected by the runtime wrappers.

Autonomous loop enforcement:

When an autonomous loop runs, stop-condition enforcement is built in rather than
hand-wired per project. One stop policy governs it: user requirements override
everything; otherwise the loop stops only when the required iteration count is
reached, credit or a spend cap is exhausted, the stated goal is resolved by a
machine-checkable success check, or the user asks to stop. Nothing else is a
valid stop. The policy ships as the `autonomous-loop-enforcement` instruction
rule, and a single runtime arbiter (`done`) derives the verdict both enforcement
paths consult.

- Interactive (Claude): a managed `hooks.Stop` entry is merged into
  `~/.claude/settings.json` (the `settings-hook-merge` surface). Its wrapper is
  fail-open: any error, timeout, missing runtime, re-entrancy, or kill switch
  (`AUTOLOOP_DISABLE`, removing the registry entry, or a `STOP_REQUESTED`
  sentinel) allows turn-end. It blocks turn-end only when the arbiter reports an
  active, unfinished loop for the session's project root.
- Headless (driver): `autoloop_driver.sh` runs one agent iteration per loop,
  exports `AUTOLOOP_DRIVER=1` so the interactive hook stands down, enforces a
  per-iteration timeout, and stops on the arbiter's verdict or after repeated
  iteration failures. It fails safe: if it cannot determine state, it stops
  rather than running unbounded.

Per-target enforcement is honest, not uniform: Claude supports both the Stop
hook and the driver; Codex and OpenCode are driver-only because they use TOML
configuration rather than a JSON `hooks` map; targets without a headless agent
run are documented as unsupported for built-in enforcement.

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
