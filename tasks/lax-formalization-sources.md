# Lax Formalization Migration: Evidence Register

Research date: 2026-09-27. This register supports
[the implementation plan](lax-formalization-migration-plan.md).
These are documentation, software-source, configuration, and CLI observations,
not mathematical proof certificates. All listed sources are `NOT_A_PAPER`;
no paper retrieval or Zotero mutation was needed. Papers cited inside the
benchmark repository were not used as independently verified sources here.

`DOC` means official documentation inspected; `CODE` means the named source
and relevant implementation were inspected; `LOCAL` means local files/help were
inspected. None means a live publication or account integration was tested.
Revalidate changing documentation before implementing the affected feature.

## Version anchors

| Component | Inspected baseline |
|---|---|
| ai-agents-skills | `c2b68de26e8ed4f90e956107855624bf5bedf5db` |
| Lax npm package | `lax-archive@0.1.48`; Node requirement `>=20` |
| Lax release source | tag `v0.1.48` resolved to `86d4dba097570bf5f3153d6f2410e7f289b98421` |
| Lax spec bytes | SHA-256 `2f3f1fda37e99effb3138a38211422c4b708a0ca1bf84f1ee08bedb1d06c5d3d` |
| Lax agent instructions | SHA-256 `9c16d648a600d41dc34b7c986780ae19957a31ce596c7119440d21dde49f1a2b` |
| Selected Lax environment | `v4.33.0` / `leanprover/lean4:v4.33.0` |
| Selected mathlib commit | `db584cd6d46c92f209a44c0f1c829460d327499d` |
| Locally inspected Lax database | `848dda1e55db2fc7ff40ec94d3b89e9d0427a72f` |

The installed spec and instructions were compared byte-for-byte with the
corresponding files at the Lax release commit: both matched. Registry metadata
was read from [npm](https://registry.npmjs.org/lax-archive/0.1.48).
This is a reproducible baseline, not an instruction to stay on it forever or
silently upgrade it during a run.

## Service documentation and primary source

| ID | Source and inspected scope | Status | Finding used by the plan |
|---|---|---|---|
| S1 | [Lax specification at the release commit](https://github.com/lax-archive/lax/blob/86d4dba097570bf5f3153d6f2410e7f289b98421/spec.md); full installed 1,642-line copy inspected | DOC | Two-package contract, environment pins, admission rules, lifecycle, replay, and generated-file restrictions. |
| S2 | [Lax agent guide at the same commit](https://github.com/lax-archive/lax/blob/86d4dba097570bf5f3153d6f2410e7f289b98421/assets/instructions.md); full 65-line file | DOC | Agree mathematical scope, review and freeze concepts, then develop proofs; inspect the proof network. |
| S3 | [Lax contributing guide](https://laxarchive.org/contributing.html), [about page](https://laxarchive.org/about.html), [environment inventory](https://laxarchive.org/environments.json), [record index](https://laxarchive.org/index.json) | DOC | Source remains in the submitter's repository. The inventory describes availability, not independent proof verification. JSON was fetched read-only after browser extraction failed. |
| S4 | [Lax local build](https://github.com/lax-archive/lax/blob/86d4dba097570bf5f3153d6f2410e7f289b98421/src/cli/build.ts); installed `dist/cli/build.js`, complete-output/nonstrict branches | CODE | Partial builds can leave an earlier output file in place. Check execution and input binding, not output-file existence. |
| S5 | [Lax prooftree implementation](https://github.com/lax-archive/lax/blob/86d4dba097570bf5f3153d6f2410e7f289b98421/src/cli/prooftree.ts); selection, loading, and generation entrypoints | CODE | Selection loads draft and registered records. The public command needs an archive ID with content; it is not a local-folder verifier. |
| S6 | [Lax static checks](https://github.com/lax-archive/lax/blob/86d4dba097570bf5f3153d6f2410e7f289b98421/src/submission-validation/phases/static.ts) and [package-file checks](https://github.com/lax-archive/lax/blob/86d4dba097570bf5f3153d6f2410e7f289b98421/src/submission-validation/phases/package-files.ts); installed implementations inspected | CODE | Put CI and packaging helpers outside the constrained concept/proof packages. Generated dependency overrides must not become submitted source. |
| S7 | [Lax process runner](https://github.com/lax-archive/lax/blob/86d4dba097570bf5f3153d6f2410e7f289b98421/src/submission-validation/host/proc.ts) and [CLI authentication](https://github.com/lax-archive/lax/blob/86d4dba097570bf5f3153d6f2410e7f289b98421/src/cli/auth.ts) | CODE | Host execution inherits environment; credentials and caches can share Lax home. A clean environment alone is not filesystem isolation. |
| S8 | [Zenodo records](https://help.zenodo.org/docs/deposit/about-records/) and [upload guide](https://help.zenodo.org/docs/deposit/create-new-upload/) | DOC | Software archives are acceptable research objects. A DOI identifies an archived object; it is not a Lean verification result. |
| S9 | [Enable GitHub integration](https://help.zenodo.org/docs/github/enable-repository/) and [archive a release](https://help.zenodo.org/docs/github/archive-software/github-upload/) | DOC | Enabling integration allows new GitHub Releases to trigger archiving. Verification after release creation is too late to serve as the publication gate. |
| S10 | [Zenodo JSON](https://help.zenodo.org/docs/github/describe-software/zenodo-json/) and [CITATION.cff](https://help.zenodo.org/docs/github/describe-software/citation-file/) | DOC | JSON metadata takes precedence over CFF for this integration; CFF alone can be sufficient. Validate consistency when keeping both. |
| S11 | [Zenodo REST API](https://developers.zenodo.org/); upload quickstart, authentication/scopes, deposition create/update, bucket upload, publish, new-version, and rate-limit sections | DOC | Documented transport supports draft creation, file upload, metadata update, and a separate publish operation. Use bearer headers, not credentials in URLs. Authenticated execution was not tested. |
| S12 | [Manage files](https://help.zenodo.org/docs/deposit/manage-files/) and [manage records](https://help.zenodo.org/docs/deposit/manage-records/) | DOC | Current guides describe limited correction/deletion windows. Older absolute statements that every published object is forever unmodifiable are not a reliable workflow contract. |
| S13 | [Zenodo integration subclass](https://github.com/zenodo/zenodo-rdm/blob/9613b2f888a90e7422126e5abb25d21c3c88d48d/site/zenodo_rdm/github/release.py) and [inherited release uploader](https://github.com/inveniosoftware/invenio-rdm-records/blob/7556e77271655482180a3b043ba53b15642702b9/invenio_rdm_records/services/github/release.py) | CODE | Inspected upload path creates one archive-file entry and fetches the source zipball. Do not assume it archives additional release assets or CI artifacts. These are upstream revisions, not an attestation of production deployment. |
| S14 | [GitHub workflows](https://docs.github.com/en/actions/concepts/workflows-and-actions/workflows) and [workflow events](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows) | DOC | Workflows live at repository root. A PR run normally checks a synthetic merge revision; it must not be confused with a release source revision. |
| S15 | [GitHub secure-use reference](https://docs.github.com/en/actions/reference/security/secure-use) | DOC | Pin reviewed actions by full SHA, minimize permissions, and separate untrusted execution from credentials. |
| S16 | [GitHub artifact retention](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/download-workflow-artifacts) | DOC | CI artifacts have retention limits; durable research evidence needs an explicit archive path. |
| S17 | [lean-action documentation](https://github.com/leanprover/lean-action) | DOC | Supports explicit package directories and optional checking features. Its generic defaults do not establish compatibility with a Lax two-package project or its concept axioms. |
| S18 | [Lean proof validation](https://lean-lang.org/doc/reference/latest/ValidatingProofs/), [axioms](https://lean-lang.org/doc/reference/latest/Axioms/), [native evaluation](https://lean-lang.org/doc/api/Lean/Meta/Native.html) | DOC | Check statement meaning and actual axiom dependence separately. Native evaluation can expand trust beyond the kernel. These explanatory pages track `latest`; executable policy is pinned to the selected toolchain. |
| S19 | [Reference submission](https://laxarchive.org/lax-345067/index.html) and [pinned source subtree](https://github.com/lax-archive/lax-submissions/tree/feabf43dcd6967ac912b2064003ea715e06e58f9/finite-ramsey-v4-33) | DOC/CODE | A small order-pattern lemma supplies a bounded benchmark. Its proof was inspected for feasibility, not independently executed or certified. A future solver must use a fresh context. |
| S27 | [Comparator documentation](https://github.com/leanprover/comparator); trusted challenge, sandbox assumptions, tool prerequisites and external kernels | DOC | Describes stronger statement/proof comparison with separate prerequisites. Compatibility with the selected Lax toolchain is not assumed or tested. |
| S28 | [Replay phase](https://github.com/lax-archive/lax/blob/86d4dba097570bf5f3153d6f2410e7f289b98421/src/submission-validation/phases/replay.ts), [inspection runner](https://github.com/lax-archive/lax/blob/86d4dba097570bf5f3153d6f2410e7f289b98421/src/submission-validation/phases/inspect-runner.ts), and [host pipeline](https://github.com/lax-archive/lax/blob/86d4dba097570bf5f3153d6f2410e7f289b98421/src/submission-validation/host/pipeline.ts); relevant installed exports and execution paths | CODE | Upstream exposes phase functions and captured-artifact execution paths. These are internal version-sensitive interfaces, not promised public CLI flags for isolated checking. |

## Local implementation evidence

Paths in this section are relative to the `ai-agents-skills` checkout unless
prefixed by `~`. They are migration inputs, not instructions to edit everything
containing a matching word.

| ID | Evidence inspected | Finding |
|---|---|---|
| S20 | `canonical/skills/lean-research-library/SKILL.md`; relevant functions throughout `canonical/runtime/skills/lean-research-library/lean_research_library.py`; `tests/test_lean_research_library_runtime.py` | The old skill combines library search, staging, maintenance, artifact scaffolding and Zenodo. Its API path creates a draft only. Its minimal scaffold pins v4.32.2. |
| S21 | `canonical/runtime/skills/lean-strict-verification-gate/lean_strict_verification_gate.py`; scan, axiom audit and project-context interfaces; corresponding skill | The generic scanner rejects active axioms. An in-memory concept-only probe returned `trust_base_blocker: axiom`; no Lean process or source file was created. |
| S22 | `formal_policy.py`, `autonomous_research_loop_runtime.py`, `goal_focus.py` under `canonical/runtime/skills/autonomous-research-loop-runtime/`; affected functions and tests | Reuse instructions, tool allowlists, project detection, terminal certificates and final re-verification all participate in migration. Editing prompt text alone is insufficient. |
| S23 | `manifest/{skills,runtime,profiles,artifacts}.yaml`, `manifest/credential-runtime.json`, `canonical/runtime/runners/run_skill.{sh,ps1}`, affected tests | Registration, runtime installation and Zenodo credential routing require coordinated changes. |
| S24 | `installer/ai_agents_skills/cli.py`: `resolve_skill_filter`; `lifecycle.py`: scope expansion/uninstall; `docs/uninstall-rollback.md`; CLI help | Uninstall first resolves the skill through the catalog. Keep the old catalog entry until its installed instances have been retired, or provide a tested state-based retirement path. Shared runtime removal depends on remaining consumers. |
| S25 | Installer state, installed old skill/instruction entries, selected manual rules, and `~/.config/lean-research-library/config.json` | The inspected host had 21 managed records across nine agent targets and two runtime roots. Some mandatory rules lie outside managed blocks. Re-enumerate; do not encode these counts or host paths in migration logic. |
| S26 | Existing HoangMathLib source/configuration and five workflows: `ci.yml`, `update.yml`, `mathlib-master-probe.yml`, `release.yml`, `pages.yml` | Preserve research code and generic CI benefits; replace library-specific staging/upstreaming behavior. The local library contained two Lean files and five theorem declarations. No new build was run. |

## Clarifications and limits

| Question | Resolution for implementation |
|---|---|
| Does Zenodo require another Lean source layout? | No separate Lean layout is required by the inspected deposit guides. Use one source repository and explicit archival metadata. |
| Will GitHub integration include our evidence bundle? | Do not rely on that. S13 shows a source-archive upload path. The default secondary option is an explicit bundle prepared for manual upload; API planning is documented separately. |
| Which Zenodo API should a future adapter target? | The documented deposition API plus returned bucket upload link in S11; do not invent Invenio endpoints from UI traffic. Mock tests are permitted now; authenticated live tests need later authorization. |
| Are published Zenodo files categorically immutable? | Official pages conflict: older records/API text is absolute, while current management guides describe exceptions. Never base rollback on deletion or editing after publication. Recheck the account's current UI before any later live release. |
| Is a successful Lax build enough? | No. The new acceptance contract also checks coverage, selected proof closure, independent dependency verification and statement correspondence. |
| Can `generate-prooftree` verify a new local folder? | Not in the inspected public CLI. Use local compiled/inspected evidence and a checked proof graph; use the archive-ID command only within its actual contract. |
| Are all Lax results independently certified here? | No. Catalog access and the reference-source inspection are discovery evidence only. |
| Is the nested-package CI known to run on all providers/OSes? | No live workflow was run. Local execution and fake-root tests precede deployment; native support must be reported separately. |
| Did this research publish or reserve anything? | No Lax submit/register, GitHub Release, Zenodo draft/DOI, login, or account-setting mutation was performed. |

Historical Zenodo issues about assets were used only as discovery leads;
they are not the basis for a current deployment claim. The unavailable guessed
documentation URLs were replaced with the official pages listed above.

## Writing and delivery record

- Style profile: `writing-style-settings.md`, inspected from the active Codex
  instructions; SHA-256
  `9cf565bea654c9357bdeb373aa3fbd3ce0bebc518d2c0c806f76022439e17e40`.
- Active requirements: scope, evidence attribution, uncertainty, reproducible
  command formatting, and separation of observed behavior from planned work.
- Output is an engineering handoff, not a mathematical manuscript or a proof.
- Source inspection supports the plan; implementation, benchmark success,
  authenticated service behavior and publication remain untested.

### Implementation qualification additions (2026-09-27)

- Node 22 documents [`--disable-sigusr1`](https://nodejs.org/download/release/v22.16.0/docs/api/cli.html#--disable-sigusr1);
  the controlled executor uses it for trusted holders and controllers.
- `npm audit` on the locked Lax 0.1.48 toolchain reports the transitive PDF.js
  advisory [GHSA-hq66-cqwq-w95j](https://github.com/advisories/GHSA-hq66-cqwq-w95j).
  No automatic downgrade was applied. This milestone runs the source/proof
  verifier in isolation, does not render a public site or open PDFs, and exposes
  no publication command. PDF/site capabilities require separate qualification.

- The pinned [actions/checkout Git implementation](https://raw.githubusercontent.com/actions/checkout/11d5960a326750d5838078e36cf38b85af677262/src/git-command-manager.ts)
  writes `gc.auto=0` and removes its sparse-worktree configuration when sparse
  checkout is disabled. Source admission permits that exact harmless GC setting;
  executable/filter/include configuration remains refused.
