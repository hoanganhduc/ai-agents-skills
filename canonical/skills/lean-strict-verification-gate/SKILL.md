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
bash "${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh" \
  skills/lean-strict-verification-gate/run_lean_strict_verification_gate.sh doctor
```

Run non-installing version/toolchain probes when you need reproducibility
metadata:

```bash
bash "${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh" \
  skills/lean-strict-verification-gate/run_lean_strict_verification_gate.sh doctor --probe
```

Scan a Lean file without running Lean:

```bash
bash "${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh" \
  skills/lean-strict-verification-gate/run_lean_strict_verification_gate.sh scan \
  --input formal/final/proof.lean \
  --artifact-stage final_candidate
```

Optionally typecheck only when Lean is already installed:

```bash
bash "${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh" \
  skills/lean-strict-verification-gate/run_lean_strict_verification_gate.sh verify \
  --input formal/final/proof.lean \
  --artifact-stage final_candidate \
  --typecheck
```

For a user-managed Lake workspace, use the explicit Lake environment runner.
The helper requires a project root containing `lakefile.lean` or
`lakefile.toml`, records the project context, and still runs the scanner before
typechecking. Verification hashes the scanned source and the Lake configuration
(`lakefile.*`, `lean-toolchain`, and `lake-manifest.json`) before and after the
command; a changed input or project context refuses the result:

A directory input selects `lake-build`. After Lake returns success, the helper
also requires every local source module to have a non-stale compiled artifact;
a narrow or no-op default build therefore cannot certify the whole project.

```bash
bash "${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh" \
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

Native reduction adds a compiler-trust axiom. Older toolchains may report fixed
names such as `Lean.ofReduceBool`, `Lean.ofReduceNat`, or
`Lean.trustCompiler`; Lean 4.33's `decide` family emits declaration-local names
such as `t._native.decide.ax_1_1`. A project may knowingly accept one, so these
names stay allowlistable, but allowlisting them can never make the proof read as
kernel-checked: the declaration is reported as `sanctioned_compiler_trust`
rather than `sanctioned`,
the axioms are listed in `compiler_trust_axioms`, and the payload gains a
matching limitation. The autonomous research loop passes no allowlist, so a
native-reduction proof is refused there.

The source scanner also refuses the equivalent `decide +native` and
`decide (native := true)` forms, plus direct uses of `trustCompiler`,
`reduceBool`, `reduceNat`, `ofReduceBool`, and `ofReduceNat`. These spellings are
grounded in Lean 4.33's `DecideConfig` and compiler-trust declarations; they are
trust-base expansions even when the token `native_decide` does not occur.
The scanner conservatively treats braces inside any quoted string as possible
interpolation escapes because Lean's parser aliases are extensible. Terms in
those braces remain active during scanning, while other literal string text
stays inert; an ordinary string containing brace-delimited code-like text may
therefore be refused as a false positive.

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
bash "${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh" \
  skills/lean-strict-verification-gate/run_lean_strict_verification_gate.sh axiom-audit \
  --input /path/to/lean/project \
  --strict
```

`kernel-check` replays the compiled environment through `lean4checker`, which
re-checks proof terms against the kernel instead of trusting the build cache.
It requires `lean4checker` on `PATH` or explicitly selected with
`AAS_LEAN4CHECKER`. The gate never auto-selects the project's
`.lake/build/bin/lean4checker`: an artifact under review must not silently
supply its own certifying checker. An explicitly selected checker inside the
project root remains allowed, but the payload marks it as project-controlled
rather than independent.

```bash
bash "${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh" \
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

For local modules, both verbs hash the exact source bytes, `.olean` bytes, and
Lake configuration before and after invoking the checker. They refuse
symlinked evidence, unreadable or silently skipped subdirectories, mutation
visible in the post-command snapshot, and a source whose modification time is
newer than its compiled module. Individual source/config reads are capped at
64 MiB and an individual compiled module at 512 MiB; a source or build-output
tree walk is capped at 100,000 entries. The two snapshots cannot detect a
change-and-restore between reads, and the source-to-`.olean` freshness
check is an mtime preflight rather than a reproducible-build proof. Explicit
dependency modules outside the project root are resolved by Lake and are not
content-hashed by this helper.

Each child command has a 16 MiB combined stdout/stderr budget. Kernel replay
shares that budget across all requested modules. Exceeding it fails closed as
`command_failed`; an axiom report truncated by the cap is never parsed as
evidence.

Lean, Lake, `lean4checker`, their launchers, the process environment, and Lake's
dependency resolution are trusted execution inputs. The payload reports tool
paths, but this helper does not content-attest those executables or the external
dependency closure.

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
- early-exit and expected-failure wrappers (`#exit`, `#check_failure`, and
  `#guard_msgs`)
- `IO.Process`
- `run_cmd`
- `run_tac`
- inline or registered custom elaborators (`elab`, `elab_rules`, `by_elab`,
  and elaborator attributes), because Lean elaboration monads can lift host IO
- `unsafe`
- `initialize` / `builtin_initialize`
- `@[extern]`
- foreign/FFI import patterns
- non-allowlisted imports unless explicitly passed with `--allow-import`
- compiler-trusting native reduction (`native_decide`, `decide +native`, and
  `decide (native := true)`)
- Lake/package files unless explicitly reviewed outside this helper

Final or claim-supporting artifacts also block on active `sorry`, `admit`, unsanctioned `axiom`, unknown trust base, or unreviewed generated proof text. Stubs may contain placeholders only when explicitly marked `artifact_stage = stub`.

## Recommended templates

When this skill is involved, consider these workflow templates (install via
the `workflow-templates` artifact profile, or `--with-deps` to pull backing skills):

- `informal-to-lean-formalization-runbook` -- Local-first intake mapping an informal proof to Lean declarations with a scanner-first verification gate separating typecheck status from claim support.
