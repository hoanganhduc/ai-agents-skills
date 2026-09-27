# Later publication

The current runtime is nonpublishing. Treat its publication plan as a reviewable
proposal. Only a separate scoped request authorizes remote effects.

Use the `lax-paper-workflow` runbook for source selection and readiness. The
initial publication repository is new; never push the research repository's
private history. Local `lax init` allocates an ID without registration. A local
ready verdict is not evidence for a later changed issue binding or renumbered ID.

Before a later publication, bind destination, source commit, public file inventory,
verification evidence and permitted stages. Lax's first submit can bind an issue;
commit/push that binding and reverify the resulting source before content submit.
Registration is a distinct irreversible stage. Never submit to obtain a free
test build or make an unverified dependency look trusted.

Zenodo is an optional secondary archive. Use the same approved source revision
and an explicit bundle of evidence. A GitHub Release can trigger archival once
Zenodo integration is enabled, so verification and authorization must precede
release creation. Do not move a published tag or rewrite a registered source to
insert a DOI; keep a separate mapping where appropriate.

## Version records and living documentation

Keep arXiv/preprint, conference, journal and correction entries in
`paper-versions.json`. An unversioned arXiv link is not a frozen review input.
Review correspondence against the actual document hash; DOI/URL identity alone
does not establish that a corrected document has unchanged content. A new entry
does not inherit acceptance from another paper version.

README on main can acquire new bibliography entries in commit B while Lax still
refers to verified commit A. Preserve A and its tag; do not submit/release B merely
because README changed. Package A from A with A's evidence. Registered Lax work
uses a successor with a fresh ID and `supersedes`; draft work is resubmitted.
Check current ownership/predecessor state at the later authorized operation.
`lax update` upgrades tooling, not a paper artifact.

Record pending publication identifiers honestly. Append observed identifiers in
a later bookkeeping commit instead of changing a pinned artifact to insert its
own future SHA/DOI. Paper authors and formalizers remain distinct credits.

Authoritative references: [Lax guide](https://laxarchive.org/contributing.html),
[Lax spec](https://github.com/lax-archive/lax/blob/v0.1.48/spec.md), and
[Zenodo GitHub integration](https://help.zenodo.org/docs/github/enable-repository/).
