# AI Agents Skills

<div align="center">
  <a href="https://www.buymeacoffee.com/hoanganhduc" target="_blank" rel="noopener noreferrer">
    <img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy Me A Coffee" height="40" />
  </a>
  <a href="https://ko-fi.com/hoanganhduc" target="_blank" rel="noopener noreferrer">
    <img src="https://storage.ko-fi.com/cdn/kofi3.png?v=3" alt="Ko-fi" height="40" />
  </a>
  <a href="https://bmacc.app/tip/hoanganhduc" target="_blank" rel="noopener noreferrer">
    <img src="https://bmacc.app/images/bmacc-logo.png" alt="Buy Me a Crypto Coffee" height="40" />
  </a>
</div>

![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python)
![Platforms](https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20Windows-blue)
![Agents](https://img.shields.io/badge/agents-Codex%20%7C%20Claude%20%7C%20DeepSeek%20%7C%20Copilot-black)
![Docs](https://img.shields.io/badge/docs-GitHub%20Pages-brightgreen?logo=githubpages)
![Status](https://img.shields.io/badge/status-active-yellow)
![License](https://img.shields.io/badge/license-GPL--3.0--or--later-blue)

Shared, manifest-driven skills and settings for Codex, Claude, DeepSeek, and
explicit adapter targets such as GitHub Copilot.

## System Summary

This is an experimental, personal-use configuration for research workflows,
especially combinatorics and graph theory work. It is not a polished general
product, and it may not behave as desired on other machines, other agent
versions, or research tasks outside the assumptions documented here.

This repo turns a multi-agent research setup into one maintainable skill source.
Codex, Claude, DeepSeek, and explicit adapter targets such as GitHub Copilot
can each load local skills, while this repository keeps the shared research
workflows, profiles, delegation settings, dependency metadata, and installer
logic in one place.

The research stack is organized as:

- agent frontends: Codex, Claude, DeepSeek, and explicit Copilot adapters
- shared skill source: `manifest/`, `canonical/skills/`, and `targets/`
- external capabilities: Python, TeX, optional SageMath, local library tools,
  document parsers, public databases, and retrieval helpers

For example, a literature-review request can route through
`research-briefing`, `deep-research-workflow`, `paper-lookup`, and
`research-verification-gate`; a paper-review request can check `zotero` first,
fall back to `calibre` for books, parse files with `docling`, and then run
`paper-review`.

Nontrivial research workflows are expected to keep evidence control visible:
scope briefs define claim boundaries and goal/backward success checks,
deep-research runs preserve source handoffs and guard outputs, delegated agents
return bounded result packets, and final reports pass review and verification
gates. The workflow tracks concrete issues and evidence gaps instead of hiding
quality behind a single aggregate score.

Reusable failures, corrections, and missing capabilities route through
`self-improving-agent`. That skill logs local `.learnings/` entries and, when a
lesson affects skills or settings, proposes a canonical repo integration plan
that names affected install targets, OS/substrates, docs, manifests, runtime
helpers, tests, and blocked coverage before any files are changed.

See `docs/workflow-overview.md` for the full sanitized system description and
workflow examples.

Multi-agent work is documented separately in `docs/multi-agent-examples.md`.
That page explains how the orchestrator selects templates, spawns bounded role
agents, waits for round outputs, runs verification, and merges the result. It
also summarizes the available templates:

- Lakatos Proof and Refutation: proof stress-testing.
- Polya Multi-Strategy Problem Solving: open problem exploration.
- Knuth Structured Manuscript Review: mathematical draft review.
- Structured Research Team: high-stakes claim and proof review.
- Graph Reconfiguration Specialist: gadgets, reductions, and PSPACE/NP-hardness checks.
- Lean Formalization Team: Lean skeleton and proof-blocker analysis.
- Prose / OpenProse-style workflow: reproducible decomposition and synthesis.

This repo is a generator and installer, not a copied dotfiles folder. It uses
canonical skill names, generates per-agent adapters, supports partial installs,
detects legacy/self-contained installs, and verifies only installed managed
skills. Reusable skill bodies live under `canonical/skills`; default installs
record the agent policy and fallback behavior used to choose symlink,
reference, or copy mode. Copy mode remains available when an agent or
filesystem must have regular files inside the settings directory.

Platform support is Linux and Windows first, with core installer flows also
covered on macOS in CI. macOS users should expect POSIX-style behavior but
lighter platform-specific guidance than Linux and Windows.

## Documentation

- [docs/source/index.md](docs/source/index.md): docs-site landing page and
  task-oriented navigation.
- [docs/installation.md](docs/installation.md): install, dry-run, conflict,
  and migration modes.
- [docs/skills.md](docs/skills.md): skill catalog and descriptions.
- [docs/artifacts.md](docs/artifacts.md): optional templates, instruction
  docs, personas, and
  entrypoint aliases.
- [docs/profiles.md](docs/profiles.md): selectable profiles such as
  `research-core` and
  `full-research`.
- [docs/dependencies.md](docs/dependencies.md): logical tools, current Linux/Windows extra
  software, Python packages, Node packages, and manual integrations.
- [docs/workflow-overview.md](docs/workflow-overview.md): how agents, skills, runtimes, and research
  tools connect during real workflows.
- [docs/multi-agent-examples.md](docs/multi-agent-examples.md): multi-agent process examples, spawn/wait
  lifecycle, and available research templates.
- [docs/submission-venue-selector-plan.md](docs/submission-venue-selector-plan.md): implementation plan for the
  fully automated submission venue selector skill.
- [docs/system-profile.md](docs/system-profile.md): sanitized maintainer-system profile and how local
  tools map to skills.
- [docs/agent-locations.md](docs/agent-locations.md): supported agent config, skill, template, command,
  persona, and tool-shim locations.
- [docs/surfaces.md](docs/surfaces.md): generated target-surface support
  states, render mechanisms, and claim basis.
- [docs/local-library-profiles.md](docs/local-library-profiles.md): local Zotero/Calibre
  discovery, profile selection, and mutation safety rules.
- [docs/audit-and-migration.md](docs/audit-and-migration.md): audit output, staged migration, unmanaged
  local skill handling, and Windows-native verification notes.
- [docs/openclaw-integration-plan.md](docs/openclaw-integration-plan.md): gated OpenClaw integration plan,
  risk fixes, and acceptance criteria.
- [docs/openclaw-install-target-plan.md](docs/openclaw-install-target-plan.md): future OpenClaw-as-install-target
  policy, phases, and tests.
- [docs/verification.md](docs/verification.md): installed-artifact verification model.
- [docs/architecture.md](docs/architecture.md): manifest-to-target rendering,
  install modes, artifact classes, and safety boundaries.
- [docs/windows.md](docs/windows.md) and [docs/linux.md](docs/linux.md):
  platform-specific command and dependency notes.
- [docs/troubleshooting.md](docs/troubleshooting.md): common install, audit,
  launcher, migration, and verification cases.
- [docs/uninstall-rollback.md](docs/uninstall-rollback.md): scoped uninstall
  and rollback behavior.

The GitHub Pages site is built from `docs/source` and deployed by
`.github/workflows/docs.yml`.

Most checked-in docs are generated. Edit `installer/ai_agents_skills/docs.py`
and the manifests for generated pages, then run `make docs`; CI checks that
generated docs are current. Generated docs are `README.md`, each page emitted
by `generated_doc_texts()` under `docs/`, and the mirrored copies under
`docs/source/`. `docs/source/index.md`, `docs/source/overview.md`, and
`docs/source/submission-venue-selector-plan.md` are maintained manually;
`docs/submission-venue-selector-plan.md` is the top-level manual copy of the
same plan.

## Acknowledgements

This repository was implemented and maintained with help from ChatGPT Codex.

## License

This project is licensed under GPL-3.0-or-later. See `LICENSE`.

## Quick Start

Clone the repo and run commands from the repository root:

```bash
git clone https://github.com/hoanganhduc/ai-agents-skills.git
cd ai-agents-skills
```

Requires Python 3.10 or newer. Linux and macOS examples use `make` and the
POSIX bootstrap script. Windows examples use `make.bat`, which requires
`pwsh` or `powershell.exe`. The installer only plans targets for existing
agent homes; absent homes are reported and skipped.

Linux/macOS:

```bash
make doctor
make precheck ARGS="--profile research-core"
make audit-system ARGS="--profile research-core"
make list-skills
make list-artifacts
make plan ARGS="--profile research-core"
make plan ARGS="--no-skills --artifact-profile workflow-templates"
make install ARGS="--profile research-core --dry-run"
make lifecycle-test ARGS="--matrix default --platform-shape all"
make fake-root-lifecycle ARGS="--profile research-core --platform-shape linux"
```

For a macOS-shaped fake-root check, use `--platform-shape macos` in the final
command.

Windows:

```bat
make.bat doctor
make.bat precheck --profile research-core
make.bat audit-system --profile research-core
make.bat list-skills
make.bat list-artifacts
make.bat plan --profile research-core
make.bat plan --no-skills --artifact-profile workflow-templates
make.bat install --profile research-core --dry-run
make.bat lifecycle-test --matrix default --platform-shape windows
make.bat fake-root-lifecycle --profile research-core --platform-shape windows
```

For a shorter first pass, run `doctor`, `precheck`, `plan`, and a dry-run
`install` before any lifecycle matrix. `lifecycle-test` and
`fake-root-lifecycle` are verification tools; they use fake roots and should
not be confused with a real install.

Applied installs, uninstalls, and rollbacks are interactive: before any
`--apply` writes files, the installer explains the install, uninstall, and
rollback process and requires the user to type the displayed confirmation
phrase. Real-system writes also require explicit `--apply --real-system`. Tests
and examples use fake roots. Existing unmanaged files are skipped by default;
use `--adopt`, `--backup-replace`, or `--migrate` only after reviewing `plan`
output.

After reviewing `plan` output, a real install uses both write gates:

```bash
make install ARGS="--profile research-core --apply --real-system"
```

Skills install in `--install-mode auto` by default so the repo remains the
single maintained source without hiding agent-loader differences. `plan --json`
shows the effective mode, agent policy evidence, apply-time symlink fallback,
and reason for each target. Use `--install-mode symlink` to force symlinks for
every agent, `--install-mode reference` to force adapters for every agent, or
`--install-mode copy` only when files must be materialized inside the agent
settings directory.

Optional workflow artifacts are not installed by default. Use
`--artifact-profile workflow-templates`, `--artifact-profile review-personas`,
`--artifact-profile workflow-instructions`, or
`--artifact-profile research-entrypoints` explicitly. Use `--with-deps` when
dependency-bound artifacts should also install their backing skills.

## Command Surfaces

- `make <target> ARGS="..."` is the normal Linux/macOS wrapper.
- `make.bat <command> ...` is the normal native Windows wrapper.
- `./installer/bootstrap.sh <command> ...` and
  `python -m installer.ai_agents_skills <command> ...` are direct entrypoints
  for installer CLI commands when debugging wrapper behavior.
- Installer CLI commands include `doctor`, `precheck`, `audit-system`, `plan`,
  `install`, `verify`, `smoke`, `rollback`, `uninstall`, `runtime-smoke`,
  `lifecycle-test`, `list-skills`, `list-artifacts`, `describe`, and
  `describe-artifact`.
- Makefile-only maintainer targets include `docs`, `docs-site`, `docs-check`,
  `static-check`, `sanitize-check`, `test`, and `release-check`; run them
  through `make` or `make.bat`, not as installer CLI commands.

## Runtime-Backed Skills

Runtime-backed skills install shared helper files from `canonical/runtime`
into a root-scoped runtime directory instead of copying executable helpers
inside every agent's skill directory. The runtime manifest declares platform
filters, newline policy, executable modes, and the exact source-to-target
mapping. Runtime inventory intentionally rejects live config, databases,
caches, downloaded documents, bytecode, archives, symlinks, sensitive material,
and persistent execution markers.

Docling is the main document/OCR runtime-backed skill. Its managed wrapper is
local-only by default: sources must be local files and remote service fields
are rejected from config. Use `scan-heavy` when you want stronger local OCR
for image-backed papers:

```bash
bash ~/.codex/runtime/run_skill.sh skills/docling/run_docling.sh doctor
bash ~/.codex/runtime/run_skill.sh skills/docling/run_docling.sh convert \
  --source "/path/to/paper.pdf" \
  --to md \
  --preset scan-heavy
```

OCR.space fallback is available only through explicit remote upload flags:

```bash
bash ~/.codex/runtime/run_skill.sh skills/docling/run_docling.sh convert \
  --source "/path/to/paper.pdf" \
  --to md \
  --preset scan-heavy \
  --ocr-fallback ocrspace \
  --allow-remote-ocr
```

To test the live OCR.space adapter, run the explicit smoke command. It
generates and uploads a synthetic one-page PDF, not a user document:

```bash
bash ~/.codex/runtime/run_skill.sh skills/docling/run_docling.sh ocrspace-smoke \
  --allow-remote-ocr
```

Docling config can be passed with `--config`, `AAS_DOCLING_CONFIG`,
`DOCLING_CONFIG`, or `$AAS_RUNTIME_WORKSPACE/config/docling.toml`. The runtime
skill directory includes `docling.example.toml`; live config files and OCR.space
keys stay outside the managed manifest.

## Profiles

Profiles are named presets for installing groups of related skills. Use a
profile when you want a workflow bundle instead of selecting every skill by
hand. For example, `research-core` installs the normal research
planning/source/review/delivery path, while `full-research` selects every
canonical research skill.

```bash
make plan ARGS="--profile research-core"
make install ARGS="--profile research-core --dry-run"
```

| Profile | Description | Skills |
|---|---|---|
| `digest` | Tracked-topic and RSS digest workflows. | `research-digest-wrapper`, `rss-news-digest`, `digest-bridge` |
| `document` | Document conversion and structured database lookup. | `docling`, `database-lookup` |
| `ebook` | Ebook discovery and library handoff. | `calibre`, `vnthuquan` |
| `figure` | Structural figure generation and checking. | `tikz-draw` |
| `formal-research` | Optional local-first Lean formalization lane for research claims. | `formal-skeleton-helper`, `lean-formalization-intake`, `lean-strict-verification-gate` |
| `formal-research-remote` | Optional remote formal lane setup for AXLE MCP plus local formal-lane skills; install is inert and does not start remote services. | `formal-skeleton-helper`, `lean-formalization-intake`, `lean-strict-verification-gate`, `axiom-axle-mcp` |
| `full-research` | All research-related skills. | `autonomous-research-loop`, `autonomous-research-loop-runtime`, `deep-research-workflow`, `source-research`, `research-briefing`, `research-report-reviewer`, `research-verification-gate`, `draft-writing`, `zotero`, `calibre`, `getscipapers-requester`, `paper-lookup`, `submission-venue-selector`, `database-lookup`, `docling`, `get-available-resources`, `formal-skeleton-helper`, `lean-formalization-intake`, `lean-strict-verification-gate`, `axiom-axle-mcp`, `model-router`, `workspace-rearranger`, `research-digest-wrapper`, `rss-news-digest`, `digest-bridge`, `tikz-draw`, `sagemath`, `graph-verifier`, `agent-group-discuss`, `prose`, `cross-agent-delegation`, `modal-research-compute`, `paper-review`, `annotated-review`, `vnthuquan`, `self-improving-agent`, `session-logs` |
| `library` | Paper and ebook library workflows. | `zotero`, `calibre`, `getscipapers-requester`, `paper-lookup` |
| `math` | Math and graph verification workflows. | `sagemath`, `graph-verifier`, `formal-skeleton-helper` |
| `multi-agent` | Multi-agent and structured workflow orchestration. | `agent-group-discuss`, `prose`, `model-router`, `cross-agent-delegation`, `autonomous-research-loop`, `autonomous-research-loop-runtime` |
| `research-core` | Default research planning, source gathering, report review, and delivery verification. | `research-briefing`, `autonomous-research-loop`, `autonomous-research-loop-runtime`, `deep-research-workflow`, `source-research`, `research-report-reviewer`, `research-verification-gate` |
| `serious-research` | Source-preserving research workflow with local libraries, document parsing, validation, and multi-agent orchestration. | `research-briefing`, `autonomous-research-loop`, `autonomous-research-loop-runtime`, `deep-research-workflow`, `source-research`, `research-report-reviewer`, `research-verification-gate`, `zotero`, `calibre`, `getscipapers-requester`, `paper-lookup`, `submission-venue-selector`, `docling`, `database-lookup`, `paper-review`, `agent-group-discuss`, `prose`, `model-router`, `cross-agent-delegation`, `get-available-resources`, `formal-skeleton-helper`, `workspace-rearranger` |
| `workflow-tools` | Reusable planning helpers for resources, model routing, formal skeletons, and workspace organization. | `autonomous-research-loop`, `autonomous-research-loop-runtime`, `get-available-resources`, `model-router`, `formal-skeleton-helper`, `workspace-rearranger` |
| `writing-workflow` | Claim-preserving draft writing, rewriting, and revision-audit workflow. | `draft-writing` |

## Artifact Profiles

Artifact profiles install optional support files outside normal skill folders:
templates, instruction docs, reviewer personas, entrypoint aliases, and
repo-management notices. They are opt-in because these files can affect agent
behavior more broadly than a single skill.

```bash
make plan ARGS="--no-skills --artifact-profile workflow-templates"
make plan ARGS="--no-skills --artifact-profile repo-management"
```

Use `--with-deps` when selected artifacts should also bring in the backing
skills they depend on.

| Artifact Profile | Description | Artifacts |
|---|---|---|
| `cross-provider-delegation` | Templates and guidance for true cross-provider delegation runs. | `instruction-doc:cross-provider-delegation`, `template:cross-provider-research-panel`, `template:manager-worker-research-review`, `template:repo-comparison-research`, `template:evidence-synthesis-critique` |
| `repo-management` | Top-level managed notice blocks for agent instruction files. | `management-notice:repo-management` |
| `research-entrypoints` | Optional command or quick-action aliases that point to backing skills. | `entrypoint-alias:deep-research`, `entrypoint-alias:research-team`, `entrypoint-alias:review`, `entrypoint-alias:tikz`, `entrypoint-alias:sage`, `entrypoint-alias:zotero`, `entrypoint-alias:docling`, `entrypoint-alias:calibre`, `entrypoint-alias:vnthuquan`, `entrypoint-alias:research-compute`, `entrypoint-alias:rss`, `entrypoint-alias:digest`, `entrypoint-alias:getscipapers` |
| `review-personas` | Reviewer and research role personas rendered to each agent's supported format. | `agent-persona:literature-scout`, `agent-persona:math-explorer`, `agent-persona:proof-checker`, `agent-persona:paper-reviewer`, `agent-persona:code-reviewer`, `agent-persona:test-reviewer`, `agent-persona:security-reviewer` |
| `serious-research` | Templates and guidance for source-preserving, validated research runs. | `template:research-scope-brief`, `template:research-evidence-matrix`, `template:research-verification-checklist`, `template:research-workflow-runbook`, `template:evidence-synthesis-critique`, `template:deep-research-sources`, `template:deep-research-analysis`, `template:deep-research-report`, `template:cross-provider-research-panel`, `template:manager-worker-research-review`, `instruction-doc:research-quick-actions`, `instruction-doc:cross-provider-delegation` |
| `workflow-artifacts` | All portable templates, workflow docs, personas, and entrypoint aliases. | `template:spec`, `template:tasks-plan`, `template:tasks-todo`, `template:draft-claim-ledger`, `template:draft-revision-map`, `template:research-scope-brief`, `template:research-evidence-matrix`, `template:research-verification-checklist`, `template:research-workflow-runbook`, `template:hierarchical-agent-delegation`, `template:cross-provider-research-panel`, `template:manager-worker-research-review`, `template:repo-comparison-research`, `template:evidence-synthesis-critique`, `template:deep-research-sources`, `template:deep-research-analysis`, `template:deep-research-report`, `instruction-doc:engineering-lifecycle`, `instruction-doc:claim-preserving-writing`, `instruction-doc:research-quick-actions`, `instruction-doc:cross-provider-delegation`, `instruction-doc:python-quality-gates`, `instruction-doc:modal-offload-routing`, `instruction-doc:scrapling-integration`, `instruction-doc:language-style-rules`, `agent-persona:literature-scout`, `agent-persona:math-explorer`, `agent-persona:proof-checker`, `agent-persona:paper-reviewer`, `agent-persona:code-reviewer`, `agent-persona:test-reviewer`, `agent-persona:security-reviewer`, `entrypoint-alias:deep-research`, `entrypoint-alias:research-team`, `entrypoint-alias:review`, `entrypoint-alias:tikz`, `entrypoint-alias:sage`, `entrypoint-alias:zotero`, `entrypoint-alias:docling`, `entrypoint-alias:calibre`, `entrypoint-alias:vnthuquan`, `entrypoint-alias:research-compute`, `entrypoint-alias:rss`, `entrypoint-alias:digest`, `entrypoint-alias:getscipapers`, `management-notice:repo-management` |
| `workflow-instructions` | Agent-readable workflow guidance documents copied outside skill folders. | `instruction-doc:engineering-lifecycle`, `instruction-doc:claim-preserving-writing`, `instruction-doc:research-quick-actions`, `instruction-doc:cross-provider-delegation`, `instruction-doc:python-quality-gates`, `instruction-doc:modal-offload-routing`, `instruction-doc:scrapling-integration`, `instruction-doc:language-style-rules` |
| `workflow-templates` | Reusable research, specification, and task templates. | `template:spec`, `template:tasks-plan`, `template:tasks-todo`, `template:draft-claim-ledger`, `template:draft-revision-map`, `template:research-scope-brief`, `template:research-evidence-matrix`, `template:research-verification-checklist`, `template:research-workflow-runbook`, `template:hierarchical-agent-delegation`, `template:cross-provider-research-panel`, `template:manager-worker-research-review`, `template:repo-comparison-research`, `template:evidence-synthesis-critique`, `template:deep-research-sources`, `template:deep-research-analysis`, `template:deep-research-report` |
| `writing-workflow` | Claim-preserving writing workflow instructions and templates. | `instruction-doc:claim-preserving-writing`, `template:draft-claim-ledger`, `template:draft-revision-map` |

## Skills

Skills are the installable agent capabilities. Installing a skill creates the
per-agent `SKILL.md` target, support files when needed, and managed instruction
blocks only for installed, adopted, or migrated skills. By default those skill
targets follow auto mode: Claude links to `canonical/skills`, while Codex and
DeepSeek receive reference adapters unless native loader evidence justifies a
different policy. Explicit `symlink`, `reference`, and `copy` modes force the
same strategy for every agent. Use `--skill` or `--skills` for narrow installs.

```bash
make plan ARGS="--skill zotero"
make install ARGS="--skills zotero,docling --dry-run"
```

| Skill | Description | Profiles |
|---|---|---|
| `agent-group-discuss` | Multi-agent discussion, review, and research orchestration. | `multi-agent`, `serious-research`, `full-research` |
| `annotated-review` | Annotated paper review workflow when both annotation and review are requested. | `full-research` |
| `autonomous-research-loop` | Run bounded autonomous research iterations with evidence gates, recovery ledgers, and optional cross-agent handoffs. | `research-core`, `serious-research`, `workflow-tools`, `multi-agent`, `full-research` |
| `autonomous-research-loop-runtime` | Offline runtime helper for autonomous research loop ledger initialization, iteration appends, validation, status, and selftest. | `research-core`, `serious-research`, `workflow-tools`, `multi-agent`, `full-research` |
| `axiom-axle-mcp` | Optional inert setup helper for AxiomMath AXLE MCP formal-proof assistance. | `formal-research-remote`, `full-research` |
| `calibre` | Calibre ebook lookup and library helper workflows. | `library`, `ebook`, `serious-research`, `full-research` |
| `cross-agent-delegation` | Cross-agent delegation packet contract for bounded parent-controlled handoffs. | `multi-agent`, `serious-research`, `full-research` |
| `database-lookup` | Structured public scientific, biomedical, regulatory, materials, and economic database lookups. | `document`, `serious-research`, `full-research` |
| `deep-research-workflow` | Phased source-preserving research workflow: search, analyze, write, with citation handoff. | `research-core`, `serious-research`, `full-research` |
| `digest-bridge` | Convert digest output into paper retrieval manifests. | `digest`, `full-research` |
| `docling` | Parse, convert, OCR, chunk, and analyze documents. | `document`, `serious-research`, `full-research` |
| `draft-writing` | Claim-preserving draft writing workflow for controlled rewriting, polishing, and revision audits. | `writing-workflow`, `full-research` |
| `formal-skeleton-helper` | Generate minimal Lean-style theorem skeletons, namespace wrappers, and formal statement stubs. | `workflow-tools`, `math`, `formal-research`, `formal-research-remote`, `serious-research`, `full-research` |
| `get-available-resources` | Detect CPU, memory, disk, and optional accelerator availability before heavy local work. | `workflow-tools`, `serious-research`, `full-research` |
| `getscipapers-requester` | External paper retrieval fallback after local library checks. | `library`, `serious-research`, `full-research` |
| `graph-verifier` | Lightweight graph sanity checks. | `math`, `full-research` |
| `lean-formalization-intake` | Optional local-first Lean formalization intake and suitability decision workflow. | `formal-research`, `formal-research-remote`, `full-research` |
| `lean-strict-verification-gate` | Scanner-first Lean artifact verification gate that separates typecheck status from claim support. | `formal-research`, `formal-research-remote`, `full-research` |
| `modal-research-compute` | Route heavy compute jobs to Modal through a local broker. | `full-research` |
| `model-router` | Choose an appropriate model, reasoning level, and role for subagents or multi-agent research work. | `workflow-tools`, `multi-agent`, `serious-research`, `full-research` |
| `paper-lookup` | External paper metadata and discovery fallback. | `library`, `serious-research`, `full-research` |
| `paper-review` | Single-agent paper review workflow. | `serious-research`, `full-research` |
| `prose` | Structured reproducible research and workflow orchestration. | `multi-agent`, `serious-research`, `full-research` |
| `research-briefing` | Scope nontrivial research before execution with evidence plan and workflow recommendation. | `research-core`, `serious-research`, `full-research` |
| `research-digest-wrapper` | Run tracked-topic research digests. | `digest`, `full-research` |
| `research-report-reviewer` | Review draft research reports for unsupported claims, ambiguity, and evidence gaps. | `research-core`, `serious-research`, `full-research` |
| `research-verification-gate` | Final evidence, date, and gap check before delivery. | `research-core`, `serious-research`, `full-research` |
| `rss-news-digest` | Run and manage RSS digest workflows. | `digest`, `full-research` |
| `sagemath` | Sage-backed math, graph theory, algebra, and verification. | `math`, `full-research` |
| `self-improving-agent` | Log durable learnings and propose canonical repo integration plans across install targets. | `full-research` |
| `session-logs` | Search prior local agent session logs when explicitly requested. | `full-research` |
| `source-research` | General web and source-gathering research workflow for current-information synthesis. | `research-core`, `serious-research`, `full-research` |
| `submission-venue-selector` | Evidence-gated journal and conference venue selection for scholarly drafts; deliverable rankings require comparator-paper evidence. | `serious-research`, `full-research` |
| `tikz-draw` | Structural TikZ figure generation, compile, review, and semantic checks. | `figure`, `full-research` |
| `vnthuquan` | Vietnam Thu Quan ebook discovery, validation, dry-run download, and Calibre dry-run handoff. | `ebook`, `full-research` |
| `workspace-rearranger` | Plan safe workspace organization with dry-run first, explicit apply, and no silent deletion. | `workflow-tools`, `serious-research`, `full-research` |
| `zotero` | Zotero paper search, retrieval, ingest, and collection workflow. | `library`, `serious-research`, `full-research` |
