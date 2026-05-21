# Lean Repo Audit Checklist

Use this static checklist before any Lean build, AXLE call, verifier run,
notebook execution, remote scan, or training workflow.

Runtime behavior: incomplete analysis.

## Scope

- local repository path:
- source id:
- expected repo family:
- auditor:
- date:
- limitations:

Remote scanning is out of scope by default. Do not clone, fetch from APIs, use
credentials, or call network endpoints unless a later opt-in phase explicitly
adds an allowlist and redaction policy.

## Repository Metadata

- repo type:
- Lean version:
- `lean-toolchain` state:
- `.environment` state:
- Lake package manager state:
- `lakefile.toml` or `lakefile.lean` state:
- `lake-manifest.json` state:
- Mathlib revision:
- lockfile state:

## Source Materials

- TeX files:
- Markdown files:
- PDF files:
- `problem.lean` files:
- `solution.lean` files:
- `task.md`:
- `requirement.md`:
- sidecar Python/Sage/notebook files:

## Provenance And License

- source URL:
- credential-free provenance URL rule:
- upstream owner:
- license file:
- license identifier:
- GPL-3.0-or-later compatibility conclusion:
- license uncertainty status:
- copied code or templates:
- external-code-derived content:

## CI And Commands

- CI files:
- test commands, not executed:
- build commands, not executed:
- docs commands, not executed:
- candidate safe commands:
- publish/deploy/destructive commands under `do_not_run_commands`:

## Secrets And Local State

- secrets:
- `.env*` files:
- credential surfaces:
- endpoint/share URLs:
- private URLs:
- local config paths:
- runtime caches:
- databases:
- reports:
- archives:
- downloaded documents:

## Compute Risk

- compute risk:
- notebooks:
- training scripts:
- GPU references:
- benchmark/performance claims:
- remote compute references:
- risk level:

## Static Soundness Signals

These are advisory only and do not prove or disprove validity.

- `sorry`:
- `axiom`:
- `native_decide`:
- `implemented_by`:
- FFI or `@[extern]`:
- opaque declarations:
- oracle-style surfaces:

## Decision

- verdict: PASS / FLAG / BLOCK
- required follow-up:
- evidence:
- limitations:
