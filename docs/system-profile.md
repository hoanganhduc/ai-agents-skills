# Sanitized Maintainer System Profile

This document records the real development setup observed during dry-run and
doctor checks. Personal paths, usernames, emails, credentials, local libraries,
and secrets are intentionally omitted or replaced with placeholders.

This page is not required for normal installation. It explains the environment
that motivated the repository design, so readers can understand why the
installer supports Linux, mounted Windows profiles, WSL-backed SageMath, and
multiple agent homes. Treat it as an example deployment, not as a requirement
for your own machine.

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
  `precheck` decides whether that capability is local, WSL-backed, missing, or
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
| Codex | Existing skills under `<HOME>/.codex/skills` | Primary Codex target. Existing unmanaged files are skipped by default; canonical installs and migrations write here. |
| Codex optional workspace | Optional `<HOME>/.agents/skills` when present | Compatibility or workspace-local target, not the default global target. |
| Claude | Existing skills under `<HOME>/.claude/skills`; some legacy aliases such as `deep-research` | Canonical names are used for new installs; aliases are detected and skipped unless migrated. |
| DeepSeek | Existing skills under `<HOME>/.deepseek/skills` | Existing unmanaged skills are skipped by default. |

The dry-run state had no managed `ai-agents-skills` instruction blocks yet.

## Tool Detection Summary

| Tool | Linux observation | Windows-profile observation | Related skills |
|---|---|---|---|
| `python-runtime` | system Python 3.10 with `ssl`, `venv`, and `pip` | native Windows Python can be detected from `C:\Python3*`, per-user Python installs, Program Files installs, or PATH-style candidates; mounted checks can verify package markers without running `python.exe` | `deep-research-workflow`, `zotero`, `docling`, digest skills, `graph-verifier`, `tikz-draw`, `session-logs` |
| `tex-runtime` | `pdflatex` from TeX Live detected | TeX Live under `C:\texlive\*\bin\windows` and common MiKTeX roots can be detected as present-unverified from a mounted Windows filesystem | `tikz-draw` |
| `sage-runtime` | not detected on Linux `PATH` | WSL-backed Sage is checked via `wsl.exe` when runnable, current local WSL paths when precheck runs from WSL/Linux, mounted WSL rootfs paths when present, and WSL `ext4.vhdx` presence as a degraded inspection gap | `sagemath`, optional `tikz-draw` graph mode |

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

Related pages: [Dependencies](dependencies.md), [Windows](windows.md),
[Linux](linux.md), [Audit And Migration](audit-and-migration.md).
