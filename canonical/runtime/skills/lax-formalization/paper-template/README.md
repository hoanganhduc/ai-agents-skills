# Paper formalization

This repository keeps a Lax submission under `submission/` and separate
verification/archival tooling. Fill the title, authors and actual claim scope
before releasing it.

Use the `lax-paper-workflow` job to build this as a new publication repository.
The original research repository is read-only; select and sanitize source before
any public commit/push. Review all publishable history and CI effects. The helper
does not certify privacy merely because Lean compiled.

Keep bibliographic versions in `paper-versions.json`: exact arXiv versions,
conference/journal versions and corrections. Render the README section with the
trusted `workflow_check.py render-papers` helper. New entries are `not-reviewed`
unless independently reviewed against the corresponding formal commit and scope.
README can advance on main while Lax remains pinned to an earlier commit. Do not
move artifact tags or auto-submit/release because bibliography metadata changed.

<!-- lax-paper-versions:start -->
## Paper versions and publications

No paper versions entered yet. Complete `paper-versions.json` and render this
section before presenting the correspondence between paper and formalization.
<!-- lax-paper-versions:end -->

Use the independently verified Lax workflow for acceptance. A plain build,
archive registration, CI badge or DOI is not a semantic-equivalence review.
The CI workflow runs machine checks against the exact checked-out revision and
keeps semantic review pending. GitHub pull-request merge revisions are not
automatically release revisions.

The CI uploads a bounded public machine summary only on success, never raw
failure receipts or logs as an artifact. The optional archive workflow validates
an explicit file set before upload; source must already have passed the local
privacy gate before the push. A public CI run cannot undo source disclosure.

Local authoring uses `lax build --replay submission` in the approved isolated
environment. Keep `.lake`, generated manifests and build outputs untracked.
Never run `lake update` to replace the archive environment's exact pins.

To prepare a secondary Zenodo archive, use `zenodo-artifact prepare` with the
exact verification receipt and completed metadata. The resulting source ZIP
omits Git history and may need pinned dependencies downloaded for reproduction.
No publication happens from the included workflows. A later release must bind
the same approved commit and the independently verified target scope.
