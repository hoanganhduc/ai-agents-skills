# Kimi Target

Kimi Code CLI (`kimi`) is a full install target for ai-agents-skills.

The installer writes Kimi-native user-global surfaces under the fixed home
`~/.kimi-code` (`%USERPROFILE%\.kimi-code` on Windows):

- `~/.kimi-code/skills/<skill>/SKILL.md` (plus copied `scripts/`, `references/`)
- `~/.kimi-code/AGENTS.md` (managed instruction and management-notice blocks)
- `~/.kimi-code/agents/<name>.md` (custom agents / personas)
- `~/.kimi-code/templates/`, `~/.kimi-code/tools/`, `~/.kimi-code/instructions/`
  (inert support storage referenced by skill relative paths)

Install the Kimi Code CLI first:

```bash
curl -fsSL https://code.kimi.com/kimi-code/install.sh | bash
```

```powershell
irm https://code.kimi.com/kimi-code/install.ps1 | iex
```

On Windows, install Git for Windows before first launch; Kimi uses Git Bash as
its shell environment. If Git Bash is non-standard, set `KIMI_SHELL_PATH`.

## Copy mode

Auto mode copies the full canonical `SKILL.md` body (directory layout) into
`~/.kimi-code/skills/<skill>/`. Copy keeps the install self-contained.
Symlink loading is unverified for Kimi and is privilege-gated on Windows, so it
is not used by default.

## KIMI_CODE_HOME is unsupported for install

`KIMI_CODE_HOME` relocates the data root Kimi reads at runtime. Relocated
installs are **unsupported**: an install into `~/.kimi-code` while Kimi reads an
overridden directory is invisible. **Unset `KIMI_CODE_HOME` before installing**
on a real system. Native smoke pins `KIMI_CODE_HOME` to the selected root so it
inspects the installed tree regardless of the developer's ambient env.

## Surfaces not installed

| Surface | Reason |
|---------|--------|
| `commands/` / entrypoint aliases | Kimi invokes skills as `/skill:<name>`; no commands dir loader |
| Discrete `hooks/*.json` | Hooks live as `[[hooks]]` entries in `config.toml` |
| Automatic rewrite of `config.toml` | v1 does **not** merge hooks into config (secret residual risk); see Autoloop |

## Hooks and autoloop

**Hard force-continue for unattended work is ARL `drive --provider kimi`.**

Interactive Stop hooks in `config.toml` are fail-open on script error and are
documented only. Optional manual snippet (edit yourself; installer does not
write secrets-bearing config in v1):

```toml
[[hooks]]
event = "Stop"
command = "python3 /path/to/autonomous_research_loop_runtime.py hook-check"
timeout = 30
```

Prefer the absolute interpreter and runtime path that match your install.
Never paste API keys into hook commands.

## Subagents

Subagents install as `~/.kimi-code/agents/<name>.md` with YAML frontmatter
`name` / `description` and a Markdown body. Unknown frontmatter fields are
ignored by Kimi.

## Native smoke

When the `kimi` CLI is available, post-install smoke may run `kimi doctor` in a
`KIMI_CODE_HOME`-pinned isolated environment. Prechecks and smoke **never open**
`config.toml` for secrets. Missing CLI → smoke skipped (layout still checked).

Filesystem layout checks are not proof that Kimi's loader indexed the skills.

## Delegation

Kimi is an active cross-agent delegation provider. Live dispatch uses
**runtime argv** transport: the parent dispatcher appends `-p <prompt>` after the
prompt is known (Kimi has no `--prompt-file`). Research runs require
`AAS_KIMI_DISPATCH_COMMAND` plus resolved model/thinking metadata.

```bash
export AAS_KIMI_DISPATCH_COMMAND='kimi'
export AAS_KIMI_LATEST_MODEL='<model-alias>'
export AAS_KIMI_HIGHEST_THINKING='high'   # soft metadata unless a CLI flag exists
```

Do not store credentials, raw config, or private paths in cross-agent packets.

## Autonomous research loop (driver)

**Provider id is `kimi`.**

### What enforces force-continue

| Mechanism | Role on Kimi |
|---|---|
| **`drive --provider kimi`** | **Only hard force-continue** |
| Skill / AGENTS.md text | Soft instructions only |
| Manual `[[hooks]]` Stop | Soft / fail-open |

### Binary resolution

Preference:

1. `AAS_AUTOLOOP_CMD_KIMI` (full shell template)
2. `AAS_AUTOLOOP_BIN_KIMI`
3. `AAS_KIMI`
4. Platform bare candidates (`kimi`, `~/.kimi-code/bin/kimi`, Windows `.exe` under `%USERPROFILE%\.kimi-code\bin\`)

When `AAS_KIMI_LATEST_MODEL` is set and `AAS_AUTOLOOP_ARGS_KIMI` is unset, the
driver adds `-m <model>`. Thinking is soft metadata only on current CLIs.

```bash
python3 path/to/autonomous_research_loop_runtime.py drive \
  --dir /path/to/loop \
  --root /path/to/project \
  --provider kimi \
  --max-failures 3
```

## Remote control

Install `remote-bridge`. Arm with **`--provider kimi`** (do not rely on defaults
that may target another provider). Without a verified PreToolUse live gate,
mailbox approvals are **advisory**.
