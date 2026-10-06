# Lax Formalization And Zenodo Archival

Ordinary Lean/Lake formalization does not require Lax. This page applies when
a Lax artifact or optional archive is selected. The existing `from-existing-lean`
workflow adapts selected source from a normal Lean repository later.
Keep artifacts per paper. Search pinned Mathlib first; Lax discovery is optional
outside a selected Lax workflow. Independently rebuild
the selected Lax results, close their actual proof dependencies, and review
whether the formal statements match the paper. Registration alone is not proof
evidence. Zenodo is the secondary archive; no personal Lean library is required.

For a complete job, follow [Lax Paper Workflow](lax-paper-workflow.md). It covers
new formalization, selected reuse from an existing Lean repo, artifact updates
and paper metadata updates. The research source stays read-only; the initial
publication repository is new and does not inherit private Git history. The
runbook uses the existing paper-template and adds source selection, bounded TeX
sanitation and an executable gate binding paper review to exact source evidence.

Install that runbook and its backing skills with:

```bash
make plan ARGS="--no-skills --artifact template:lax-paper-workflow --with-deps --runtime-profile auto"
```

Its `paper-versions.json` tracks preprint/conference/journal/correction entries.
README on main may change while the Lax artifact stays at its pinned commit;
new bibliography entries do not inherit semantic acceptance. CI exports only a
bounded machine summary on success. Public source/history review must precede
the first push; a CI badge does not provide privacy or paper-correspondence proof.

## Install The Workflow

From the ai-agents-skills checkout, preview the focused template installation:

```bash
make plan ARGS="--no-skills --artifact template:lax-paper-artifact --with-deps --runtime-profile auto"
make install ARGS="--no-skills --artifact template:lax-paper-artifact --with-deps --runtime-profile auto --dry-run"
```

This selects `lax-formalization`, `lean-strict-verification-gate` and
`zenodo-artifact`, including runtime helpers. Selecting only the Lax skill with
runtime profile `auto` also includes the strict-gate runtime dependency. The
`formal-research` skill profile provides the broader Lean workflow. Follow
[Installation](installation.md) to apply a reviewed plan; installing these
files does not provision Docker, download Lean, or publish anything.

Commands below run from the ai-agents-skills checkout unless otherwise stated.
Installed copies use the same verbs through their platform's `run_skill` wrapper;
see the runtime roots in [Installation](installation.md).

## Prerequisites And Qualified Scope

| Operation | Requirements and limits |
|---|---|
| Helper `doctor`, local catalog `search`, bundle `validate` / `restore`, publication plans | Python 3.10+; local inputs; no Docker or service credentials. Search does not fetch a catalog. |
| Source bundle `prepare` | Python and Git; clean committed source plus matching verification evidence. |
| Local authoring setup | Git and the pinned Lax CLI; the official setup guide currently requires Node 20+ and about 10 GB free disk. |
| Independent `verify` / `verify-dependency` | A provisioned executor on non-root Linux/WSL, Docker access, pinned Lax/Lean/mathlib, a local database with required objects, reviewed concepts, and the strict-gate runtime. |

The implemented executor profile is **Lax 0.1.48**, **Lean v4.33.0**, and
Mathlib commit `db584cd6d46c92f209a44c0f1c829460d327499d`. It uses pinned
Node 22 container images, supports AMD64/ARM64 image selection, and was qualified
locally on ARM64 Linux. Native Windows/macOS offline commands do not establish
native Lean execution support. Environment upgrades require requalification.

Verification containers are limited to two CPUs and 6 GiB memory. The executor
requires at least 5 GiB free space at the output location, in addition to the
toolchain, warm Mathlib store and image storage. That threshold is a preflight
check, not a guarantee that any paper will fit. Measure resources before setup.
The generic installer precheck does not qualify the executor.

The upstream [`lax doctor`](https://laxarchive.org/contributing.html) downloads
the Lean toolchain, prebuilt Mathlib and archive database. In contrast, the
following helper is offline and only reports readiness:

```bash
python3 -B canonical/runtime/skills/lax-formalization/lax_formalization.py doctor
python3 -B canonical/runtime/skills/zenodo-artifact/zenodo_artifact.py doctor
```

After intentionally preparing the supported CLI and warm store, preview executor
setup, review it, then apply it as a separate local setup step:

```bash
python3 -B canonical/runtime/skills/lax-formalization/provision_executor.py
python3 -B canonical/runtime/skills/lax-formalization/provision_executor.py --apply
```

Setup builds a local image/inspector and writes operator configuration. Do not
store that configuration in candidate source. Detailed setup and input-admission
rules are in `canonical/skills/lax-formalization/references/executor.md`.

## Author And Independently Verify

1. Record the paper claims, definitions, assumptions and target inventory. Search
   the pinned Mathlib source before extending it. An unsuccessful text search
   is not a complete absence proof.
2. Search a locally provisioned, pinned Lax database. Treat each hit as an
   unverified candidate; check source provenance and environment compatibility.
3. In the paper's Git repository, use `lax init submission --env v4.33.0`.
   Keep the generated package layout and pins. Read `lax print spec` and
   `lax print instructions` from the selected CLI; do not run `lake update`.
4. Review and freeze the concept package outside the candidate repository before
   proof work. Review retrieved source before allowing execution through the
   controlled executor. Keep verification requests outside candidate source too.
5. Commit a clean candidate and independently verify it and all required Lax
   dependencies. Inspect machine checks, obligation closure and semantic review
   separately; a machine pass alone does not establish correspondence to a paper.

```bash
python3 -B canonical/runtime/skills/lax-formalization/lax_formalization.py search \
  --query "your concept" --database /path/to/pinned/lax-database \
  --environment v4.33.0
python3 -B canonical/runtime/skills/lax-formalization/lax_formalization.py verify \
  --request /path/to/operator/request.json --out /path/to/new-evidence
```

The request schema, target IDs, dependency request map and hash-bound semantic
review are documented in
`canonical/skills/lax-formalization/references/verification.md`. Use
`verify-dependency` with the same request/output flags for selected archive
dependencies. Missing dependency checks remain missing evidence, even for
registered results. A local ordinary Git checkout is required; linked worktrees
and unqualified hooks/filters/configuration are refused.

## Keep GitHub Actions And Prepare The Paper Repository

Copy and review the files in
`canonical/runtime/skills/lax-formalization/paper-template/` without overwriting
existing project files blindly. Keep `.github/workflows/`, `scripts/`,
`CITATION.cff` and `.zenodo.json` at the repository root, outside the submission's
`concepts/` and `proofs/`. Existing CI can remain; adapt it to the pinned
environment and keep publication triggers separate from verification.

Fill `.lax-targets.json` with the submission path, supported environment and
nonempty real target IDs. Complete title, version, license and creator metadata.
Use the supplied JSON-compatible YAML CFF form for the helper's consistency
checks; full CFF schema validation is separate.

The workflow checks out a reviewed, published full ai-agents-skills commit from
the repository variable `AAS_SKILLS_REV`. Verify that the chosen revision
contains the runtime, and keep the reviewed action SHAs pinned. It runs the
trusted verifier from that checkout; it does not execute the candidate's
`scripts/verify.py` as the trusted verifier. Exercise the same entrypoint locally
with an already provisioned executor:

```bash
python3 -B canonical/runtime/skills/lax-formalization/ci_verify.py \
  --project /path/to/paper-repo --state-dir /path/to/new-ci-state \
  --database /path/to/pinned/lax-database
```

For archive dependencies, provision their reviewed sources and pass an
outside-project mapping through `--dependency-requests /path/to/dependencies.json`.
The default standalone workflow refuses missing dependency requests; adapt its
trusted setup before relying on such a run. CI always leaves semantic review
pending. Record the paper workflow's actual hosted run and checked commit;
ai-agents-skills CI and local Lean/Docker tests do not establish that hosted run.

## Use The Same Source For A Secondary Zenodo Archive

A Lax-layout repository can also supply a Zenodo source archive. Lax identifies
the submitted source by repository, commit and folder; archival metadata and CI
can accompany that source. This shared-layout conclusion does not guarantee
acceptance by either service. See the [Lax guide](https://laxarchive.org/contributing.html)
and [Zenodo software metadata guide](https://help.zenodo.org/docs/github/describe-software/zenodo-json/).

Prepare the explicit bundle from a clean revision and its trusted supervisor
report. The output directory must be new and outside the source tree:

```bash
python3 -B canonical/runtime/skills/zenodo-artifact/zenodo_artifact.py prepare \
  --project /path/to/paper-repo \
  --evidence /path/to/new-evidence/verification.json \
  --metadata /path/to/paper-repo/.zenodo.json --out /path/to/new-bundle
python3 -B canonical/runtime/skills/zenodo-artifact/zenodo_artifact.py validate \
  --dir /path/to/new-bundle
python3 -B canonical/runtime/skills/zenodo-artifact/zenodo_artifact.py restore \
  --bundle /path/to/new-bundle --out /path/to/new-restored-repo
```

Inspect `source.zip`, metadata, checksums, evidence and `REPRODUCE.md`. This is a
source-and-evidence bundle, not a fully offline toolchain distribution. Restore
extracts source files without Git history and does not rerun proof verification.
Before re-verification, follow `REPRODUCE.md` to initialize a separate synthetic
Git context, keeping its commit distinct from the original source identity.
Checksums do not authenticate a candidate-authored report or certify mathematics.
Preparation requires passed machine checks and closed dependencies; pending
semantic review remains explicitly pending.

Zenodo's GitHub integration uses `.zenodo.json` when both metadata formats exist,
so keep it consistent with `CITATION.cff`.
[Zenodo metadata precedence](https://help.zenodo.org/docs/github/describe-software/zenodo-json/).
Once enabled, [GitHub integration](https://help.zenodo.org/docs/github/enable-repository/)
automatically archives new releases. Run verification before creating a release;
release-event checks cannot serve as the pre-publication gate. Preserve evidence
explicitly rather than assuming CI artifacts are included in the archive.

These helpers do not log in, submit/register Lax, upload to Zenodo, reserve a DOI
or create releases. Complete local work first. A separate request must authorize
the intended remote operation and exact source/files. Publication plans remain
proposals; see `canonical/skills/lax-formalization/references/publication.md` and
`canonical/skills/zenodo-artifact/references/delivery.md` before that later step.
Service guidance above was checked on 2026-09-27; recheck before publication.

## Migration, Evaluation And Troubleshooting

- **Retired personal-library workflow:** follow the scoped retirement procedure
  in [Uninstall And Rollback](uninstall-rollback.md). Preserve existing Lean
  sources and history; remove obsolete routing/settings only after inspecting
  their ownership and replacements.
- **Executor unavailable or profile mismatch:** inspect helper `doctor`, confirm
  the supported pins and explicitly provision/requalify. An unavailable check
  does not permit running retrieved Lean directly on the host.
- **Missing strict-gate file:** preview an updated partial install with runtime
  profile `auto`; `--no-runtime` deliberately omits executable helpers.
- **Disk pressure:** measure caches/build outputs separately from authored source.
  Regenerable outputs may be removed after reviewing the exact paths; account for
  download and rebuild cost. Do not delete source or evidence to make a test pass.
- **Build passes, result still pending:** inspect closure and semantic status;
  neither can be replaced by an archive registration or a CI badge.
- **Bounded comparison test:** select a small statement subset from a pinned Lax
  reference, hide reference proofs during independent formalization, then compare
  definitions, assumptions, proof dependencies and build behavior. Report only
  the tested subset. See `canonical/skills/lax-formalization/references/benchmark.md`
  and the dated [Lean benchmark survey](lean-formalization-benchmarks.md).

Related pages: [Dependencies](dependencies.md), [Verification](verification.md),
[Workflow Overview](workflow-overview.md), [Windows](windows.md).
