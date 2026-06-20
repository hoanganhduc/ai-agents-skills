# OpenCode Target

OpenCode is a full install target for ai-agents-skills.

The installer writes OpenCode-native user-global surfaces under:

- `~/.config/opencode/skills/<skill>/SKILL.md`
- `~/.config/opencode/AGENTS.md`
- `~/.config/opencode/agents/<name>.md`
- `~/.config/opencode/commands/<name>.md`
- `~/.config/opencode/templates/`
- `~/.config/opencode/instructions/`

Auto mode copies canonical skill files and support files so OpenCode installs
are self-contained across Linux, macOS, WSL, and Windows-shaped roots. Explicit
`reference` and `symlink` modes remain available, but native loader smoke must
confirm those modes before they are treated as fully verified.

Managed OpenCode artifacts do not configure providers, models, API keys, MCP
credentials, or other auth-bearing settings. Existing `opencode.json` and
`opencode.jsonc` files are user-owned unless a future manifest declares a
specific reversible managed config artifact.

OpenCode project-local `.opencode/` directories are not used as the personal
global target and do not activate default detection.

Native OpenCode smoke, when the CLI is available, uses isolated XDG directories
and checks:

- `opencode debug paths`
- `opencode debug skill --pure`
- `opencode agent list --pure`

Native Windows execution still requires a host-side smoke pass before claiming
real Windows loader/runtime verification.

## Autonomous loop enforcement

OpenCode has no interactive Stop-hook merge for autonomous loops: its config is
`opencode.json`/`opencode.jsonc`, not a JSON `hooks` map. Headless loops are
enforced by the shared `autoloop_driver.sh`. See the repo
[Architecture](../../docs/architecture.md) for the full per-target matrix.
