# Target Surface Support Matrix

Support claims are intentionally separate from skill selection. This generated page separates install eligibility from support claims. `supported_agents` in `manifest/skills.yaml` and `manifest/artifacts.yaml` is selection eligibility; the rows below state how each target surface is rendered, whether it is supported, degraded, blocked, manual, or unsupported, and what code or policy backs that claim. Do not infer runtime support from `supported_agents` alone.

The current manifest contains 44 installable skills; the matrix below describes the target/surface support contract that those generated skill and artifact plans use.

OpenClaw source/import evidence and OpenClaw install-target behavior are separate. Source/import evidence never authorizes real `.openclaw` writes; current OpenClaw install-target behavior remains fake-root scoped.

| Target | Surface | State | Mechanism | Scope | Claim Basis | Notes |
|---|---|---|---|---|---|---|
| `codex` | `skill-file` | `supported` | `reference-adapter` | agent-visible regular SKILL.md adapter pointing at canonical source | `policy` | Auto mode uses reference adapters because symlinked Codex skill loading is not assumed. |
| `claude` | `skill-file` | `supported` | `symlink` | native Claude skill file | `policy` | Auto mode links to canonical SKILL.md with apply-time fallback. |
| `deepseek` | `skill-file` | `supported` | `reference-adapter` | agent-visible regular SKILL.md adapter pointing at canonical source | `policy` | Auto mode uses reference adapters because native symlink loading is not verified. |
| `copilot` | `skill-file` | `supported` | `reference-adapter` | personal GitHub Copilot skill adapter | `policy` | Copilot receives personal skill/persona surfaces only; commands/templates are unsupported. |
| `opencode` | `skill-file` | `supported` | `copy` | OpenCode-native regular SKILL.md file with copied support files | `policy` | Auto mode copies canonical skills for cross-platform parity; explicit reference and symlink modes remain available with evidence. |
| `antigravity` | `skill-file` | `supported` | `copy` | Antigravity global flat Markdown skill file with the embedded canonical body | `official-docs` | Auto mode writes ~/.gemini/antigravity-cli/skills/<skill>.md with the full canonical skill body and copies support files, matching the documented global skill layout. |
| `openclaw` | `skill-file` | `manual` | `fake-root-copy` | fake-root install-target layout only | `fake-root-only` | OpenClaw real-system target writes stay blocked until native target evidence exists. |
| `codex` | `entrypoint-alias` | `supported` | `reference-doc` | non-executing quick-action reference | `renderer` | Codex does not receive native slash-command registrations from this artifact class. |
| `claude` | `entrypoint-alias` | `supported` | `native-command` | Claude command file | `renderer` | Claude is the only current target that receives native command files for entrypoint aliases. |
| `deepseek` | `entrypoint-alias` | `supported` | `reference-doc` | non-executing quick-action reference | `renderer` | DeepSeek receives entrypoint references rather than native command registrations. |
| `copilot` | `entrypoint-alias` | `unsupported` | `unsupported` | not installed | `planner` | Copilot optional artifacts are limited to supported personal surfaces. |
| `opencode` | `entrypoint-alias` | `supported` | `native-command` | OpenCode command markdown file | `renderer` | OpenCode receives native command aliases under ~/.config/opencode/commands. |
| `antigravity` | `entrypoint-alias` | `supported` | `native-skill` | Antigravity global flat Markdown skill alias | `renderer` | Entry-point aliases render as global Antigravity Markdown skills under ~/.gemini/antigravity-cli/skills/. |
| `openclaw` | `entrypoint-alias` | `unsupported` | `unsupported` | not installed | `fake-root-only` | OpenClaw commands/aliases remain outside target support. |
| `codex` | `runtime-file` | `supported` | `runtime-copy` | root-scoped managed runtime helpers | `runtime-manifest` | Runtime files are copied under the selected root runtime, not per-agent skill folders. |
| `claude` | `runtime-file` | `supported` | `runtime-copy` | root-scoped managed runtime helpers | `runtime-manifest` | Runtime files are copied under the selected root runtime, not per-agent skill folders. |
| `deepseek` | `runtime-file` | `supported` | `runtime-copy` | root-scoped managed runtime helpers | `runtime-manifest` | Runtime files are copied under the selected root runtime, not per-agent skill folders. |
| `copilot` | `runtime-file` | `supported` | `runtime-copy` | root-scoped managed runtime helpers | `runtime-manifest` | Runtime files are copied under the selected root runtime, not per-agent skill folders. |
| `opencode` | `runtime-file` | `supported` | `runtime-copy` | root-scoped managed runtime helpers | `runtime-manifest` | Runtime files are copied under the neutral shared ai-agents-skills runtime root, not inside OpenCode config. |
| `antigravity` | `runtime-file` | `supported` | `runtime-copy` | root-scoped managed runtime helpers | `runtime-manifest` | Runtime files use the neutral shared ai-agents-skills runtime root, not a Codex-specific runtime path. |
| `openclaw` | `runtime-file` | `blocked` | `unsupported` | not installed | `fake-root-only` | OpenClaw-associated shared runtime writes are blocked until separate native evidence exists. |
| `antigravity` | `agent-persona` | `supported` | `plugin` | Antigravity plugin agent definition | `official-docs` | Personas are installed under ~/.gemini/antigravity-cli/plugins/ai-agents-skills/agents/ with the plugin manifest. |
| `antigravity` | `plugin` | `supported` | `plugin` | Antigravity native plugin package | `official-docs` | The installer creates plugin.json and a managed plugin payload under ~/.gemini/antigravity-cli/plugins/ai-agents-skills/. |
| `antigravity` | `global-context` | `supported` | `instruction-block` | managed block in ~/.gemini/GEMINI.md | `official-docs` | Skill and repo-management instruction blocks use the documented Antigravity global developer context file. |
| `antigravity` | `settings-file` | `supported` | `settings-file` | sparse Antigravity settings JSON | `official-docs` | A no-op settings.json scaffold is managed under ~/.gemini/antigravity-cli/settings.json when Antigravity artifacts are installed. |
| `antigravity` | `mcp-config` | `supported` | `mcp-config` | plugin-scoped Antigravity MCP config | `official-docs` | A no-op mcp_config.json scaffold with an empty mcpServers map is installed inside the managed Antigravity plugin. |
| `antigravity` | `hook-config` | `supported` | `hook-config` | plugin-scoped Antigravity hooks config | `official-docs` | A no-op hooks.json scaffold is installed inside the managed Antigravity plugin. |

Claim levels used here:

- `supported`: installer behavior is implemented for the listed surface.
- `fallback` or `degraded`: the installer can proceed with reduced or apply-time fallback behavior.
- `blocked`: the installer intentionally refuses the surface.
- `manual`: the surface needs explicit fake-root/manual evidence and is not a real-system support claim.
- `unsupported`: the target does not receive that surface today.
