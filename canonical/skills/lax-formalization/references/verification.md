# Requests and evidence

Supported initial profile: Lax 0.1.48, Lean v4.33.0,
mathlib commit `db584cd6d46c92f209a44c0f1c829460d327499d`. Unsupported profiles fail explicitly.
Lax's own spec/agent guide can be read with `lax print spec` and
`lax print instructions`. Catalog/search output is unverified provenance.

Write the request outside the candidate's Git root. The project must be clean
and committed. `challenge_root` is a separately frozen, reviewed copy of the
concept package's authored inputs, without `.lake` or generated manifests.
It must also be outside the candidate root.

```json
{
  "schema_version": "lax-request.v1",
  "project_root": "/path/to/project",
  "submission": "submission",
  "database_root": "/path/to/pinned/lax-database",
  "environment": "v4.33.0",
  "targets": ["Lax123456.Result.target"],
  "challenge_root": "/path/to/reviewed-concepts",
  "dependencies": {}
}
```

`dependencies` maps each required archive ID to its independently prepared
verification request. Sources must already be fetched and reviewed; verification
does not silently fetch Git repositories with credentials. The source triple
must match the registered record. Every recursively checked result is rebuilt;
editable archived status strings do not substitute for a fresh check.

The optional operator-controlled `compile_timeout_seconds` selects the proof
compilation budget as an integer from 60 through 3600 seconds; its default is
1200. It is part of the request digest and is recorded in the receipt. Each
dependency request selects its own budget. Static validation, concept-only
compilation and final checking keep their existing 1200-second limits. This
option does not increase CPU, memory, filesystem access or network access,
and never converts a timeout or partial build into verification evidence.
Keep the whole workflow within its separately approved resource/time budget.

For a registered dependency whose concepts contain **only definitions**, use
`"verification_kind": "definitions-only"` and `"targets": []` in that
dependency's request. This explicit mode is supported only by
`verify-dependency` and recursive dependency checks, not ordinary paper
verification or `publication-plan`. It still performs source qualification,
separate concept compilation, proof-package compilation and fresh inspection.
The checked concept inventory must contain no theorem statements; a package
with any such statement is rejected rather than silently skipping it. The
default `theorems` mode continues to require nonempty targets.

A definitions-only receipt reports its verification kind and explicitly
certifies no theorem targets. An empty obligation graph is not evidence for
a theorem, a downstream algorithm or a complexity bound. Definition meaning
still requires its own review against the frozen challenge, and absent review
keeps semantic status pending. Do not invent a trivial theorem target merely
to admit a definition library.

An optional `semantic_review` object records `status: accepted`, a nonempty
`reviewer`, `challenge_sha256` from the reviewed concept inventory, and
`scope_digest` from the exact reviewed target list. Only
record acceptance after an actual statement/definition review. Missing review
stays pending even when machine checks pass.

The supervisor checks source and database identities, compiles reviewed concepts
separately from proofs, terminates each container, seals its artifacts, and runs
fresh replay/inspection over read-only captures. Upstream Lax performs format
and compiled-environment judgments. The proof graph is then grounded only through
eligible independently checked witnesses. Fresh concept artifacts prevent proof
compilation from silently replacing the challenge's definitions.

The final `verification.json` binds source, scope, challenge, database, tools,
captures and phase results. `machine_status`, `closure_status` and
`semantic_status` are distinct. Pending semantic review supports only the formal
statement. No result is a remote-publication receipt. The pinned Lean/mathlib
background and checker implementations remain declared trust assumptions; no
external Comparator result is implied.

Do not use a stale `build-output.json`, a partial build, or `--nonstrict` output
as final evidence. The ordinary `lean-strict-verification-gate` continues to
protect generic Lean projects; its blanket axiom scanner is not a replacement
for the Lax-specific proof/obligation policy.

Dependency semantic acceptance also requires that the statements actually used
by the closure lie in the independently reviewed dependency target sets. Reviewing
one theorem does not endorse every proof in its repository.

For CI dependencies, provision their pinned sources/challenges and pass an
outside-project archive-ID-to-request JSON map through
`ci_verify.py --dependency-requests /operator/dependencies.json`. The default
standalone workflow fails closed if such requests are required and missing.
The workflow always leaves semantic review pending; a successful build cannot
perform the paper-to-statement review on its own.
