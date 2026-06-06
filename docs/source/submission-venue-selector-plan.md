# Fully Automated Submission Venue Selector Skill Plan

This is an implementation plan, not current implemented behavior.

## Scope And Review Status

Create a runtime-backed skill named `submission-venue-selector` that can run a
fully automated venue-selection workflow for an existing scholarly draft. The
final acceptance target is installation across Codex, Claude, DeepSeek,
explicit Copilot, and OpenClaw, with Linux, macOS, WSL, Windows PowerShell, and
Windows CMD runtime coverage.

Independent review found that current OpenClaw runtime-backed installs are
blocked by the installer until neutral runtime evidence and target-specific
OpenClaw gates exist. Therefore this plan keeps all-target support as a final
acceptance requirement, but sequences OpenClaw enablement before any "all
targets done" claim. Until that phase lands, OpenClaw tests must assert an
expected block, not a false pass.

## Skill Contract

- Add `canonical/skills/submission-venue-selector/SKILL.md`.
- Frontmatter:
  - `name: submission-venue-selector`
  - `description: Use when selecting, ranking, or validating submission venues
    for an existing scholarly manuscript or draft venue shortlist. Do not use
    for generic draft review, rewriting, paper retrieval, paper download,
    Zotero mutation, or one-off venue facts.`
- Keep `SKILL.md` concise and move details into progressive-disclosure
  references:
  - `references/provider-policy.md`
  - `references/artifact-schema.md`
  - `references/scoring-rubric.md`
  - `references/report-contract.md`
  - `references/privacy-and-network-policy.md`
- Add optional `agents/openai.yaml` only if the repo has a current convention
  for deterministic skill UI metadata.

The skill composes with existing research skills for workflow guidance, but the
runtime helper owns the automation. `paper-lookup` is reference guidance, not
an executable provider client. The selector must implement its own explicit
provider registry/client layer.

## CLI Surface

All commands accept `--dir <workspace>`. Commands that can call public services
are local/offline by default and require explicit network flags.

- `init --dir <workspace> --draft <path>`
- `plan --dir <workspace>`
- `extract --dir <workspace>`
- `privacy-gate --dir <workspace>`
- `providers --check --dir <workspace>`
- `resolve --dir <workspace> [--allow-network] [--allow-provider <name>]`
- `expand --dir <workspace> --max-hop 1 --max-papers <n> [--allow-network]`
- `venues --dir <workspace> [--allow-network]`
- `recent --dir <workspace> --years 5 --per-venue <n> [--allow-network]`
- `score --dir <workspace>`
- `report --dir <workspace>`
- `validate --dir <workspace>`
- `purge --dir <workspace>`
- `run --dir <workspace> --draft <path> [--allow-network]`
- `smoke`

Shared options:

- `--offline`: force fixture/cache-only behavior.
- `--fixture-dir <path>`: use committed or test fixtures.
- `--cache-dir <path>`: override cache location.
- `--max-requests <n>` and `--timeout <seconds>`: bound live calls.
- `--force`, `--refresh-cache`, and `--no-cache`: explicit overwrite/cache
  semantics.
- `--retain-draft-text`: allow raw draft text in artifacts.
- `--allow-downloads`, `--allow-zotero-mutation`, and
  `--allow-unpaywall-email`: fail-closed gates for actions that are forbidden
  by default.

Exit/status semantics:

- `ready`: exit 0, all mandatory validation and delivery gates pass.
- `ready-with-caveats`: exit 0 with warnings, evidence gaps are explicit and
  non-rank-critical.
- `not-ready`: exit 1, validation finds unsupported, stale, missing, or unsafe
  claims/artifacts.

## Automation Workflow

1. `init`: create a private workspace, reject unsafe workspace locations by
   default, hash the draft, and create `run_status.json`.
2. `plan`: create `selection_plan.json` with field/topic constraints, venue
   type constraints, request budgets, scoring weights, and assumptions.
3. `extract`: parse `.bib`, `.tex`, markdown/text, and Docling-exported text
   fixtures without network calls.
4. `privacy-gate`: default drafts to `unpublished`, generate
   `queries.jsonl`, and block network if queries contain raw draft sentences,
   novel theorem/section names, acknowledgments, author emails, or full
   abstracts.
5. `providers --check`: write `provider_status.json` with provider
   capabilities, auth/email state, rate limits, configured domains, and cache
   policy.
6. `resolve`: resolve references by DOI, title, PMID/arXiv ID, or other
   identifiers using the provider capability matrix.
7. `expand`: expand only from resolved seeds and record edge type:
   `reference`, `citation`, `co_citation`, `bibliographic_coupling`,
   `provider_related`, or `topic_search`.
8. `venues`: normalize publication venues while separating journals,
   conference series, conference instances, proceedings containers, preprint
   servers, repositories, and currently open submission venues.
9. `recent`: collect recent venue papers with logged provider queries, total
   hits, sampling method, year distribution, and topic similarity thresholds.
10. `score`: apply hard eligibility gates first, then deterministic weighted
    soft criteria with evidence IDs and sensitivity analysis.
11. `report`: produce `recommendation.md` with observed evidence separated from
    inferred fit, embedded `Review Findings`, and an embedded `Delivery Check`.
12. `validate`: enforce schemas, cross-references, privacy rules, current-source
    freshness, delivery status, and no unsupported rank-affecting claims.

## Provider Registry

Replace linear fallback with a capability matrix. Each provider record must
define supported capabilities, domains, auth requirements, rate/pacing policy,
cache TTL, freshness policy, and downgrade behavior.

Required capabilities:

- `resolve_by_doi`
- `resolve_by_title`
- `venue_recent_by_source`
- `citation_refs`
- `citation_citers`
- `biomed_related`
- `preprint_published_link`
- `oa_status`

Provider-specific policy:

- OpenAlex: default broad metadata source when allowed; support works, sources,
  references, and recent source queries; record API-key/credit status.
- Crossref: support DOI/title/container metadata; use polite-pool metadata when
  configured; record member/ISSN data and 429/403 responses.
- Semantic Scholar: optional unless configured; record fields requested and
  citation/reference pagination truncation.
- arXiv: Atom XML and 3-second pacing; use only for arXiv identifiers or
  explicit topic/preprint expansion.
- bioRxiv/medRxiv: use endpoint/pagination constraints; do not treat as keyword
  search unless supported by the provider client.
- PubMed/PMC: biomedical metadata/related/reference coverage only; do not treat
  PubMed related articles as citation edges.
- Unpaywall: DOI-first OA status only; never fetch PDFs; store only
  `email_configured: true`, not the email address.

## Artifact Schemas

Use closed JSON/JSONL schemas with `schema_version`, stable IDs, timestamps,
status enums, provenance, and unknown-field rejection. Reject duplicate IDs and
broken cross-references. Reuse the deep-research style of stable `S*`, `C*`,
`E*`, and `G*` IDs where applicable.

Required artifacts:

- `run_status.json`
- `selection_plan.json`
- `draft.json`
- `references.jsonl`
- `papers.jsonl`
- `sources.jsonl`
- `queries.jsonl`
- `provider_status.json`
- `evidence.jsonl`
- `claims.jsonl`
- `guards.jsonl`
- `venues.jsonl`
- `venue_profiles.jsonl`
- `recent_papers.jsonl`
- `scores.jsonl`
- `delivery.json`
- `recommendation.md`

Required schema details:

- `draft.json`: `draft_id`, tokenized or relative `draft_path`, `draft_hash`,
  `sensitivity_class`, `redaction_status`, `artifact_visibility`, and
  structural metadata. Raw text is forbidden unless `--retain-draft-text` is
  used.
- `references.jsonl`: `reference_id`, `raw_citation`,
  normalized bibliographic fields, provider IDs, `resolution_status`
  (`resolved`, `ambiguous`, `unresolved`, `not_a_paper`, `excluded`),
  `candidate_work_ids`, `selected_work_id`, and `resolution_reason`.
- `sources.jsonl`: `source_id`, `provider`, `source_url`, `query`, `cache_key`,
  `retrieved_at`, `current_as_of`, `staleness_policy`, and response metadata.
- `evidence.jsonl`: `evidence_id`, `evidence_type`, `source_ids`,
  `paper_ids`, `venue_ids`, `claim_ids`, `provider`, `query_id`,
  `artifact_ref`, `summary`, `created_at`, `inspection_status`,
  `confidence`, and `limitations`.
- `venues.jsonl`: `venue_id`, `canonical_name`, `venue_type`,
  `venue_series`, `venue_instance`, `submission_cycle`, `aliases`,
  `issn`, `eissn`, `issn_l`, `openalex_source_id`,
  `crossref_member`, `s2_publication_venue_id`, `nlm_ta`,
  `publisher_or_org`, `sponsor`, `homepage_url`, `scope_text`,
  `submission_url`, `current_as_of`, `eligibility_status`,
  `exclusion_reason`, `classification_evidence_ids`, and
  `provenance_evidence_ids`.
- `venue_profiles.jsonl`: aims/scope, article types, review model,
  deadlines/frequency, APC/OA policy, indexing, length constraints, audience,
  exclusion criteria, recent-paper sample metadata, and evidence IDs for every
  field.
- `scores.jsonl`: `score_id`, `venue_id`, `rubric_version`, hard eligibility
  gates, `criteria[]`, `criterion_id`, `weight`, `raw_score`,
  `normalized_score`, `evidence_ids`, `missing_data_policy`, `confidence`,
  `sensitivity_result`, `tie_breaker`, and `rationale`.
- `delivery.json`: `delivery_status`, `review_findings_ref`,
  `delivery_check_ref`, unsupported-claim count, stale-source count, privacy
  finding count, and downgrade reasons.

## Privacy, Network, And Mutation Policy

- `init`, `plan`, `extract`, `privacy-gate`, `score`, `report`, `validate`, and
  `smoke` must run locally by default.
- Network calls require `--allow-network` plus explicit allowed providers.
- Provider requests must be HTTPS-only, use domain allowlists, timeouts,
  response-size caps, request caps, and redacted logs.
- Credentials are never copied into artifacts. Store symbolic refs and booleans
  only, such as `semantic_scholar_key_configured: true`.
- Workspaces must be created with private permissions where supported
  (`0700` dirs, `0600` files). Reject workspaces inside the repo checkout,
  canonical runtime source, agent skill directories, or known synced folders
  unless `--unsafe-workspace-ok` is explicit.
- `purge` removes raw caches and draft-derived local artifacts without touching
  source files.
- Zotero integration must be read-only by default. Allowlist only non-mutating
  lookup operations; forbid `add`, `update`, `get`, `sync-cache`,
  `create-collection`, `trash`, `empty-trash`, WebDAV uploads/downloads, and
  staging writes unless explicit mutation/download flags are set.
- Downloads stay out of MVP. If later added, require safe zip member validation,
  basename-only extraction, no absolute paths or `..`, PDF magic checks, size
  caps, quarantine directories, and malicious-archive tests.

## Runtime And OS Support

Add runtime files under
`canonical/runtime/skills/submission-venue-selector/`:

- `submission_venue_selector.py`
- `run_submission_venue_selector.sh`
- `run_submission_venue_selector.ps1`
- `run_submission_venue_selector.bat`
- fixture files under a safe runtime fixture path, avoiding denied names such
  as `config.*`, `provider*`, `.env`, PDFs, DBs, and archives.

Runtime manifest requirements:

- Add `submission-venue-selector` to `manifest/runtime.yaml`
  `runtime_profiles.full.skills`.
- Add source/target entries for all runtime files:
  - `.py`: all platforms, `lf`, `0644`
  - `.sh`: Linux/macOS/WSL, `lf`, `0755`
  - `.ps1`: Windows, `crlf`, `0644`
  - `.bat`: Windows, `crlf`, `0644`
- Add a `runtime-smoke.v1` contract with command keys for `linux`, `macos`,
  `wsl`, `windows`, and `windows_ps1`.
- Add `submission-venue-selector` output validation in
  `installer/ai_agents_skills/runtime_smoke.py`. The smoke JSON must include:
  `status=ok`, `smoke_mode=offline`, `network_required=false`,
  `live_api_attempted=false`, `package_install_attempted=false`,
  `config_written=false`, `real_secrets_read=false`, and canary non-leakage.
- Add unit tests that monkeypatch or block `socket`, `urllib`, and `requests`
  in smoke/offline paths.

Wrapper requirements:

- POSIX wrapper must avoid shell-specific assumptions beyond the repo's
  existing POSIX runner convention.
- PowerShell wrapper must invoke Python as `& $python $script @SkillArgs`, not
  by reconstructing a command string.
- CMD wrapper must use the robust env-argument marshalling pattern used by
  runtime-backed skills with path-heavy CLIs, not naive `%*` forwarding.

## Install Targets

Codex, Claude, DeepSeek, and OpenCode:

- Normal skill install with runtime files managed by the shared runtime root.
- Manifest support must include active skill loading and runtime actions.

Copilot:

- Default-detected adapter install when the Copilot home exists.
- No unsupported instruction, template, command, hook, plugin, or management
  artifacts.
- Add explicit tests for Copilot install selection and blocked artifact classes.

OpenClaw:

- Final target is supported installation, but current runtime-backed OpenClaw
  support is blocked until global OpenClaw gates are implemented.
- Required OpenClaw enablement phase:
  - `validate_openclaw_runtime_root` rejects roots under `.openclaw`, `.codex`,
    `.claude`, `.deepseek`, repo checkouts, workspaces, unsafe mounts,
    world-writable paths, and active loader/config/runtime areas.
  - Add schema-backed support-file metadata, for example
    `manifest/openclaw/target-support-files/submission-venue-selector.json`.
  - Add target-evidence schema/versioning for OpenClaw loader evidence,
    inertness evidence, helper invocation evidence, runtime-root evidence, and
    artifact-specific support-file evidence.
  - Add immutable approval manifest binding, target/runtime realpath binding,
    pre-state hashes, quiescence/lock checks, and write-time pre-state rechecks.
  - Preserve the generic fail-closed OpenClaw runtime-backed block until the
    scoped gate passes.
- Tests before enablement must assert OpenClaw is expected-blocked for this
  runtime-backed skill. Tests after enablement must prove fake-root lifecycle,
  no forbidden artifacts, no real `.openclaw` runtime writes, and correct
  rollback.

## Manifest, Dependencies, And Generated Docs

Implementation must update:

- `manifest/skills.yaml`: skill entry, `supported_agents`, dependencies, and
  profiles.
- `manifest/profiles.yaml`: reciprocal profile membership. Prefer adding to
  `full-research` first; add to `serious-research` only after trigger and smoke
  behavior are stable, or create a narrower venue-selection profile.
- `manifest/runtime.yaml`: runtime files, smoke contract, and
  `runtime_profiles.full.skills`.
- `manifest/dependencies.yaml`: use existing `python-runtime` and
  `requests-python-package`; make `requests-python-package` required unless a
  stdlib `urllib` provider fallback is implemented.
- `manifest/system-dependencies.yaml`: add `submission-venue-selector` to
  `python-runtime.used_by` and to package `used_by` lists if imports require
  them.
- `installer/ai_agents_skills/docs.py`: update `write_readme`, and any
  relevant generated `installation_text`, `verification_text`,
  `architecture_text`, and `agent_locations_text` sections.
- `docs/source/index.md`: add a toctree entry for this plan if it remains a
  public docs page.
- `README.md` and `docs/`: regenerate with `make docs`.

This plan document is a public docs page and must exist as both
`docs/submission-venue-selector-plan.md` and
`docs/source/submission-venue-selector-plan.md` with identical content.

## User-Facing Examples

Add exact POSIX and Windows examples in `SKILL.md`, generated docs, and README
navigation. Examples must cover:

- full automated run
- planning-only run
- provider check with no network
- validate existing workspace
- interpret `ready`, `ready-with-caveats`, and `not-ready`
- purge private artifacts

Example POSIX command:

```bash
bash ~/.codex/runtime/run_skill.sh \
  skills/submission-venue-selector/run_submission_venue_selector.sh \
  run --dir ~/venue-selection/run-001 --draft ~/drafts/paper.tex --offline
```

Example PowerShell command:

```powershell
& "$env:USERPROFILE\.codex\runtime\workspace\skills\submission-venue-selector\run_submission_venue_selector.ps1" `
  run --dir "$env:USERPROFILE\venue-selection\run-001" `
  --draft "$env:USERPROFILE\drafts\paper.tex" --offline
```

## Test Plan

Runtime unit tests:

- `init` creates private workspace scaffolding and never stores raw draft text
  by default.
- `plan` creates deterministic `selection_plan.json`.
- `extract` handles `.bib`, `.tex`, markdown/text, and Docling-exported text
  fixtures.
- `privacy-gate` blocks unsafe provider queries for unpublished drafts.
- `providers --check` writes provider capability/status records without
  reading real secrets in offline/smoke mode.
- `resolve` handles fixture responses and ambiguous candidates.
- `expand` respects hop, paper, provider, and edge-type caps and records
  truncation.
- `venues` normalizes aliases, ISSNs, OpenAlex/Crossref/Semantic Scholar/PubMed
  identifiers, and conference series/instances.
- `recent` records query, sample, year, and topic-threshold metadata.
- `score` is deterministic and records component evidence.
- `report` embeds review findings and delivery check.
- `validate` covers `ready`, `ready-with-caveats`, and `not-ready`.
- `purge` removes derived private artifacts only.
- `smoke` is offline JSON and attempts no network, config, secrets, package
  install, downloads, or mutations.

Provider-failure fixtures:

- missing Crossref mailto, Crossref 429/403
- OpenAlex exhausted credits
- Semantic Scholar omitted fields and paginated citation truncation
- arXiv XML parsing and pacing
- bioRxiv unsupported keyword-search request
- NCBI missing tool/email
- Unpaywall placeholder email/422
- duplicate venues with print/eISSN
- renamed journals
- ambiguous conference acronyms
- preprint server/repository mistaken for venue

Installer and manifest tests:

- `tests/test_installer.py`: manifest entry, supported agents, dependencies,
  reciprocal profile membership, Copilot explicit behavior, and OpenClaw
  expected-block/enablement behavior.
- `tests/test_runtime_integration.py`: runtime file coverage, command targets
  for Linux/macOS/WSL/Windows/Windows PowerShell, newline/mode policies, full
  runtime profile membership, and smoke contract shape.
- Runtime smoke validator tests for `submission-venue-selector` JSON fields and
  canary non-leakage.
- Docs tests proving the public plan page is in both root/source docs, in the
  Sphinx toctree, and current after `make docs`.
- Fake-root lifecycle tests must fail if a requested target silently produces
  no managed skill action without an expected skip/block reason.

Native OS verification:

- Linux POSIX runtime smoke.
- macOS POSIX runtime smoke.
- Windows CMD runtime smoke.
- Windows PowerShell runtime smoke.
- WSL POSIX runtime smoke, or a documented WSL layout-only limitation until a
  real WSL job exists.

Required verification before claiming implementation complete:

```bash
make docs
git diff --exit-code -- README.md docs
make docs-site
make test
make runtime-smoke ARGS="--skills submission-venue-selector"
make fake-root-lifecycle ARGS="--skill submission-venue-selector --platform-shape all"
make fake-root-lifecycle ARGS="--agents codex,claude,deepseek,copilot,openclaw --skill submission-venue-selector --platform-shape all"
```

The final all-target lifecycle command is allowed to pass with an explicit
OpenClaw expected-block only before the OpenClaw enablement phase. After that
phase, it must prove OpenClaw fake-root install, verify, uninstall, and rollback
without forbidden artifacts or runtime writes under `.openclaw`.

## Acceptance Criteria

- Full automation can produce a venue recommendation dossier without manual
  ranking.
- All rank-affecting claims are backed by evidence IDs.
- Draft privacy gates are enforced before network calls.
- Provider capability gaps are visible and affect delivery status.
- Recommendations do not claim acceptance probability or prestige unless backed
  by explicit evidence and allowed by the rubric.
- Runtime-backed installation is validated for every final install target and
  OS runtime path.
- OpenClaw is not claimed complete until the repo's runtime-backed OpenClaw
  gates are implemented and verified.
