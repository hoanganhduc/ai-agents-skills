# Lax documentation follow-up

## Specification

Update the main user guides after the Lax migration and fix the demonstrated
missing verifier dependency in partial installs. Keep the existing independent
verification, per-paper source layout, secondary Zenodo archive, and separate
publication authorization boundaries.

Scope: manifests, docs generator, manual navigation pages, paper template,
and focused installer regressions. No host install, executor provisioning,
paper formalization, remote push, release, or service publication.

## Evidence and plan

1. Inspect CLI help, runtime call sites, installer dependency resolution and
   official Lax/Zenodo guides. The audit found no need to delete generic Lean
   skills or historical research reports.
2. Reproduce the missing strict-gate runtime in a fresh partial installation;
   declare the artifact and runtime dependencies and repeat the regression.
3. Add a generated Lax/Zenodo guide, including setup, independent reuse, CI,
   archival, migration and troubleshooting. Correct platform/default-mode
   wording at its generator source and update manual navigation.
4. Regenerate docs; run focused tests, docs consistency, Sphinx and file-link
   checks. Obtain a bounded fresh-context boundary review before delivery.

The strict Sphinx build also exposed two existing issues in this scope: the
Windows WSL heading skipped a level, and Markdown heading anchors were disabled.
Correct the heading and enable anchors through level 3; verify with warnings
treated as errors.

## Acceptance and verification

- A fresh template-with-dependencies or standalone Lax runtime installation
  includes the strict gate and can compute the executor identity offline.
- Native Windows examples respect the mutation gate; force-loop enforce is
  described as Linux/WSL execution, separately from portable wrappers.
- README and the Sphinx site lead to a usable Lax/Zenodo workflow with explicit
  machine/semantic/publication distinctions and safe retired-skill migration.
- Generated copies agree, new navigation resolves and the docs site builds.
- Service claims cite current primary guides; runtime requirements describe
  the supported pinned profile, not the latest upstream environment.

## Tasks

- [x] Inspect instructions, implementation, examples and source documentation.
- [x] Reproduce and fix the partial-install dependency failure.
- [x] Update guides, template, navigation and generated copies.
- [x] Verify and resolve fresh-review findings.

## Writing contract

Profile: `~/.codex/instructions/writing-style-settings.md`; no domain overlays.
Preserved claims: registration is not proof evidence; semantic review is
independent; no publication or personal-library staging is implied. Updated
claims are grounded in runtime/installer code and linked official service docs.

- `style_profile_ref`: `~/.codex/instructions/writing-style-settings.md`
- `policy_hash`: `9cf565bea654c9357bdeb373aa3fbd3ce0bebc518d2c0c806f76022439e17e40`
- `active_overlays`: none
- `active_requirement_ids`: profile sections `claim-and-evidence-discipline`,
  `uncertainty-and-gaps`, `sentence-level-defaults`
- `style_applied`: true

## Verification evidence

- Before the manifest fix, both new tests failed: standalone runtime resolution
  omitted the strict gate, and the fresh installed executor raised
  `FileNotFoundError` while hashing that gate. Both pass after the fix.
- Dependency closure plus new install/encoding regressions: 13 tests passed.
- Final scoped installer/runtime/docs suite: 75 tests passed. The broader
  `DocsAndLauncherTests` class was interrupted during its unrelated lifecycle
  stress matrix (state-file fsync); no full-suite pass is claimed.
- `make docs-check`: current, no missing generated pages.
- Sphinx HTML build with `-W --keep-going`: passed without warnings after the
  heading/anchor fixes. Build dependencies were isolated in a temporary venv.
- Relative Markdown file targets: 55 files checked; 26 mirrored pairs agree.
  This is not a live external-link or service-availability check.
- `make static-check`: passed. The first pass caught missing explicit UTF-8 in
  the new subprocess test; corrected using the repository's existing rule and
  learning, without adding a duplicate policy.
- Fresh-context security review found no remaining issue in dependency closure,
  command syntax, ownership-aware migration or publication boundaries. Checked
  candidate/trusted-code separation, evidence authenticity and deletion scope.
- No host install, executor re-provisioning, hosted paper CI run, native Windows
  execution or remote publication was performed in this documentation task.
