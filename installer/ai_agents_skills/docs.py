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
        write_artifacts_doc(manifests, docs_dir / "artifacts.md"),
        write_profiles_doc(manifests, docs_dir / "profiles.md"),
        write_dependencies_doc(manifests, docs_dir / "dependencies.md"),
        write_static_doc(docs_dir / "workflow-overview.md", workflow_overview_text()),
        write_static_doc(docs_dir / "multi-agent-examples.md", multi_agent_examples_text()),
        write_static_doc(docs_dir / "system-profile.md", system_profile_text()),
        write_static_doc(docs_dir / "agent-locations.md", agent_locations_text()),
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
    artifact_profiles_table = artifact_profiles_table_text(manifests)
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
make.bat list-skills
make.bat list-artifacts
make.bat plan --profile research-core
make.bat plan --no-skills --artifact-profile workflow-templates
make.bat install --profile research-core --dry-run
make.bat install --profile research-core --apply --root %TEMP%\\aas-fake-home
make.bat verify --root %TEMP%\\aas-fake-home
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

{profiles_table}

## Artifact Profiles

{artifact_profiles_table}

## Skills

{skills_table}
""",
        encoding="utf-8",
    )
    return path


def write_skills_doc(manifests: dict[str, Any], path: Path) -> Path:
    path.write_text("# Skills\n\n" + skill_table(manifests) + "\n", encoding="utf-8")
    return path


def write_artifacts_doc(manifests: dict[str, Any], path: Path) -> Path:
    path.write_text(
        "# Optional Artifacts\n\n"
        "Artifacts are opt-in files outside normal skill directories. They are "
        "installed only when selected with `--artifact`, `--artifacts`, or "
        "`--artifact-profile`.\n\n"
        + artifact_profiles_table_text(manifests)
        + "\n\n"
        + artifact_table(manifests)
        + "\n\n"
        "Artifacts with dependencies are installed only when their backing "
        "skill is selected, already managed, or added with `--with-deps`. DeepSeek "
        "personas are installed as reference prompts because native persona-file "
        "loading has not been verified.\n",
        encoding="utf-8",
    )
    return path


def write_profiles_doc(manifests: dict[str, Any], path: Path) -> Path:
    path.write_text("# Profiles\n\n" + profiles_table_text(manifests) + "\n", encoding="utf-8")
    return path


def write_dependencies_doc(manifests: dict[str, Any], path: Path) -> Path:
    tools = manifests["dependencies"]["tools"]
    lines = ["# Dependencies", "", "| Logical Tool | Description |", "|---|---|"]
    for name in sorted(tools):
        lines.append(f"| `{name}` | {tools[name]['description']} |")
    packages = manifests["dependencies"].get("packages", {})
    if packages:
        lines.extend(["", "## Packages And Services", "", "| Dependency | Type | Detail |", "|---|---|---|"])
        for name in sorted(packages):
            spec = packages[name]
            detail = spec.get("module") or spec.get("logical_tool") or spec.get("type", "")
            if spec.get("candidate_set"):
                detail = f"{detail}; candidate set `{spec['candidate_set']}`"
            lines.append(f"| `{name}` | `{spec.get('type')}` | {detail} |")
    lines.extend(current_config_dependency_sections(manifests))
    lines.extend(
        [
            "",
            "Dependencies are declared as logical capabilities rather than personal",
            "paths. `precheck` resolves them from environment overrides, repo-local",
            "runtimes, `PATH`, native Windows commands, Python imports, remote-service",
            "placeholders, and WSL-backed commands where appropriate. Python package",
            "checks use root-relative candidate sets, including agent virtualenvs,",
            "user-local site-package directories, Codex runtime site-package",
            "directories, dedicated Docling environments, official Windows Python",
            "install roots, and per-user Windows package directories.",
            "When inspecting a mounted Windows home from Linux, `precheck` can",
            "verify package markers in `site-packages`, find common TeX Live and",
            "MiKTeX install roots, detect Sage in the current WSL/Linux",
            "filesystem, and detect mounted WSL rootfs Sage paths or WSL VHDX",
            "presence. It still marks native Windows executables as",
            "present-unverified instead of trying",
            "to execute them.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def current_config_dependency_sections(manifests: dict[str, Any]) -> list[str]:
    inventory = manifests.get("system_dependencies", {})
    if not inventory:
        return []
    lines = [
        "",
        "## Current Linux And Windows Config Inventory",
        "",
        "This sanitized inventory is derived from the maintainer's current Linux",
        "and Windows Codex, Claude, and DeepSeek configs. It intentionally excludes",
        "auth files, provider secrets, session/history/log files, local library",
        "databases, caches, backups, and file-history snapshots.",
        "",
        "Personal paths are represented as `<LINUX_HOME>` or `<WINDOWS_HOME>`.",
        "",
    ]

    scope = inventory.get("scope", {})
    included = scope.get("included_evidence", [])
    if included:
        lines.extend(["Evidence inspected:", ""])
        lines.extend(f"- {item}" for item in included)
        lines.append("")

    software = inventory.get("software", {})
    if software:
        lines.extend(
            [
                "### Extra Software",
                "",
                "| Software | Requirement | Linux | Windows | Used By |",
                "|---|---|---|---|---|",
            ]
        )
        for name, spec in sorted(software.items()):
            used_by = ", ".join(f"`{item}`" for item in spec.get("used_by", []))
            lines.append(
                "| "
                f"`{name}`"
                f" | {md_cell(spec.get('requirement', ''))}"
                f" | {md_cell(spec.get('linux', ''))}"
                f" | {md_cell(spec.get('windows', ''))}"
                f" | {used_by}"
                " |"
            )
        lines.append("")

    python_packages = inventory.get("python_packages", {})
    if python_packages:
        lines.extend(
            [
                "### Python Packages",
                "",
                "| Package | Import | Requirement | Platforms | Used By |",
                "|---|---|---|---|---|",
            ]
        )
        for name, spec in sorted(python_packages.items()):
            platforms = ", ".join(f"`{item}`" for item in spec.get("platforms", []))
            used_by = ", ".join(f"`{item}`" for item in spec.get("used_by", []))
            lines.append(
                "| "
                f"`{name}`"
                f" | `{spec.get('import_name', '')}`"
                f" | {md_cell(spec.get('requirement', ''))}"
                f" | {platforms}"
                f" | {used_by}"
                " |"
            )
        lines.append("")

    node_packages = inventory.get("node_packages", {})
    if node_packages:
        lines.extend(
            [
                "### Node Packages",
                "",
                "| Package | Requirement | Used By | Notes |",
                "|---|---|---|---|",
            ]
        )
        for name, spec in sorted(node_packages.items()):
            used_by = ", ".join(f"`{item}`" for item in spec.get("used_by", []))
            notes = spec.get("source", "")
            runtime_deps = spec.get("runtime_dependencies", [])
            if runtime_deps:
                notes = f"{notes} Runtime deps: " + ", ".join(f"`{item}`" for item in runtime_deps)
            lines.append(
                "| "
                f"`{name}`"
                f" | {md_cell(spec.get('requirement', ''))}"
                f" | {used_by}"
                f" | {md_cell(notes)}"
                " |"
            )
        lines.append("")

    manual = inventory.get("manual_integrations", {})
    if manual:
        lines.extend(
            [
                "### Manual Integrations",
                "",
                "| Integration | Description | Used By |",
                "|---|---|---|",
            ]
        )
        for name, spec in sorted(manual.items()):
            used_by = ", ".join(f"`{item}`" for item in spec.get("used_by", []))
            lines.append(f"| `{name}` | {md_cell(spec.get('description', ''))} | {used_by} |")
        lines.append("")

    notes = inventory.get("windows_substrate_notes", [])
    if notes:
        lines.extend(["### Windows Substrate Notes", ""])
        lines.extend(f"- {item}" for item in notes)
        lines.append("")
    return lines


def write_verification_doc(path: Path) -> Path:
    path.write_text(
        """# Verification

Verification is selective. Only installed and enabled managed artifacts from the
installer state are checked.

Current skill checks:

- `L1 file-exists`
- `L2 metadata-valid`
- `L3 managed-marker`
- `L4 no-secret-leak`
- `L5 agent-visible`

Current instruction-block checks:

- `S1 file-exists`
- `S2 managed-block-present`
- `S3 no-secret-leak`

Current support-file checks:

- `A1 file-exists`
- `A2 managed-marker`
- `A3 no-secret-leak`

Current optional artifact checks:

- `O1 file-exists`
- `O2 managed-marker`
- `O3 no-secret-leak`
- `O4 format-specific checks for Codex TOML personas and Claude frontmatter`

The verifier intentionally skips skills and artifacts that were not installed.
Runtime smoke tests, runner-specific `doctor` commands, and direct
`agent-loads-config` checks are not automatic yet; use `precheck` and the
agent's own diagnostics for those layers.
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
machine-specific paths. Instead, `precheck` detects logical capabilities such
as `python-runtime`, `tex-runtime`, `sage-runtime`, library access, and
optional Python packages on the current system.

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


def multi_agent_examples_text() -> str:
    return """# Multi-Agent Examples And Templates

This page describes how the experimental multi-agent layer is intended to work
in this personal research setup. It is optimized for combinatorics, graph
theory, mathematical writing, and related research workflows. It may not behave
as desired on every agent frontend or model version.

The shared skills involved are:

| Skill | Use |
|---|---|
| `agent-group-discuss` | Template-based multi-agent discussion, review, and research. |
| `prose` | More explicit OpenProse-style decomposition, parallel work, and synthesis. |
| `sagemath` | Optional graph theory, algebra, enumeration, and invariant checks. |
| `graph-verifier` | Lightweight graph sanity checks. |
| `research-verification-gate` | Final evidence and gap check before delivery. |

Codex has a native `spawn_agent` orchestration model. Claude and DeepSeek get
the same templates and adapter instructions, but their actual process control
depends on the frontend and installed tools. When a frontend cannot spawn
separate agents directly, the templates still serve as a disciplined role and
round protocol for manual or sequential execution.

## Orchestration Lifecycle

A normal multi-agent run follows this shape:

1. **Classify the request.** Decide whether the task is discussion, review,
   research, proof stress-testing, manuscript review, graph reconfiguration, or
   formalization.
2. **Select a template.** Choose the most specific matching template and state
   why it was chosen.
3. **Show the plan first.** List roles, models or reasoning tiers, round order,
   verification steps, expected artifacts, and time assumptions.
4. **Wait for confirmation.** Multi-agent execution should not start until the
   user confirms the plan.
5. **Spawn bounded role agents.** The orchestrator launches independent roles
   for the current round. Each role gets a narrow prompt, clear output format,
   and no file-write authority unless it owns a specific write target.
6. **Collect round outputs.** The orchestrator waits once per round or critical
   batch, compresses decisive findings, and records the state.
7. **Cross-pollinate only after Round 1.** Later rounds receive a compressed
   summary of the strongest findings, objections, and unresolved claims.
8. **Run independent verification.** Where useful, the orchestrator runs
   SageMath, graph checks, source checks, or local tests instead of trusting
   role opinions alone.
9. **Synthesize locally or with a referee.** The final answer separates
   accepted, rejected, unresolved, and unverified claims.
10. **Close or recover agents.** Completed role agents are closed. Interrupted
    runs resume from state rather than rerunning completed rounds.

## Spawn And Round Handling

For Codex-style execution, the mapping is:

| Concept | Process |
|---|---|
| Launch role | `spawn_agent` with a concrete role prompt. |
| Launch parallel roles | Multiple independent `spawn_agent` calls in the same round. |
| Continue a role | `send_input` with compressed prior findings. |
| Wait for outputs | `wait_agent` once per round or per critical batch. |
| Recover interrupted role | `resume_agent` when a prior agent must continue. |
| Finish role | `close_agent` after the role is no longer needed. |
| External verification | Orchestrator runs local tools directly, then feeds verified facts into synthesis. |

Role prompts should include:

- template and role name
- exact task or claim
- round number and round-specific instructions
- prior-round summary when applicable
- required output format
- tool permissions and write boundaries
- hard rules for evidence, uncertainty, and fatal gaps

## Available Templates

| Template | Best for | Default shape |
|---|---|---|
| Lakatos Proof and Refutation | Stress-testing a theorem or proof draft. | 4 roles, 3 rounds, debate. |
| Polya Multi-Strategy Problem Solving | Exploring an open problem or complexity boundary. | 3 roles, 3 rounds, star topology. |
| Knuth Structured Manuscript Review | Reviewing a mathematical paper draft. | 3 roles, 2 rounds, panel synthesis. |
| Structured Research Team | General high-stakes claim, proof, algorithm, or characterization review. | 4 roles, 3 rounds plus optional repair. |
| Graph Reconfiguration Specialist | Token sliding, token jumping, gadgets, reductions, PSPACE/NP-hardness, graph-class preservation. | 4 roles, 3 rounds plus optional repair. |
| Lean Formalization Team | Turning a proved lemma into a Lean scaffold or debugging a formal proof. | 5 roles, 2 rounds. |
| Prose / OpenProse-style workflow | Reproducible decomposition with explicit tracks and artifacts. | Variable tracks, parallel where independent. |

Template chaining is allowed when the task naturally has phases. For example,
a graph reconfiguration reduction can use Graph Reconfiguration Specialist
first, then Knuth Structured Manuscript Review after the proof is stable.

## Example: Graph Theory Proof Stress-Test

User request:

```text
Use a multi-agent panel to stress-test my proof that every graph in class C has
property P under token sliding.
```

Likely process:

1. Select **Lakatos Proof and Refutation** if the main goal is proof attack, or
   **Graph Reconfiguration Specialist** if gadgets and state graphs are central.
2. Show a plan with Prover or Constructor, Counterexample Hunter or Adversary,
   Monster-Barrer or Auditor, and Formalist or Referee.
3. Spawn independent Round 1 role agents.
4. Let the counterexample role use SageMath or graph checks when a finite search
   is meaningful.
5. Run Round 2 with compressed objections and proposed repairs.
6. Return a ledger of accepted, rejected, unresolved, and weakened claims.

Typical final output:

- strongest surviving theorem statement
- proof steps that survived
- hidden assumptions found
- smallest counterexample candidates, if any
- verification limits
- recommended next proof repair

## Example: Graph Reconfiguration Reduction Audit

User request:

```text
Check whether this PSPACE-hardness reduction for token jumping is sound.
```

Likely process:

1. Select **Graph Reconfiguration Specialist**.
2. Split the work into Constructor, Adversary, Auditor, and Referee.
3. Track separate claims for local gadget behavior, soundness, completeness,
   noninterference, graph-class preservation, and polynomial size.
4. Run local verification for small gadgets when possible.
5. Stop defending the original proof if a decisive counterexample is found.

The important distinction is that prose polishing does not happen until the
construction is stable. Correctness comes first.

## Example: Mathematical Manuscript Review

User request:

```text
Run a multi-agent review of this draft before submission.
```

Likely process:

1. Select **Knuth Structured Manuscript Review**.
2. Spawn Correctness Reviewer, Exposition Reviewer, and Literature Reviewer.
3. Ask each reviewer for section-level findings with severity and concrete
   fixes.
4. Merge overlaps into one prioritized action list.

Typical final output:

- critical correctness issues
- significant exposition problems
- missing or questionable citations
- minor issues
- optional cosmetic suggestions

## Example: Open Problem Exploration

User request:

```text
Use multiple agents to explore whether this graph problem is likely fixed-
parameter tractable or hard.
```

Likely process:

1. Select **Polya Multi-Strategy Problem Solving**.
2. Spawn Specializer, Generalizer, and Reducer.
3. Specializer studies restricted cases and small examples.
4. Generalizer searches for known techniques and neighboring dichotomies.
5. Reducer proposes plausible hardness sources and gadget outlines.
6. The final synthesis ranks approaches by promise and expected difficulty.

## Example: Lean Formalization Handoff

User request:

```text
Use a formalization team to turn this lemma into a Lean skeleton.
```

Likely process:

1. Select **Lean Formalization Team**.
2. Spawn Informal Planner, Formalizer, and Missing-Lemma Miner in Round 1.
3. Spawn Repair Agent and Checker in Round 2.
4. Separate mathematical gaps from formalization friction.

The output should say whether the skeleton is complete, blocked by missing
lemmas, or revealing a real gap in the informal proof.

## When To Prefer Prose

Use `prose` instead of `agent-group-discuss` when the user asks for a more
reproducible workflow, explicit tracks, or a reusable process. Good examples:

- source gathering plus independent verification plus synthesis
- comparing two approaches with separate advocates
- producing durable intermediate artifacts
- decomposing a long research task into named phases

`prose` is still an adapter here. It describes the workflow and maps it to the
available agent tools; it is not a bundled OpenProse virtual machine.
"""


def skill_table(manifests: dict[str, Any]) -> str:
    rows = ["| Skill | Description | Profiles |", "|---|---|---|"]
    for name, spec in sorted(manifests["skills"]["skills"].items()):
        profiles = ", ".join(f"`{p}`" for p in spec.get("profiles", []))
        rows.append(f"| `{name}` | {spec['description']} | {profiles} |")
    return "\n".join(rows)


def artifact_table(manifests: dict[str, Any]) -> str:
    rows = ["| Artifact | Description | Depends On Skills |", "|---|---|---|"]
    for artifact_type, specs in sorted(manifests["artifacts"]["artifacts"].items()):
        for name, spec in sorted(specs.items()):
            dependencies = ", ".join(f"`{item}`" for item in spec.get("depends_on_skills", [])) or ""
            rows.append(f"| `{artifact_type}:{name}` | {spec['description']} | {dependencies} |")
    return "\n".join(rows)


def artifact_profiles_table_text(manifests: dict[str, Any]) -> str:
    rows = ["| Artifact Profile | Description | Artifacts |", "|---|---|---|"]
    for name, spec in sorted(manifests["artifacts"]["artifact_profiles"].items()):
        artifacts = ", ".join(f"`{item}`" for item in spec["artifacts"])
        rows.append(f"| `{name}` | {spec['description']} | {artifacts} |")
    return "\n".join(rows)


def profiles_table_text(manifests: dict[str, Any]) -> str:
    rows = ["| Profile | Description | Skills |", "|---|---|---|"]
    for name, spec in sorted(manifests["profiles"]["profiles"].items()):
        skills = ", ".join(f"`{s}`" for s in spec["skills"])
        rows.append(f"| `{name}` | {spec['description']} | {skills} |")
    return "\n".join(rows)


def md_cell(value: str) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def write_static_doc(path: Path, text: str) -> Path:
    path.write_text(text.strip() + "\n", encoding="utf-8")
    return path


def architecture_text() -> str:
    return """# Architecture

The manifests are the source of truth. The installer resolves canonical skills
to per-agent target artifacts and records ownership in a journal. Existing
unmanaged files are skipped by default.

Artifact classes:

| Artifact class | Current behavior |
|---|---|
| `skill-file` | Installs canonical `SKILL.md` into the agent skill directory. |
| `skill-support-file` | Installs canonical references, scripts, assets, templates, and agent notes inside the skill directory. |
| `instruction-block` | Adds or updates a managed block in `AGENTS.md` or `CLAUDE.md` only when the matching skill artifact is installed, adopted, updated, or migrated. |
| `agent-persona` | Optional reviewer/persona files. Codex receives TOML custom agents, Claude receives Markdown subagents, and DeepSeek receives reference prompts. |
| `template` | Optional research, report, specification, and task templates. |
| `instruction-doc` | Optional workflow reference documents installed outside skill folders. |
| `entrypoint-alias` | Optional quick-action aliases. Claude receives command files; Codex and DeepSeek receive reference documents. |
| `command` | Reserved optional target class for direct command wrappers. |
| `tool-shim` | Reserved optional target class for DeepSeek or runtime helper tools. |

Codex user-level skills target `~/.codex/skills` in this setup. The optional
`.agents/skills` layout is treated as a compatibility or workspace target when
detected, not as the default global Codex target.
"""


def installation_text() -> str:
    return """# Installation

Use `make precheck` or `make.bat precheck` first when installing on a new
machine. Use `plan` before `install`. Partial installs are first-class: select
`--skill`, `--skills`, or `--profile`. Artifact installs are also partial:
select `--artifact`, `--artifacts`, or `--artifact-profile`.

`doctor` is a quick required-tool check. `precheck` is broader: it detects
required tools, optional tools, Python packages, remote-service configuration
placeholders, detected agents, skipped agents, ignored dependencies, and
Windows/WSL substrate information where possible.

Use `precheck --interactive` for a guided one-by-one pass through missing
dependencies. It does not install packages automatically; it shows the install
hint, lets the user skip or ignore a dependency, and tells them to rerun
`precheck` after installing software.

`install --dry-run` previews the same actions as a default install preview;
`install --apply` is required before any writes occur. Real home-directory
writes additionally require `--real-system`.

Conflict modes:

- default: create missing managed files and skip unmanaged or legacy files
- `--adopt`: record an existing target file as user-owned managed state
- `--backup-replace`: back up and replace an unmanaged target file
- `--migrate`: copy a detected legacy skill into the canonical target while
  leaving the legacy source in place

Instruction blocks are installed only when the corresponding skill artifact is
actually installed, adopted, updated, already managed, or migrated. A skipped
skill does not receive an `AGENTS.md` or `CLAUDE.md` block.

Optional artifacts are not installed by default. Use `--no-skills` when you
want an artifact-only install. Use `--with-deps` when selected dependency-bound
artifacts should bring in their required backing skills.

Scenario summary:

| Scenario | Result |
|---|---|
| Agent home absent | Agent is skipped; its dependencies are not required. |
| Skill absent | Managed skill files and support files are created. |
| Skill already managed | Files are updated or left unchanged according to hashes. |
| Skill exists unmanaged | Default plan skips it; use `--adopt` or `--backup-replace` explicitly. |
| Legacy alias exists | Default plan skips; `--migrate` copies canonical content under the canonical name. |
| Dependency-bound artifact selected without dependency | Artifact is blocked and skipped until the backing skill is managed or selected with `--with-deps`. |
| Persona selected | Codex gets TOML, Claude gets Markdown frontmatter, DeepSeek gets a reference prompt. |
| Windows SageMath | Prefer WSL-backed detection when native SageMath is absent. |
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
| `python-runtime` | system Python 3.10 with `ssl`, `venv`, and `pip` | native Windows Python can be detected from `C:\\Python3*`, per-user Python installs, Program Files installs, or PATH-style candidates; mounted checks can verify package markers without running `python.exe` | `deep-research-workflow`, `zotero`, `docling`, digest skills, `graph-verifier`, `tikz-draw`, `session-logs` |
| `tex-runtime` | `pdflatex` from TeX Live detected | TeX Live under `C:\\texlive\\*\\bin\\windows` and common MiKTeX roots can be detected as present-unverified from a mounted Windows filesystem | `tikz-draw` |
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
"""


def agent_locations_text() -> str:
    return """# Agent Locations

The installer detects agent homes first. If an agent home is absent, that agent
is skipped and its target-specific files are not planned.

| Agent | Home | Skill target | Instruction file |
|---|---|---|---|
| Codex | `~/.codex` | `~/.codex/skills/<skill>/` | `~/.codex/AGENTS.md` |
| Claude | `~/.claude` | `~/.claude/skills/<skill>/` | `~/.claude/CLAUDE.md` |
| DeepSeek | `~/.deepseek` | `~/.deepseek/skills/<skill>/` | `~/.deepseek/AGENTS.md` |

Optional or compatibility skill locations:

| Agent | Optional location | Meaning |
|---|---|---|
| Codex | `~/.agents/skills` | Optional workspace/local target where supported; not the default global target. |
| DeepSeek | `~/.agents/skills`, `./skills` | Workspace-local locations that may shadow global DeepSeek skills. |

Optional artifact-class target directories:

| Agent | Personas | Templates | Commands | Tool shims |
|---|---|---|---|---|
| Codex | `~/.codex/agents` | `~/.codex/templates` | `~/.codex/commands` | `~/.codex/tools` |
| Claude | `~/.claude/agents` | `~/.claude/templates` | `~/.claude/commands` | `~/.claude/tools` |
| DeepSeek | `~/.deepseek/agents` | `~/.deepseek/templates` | `~/.deepseek/commands` | `~/.deepseek/tools` |

Instruction docs target each agent's `instructions` directory. Entrypoint
aliases target Claude commands, but Codex and DeepSeek receive reference docs
under `instructions/entrypoints` because equivalent slash-command loading is
not assumed.

These optional artifact classes are intentionally not installed by default.
They require explicit artifact selection because commands, personas, hooks, and
tool shims can affect behavior more broadly than a normal skill directory.

Instruction files are modified through managed marker blocks only. Uninstall
and rollback remove only those managed blocks and managed files.
"""


def windows_text() -> str:
    return """# Windows

Windows is multi-substrate. Native Windows, PowerShell/CMD, Git Bash/MSYS, WSL,
and remote services are checked separately. SageMath is usually WSL-backed and
must not be treated as a normal Windows package.

Use `make.bat precheck` before installation. The precheck reports whether each
dependency is native Windows, WSL-backed, missing, degraded, or manual. A
missing DeepSeek home on Windows is not an error; DeepSeek-specific artifacts
and dependencies are skipped when the agent is absent.

For WSL-backed tools, the relevant check is whether `wsl.exe` exists and the
command is available inside the default WSL distro. For example, `sage-runtime`
may be satisfied by `sage` inside WSL even if no native Windows `sage.exe`
exists.

When a Windows profile is inspected from Linux through a mounted drive,
`precheck` also looks for official or common native install locations such as
`C:\\Python3*`, per-user Python installs, `C:\\texlive\\*\\bin\\windows`, and
MiKTeX roots. For SageMath, it checks current local WSL/Linux paths first when
the precheck itself is running from that substrate, then mounted WSL rootfs
locations when they exist. If only a WSL distro `ext4.vhdx` is visible, the
result is degraded: the distro exists, but Sage inside the image cannot be
verified without WSL, a local WSL filesystem, or a mounted rootfs.
"""


def linux_text() -> str:
    return """# Linux

Linux checks resolve logical tools from installed commands, repo-local runtimes,
and user overrides such as `AAS_PYTHON` or `AAS_SAGE`. `precheck` also checks
selected optional Python packages where a skill declares them.
"""


def troubleshooting_text() -> str:
    return """# Troubleshooting

Run `precheck --json` to inspect detected agents, selected tools, optional
packages, skipped agents, missing required dependencies, and degraded optional
capabilities. Use `plan` to preview every file change.

If a plan reports `classification=unmanaged`, the installer found user-owned
content in the target path and will skip it unless `--adopt` or
`--backup-replace` is used. If a plan reports `classification=legacy`, the
installer found a compatibility or alias path and will skip it unless
`--migrate` is used.
"""


def uninstall_text() -> str:
    return """# Uninstall And Rollback

`rollback` restores previous managed state from a recorded run. `uninstall`
removes current managed artifacts. Both support skill and agent scopes and both
support dry-run previews.

Applied uninstall requires an explicit scope: use `--skill`, `--skills`, or
`--artifact`, `--artifacts`, or `--all`. Uninstall removes only managed files
and managed instruction blocks. Rollback can target one run, one skill,
multiple skills, one artifact, multiple artifacts, or one agent. If a managed
instruction file was created by the installer and becomes empty after block
removal, it is removed.
"""
