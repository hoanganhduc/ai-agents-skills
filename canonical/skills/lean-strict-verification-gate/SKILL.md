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
and `leanchecker`. The `comparator` verb (see "Comparator") runs it; `doctor`
reports under `comparator` whether this host could:

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
`bwrap --ro-bind / / --unshare-all --die-with-parent -- /bin/true` with no
time limit and `PATH=/usr/bin:/bin` as the whole environment. Its
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
  challenge that declares anything with it cannot build and is refused. A
  theorem with explicit universe parameters, such as `theorem foo.{u}`, is
  refused too, since the walk reads its name as `foo.`;
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
  an `#eval` after it would hide from the patterns. The same kind covers an
  `axiom` declaration: a challenge may not declare an axiom (see compiler
  trust below);
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
- The challenge must not declare such an axiom itself. A challenge declared
  `axiom helper._native.native_decide.ax_1_1 : False`, the record permitted
  that name, and a solution that declared the same axiom could then prove
  `1 = 2`: the comparator accepted it (observed on v4.35.0). A challenge
  could declare `Lean.ofReduceBool` the same way. The freeze therefore
  refuses any `axiom` declaration in the challenge. It finds one with the
  scanner's lexer, so an axiom hidden from that lexer, for example between
  two raw strings, is not seen: when compiler trust is permitted, the
  statement review must still look for one.

`--allow-compiler-trust` therefore really admits compiler trust, and the
statement review must see it.

## Comparator

`comparator` judges a solution project with `lake comparator` against a
challenge that `challenge-freeze` wrote. It runs only on Linux, and never as
root:

```bash
bash "${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh" \
  skills/lean-strict-verification-gate/run_lean_strict_verification_gate.sh comparator \
  --input /path/to/solution/project \
  --challenge-record formal/challenge/frozen/challenge.json \
  --challenge-record-sha256 "$RECORD_SHA256" \
  --solution-module Solution
```

- `--input`: the solution project, a directory that is not a symlink.
- `--challenge-record`: the `challenge.json` that `challenge-freeze` wrote,
  with its `Challenge.lean` beside it, outside `--input`.
- `--challenge-record-sha256`: the `record_sha256` that `challenge-freeze`
  reported, as 64 lowercase hex digits. The record must still hash to it.
- `--solution-module`: a module of `--input` that declares the record's
  theorems; repeatable, at least one.
- `--timeout`: the run's wall limit in seconds. It also bounds each program
  the run starts (the sandbox preflight, `lean --version`, `lake
  resolve-deps`, `systemd-run` and `systemctl`) and the wait for the host
  lock. No limit unless set.
- `--paranoid`: also run every external kernel bundled with Lean. A record
  frozen with `--paranoid` asks for this too.
- `--keep-workspace`: keep the run's directory.
- `--memory-limit`: the run's memory limit, applied in a systemd user scope
  with no swap. A size is a positive whole number followed by `K`, `M` or
  `G`. No limit unless set; `off` says so explicitly.
- `--min-free-disk`: the free space the state directory needs, as a positive
  whole number followed by `K`, `M`, `G` or `T`. With less before the run,
  the run is refused; with less after a failed build, that rejection is
  taken back. Not checked unless set.
- `--nice`: run the judge under `nice -n N`, with N from 1 to 19. Not used
  unless set.

None of these four has a default: the user decided on 2026-11-13 that no
number the agent chose is a default. Without them a run has no wall limit, no
memory limit, no disk check and no lowered priority, so a judge that never
finishes runs until someone stops it. `bwrap --version` still runs through
doctor's version probe, whose 5-second limit was in the repository before
this work.

`comparator_status` is the verdict field. Only `accepted` and `rejected` are
verdicts; `not_run`, `unsupported_toolchain`, `tool_unavailable` and
`command_failed` give no verdict. The verb exits 0 only for `accepted`, 1 for
every other result, and 2 for a usage error. A result other than `accepted`
or `rejected` carries findings, `{kind, detail}`, that say why.

### Checks before the run

The verb checks the following in this order. The first check that fails ends
the run with its status and finding, and nothing after it runs.

1. The host is Linux and the gate does not run as root
   (`platform_unsupported`, `running_as_root`; `tool_unavailable`).
2. The input and the challenge give `not_run` when one of these fails:
   - `--input` is a directory, not a symlink (`input_invalid`);
   - the record lies outside `--input`, by real path
     (`challenge_record_inside_input`);
   - the record and the `Challenge.lean` beside it are regular files with
     one link, owned by the user who runs the gate and closed to group and
     others, as `challenge-freeze` writes them (`0600`), and each is read
     once within 1 MiB (`challenge_record_unreadable`,
     `challenge_unreadable`). A copy that lost its mode needs `chmod 600`;
   - the record hashes to `--challenge-record-sha256`
     (`challenge_record_mismatch`) and holds exactly what `challenge-freeze`
     writes: canonical bytes, the closed key set and every field rule
     (`challenge_record_invalid`, with the broken rule as detail);
   - `Challenge.lean` hashes to the record's `challenge_sha256`
     (`challenge_mismatch`).

   A record whose `workspace` is not `{"kind": "core"}` gives
   `unsupported_workspace` (`tool_unavailable`): only the core profile runs.
3. The toolchain the record names is found as in "Comparator readiness",
   outside `--input`. A release older than v4.35.0, a release candidate or a
   nightly gives `unsupported_toolchain` before any tool runs. The
   attestation must succeed (`toolchain_unavailable`), and its file digests
   and `lean --version` must equal the record's (`toolchain_changed`, naming
   up to five differing paths); both give `tool_unavailable`.
4. The sandbox: `bwrap`, found as in "Comparator readiness", reports 0.12.0
   or later to `bwrap --version`, its preflight starts, and its binary is
   hashed (`sandbox_unavailable`, `tool_unavailable`).
5. A paranoid run, by `--paranoid` or by the record, needs the five bundled
   kernels among the record's pinned files (`paranoid_kernel_missing`,
   `tool_unavailable`).
6. The solution gives `not_run` when one of these fails. Each
   `--solution-module` is a valid Lean name without a `_root_` component; it
   is not `AASChallenge`, `AASSolution` or `lakefile`, nor a name below the
   first two, and it does not lie under `Init`, `Lake`, `Lean` or `Std`
   (`solution_module_invalid`). Module `A.B` is the file `A/B.lean` of
   `--input` (`solution_module_missing` when it is not there). From the named
   modules the verb walks the local imports breadth first and reads each
   file once; those bytes are what the run gets. An import under `Init`,
   `Lake`, `Lean` or `Std` comes from the toolchain and is never copied, and
   a file of `--input` at that module's path is refused
   (`solution_shadows_trusted_module`). Any other import must be a file of
   `--input` and must not be `AASChallenge`, `AASSolution` or `lakefile`
   (`solution_import_not_allowed`); a `Mathlib` import lands here. Every
   file is a regular file with one link, under directories that are not
   symlinks, in UTF-8 that the scanner's lexer reads to its end
   (`solution_file_unsafe`). The walk refuses a solution of more than 240
   files, 15 MiB in total or 16 MiB in one file, and never truncates
   (`solution_too_large`). Only these files reach the run: the gate never
   reads the project's `lakefile*`, `lake-manifest.json`, `lean-toolchain`
   or `.lake`.
7. The state directory is
   `$XDG_STATE_HOME/ai-agents-skills/comparator` when `XDG_STATE_HOME` is an
   absolute path, and `~/.local/state/ai-agents-skills/comparator`
   otherwise. It must lie outside `--input` (`state_dir_inside_input`,
   `not_run`). Then the host:
   - The state directory, its `receipts/` and its `runs/` are made, or set,
     `0700`. Missing parents are made `0700`, and parents that already exist
     keep their mode. A symlink or a file in place of one of the three is
     refused (`comparator_failed`, `command_failed`).
   - With `--min-free-disk`, at least that much must be free there
     (`insufficient_disk`, `tool_unavailable`).
   - With `--memory-limit`, a user scope with the limit and no swap must
     start, within `--timeout` when it is set:
     `systemd-run --user --scope -p MemoryMax=<limit> -p MemorySwapMax=0 -- /bin/true`
     (`memory_limit_unavailable`, `tool_unavailable`). `systemd-run` gets
     only `PATH=/usr/bin:/bin` and the gate's `XDG_RUNTIME_DIR` and
     `DBUS_SESSION_BUS_ADDRESS`. Without a memory limit, the payload's
     limitations say that no memory limit was applied.
   - `/usr/bin/python3`, which runs the sandbox wrapper, must be an
     executable file (`wrapper_interpreter_unavailable`, `tool_unavailable`).
   - With `--nice`, `/usr/bin/nice` must be an executable file too
     (`nice_unavailable`, `tool_unavailable`).
   - One run at a time: the run takes the lock `comparator.lock`, a `0600`
     file in the state directory. It waits for it for at most `--timeout`
     seconds (`host_busy`, `command_failed`), or for as long as the lock is
     held when there is no `--timeout`.

   The gate does not check the host's load; its caller does, before it
   starts a run. A run starts only when the 1-minute load average is at most
   the number of CPU cores minus one and `MemAvailable` is at least the
   memory limit. Otherwise the caller records the check as not run, which is
   no verdict.

### The run

Each run then gets a private directory `runs/<run_id>/`, where `run_id` is the
UTC time and eight random hex digits. Its workspace `ws/` first gets the
generated `lakefile.toml` and `lean-toolchain`, then the `lake-manifest.json`
that the toolchain's `lake resolve-deps` writes for them, before any byte of
the solution arrives (`workspace_setup_failed`, `command_failed`, when that
fails or writes no manifest). Then come `AASChallenge.lean`, the frozen
challenge bytes, the solution files exactly as the checks read them, and
`AASSolution.lean`. The files the gate writes are `0600` and its directories
`0700`.

- `lakefile.toml` is TOML, so no configuration code runs. It declares the
  library `AASChallenge` and the library `AASSolution`, whose `roots` name
  `AASSolution` and the first component of each module of the solution's
  closure, and whose `globs` name `AASSolution` and each of those modules.
  The globs matter: by default Lake builds each root's own module, such as a
  module `Sol` that does not exist for `Sol.Main`. The generated file
  carries none of the solution project's own Lake options, such as
  `leanOptions` (`maxHeartbeats`, for one) or `moreLeanArgs`, so a proof that
  builds only with them is rejected as `build_failed`. Such an option belongs
  in the source, as `set_option maxHeartbeats <n> in`.
- `AASSolution.lean` starts with `prelude` and holds one `import` line for
  each `--solution-module`. `prelude` keeps `Init` out of the file itself, so
  a `prelude` solution that declares a name `Init` also declares is judged by
  lake comparator, not refused by the build. A solution module without
  `prelude` still imports `Init` itself.
- `comparator.json`, in the run directory, names `AASChallenge`,
  `AASSolution`, the record's theorems and its permitted axioms, and no
  definition holes and no extra kernels.

The judge is `<toolchain>/bin/lake comparator --config <run>/comparator.json`,
with `--paranoid` for a paranoid run, under `nice -n N` when `--nice` gives
N, and under `env -i`, so it
gets only `PATH=<toolchain>/bin:/usr/bin:/bin`, `HOME=<run>/home`,
`TMPDIR=<run>/tmp`, `LANG=C.UTF-8` and
`COMPARATOR_BWRAP=<run>/bwrap-wrapper`. lake comparator writes its temporary
exports and kernel configuration under that `TMPDIR`, inside the run
directory. `lake resolve-deps` and the judge start with umask `077`, whatever
the gate's umask is: Lake makes `.lake/build` under the umask it inherits,
and under one without the owner's write bit, such as `0277`, it could not
write there. Unless `--memory-limit off`, the judge runs in a user scope of
its own, `aas-comparator-<run_id>`, with the limit and no swap.

`COMPARATOR_BWRAP` is the gate's sandbox wrapper. It is a Python program held
in the gate's own source, so the gate's sha256 covers it, and the run writes
it to `<run>/bwrap-wrapper` after a `#!/usr/bin/python3 -I` line;
`sandbox.wrapper_sha256` is the file's digest. lake comparator runs it in
place of `bwrap` for each sandboxed step, with an empty environment. The
wrapper reads `wrapper.json` beside it, then checks the step:

- only the `bwrap` options lake comparator uses are allowed, and
  `--unshare-all`, `--die-with-parent`, `--clearenv` and `--new-session` must
  all be there;
- `--bind` may only bind the workspace's `.lake` onto itself, and
  `--ro-bind` may only bind `/`, or a path in the toolchain or the run
  directory, onto itself;
- the command must be a program in the toolchain's `bin/`, and the step one
  that lake comparator takes: `lake env`, `lake build` of `AASChallenge` or
  `AASSolution`, `leanexport` of one of them, or a kernel.

It drops `--share-net`, which lake comparator asks for when it resolves the
workspace, so no step gets a network; a core workspace has nothing to fetch.
It then runs the step in the pinned `bwrap` and appends one line to
`phases.jsonl` in the run directory: the phase, the target, the exit code,
the seconds, the options it dropped, and how many processes of the run's
cgroup the out-of-memory killer stopped while the step ran (`oom_kills`, from
the cgroup's `memory.events`, or -1 when that file cannot be read). A step it refuses never runs and is
logged with the reason. The log takes at most 64 lines, and the wrapper never
writes to standard output, which carries an export.

`--timeout` bounds the judge (`timeout`), and so does a 16 MiB cap on its
output (`output_limit`); both give `command_failed`, and the judge's scope,
when it has one, is killed too. Ctrl-C during the judge kills the judge's
process group and its scope, removes the run directory and writes no
receipt. A `SIGTERM` or `SIGKILL` sent to the gate itself does not stop the
judge. A run that outgrows `--memory-limit` is
killed inside its scope. On the host where this was tried (systemd 255,
whose `DefaultOOMPolicy` is `stop`), systemd then stopped the whole scope,
and the judge ended with `SIGTERM`, which gives `comparator_exit`, no
verdict. Where systemd stops only the killed process, Lake reports a failed
build, so the gate does not rely on the scope's policy: a step during which
the out-of-memory killer stopped a process makes any result
`out_of_memory`, no verdict, and so does a run with no memory limit that the
host's own out-of-memory killer hits.

### Verdicts

The solution's build prints whatever it likes, so the gate trusts only three
things from a run: lake comparator's exit code, its last line on standard
error, and the wrapper's phase log. The run is `accepted` only when lake
comparator exits 0 after exactly the steps of a run, each exiting 0
(resolving, the build and export of `AASChallenge`, the build and export of
`AASSolution`, then the five bundled kernels in a paranoid run, then
`leanchecker`), its last line on standard output is `Your solution is okay!`,
every check after the run holds, and its receipt is written.

The run is `rejected` only for one of these last lines of lake comparator,
written after `error: ` when it exits 1. `comparator_rejection_kind` names
the kind, and `comparator_detail` is the line without `error: `, at most 500
characters. The first five kinds count only after the five steps before the
kernels, each exiting 0:

| Kind | Last line |
|---|---|
| `statement_mismatch` | `Challenge and solution theorem statement do not match: '<name>'` |
| `kind_mismatch` | `Challenge and solution constant kind don't match: '<name>'`, `Solution constant is not a theorem: '<name>'`, `Solution constant is not a definition: '<name>'` |
| `constant_mismatch` | `Const does not match between challenge and target '<name>'` |
| `missing_constant` | `Const not found in solution '<name>'`, `Const not found in solution: '<name>'`, `Constant not found in solution '<name>'` |
| `forbidden_axiom` | `Illegal axiom detected: '<name>'` |
| `kernel_rejected` | `<kernel> exited with <n>`, after every kernel ran with `leanchecker` last and exiting 1, where `<kernel>` and `<n>` are the first kernel that failed and its exit code |
| `build_failed` | `Child exited with 1`, when the solution's build was the last step and exited 1 |

These are the messages of lake comparator v4.35.0. On that release,
`leanchecker` exits 1 when it rejects, as Lean's `olean-issue` test shows. A
line on standard error that starts with `bwrap: ` turns the last two kinds
into `sandbox_failed`, since a `bwrap` that cannot start a step also exits 1.
A `bv_decide` proof is rejected as `forbidden_axiom`: Lean v4.35.0 names its
axiom like `bv_and._native.bv_decide.ax_1_5`, which no record permits.

Once the checks before the run have passed, every other result is
`command_failed`, with one of these findings:

- `phase_log_unreadable`: the phase log is missing or malformed;
- `wrapper_refused`: the wrapper refused a step;
- `comparator_exit`: lake comparator ended by a signal, or with an exit code
  other than 0, 1 and 2;
- `comparator_could_not_start`: lake comparator exited 2, as it does when it
  cannot start;
- `inconsistent_run`: the steps, their exit codes and lake comparator's exit
  code do not fit together, such as exit 0 without the okay line;
- `unclassified_failure`: exit 1 with a last line that is not in the table,
  including the messages about the challenge's side, such as
  `Const not found in challenge '<name>'` and
  `Challenge constant is not a definition: '<name>'`;
- `resolve_failed`, `challenge_build_failed` and `challenge_export_failed`:
  a step on the challenge's side failed. A record that permits
  `Lean.ofReduceBool` lands in `challenge_export_failed`: Lean v4.35.0 has no
  such constant, and `leanexport` aborts with exit 134;
- `solution_build_aborted`: the solution's build failed in another way, such
  as by a signal;
- `solution_export_failed`: the solution's export failed;
- `external_kernel_disagreement`: a bundled kernel rejected what
  `leanchecker` accepted;
- `kernel_failed`: a kernel failed without the rejection above;
- `sandbox_failed`: a stderr line holding `bwrap: `, even after other text on
  that line, came with a would-be rejection;
- `timeout`, `output_limit` and `comparator_failed`, the last for an
  unexpected error or a judge that cannot be started;
- `out_of_memory`: the out-of-memory killer stopped a process of the run
  during a step, which can fail a build or a kernel as a wrong solution
  would; this takes back an acceptance or a rejection;
- `oom_check_unavailable`: a `build_failed` or `kernel_rejected` rejection
  whose out-of-memory count the wrapper could not read;
- `insufficient_disk` after the run: with `--min-free-disk`, a `build_failed`
  rejection with less than that free before the cleanup, since a build that
  runs out of space fails like one that does not compile;
- `workspace_setup_failed` from the workspace above, and the checks after
  the run below.

### After the run

After the judge, the gate checks that nothing the run relied on changed. A
change gives `command_failed`, whatever the judge said:

- `input_changed_during_verification`: the solution's files in `--input`;
  `solution.post_digest` is their digest after the run;
- `workspace_changed_during_verification`: the workspace outside `.lake`;
- `toolchain_changed_during_verification`: the toolchain's pinned files, or a
  stat walk of its whole tree (path, mode, size, modification time and change
  time, which a user process cannot set back);
- `sandbox_changed_during_verification`: the sandbox wrapper, its
  `wrapper.json` or the bwrap;
- `challenge_changed_during_verification`: the record, its `Challenge.lean`
  or the run's `comparator.json`.

Once the gate has tried to start the judge, the run writes a receipt whatever
its result, even when the judge could not be started:
`receipts/<run_id>.json`, a new `0600` file holding a
`host_comparator_receipt.v1` record. The record names the run and its time,
the gate file and its sha256, and the platform. It holds the payload's
toolchain, sandbox, challenge and solution sections, the run's argv, exit
code, seconds, timeout and phases, and the result. The payload's `receipt`
gives the file's path and sha256. A receipt that cannot be written gives
`receipt_write_failed` (`command_failed`), so no acceptance goes unrecorded. A
run that stops before the gate tries to start the judge writes no receipt.

The run directory is removed on every path, without following a symlink,
before the receipt is written. The removal works one directory at a time
through directory descriptors, so a tree too deep for recursion or with paths
longer than `PATH_MAX` still goes. `--keep-workspace` keeps the directory and
reports its path as `run.workspace_kept`. A removal that fails gives
`cleanup_failed` (`command_failed`, no verdict), in the payload and the
receipt alike.

### Limitations

The payload's `limitations` always lists:

- the comparator decides statement match, axiom use and kernel acceptance;
  whether the challenge states the intended claim is still a review step
- statement comparison is exact: an equivalent statement written differently
  is rejected
- a process running as the same user can change the toolchain, bwrap, the
  gate or a receipt; the digests detect a change made during a run, not one
  made before the freeze or after the receipt
- the solution's own build directory is writable inside the sandbox, so a
  solution can fill the disk; --min-free-disk, when set, and the cleanup bound
  this only after the fact
- core profile: imports outside Init, Std, Lean and Lake are refused

Without a memory limit, which is the default, it adds "no memory limit was
applied". Two more limits hold:

- Compiler trust can hide in the challenge. `challenge-freeze` refuses an
  `axiom` in a challenge, but it finds one with the scanner's lexer, and two
  raw strings, such as `r#"a"b"#` and a later `r#"c"d"#`, hide what lies
  between them from that lexer. On Lean v4.35.0, a challenge that hid
  `axiom helper._native.native_decide.ax_1_1 : False` that way was frozen
  with `--allow-compiler-trust` and that axiom permitted. A solution that
  declared the same axiom then proved `1 = 2`, and the comparator accepted
  it. With the default permitted axioms, such a solution is rejected
  (`forbidden_axiom`). A hidden `#eval` runs only inside the sandbox, during
  the challenge's build. When compiler trust is permitted, the statement
  review must therefore read the whole challenge for hidden declarations.
- `doctor`'s `has_comparator` reads directory listings only, so it can say
  `true` for a toolchain whose attestation fails. The comparator's own checks
  of the record's toolchain decide whether a run starts.

### Bounds the agent chose

These bounds keep the gate from reading or writing too much; none of them
limits a run. The agent chose their values, and no source fixes them except
where noted. To use other values, change the constants in
`lean_strict_verification_gate.py`.

- `challenge.json` and `Challenge.lean`: at most 1 MiB each
  (`CHALLENGE_RECORD_MAX_BYTES`, `CHALLENGE_SOURCE_MAX_BYTES`).
- The toolchain listing: at most 64 files in `bin/` and 32 libraries in
  `lib/lean/` (`TOOLCHAIN_BIN_MAX_FILES`, `TOOLCHAIN_LIBRARY_MAX_FILES`).
- The output of the sandbox preflight, `lean --version`, `lake
  resolve-deps`, `systemd-run` and `systemctl`: at most 64 KiB each
  (`READINESS_OUTPUT_MAX_BYTES`).
- The phase log: at most 64 lines and 64 KiB
  (`COMPARATOR_PHASE_LOG_MAX_LINES`, `COMPARATOR_PHASE_LOG_MAX_BYTES`).
- Details are cut to 500 characters, and output tails to 4000.
- Waits inside the gate: up to 5 seconds to reap a killed process and 1
  second for its output readers, and a 0.5-second poll while another run
  holds the lock.
- Older than this work: the judge's output cap of 16 MiB
  (`COMMAND_OUTPUT_MAX_BYTES`), and the solution caps of 240 files, 15 MiB in
  all and 16 MiB a file, which mirror the remote formal lane.

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
