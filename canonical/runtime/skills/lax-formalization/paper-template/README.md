# Paper formalization

This repository keeps a Lax submission under `submission/` and separate
verification/archival tooling. Fill the title, authors and actual claim scope
before releasing it.

Use the independently verified Lax workflow for acceptance. A plain build,
archive registration, CI badge or DOI is not a semantic-equivalence review.
The CI workflow runs machine checks against the exact checked-out revision and
keeps semantic review pending. GitHub pull-request merge revisions are not
automatically release revisions.

Local authoring uses `lax build --replay submission` in the approved isolated
environment. Keep `.lake`, generated manifests and build outputs untracked.
Never run `lake update` to replace the archive environment's exact pins.

To prepare a secondary Zenodo archive, use `zenodo-artifact prepare` with the
exact verification receipt and completed metadata. The resulting source ZIP
omits Git history and may need pinned dependencies downloaded for reproduction.
No publication happens from the included workflows. A later release must bind
the same approved commit and the independently verified target scope.
