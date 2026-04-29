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
![Platforms](https://img.shields.io/badge/platform-Linux%20%7C%20Windows-blue)
![Agents](https://img.shields.io/badge/agents-Codex%20%7C%20Claude%20%7C%20DeepSeek-black)
![Docs](https://img.shields.io/badge/docs-GitHub%20Pages-brightgreen?logo=githubpages)
![Status](https://img.shields.io/badge/status-active-yellow)
![License](https://img.shields.io/badge/license-GPL--3.0--or--later-blue)

Shared, manifest-driven skills and settings for Codex, Claude, and DeepSeek.

## System Summary

This is an experimental, personal-use configuration for research workflows,
especially combinatorics and graph theory work. It is not a polished general
product, and it may not behave as desired on other machines, other agent
versions, or research tasks outside the assumptions documented here.

This repo turns a multi-agent research setup into one maintainable skill source.
Codex, Claude, and DeepSeek can each load local skills, while this repository
keeps the shared research workflows, profiles, dependency metadata, and
installer logic in one place.

The research stack is organized as:

- agent frontends: Codex, Claude, and DeepSeek
- shared skill source: `manifest/`, `canonical/skills/`, and `targets/`
- external capabilities: Python, TeX, optional SageMath, local library tools,
  document parsers, public databases, and retrieval helpers

For example, a literature-review request can route through
`research-briefing`, `deep-research-workflow`, `paper-lookup`, and
`research-verification-gate`; a paper-review request can check `zotero` first,
fall back to `calibre` for books, parse files with `docling`, and then run
`paper-review`.

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
skills. Reusable skill bodies live under `canonical/skills`; the installer
copies those bodies into each supported agent and adds managed metadata.

## Documentation

- `docs/installation.md`: install, dry-run, conflict, and migration modes.
- `docs/skills.md`: skill catalog and descriptions.
- `docs/artifacts.md`: optional templates, instruction docs, personas, and
  entrypoint aliases.
- `docs/profiles.md`: selectable profiles such as `research-core` and
  `full-research`.
- `docs/dependencies.md`: logical tools, current Linux/Windows extra
  software, Python packages, Node packages, and manual integrations.
- `docs/workflow-overview.md`: how agents, skills, runtimes, and research
  tools connect during real workflows.
- `docs/multi-agent-examples.md`: multi-agent process examples, spawn/wait
  lifecycle, and available research templates.
- `docs/system-profile.md`: sanitized maintainer-system profile and how local
  tools map to skills.
- `docs/agent-locations.md`: supported agent config, skill, template, command,
  persona, and tool-shim locations.
- `docs/audit-and-migration.md`: audit output, staged migration, unmanaged
  local skill handling, and Windows-native verification notes.
- `docs/verification.md`: installed-artifact verification model.

The GitHub Pages site is built from `docs/source` and deployed by
`.github/workflows/docs.yml`.

## Acknowledgements

This repository was implemented and maintained with help from ChatGPT Codex.

## License

This project is licensed under GPL-3.0-or-later. See `LICENSE`.

## Quick Start

Linux:

```bash
make doctor
make precheck ARGS="--profile research-core"
make audit-system ARGS="--profile research-core"
make list-skills
make list-artifacts
make plan ARGS="--profile research-core"
make plan ARGS="--no-skills --artifact-profile workflow-templates"
make install ARGS="--profile research-core --dry-run"
make install ARGS="--profile research-core --apply --root /tmp/aas-fake-home"
make verify ARGS="--root /tmp/aas-fake-home"
```

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
make.bat install --profile research-core --apply --root %TEMP%\aas-fake-home
make.bat verify --root %TEMP%\aas-fake-home
```

Real-system writes require explicit `--apply --real-system`. Tests and examples
use fake roots. Existing unmanaged files are skipped by default; use `--adopt`,
`--backup-replace`, or `--migrate` only after reviewing `plan` output.

Optional workflow artifacts are not installed by default. Use
`--artifact-profile workflow-templates`, `--artifact-profile review-personas`,
`--artifact-profile workflow-instructions`, or
`--artifact-profile research-entrypoints` explicitly. Use `--with-deps` when
dependency-bound artifacts should also install their backing skills.

## Profiles

| Profile | Description | Skills |
|---|---|---|
| `digest` | Tracked-topic and RSS digest workflows. | `research-digest-wrapper`, `rss-news-digest`, `digest-bridge` |
| `document` | Document conversion and structured database lookup. | `docling`, `database-lookup` |
| `ebook` | Ebook discovery and library handoff. | `calibre`, `vnthuquan` |
| `figure` | Structural figure generation and checking. | `tikz-draw` |
| `full-research` | All research-related skills. | `*` |
| `library` | Paper and ebook library workflows. | `zotero`, `calibre`, `getscipapers-requester`, `paper-lookup` |
| `math` | Math and graph verification workflows. | `sagemath`, `graph-verifier` |
| `multi-agent` | Multi-agent and structured workflow orchestration. | `agent-group-discuss`, `prose`, `model-router` |
| `research-core` | Default research planning, source gathering, report review, and delivery verification. | `research-briefing`, `deep-research-workflow`, `source-research`, `research-report-reviewer`, `research-verification-gate` |
| `workflow-tools` | Reusable planning helpers for resources, model routing, formal skeletons, and workspace organization. | `get-available-resources`, `model-router`, `formal-skeleton-helper`, `workspace-rearranger` |

## Artifact Profiles

| Artifact Profile | Description | Artifacts |
|---|---|---|
| `repo-management` | Top-level managed notice blocks for agent instruction files. | `management-notice:repo-management` |
| `research-entrypoints` | Optional command or quick-action aliases that point to backing skills. | `entrypoint-alias:deep-research`, `entrypoint-alias:research-team`, `entrypoint-alias:review`, `entrypoint-alias:tikz`, `entrypoint-alias:sage`, `entrypoint-alias:zotero`, `entrypoint-alias:docling`, `entrypoint-alias:calibre`, `entrypoint-alias:vnthuquan`, `entrypoint-alias:research-compute`, `entrypoint-alias:rss`, `entrypoint-alias:digest`, `entrypoint-alias:getscipapers` |
| `review-personas` | Reviewer and research role personas rendered to each agent's supported format. | `agent-persona:literature-scout`, `agent-persona:math-explorer`, `agent-persona:proof-checker`, `agent-persona:paper-reviewer`, `agent-persona:code-reviewer`, `agent-persona:test-reviewer`, `agent-persona:security-reviewer` |
| `workflow-artifacts` | All portable templates, workflow docs, personas, and entrypoint aliases. | `template:spec`, `template:tasks-plan`, `template:tasks-todo`, `template:deep-research-sources`, `template:deep-research-analysis`, `template:deep-research-report`, `instruction-doc:engineering-lifecycle`, `instruction-doc:research-quick-actions`, `instruction-doc:python-quality-gates`, `instruction-doc:modal-offload-routing`, `instruction-doc:scrapling-integration`, `instruction-doc:language-style-rules`, `agent-persona:literature-scout`, `agent-persona:math-explorer`, `agent-persona:proof-checker`, `agent-persona:paper-reviewer`, `agent-persona:code-reviewer`, `agent-persona:test-reviewer`, `agent-persona:security-reviewer`, `entrypoint-alias:deep-research`, `entrypoint-alias:research-team`, `entrypoint-alias:review`, `entrypoint-alias:tikz`, `entrypoint-alias:sage`, `entrypoint-alias:zotero`, `entrypoint-alias:docling`, `entrypoint-alias:calibre`, `entrypoint-alias:vnthuquan`, `entrypoint-alias:research-compute`, `entrypoint-alias:rss`, `entrypoint-alias:digest`, `entrypoint-alias:getscipapers`, `management-notice:repo-management` |
| `workflow-instructions` | Agent-readable workflow guidance documents copied outside skill folders. | `instruction-doc:engineering-lifecycle`, `instruction-doc:research-quick-actions`, `instruction-doc:python-quality-gates`, `instruction-doc:modal-offload-routing`, `instruction-doc:scrapling-integration`, `instruction-doc:language-style-rules` |
| `workflow-templates` | Reusable research, specification, and task templates. | `template:spec`, `template:tasks-plan`, `template:tasks-todo`, `template:deep-research-sources`, `template:deep-research-analysis`, `template:deep-research-report` |

## Skills

| Skill | Description | Profiles |
|---|---|---|
| `agent-group-discuss` | Multi-agent discussion, review, and research orchestration. | `multi-agent`, `full-research` |
| `annotated-review` | Annotated paper review workflow when both annotation and review are requested. | `full-research` |
| `calibre` | Calibre ebook lookup and library helper workflows. | `library`, `ebook`, `full-research` |
| `database-lookup` | Structured public scientific, biomedical, regulatory, materials, and economic database lookups. | `document`, `full-research` |
| `deep-research-workflow` | Phased source-preserving research workflow: search, analyze, write, with citation handoff. | `research-core`, `full-research` |
| `digest-bridge` | Convert digest output into paper retrieval manifests. | `digest`, `full-research` |
| `docling` | Parse, convert, OCR, chunk, and analyze documents. | `document`, `full-research` |
| `formal-skeleton-helper` | Generate minimal Lean-style theorem skeletons, namespace wrappers, and formal statement stubs. | `workflow-tools`, `math`, `full-research` |
| `get-available-resources` | Detect CPU, memory, disk, and optional accelerator availability before heavy local work. | `workflow-tools`, `full-research` |
| `getscipapers-requester` | External paper retrieval fallback after local library checks. | `library`, `full-research` |
| `graph-verifier` | Lightweight graph sanity checks. | `math`, `full-research` |
| `modal-research-compute` | Route heavy compute jobs to Modal through a local broker. | `full-research` |
| `model-router` | Choose an appropriate model, reasoning level, and role for subagents or multi-agent research work. | `workflow-tools`, `multi-agent`, `full-research` |
| `paper-lookup` | External paper metadata and discovery fallback. | `library`, `full-research` |
| `paper-review` | Single-agent paper review workflow. | `full-research` |
| `prose` | Structured reproducible research and workflow orchestration. | `multi-agent`, `full-research` |
| `research-briefing` | Scope nontrivial research before execution with evidence plan and workflow recommendation. | `research-core`, `full-research` |
| `research-digest-wrapper` | Run tracked-topic research digests. | `digest`, `full-research` |
| `research-report-reviewer` | Review draft research reports for unsupported claims, ambiguity, and evidence gaps. | `research-core`, `full-research` |
| `research-verification-gate` | Final evidence, date, and gap check before delivery. | `research-core`, `full-research` |
| `rss-news-digest` | Run and manage RSS digest workflows. | `digest`, `full-research` |
| `sagemath` | Sage-backed math, graph theory, algebra, and verification. | `math`, `full-research` |
| `self-improving-agent` | Log durable learnings, failures, and missing capabilities. | `full-research` |
| `session-logs` | Search prior local agent session logs when explicitly requested. | `full-research` |
| `source-research` | General web and source-gathering research workflow for current-information synthesis. | `research-core`, `full-research` |
| `tikz-draw` | Structural TikZ figure generation, compile, review, and semantic checks. | `figure`, `full-research` |
| `vnthuquan` | Vietnam Thu Quan ebook discovery, validation, dry-run download, and Calibre dry-run handoff. | `ebook`, `full-research` |
| `workspace-rearranger` | Plan safe workspace organization with dry-run first, explicit apply, and no silent deletion. | `workflow-tools`, `full-research` |
| `zotero` | Zotero paper search, retrieval, ingest, and collection workflow. | `library`, `full-research` |
