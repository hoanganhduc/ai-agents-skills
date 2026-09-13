# Phase 0 Skill Capability Audit Scope

## Baseline

- Audit date: `2026-09-13` (`Asia/Ho_Chi_Minh`).
- Authoritative source revision: `8bcb4c7ead6f539fba93e2686912735b07e2fc73`.
- Working-tree overlay: one uncommitted change in
  `canonical/runtime/skills/lean-explore-mcp/lean_explore_mcp.py`, recorded
  separately with diff SHA-256
  `07e0cdee693aa20c2ae99d7ead01f7d470798c5aae83835ad8904ab88375380f`.
- Scope: every skill currently registered in `manifest/skills.yaml`, every
  runtime mapping in `manifest/runtime.yaml`, and the declared target/platform
  surfaces those records reach.

The baseline audit was non-remediating. It wrote only the five files in this
audit directory and disposable temporary files. It did not change runtime code,
manifests, tests, generated documentation, installed agent homes, credentials,
dependencies, or external resources.

## Bounded Post-Audit Remediation

After the baseline was reviewed, the user separately authorized remediation of
four named items only: the LeanExplore command-target overlay, the vnthuquan
package route and wrapper interface, the Hetzner shared-workspace/live-check
configuration, and this Phase 0 report. The ai-agents-skills implementation
evidence is pinned to revision
`6630e10bf0f7c3ac2e36cf0b7486e4b229ea89e4`; the vnthuquan package evidence is
pinned to revision `1e4dd2bf5d2a349e7512faf2b0ff3e9fcd6e05a0`.

Post-remediation evidence uses the `post-remediation` source variant or an
`installed` variant with an explicit source revision. It may supersede a dated
observation only in the platform/backend scope it actually exercised. Baseline
verdicts that changed retain a `baseline_verdict` field; resolved gaps retain a
resolution status and evidence link.

The Hetzner migration retained the legacy workspace and an owner-private
crontab backup for rollback. It changed no credential values and created,
modified, or deleted no paid server. The vnthuquan checks were read-only and
downloaded no ebook. Native macOS, Windows, and WSL execution remain outside
the observed remediation scope.

## Source Variants

- `head`: content from the authoritative clean revision.
- `working-tree`: current checkout content, including the LeanExplore overlay.
- `installed`: an installed managed artifact or dependency, identified by
  version and integrity/state evidence.
- `external-observation`: a dated response from an outside service. This is
  evidence of the observed response only, not automatically a repository
  defect.
- `historical-lead`: a session/log locator that must be rechecked against HEAD
  before it can support a finding.
- `post-remediation`: repository content from the bounded implementation
  revision recorded above.

## Claim Taxonomy

1. `core`: the capability stated by the canonical skill description.
2. `optional-capability`: a capability declared optional in the skill manifest.
3. `documented-command`: an invocation shown in a canonical skill command block.
4. `normative-rule`: a routing, safety, refusal, sequencing, or output promise.
5. `declared-runtime-coverage`: the coverage class recorded in the runtime manifest.
6. `reference-file`: a direct mapping from a skill body to an existing canonical reference.
7. `reference-command`: an invocation shown in a directly referenced canonical file.
8. `reference-rule`: a normative promise in a directly referenced canonical file.
9. `source-interface`: a derived compatibility obligation supported by two or more
   inspected current source/manifest interfaces.

Evidence levels remain independent:

- `artifact`: an expected file or rendered record exists and matches its source.
- `discovery`: a native loader reports or resolves the skill.
- `launch`: the supported launcher reaches the intended process boundary.
- `functional`: a public input produces the specified observable result.
- `live`: a real external integration produces the specified read-only result.
- `safety`: a refusal, cleanup, redaction, or recovery property is observed.

Artifact presence, exit code zero, `--help`, doctor output, a self-reported tool
list, or a platform-shaped fake root cannot alone establish functional or live
behavior.

## Verdicts And Diagnosis

- `confirmed`: direct evidence establishes the exact claim within its recorded
  source, agent, platform, backend, and dependency scope.
- `contradicted`: direct evidence establishes behavior inconsistent with the claim.
- `conflicted`: current evidence sources disagree and the conflict is unresolved.
- `blocked`: a named prerequisite or native substrate is unavailable.
- `unverified`: no sufficiently strong observation has been made.

An observed failure is diagnosed separately as one of:

- `repository-defect`
- `environment-gap`
- `configuration-gap`
- `dependency-gap`
- `external-service-drift`
- `unsupported-surface`
- `operator-probe-error`
- `unknown`

A `repository-defect` requires a reproducible failure on current HEAD in a
documented supported configuration, plus evidence that rules out or directly
separates configuration, dependency, and external-service causes.

Severity is recorded as `P0` (credential/data-loss/security), `P1` (required
capability unusable on a claimed surface), `P2` (optional/degraded/reporting),
or `P3` (documentation/test hygiene).

## Evidence Priority And Conflict Rules

Use the following order, while preserving any genuine conflict:

1. current-HEAD execution through the documented supported entrypoint;
2. current-HEAD implementation and manifest inspection;
3. current tests and generated documentation;
4. exact-version installed dependency source;
5. dated external observations;
6. historical logs and reviewer output as leads only.

Newer evidence does not silently erase older evidence. It either supersedes a
dated observation with an explicit link or leaves the claim `conflicted`.

## Probe Boundary

- Offline probes use disposable roots with `HOME`, `USERPROFILE`, XDG, cache,
  and temporary paths redirected there.
- Ambient credential variables are removed. Synthetic values are offline-only.
- Commands are reviewed fixed argument vectors. No command is parsed from a
  skill body, legacy log, dependency output, or live response and executed.
- Command-like summaries in claims are inert quoted evidence. No claim, gap, or
  report record grants authority to execute a command, use credentials, make a
  live call, open Windows mutation, or write to an external system.
- Native agent probes are not run unless hooks, plugins, MCP/providers,
  auto-update, telemetry, and access to the real home can be disabled.
- Network and authenticated probes require a separate reviewed packet naming
  account/test tenant, endpoint and method, scopes, data sent, expected
  server-side effects, request/cost bounds, and cleanup. Absence of such a
  packet produces `blocked`, not an implicit probe.
- Secret values and raw sensitive output are never stored. Evidence keeps only
  redacted summaries, relative repository paths, versions, hashes, and status.

## Artifact Schemas

`claims.jsonl` records:

- `schema_version`, `claim_id`, `skill`, `kind`, `summary`
- `source` (`path`, line bounds or manifest pointer, source variant)
- `requiredness`, `supported_agents`, `platforms`, `verdict`

`evidence.jsonl` records:

- `schema_version`, `evidence_id`, `claim_ids`, `evidence_type`
- source revision/variant, platform/substrate, agent/backend when applicable
- reviewed command summary, observed status, versions/hashes, limitations

`gaps.jsonl` records:

- `schema_version`, `gap_id`, `claim_ids`, `skill`, `gap_type`
- `reason`, `blocking_prerequisite`, `next_read_only_probe`, `material`

## Completion Gate

Phase 0 may finish with `incomplete analysis`, but it may not finish with an
unclassified public claim. Every registered skill must have a sourced core
claim and every runtime-backed skill must have a runtime mapping. Every absent
native platform, credential, dependency, or test tenant remains explicit.

Phase 1 may be planned only for a bounded finding with a current reproduction,
sourced expected behavior, diagnosis, severity, owner, and acceptance evidence.
Blocked or unverified cells do not imply a repair design.
