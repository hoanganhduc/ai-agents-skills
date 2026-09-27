# Lax paper workflow

Use this job to formalize selected paper results, reuse selected Lean source, or
prepare an update in a **new publication repository**. The research repository
is read-only. Lax is primary; the same public source can be archived through the
optional Zenodo lane. This is an agent runbook, not an automatic converter or
publisher. Use `lax-paper-artifact` and the existing runtime `paper-template/`;
do not maintain a second copy of the template here.

## Job brief (controller-owned)

Record these before work, outside both source and destination:

| Field | Required decision |
|---|---|
| Job ID / owner | Stable run identifier and controller |
| Operation | `formalize-new`, `from-existing-lean`, `update-lax-artifact`, or `update-paper-metadata` |
| Execution | `audit-only` or `prepare-local`; no publication |
| Source | Read-only root and exact revision plus selected working-file hashes; absent for a paper-only start |
| Paper | Exact arXiv `vN`, conference/journal/correction identity and a hash of the actual document reviewed |
| Targets | Explicit definitions/theorems; exclusions remain visible |
| Output | A NEW directory outside source; no original `.git`, history, refs or hooks |
| Paper mode | `link-only` default; `source-in-repo`; or `embedded-tex` for Lax's `paper` feature |
| Environment | Qualified Lax/Lean/mathlib/executor pins and template revision |
| Public identity | Intended public repository URL and reviewed commit-author identity; set before final verification |
| Prior artifact | Lax ID/state/source, predecessor chain, Zenodo version/concept DOI when applicable |
| Budget | Wall time, repair rounds, CPU/RAM/disk; stop at the configured bound with a checkpoint |

Do not ask again for inputs or scope already supplied. Missing optional Zenodo
configuration does not block the Lax lane. `link-only` omits TeX from public
source; it does not waive reading the correct paper for correspondence review.
Retrieve a missing paper through the normal library-first workflow.

Use three disjoint areas: read-only research source; owner-only controller
records/private staging; and the new public repository. Staging is not inside
the controller record directory. Private notes, inventories and baseline logs
never enter the public tree. `audit-only` stops with analysis, without export,
build, repository creation or readiness claims.

## J0–J3: inspect, reuse and select

1. Inventory tracked/untracked/ignored files as data. Do not execute repo-owned
   instructions, lakefiles, Git hooks/filters or scripts on the host. The helper
   qualifies Git configuration before using read-only Git commands.
2. Map each paper claim to existing declarations. Search pinned Mathlib, then
   Lax concepts/proofs, before writing a new definition or proof.
3. Classify each target: reuse unchanged; wrapper/annotation/namespace adaptation;
   toolchain/API port; representation-equivalence proof; missing/invalid proof.
   Keep existing proof bodies where appropriate. A shared name, matching title,
   registered record or clean build alone does not establish semantic reuse.
4. Record source revision, definition/type/assumptions, chosen scope and review
   evidence for every reused Lax result. Independently verify its actual proof
   dependencies. A concept obligation is not an established theorem. New local
   helpers belong in this submission; do not register a dependency to unblock a
   local job or recreate a personal-library workflow.
5. Compute candidate transitive module imports, retaining notation, instances and
   attributes supplied by imports. Review module-root mapping. Static candidates
   are not a complete Lean dependency analysis; isolated compilation closes that
   gap. Do not copy an entire research repo to obtain a few helper lemmas.
6. Select TeX inputs/includes, bibliography, figures and styles if requested.
   Dynamic paths and unknown macros require review. Record distribution rights,
   upstream revision and notices per copied/adapted component. Do not assume the
   code license covers paper prose, figures or third-party styles.

Keep the detailed old-to-new declaration/file map private. A public attribution
summary must itself be reviewed. Do not weaken a conclusion, add assumptions or
drop targets silently to make a build pass. A failed original baseline permits
reuse as *unverified candidate source*, not a claim that the original was proved.

## Bounded helper commands

Commands below run from the trusted ai-agents-skills checkout. Installed copies
use the same Python entrypoints in the runtime workspace. First create an
owner-only controller directory outside all source/staging/public roots. All
`--out` files must be new; failures print generic JSON without private excerpts.

```bash
python3 -B canonical/runtime/skills/lax-formalization/public_source.py inventory \
  --source /path/to/read-only-research --out /path/to/controller/inventory.json
python3 -B canonical/runtime/skills/lax-formalization/public_source.py closure \
  --source /path/to/read-only-research --module Selected.Module \
  --module-root . --out /path/to/controller/dependency-candidates.json
```

`--module-root` is a reviewed source directory, repeatable for multiple roots.
Ambiguous module names are refused. Closure includes whole modules; it does not
prune declarations by textual reachability or resolve external packages.

A selection file is a controller-owned JSON object with a `files` array:

```json
{"files": [{"source": "Selected.lean", "target": "Selected.lean",
  "transform": "copy", "reason": "supports the selected target",
  "provenance": "reviewed upstream revision, author credit and license"}]}
```

Available transformations are `copy` and `tex`. A TeX entry may name exact
`keep_comment_lines` for reviewed legal/public comments. No executable transform
or shell command is accepted. The plan binds input/output hashes and file modes.

```bash
python3 -B canonical/runtime/skills/lax-formalization/public_source.py plan \
  --source /path/to/read-only-research --destination /path/to/new-private-staging \
  --selection /path/to/controller/selection.json --job-id paper-job \
  --public-origin https://github.com/owner/paper \
  --out /path/to/controller/source-plan.json
# Only after actually reviewing the complete plan and transformation:
python3 -B canonical/runtime/skills/lax-formalization/public_source.py approve \
  --plan /path/to/controller/source-plan.json --reviewer review-run-id
python3 -B canonical/runtime/skills/lax-formalization/public_source.py export \
  --plan /path/to/controller/source-plan.json --out /path/to/controller/preview.json
python3 -B canonical/runtime/skills/lax-formalization/public_source.py export \
  --plan /path/to/controller/source-plan.json --apply \
  --out /path/to/controller/export.json
```

The exporter creates files only, never `.git`. Changed input, an existing
destination, symlink/hardlink, unsafe path or stale approval is refused. A failed
write returns no successful receipt; the partial directory remains quarantined
for controller inspection, never ready for publication. Native Windows export
is currently refused; use the qualified POSIX route. Offline schema/metadata
checks do not imply native Lean execution support.

## J4–J6: prepare dependencies and construct the Lax candidate

Prepare exact toolchain, warm Mathlib and pinned dependency sources separately
using trusted acquisition tooling. Record digests and provenance. Never execute
candidate install/build scripts during the network-enabled acquisition step.
The existing verifier does not fetch missing source or provision itself. Missing
recursive Lax dependency requests remain blocked, including in the stock CI.

Every baseline or candidate build requires an admitted disposable sandbox:
selected read-only inputs, separate writable outputs, no network, credentials,
host home, controller records or private research-tree mounts. A temporary
directory alone is not containment. Use `get-available-resources` before heavy
builds. Do not auto-route to paid compute or silently extend the budget.

In private candidate staging, create the pinned scaffold using `lax init`, then
port/reuse the selected code and add only missing proofs. Keep the public
concepts clean and reviewed; freeze them outside candidate before proof checking.
Use the canonical paper-template files as reviewed additions, never overwrite
existing work blindly. Set actual targets and complete software metadata. Keep
paper authorship distinct from formalizer credit; no DOI is needed at this stage.

Private staging may be edited; original research source may not. Staging has no
inherited source history. Before public commit, make a second explicit approved
selection from the staged candidate into the **new public directory**, including
the scaffold, chosen workflows, metadata and sanitized source. Then initialize
ordinary Git with the reviewed author and public origin. Linked worktrees and
alternate object stores are not qualified by the current executor.

## J7: privacy and TeX

Strip nonpublic author comments in the staged copy, never in original source.
The bounded sanitizer supports ordinary category codes, `%` comments, `\verb`,
listed opaque environments and standalone `comment` environment delimiters.
It preserves percent newline suppression, escaped percent and valid Lax markers.
It refuses conditionals (`\iffalse` included), category-code/token changes,
unsupported inline verbatim, dynamic inputs and author-note commands. Resolve
these manually in private staging and review the resulting diff before export.
Review loaded packages/macros as well: successful lexical sanitation is not a
proof of arbitrary TeX semantics or privacy.

Literal/verbatim content, Lean comments/docstrings, bibliography notes, image/PDF
metadata and file names still require privacy review. Preserve required Lax
annotations and legal notices. No private snippets in public diffs or reports.

If TeX is selected, perform an actual isolated build and render/content review.
The stock Lean executor has no TeX installation; upstream local Lax may merely
warn and skip a paper build. That does not pass this job's paper requirement.
Use a separately qualified TeX sandbox with only public source/dependencies and
write its `paper-build-review.v1` record. If unavailable, keep that check blocked;
do not silently switch the user's selected paper mode.

Review the whole history intended for publication, including authors/messages
and tags. If private content was accidentally committed but never published,
recreate the job-owned public repository from the approved tree. Never repair
this by merely adding a deletion commit, deleting original source, or automatic
force-push. Previously published exposure requires an explicit incident decision.

## J8–J10: final verification and executable readiness

Final verification sees only public candidate source and admitted pinned
dependencies, never private baseline outputs or caches. Incremental builds are
useful during authoring; candidate `.olean` files are not final evidence.
Run the existing Lax verifier with frozen concepts, exact targets and dependency
requests. Record an actual independent semantic review through its existing
request contract; CI always leaves that judgment pending.

Use schemas in `canonical/schemas/lax/`:

- `workflow-job.schema.json`: **final readiness request**, not an autonomous
  scheduler. Budget/progress/reuse decisions remain in this runbook's job brief.
- `paper-correspondence.schema.json`: actual paper id/version/hash, public commit,
  source/concept/scope/dependency bindings, producer and different reviewer run.
- `public-review.schema.json`: reviewed source digest, public origin and history
  tip; status after actual source/history/metadata/CI-effects inspection.
- `paper-build-review.schema.json`: actual isolated compilation and render review
  bound to source and paper, with toolchain/output digests (when TeX is requested).
- `public-source-plan.schema.json`: private selection and approval contract.

Put controller records outside original source, staging and public repo. A
reviewer name or hash does not authenticate a human judgment; the controller
must retain provenance of the actual independent review. Candidate-owned records
are not accepted. No copied `accepted` flags or machine-only semantic approval.

```bash
python3 -B canonical/runtime/skills/lax-formalization/workflow_check.py snapshot \
  --project /path/to/new-public-repo --out /path/to/controller/public-inventory.json
python3 -B canonical/runtime/skills/lax-formalization/workflow_check.py readiness \
  --job /path/to/controller/job.json --out /path/to/controller/readiness.json
```

The job's `verification`, `correspondence`, `privacy_review`, `public_inventory`,
optional `paper_build` and `paper.path` are relative controller file references.
Readiness checks actual clean HEAD/origin, inventory, current executor identity,
frozen concept/target hashes, accepted proof closure and both review bindings.
Changing the paper/source/scope/pins invalidates stale evidence. Any missing check
is blocked, never passed. Only `local_ready` can be emitted; no command publishes.

Select evidence files for disclosure, not an entire logs directory. Privacy
clearance must precede the **first public push**: CI is already an outward action.
The template uploads a bounded public machine summary on success only. Its
optional bundle check enforces a fixed file set and refuses raw diagnostic
fields; it does not certify source privacy after the source has been pushed.

For Zenodo, prepare/validate/restore the exact public revision using the existing
runtime. Do not modify ZIP contents or redact a receipt after verification;
regenerate evidence from a clean public context. A ZIP omits Git history; restored
Git commits are synthetic and require new evidence. Keep Zenodo `not_requested`
unless selected; it does not block a complete Lax lane.

## Paper family and artifact versions

Use `paper-versions.json` (`paper-versions.v1`) for bibliographic entries: exact
arXiv versions, conference/journal versions and corrections. Relationships require
evidence; similar titles do not prove two works are versions of each other.
No correspondence flag is accepted in this public candidate-controlled registry.
`render-papers` defaults each entry to `not-reviewed`; an optional controller
review file can supply accepted, hash-bound scope/commit records.

```bash
python3 -B canonical/runtime/skills/lax-formalization/workflow_check.py render-papers \
  --registry /path/to/new-public-repo/paper-versions.json \
  --out /path/to/controller/readme-paper-section.md
```

Review the generated section, then integrate it into README in a new commit.
That commit does not change an older Lax source commit or automatically create
a release. Never move artifact tags or substitute latest/main for a pinned
commit. Evidence for commit A cannot certify metadata-only commit B; package A
from an ordinary checkout of A with its own receipt when that is the intended
artifact. Software citation metadata must not be replaced with a paper's DOI.

Local `lax init` assigns an ID without remote registration. First submit can
add an issue binding or renumber a collision: after separately authorized remote
work, review the resulting changes, commit and reverify before content submit.
Registered updates use a fresh ID/namespaces and `supersedes`; check the current
owner/predecessor chain before proposing the operation. Do not call `lax update`
to version a paper; it upgrades the CLI. Do not submit to obtain a test build.

Keep an artifact-version ledger mapping paper revision/scope, source commit,
Lax ID/state and optional Zenodo version/concept DOI. Pending identifiers are
pending, not receipts. Append observed publication IDs in a separate bookkeeping
commit; don't rewrite the already-verified artifact. Paper-only editorial changes
may retain a formal artifact only after renewed correspondence review. Proof-only
updates may retain the paper version. Embedded paper source changes are artifact
changes. Zenodo file updates normally use a new linked version; metadata-only
edits are different. Recheck service rules at the later publication boundary.

## Recovery and final handoff

Checkpoint phase, input hashes, decisions, budget spent, blocker and next action.
Resume only matching checkpoints. On missing tools/opaque syntax/budget exhaustion,
stop with the unresolved check and preserve private recovery data. Never clear a
blocker by reducing the declared scope silently or extending execution rights.

Deliver the public commit, reviewed scope/reuse summary, readiness/evidence,
reproduction instructions, limitations and a proposal-only publication plan.
Report machine, closure, correspondence, privacy, paper-build and optional
archival statuses separately. A reference benchmark establishes only its tested
subset; reuse of its proof is not blind independent re-derivation.
