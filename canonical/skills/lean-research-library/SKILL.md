---
name: lean-research-library
description: Use when any Lean formalization task starts (reuse Mathlib and the personal research library before proving anything new) and when it ends (gate finished results into the library and flag mathlib-PR candidates, always asking the user first). Also scaffolds and publishes paper artifacts from the personal template.
metadata:
  short-description: Personal Lean library reuse, intake gate, and paper artifacts
---

# Lean Research Library

Steward for the user's personal mathlib-style staging library
(`HoangMathLib`) and paper-artifact template. Propose-only by design: this
skill never commits, pushes, opens PRs, or publishes — those remain user
actions behind explicit approval gates.

## Hard contract (applies on every install target)

1. **Before any Lean formalization** — whatever skill, plugin, template, or
   agent lane starts it — run `search` for each target statement. Precedence
   is normative: **mathlib hit → use mathlib, stop; else library hit → use the
   library; else peer-satellite hit → cite it and decide; else formalize
   new.** A `statement_only` hit (e.g. FormalConjectures) is a sorry'd
   statement, never a reusable proof.
2. **After a formalization result is accepted** — run `intake` on the new
   file. A declaration is a library candidate **only if** it is (i) absent
   from mathlib AND the library (search-verified) and (ii) useful beyond the
   immediate task; everything else stays where it was produced. Present the
   proposal packet with a usefulness justification per candidate and **ask
   before any `stage --apply`**. Never write to the library without that
   approval. For paper-artifact campaigns, this intake pass runs **once,
   after the full formalization is complete**, not per result.
3. **Two actions are always user-gated**, even inside autonomous loops:
   staging into the library, and anything outward-facing (repo creation,
   pushes, Zenodo publishing). Autonomous runs batch these gates at run
   boundaries; nothing is auto-published mid-loop.

## Configuration

Resolution order: `AAS_LEAN_LIBRARY_ROOT` env var → config file
(`${XDG_CONFIG_HOME:-~/.config}/lean-research-library/config.json`; on
Windows `%APPDATA%\lean-research-library\config.json`). Keys:
`library_root`, `library_module` (default `HoangMathLib`), `template_root`
(clone of `lean-paper-artifact-template`), `peer_satellites` (list of local
checkout paths, e.g. cam-combi, add-combi), `closed_deps` (bool: restrict to
Lean core + mathlib + the library; disables the peer-satellite tier — use
for closed-dependency formalization pipelines).

Run `doctor` on a fresh machine: it prints exact clone commands and the
config to write (first-run bootstrap). Missing Lean is a reported status,
never a failure; the skill installs nothing.

## Verbs

| Verb | Network | Purpose |
|---|---|---|
| `doctor [--ecosystem]` | no / marked | Tools, config, first-run bootstrap; `--ecosystem` re-checks latest stable mathlib vs pin and search-endpoint liveness (drift detection). |
| `search --query Q [--offline] [--with-leansearch]` | marked | Bucketed reuse check: `{mathlib, library, peer_satellite, elsewhere}` + recommendation per the precedence rule. Backends: library grep + `decls-index.jsonl`, peer-satellite greps, Loogle (with quoted-form retry), LeanStateSearch; LeanSearch off by default. Endpoints env-overridable (`AAS_LOOGLE_URL`, …) — pointing `AAS_LOOGLE_URL` at a self-hosted loogle (`server.py --project-dir <library>`) gives type-pattern search over the personal library itself. |
| `status` | no | Library pin, staging readiness (sorry-free + import discipline per file), research sorry census. |
| `intake --file F` | no | The ask-the-user gate: proposal packet per declaration + a lean-strict-verification-gate packet (typechecking ≠ claim support). Writes nothing. |
| `stage --file F --target T [--apply]` | no | Dry-run by default. Two targets: `Mathlib/A/B/C.lean` (staging mirror; validates sorry-freedom, import discipline, pinned-mathlib file form, offers header scaffold) or research dirs (e.g. `Reconfig/X.lean`). `--apply` only after user approval; commits stay with the user. |
| `prepare-upstream --file F` | no | Port-to-master packet: rewritten imports (`<Lib>.Mathlib.X → Mathlib.X`), mathlib PR checklist (header form, fix_deprecations, title conventions, AI disclosure). |
| `bump [--to TAG] [--apply]` | marked | Stable-ladder bump: dry-run shows the ladder + checklist; `--apply` edits `lean-toolchain` + lakefile rev only, then hands the lake commands to the user. |
| `audit [--run-gate]` | no | Staging violations + the deterministic landed-in-mathlib gate. Default prints the exact commands; `--run-gate` executes them when lake and a built library are available (minutes; imports the full mathlib closure). Nonzero gate exit = delete or rename the staged copy. |
| `artifact new --paper SLUG --dir D [--library-rev SHA]` | no | Scaffold from `template_root` (full verification ladder) or an embedded minimal fallback; proposes — never runs — `gh repo create`/push and the per-paper library tag. |
| `artifact publish --dir D [--mode github-sync\|api]` | marked | Zenodo: `github-sync` prints the verified checklist; `api` targets **sandbox by default**, needs `ZENODO_TOKEN`, and **refuses `--production` without `--confirm-production`** (a published DOI cannot be deleted). Prechecks block on leftover `<PLACEHOLDER>`s. Uploads/publish stay gated. |

## Paper→formalization pipeline (with autonomous-research-loop)

For "formalize this paper end to end": intake the paper's claims
(`lean-formalization-intake`), skeleton statements
(`formal-skeleton-helper`), then drive proof work with
`autonomous-research-loop` using `formal_policy: force` and this skill wired
at F2′ (search-first) and F7′ (intake-after). Set `closed_deps: true` for a
core+mathlib+library-only run. The loop ends in exactly one of two states:
a sorry-free artifact scaffolded by `artifact new`, or an honest ledger of
open statements — `lean-strict-verification-gate` decides which, never the
loop itself. Heavy proof iteration needs a mathlib toolchain: run where one
exists or route through the compute-lane skills.

## Windows Runtime Commands

Runtime helpers live under the shared runtime root. From PowerShell:

```powershell
$runtime = if ($env:AAS_RUNTIME_ROOT) { $env:AAS_RUNTIME_ROOT } else { "$env:LOCALAPPDATA\ai-agents-skills\runtime" }
& "$runtime\run_skill.ps1" "skills/lean-research-library/run_lean_research_library.ps1" doctor
```

or directly from PowerShell: `skills/lean-research-library/run_lean_research_library.ps1`
and `skills/lean-research-library/run_lean_research_library.ps1` (both
resolve Python via `run_python.ps1`/`AAS_RUNTIME_PYTHON`). On POSIX:

```bash
bash "${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh" skills/lean-research-library/run_lean_research_library.sh doctor
```

## Recommended templates

- `informal-to-lean-formalization-runbook` — the F1–F7 formal lane this
  skill's F2′/F7′ gates extend.
