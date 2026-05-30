# AI Agents Skills Documentation

Shared, sanitized skill bodies, settings metadata, and installers for Codex,
Claude, and DeepSeek. The docs describe a research workstation where multiple
agent frontends share one canonical skill repository, while local software such
as Python, TeX, optional SageMath, library tools, document parsers, and public
database clients are detected as external capabilities instead of being
hardcoded.

Latest update: {sub-ref}`today`

## Start Here

- First time here: clone the repository, run commands from the repo root, and
  use `doctor`, `precheck`, and `plan` before any applied install.
- New install: read [Installation](installation.md), then choose a
  [Profile](profiles.md).
- Existing agent setup: run a read-only audit with
  [Audit And Migration](audit-and-migration.md) before installing anything.
- OpenClaw integration work: use the
  [OpenClaw Integration Plan](openclaw-integration-plan.md) before adding
  migration, hook, or runtime behavior.
- OpenClaw install-target work: use the
  [OpenClaw Install Target Plan](openclaw-install-target-plan.md) before
  adding OpenClaw as a writable target.
- Dependency questions: use [Dependencies](dependencies.md), then check the
  platform-specific [Windows](windows.md) or [Linux](linux.md) notes.
- Runtime-backed skill questions: use [Installation](installation.md) for
  runtime roots and inventory boundaries, then [Dependencies](dependencies.md)
  for Docling/OCR and local config notes.
- Zotero/Calibre local-library changes: run
  [Local Library Profiles](local-library-profiles.md) before choosing paths.
- Unsure what will be written: read [Agent Locations](agent-locations.md) and
  [Verification](verification.md).
- Reusable failure or workflow lesson: use `self-improving-agent` to log the
  `.learnings/` entry and propose a canonical repo integration plan with
  install-target and OS coverage before editing shared files.
- Research workflow motivation: start with
  [System And Research Workflow Overview](workflow-overview.md) and
  [Multi-Agent Examples](multi-agent-examples.md).
- Submission venue selector work: use the
  [Submission Venue Selector Plan](submission-venue-selector-plan.md) before
  implementing the automated venue-ranking skill.

## Safety Model

The installer is dry-run first. Real home-directory writes require explicit
`--apply --real-system`. Existing unmanaged files are skipped unless the user
chooses `--adopt`, `--backup-replace`, or `--migrate`. Secrets, auth files,
session logs, local paper libraries, and runtime caches are outside the managed
scope.

Runtime-backed skills use managed helper files under a shared runtime root.
Live runtime config, caches, local databases, downloaded papers, bytecode, and
secrets stay outside the canonical source tree. The Docling wrapper is
local-only by default; OCR.space is available only as an explicit fallback
with `--ocr-fallback ocrspace --allow-remote-ocr`.

Most repository docs are generated from `installer/ai_agents_skills/docs.py`
and manifest data. Edit the generator or manifests, run `make docs`, then use
`make docs-site` when you need to preview the Sphinx site. This `index.md` page
and `overview.md` are maintained manually as docs-site landing pages.

```{toctree}
:maxdepth: 2
:caption: Contents

overview
workflow-overview
multi-agent-examples
submission-venue-selector-plan
installation
skills
artifacts
profiles
dependencies
system-profile
agent-locations
local-library-profiles
audit-and-migration
openclaw-integration-plan
openclaw-install-target-plan
verification
architecture
windows
linux
troubleshooting
uninstall-rollback
```
