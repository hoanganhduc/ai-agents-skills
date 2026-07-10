# Installation

This page describes safe installation flows. The installer is conservative:
planning and dry-run previews are the default workflow, and real home-directory
writes require both `--apply` and `--real-system`.

Use `make precheck` or `make.bat precheck` first when installing on a new
machine. The launchers detect a usable runtime instead of requiring a specific
command name. Use `plan` before `install`. Partial installs are first-class:
select `--skill`, `--skills`, or `--profile`. Artifact installs are also
partial: select `--artifact`, `--artifacts`, or `--artifact-profile`.

`doctor` is a quick required-tool check. `precheck` is broader: it detects
required tools, optional tools, Python packages, remote-service configuration
placeholders, detected agents, skipped agents, ignored dependencies,
target-specific prechecks, and Windows/WSL substrate information where
possible. `precheck --json` includes `target_prechecks` for every requested or
detected target. Each entry reports host-inspected home, skill, instruction,
optional skill, artifact-directory, install-mode, and read-policy metadata; the
`path_style` field labels the selected platform path convention but the `path`
values remain paths inspected from the current host/root. Target prechecks do
not read target file contents; known auth-token sources are reported by
presence only rather than value. `external_agent_prechecks` reports sanitized
Claude, DeepSeek, Copilot, Antigravity, and reference-only OpenClaw delegation readiness,
including latest-model/highest-thinking probe requirements and nested-worker
capability status. Copilot extends the base precheck with CLI
detection, the `.copilot` directory shape, redacted auth-source presence,
provider/model probe status, delegation authority metadata, and a separate
`copilot_status` field for CLI/account/model readiness; command arguments and
version output are redacted.

`delegate-agent` is the live external CLI adapter for parent-owned
cross-provider runs. Use `delegate-agent --dry-run` first; actual external
process launch requires `--allow-external-cli`. Research launch is fail-closed
unless a provider dispatch command and resolved latest-model/highest-thinking
settings are available.

OpenClaw
prechecks report the current fake-root-only gate and evidence requirements
without enabling real `.openclaw` writes.

OpenClaw real-system runtime install (advanced, evidence-gated): runtime-backed
skills can be installed to a real OpenClaw host through a separate fail-closed
flow, distinct from the v2 skill-file path. The sequence is
`openclaw-runtime-probe` (mint native-loader/quiescence/neutral-root evidence on
a quiescent host), `openclaw-runtime-dry-run-manifest` (build a content-addressed
runtime manifest), `openclaw-runtime-approve-manifest`, then
`openclaw-runtime-apply-manifest --real-system` with the confirmation phrase and
verify-before-write. Apply writes inert support files under
`.openclaw/skills/<skill>/` and executable runtime files under a validated
neutral runtime root outside `.openclaw`. Executable files are not run inside the
OpenClaw sandbox; the host `openclaw-broker` (started with `--serve`, a per-agent
capability token file, and a managed host firewall rule) exposes them to the
sandboxed agent with per-agent tokens and verify-before-exec. This path is
optional and host-gated; the default remains fake-root-only.
OpenCode prechecks report the user-global `~/.config/opencode` target,
OpenCode-native artifact directories, copy-mode default, and native smoke
expectations without reading config contents or credentials.
Antigravity prechecks report the user-global
`~/.gemini/antigravity-cli` target, flat global skill directory, managed plugin
payload, sparse settings file, plugin-scoped MCP config, plugin-scoped hooks
config, `agy` CLI discovery status, and native smoke expectations without
reading config contents or credentials.
`audit-system` is read-only and compares the selected repo profile with the
current agent homes, managed state, legacy aliases, unmanaged files, dependency
status, and install-plan summaries.

Before running installer commands, clone the repository and run commands from
the repo root. The launchers need Python 3.10 or newer. On Linux and macOS,
use `make` or `./installer/bootstrap.sh`; on Windows, use `make.bat`, which
requires `pwsh` or `powershell.exe`. The direct Python entrypoint is useful for
debugging wrapper behavior:

```bash
python3 -m installer.ai_agents_skills help
python3 -m installer.ai_agents_skills describe zotero
```

Use `list-skills`, `list-artifacts`, `describe`, and `describe-artifact` to
inspect manifest content without planning writes. Use `make docs` to
regenerate generated docs and `make docs-site` to build the Sphinx site after
installing `docs/requirements.txt`.

Use `precheck --interactive` for a guided one-by-one pass through missing
dependencies. It does not install packages automatically; it shows the install
hint, lets the user skip or ignore a dependency, and tells them to rerun
`precheck` after installing software.

`install --dry-run` previews the same actions as a default install preview;
`install --apply` is required before any writes occur. Applied installs,
uninstalls, and rollbacks are interactive: before writing files, the installer
explains the install, uninstall, and rollback process and requires the user to
type the displayed confirmation phrase. Real home-directory writes additionally
require `--real-system`.

After a successful `install --apply`, the installer runs post-install smoke in
`auto` mode. That means it verifies managed installer state, checks
agent-visible skill files, and runs offline runtime smoke for selected
runtime-backed skills with safe smoke contracts. When OpenCode is selected and
the `opencode` CLI is available, it also runs isolated native discovery smoke
for OpenCode paths, skills, and agents. When Antigravity is selected and the
`agy` CLI is available, it runs isolated native smoke for `agy --help`,
`agy plugin list`, global skill file shape, plugin manifest, MCP config, hook
config, and settings scaffolds. These checks write a bounded report under
`.ai-agents-skills/runs/` and use temporary scratch directories for runtime
outputs. They do not configure credentials, start servers, install packages,
or call live services. Antigravity MCP and hook files are no-op JSON scaffolds
unless a future manifest declares live entries. Use
`--post-install-smoke strict` in automation to make degraded smoke fail the
command, `--post-install-smoke verify` for integrity-only checks, or
`--post-install-smoke off` to skip post-install checks.

## Safe First Install

Linux:

```bash
make doctor
make precheck ARGS="--profile research-core"
make plan ARGS="--profile research-core"
make install ARGS="--profile research-core --dry-run"
make lifecycle-test ARGS="--matrix default --platform-shape all"
```

Windows:

```bat
make.bat doctor
make.bat precheck --profile research-core
make.bat plan --profile research-core
make.bat install --profile research-core --dry-run
make.bat lifecycle-test --matrix default --platform-shape windows
```

To test file writes without touching a real agent home, use a fake root:

```bash
make lifecycle-test ARGS="--matrix default --platform-shape all"
make fake-root-lifecycle ARGS="--profile research-core --platform-shape linux"
make fake-root-lifecycle ARGS="--profile research-core --platform-shape all"
```

Fake-root plans detect only agent homes that exist under the fake root. Create
`.codex`, `.claude`, `.deepseek`, `.copilot`, `.config/opencode`,
`.gemini/antigravity-cli`, or `.openclaw` inside the fake root for the agents
you want to exercise; a fake root with no agent homes produces no install actions,
no managed installer state, and later verification may report
`no-managed-artifacts`.

Real-system writes should be a final step after reviewing `plan` output:

```bash
make install ARGS="--profile research-core --apply --real-system"
make install ARGS="--profile research-core --apply --real-system --post-install-smoke strict"
```

## Runtime Files

`--runtime-profile auto` is the default. When a selected skill has declared
portable runtime files, the installer copies those files into a runtime root
and records them as root-scoped `runtime-file` artifacts. They are not installed
inside each agent's skill directory. Use `--no-runtime` or
`--runtime-profile none` to skip runtime files, and use `--runtime-root` to
choose a non-default runtime location.

Default runtime roots:

- Codex-only installs: `<root>/.codex/runtime`
- Windows multi-agent or non-Codex installs: `<root>/AppData/Local/ai-agents-skills/runtime`
- Linux/macOS multi-agent or non-Codex installs: `<root>/.local/share/ai-agents-skills/runtime`

Before promoting files from an existing local runtime into this repo, inspect
that source with the read-only inventory command:

```bash
python3 -m installer.ai_agents_skills --json runtime-inventory --source-root <runtime-root>
```

The inventory denies configs, databases, caches, downloaded documents, SQLite
sidecars, symlinks, personal paths, sensitive material, and persistence markers
such as cron, systemd, launchd, scheduled tasks, and Docker
`restart: unless-stopped`.

Runtime-backed skill config should be a local live file under the installed
runtime workspace or passed explicitly, not a canonical runtime source file.
For Docling, start from the tracked `docling.example.toml`, then place the live
config at `$AAS_RUNTIME_WORKSPACE/config/docling.toml` or pass it with
`--config`. The inventory allows example config templates but denies live
`config.toml`, `workspace/config/*.toml`, caches, bytecode, and downloaded
documents so credentials and local state are not accidentally promoted.

`self-improving-agent` also uses the shared runtime for its portable helpers.
Use those runtime commands instead of paths inside an agent skill directory:
reference install mode deliberately points back to the canonical repo and does
not copy support files. The helper smoke contract is offline and checks the
learning review, command-safety, error-detection, and integration-plan command
surface without reading credentials or live config.

## Install Modes

`--install-mode auto` is the default. The installer resolves that request per
agent based on recorded agent-loader policy and source availability, and it
records the reason in `plan --json`. Symlink creation itself is verified during
apply; if a symlink cannot be created, skill files fall back to reference
adapters and support files fall back to copied files.

Codex, DeepSeek, Copilot, OpenCode, and Antigravity are compatibility
exceptions. Current Codex skill discovery loads regular user `SKILL.md` files
but ignores file-symlinked user `SKILL.md` files. DeepSeek native symlinked
`SKILL.md` loading has not been verified. Copilot agent skills are regular
`SKILL.md` files in `~/.copilot/skills` or `.github/skills`; symlinked skill
loading is not assumed. OpenCode native skills are regular files under
`~/.config/opencode/skills`, and auto mode copies canonical skill files plus
support files for cross-platform parity. Antigravity global skills are flat
Markdown files under `~/.gemini/antigravity-cli/skills/<skill>.md`, so auto
mode copies the full canonical skill body into that native global skill
directory and creates the managed Antigravity plugin/config scaffolds. In
default auto mode, Codex, DeepSeek, and Copilot resolve skill files to reference
adapters that point at the canonical repo skill, while OpenCode and Antigravity
resolve to copy mode. `plan --json` shows the effective `install_mode`, `mode_reason`,
`capability_evidence`, and fallback mode for each target before anything is
written.

Use `--install-mode symlink` to force symlinked skill files for every agent.
This is useful for testing future loader behavior, but it can produce Codex
skill targets that current Codex will not discover.

Use `--install-mode reference` for agents or environments that should not load
symlinked skills. This mode writes a thin adapter into every agent settings
directory, using `SKILL.md` for directory-shaped targets and `<skill>.md` for
Antigravity. The adapter tells the agent where the canonical repo skill file is
and does not copy support files. If a previously managed skill is switched to
reference mode, obsolete managed support files may be planned for removal
because the adapter now points back to the repo copy.

When targeting a mounted Windows profile from Linux or WSL, verify that the
reference path written into the adapter is readable by the target agent runtime.
If the agent actually runs on native Windows and cannot read the POSIX repo
path, use a native Windows checkout or `--install-mode copy`.

Use `--install-mode copy` only when the agent must have regular files inside
its settings directory. Copy mode materializes skill files and support files
with managed metadata, so it uses more space and needs reinstalling after repo
skill changes.

Optional artifacts outside skill directories are always copied because agents
do not load them as canonical skill source.

## Selection Model

- `--profile research-core` selects a workflow bundle.
- `--skill zotero` selects one skill.
- `--skills zotero,docling` selects a comma-separated skill set.
- `--no-skills --artifact-profile workflow-templates` installs only optional
  artifacts.
- `--with-deps` lets dependency-bound artifacts bring in their backing skills.

See [Profiles](profiles.md), [Skills](skills.md), and
[Optional Artifacts](artifacts.md) for the available selectors.

## Conflict Modes

- default: create missing managed files and skip unmanaged or legacy files
- `--adopt`: record an existing target file as user-owned managed state; verify
  tracks its recorded hash instead of requiring managed marker text
- `--backup-replace`: back up and replace an unmanaged target file
- `--migrate`: install a detected legacy skill under the canonical name using
  the selected install mode, then back up and remove the legacy alias directory

Instruction blocks are installed only when the corresponding skill artifact is
actually installed, adopted, updated, already managed, or migrated. A skipped
skill does not receive an `AGENTS.md` or `CLAUDE.md` block.

Optional artifacts are not installed by default. Use `--no-skills` when you
want an artifact-only install. Use `--with-deps` when selected dependency-bound
artifacts should bring in their required backing skills.

For an existing personal system, prefer staged migration:

1. Run `precheck --profile full-research`.
2. Run `audit-system --profile full-research`.
3. Review `plan --profile full-research --migrate` for legacy aliases.
4. Review `plan --profile full-research --adopt` for canonical files that
   already exist but are not managed.
5. Apply one small selected scope at a time, then run `verify`.

Use `--artifact-profile repo-management` when you want a top-level managed
notice in `AGENTS.md` or `CLAUDE.md` without installing every skill.

Scenario summary:

| Scenario | Result |
|---|---|
| Agent home absent | Agent is skipped; its dependencies are not required. |
| Skill absent | Managed skill files and support files are created. |
| Skill already managed | Files are updated or left unchanged according to hashes. |
| Skill exists unmanaged | Default plan skips it; use `--adopt` or `--backup-replace` explicitly. |
| Legacy alias exists | Default plan skips; `--migrate` installs the canonical target, backs up the legacy alias directory, and removes the legacy alias directory. |
| Agent rejects symlinked skills | Auto mode already resolves Codex, DeepSeek, and Copilot skill files to reference adapters, while OpenCode and Antigravity use copy mode. Use `--install-mode reference` to force adapters for every agent; use `copy` only if regular files are unavoidable. |
| Top-level management notice selected | Adds a removable managed block explaining repo/source ownership boundaries. |
| Dependency-bound artifact selected without dependency | Artifact is blocked and skipped until the backing skill is managed or selected with `--with-deps`. |
| Persona selected | Codex gets TOML, Claude and OpenCode get Markdown frontmatter, Antigravity gets plugin-scoped Markdown frontmatter, Copilot gets `.agent.md`, and DeepSeek gets a reference prompt. |
| Windows SageMath | Prefer WSL-backed detection when native SageMath is absent. |

Related pages: [Dependencies](dependencies.md), [Audit And Migration](audit-and-migration.md),
[Verification](verification.md), [Troubleshooting](troubleshooting.md).
