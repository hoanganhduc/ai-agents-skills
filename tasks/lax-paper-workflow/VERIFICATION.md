# Lax paper workflow — implementation verification

Date: 2026-09-27. Scope: approved local runbook/helpers, new-repository source
preparation, metadata/version bindings, installation integration and bounded
qualification. No real research repository was migrated. No installed agent
homes, accounts, remote repositories, releases, Lax registration or Zenodo/arXiv
publication were changed. This is not a hosted paper-workflow CI result.

## Delivered

- `lax-paper-workflow` runbook for formalize-new, from-existing-lean,
  update-lax-artifact and update-paper-metadata; audit-only/prepare-local and
  link-only/source-in-repo/embedded-tex boundaries.
- `public_source.py`: read-only inventory and Git classification; transitive
  import candidates; explicit source/output hash-bound selection; owner-controlled
  review and new-directory POSIX export without history, source mutation or
  hardlinks. No executable transforms or implicit dependency retrieval.
- `tex_sanitize.py`: bounded comment sanitation, percent semantics, verbatim and
  Lax marker preservation. Ambiguous conditionals, dynamic token/input forms and
  unsupported inline-verbatim are refused, not silently treated as sanitized.
- `workflow_check.py`: executable readiness bindings for paper/source/targets,
  concepts, dependencies, actual independent controller review, privacy and
  optional paper build; public paper-version rendering and safe CI summary/bundle
  checks. Candidate/source-owned controller records and stale receipts refuse.
- Six data schemas; artifact/runtime registration; skill routes and publication
  guidance; generated docs and site navigation. Installed helper entrypoints were
  exercised from a fresh partial installation.
- Existing paper-template retained. Added `paper-versions.json`, living README
  guidance, success-only bounded CI summaries and an explicit bundle upload set.

The Git admission helper's only shared-code change suppresses Git parser stderr:
malformed candidate configuration must not leak private paths through new helper
diagnostics. New entrypoints normalize subprocess failures to structured output.

## Verification performed

| Check | Result |
|---|---|
| Selected source/readiness/Lax/Zenodo/install/runtime/schema-contract/docs regressions | 133 tests passed |
| Source sanitizer checks | Passed; 12 sanitizer tests passed |
| Static source checks | Passed |
| `make docs-check` and relative Markdown file targets | Passed |
| Sphinx HTML with `-W --keep-going` | Passed without warnings |
| Skill creator validation | Lax skill valid |
| JSON Schema definitions and actual benchmark controller records | Validated with Draft 2020-12 |
| Independent forward test | New five-file source tree; exact original hashes preserved, selected support reused, TeX comment removed, new paper entries not-reviewed |
| Real source/history comparison | Namespace-only reuse confirmed; public histories contain only synthetic/public source |
| Isolated TeX roundtrip | Text and raster image identical after comment removal; private canary unavailable |
| Native Lean reuse and blind candidate | Fresh build/replay, closed obligations and bound local readiness passed |
| Optional archive roundtrip | Both bundles validate; public bundle gate passes; restored bytes match and fresh replay passes under different synthetic commits |

The selected unit command covers `test_lax_public_source`,
`test_lax_workflow_check`, `test_lax_install_dependencies`,
`test_zenodo_artifact`, `test_lax_formalization`, `test_runtime_dependencies`,
`test_manifest_runtime_contracts`, `test_retired_skill_selector`,
`test_suite_text_encoding`, and the directly relevant generated-doc/profile
tests. A whole-repository stress suite was not run for this scoped change.

New tests were first observed failing before the new helpers existed. Subsequent
behavioral regressions reproduce changed-source export refusal, unsupported TeX,
malformed Git configuration, controller/source overlap, input-output collision,
missing review bindings and forbidden nested CI diagnostics.

## Bounded real Lean benchmark

Reference: [lax-345067](https://laxarchive.org/lax-345067/index.html), selected
order-pattern lemma from public upstream commit
`feabf43dcd6967ac912b2064003ea715e06e58f9`. Reused the previously reviewed,
attributed extraction; no claim that the complete Ramsey development was checked.
The exact scoped statement, not the full research paper, is the review input.

| Case | Public fixture commit | Result |
|---|---|---|
| Reuse selected definitions/proof | `c64ae3fbf889059a53c777cda944f37b83d11437` | local_ready; machine passed; closure closed; correspondence/privacy accepted |
| Independent proof candidate | `6bb2ebe3fe44916b9eff5867cf5f613eb52e8f28` | Same bounded acceptance |

Both statements quantify over arbitrary linear orders and finite tuple lengths;
repeated/equal coordinates are included. The reuse case preserves all six selected
Lean files modulo namespace substitution. The independent solver received only
the statement, fixed definition and permitted Mathlib imports, and affirmed that
it had not seen the reference proof. It made no tool calls. The model context was
reused from an unrelated cache-security review due to the agent-thread limit;
this is reference-proof separation, not a claim about training contamination.

The first independent proof failed because comparison `simp` steps made no
progress in the strict-order branches. A solver-supplied repair and mechanical
lemma-name correction from the pinned Mathlib declarations passed. The target,
definitions and assumptions were unchanged. An independent reviewer checked
correspondence and source/history at the final commits before acceptance was
bound to the verifier's request and workflow gate.

Qualification: non-root ARM64 Linux, existing pinned Lax 0.1.48 / Lean v4.33.0 /
Mathlib `db584cd6d46c92f209a44c0f1c829460d327499d` executor. The final executor
identity was `2e1ea21c1de03ec3a1b1f9a4e77b4fdaf09247e745e78f57b45c7d97ef01a290`.
Dependencies were already provisioned; the same trusted preparation/non-network
execution boundary is required for future jobs. This benchmark uses pinned
Mathlib background, not a newly qualified live cross-submission import chain.

Restoration produced identical source bytes under new synthetic commits:
`3cec1a2bdf654f8c85334b01a51a6df8e36d08b1` (reuse) and
`525b922b655eeb3f3f81152de1ac821a48aa20c2` (independent). Fresh restored replay
passed, while correspondence remains pending a new source-commit binding.
No old readiness record was promoted to the restored commit.

Private reproducibility artifacts are under the ignored
`.codex/runs/lax-paper-workflow/`: original selections, frozen blind proof,
forward-output reviews, native case requests/evidence/ready records, final bundles,
restored projects, TeX roundtrip and logs. Intended fixture URLs identify no
created remote publication repository.

## Review findings resolved

Two bounded independent code reviews found and verified corrections for:

- TeX hidden conditional/comment delimiter leakage and unsupported inline verbatim;
- a wrong Git environment-helper import caught by real-Git inventory testing;
- output paths overwriting paper/review inputs;
- missing producer/hash bindings accidentally satisfying metadata acceptance;
- source-owned controller records and omitted original-source identity;
- inherited Git stderr/tracebacks exposing candidate paths;
- extra checksum-listed bundle files and untyped nested diagnostic fields.

The final narrow recheck found no remaining issue in these reviewed boundaries
and reran 15 workflow tests successfully. It did not claim a full security audit
or repeat native Lean/TeX qualification.

## Limits and trust assumptions

- Readiness authenticates bindings under a trusted controller, not the identity
  or mathematical judgment of a reviewer. Actual independent review provenance
  must exist; JSON status strings alone do not constitute it.
- Static import/include analysis is advisory. Sanitization covers a stated TeX
  lexical subset; custom packages/macros, legal notices and semantic privacy
  still need review. Native TeX testing used a simple synthetic document in
  bwrap without network access, host home or private mounts; arbitrary paper rendering and Lax's
  hosted TeX pipeline were not qualified.
- Full reference-paper coverage, native Windows/macOS proof execution, remote
  service publication and the paper repository's hosted Actions remain untested.
  Native Windows public-tree export is explicitly refused.
- CI can constrain diagnostics but cannot prevent source disclosure that already
  occurred at push. The local privacy gate precedes the first public push.
- A source/evidence ZIP is not an offline-complete toolchain distribution.

## Style and scope

Writing profile: `~/.codex/instructions/writing-style-settings.md`, hash
`9cf565bea654c9357bdeb373aa3fbd3ce0bebc518d2c0c806f76022439e17e40`;
no manuscript-prose overlay was applied to runtime code. Active profile sections:
claim/evidence discipline, uncertainty/gaps and sentence-level defaults.
`style_applied: true`. No material project work outside the approved plan was
added; implementation reports and synthetic qualification artifacts support it.
