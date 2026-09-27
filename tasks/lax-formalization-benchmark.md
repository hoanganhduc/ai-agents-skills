# Local Lax workflow benchmark — 2026-09-27

Scope: one order-pattern lemma, not the Ramsey paper. No Lax registration,
GitHub push/release, Zenodo draft/DOI/upload/publication or Pages deployment ran.

Reference: [lax-345067](https://laxarchive.org/lax-345067/index.html),
[pinned source](https://github.com/lax-archive/lax-submissions/tree/feabf43dcd6967ac912b2064003ea715e06e58f9/finite-ramsey-v4-33).
The three file hashes recorded in the approved plan matched the fetched bytes.
The extraction retains the selected theorem statement/proof and `otp` body;
`orderType` is copied exactly. A new concept obligation expresses the same target
through its explicit comparison function. An annotation links the existing helper
proof to that obligation. Imports/root inventories are minimized and recorded in
private extraction evidence. This checks selected source bytes; it does not certify
the original entire submission or claim that the synthetic extraction is registered.

The independent solver used a fresh context, received only the mathematical
statement, fixed definitions and two allowed Mathlib imports, and made zero tool
calls. It returned its proof before seeing reference material. The evaluator
assembled the Lax wrapper and froze the candidate before comparison. This is
prompt/context separation, not a claim about the model's training data.

| Check | Reference subset | Independent candidate |
|---|---|---|
| Generality | Arbitrary `V`, `LinearOrder V`, `Fin ℓ` tuples | Same |
| Repeated entries/equality | Included | Included |
| Strict-order definition | `a i < a j` | Same definition |
| Comparison definition | `compare (a p.1) (a p.2)` | Same definition |
| Proof method | Trichotomy | Two strict-comparison case splits, then antisymmetry |
| New top-level helper lemmas | None in selected proof | None |
| Machine status | passed | passed |
| Proof closure | closed | closed |
| Semantic correspondence | accepted | accepted |
| Fresh concept/proof replay | Both packages | Both packages |
| Synthetic local source commit | `f98166ae8a338160dc548ed9c482cc71e68f5e22` | `e73768c9d29347307fb40073efd600dc0925a20b` |

Archive preparation and validation passed. CFF metadata passed the official CFF
1.2.0 JSON schema. The bundle restored without `.git`, then compiled and replayed
again after an explicitly synthetic Git initialization. Original and restored
source digest both equal `579633c63229795b288bfa3333049761d09769e8068303b49a525e71726b1c5e`. Restored commit is
`73f8a4c166f3b52a9e8a40f9ae30a80824188519`; it is deliberately distinct from the original commit.
A weakened target (`True`) was rejected against the frozen challenge before proof
compilation. Earlier native fixtures also distinguished closed True, an open
obligation, a rogue axiom and native/compiler-trust evaluation.

| Gap exposed | Correction / disposition |
|---|---|
| Extracted root imported a module only transitively | Enumerate every own module directly; Lax rejected the incomplete inventory |
| Archived Git source loses repository identity | Preserve original provenance; reconstruct synthetic Git separately and reverify bytes |
| Clean status can hide worktree changes | Compare committed export and tracked file inventories |
| Tool/cache labels alone do not bind actual inputs | Provisioned fingerprints plus before/after inventory checks |
| Reviewing one dependency target could endorse unrelated witnesses | Check semantic scope of actually used dependency statements |
| Shared template initially omitted installed runtime metadata | Runtime full-profile and install inventory checks corrected the integration |

The candidate remains standalone. Live cross-submission imports, the complete
Ramsey development, hosted GitHub Actions, remote publication and native
macOS/Windows execution were not tested. Those are exclusions, not inferred
successes. Closure selection/cycles and unavailable search are covered by local
fixtures; the actual reference subset was independently executed.

Private reproducible source, requests, receipts, extraction patch and bundle:
`.codex/runs/lax-migration-20260927/benchmark/` (ignored by Git). Public planning
source register: [sources](lax-formalization-sources.md). The ZIP is a source-and-
evidence bundle; dependencies/toolchains must be provisioned from their recorded
pins. No offline-complete toolchain distribution is claimed.

The locked local npm installation used by CI was separately provisioned and ran
the complete template verification entrypoint successfully after switching to a
nested dependency layout. This covers the CI tool installation shape locally;
it is not a hosted Actions run. Native boundary canaries also confirmed absent
host-private synthetic files and engine socket, read-only source mounts, no
outbound connection, disabled SIGUSR1 debugger activation, and stopped compiler
descendants before collection. Only synthetic canaries were used.

Final handoff uses `handoff-evidence/`, `handoff-bundle/` and
`handoff-restore-evidence/` under the private benchmark directory. The original
independent Lean proof was frozen at `050c2071051d5db4abd54284e2c6a1e37e76eb13`.
Only the CI workflow changed afterwards; a Git diff confirmed the Lean sources
were unchanged. Both the current candidate and the reconstructed current bundle
were verified through the installed Codex runtime. A deliberately corrupted own
compiled module was refused by fresh replay. Packaging also refused the original
receipt when supplied with the synthetic restored commit.

The delivery archive is `delivery-bundle/`. Its receipt comes from the final
installed verifier with the `gc.auto=0` configuration emitted by the pinned
GitHub checkout action. That exact configuration passed; other GC values remain
refused. `delivery-restored/` has the identical source bytes already replayed in
`handoff-restore-evidence/`. This is a local checkout-shaped qualification, not
a hosted GitHub Actions run.
