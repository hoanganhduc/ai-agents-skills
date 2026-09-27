# Lax workflow migration — delivery verification

Scope: the approved local T0–T8 migration, a bounded independent benchmark and
offline archival preparation. This report records the local acceptance checkpoint
before the separately authorized GitHub push. At that checkpoint main-repository
changes were uncommitted; hosted CI results are tracked separately on GitHub.

## Delivered

- `lax-formalization`: local catalog discovery, version-pinned isolated source
  verification, protected concepts, fresh replay/inspection, grounded proof closure,
  separately bound semantic review and proposal-only publication planning.
- `zenodo-artifact`: source/evidence preparation, metadata/provenance checks,
  checksums and safe restoration. No login, upload, DOI or publishing operation.
- A shared paper template retaining GitHub verification and manual artifact
  preparation. Locked nested npm dependencies live at `tools/lax-ci`.
- ARL host-pinned Lax requests and terminal verification; generic Lean retains
  its strict gate. Registration and a green build do not establish paper support.
- Source manifests, profiles, consumers, workflow templates, formal-policy
  instructions, both runtime launchers and generated documentation updated.
- Installer lifecycle selectors resolve retired exact names from managed state
  or an explicitly selected known run. Ordinary install selection stays catalog-only.

## Installed scope and retirement

Updated the inspected Codex, Claude, DeepSeek, Copilot, OpenCode, Antigravity,
Kimi, Grok and ChatGPT Local Coder targets. Both `.codex/runtime` and
`.local/share/ai-agents-skills/runtime` were verified. Actual native proof execution
was qualified on Linux ARM64. Four fake-root shapes (Linux, macOS, Windows, WSL)
passed lifecycle checks; those are not native OS execution claims.

Removed the managed `lean-research-library` installation and catalog/runtime
sources, replaced five exact manual mandatory rules, and deactivated its config.
Five restored/unmanaged old skill files and the config were preserved outside
skill discovery. Research repositories, HoangMathLib source and history remain.
There are zero active installer records for the retired skill. Reinstallation
was checked for idempotence and does not reintroduce its old mandatory route.

Backups, installer journals and exact manual before/after hashes are recorded
privately in `.codex/runs/lax-migration-20260927/`. Do not overwrite later user
edits when restoring any backup. Use normal installer rollback with the exact
recorded run; manual-rule restoration must first match its recorded after-hash.

## Evidence

| Check | Observed result |
|---|---|
| Independent reference subset and blind candidate | Passed; same target/general assumptions, closed proof obligations |
| Archive restoration and installed-runtime replay | Passed with identical source digest and distinct synthetic Git commit |
| Real Lean controls | Correct True accepted; open obligation retained; rogue axiom/native trust rejected |
| Additional negative controls | Weakened challenge, corrupt compiled capture and wrong-revision packaging refused |
| Native execution boundary | Synthetic private-path/socket/network checks and descendant/debugger canaries passed |
| Real local npm layout used by CI | Provisioning and all verification phases passed with nested locked dependencies |
| Lax unit/ARL contracts | 31 run without failures, including 3 native opt-in skips; native cases ran separately; the final GitHub-config regression adds one passing case |
| Zenodo / retired-selector tests | 10 / 4 passed |
| Formal terminal / formal policy | 74 / 36 passed |
| Runtime integration | 119 run without failures, including 24 platform skips; the known dirty-tree inventory case was checked on a clean source copy |
| Manifest / runtime-contract / canary / static tests | 38 / 51 / 29 / 40 run without failures (one platform skip in static tests) |
| Encoding / wrapper argv0 / inert-parameter checks | 2 / 7 / 9 passed |
| Docs, static source checks, sanitization | Passed; sanitization tests: 12 passed |
| Direct old-to-final upgrade | Both pristine and user-modified baseline installations passed; changed content preserved |

See [benchmark details](lax-formalization-benchmark.md), the
[approved plan](lax-formalization-migration-plan.md), and
[source register](lax-formalization-sources.md).

## Whole-repository check: not globally green

The full test target ran 3,549 tests (41 skipped), initially reporting 10 failures
and 9 errors. The new integration omissions were corrected and the affected test
groups rerun successfully. The whole suite was not rerun after those focused fixes.
Two pre-existing conditions remain outside this migration:

1. Nine `vnthuquan` errors require an absent external executable. Running that
   suite from an isolated checkout of baseline
   `c2b68de26e8ed4f90e956107855624bf5bedf5db` reproduced the same nine errors
   (30 tests). No vnthuquan implementation or installation was changed.
2. The original runtime inventory contains nine ignored bytecode files dated
   2026-09-17 in unrelated compute modules. They predate this task and were left
   intact. Current runtime source copied without generated `__pycache__` passed
   inventory admission. Bytecode generated by this task was moved out of the
   source tree; test invocations suppress new bytecode.

These results do not justify claiming the entire repository test target passes.

## Review and qualification limits

Fresh code/security reviews identified Git configuration execution, artifact
shadowing, dependency identity/scope, mutable archive reads, surviving process
code, cache identity, CI dependency layout and semantic-status consistency issues.
The findings were fixed and checked. Trusted kernel/toolchain/background inputs
and the operator-owned supervisor remain explicit assumptions.

Hosted GitHub Actions, native Windows/macOS builds, live cross-submission import
chains, the full Ramsey development and remote publication were not qualified.
The benchmark checks one selected lemma. The workflow needs a reviewed published
`AAS_SKILLS_REV` before GitHub can run the new runtime. A source/evidence ZIP is
not an offline-complete Lean/mathlib/toolchain distribution. Full CFF schema
validation of the benchmark was a separate check against CFF 1.2.0.

No Lax submission/registration, GitHub push/release, Zenodo draft/upload/DOI,
Pages deployment, external notification or message was performed.

Delivery pointers: the private benchmark `delivery-bundle/` contains the source
ZIP, metadata, verification summary, provenance, dependency inventory,
reproduction instructions and checksums. `github-shaped-evidence/verification.json`
is the final installed-verifier receipt. The GitHub checkout configuration was
checked against the pinned action source and then exercised locally.
