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

Both forms also report whether this host can run `lake comparator`; see
Comparator readiness below.

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
already-built Lake project. Neither writes its own output into the project,
but both run `lake env` in the project root, and Lake may then create
`lake-manifest.json`, or its configuration cache under `.lake`, when they are
missing (observed with Lean v4.35.0).

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

`kernel-check` replays the compiled environment through a kernel checker,
which re-checks proof terms against the kernel instead of trusting the build
cache. The gate takes the first checker it finds, in this order:

1. the executable `AAS_LEAN4CHECKER` selects explicitly;
2. `lean4checker` on `PATH`;
3. the `leanchecker` of the toolchain that the selected `lake` runs, which
   Lean toolchains ship from v4.28. `kernel-check` runs `lake env` once in the
   project root, within the `--timeout` budget and the output cap, reads the
   `LEAN_SYSROOT` it prints, and takes `<LEAN_SYSROOT>/bin/leanchecker` when
   that is a regular file whose real path lies outside the project root.
   It runs this lookup even when it then finds no checker.
   It never takes the file beside `lake`: under elan that file is a proxy that
   follows the project's `lean-toolchain`.

When `lake env` fails, times out or exceeds the output cap, when it prints no
single absolute `LEAN_SYSROOT`, or when that toolchain has no `leanchecker`
(Lean before v4.28), no replay runs. The result is `tool_unavailable`, as for
any missing checker, and `tool_status.lean4checker.detail` names the reason.

`tool_status.lean4checker` reports the choice: `source` is `env`, `path`,
`toolchain` or `not-found`, and a toolchain checker also carries
`name: leanchecker`. `doctor` never runs `lake env`, because that loads the
project's Lake configuration. When neither `AAS_LEAN4CHECKER` nor `PATH` gives
a checker, `doctor` reports `not-found` with a `detail` saying that
`kernel-check` resolves the toolchain's `leanchecker` through `lake env` at
run time. The gate never auto-selects a checker inside the project root, such
as the project's `.lake/build/bin/lean4checker` or a `leanchecker` under a
`LEAN_SYSROOT` inside it: an artifact under review must not silently supply
its own certifying checker. An explicitly selected checker inside the project
root remains allowed, but the payload marks it as project-controlled rather
than independent.

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

Lean, Lake, `lean4checker` or `leanchecker`, their launchers, the process
environment, and Lake's dependency resolution are trusted execution inputs. The
payload reports tool paths, but this helper does not content-attest those
executables or the external dependency closure.

For both verbs `--strict` means an audit that never ran is a failure. Without
it, a missing toolchain reports `tool_unavailable` and exits 0, which callers
must treat as "no evidence", never as a clean trust base.

`axiom-audit` reports what the proofs depend on, not whether the Lean
statement matches the informal claim; statement equivalence stays a review
step.

The helper never installs Lean, Lake, mathlib, npm packages, Python packages, credentials, services, or MCP servers. Missing Lean reports `tool_unavailable`.

## Comparator readiness

`lake comparator`, in Lean v4.35.0 and later, builds and exports a solution
inside a `bwrap` sandbox and judges it with the same toolchain's `leanexport`
and `leanchecker`. No verb of this gate runs it yet; `doctor` reports under
`comparator` whether this host could:

- `platform_supported`: Linux and not running as root. Any other host gives no
  verdict.
- `bwrap`: the sandbox binary, with `path` (the resolved file), `source`
  (`env`, `local-opt` or `not-found`), `version`, `version_source`
  (`directory` or `probe`), `supported`, `status` and `detail`. Only
  bubblewrap 0.12.0 or later is `supported`, and `status` is `available` only
  then.
- `toolchains`: one row per `$ELAN_HOME/toolchains/leanprover--lean4---*`
  directory, with `name` (such as `leanprover/lean4:v4.35.0`), `version`
  (stable releases only), `supported` and `has_comparator` (`bin/` holds
  `lake`, `lean`, `leanchecker` and `leanexport`). Only a stable release at
  v4.35.0 or later is `supported`; release candidates and nightlies never
  are. A listing that cannot be made gives no rows and `toolchains_error`.

Plain `doctor` reads directory listings and runs nothing, so the `bwrap`
version comes from its `bubblewrap-X.Y.Z` directory name. `doctor --probe`
runs `bwrap --version`, which then decides the version, and for a supported
`bwrap` it adds `sandbox_probe`: one run of
`bwrap --ro-bind / / --unshare-all --die-with-parent -- /bin/true` with a
5-second timeout and `PATH=/usr/bin:/bin` as the whole environment. Its
`status` is `ok` when the sandbox started, `command_failed` when it did not
(for example when unprivileged user namespaces are disabled), and
`tool_unavailable` when nothing ran. An unexpected error in this section is
reported in `comparator.error`; `doctor` still succeeds.

The gate never looks for `bwrap` on `PATH`, where an older distribution
`bwrap` may sit. `AAS_BWRAP` names the binary by absolute path; a relative
name, a missing file or a file that is not executable is refused as
`tool_unavailable`, with no fallback. An empty or blank `AAS_BWRAP` counts as
unset. When `AAS_BWRAP` is unset, the gate takes the highest version among
`~/.local/opt/bubblewrap-X.Y.Z/bin/bwrap`, the layout of a private bubblewrap
build kept off `PATH`; other directory names are ignored.

The toolchain a challenge names, such as `leanprover/lean4:v4.35.0`, is the
directory `$ELAN_HOME/toolchains/leanprover--lean4---v4.35.0`: elan turns `/`
into `--` and `:` into `---`. `ELAN_HOME` defaults to `~/.elan` and must be
absolute. `AAS_LEAN_COMPARATOR_TOOLCHAIN` replaces that directory with an
absolute one; `doctor` lists `$ELAN_HOME/toolchains` only and does not read
it. Either way the directory is refused when it is the input under review or
lies inside it, and its `bin/` must hold `lake`, `lean`, `leanchecker` and
`leanexport` as regular files. The elan proxies in `$ELAN_HOME/bin` are never
used, because a proxy follows the working directory's `lean-toolchain`. The
toolchain attestation hashes every file directly in `bin/`, the bundled
kernels that `--paranoid` runs (`leanchecker-paranoid`, `lean4lean`,
`nanoda_bin`, `con-leche` and `con-ron`) among them, and every
`lib/lean/*.so` (`bin/lean` and `bin/lake` are launchers for those
libraries). A symlink or any other entry of `bin/` that is not a regular file
is refused, and so is a `bin/` with more than 64 entries or a `lib/lean` with
more than 32 libraries: the attestation never truncates. It then reads
`lean --version` in the toolchain directory with only `PATH` and `LANG` set,
so no `LEAN_*`, `ELAN_*` or `LAKE_*` variable can select another Lean.

## Challenge freeze

A Lean proof supports a claim only when a host-run `lake comparator` accepts
it against a trusted challenge. `challenge-freeze` makes that challenge from a
reviewed statement file: it copies the source byte for byte to
`Challenge.lean` in `--out` and writes the record `challenge.json` beside it.
The verb runs no tool, Lean included, and works on every platform, but the
toolchain it names must be installed, because the record pins that
toolchain's bytes.

```bash
bash "${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh" \
  skills/lean-strict-verification-gate/run_lean_strict_verification_gate.sh challenge-freeze \
  --source formal/challenge/Statement.lean \
  --theorem main_claim \
  --out formal/challenge/frozen
```

The record (`aas_lean_challenge.v1`) has a closed key set and no timestamp,
so the same inputs and the same toolchain bytes always give the same bytes:

- `challenge_sha256`: the sha256 of `Challenge.lean`;
- `challenge_module`: `AASChallenge`, the module name the comparator
  workspace gives the frozen copy;
- `theorem_names`: the fully qualified names given with `--theorem`
  (repeatable, at least one);
- `definition_names`: always empty;
- `permitted_axioms`: `propext`, `Classical.choice` and `Quot.sound`, unless
  `--permitted-axiom` (repeatable) names the set instead;
- `paranoid`: `--paranoid` (off by default) asks the comparator to run every
  external kernel bundled with Lean as well;
- `toolchain`: `{"spec": ..., "files": {...}}`. `spec` is `--toolchain`, by
  default `leanprover/lean4:v4.35.0`. `files` maps each file the toolchain
  attestation hashes (see "Comparator readiness") to its sha256, by POSIX path
  relative to the toolchain directory, such as `bin/lake` or
  `lib/lean/libleanshared.so`, as read from the installed toolchain at freeze
  time;
- `workspace`: `{"kind": "core"}`;
- `allowed_import_roots`: `Init`, `Lake`, `Lean` and `Std`;
- `schema_version`.

Lists are sorted and de-duplicated. The JSON has sorted keys, compact
separators and a trailing newline. The payload reports `record_sha256`, the
sha256 of the `challenge.json` bytes, and that value is the pin: keep it, and
check the record against it before any judgment. It also reports
`toolchain_dir`, the toolchain directory whose files were hashed. `--out`
must be absent under an existing parent directory, or an empty directory. The
parent may be reached through a symlink, as macOS's `/tmp` is, but `--out`
itself must not be one. On POSIX the directory becomes `0700` and both files
`0600`, whatever the umask.

On a refusal the verb exits 1 and writes nothing. Each refusal is a finding:

- `unsupported_toolchain`: `--toolchain` is not a stable
  `leanprover/lean4:vX.Y.Z` release at v4.35.0 or later, for example an older
  release, a release candidate or a nightly;
- `toolchain_unavailable`: the release is not installed, or it lacks `lake`,
  `lean`, `leanchecker` or `leanexport`; its directory is the source file's
  directory or lies inside it; `ELAN_HOME` or `AAS_LEAN_COMPARATOR_TOOLCHAIN`
  is not absolute; or its files cannot be hashed, for example because `bin/`
  holds a symlink, a directory or more entries than the attestation takes.
  The detail says which;
- `never_sanctioned_axiom`: `sorryAx`, which `--allow-compiler-trust` does not
  change, or an axiom that is neither sanctioned nor compiler trust;
- `compiler_trust_not_allowed`: a compiler-trust axiom such as
  `Lean.ofReduceBool` without `--allow-compiler-trust`;
- `definition_hole_not_allowed`: any `--definition`. A definition hole lets
  the solution choose the definition, so a challenge names theorems only;
- `theorem_not_declared`: a `--theorem` that the declaration walk does not
  find as a public `theorem` under that fully qualified name. A `def`, a
  `private` theorem, a commented-out one and a name with `«»` escapes are all
  refused. So is every source with a line the walk cannot read, such as
  `namespace «A B»`, because a name the walk reports may then sit in another
  namespace than the one Lean gives it; the detail counts those lines and
  shows the first. A `--theorem` with a `_root_` component is refused rather
  than rewritten, because Lean drops it: `theorem _root_.foo` inside
  `namespace N` declares `foo`. `lemma` is not a core Lean command, so a core
  challenge that declares anything with it cannot build and is refused;
- `source_unreadable`: the source is missing, a symlink, not a regular file,
  larger than 1 MiB, or not UTF-8;
- `challenge_import_not_trusted`: an import outside `allowed_import_roots`,
  such as `Mathlib`, an `import` that names no module, or a module name with
  `«»` escapes. So is a module the named toolchain does not ship: Lean looks
  for `import A.B` at `lib/lean/A/B.olean` in the toolchain directory, so
  without that regular file the challenge can never build. The detail is then
  `<module>: not shipped by <spec>`, such as
  `Lean.NoSuchModule: not shipped by leanprover/lean4:v4.35.0`;
- `challenge_unsafe_construct`: a hit of the scanner's safety patterns, such
  as `#eval` or `run_cmd`, or a source the scanner's lexer cannot read to its
  end, with the detail `lexical_scan_incomplete: ...`. That lexer does not
  read raw strings, so `r#"a"b"#` leaves it in a string that never ends, and
  an `#eval` after it would hide from the patterns;
- `output_dir_unusable`: `--out` is a symlink, a file or a non-empty
  directory, or its parent is missing or not a directory;
- `output_write_failed`: a write failed. What the run created is removed, and
  an existing `--out` gets its mode back;
- `challenge_freeze_failed`: an unexpected error, after which nothing is left
  behind, and an existing `--out` gets its mode back.

A `sorry` in the challenge is expected: a challenge states theorems and does
not prove them. The payload's `advisory_scan` is the `stub`-stage scan of the
frozen file and never changes the verdict.

Freezing checks names and bytes, not meaning. Whether the challenge states the
claim it stands for is still a review by a human or by a model of a different
family. The theorem names come from the same line walk as `axiom-audit`, not
from Lean.

The record binds the toolchain bytes seen at freeze time. Freeze on the host
that will judge, with that toolchain installed. A changed toolchain, such as
an update, a rebuild or an edited binary, needs a new freeze, and a record
frozen on another host or architecture will not match, because its binaries
differ.

The comparator exports and compares every permitted axiom from both the
challenge and the solution. For compiler trust this means:

- A record that permits `Lean.ofReduceBool`, `Lean.ofReduceNat` or
  `Lean.trustCompiler` cannot be accepted, because those are absent from the
  v4.35.0 environment: their export fails, and the comparator exits 1.
- A record that permits the axiom that `native_decide` adds for a helper can
  lead to an acceptance that rests on compiler trust, when the challenge
  declares the same helper (observed on v4.35.0). For
  `theorem helper : 2 + 2 = 4 := by native_decide`, Lean v4.35.0 named that
  axiom `helper._native.native_decide.ax_1_1`.
- An axiom of the `_native.bv_decide` form is not classed as compiler trust
  by the gate's pattern, so the freeze refuses it even with
  `--allow-compiler-trust`.

`--allow-compiler-trust` therefore really admits compiler trust, and the
statement review must see it.

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

These pattern lists are frozen: they are an advisory preflight, a proof supports a claim only when a host-run `lake comparator` accepts it, and a newly found trick becomes a comparator regression case rather than a new pattern.

## Recommended templates

When this skill is involved, consider these workflow templates (install via
the `workflow-templates` artifact profile, or `--with-deps` to pull backing skills):

- `informal-to-lean-formalization-runbook` -- Local-first intake mapping an informal proof to Lean declarations with a scanner-first verification gate separating typecheck status from claim support.
