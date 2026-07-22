# OpenGauss local install (manual-native)

AAS never auto-installs OpenGauss in installer smoke or CI.

## Linux / macOS

```bash
git clone https://github.com/math-inc/OpenGauss.git
cd OpenGauss
./scripts/install.sh
```

Optional secrets belong in user-managed paths (e.g. `~/.gauss/.env`). Do not commit tokens.

After install, use the upstream `gauss` CLI and project model (`.gauss/project.yaml`). Register an **existing** Lake project; discovery must not invent project roots mid-research.

## Prerequisites (upstream)

- `uv` / `uvx`
- backend: `claude` (claude-code) and/or `codex`, authenticated
- `rg` for Lean search
- Lean/Lake toolchain for the target project

Run upstream `gauss doctor` when available. AAS `opengauss doctor` only reports PATH presence offline.

## Updating

```bash
cd OpenGauss && git pull && gauss update
```

Prefer pinning a known-good commit for any future auto mode.
