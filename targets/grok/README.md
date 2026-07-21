# Grok Target

Grok (xAI's `grok` CLI) is a full install target for ai-agents-skills.

The installer writes Grok-native user-global surfaces under the fixed home
`~/.grok` (`%USERPROFILE%\.grok` on Windows):

- `~/.grok/skills/<skill>/SKILL.md` (plus copied `scripts/`, `references/`)
- `~/.grok/AGENTS.md` (managed instruction and management-notice blocks; there
  is no `GROK.md`)
- `~/.grok/agents/<name>.md` (subagents)
- `~/.grok/commands/<name>.md` (slash-command entry points)
- `~/.grok/rules/<name>.md` (instruction docs)
- `~/.grok/templates/`, `~/.grok/tools/` (inert support storage)
- `~/.grok/hooks/ai-agents-skills-autoloop.json` (optional autoloop tier)
- `~/.grok/config.toml` (managed `[compat.claude]` block)

Install the Grok CLI first:

```bash
curl -fsSL https://x.ai/cli/install.sh | bash
```

```powershell
irm https://x.ai/cli/install.ps1 | iex
```

## Copy mode

Auto mode copies the full canonical `SKILL.md` body (directory layout,
matching `08-skills.md`) into `~/.grok/skills/<skill>/`. Copy keeps the install
self-contained and independent of the repository checkout and of the
`[compat.claude]` ride-along. Symlink loading is unverified for Grok and is
privilege-gated on Windows, so it is not used by default.

Grok does not use XDG base directories: everything lives under the fixed
`~/.grok` home, so no `XDG_CONFIG_HOME`/`XDG_DATA_HOME` relocation applies to
the native install.

## GROK_HOME is unsupported

`GROK_HOME` relocates the directory Grok reads at runtime (05-configuration.md).
Relocated installs are **unsupported**: an install into `~/.grok` while Grok
reads an overridden directory is invisible on every OS. **Unset `GROK_HOME`
before installing.** The native smoke pins `GROK_HOME` to the selected root so
it inspects the installed tree regardless of the developer's real `GROK_HOME`.

## `[compat.claude]` double-load caveat

Grok's `[compat.claude]` ride-along is default-on and scans `~/.claude/skills/`,
`~/.claude/` (`CLAUDE.md`), `~/.claude/rules/`, and `~/.claude/settings.json`.
When both `claude` and `grok` are installed, Grok loads managed skills from
**both** `~/.grok/skills/` and `~/.claude/skills/`, and managed instruction text
from **both** `~/.grok/AGENTS.md` and `~/.claude/CLAUDE.md`, duplicating every
managed slash command and instruction block.

To present a single self-contained view, the installer merges a managed block
into `~/.grok/config.toml`:

```toml
[compat.claude]
skills = false
agents = false
rules = false
hooks = false
```

The block is idempotent and is removed on uninstall; user-authored TOML in
`config.toml` is preserved.

## Hooks

The optional autoloop `Stop` hook installs as a discrete, fully-owned native
hook file `~/.grok/hooks/ai-agents-skills-autoloop.json` with Grok's documented
shape (`{"hooks": {"Stop": [...]}}`, 10-hooks.md). Grok does **not** read
`~/.grok/settings.json` for hooks, so that file is never written. The hook is
only installed when the `autonomous-research-loop-runtime` is selected.

## Subagents

Subagents install as Claude-style `~/.grok/agents/<name>.md` files. They act as
name/description overlays only: Claude tool-restriction frontmatter is not
resolved to Grok tool ids and is not enforced on Grok.

## Instruction docs and support storage

Instruction docs copy to `~/.grok/rules/`. Home-scope `rules/` loading is
unverified; the managed instruction blocks in `~/.grok/AGENTS.md` are the
verified instruction surface. `~/.grok/templates/` and `~/.grok/tools/` are
inert support storage referenced by skill relative paths; Grok does not load
them as surfaces.

## Native smoke

Native Grok smoke, when the `grok` CLI is available, runs `grok inspect --json`
in a `GROK_HOME`-pinned isolated environment and checks:

- managed skill file shape under `~/.grok/skills/`
- installed skills and at least one rendered persona appear in the inspect output

The smoke resolves a bare `grok` (never the `grok-remote` proxy) so it does not
bring up a network tunnel. Native Windows execution still requires a host-side
smoke pass before claiming real Windows loader/runtime verification.

Project-local `.agents/` directories do not activate the global Grok target by
themselves.

## Delegation

Grok is also a cross-agent delegation provider. Live dispatch is CLI-based
through `grok --prompt-file /dev/stdin`; on hosts with the region-correct
`grok-remote` proxy it is preferred automatically. The parent dispatcher sets
no multi-session environment override and adds no route flags. A bare proxy
command relies on its active managed profile; explicit caller-supplied route or
profile flags are preserved. Concurrent participants must use the same ready
profile and concrete model/release identities. The dispatcher checks
`grok-remote doctor --json`, accepts only an exact
`grok-remote.profile-status.v1` result in `ready` or `degraded` state, and
requires its `model_id` to match the resolved model. Invalid or non-ready
results fail closed, and private topology is not recorded. The dispatcher first
requires `--help` to advertise that exact command, so older proxies fail closed
without receiving `doctor` as ordinary Grok input. Grok authenticates through
an interactive OIDC session rather than an API-key environment variable. See
the repo
[Architecture](../../docs/architecture.md) for the full per-target matrix.

## Autonomous research loop force-continue (driver)

**Provider id is always `grok`.** There is no `grok-remote` provider. The on-disk
CLI may resolve to `grok-remote` (region proxy) or bare `grok` / `grok.exe`.

### What enforces force-continue

| Mechanism | Role on Grok |
|---|---|
| **`drive --provider grok`** | **Only hard force-continue** — external process spawns one headless iteration at a time until `done` |
| `arm --driver` | Registry ownership; interactive hooks stand down while the driver PID is live |
| Stop hook (`~/.grok/hooks/ai-agents-skills-autoloop.json`) | **Passive only** — Grok documents `Stop` as non-blocking; exit codes cannot force the agent to keep working |
| Skill / AGENTS.md text | Soft instructions only |

Interactive Grok chat is for status and forks. Long unattended runs must use the
headless driver, not a single chat session.

### Binary resolution (drive / `agent-cmd --provider grok`)

Preference (first usable wins), aligned with installer `GROK_CLI_TOOL_SPEC`:

1. `AAS_AUTOLOOP_CMD_GROK` (full shell template)
2. `AAS_AUTOLOOP_BIN_GROK` (explicit binary path)
3. `AAS_GROK` (same override as installer/delegation)
4. Platform candidates — prefer **`grok-remote`** then bare **`grok`**
   - Linux/WSL: `grok-remote`, `~/grok-proxy/grok-remote`, `grok`, `~/.local/bin/grok`, `~/.grok/bin/grok`
   - macOS: same plus `/opt/homebrew/bin/grok`, `/usr/local/bin/grok`
   - Windows: `grok-remote.cmd`, `grok-remote`, `%USERPROFILE%\.grok\bin\grok.exe`, `grok.exe`, `grok`

Native smoke and prechecks still resolve **bare `grok` only** (never open the
proxy tunnel). Do not point `AAS_GROK` at `grok-remote` for diagnostic smoke
unless you intentionally accept tunnel side effects.

### Start a driven loop (cross-platform)

From a checkout that has the runtime skill installed (paths may be the
repo `.venv` python or the installed runtime copy):

```bash
# Linux / macOS
python3 path/to/autonomous_research_loop_runtime.py drive \
  --dir /path/to/loop \
  --root /path/to/project \
  --provider grok \
  --max-failures 3 \
  --max-quota-waits 0
```

```powershell
# Windows (PowerShell)
py path\to\autonomous_research_loop_runtime.py drive `
  --dir C:\path\to\loop `
  --root C:\path\to\project `
  --provider grok `
  --max-failures 3 `
  --max-quota-waits 0
```

Convenience wrappers shipped with the runtime skill:
`run_autonomous_research_loop.sh`, `.bat`, and `.ps1` (same arguments after the
subcommand).

Each iteration runs with the project **root as cwd** so Grok sees the correct
workspace even if the driver process was started elsewhere.

### Kill switches

| Action | Linux/macOS | Windows PowerShell |
|---|---|---|
| Stop loop | `touch <loop>/STOP_REQUESTED` | `New-Item -ItemType File <loop>\STOP_REQUESTED` |
| Pause | `touch <loop>/PAUSE` | `New-Item -ItemType File <loop>\PAUSE` |
| Resume pause | `rm <loop>/PAUSE` | `Remove-Item <loop>\PAUSE` |
| Disable hooks env | `AUTOLOOP_DISABLE=1` | `$env:AUTOLOOP_DISABLE=1` |
| Disarm registry | `… disarm --dir <loop>` | same |

### Probe which binary will be used

```bash
python3 path/to/autonomous_research_loop_runtime.py agent-cmd \
  --provider grok --dir /path/to/loop
```

Inspect `providers.grok.binary` and `binary_found` in the JSON output.
