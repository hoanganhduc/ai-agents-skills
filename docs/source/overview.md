# Overview

AI Agents Skills is an experimental, personal-use repository for sharing
research-oriented agent skills and settings across Codex, Claude, DeepSeek,
and OpenCode.
It is designed for combinatorics and graph theory workflows, but the installer
and documentation are written so other users can inspect, dry-run, and install
only the parts that fit their own machines.

The repository is a generator and installer, not a copied dotfiles folder. It
keeps reusable skill bodies, dependency metadata, profiles, optional artifacts,
and target-specific rendering logic in one source tree. Agent homes such as
`~/.codex`, `~/.claude`, `~/.deepseek`, and `~/.config/opencode` are runtime
targets. Default skill installs use auto mode: Claude links back to the
canonical repo files, Codex and DeepSeek receive reference adapters, and
OpenCode receives copied native skill files plus support files unless native
loader evidence justifies a different policy. Explicit symlink, reference, and
copy modes are available when you need to force one strategy.

Most checked-in documentation is generated from
`installer/ai_agents_skills/docs.py`, with manifest-derived tables inserted
from `manifest/`. Maintainers should edit the generator or manifests and run
`make docs` rather than hand-editing generated `README.md` or mirrored
`docs/*.md` pages.

## Main Ideas

- Canonical skills live under `canonical/skills/`. Runtime helpers live under
  `canonical/runtime/`. Agent homes and `~/.openclaw/workspace/skills/*` are
  **install products** — edit the checkout first, then install or publish.
- OpenClaw dual-route `/aas` for remote-bridge is published from
  `canonical/runtime/skills/remote-bridge/` via `publish_openclaw_adapter.py`
  into `~/.openclaw/workspace/skills/aas-remote-bridge/` (not a managed
  `openclaw-target-*` skill-file install).
- Profiles in `manifest/profiles.yaml` select useful skill bundles.
- Optional artifacts add templates, personas, instruction docs, entrypoint
  aliases, and management notices outside normal skill directories.
- Runtime-backed skills install helper scripts under a shared runtime root; live
  config, caches, local databases, and downloaded documents are outside managed
  canonical source.
- `self-improving-agent` turns reusable failures, corrections, and missing
  capabilities into `.learnings/` entries plus canonical repo integration plans
  that name affected targets, OS/substrates, docs, manifests, runtime helpers,
  and tests before implementation.
- `precheck` detects tools and Python packages from the current substrate.
- `plan` and `install --dry-run` preview writes before anything is changed.
- `--install-mode auto` is the default and resolves per agent. Claude uses
  symlinked skill files. Codex uses reference adapters because current Codex
  discovery ignores file-symlinked user `SKILL.md` files, DeepSeek uses
  reference adapters because native symlinked skill loading has not been
  verified, and OpenCode uses copied native skill files. `symlink`,
  `reference`, and `copy` force one strategy for every agent.
- Real home-directory writes require explicit `--apply --real-system`.
- Verification checks only installed managed artifacts.
- The Docling document/OCR runtime is local-only by default. Stronger scanned
  PDF extraction uses local presets such as `scan-heavy`; OCR.space is only an
  explicit opt-in fallback when local conversion fails or quality degrades.

## Typical Workflow

Clone the repository and run commands from its root before starting:

```bash
git clone https://github.com/hoanganhduc/ai-agents-skills.git
cd ai-agents-skills
```

```bash
make doctor
make precheck ARGS="--profile research-core"
make audit-system ARGS="--profile research-core"
make plan ARGS="--profile research-core"
make install ARGS="--profile research-core --dry-run"
```

Use a fake root for write testing:

```bash
make lifecycle-test ARGS="--matrix default --platform-shape linux"
make verify ARGS="--root <fake-or-real-root>"
```

## Where To Go Next

- [Installation](installation.md): safe install, dry-run, conflict, and
  migration flows.
- [Skills](skills.md): canonical skill catalog.
- [Profiles](profiles.md): workflow bundles such as `research-core`,
  `library`, `math`, `full-research`, and `course-management`.
- [Course Management Skills](course-management.md): Classroom50, Canvas,
  Google Classroom, and local roster DB agent entrypoints.
- [Optional Artifacts](artifacts.md): templates, personas, instruction docs,
  entrypoints, and management notices.
- [Dependencies](dependencies.md): logical tools, Python packages, and
  platform-specific detection behavior.
- [Workflow Overview](workflow-overview.md): how the research stack connects
  agents, skills, runtimes, and external software.
- [Audit And Migration](audit-and-migration.md): how to inspect an existing
  setup before adopting or migrating files.
- [OpenClaw Install Target Plan](openclaw-install-target-plan.md): how to
  stage future OpenClaw writable target support behind evidence gates.
- [Verification](verification.md): what installed managed artifacts are
  checked after install, uninstall, or rollback.
