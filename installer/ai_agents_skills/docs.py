from __future__ import annotations

from pathlib import Path
from typing import Any

from .manifest import REPO_ROOT


def generate_docs(manifests: dict[str, Any]) -> list[Path]:
    docs_dir = REPO_ROOT / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    written = [
        write_readme(manifests),
        write_skills_doc(manifests, docs_dir / "skills.md"),
        write_profiles_doc(manifests, docs_dir / "profiles.md"),
        write_dependencies_doc(manifests, docs_dir / "dependencies.md"),
        write_static_doc(docs_dir / "workflow-overview.md", workflow_overview_text()),
        write_static_doc(docs_dir / "system-profile.md", system_profile_text()),
        write_verification_doc(docs_dir / "verification.md"),
        write_static_doc(docs_dir / "architecture.md", architecture_text()),
        write_static_doc(docs_dir / "installation.md", installation_text()),
        write_static_doc(docs_dir / "windows.md", windows_text()),
        write_static_doc(docs_dir / "linux.md", linux_text()),
        write_static_doc(docs_dir / "troubleshooting.md", troubleshooting_text()),
        write_static_doc(docs_dir / "uninstall-rollback.md", uninstall_text()),
    ]
    return written


def write_readme(manifests: dict[str, Any]) -> Path:
    path = REPO_ROOT / "README.md"
    skills_table = skill_table(manifests)
    profiles_table = profiles_table_text(manifests)
    path.write_text(
        f"""# AI Agents Skills

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

This repo is a generator and installer, not a copied dotfiles folder. It uses
canonical skill names, generates per-agent adapters, supports partial installs,
detects legacy/self-contained installs, and verifies only installed managed
skills. Reusable skill bodies live under `canonical/skills`; the installer
copies those bodies into each supported agent and adds managed metadata.

## Documentation

- `docs/installation.md`: install, dry-run, conflict, and migration modes.
- `docs/skills.md`: skill catalog and descriptions.
- `docs/profiles.md`: selectable profiles such as `research-core` and
  `full-research`.
- `docs/dependencies.md`: logical tools and dependency categories.
- `docs/workflow-overview.md`: how agents, skills, runtimes, and research
  tools connect during real workflows.
- `docs/system-profile.md`: sanitized maintainer-system profile and how local
  tools map to skills.
- `docs/verification.md`: installed-artifact verification model.

The GitHub Pages site is built from `docs/source` and deployed by
`.github/workflows/docs.yml`.

## Quick Start

Linux:

```bash
make doctor
make list-skills
make plan ARGS="--profile research-core"
make install ARGS="--profile research-core --dry-run"
make install ARGS="--profile research-core --apply --root /tmp/aas-fake-home"
make verify ARGS="--root /tmp/aas-fake-home"
```

Windows:

```bat
make.bat doctor
make.bat list-skills
make.bat plan --profile research-core
make.bat install --profile research-core --dry-run
make.bat install --profile research-core --apply --root %TEMP%\\aas-fake-home
make.bat verify --root %TEMP%\\aas-fake-home
```

Real-system writes require explicit `--apply --real-system`. Tests and examples
use fake roots. Existing unmanaged files are skipped by default; use `--adopt`,
`--backup-replace`, or `--migrate` only after reviewing `plan` output.

## Profiles

{profiles_table}

## Skills

{skills_table}
""",
        encoding="utf-8",
    )
    return path


def write_skills_doc(manifests: dict[str, Any], path: Path) -> Path:
    path.write_text("# Skills\n\n" + skill_table(manifests) + "\n", encoding="utf-8")
    return path


def write_profiles_doc(manifests: dict[str, Any], path: Path) -> Path:
    path.write_text("# Profiles\n\n" + profiles_table_text(manifests) + "\n", encoding="utf-8")
    return path


def write_dependencies_doc(manifests: dict[str, Any], path: Path) -> Path:
    tools = manifests["dependencies"]["tools"]
    lines = ["# Dependencies", "", "| Logical Tool | Description |", "|---|---|"]
    for name in sorted(tools):
        lines.append(f"| `{name}` | {tools[name]['description']} |")
    lines.extend(
        [
            "",
            "Dependencies are declared as logical capabilities rather than personal",
            "paths. `doctor` resolves them from environment overrides, repo-local",
            "runtimes, `PATH`, native Windows commands, and WSL-backed commands where",
            "appropriate.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_verification_doc(path: Path) -> Path:
    path.write_text(
        """# Verification

Verification is selective. Only installed and enabled managed artifacts from the
installer state are checked.

Skill checks:

- `L1 file-exists`
- `L2 metadata-valid`
- `L3 agent-visible`
- `L4 runner-doctor`
- `L5 smoke-test`

Settings checks:

- `S1 file-exists`
- `S2 parse-valid`
- `S3 managed-block-present`
- `S4 no-secret-leak`
- `S5 agent-loads-config`
""",
        encoding="utf-8",
    )
    return path


def workflow_overview_text() -> str:
    return """# System And Research Workflow Overview

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
"""


def skill_table(manifests: dict[str, Any]) -> str:
    rows = ["| Skill | Description | Profiles |", "|---|---|---|"]
    for name, spec in sorted(manifests["skills"]["skills"].items()):
        profiles = ", ".join(f"`{p}`" for p in spec.get("profiles", []))
        rows.append(f"| `{name}` | {spec['description']} | {profiles} |")
    return "\n".join(rows)


def profiles_table_text(manifests: dict[str, Any]) -> str:
    rows = ["| Profile | Description | Skills |", "|---|---|---|"]
    for name, spec in sorted(manifests["profiles"]["profiles"].items()):
        skills = ", ".join(f"`{s}`" for s in spec["skills"])
        rows.append(f"| `{name}` | {spec['description']} | {skills} |")
    return "\n".join(rows)


def write_static_doc(path: Path, text: str) -> Path:
    path.write_text(text.strip() + "\n", encoding="utf-8")
    return path


def architecture_text() -> str:
    return """# Architecture

The manifests are the source of truth. The installer resolves canonical skills
to per-agent target artifacts and records ownership in a journal. Existing
unmanaged files are skipped by default. Agent-specific legacy locations, such as
Codex's historical `~/.codex/skills`, are detected during planning so existing
skills are not duplicated unless the user explicitly chooses `--migrate`.
"""


def installation_text() -> str:
    return """# Installation

Use `make doctor` or `make.bat doctor` first. Use `plan` before `install`.
Partial installs are first-class: select `--skill`, `--skills`, or `--profile`.
`install --dry-run` previews the same actions as a default install preview;
`install --apply` is required before any writes occur.
Conflict modes:

- default: create missing managed files and skip unmanaged or legacy files
- `--adopt`: record an existing target file as user-owned managed state
- `--backup-replace`: back up and replace an unmanaged target file
- `--migrate`: copy a detected legacy skill into the canonical target while
  leaving the legacy source in place
"""


def system_profile_text() -> str:
    return """# Sanitized Maintainer System Profile

This document records the real development setup observed during dry-run and
doctor checks. Personal paths, usernames, emails, credentials, local libraries,
and secrets are intentionally omitted or replaced with placeholders.

## Roots

| Substrate | Placeholder | Notes |
|---|---|---|
| Linux home | `<LINUX_HOME>` | Primary development root used for local tests. |
| Mounted Windows home | `<WINDOWS_HOME>` | Windows profile inspected from Linux/WSL-style mount. |

## Execution Topology

The observed setup is best understood as shared research logic plus
agent-local installation targets:

- Codex, Claude, and DeepSeek each load skills from their own supported local
  skill/config locations.
- This repository holds the reusable skill bodies and dependency metadata.
- The installer detects which agent homes exist, installs only those targets,
  and skips absent agents without requiring their tools.
- Runtime-backed workflows use logical dependencies, not personal paths. For
  example, a skill asks for `python-runtime`, `tex-runtime`, or `sage-runtime`;
  `doctor` decides whether that capability is local, WSL-backed, missing, or
  degraded.

For a research task, the agent instruction layer chooses the workflow, while
the software layer supplies concrete capabilities such as library lookup,
document parsing, database access, figure compilation, and math verification.

## Detected Agents

| Substrate | Codex | Claude | DeepSeek |
|---|---|---|---|
| Linux | present at `<LINUX_HOME>/.codex` | present at `<LINUX_HOME>/.claude` | present at `<LINUX_HOME>/.deepseek` |
| Windows profile | present at `<WINDOWS_HOME>/.codex` | present at `<WINDOWS_HOME>/.claude` | not detected |

If an agent home is absent, the installer skips that agent and does not require
its dependencies.

## Existing Skill Layouts

| Agent | Existing layout observed | Installer behavior |
|---|---|---|
| Codex | Existing skills under `<HOME>/.codex/skills` | Treated as legacy/self-contained skills; skipped by default or copied to `<HOME>/.agents/skills` only with `--migrate`. |
| Claude | Existing skills under `<HOME>/.claude/skills`; some legacy aliases such as `deep-research` | Canonical names are used for new installs; aliases are detected and skipped unless migrated. |
| DeepSeek | Existing skills under `<HOME>/.deepseek/skills` | Existing unmanaged skills are skipped by default. |

The dry-run state had no managed `ai-agents-skills` instruction blocks yet.

## Tool Detection Summary

| Tool | Linux observation | Windows-profile observation | Related skills |
|---|---|---|---|
| `python-runtime` | system Python 3.10 with `ssl`, `venv`, and `pip` | WSL/POSIX Python 3.10 detected; native Windows Python candidates not detected from this check | `deep-research-workflow`, `zotero`, `docling`, digest skills, `graph-verifier`, `tikz-draw`, `session-logs` |
| `tex-runtime` | `pdflatex` from TeX Live detected | native Windows TeX candidates not detected from this check | `tikz-draw` |
| `sage-runtime` | not detected on Linux `PATH` | WSL-backed Sage candidate declared, but native Windows verification must run on Windows | `sagemath`, optional `tikz-draw` graph mode |

## Skill-To-Software Relationship

| Skill area | Main software or capability |
|---|---|
| Research planning and synthesis | agent instructions plus optional Python helper runtime |
| Paper/library workflows | Zotero credentials and local library access are external configuration, not repo content |
| External paper retrieval | `getscipapers`-style helper/runtime is treated as an external or runtime-backed dependency |
| Document parsing | Python plus optional `docling` package and OCR tools |
| Database lookup | public HTTP APIs; API keys, when needed, are supplied externally |
| Digest workflows | Python runtime and user-managed topic/feed files outside the repo |
| TikZ figures | TeX engine; optional SageMath and graph helpers |
| Math verification | SageMath when available; Python/NetworkX for lightweight graph checks |
| Multi-agent workflows | agent orchestration instructions; no extra binary required by default |

## Privacy Boundary

The repo should contain reusable skill logic, docs, and installers. It should
not contain personal paths, auth files, credentials, session logs, downloaded
papers/books, Zotero databases, Calibre libraries, or local runtime state.
"""


def windows_text() -> str:
    return """# Windows

Windows is multi-substrate. Native Windows, PowerShell/CMD, Git Bash/MSYS, WSL,
and remote services are checked separately. SageMath is usually WSL-backed and
must not be treated as a normal Windows package.
"""


def linux_text() -> str:
    return """# Linux

Linux checks resolve logical tools from installed commands, repo-local runtimes,
and user overrides such as `AAS_PYTHON` or `AAS_SAGE`.
"""


def troubleshooting_text() -> str:
    return """# Troubleshooting

Run `doctor --json` to inspect detected agents, selected tools, skipped agents,
and degraded optional capabilities. Use `plan` to preview every file change.
If a plan reports `classification=legacy`, the installer found a skill in an
older or agent-specific location and will skip it unless `--migrate` is used.
"""


def uninstall_text() -> str:
    return """# Uninstall And Rollback

`rollback` restores previous managed state from a recorded run. `uninstall`
removes current managed artifacts. Both support skill and agent scopes and both
support dry-run previews.
"""
