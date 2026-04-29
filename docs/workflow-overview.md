# System And Research Workflow Overview

This repository is designed for an experimental personal multi-agent research
workstation, with an emphasis on combinatorics and graph theory workflows. It
is not guaranteed to work as desired in every environment. Codex, Claude, and
DeepSeek each keep their own local configuration directory, but the reusable
research instructions live here as canonical skill bodies. The installer copies
those skill bodies into whichever agents are present and leaves absent agents
alone.

The system has three layers:

| Layer | Role |
|---|---|
| Agent frontends | Codex, Claude, and DeepSeek receive user requests and load installed skill instructions. |
| Shared skill repository | `manifest/` selects skills and profiles; `canonical/skills/` stores reusable workflows; `targets/` holds agent-specific notes. |
| Runtime and software tools | Python, TeX, optional SageMath, local library tools, document parsers, public databases, and external retrieval helpers do the actual work when a skill needs them. |

The installer links these layers without embedding private state. It does not
store credentials, session logs, local library databases, downloaded papers, or
machine-specific paths. Instead, `doctor` detects logical capabilities such as
`python-runtime`, `tex-runtime`, `sage-runtime`, library access, and optional
Python packages on the current system.

A typical research workflow looks like this:

1. A request enters one installed agent, for example Codex, Claude, or DeepSeek.
2. The agent loads a shared skill such as `research-briefing`,
   `deep-research-workflow`, `zotero`, `docling`, or `tikz-draw`.
3. The skill routes to the right software capability: local libraries first,
   document parsing when files are involved, public databases for structured
   records, TeX for figures, and SageMath or Python for math checks.
4. The final answer passes through review or verification skills when the task
   needs stronger evidence control.

Examples:

- **Current literature brief:** `research-briefing` scopes the question,
  `deep-research-workflow` preserves source IDs across search and synthesis,
  `paper-lookup` or `database-lookup` fills metadata gaps, then
  `research-report-reviewer` and `research-verification-gate` check the final
  report.
- **Paper review from a local library:** `zotero` checks the paper library
  first, `calibre` is used for book-like review inputs, `docling` parses local
  documents when structure matters, and `paper-review` or
  `annotated-review` performs the review workflow.
- **Research figure or math-heavy answer:** `deep-research-workflow` produces a
  figure brief, `tikz-draw` turns it into a structural diagram using TeX, and
  `sagemath` or `graph-verifier` handles graph or algebra checks when
  available.
- **Windows plus WSL-backed tools:** Windows agents can receive the same skill
  bodies as Linux agents. Tools such as SageMath may be detected as WSL-backed
  capabilities, so the dependency graph records the substrate instead of
  hardcoding a personal path.
