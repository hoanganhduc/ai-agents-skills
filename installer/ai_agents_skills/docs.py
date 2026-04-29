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

Shared, manifest-driven skills and settings for Codex, Claude, and DeepSeek.

This repo is a generator and installer, not a copied dotfiles folder. It uses
canonical skill names, generates per-agent adapters, supports partial installs,
detects legacy/self-contained installs, and verifies only installed managed
skills.

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
