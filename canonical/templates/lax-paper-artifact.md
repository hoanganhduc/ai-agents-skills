# Shared Lax paper artifact

Use `lax init submission` inside the paper's Git repository. Keep its generated
names, environment pins and package layout. Then copy the paper-template files
from the `lax-formalization` runtime's `paper-template/` directory to the repository
root. This adds CI and archival metadata without a HoangMathLib dependency.

Before using the template:

1. Fill the real target IDs in `.lax-targets.json` after the concept scope is
   reviewed; an empty target list is deliberately not a passing fixture.
2. Fill metadata in `.zenodo.json` and `CITATION.cff`; keep identity/version/license
   consistent. The CFF example uses JSON-compatible YAML for deterministic checks.
3. Select a reviewed, published full commit of `ai-agents-skills` in the GitHub
   repository variable `AAS_SKILLS_REV`. The placeholder is intentionally not an
   old commit lacking the verifier. Until this implementation is published by its
   owner, validate entrypoints locally with the current source checkout.
4. Pin the action revisions after checking their upstream source. The workflow
   template uses reviewed full SHAs, not floating action tags.

The verifier script is executed from the separately pinned AAS checkout; project
configuration is read as data. The CI result always leaves semantic review
pending. Local independent acceptance adds an actual review bound to the exact
concept/scope hashes. PR checks do not authorize any publication.

The packager prepares explicit source/evidence files. No workflow here creates a
release, enables Zenodo integration, uploads to Zenodo or submits/registers Lax.
Leave optional Pages deployment and version-port probes as separately reviewed
workflows. A Lax environment upgrade must not silently follow latest mathlib.

Install `lean-strict-verification-gate` with the two artifact skills. The hosted
workflow requires the owner to set `AAS_SKILLS_REV` to a reviewed, published
40-character commit that contains this runtime; a local uncommitted checkout
cannot be used by GitHub Actions. Until that later GitHub operation is authorized,
exercise the same trusted entrypoint locally. No test here claims a hosted run.
