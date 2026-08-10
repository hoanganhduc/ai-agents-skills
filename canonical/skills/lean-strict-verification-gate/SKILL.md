---
name: lean-strict-verification-gate
description: Use when checking whether a Lean artifact can safely support a research claim.
---

# Lean Strict Verification Gate

## Windows Runtime Commands

On native Windows, use the managed Windows runner and the native runtime command target. Set `$runtime` to the installed runtime root. Multi-agent installs usually use `%LOCALAPPDATA%\ai-agents-skills\runtime`. Then run:

```powershell
$runtime = if ($env:AAS_RUNTIME_ROOT) { $env:AAS_RUNTIME_ROOT } else { "$env:LOCALAPPDATA\ai-agents-skills\runtime" }
& "$runtime\run_skill.ps1" "skills/lean-strict-verification-gate/run_lean_strict_verification_gate.ps1" doctor
```

PowerShell runner target:

```powershell
& "$runtime\run_skill.ps1" "skills/lean-strict-verification-gate/run_lean_strict_verification_gate.ps1" doctor
```

POSIX examples below use `run_skill.sh` and `.sh` command targets; use the Windows command target above on native Windows.

Use this skill to prevent overclaiming from generated Lean, skeletons, partial formalizations, or checker output. It separates:

- syntactic/safety scan
- placeholder and trust-base status
- optional local Lean typecheck
- statement-equivalence review, which remains a human/lead review step

## Runtime Helper

Check the local tool status:

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" \
  skills/lean-strict-verification-gate/run_lean_strict_verification_gate.sh doctor
```

Run non-installing version/toolchain probes when you need reproducibility
metadata:

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" \
  skills/lean-strict-verification-gate/run_lean_strict_verification_gate.sh doctor --probe
```

Scan a Lean file without running Lean:

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" \
  skills/lean-strict-verification-gate/run_lean_strict_verification_gate.sh scan \
  --input formal/final/proof.lean \
  --artifact-stage final_candidate
```

Optionally typecheck only when Lean is already installed:

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" \
  skills/lean-strict-verification-gate/run_lean_strict_verification_gate.sh verify \
  --input formal/final/proof.lean \
  --artifact-stage final_candidate \
  --typecheck
```

For a user-managed Lake workspace, use the explicit Lake environment runner.
The helper requires a project root containing `lakefile.lean` or
`lakefile.toml`, records the project context, and still runs the scanner before
typechecking:

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" \
  skills/lean-strict-verification-gate/run_lean_strict_verification_gate.sh verify \
  --input formal/final/proof.lean \
  --artifact-stage final_candidate \
  --typecheck \
  --runner lake-env-lean \
  --project-root /path/to/lean/project
```

Set `AAS_LEAN` or `AAS_LAKE` to select a specific already-installed local
executable. Invalid explicit paths fail closed instead of silently using a
different tool.

## Trust base and kernel replay

The scanner reads source text, so it cannot see what a compiled proof actually
rests on: a `sorryAx` reached through an imported dependency leaves no `sorry`
token in the file being scanned. Two verbs close that gap over an
already-built Lake project. Both are read-only and neither writes into the
project.

`axiom-audit` reports the trust base. It discovers the project's theorems and
lemmas, runs `#print axioms` on each through `lake env lean`, and classifies
every dependency against the sanctioned trio (`propext`, `Classical.choice`,
`Quot.sound`). `--allow-axiom NAME` widens that set for a project that
knowingly depends on a further axiom; `sorryAx` is refused ahead of the
allowlist and can never be sanctioned. A declaration the built environment
does not have is reported as `unresolved` rather than passing silently.

`native_decide` reports `Lean.ofReduceBool` and `Lean.trustCompiler`, which
mark a complete proof that rests on the compiler and the native runtime rather
than on the kernel alone. A project may knowingly accept that, so both stay
allowlistable, but allowlisting them can never make the proof read as
kernel-checked: the declaration is reported as `sanctioned_compiler_trust`
rather than `sanctioned`, the axioms are listed in `compiler_trust_axioms`, and
the payload gains a matching limitation. The autonomous research loop passes no
allowlist, so a `native_decide` proof is refused there.

Declaration discovery is a line walk, not a Lean parser. It covers `theorem`
and `lemma` under `namespace`, `section` (including `noncomputable section`),
and `mutual` scopes, through
attributes, a same-line command prefix such as `open … in`, `set_option … in`,
`attribute … in`, or `variable … in`, and modifiers such as `noncomputable` and
`nonrec`. Definitions (`def`, `abbrev`, `instance`) are out of scope by
design — a theorem that uses one inherits its axioms, so an unsound definition
still surfaces through the theorem depending on it — and `example` has no name
`#print axioms` could be asked about.

A line that names `theorem` or `lemma` but that the walk cannot read a name off
fails the audit rather than being skipped: it is listed in
`declarations_unparsed` with a `declaration_unparsed` finding and `ok: false`.
Silent non-coverage is the one outcome the audit cannot survive, since it would
report a clean trust base over a scan that missed a proof. Pass `--declaration`
to name the targets explicitly when a project trips this.

`private` theorems are discovered but not audited: Lean mangles the name, so an
importing harness cannot ask about one and asking anyway would fail the audit
on `unresolved` for every project that has a private lemma. They are listed in
`declarations_skipped_private` with a matching limitation, never dropped
silently. The transitivity argument covers them as it covers definitions —
nothing outside their module can cite one, and a public theorem that uses one
inherits its axioms.

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" \
  skills/lean-strict-verification-gate/run_lean_strict_verification_gate.sh axiom-audit \
  --input /path/to/lean/project \
  --strict
```

`kernel-check` replays the compiled environment through `lean4checker`, which
re-checks proof terms against the kernel instead of trusting the build cache.
It requires `lean4checker` on `PATH`, in `AAS_LEAN4CHECKER`, or built into the
project's `.lake/build/bin`.

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" \
  skills/lean-strict-verification-gate/run_lean_strict_verification_gate.sh kernel-check \
  --input /path/to/lean/project \
  --strict
```

Both verbs work on compiled modules, so both derive their targets from what
Lake actually built (`.lake/build/lib`) rather than from the `.lean` files
lying under the project root. A source Lake never built is listed in
`modules_skipped_unbuilt` and its declarations go unaudited — without that
rule a staged proof artifact inside the project, which is exactly what the
research loop writes, would enter the harness as a module name and abort the
run with `unknown module prefix` before producing any evidence. A project with
no compiled modules at all reports `project_not_built`. Pass `--import` /
`--module` (and `--declaration`) to name targets yourself and bypass the
derivation.

For both verbs `--strict` means an audit that never ran is a failure. Without
it, a missing toolchain reports `tool_unavailable` and exits 0, which callers
must treat as "no evidence", never as a clean trust base.

`axiom-audit` reports what the proofs depend on, not whether the Lean
statement matches the informal claim; statement equivalence stays a review
step.

The helper never installs Lean, Lake, mathlib, npm packages, Python packages, credentials, services, or MCP servers. Missing Lean reports `tool_unavailable`.

## Blocking Policy

Before any typecheck, the scanner blocks active:

- `#eval`
- `IO.Process`
- `run_cmd`
- `unsafe`
- `initialize`
- `@[extern]`
- foreign/FFI import patterns
- non-allowlisted imports unless explicitly passed with `--allow-import`
- Lake/package files unless explicitly reviewed outside this helper

Final or claim-supporting artifacts also block on active `sorry`, `admit`, unsanctioned `axiom`, unknown trust base, or unreviewed generated proof text. Stubs may contain placeholders only when explicitly marked `artifact_stage = stub`.

## Recommended templates

When this skill is involved, consider these workflow templates (install via
the `workflow-templates` artifact profile, or `--with-deps` to pull backing skills):

- `informal-to-lean-formalization-runbook` -- Local-first intake mapping an informal proof to Lean declarations with a scanner-first verification gate separating typecheck status from claim support.
