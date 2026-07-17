## Plan: Add `grok` as a first-class ai-agents-skills install target (Windows/macOS/Linux)

> Revised to close every critical/major finding from the three adversarial reviews
> (cross-platform, registration-correctness, grok-fidelity) plus their minors.
> Load-bearing corrections vs. the prior draft:
> 1. **Hooks sink is `~/.grok/hooks/*.json`, NOT `~/.grok/settings.json`** (critical). Grok never reads `~/.grok/settings.json` for hooks — its only `settings.json` source is `~/.claude/settings.json` via `[compat.claude]`.
> 2. **`instruction-doc` routes to `~/.grok/rules/`, NOT `~/.grok/instructions/`** (major). `instructions/` is not a grok discovery path; `rules/` is grok's documented rules-directory name (and antigravity already maps `instruction-doc → rules/`). Home-scope `rules/` loading is flagged unverified.
> 3. **Delegation is explicitly scoped OUT** (major). grok is an install target only, not a cross-agent delegation provider; the `0c886e9` delegation-mirror claim is dropped, and the delegation registration/test sites are deliberately NOT touched.
> 4. **`tests/test_installer.py:596` (skipped_agents `7 → 8`)** and **`tests/test_runtime_integration.py:310` (6-agent runtime-file loop)** are added to the change-set (guaranteed breakage otherwise).
> 5. **`templates/`, `tools/` are inert support storage, not grok-loaded surfaces** — surface rows relabeled with honest `claim_basis`.
> 6. **`GROK_HOME` handling made explicit**; **native smoke pins `GROK_HOME` to the fake root**; **`[compat.claude]` double-load documented with concrete consequence + mitigation**.

### 1. Overview

Grok Build (xAI's `grok` CLI, v0.2.93) stores everything under a fixed home `~/.grok` (`%USERPROFILE%\.grok` on Windows; documented runtime override `GROK_HOME`, 05-configuration.md:725). Its surfaces are close to Claude Code but NOT identical — the differences the prior draft got wrong are the whole point of this revision:

- **Skills:** directory-layout `~/.grok/skills/<name>/SKILL.md`, YAML frontmatter is a documented superset of the canonical frontmatter (08-skills.md).
- **Instructions (home scope):** the single native `~/.grok/AGENTS.md` (there is NO `GROK.md`). `CLAUDE.md`/`CLAUDE.local.md` are also read at home scope via compat, but AGENTS.md is grok's own file.
- **Rules directories:** `~/.grok/rules/*.md` uses grok's documented rules-directory *name*, but home-scope rules loading is explicitly documented only for AGENTS.md-style files; the `<dir>/.grok/rules/` table row is scoped repo-root→cwd. Treat home-scope `rules/` loading as **unverified**.
- **Subagents:** Markdown+YAML `~/.grok/agents/*.md` (grok also natively reads `~/.claude/agents/*.md`). Grok's tool-restriction frontmatter uses grok tool ids (`read_file`, `grep`, …); Claude tool names in a `tools:` list do NOT resolve — persona files install as name/description overlays with tool/model restrictions unenforced on grok.
- **Slash commands:** every `user-invocable` skill auto-surfaces as `/<name>`, plus flat `~/.grok/commands/*.md`.
- **Hooks:** discovered ONLY from `~/.grok/hooks/*.json` (per-file `{"hooks": {<Event>: [...]}}` objects) at home scope, plus compat sources `~/.claude/settings.json` and `~/.cursor/hooks.json` (10-hooks.md:59-74). `~/.grok/settings.json` is **not** a hook source and does not exist in a stock `~/.grok`.
- **Config / MCP:** `~/.grok/config.toml` (TOML), `[mcp_servers.*]`, `[[marketplace.sources]]`, `[compat.*]`.
- **Compat ride-along:** `[compat.claude]` is default-on and scans `~/.claude/skills/`, `~/.claude/` (CLAUDE.md), `~/.claude/rules/`, `~/.claude/settings.json` (hooks). The native `~/.grok` install exists to be self-contained and toggle-independent — but see §6 for the concrete double-load consequence when both targets are installed.

Because the installer is generic over `AgentTarget` + a capability policy (installer-core aspect), the entire lifecycle (plan → apply → verify → smoke → rollback → uninstall) works for a new target the moment `target_for()` resolves it and a loader policy exists. This plan mirrors the **antigravity target-registration change-set** (commits 93d4572 core + 6a37b04 fixup) for identity/surface shape. It deliberately does **NOT** mirror the `0c886e9` delegation commit — grok is registered as an install target only (see §3 "Delegation — scoped OUT").

**Four load-bearing decisions (recommended, justified below):**

- **(A) Manifest membership: ADAPTER.** Add `grok` to `ADAPTER_AGENT_NAMES` (agents.py:15), NOT `PORTABLE_MANIFEST_AGENT_NAMES`. `agent_supports_manifest_entry` (agents.py:240-246) then makes grok inherit every skill/artifact whose `supported_agents` intersects `{codex,claude,deepseek}` — all 49 skills + all artifacts — with **zero edits to `manifest/skills.yaml` / `manifest/artifacts.yaml`** and without breaking `test_portable_manifest_entries_explicitly_support_adapter_targets` (which hardcodes only opencode+antigravity). Manifest membership is orthogonal to render mode. Minimal-blast-radius choice.
- **(B) Install mode: `copy`.** `AGENT_SKILL_LOADER_POLICY["grok"] = {symlink_skill_file: False, default_mode: "copy", ...}` (capabilities.py:7). Copy yields a self-contained `~/.grok/skills/<skill>/`, independent of the repo checkout and of `[compat.claude]`. Symlink rejected (grok symlink-loading unverified; Windows symlink privilege-gated); reference rejected (points at a canonical repo path the user may not have). An unset policy silently falls through to the symlink default (capabilities.py:59-66) → this entry is mandatory.
- **(C) Skill layout: `directory`** (`skills_dir/<skill>/SKILL.md`) — installer default, matching 08-skills.md. `skill_path_is_agent_visible` (agents.py:234 / capabilities.py:183) already returns the correct rule for every non-antigravity agent; no new visibility branch.
- **(D) Instructions: `~/.grok/AGENTS.md`, `instruction_blocks_enabled=True`.** AGENTS.md is grok's documented home-scope instruction surface; the managed instruction/notice block routes there.

**Settings/hooks scope decision (CORRECTED).** Current canonical skills ship NO MCP servers and only one hook: the claude-only `autonomous-research-loop` Stop hook. Delivered in tiers:
1. **Baseline (native, zero new machinery):** managed instruction block in `~/.grok/AGENTS.md`.
2. **OPTIONAL autoloop hook tier — native hook FILE, not a settings merge.** Grok reads hooks from `~/.grok/hooks/*.json`. Therefore the autoloop Stop hook is written as a discrete, fully-managed file `~/.grok/hooks/ai-agents-skills-autoloop.json` with grok's documented shape `{"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "python3 <runtime> hook-check"}]}]}}`. This requires a new `"hooks"` entry in grok's `artifact_dirs` and a dedicated planner branch — it is **NOT** a verbatim reuse of the claude `settings.json` json-merge path (that path is a dead sink for grok). Only fires when `autonomous-research-loop-runtime` is installed. (Note: because `[compat.claude]` is default-on, grok already partially sees the claude autoloop hook via `~/.claude/settings.json`; the native file is what makes grok self-contained.)
3. **DEFERRED:** `[mcp_servers]` / `[[marketplace.sources]]` in `config.toml` needs a new TOML merge helper — out of scope until a skill ships an MCP server (see Risks/Open Questions). Baseline does NOT write `config.toml` or `settings.json` (Codex-style, driver-owned) — safe and idempotent.

### 2. Grok surfaces map (authoritative, from `/home/ubuntu/.grok/docs/user-guide/` + `~/.grok/README.md`)

| Surface | Grok native path | Format | artifact_type | Mechanism | Loaded by grok? | claim_basis |
|---|---|---|---|---|---|---|
| Skills | `~/.grok/skills/<name>/SKILL.md` (+ `scripts/`,`references/`) | YAML+MD | `skill-file` | `copy` (directory layout) | Yes (08-skills.md) | `official-docs` |
| Instructions | `~/.grok/AGENTS.md` (home scope) | MD | instruction-block + management-notice | `instruction-block` | Yes (12-project-rules.md:24-46) | `official-docs` |
| Subagents | `~/.grok/agents/*.md` | MD+YAML | `agent-persona` | `copy` (claude-style MD, name/desc overlay only) | Yes; `tools:` restrictions NOT honored | `official-docs` |
| Slash commands | auto from `user-invocable` skills; flat `~/.grok/commands/*.md` | MD(+fm) | `entrypoint-alias`, `command` | `native-command` | Yes | `official-docs` |
| Instruction docs | `~/.grok/rules/*.md` | MD | `instruction-doc` | `copy` | **Unverified at home scope** (rules dirs documented repo-root→cwd) | `installer-convention` |
| Templates | `~/.grok/templates/` | MD/text | `template` | `copy` | **No** — inert support storage, referenced by skill body relative paths | `installer-convention` |
| Tool shims | `~/.grok/tools/` | text | `tool-shim` | `copy` | **No** — inert support storage, referenced by skill body relative paths | `installer-convention` |
| Hooks (optional tier) | `~/.grok/hooks/ai-agents-skills-autoloop.json` | JSON (`{"hooks":{"Stop":[...]}}`) | `native-hook-file` | discrete managed file (own-outright) | Yes (10-hooks.md:62-69, always trusted) | `official-docs` |
| MCP servers | `~/.grok/config.toml` `[mcp_servers.<n>]` | TOML | (deferred) | — | Yes | — |
| Compat settings | `~/.grok/settings.json` | — | **NOT USED** | — | **No** — grok reads it only for plugin `extraKnownMarketplaces`; never for hooks | — |
| Plugins | `~/.grok/plugins/` (.claude-plugin/plugin.json) | dir bundle | `plugin` | (deferred / optional scaffold) | Yes | — |
| CLI binary | `~/.grok/bin/{grok,agent}` | — | precheck only | `grok inspect --json` smoke | — | — |

Runtime root for grok (non-codex) is the shared neutral root: `~/.local/share/ai-agents-skills/runtime` (POSIX) / `%USERPROFILE%\AppData\Local\ai-agents-skills\runtime` (Windows), via `runtime.default_runtime_root`. Existing `run_skill.{sh,bat,ps1}` shims serve grok unchanged; canonical SKILL.md bodies embed both POSIX and Windows invocation blocks.

### 3. Exact target-registration change-set

**REQUIRED — registration (makes grok discoverable/plannable/installable):**

1. **`installer/ai_agents_skills/agents.py:12`** — add `"grok"` to `DEFAULT_AGENT_NAMES` (`KNOWN_AGENT_NAMES` derives at :13). Order controls detection listing.
2. **`installer/ai_agents_skills/agents.py:15`** — add `"grok"` to `ADAPTER_AGENT_NAMES` (decision A). Do NOT add to `PORTABLE_MANIFEST_AGENT_NAMES`.
3. **`installer/ai_agents_skills/agents.py:47-157`** — insert a branch before `raise ValueError`, modeled on claude (:65-79) but with corrected artifact dirs:
   ```python
   if agent == "grok":
       return AgentTarget(
           name="grok",
           home=root / ".grok",
           skills_dir=root / ".grok" / "skills",
           instructions_file=root / ".grok" / "AGENTS.md",
           optional_skills_dirs=(root / ".claude" / "skills", root / ".agents" / "skills"),
           artifact_dirs={
               "agent-persona": root / ".grok" / "agents",
               "template": root / ".grok" / "templates",       # inert support storage
               "instruction-doc": root / ".grok" / "rules",     # CORRECTED: rules/, NOT instructions/
               "entrypoint-alias": root / ".grok" / "commands",
               "command": root / ".grok" / "commands",
               "tool-shim": root / ".grok" / "tools",           # inert support storage
               "native-hook-file": root / ".grok" / "hooks",    # optional autoloop tier (§5)
           },
           skill_file_layout="directory",
           instruction_blocks_enabled=True,
       )
   ```
   `home=root/".grok"` resolves on all OSes via `Path.home()` — no OS branching. **`instruction-doc` maps to `.grok/rules/`** (grok's documented rules-directory name; antigravity already maps `instruction-doc → plugin_home/rules`), NOT the non-existent `.grok/instructions/`. Home-scope `rules/` loading is flagged unverified in docs/tests (see Risks). **`native-hook-file` → `.grok/hooks/`** exists solely so the optional autoloop hood file (§5, #10) lands in grok's real hook directory; it holds nothing in the baseline install.

   **GROK_HOME (decision documented, not deferred silently):** baseline hardcodes `root/".grok"`, consistent with claude/codex/deepseek. If a contained `GROK_HOME` install is later required, add a `grok_home(root)` helper mirroring `contained_xdg_config_home`/`opencode_home` (agents.py:160-177). Until then, `targets/grok/README.md` MUST state that `GROK_HOME`-relocated installs are unsupported and the user must unset `GROK_HOME` before installing (05-configuration.md:725) — an install into `~/.grok` while grok reads an overridden dir is invisible on every OS.
4. **`installer/ai_agents_skills/capabilities.py:7`** — add the grok loader policy (decision B): `{"symlink_skill_file": False, "default_mode": "copy", "reason": "Grok native ~/.grok/skills SKILL.md files; copy keeps the install self-contained and toggle-independent; symlink loading is unverified and Windows-privilege-gated."}`.

**REQUIRED — honest surface matrix:**

5. **`installer/ai_agents_skills/target_surfaces.py:39-295`** — append `TargetSurface` rows for grok using the honest `claim_basis` column above:
   - `skill-file` = copy, `claim_basis="official-docs"`, cite `08-skills.md`.
   - `instruction-block` (AGENTS.md) = native, `claim_basis="official-docs"`.
   - `entrypoint-alias`/`command` = native-command (like claude :112-120), `claim_basis="official-docs"`.
   - `agent-persona` = copy, `claim_basis="official-docs"`, note "name/description overlay; grok tool-restriction frontmatter not honored for Claude tool names."
   - `instruction-doc` (rules/) = copy, **`claim_basis="installer-convention"`**, note "home-scope rules/ loading unverified."
   - `template`, `tool-shim` = copy, **`claim_basis="installer-convention"`**, note "inert support storage referenced by skill relative paths; not a grok-loaded surface."
   - `native-hook-file` (optional tier) = discrete managed file, `claim_basis="official-docs"`, cite `10-hooks.md`.
   - `runtime-file` = runtime-copy.
   `validate_target_surfaces` (:298-313) enforces enum validity + uniqueness; `surface_matrix_table` (docs.py:2569-2591) auto-regenerates `docs/surfaces.md`. If `claim_basis` uses a fixed enum, add `installer-convention` to it (check `target_surfaces.py` enum; add value + update its validator/test if present).

**REQUIRED — rendering (avoid the DeepSeek-else mislabel):**

6. **`installer/ai_agents_skills/render.py`** — skill body needs NO grok branch: `render_skill_md` (:78-90) falls through to `return content` (:90) for copy mode (correct). ADD a grok case to `render_persona` (:223-289) and `render_entrypoint` (:292-332), reusing the claude branches (persona :236-244, entrypoint :294-301), so grok gets claude-style Markdown+YAML instead of the `else` "DeepSeek persona reference" (:276-288). **Persona fidelity caveat:** the claude branch emits `tools:`-style frontmatter whose Claude tool names do not resolve to grok tool ids — this installs cleanly (grok parses claude-format agents) but tool/model restrictions are NOT enforced on grok. Document this in `targets/grok/README.md` and the surface note; do not claim tool-restriction parity. Verify a rendered persona actually appears in `grok inspect --json` before asserting persona coverage. No runtime-path neutralizer/skill-note branch (grok is a standard CLI home, not sandboxed).

**RECOMMENDED — precheck + native smoke (parity with copilot/antigravity):**

7. **ADD `installer/ai_agents_skills/grok.py`** — copy `antigravity.py`'s structure:
   - `GROK_CLI_TOOL_SPEC` with per-platform `grok` binary candidates + env override (see §6).
   - `build_grok_precheck(root, platform, cli_result)` reporting `~/.grok` children **`skills/`, `agents/`, `commands/`, `rules/`, `hooks/`, `config.toml`, `AGENTS.md`** — **NOT `settings.json`** (grok has no `~/.grok/settings.json` hook sink; reporting it would be a dead-path artifact).
   - `run_grok_native_smoke(root, ...)` running `grok inspect --json` in an isolated env. **The isolated env MUST explicitly set `GROK_HOME=str(root/".grok")` as an override** (reuse the `isolated_antigravity_env` HOME/USERPROFILE/XDG/LOCALAPPDATA/APPDATA pattern at antigravity.py:162, but ADD a `GROK_HOME` override) — because `isolated_antigravity_env` inherits `dict(os.environ)`, a developer's real `GROK_HOME` would otherwise redirect the smoke to the real home and produce false results on every OS (05-configuration.md:725).
   - `validate_grok_file_layout`. Redaction helpers reused verbatim.
8. **`installer/ai_agents_skills/target_prechecks.py:47-73`** — import `build_grok_precheck` + spec; add a grok dispatch branch (mirror antigravity :50-57) and summary lines (:140). Optional grok note in `target_notes()` (:124-155). If skipped, grok uses `build_base_target_precheck` like deepseek/opencode — acceptable.
9. **`installer/ai_agents_skills/post_install_smoke.py:7,78-98,175`** — import + wire `run_grok_native_smoke` into the aggregate (mirror antigravity). If grok.py is skipped, generic verify+smoke_artifact coverage suffices.

**OPTIONAL — autoloop hook tier (CORRECTED: native hook file, not settings.json):**

10. **`installer/ai_agents_skills/planner.py:316-337`** — do **NOT** simply widen the claude `settings.json` loop to grok (that writes a dead sink). Instead, in `autoloop_stop_hook_actions`, add a **separate grok branch** that emits a discrete, fully-managed native hook file:
    - Path: `agent.artifact_dirs["native-hook-file"] / "ai-agents-skills-autoloop.json"` = `~/.grok/hooks/ai-agents-skills-autoloop.json`.
    - Content: the full grok hook object `{"hooks": {"Stop": [ {"hooks": [ {"type": "command", "command": f'{interpreter} "{runtime_py}" hook-check'} ]} ]}}` (interpreter `python` on windows else `python3`, matching planner.py:313).
    - Emit as a standard managed **file** action (`kind:"file"`, `classification:"managed"`, own-outright), so uninstall uses `delete-created` / `restore-backup` — clean because the whole file is ours. (json_merge could technically be reused since the nested shape matches Claude's schema, but a discrete owned file is simpler and gives exclusive-ownership rollback; prefer the file action.)
    - Guard: only when `autonomous-research-loop-runtime` is installed, same condition as the claude branch.
    Keep the claude `settings.json` branch (:308-337) untouched.

11. **DEFERRED — `installer/ai_agents_skills/toml_merge.py`** (new): TOML analog of `json_merge.py` (idempotent managed-key tagged upsert/remove) for `[mcp_servers]`/`[[marketplace.sources]]` in `config.toml`. Build only when a skill ships an MCP server. Baseline leaves `config.toml` untouched.
12. **OPTIONAL — `grok_fixup.py` + cli subcommand** (mirror antigravity_fixup.py + cli.py:340-343,740-745): only if a grok `config.toml` or hook-file field needs post-hoc adaptation. **Note:** any such fixup must target `~/.grok/hooks/*.json` or `config.toml` — NOT `settings.json`. Skip unless a concrete need appears.

**PARITY — OpenClaw path-scrubbing / evidence:**

13. **`installer/ai_agents_skills/openclaw_manifest.py:15,36`** — add `"grok"` to `TARGET_AGENTS` and `'grok': '.grok'` to the home map. This breaks the hardcoded tuple assertion at `tests/test_openclaw_manifest.py:82,85` → update that test.
14. **`installer/ai_agents_skills/openclaw_runtime_target_paths.py:21`** — add `.grok` to `AGENT_HOME_DIRS`.
15. **`installer/ai_agents_skills/openclaw_evidence.py:12`** — optionally add `grok` to `AGENTS`.

**Delegation — scoped OUT (explicit, no edits):**

16. grok is registered as an **install target only**, NOT a cross-agent delegation provider. The prior draft's claim to mirror commit `0c886e9` (delegation) is **withdrawn**. Concretely, we make **NO** edits to:
    - `installer/ai_agents_skills/delegation.py` (`PROVIDER_CLI_SPECS`, token-env, home-dir maps :7-9,68-93),
    - `installer/ai_agents_skills/delegation_dispatch.py` (`EXTERNAL_PROVIDERS = {"claude","deepseek","copilot","antigravity"}` :18, provider branch :312),
    - `manifest/delegation.yaml` (`active_providers`/`providers`/`recipient_profile`),
    - `tests/test_cross_agent_delegation.py:163-176` (which asserts the exact set `{claude,deepseek,copilot,antigravity}`).
    Because these sites use exclusive hardcoded sets and grok is deliberately absent, they continue to pass unchanged. If grok delegation is desired later, it is a **separate, scoped follow-up** that must edit all four sites together (Open Questions).

**CLI (cosmetic):**

17. **`installer/ai_agents_skills/cli.py:197-199`** — update `--target-agents` default help text. `--agent/--agents` have no `choices=` (:115-116), so grok becomes selectable the moment `target_for` knows it; no functional change.

**DOCS (hand-maintained prose + per-target README):**

18. **`installer/ai_agents_skills/docs.py`** — hand-edit hardcoded prose tables: `agent_locations_text` (:3273-3376, add grok home/skill-target/instruction-file/artifact-dir/rendered rows — reflect `rules/` for instruction-doc, `hooks/` for the optional hook tier, and mark templates/tools as inert), `architecture_text` (:2628+ target list :2632-2633), `installation_text` (:2789+ per-target precheck prose). `surface_matrix_table` auto-updates from `TargetSurface` rows.
19. **ADD `targets/grok/README.md`** — mirror `targets/claude/README.md` + `targets/deepseek/README.md`, and MUST include:
    - `~/.grok` surfaces (skills/agents/commands/rules/hooks/AGENTS.md/config.toml), install command (`curl -fsSL https://x.ai/cli/install.sh | bash` / PowerShell `irm https://x.ai/cli/install.ps1 | iex`),
    - **`GROK_HOME` unsupported note:** relocated installs are invisible; unset `GROK_HOME` before installing,
    - **no-XDG note**, copy-mode rationale, the "full install target" phrasing the README test asserts,
    - **`[compat.claude]` double-load caveat with concrete consequence (see §6):** when both `claude` and `grok` are installed, grok loads managed skills from BOTH `~/.grok/skills/` and `~/.claude/skills/`, and managed instruction text from BOTH `~/.grok/AGENTS.md` and `~/.claude/CLAUDE.md`, duplicating every managed slash command and instruction block. To get a single self-contained view, set `[compat.claude] skills=false, agents=false, rules=false, hooks=false` in `~/.grok/config.toml`.
    - **hooks note:** the optional autoloop hook installs as native `~/.grok/hooks/ai-agents-skills-autoloop.json`; grok does NOT read `~/.grok/settings.json`.
    - **persona note:** subagents install as name/description overlays; Claude tool-restriction frontmatter is not enforced on grok.

**Manifest edits — NOT required under ADAPTER (decision A).** Only if switching to PORTABLE: add `"grok"` to every `supported_agents` in `manifest/skills.yaml` (~49) and `manifest/artifacts.yaml`, and extend `test_portable_manifest_entries_explicitly_support_adapter_targets`.

### 4. Canonical-skill → grok-skill render design

- Grok frontmatter is a documented superset of canonical (`name`, `description`, `metadata.short-description`, `user-invocable`, `disable-model-invocation`, 08-skills.md). Canonical SKILL.md bodies + frontmatter install **verbatim** under copy mode — no field transformation.
- Copy mode → `planner.skill_content_for_mode` (:676-685) → `render_skill_md` → canonical body with `add_managed_header` (render.py:390-399) injecting `<!-- Managed by ai-agents-skills. Generated target: grok. -->` after the closing `---`. Grok ignores the HTML comment. `sanitize.normalize_frontmatter_name` forces `name:` to the canonical skill name; the `has_sensitive_material` leak gate applies unchanged.
- Support files (`scripts/`, `references/`, sibling `.md`) copy into `agent.support_dir_for(skill)` = `~/.grok/skills/<skill>/` via `support_file_actions`, honoring relative-path references in the body. `support_file_platform_block_reason` (planner.py:642) drops `.sh` on Windows and `.bat/.ps1` on POSIX automatically.
- Runtime-backed skills (graph-verifier, zotero, …) already emit portable `$AAS_RUNTIME_ROOT`/`%AAS_RUNTIME_ROOT%` tokens and dual invocation blocks — grok needs no neutralizer.
- Slash commands are free: every installed `user-invocable` skill auto-surfaces as `/<skill>`; `user-invocable:false` (2 skills) is honored to hide command-backed skills.
- Personas/commands: `render_persona`/`render_entrypoint` grok branches (§3.6) emit `~/.grok/agents/<name>.md` and `~/.grok/commands/<name>.md`. Personas are name/description overlays (tool restrictions not enforced on grok).
- **Instruction-docs** (`instruction-doc:*` artifacts, e.g. `research-quick-actions`, `cross-provider-delegation`) route to `~/.grok/rules/*.md`. Home-scope loading is unverified; if verification fails, the fallback is to fold their content into managed blocks inside `~/.grok/AGENTS.md` (Open Questions).
- **Templates/tool-shims** copy to `~/.grok/templates/` and `~/.grok/tools/` purely as inert storage referenced by skill bodies via relative paths — grok does not load them as surfaces.

### 5. Settings / config / MCP / hooks / commands / agents install design (idempotent)

- **AGENTS.md instruction block (baseline, native):** `render_instruction_block` (render.py:427-443) + `render_management_notice` (:446-484) upsert `<!-- ai-agents-skills:<skill>:start/end -->` and the management-notice block into `~/.grok/AGENTS.md` via `replace_or_append_block` (:487-496), gated by `instruction_blocks_enabled=True`. Idempotent by marker; uninstall removes only the managed block.
- **Autoloop hook (optional tier, native FILE):** written as a discrete fully-managed `~/.grok/hooks/ai-agents-skills-autoloop.json` containing grok's `{"hooks":{"Stop":[{"hooks":[{"type":"command","command":"python3 <runtime> hook-check"}]}]}}` shape. This is grok's real hook sink (10-hooks.md:62-69, always trusted at home scope). Because the file is owned outright, it flows through `classify_file_action`/`apply_file_action` and uninstall uses `delete-created`/`restore-backup`. **`~/.grok/settings.json` is never written** — it is not a grok hook source.
- **config.toml (deferred):** no TOML merge primitive exists; `json_merge` is JSON-only. Three documented patterns: (a) new `toml_merge.py` with a managed-key tag convention (recommended when needed); (b) antigravity-style managed no-op scaffold; (c) Codex-style driver-owned (chosen for baseline — leave `config.toml` untouched). No current skill needs `[mcp_servers]`.
- **MCP/plugins:** deferred. `[[marketplace.sources]]`/`[mcp_servers.*]` go through the future `toml_merge.py` under a managed block; plugin bundles under `~/.grok/plugins/ai-agents-skills/` with `.claude-plugin/plugin.json` (mirror `antigravity_native_scaffold_actions`, planner.py:205-272).
- **Idempotency/state:** all writes flow through `classify_file_action`/`apply_file_action`, keyed by `artifact_signature` (state.py:51), recorded in `<root>/.ai-agents-skills/state.json` with backup + reverse `uninstall_origin` (apply.py:419) — grok inherits verify/rollback/uninstall for free.

### 6. Cross-platform design (Windows / macOS / Linux / WSL)

- **Home resolution:** `home = root/".grok"` via `Path.home()` → `/home/<u>/.grok` (Linux), `/Users/<u>/.grok` (macOS), `C:\Users\<u>\.grok` (Windows). Zero OS branching. `agent_home_status` (agents.py:195-219) gates on `~/.grok` pre-existing as a real (non-symlink) dir inside root; `resolved_path_within` normcases for Windows case-insensitivity. WSL is never auto-detected — installs under WSL must pass `--platform wsl`.
- **GROK_HOME:** documented override (05-configuration.md:725). Baseline hardcodes `~/.grok` (parity with claude/codex/deepseek). README states relocated installs are unsupported; unset `GROK_HOME` before installing. **Native smoke pins `GROK_HOME=root/".grok"` as an explicit override** so the smoke inspects the fake root regardless of the developer's real `GROK_HOME` on any OS.
- **`[compat.claude]` double-load (concrete):** default-on `[compat.claude]` scans `~/.claude/skills/`, `~/.claude/` (CLAUDE.md), `~/.claude/rules/`, `~/.claude/settings.json` on all OSes (05-configuration.md:348-354, 08-skills.md:23-24, 12-project-rules.md:26). When both `claude` and `grok` targets are installed, every managed surface is double-loaded: duplicate slash commands (from `~/.grok/skills` + `~/.claude/skills`), duplicated instruction text (from `~/.grok/AGENTS.md` + `~/.claude/CLAUDE.md`), and the autoloop hook seen from both `~/.grok/hooks/` and `~/.claude/settings.json`. Dedup-by-name applies only within a tier, NOT across `~/.grok` vs `~/.claude`. Mitigation (documented in README): set `[compat.claude] skills/agents/rules/hooks = false`.
- **Runtime root (grok is non-codex):** `runtime.default_runtime_root` → `~/.local/share/ai-agents-skills/runtime` (linux/macos/wsl) / `root/AppData/Local/ai-agents-skills/runtime` (windows). Shared, neutral, already populated with runner shims. No grok-specific runner.
- **Runner shims + exec bit:** `manifest/runtime.yaml` runners platform-gated (`run_skill.sh` 0755/LF linux/macos/wsl; `run_skill.ps1`/`.bat`/`run_python.bat` 0644/CRLF windows). `apply_mode` chmods on POSIX, NO-OP on `os.name=="nt"` (runtime.py:502-508); Windows relies on `.bat`/`.ps1` extension. Grok inherits all of this.
- **Support-file suffix gating:** `.sh` blocked on Windows, `.bat/.cmd/.ps1` blocked on POSIX (planner.py:642-647).
- **Newlines:** CRLF for Windows shims, LF for POSIX, per-entry (runtime.py:435-476), fed into content-addressed apply hash; atomic writes use `newline=""` (state.py:309). The native hook JSON is LF everywhere (a JSON payload, not a shim).
- **Install mode = copy** sidesteps Windows symlink privilege; `apply.py:164-176` still provides symlink→text OSError fallback for any explicit `--symlink`.
- **grok.py CLI spec** — four candidate lists with env override first:
  - linux/wsl: `${AAS_GROK}`, `grok`, `~/.grok/bin/grok`, `~/.local/bin/grok`
  - macos: adds `/opt/homebrew/bin/grok`, `/usr/local/bin/grok`
  - windows: `%AAS_GROK%`, `%USERPROFILE%\.grok\bin\grok.exe`, `grok.exe`, `grok`
  Native smoke isolates HOME/USERPROFILE/LOCALAPPDATA/APPDATA/XDG_* **and GROK_HOME** to the fake root so it runs identically on all OSes.
- **run_skill.sh dep search:** hardcodes `~/.codex/.local`, `~/.claude/.local`, `~/.deepseek/.local` (run_skill.sh:84-107). Grok reuses the shared workspace `.local`; only add `~/.grok/.local` if grok ever vendors its own Python deps (optional).
- **config.toml/hooks/*.json paths** are `~/.grok/…` on all OSes (Windows npx/npm resolve as `.cmd` via PATHEXT).

### 7. Phased task breakdown

- **Phase 0 — Confirm grok layout** (from live docs at `/home/ubuntu/.grok/docs/user-guide/` + `~/.grok/README.md`, done): home `~/.grok`, instructions `AGENTS.md`, directory SKILL.md layout, copy mode, ADAPTER membership, hooks at `~/.grok/hooks/*.json`, rules at `~/.grok/rules/`, NO `~/.grok/settings.json` hook sink, delegation scoped OUT.
- **Phase 1 — Core registration:** agents.py (#1-3, corrected artifact_dirs), capabilities.py (#4), target_surfaces.py (#5, honest claim_basis), render.py persona/entrypoint (#6). After this, `--agent grok` plans/applies/verifies/uninstalls end-to-end.
- **Phase 2 — Precheck + smoke:** grok.py (#7, GROK_HOME-pinned smoke, hooks/-not-settings.json children), target_prechecks.py (#8), post_install_smoke.py (#9).
- **Phase 3 — Optional autoloop hook tier:** native `~/.grok/hooks/ai-agents-skills-autoloop.json` (planner.py branch, #10).
- **Phase 4 — Parity + docs:** openclaw_manifest/runtime_target_paths/evidence (#13-15), cli help (#17), docs.py prose (#18), targets/grok/README.md (#19), then `make docs` + `make docs-check` + `make sanitize-check`.
- **Phase 5 — Tests + CI:** GrokTargetTests, skipped_agents count fix (:596), runtime-file parity (:310), test_openclaw_manifest assertion, targets-README test coverage, lifecycle matrix grok-only-auto, `.github/workflows/tests.yml` grok-only steps.
- **Phase 6 (deferred):** toml_merge.py + config.toml MCP/marketplace/plugin surface, grok_fixup.py, and (separately) grok cross-agent delegation across all four delegation sites — only when concretely required.

### 8. Test plan (incl. fake-root)

- **Unit / fake-root (`tests/test_installer.py`):** add `GrokTargetTests` mirroring `OpenCodeTargetTests` (~3847+): `all_agent_names()`/`known_agent_names()` contain `grok`; `create_agent_homes(root,"grok")` under `TemporaryDirectory`; `build_plan→apply_plan→verify=="ok"` with SKILL.md at `.grok/skills/<skill>/SKILL.md`; instruction-doc lands at `.grok/rules/`; uninstall returns tree to baseline. Also:
  - **`:596` — bump `skipped_agents` count `7 → 8`** (adding grok to `DEFAULT_AGENT_NAMES` makes an empty-home fake root skip 8 agents; `build_plan` derives `skipped_agents` from `agent_home_statuses` over `DEFAULT_AGENT_NAMES`, planner.py:45-54). Guaranteed breakage otherwise.
  - `:1385` `test_all_default_agent_fake_root_detects_available_homes` — add grok + expected mode `copy`.
  - precheck `target_names` list (:3274) + a `by_target['grok']` status/`default_install_mode=="copy"` assertion (:3313-3316).
  - surface test `target_surface_for('grok','skill-file')` (:512-522); add a grok `instruction-doc` surface assertion checking `claim_basis=="installer-convention"` and the rules/ path; add a `native-hook-file` surface assertion.
  - **targets-README coverage:** extend `test_adapter_target_readmes_capture_install_boundaries` (:4226-4240) to also read `targets/grok/README.md` and assert the "full install target" phrasing + the GROK_HOME/compat.claude caveats. (The prior draft wrongly claimed this test already asserts grok phrasing — it reads only copilot/openclaw/opencode/antigravity; grok must be added.)
- **Runtime-file parity (`tests/test_runtime_integration.py:310`):** add `"grok"` to the tuple `("codex","claude","deepseek","copilot","opencode","antigravity")` so grok's runtime-backed skill install is exercised across all four platforms like the other adapters.
- **Lifecycle matrix (`lifecycle_matrix.py`):** grok is auto-covered once in `DEFAULT_AGENT_NAMES` across all 4 platform shapes (`fake_root_for_shape` :518-525). Add a `grok-only-auto` `LifecycleScenario(agent_subset=("grok",))` to `STRESS_EXTRA_SCENARIOS` (:152-156) — keeps `scenario_count>=18` (test_installer.py:3759). Invariant chain: dry-run byte-identical → applied==dry-run → verify==expected → smoke ∈ allow-list (`ok`, guaranteed by copy mode) → uninstall dry-run==real → post-uninstall `no-managed-artifacts` → final tree == baseline. The optional autoloop hook file must survive the same rollback assertions when `autonomous-research-loop-runtime` is in the subset.
- **Native smoke:** on a host with `grok` installed, `run_grok_native_smoke` runs `grok inspect --json` in a GROK_HOME-pinned isolated fake-root env and asserts installed skills appear with source `user`; verify at least one rendered persona appears (persona-fidelity check).
- **`tests/test_openclaw_manifest.py:82,85`:** update the `TARGET_AGENTS` tuple assertion to include grok.
- **Delegation tests (`tests/test_cross_agent_delegation.py:163-176`):** deliberately **unchanged** — they assert the exclusive set `{claude,deepseek,copilot,antigravity}` and grok is intentionally absent. Include a comment in the change-set noting this is an intended no-op, so a future reviewer does not mistake it for an omission.
- **`make` targets:** `make verify`, `make smoke`, `make lifecycle-test --matrix stress --platform-shape all`, `make runtime-smoke`, `make docs-check`, `make sanitize-check`, `make release-check`.
- **CI (`.github/workflows/tests.yml`):** default+stress matrices already exercise grok on Ubuntu; add per-OS `grok-only-auto` lifecycle steps on macOS and windows-latest (mirror antigravity lines ~207/266; windows leg via `make.bat`).

### 9. Uninstall / rollback

No new generic work — grok inherits the lifecycle. Every grok artifact is recorded in `state.json` with `installed_signature`, optional backup, and a reverse `uninstall_origin` (apply.py:419: restore-backup / restore-removed / delete-created / unmanage-only / remove-managed-block). `lifecycle.uninstall` (lifecycle.py:26) and `lifecycle.rollback` (:64) replay these, touching only managed artifacts, restoring backups only when unchanged, skipping (`skip-conflict`) user-edited files, refusing paths outside root. The AGENTS.md instruction block is removed by `remove-managed-block`; the optional autoloop hook file `~/.grok/hooks/ai-agents-skills-autoloop.json` (owned outright) by `delete-created`/`restore-backup`. `~/.grok/config.toml` and `~/.grok/settings.json` are never written, so nothing to roll back there. `make uninstall`/`make rollback` verified by the lifecycle matrix's post-uninstall `no-managed-artifacts` + baseline-tree assertions.

### 10. Risks

(see structured risks list)

### 11. Open questions

(see structured open_questions list)

---

# APPENDIX A — Amendments (post-workflow, per user directives)
These override/extend the plan above where they conflict.
## A1. Cross-agent delegation: grok is IN (reverses §3 #16)
User directive: add grok to cross-agent-delegation. Mirror commit 0c886e9 (antigravity provider):
- `installer/ai_agents_skills/grok.py`: export `GROK_CLI_TOOL_SPEC` (shared with the target precheck).
- `installer/ai_agents_skills/delegation.py`: import `GROK_CLI_TOOL_SPEC`; add to `PROVIDER_CLI_SPECS['grok']`; add `PROVIDER_CONFIG_PATHS['grok']=('.grok',)`; add token-env if grok needs one (it uses OIDC session, not an env key -> likely omit / empty).
- `installer/ai_agents_skills/delegation_dispatch.py`: add 'grok' to `EXTERNAL_PROVIDERS`; in `default_dispatch_command`, return `f'{command} --single'` (grok headless flag; `-p`/`--single`).
- `manifest/delegation.yaml`: add 'grok' to `policy.active_providers`; add `recipients.grok` {status: active, recipient_profile: 'grok-like-code-reviewer', default_role_family: 'Grok CLI code and research workflow review'}.
- `canonical/skills/cross-agent-delegation/references/external-cli-agents.md` + `recipient-profiles.md`: add grok entry + profile.
- `installer/ai_agents_skills/docs.py`: provider docs generation includes grok.
- Tests: `tests/test_cross_agent_delegation.py:163-176` provider set now {claude,deepseek,copilot,antigravity,grok}; `tests/test_installer.py` as needed.
## A2. grok vs grok-remote command selection (per-system)
User: on THIS host `grok-remote` is preferred; elsewhere `grok`. Mechanism = candidate ordering in `GROK_CLI_TOOL_SPEC` (first-found-wins via discovery.discover_tool):
- DELEGATION (actually running grok to do work -> needs region-correct egress): prefer grok-remote.
  candidates linux/wsl: ['${AAS_GROK}', 'grok-remote', '~/grok-proxy/grok-remote', 'grok', '~/.local/bin/grok', '~/.grok/bin/grok']
  macos: + '/opt/homebrew/bin/grok', '/usr/local/bin/grok'
  windows: ['%AAS_GROK%', 'grok-remote.cmd', 'grok-remote', '%USERPROFILE%\\.grok\\bin\\grok.exe', 'grok.exe', 'grok']
  Rationale: grok-remote only EXISTS on this host, so it wins here and is absent (falls through to grok) elsewhere. `${AAS_GROK}`/`%AAS_GROK%` forces either.
- PRECHECK/SMOKE (local `grok inspect --json`, must NOT trigger the SOCKS tunnel / network): prefer BARE grok. Either (a) a separate diagnostic candidate list that omits grok-remote, or (b) grok-remote fast-paths local subcommands (see A3) so `grok-remote inspect` runs without a tunnel. Recommended: smoke resolves bare `grok` directly to avoid tunnel flakiness; delegation uses grok-remote.
## A3. grok-remote passthrough + local-subcommand fast-path
User: update grok-remote to pass arguments to grok. Current behavior already forwards non-reserved args (`launch_socks` -> `exec grok --leader-socket <s> "$@"`), and delegation always calls with `-p/--single` (never a reserved word), so delegation works today. Hardening:
- Add a `--` sentinel: `grok-remote -- <verbatim args>` bypasses all subcommand parsing and execs grok with the tunnel.
- Add a local fast-path: for `inspect`, `--version`, `-V`, `--help` passthrough (read-only, no region needed), exec BARE grok WITHOUT bringing up the tunnel, so `grok-remote inspect --json` works even when no home PC is awake. (`models` still needs the tunnel to fetch the region catalog.)
