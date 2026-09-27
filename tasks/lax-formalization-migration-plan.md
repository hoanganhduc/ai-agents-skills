# Lax Formalization Migration: Agent Execution Plan

Status: implementation handoff; no implementation or publication has been run.
Prepared: 2026-09-27.
Repository baseline: `c2b68de26e8ed4f90e956107855624bf5bedf5db`.
Authority and source IDs: [evidence register](lax-formalization-sources.md).

## 1. Start here

This plan replaces the personal-library-centered formalization workflow with
Lax-compatible paper projects. Mathlib and independently checked Lax results
provide reusable mathematics. Zenodo remains an optional secondary archive.
Automatic verification, packaging and documentation remain supported.

An implementing agent must read this plan and the evidence register, then the
current repository instructions and affected files. This document describes
future work; names marked **new** are not installed commands or files yet.
Do not execute a command merely because it appears in a future publication
section. Obtain implementation authorization before starting T0; this document
itself was requested as planning work.

The plan is executable by one agent in task order. If delegation is authorized,
give each worker explicit file ownership, preserve other workers' edits, and
recombine before running integration checks. A proof solver in the benchmark
has a deliberately smaller input packet than the implementation agent.

### User requirements that must survive handoff

- R1: Paper-formalization requests default to Lax-compatible output. Reading or
  reviewing a paper alone does not authorize formalization.
- R2: Archive membership, registration, endorsement, a DOI or a green CI badge
  never substitutes for our independent verification.
- R3: Retire the obligation to build or feed HoangMathLib. Preserve its source,
  history and any old project's reproducible dependency pins.
- R4: Preserve Zenodo as a second option independent of the retired skill.
- R5: Preserve useful GitHub Actions checks, reports and cache support.
- R6: No publication during implementation or testing: no Lax submit/register,
  remote issue/comment creation, remote repository creation, push, GitHub
  Release, Zenodo draft/DOI reservation/upload/publish, or Pages deployment.
  Sandbox Zenodo writes are also excluded until separately authorized.
- R7: Test by independently formalizing a small subset of an existing submitted
  repository, freezing the candidate, then comparing it with the reference.
  Do not reproduce the entire reference paper.
- R8: Complete the other components before that benchmark. Publication is a
  separate later operation and is not implied by successful tests.

In scope: canonical skills, runtime contracts, ARL integration, shared paper
template, offline Zenodo preparation, documentation, tests and staged retirement
of existing installed rules. Out of scope: bulk migration/formalization of old
papers, deleting research repositories, new remote compute provisioning,
changing the Lax service, and live publication.

## 2. Decisions already grounded in documentation

| Decision | Implementation consequence | Evidence |
|---|---|---|
| Use the official Lax CLI as the submission validator | Do not fork its submission schema or maintain a second scaffold generator | S1–S6 |
| Keep one source repo with a nested `submission/` | CI, scripts and citation metadata stay outside concept/proof packages | S1, S6, S14 |
| Use version-specific policy | Begin with Lax 0.1.48 and environment v4.33.0; record tool/spec hashes and re-evaluate deliberately on upgrade | S1–S3 |
| Revalidate relevant Lax results from pinned sources | Discovery results start unverified; recorded upstream success is provenance only | S5, S18 |
| Preserve the generic Lean gate | Add an explicit Lax backend; do not globally allow `axiom` or all `Lax*` imports | S21 |
| Separate semantic acceptance from machine acceptance | A human/independent review connects the Lean statement to the intended claim | S2, S18 |
| Default Zenodo delivery is an explicit local bundle for later manual upload | No GitHub integration or remote mutation is needed to complete this milestone | S8–S13 |
| CI checks exact identities | PR-merge evidence and release-commit evidence have different identities | S14 |
| Support state-based retirement before removing the old catalog selector | Direct upgrades must still be able to scope uninstall/rollback to recorded old artifacts | S24 |

The initial toolchain is `leanprover/lean4:v4.33.0`, with mathlib
`db584cd6d46c92f209a44c0f1c829460d327499d`. These are inputs to the initial
compatibility profile, not permanent hard-coded defaults for every future
environment. Detect unsupported versions explicitly; never silently change pins.

## 3. Target architecture

### Skills and files

| Component | Ownership/change |
|---|---|
| `canonical/skills/lax-formalization/` **new** | `SKILL.md` plus focused references for reuse verification, authoring, benchmark, CI and publication boundaries |
| `canonical/runtime/skills/lax-formalization/` **new** | Small stdlib orchestration/evidence helper and POSIX/PowerShell launchers; official Lax performs validation |
| `canonical/skills/zenodo-artifact/` **new** | Independent optional archival preparation, metadata and later manual/API publication guide |
| `canonical/runtime/skills/zenodo-artifact/` **new** | Offline bundle/metadata validation and publication-plan generation; live verbs remain unavailable in this milestone |
| `lean-formalization-intake` | Add declared format, target set, dependency and environment requirements |
| `formal-skeleton-helper` | Keep generic stubs; route Lax workspace creation through `lax init` |
| `lean-strict-verification-gate` | Add Lax-specific machine-evidence adapter while preserving generic rejection behavior |
| ARL skill/runtime/templates | Replace old reuse/intake instructions and migrate all evidence producers/consumers |
| `lean-explore-mcp`, `axiom-axle-mcp`, `opengauss` | Keep optional assistance and existing evidence limits; no new auto-launch authority |
| `lean-research-library` | Transitional only until retirement; no new mandatory use |

The new Lax helper has a deliberately bounded command contract:

| Planned command | Effects and result |
|---|---|
| `doctor` | Offline readiness JSON; no installation, login or update |
| `search --query Q --database D --environment E` | Read a pinned local catalog; return candidates, provenance and coverage limits; never certify a result |
| `verify-dependency --request R --out O` | After reviewed fetch/provisioning, independently check the request's selected closure in the approved executor; write evidence under O |
| `verify --request R --out O` | Full local submission verification in that executor, including independent dependency evidence |
| `publication-plan --request R --out O` | Write a non-executable proposal bound to source identity; no remote calls |

Actual CLI authoring uses `lax init` and `lax build`; do not add aliases for every
Lax verb. No generic `run`, arbitrary shell argument or caller-selected backend
executable is accepted by the helper. Unknown options fail rather than being
accepted as inert settings. Use JSON exit semantics: 0 for a completed query or
passing required checks, 1 for failed/blocked required verification, 2 for invalid
requests/unsupported operations. A successful search process may return zero
hits or incomplete coverage without claiming absence of a theorem.

The new Zenodo helper provides `prepare`, `validate`, and `publication-plan`.
`prepare` writes the explicitly selected output directory; `validate` is
read-only; the plan describes later service steps. `publish`, `upload`, and
draft-creation requests fail with an explicit not-enabled reason. Do not retain
the old skill's misleading suggestion that draft creation is full publication.
No real credentials are needed by either new helper in this milestone.

### Shared paper template

Add a template under `canonical/templates/lax-paper-artifact/` and register it
using the repository's existing artifact mechanism, not a second installer.
The template supplements `lax init`; it does not ship guessed generated IDs.

```text
paper-repo/
  .github/workflows/verify.yml
  .github/workflows/package.yml
  scripts/verify.sh
  scripts/package.py
  README.md
  LICENSE
  CITATION.cff
  .zenodo.json                  # only when its extra fields are required
  submission/
    manifest.yaml
    abstract.md
    LICENSE
    concepts/
    proofs/
    paper/                      # optional, explicitly licensed content
```

Verification reports and bundles go to an ignored output folder or outside the
repo. The required submission license must be a real file, not a symlink to the
outer license. Extra metadata is not inserted into Lax's closed manifest schema.
Use one reviewed metadata model to check consistency of title, formalizer
credit, version and license across the two publication paths. Credit the
original paper separately from the formalization's authors.

## 4. Verification and reuse contract

### Separate facts that the old workflow combined

Every result records these independent dimensions:

- `discovery_status`: candidate, unavailable, or not-found-with-scope;
- `source_status`: pinned-and-matched or not-verified;
- `machine_status`: passed, failed, or not-run;
- `closure_status`: closed, open, cyclic-without-foundation, or unknown;
- `semantic_status`: accepted, rejected, or pending;
- `publication_status`: local, draft, registered, or archived-on-zenodo.

Publication status never changes the other fields. A concept definition without
a proof obligation has an explicit `not_applicable` proof field, rather than an
invented proof. Mathlib/Lean at the selected, reviewed pins form the declared
background trust base; results imported from Lax require separate evidence.

### Independent verification procedure

1. Freeze an evaluator-authored challenge, its definitions, intended statements
   and informal meaning before proof work. Keep these immutable inputs outside
   the candidate's writable namespace. Record allowed abstractions and excluded
   claims. Search the selected mathlib
   environment first, then relevant Lax candidates. A search outage does not
   authorize a claim that no reusable result exists.
2. Validate the selected record against a pinned database snapshot, then fetch
   its exact source revision through a reviewed, bounded read-only path.
   Resolve all relevant source/statement/proof dependencies. Validate origin,
   paths, source hashes and environment consistency before executing anything.
   Fetch and extract without credentials in a disposable workspace, not on the
   host's general filesystem. Apply the archive-admission rules below to source
   and dependency provisioning as well as to final bundle recovery.
3. Rebuild the relevant Lax dependency sources in a disposable build environment.
   Do not count a downloaded `.olean`, capture digest or upstream checkmark as
   independent replay. Reuse our own earlier evidence only when its complete
   source/tool/dependency fingerprint still matches.
4. Terminate compilation and its descendants, then seal a digest-bound snapshot
   of its outputs. Run the pinned kernel replay and inspect that same read-only
   snapshot in fresh checking environments. Check
   target coverage, actual axiom sets, conclusion/type correspondence and
   namespace/import contracts. Background axioms are the reviewed environment's
   set; `sorryAx` and compiler-trust axioms never become accepted merely because
   their producer was a registered submission.
5. Construct the proof-obligation graph from checked evidence. Only use verified
   witnesses compatible with the selected environment. External witnesses for
   a publication-ready dependency must be registered; the candidate's own local
   proofs are handled separately. Compute grounding from independent bases;
   cycles do not ground themselves, while a cycle with a valid independent
   witness need not be rejected wholesale.
6. Compare the compiled target type and relevant definitions with the immutable
   challenge in a trusted checking context. A different representation needs a
   checked correspondence, not a comparison of pretty-printed strings alone.
   Independently review the formal statement, definitions and assumptions
   against the intended claim. A machine pass with pending semantic review may
   support only the formal statement, not the informal paper claim.

The public `generate-prooftree` command is supplemental, not the pre-submit
verifier. Its current selector sees drafts as well as registered work. Filter
eligible witnesses before the new closure calculation; do not simply reject its
chosen draft after selection when another verified registered witness exists.
Do not write synthetic records into the user's real Lax database to make a
local candidate appear registered. If an internal Lax function is reused, bind
it to the supported CLI version and add an explicit API-drift test.

The release-quality local check runs a new, full, strict `lax build --replay`.
It rejects partial/nonstrict evidence, stale outputs, missing target modules,
source changes during the check and unchecked dependency closures. Resolve
the pinned `leanchecker` through the Lax backend: do not pretend the existing
generic helper's `lean4checker` interface is interchangeable.

### Execution boundary

The initial executor backend is local Docker, under a reviewed unprivileged
container policy; no new remote compute service is required. Detect availability
and resources first, and report a blocked execution when unavailable. Do not
claim native Windows support from WSL or simulated installation tests.

Use a reviewed disposable executor: isolated home, no host credentials, agent
sockets or container-engine socket, no broad home/repo mounts, explicit resource
limits and disposable writable storage. Shared trusted toolchain/mathlib inputs
are mounted read-only. Prepare public downloads in a separate fetch phase;
disable network during compilation/replay/inspection. No credential variables
or real `~/.lax/credentials.json` enter this environment. Do not use privileged
containers. If this boundary cannot be established, report blocked execution;
never silently fall back to running retrieved Lean on the agent's host.

Compile, replay and inspection must not share a live writable artifact tree.
After compilation, terminate all descendants and admit a sealed output inventory.
Replay and inspection consume its read-only bytes; compare digests before/after
each check. Store checker results in a supervisor-owned location inaccessible to
the compiler. A missing teardown guarantee or changed artifact invalidates the
check. Running a command named `--replay` does not itself prove this separation.

Implementation route: keep the public full-build command as an authoring and
compatibility check. For the independent certificate, add a small reviewed
`lax_adapter.mjs` beside the stdlib helper. Bind its imports to the admitted Lax
package/version and reuse upstream capture, replay, inspection parsing and
judgment code. Relevant inspected entrypoints include
`validateSubmissionOnHost`, `replayPackage`, `runInspector`,
`parseInspectorReport` and `judgeInspection` (S28). The supervisor controls
request, inventory, resolution, job directory and phase runner. It discards
compiler-owned reports as authority and repeats the checks over the sealed
capture in fresh environments. There is no invented public `lax replay-only`
flag. A contract test must detect drift in these internal interfaces before
executing retrieved code; adaptation is version-scoped rather than a validator
fork. Qualify this route in T2 before investing in the benchmark.

The official Lean guide describes stronger trusted-challenge/export checking,
including Comparator (S18, S27). The adapter must record which checks actually
ran. Do not claim complete protection against adversarial source or malformed
compiled files merely from a kernel replay. If Comparator is selected for a
stronger assurance tier, first qualify compatible tool revisions and its sandbox
prerequisites; never substitute an unavailable checker with an undocumented pass.
No universal Comparator compatibility with the selected Lax version is asserted
by this plan.

Every archive admission rejects traversal, absolute names, special files,
symlink/hardlink escape, duplicate entries and platform-colliding paths. Bound
download bytes, expanded bytes, entry count, depth and individual file size.
Validate extraction into an empty disposable directory before exposing sources
to the next phase; these limits also apply before compilation begins.

Use a sanitized Lax home with a pinned database and reviewed dependencies.
Confirm against the supported CLI that it can run with those preprovisioned
inputs and without refreshing them. Treat an unavoidable network request or
unexpected shared-cache write as an integration issue to resolve, not permission
to broaden the sandbox. A read-only warm store may need explicit provisioning
before the validation phase; do not make it writable to submission code.

### Evidence files and ARL integration

Define one versioned `lax-verification.v1` JSON schema and test it before
implementing consumers. Minimum fields:

| Group | Required binding |
|---|---|
| Identity | backend, schema version, run ID, relative submission root, intended target IDs, scope digest, immutable challenge/definition digests |
| Source | source commit when present, exact source/config tree digest, selected files and hashes |
| Tools | Lax version/integrity/spec digest, Lean version/checker identity, mathlib SHA, executor image/config identity |
| Database | snapshot commit and content digest; source records and selected witness IDs |
| Dependencies | exact source pins, artifact/source correspondence, independent-check evidence digests and relevant closure |
| Results | phase execution/status, warnings, replay coverage, computed axiom sets, open obligations, semantic review reference |
| Integrity | pre/post authored-input digests, sealed generated-output inventory, compile teardown evidence, checker phase identities, process exits, bounded transcript references |

Use canonical relative paths, deterministic set ordering and bounded fields.
Do not include credentials, machine-specific home paths or raw environment dumps.
The host verifier produces/rechecks evidence; the agent cannot promote an
editable report to a certificate simply by changing its status string.

Lax has two Lake roots and generates manifests/overrides. Extend ARL project
detection accordingly. Separate authored immutable inputs from host-generated
build outputs: allow the pinned CLI to generate the latter in the controlled
workspace, without giving the proof agent general build-config edit authority.
Hash and validate generated resolution separately.

Migrate all consumers, including `resolve_formal_project`, allowed force verbs,
`evaluate_formal_terminal_state`, `reverify_formal_evidence`, success admission
in `autonomous_research_loop_runtime.py`, and the final `goal_focus.py` path.
Keep the meaning of the existing terminal states where possible:
`sorry_free_artifact` requires closed targets under this backend;
`open_ledger` honestly lists gaps; `indeterminate` covers missing evidence.
Extend the versioned certificate binding rather than weakening old consumers.
Old generic Lean runs must retain their existing rejection behavior.

## 5. CI and archival behavior

### Verification workflow

`verify.yml` runs on push, pull request and manual invocation, with read-only
permissions and no publication secrets. Pin reviewed action revisions and the
Lax package version. Disable checkout credential persistence. Never execute PR
code with privileged `pull_request_target` behavior.

The job provisions reviewed inputs, invokes the same controlled verification
entrypoint used locally, and uploads bounded reports. It covers both packages,
metadata, proof obligations and freshness. A semantic review can be carried as
an input binding; CI must not invent human acceptance.

Record both the event's revision and the actual checked-out commit. PR merge
checks are integration evidence, not release evidence. A later packaging or
publication decision must rerun/admit evidence for the exact proposed source
SHA. Partial path filters must not skip manifest, abstract, metadata, workflow
or dependency changes relevant to the certificate.

Keep cache keys environment/tool/source aware. Never save credentials or all of
Lax home in a cache, and never treat a PR-written cache/artifact as trusted
release evidence. Keep any proposed upgrade probe separate from required checks;
it follows admitted Lax environments, not arbitrary latest mathlib. Pages/docs
may be built locally, but deployment is excluded by R6.

### Bundle and recovery

`package.yml` and the local packager have no publication capability. They create
an explicitly enumerated bundle from a checked revision, not a recursive copy
of the working directory. Suggested contents:

```text
source.zip
metadata.json
provenance.json
verification-summary.json
dependency-inventory.json
REPRODUCE.md
SHA256SUMS
```

The complete certificate/transcripts can be additional explicitly reviewed files.
Do not include `.git`, credentials, caches, private logs or unrelated working
copies. Preserve license notices for included third-party source.
Default bundle class is `source-and-evidence`, requiring the declared toolchain
and, where specified, network retrieval of pinned dependencies. Do not call it
offline/self-contained unless an additional dependency bundle was built and
tested for that purpose. Large toolchains/mathlib caches are not bundled by
default.

For recovery tests, validate the archive inventory before extraction; reject
traversal, absolute names, symlinks and special entries. Use a new directory.
A source ZIP has no Git context. Initialize an isolated local Git workspace if
Lax needs it, and record its new test commit separately from the original source
SHA. Verify the exported bytes against the original inventory; do not claim
the synthetic commit is the original published commit. Re-provision exact
dependencies, rebuild, and compare scope/check results.

### Zenodo preparation and later options

The milestone implements offline preparation and validation, plus a future
publication runbook. The default eventual secondary route is manual upload of
the reviewed bundle, so archived contents are explicit. GitHub integration is an
optional later route for a source-release archive; it stays disabled now.
Validate `CITATION.cff`; add `.zenodo.json` only where its additional metadata is
needed and reject mismatches between the two. Do not reuse the original paper's
DOI as the formalization software's identity.

For a later separately authorized API implementation, S11 establishes this
sequence; it is not an execution instruction for this milestone:

| Step | Documented interface |
|---|---|
| Create draft | `POST /api/deposit/depositions`, JSON body, `deposit:write` |
| Add metadata | `PUT /api/deposit/depositions/:id` |
| Upload | Stream each file to the returned `links.bucket` URL plus filename |
| Verify draft | Re-read metadata/files and compare the intended inventory |
| Publish | Separate `POST /api/deposit/depositions/:id/actions/publish`, `deposit:actions` |
| Confirm | Re-read the resulting published record and exact files; do not equate an accepted asynchronous request with completed archival |

Keep bearer tokens out of URLs/logs; validate returned upload destinations before
forwarding credentials. Sandbox and production are separate environments.
Do not blindly retry a draft-creation/publish request after an ambiguous response;
reconcile recorded IDs and state first. Current service guides and old API text
disagree on post-publication modification policy: rely on neither deletion nor
editing as rollback. No account-specific API behavior is claimed tested here.

### Later Lax publication boundary

Only after a new explicit publication request: review the destination, public
payload, source revision and permitted stages. The first submit can bind an issue
and change the manifest; commit/push and reverify that new revision before the
content submit. Draft submission and registration are separate permitted stages.
Future Zenodo release tags must point to the final approved revision, not a moving
branch. Record DOI/Lax-ID mapping without moving tags or rewriting a registered
source to add metadata after the fact. GitHub Release creation is a publication
boundary when Zenodo integration is enabled; release-event CI is not a preflight.

## 6. Concrete local benchmark

This replaces the earlier publication pilot. Nothing in this section submits,
registers, pushes or reserves an identifier remotely.

### Reference and bounded scope

- Record: `lax-345067`, Finite Ramsey Theorems for Pairs and Tuples.
- Repository: `https://github.com/lax-archive/lax-submissions`.
- Commit: `feabf43dcd6967ac912b2064003ea715e06e58f9`.
- Folder: `finite-ramsey-v4-33`.
- Reference definition: `concepts/Lax345067/OrderTypes.lean`.
- Reference comparison definition: `otp` in
  `proofs/Lax345067Proofs/TupleCore.lean`.
- Target: `otp_eq_of_orderType_eq` in
  `proofs/Lax345067Proofs/TupleRamsey.lean`.

The target says that tuples with the same strict-order pattern have the same
three-way comparison pattern. The candidate should formalize those definitions
and that one lemma, with at most two purposeful helper lemmas, in a local Lax
submission. Full Ramsey theorems, hypergraph induction and the reference's other
results are explicitly excluded. This is a workflow benchmark, not a claim of
research novelty or broad theorem-proving capability.

Reference file SHA-256 values, obtained read-only:

| File | SHA-256 |
|---|---|
| `concepts/Lax345067/OrderTypes.lean` | `33e3eac6e291c241a2e15bcc55ddb63e53b3c530f5769eeab9d0d99892ced2fd` |
| `proofs/Lax345067Proofs/TupleCore.lean` | `ce9a4805aca58928bb319a2ca59d338676aaecced8558c4cb8cef6a1a593fee5` |
| `proofs/Lax345067Proofs/TupleRamsey.lean` | `50fa644e1d2119a82679ba84486b572d0d323f5c443a2d1e59ef9ad1db2ecd78` |

### Evaluator and solver separation

1. The evaluator verifies the reference bytes, selected definitions and theorem
   in the controlled environment. A faithful minimal extraction is permitted:
   retain the selected theorem's statement/proof bytes, record the extraction
   patch and reviewed minimal imports, and check correspondence of definitions.
   Label evidence as selected-subset verification, not certification of the
   whole reference repository. If faithful extraction is not possible, stop
   and revise the bounded scope; do not silently expand to the entire paper.
2. The evaluator prepares a minimal solver packet: mathematical statement,
   fixed definitions/API, allowed background imports, scope and expected output
   format. Remove proof bodies, strategy comments, proof-bearing documentation
   and links to answer files. Do not give the solver this full plan or source
   register; the planning context has already seen the reference proof.
3. Use a fresh solver context and a workspace without the reference checkout or
   unrestricted network/search access to it. The solver writes its own local
   concepts/proofs using the pinned mathlib background. It must not import the
   target or an equivalent/stronger reference theorem indirectly through another
   module, instance or tactic. Record permitted inputs and reuse honestly; this
   separation is not a guarantee about a model's training data.
4. Freeze candidate source and verification inputs before exposing the reference
   to the comparison step. Verify both sides using the same acceptance policy.
   Differences in namespace or representation require an explicit correspondence
   check; differences in proof strategy are acceptable.
5. Compare statement strength, hypotheses, definition meaning, proof closure,
   module coverage, Lax layout/annotations, independent replay and bundle
   recovery. Write a gap table with evidence, classification and the smallest
   required skill/runtime correction. Unselected reference results are not gaps.

A solver-only proof task can use the permitted host delegation after authorization;
otherwise a new top-level agent session can consume the same minimal packet.
Do not reuse the evaluator's context as the supposedly independent solver.

Live cross-submission import coverage is not implied by this small standalone
candidate. Test dependency selection/closure with fixtures and independently
check the selected real reference result. Report that distinction rather than
expanding the proof task. A later live dependency-import benchmark can be a
separately scoped extension.

## 7. Ordered implementation tasks

Each task requires the preceding dependencies, a scoped diff, evidence of its
acceptance checks, and an updated handoff entry. Implementation commits can be
local if authorized; this plan does not authorize pushing them.

### T0 — Baseline and migration inventory

Dependencies: implementation authorization. Ownership: inventory/plan only.

- Read current instructions, `skill-creator`, lifecycle docs and manifest schema.
- Record repository state and pre-existing relevant test failures.
- Enumerate actual installed consumers, runtime roots, old config/environment
  pointers and manual rules. Read selected state fields, not raw secret files.
- Find real project imports/requires of HoangMathLib, including beyond the
  previously inspected local research tree when those projects are in scope.
- Preserve affected operator-owned files and installer state before any cutover.
  Record hashes and restoration scope in a private migration record.

Acceptance: exact change inventory, protected-data list and rollback inputs;
no claim of universal absence of downstream users from a limited grep.

### T1 — Contracts and failing regressions

Dependencies: T0. Ownership: schema/fixtures/tests.

Create schemas for reuse candidates, verification requests/certificates and
publication plans. Define closed enums, bounded fields and allowed source roots.
Add the regression matrix in section 8 before implementing the new behavior.
Keep untrusted fixtures inert except within the deliberately isolated test runner.

Acceptance: new behavior tests fail for the intended missing capability;
pre-existing generic gate tests still distinguish placeholders and trust growth.

### T2 — New skills, discovery and controlled verifier

Dependencies: T1. Ownership: new Lax skill/runtime plus Lax gate adapter.

Implement the bounded interfaces in sections 3–4. Add references instead of
copying the entire evolving Lax specification into the skill. Implement local
catalog search, pinned request resolution and the credential-free executor.
Make independently verified reuse the only route from a discovered Lax result
to accepted dependency evidence. Preserve the generic gate's old defaults.

Acceptance: positive and negative fixtures; a checked executor-boundary test;
unsupported CLI/database formats fail visibly; no publication verbs are present.

### T3 — Shared template and local CI entrypoints

Dependencies: T2. Ownership: template, scripts and workflow templates.

Supplement official Lax scaffolding with the shared repo layout. Preserve useful
build/coverage/report/cache behaviors while removing private-library checks.
Implement exact-commit verification and packaging entrypoints. Keep workflows
as templates: no remote workflow execution or Pages/release deployment now.

Acceptance: schema/static checks and local execution of the exact scripts the
workflow would invoke; no nested-package/root-package ambiguity, publication
trigger or credential-bearing build context.

### T4 — Independent Zenodo preparation

Dependencies: T1, T3. Ownership: new Zenodo skill/runtime and archival tests.

Implement explicit file selection, metadata validation, checksum inventory and
manual-upload/API proposal generation. Extract useful old guidance; remove its
library dependency and stale absolute policy claims. Offline commands never
load secrets. Because live calls are not enabled, do not grant a new credential
projection to a helper that does not need one.

Acceptance: metadata disagreement, placeholder, unsafe archive entry and missing
file tests fail correctly. Bundle round-trip validates source bytes and reports
its actual reproducibility class. Live publication requests are refused.

### T5 — ARL and routing integration

Dependencies: T2–T4. Ownership: existing consumers and related tests.

Update the full producer/consumer graph in section 4, both current runbooks,
the formal policy instruction, `agent-group-discuss/TEMPLATES.md`, and relevant
intake/source/deep-research routes. Default new paper-formalization work to Lax;
preserve generic Lean verification for existing work. Remove library search and
intake force verbs and replace only with the new bounded nonpublishing ones.

Acceptance: final re-verification rejects changed source/database/dependencies;
open or unknown work cannot become a success stop; plain research and old Lean
runs do not acquire a new mandatory Lax operation.

### T6 — Bounded independent benchmark and gap correction

Dependencies: T2–T5 complete. Ownership: evaluator; separate solver input.

Execute section 6, including negative controls and bundle recovery. Follow the
existing compute-resource policy before any heavy build. Do not let a resource
failure become a false theorem result or a reason to publish for remote testing.
Fix only defects exposed in the agreed integration scope, then repeat affected
checks. Preserve the initial failed cases as regressions.

Acceptance: benchmark report states exactly what was reproduced and independently
checked; all required negative controls fail for the intended reason. No remote
state changed. This T6 is a local benchmark, not the cancelled publication pilot.

### T7 — Catalog, docs and staged retirement

Dependencies: T6. Ownership: manifests, installer integration and scoped homes.

Before deleting the old selector, implement lifecycle-only resolution against
the selected root's installer state. `uninstall` and `rollback` may accept an
exact skill name absent from the active catalog only when it has matching
recorded managed artifacts. All existing root, signature, ownership, conflict
and shared-consumer checks still apply. This does not make an unknown skill
installable, does not accept arbitrary paths, and does not authorize journal
editing. Keep active `plan`/`install` selection catalog-based. Test this path
against an old installation using the final checkout, skipping any transition
release. Catalog deletion is blocked until that test passes.

Update these canonical surfaces together:

- `manifest/skills.yaml`, `runtime.yaml`, `profiles.yaml`, `artifacts.yaml`;
- `manifest/credential-runtime.json`, `canonical/runtime/runners/run_skill.sh`
  and `run_skill.ps1` for removal of the obsolete Zenodo route;
- the affected stdlib/wrapper/runtime/static/credential tests;
- `installer/ai_agents_skills/cli.py` lifecycle selector resolution and the
  corresponding state/lifecycle tests, without relaxing installation selection;
- generated docs through `installer/ai_agents_skills/docs.py` and `make docs`;
- all existing formal-research consumers identified by T0.

Use a two-stage transition. First make the new skills installable and verified
while keeping the old catalog selector solely for retirement. Do not install
the old skill again via broad profile selection. Then:

1. Preview and install the approved new/changed scope into fake roots.
2. Demonstrate install, retirement, reinstall and rollback there.
3. With authorized live scope, preview retirement while the old selector still
   resolves. Compare current file hashes to T0's reviewed inputs; stop on drift.
4. Gently cut over the selected installed consumers, remove managed old files
   through the installer, and make exact edits to manual rules outside managed
   blocks. Deactivate the old config/env pointer without deleting the library.
5. Verify every selected target and both actual runtime roots; only then remove
   the old canonical skill/runtime/catalog entry and regenerate docs.

Installations encountered after catalog removal use the state-based retirement
path established above. Never ask an agent to edit the installer journal
manually or use `rm -rf` on skill homes.
No compatibility alias may silently route a new task back to private-library
staging. Historical logs/commits and this migration record may retain the old
name. Unrelated user files and modified managed files are preserved for review.

Acceptance: a second install does not resurrect old mandatory rules; new profiles
work without a library config; no broken runtime reference remains; rollback
does not overwrite user edits made after cutover.

### T8 — Final delivery

Dependencies: T7. Ownership: lead agent.

Run focused checks, generated-doc checks and the required broader repository
checks. Obtain bounded independent code/security reviews. Report changed files,
actual target/OS coverage, benchmark scope, pre-existing failures and any blocked
inspection. Stop with a usable local workflow and archival bundle. Do not append
an automatic publication phase.

## 8. Required regression matrix

| ID | Case | Required observation |
|---|---|---|
| V01 | Registered result whose target has no proof | Candidate discovered; reuse acceptance refused |
| V02 | Proof with imported `sorryAx` or compiler-trust axiom | Rejected even if the current file has no placeholder token |
| V03 | Matching source with correct closed proofs | Machine pass scoped to the inspected targets; semantic review remains separate |
| V04 | Target/source/capture or environment mismatch | Rejected before accepting the dependency |
| V05 | Ungrounded two-node proof cycle | Open/cyclic result, never closed |
| V06 | Cycle with an independent verified base proof | Valid grounded witness selected |
| V07 | Competing draft and registered witnesses | Registered eligible witness used; no accidental reliance on draft |
| V08 | Search unavailable or incomplete | Coverage disclosed; no global no-result claim |
| V09 | Concept axiom versus stray proof axiom | Lax concept handled by its contract; stray/unsupported proof assumption rejected |
| V10 | Weakened statement or altered definition that still compiles | Semantic comparison rejects the mismatch, not merely a syntax failure |
| V11 | Missing module/partial build/stale `build-output.json` | No success certificate |
| V12 | `--nonstrict` or source modification during verification | Not release-quality evidence |
| V13 | Source, DB, dependency or tool changes after certificate | ARL final re-verification refuses stale acceptance |
| V14 | Two package roots and regenerated manifests | Expected host generation allowed; unauthorized authored config edits rejected |
| V15 | Attempted credential-file/socket/network access | Controlled executor prevents access; no host secrets used in the test |
| V16 | Generic Lean-mode axiom/native reduction regression | Existing strict behavior preserved |
| V17 | PR merge revision differs from release SHA | Evidence identities stay distinct; release admission requires its own SHA |
| V18 | CFF/Zenodo metadata mismatch or unresolved placeholders | Packaging validation fails |
| V19 | Bundle unsafe path, symlink, missing hash or source mismatch | Safe extraction/admission refuses it |
| V20 | Bundle restored without original `.git` | Documented reconstruction works; synthetic and original identities remain distinct |
| V21 | Reference proof or equivalent target leaks to solver | Benchmark marked contaminated; restart only that proof attempt with a clean packet |
| V22 | Live Lax/Zenodo/release operation requested during test | Refused before network mutation; fixtures assert zero outbound publication calls |
| V23 | Old skill removed from profile then reinstalled | Old route/manual mandatory rule does not return |
| V24 | Modified user file encountered during retirement/rollback | Preserve and report conflict; no overwrite or broad cleanup |
| V25 | Compiler descendant attempts to change artifacts between checks | Teardown/sealed handoff prevents it, or certificate admission fails |
| V26 | Fetched source/dependency archive has duplicate, colliding or oversized entries | Provisioning refuses it before host exposure or compilation |
| V27 | Old installation upgraded directly to final catalog without transition | Scoped state-based uninstall/rollback works; modified files survive; unknown installation names remain refused |

The evaluator introduces negative controls independently of the checking actor
and records expected failure reasons. A malformed file that merely fails to
compile does not test detection of a weaker mathematical claim.

## 9. Command cookbook

Run from the repository root. Commands in this section were checked against the
current helper/help surface, but commands naming **new** skills/tests are for the
corresponding completed task, not for the current unimplemented checkout.

Baseline/read-only checks:

```bash
git status --short
git rev-parse HEAD
./installer/bootstrap.sh --help
./installer/bootstrap.sh --json plan --skills lean-research-library
./installer/bootstrap.sh --json uninstall --skill lean-research-library --dry-run
LAX_DISABLE_UPDATE_CHECK=1 lax doctor --dry
```

Focused verification after implementation:

```bash
./installer/bootstrap.sh --run-python -m unittest discover \
  -s tests -p 'test_lax*.py' -v
./installer/bootstrap.sh --run-python -m unittest discover \
  -s tests -p 'test_zenodo_artifact*.py' -v
./installer/bootstrap.sh --run-python -m unittest discover \
  -s tests -p 'test_formal_terminal_state.py' -v
./installer/bootstrap.sh --run-python -m unittest discover \
  -s tests -p 'test_autoloop_formal_policy.py' -v
./installer/bootstrap.sh --run-python -m unittest discover \
  -s tests -p 'test_lean_gate_scanner.py' -v
make docs
make docs-check
make static-check
make sanitize-check
make runtime-smoke ARGS="--skills lax-formalization,zenodo-artifact"
make fake-root-lifecycle \
  ARGS="--skills lax-formalization,zenodo-artifact --platform-shape all"
```

Run the changed wrapper/credential/integration suites identified in T7 and the
repository's full test target before claiming the migration passes. A test
pattern selecting zero tests is a failed verification step, not a pass.

Inside the approved isolated project workspace only:

```bash
LAX_DISABLE_UPDATE_CHECK=1 lax build --replay submission
```

This command is one part of section 4, not a complete certificate on its own.
The executor's preflight/provisioning must already have succeeded.

Target options such as `--agents` and `--root` are global and precede the
subcommand. Set a task-specific list from the inspected inventory; do not assume
every agent home is present. For example, a reviewed Codex-only preview is:

```bash
./installer/bootstrap.sh --agents codex --json plan \
  --skills lax-formalization,zenodo-artifact
```

Use the matching target-native launcher documented by the installer on Windows.
Native Windows applied mutation is currently blocked by the installer; fake-root
shape tests on Linux do not establish native Windows execution. Lax's documented
Windows path is WSL. Report Linux/macOS/WSL/native-Windows coverage separately.
Restricted OpenClaw deployment remains governed by its dedicated existing
workflow; this migration does not grant a new real-system OpenClaw write path.

## 10. Handoff and stop rules

At every handoff record: completed task IDs, branch/commit and scoped diff,
test commands/results, version/source IDs, unresolved findings, exact next task,
and the still-active no-publication rule. Do not mark unchecked tasks complete.

Stop dependent work on changed user files, unsupported tool/spec version,
unresolvable dependencies, unavailable independent verification, isolation failure,
benchmark contamination or loss of source identity. Continue independent planning
or diagnostics where useful, but never convert missing evidence into a pass.

Completion requires T0–T8 evidence, not a successfully registered Lax record.
Report `incomplete analysis` or incomplete implementation when a material claim
exceeds inspected/tested coverage. No local result licenses publishing itself.

## 11. Planning delivery checks

- [x] Relevant local source, instructions, manifests, tests and deployed-rule
  locations inspected; source IDs recorded.
- [x] Service questions investigated with official documentation and relevant
  primary source; version-sensitive assumptions made explicit.
- [x] Benchmark repository, revision, target and exclusion boundaries identified.
- [x] No live publication, account change or formal proof run performed.
- [x] Plan reviewed for implementability and boundary errors; findings addressed
  with immutable phase handoff, provisioning limits and mandatory state-based
  retirement before catalog deletion.
- [x] Relative links, task dependencies, commands and scoped file diff checked.

Review Findings: PASS for the planning scope after independent implementability
and security reviews and a bounded re-review of corrections. Delivery Check:
READY for an implementation handoff, using research-verification-gate 1.0.0.
Document checks passed for relative links, source/task/test IDs, Markdown fences
and tables, Bash syntax of command examples, the repository's sensitive-material
checker on these two files, and whitespace. The only intended repository changes
are this plan and its source register. No implementation test or proof check was
run; optional checker qualification and authenticated service behavior remain
explicit future limits rather than claimed passes.

Implementation checkboxes T0–T8 are intentionally not completed by this planning
document. The source register distinguishes documented behavior from the live
integration tests that remain for an authorized future stage.
