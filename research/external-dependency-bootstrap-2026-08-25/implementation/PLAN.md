# External Dependency Bootstrap Task Plan

## Context

The full-system audit found the course-management and VNU eOffice skill
dependencies missing. The installer currently detects those imports but does
not provision their upstream source projects. Native course skills already
require `~/.course_venv`; VNU detection already reserves
`~/.vnu-eoffice_venv` as the authoritative candidate.

## Steps

1. Add a validated, allowlisted external-source manifest and a pure plan
   builder for the two source pins.
2. Implement guarded source generation, dependency-wheel resolution, offline
   installation, verification, receipt writing, and atomic pointer activation.
3. Expose the command through the CLI and Makefile; document its host-only
   target matrix and non-reproducible dependency limitation.
4. Add focused tests, then run repository quality gates.
5. Produce and inspect a real-host dry run, apply its exact confirmed plan,
   propagate the five dependent skill bodies, and re-run prechecks.
6. Inspect OpenClaw independently for `vnu_eoffice` and
   `course_hoanganhduc`; do not modify it.

## Decisions

| Decision | Rationale | Status |
|---|---|---|
| Fixed bundle allowlist, not user-provided URLs | Prevents command/remote injection and makes source review possible. | accepted |
| Full commit pins and recorded tree/archive hashes | The commit cryptographically binds the source tree; receipts make the applied object auditable. | accepted |
| Non-editable wheel install | Avoids a mutable checkout remaining on the runtime import path. | accepted |
| Transaction-backed generation plus stable pointer | Preserves a known prior generation, avoids relocating venv console scripts, and makes a failed multi-bundle apply retry-safe. | accepted |
| Wheel-only, offline installation after acquisition | Keeps package installation deterministic within one generation and prevents resolver network access during activation. | accepted |
| Scrubbed child environment plus explicit risk acknowledgement | Reduces credential/config inheritance into Git, build, pip, import, and help processes; it does not claim to sandbox fetched code. | accepted |
| OpenClaw excluded from provisioning | Its locked image has a distinct execution boundary. | accepted |
| Native Windows apply blocked | Existing installer cannot yet bind mutation to validated Windows handles. | accepted |

## Verification Plan

| Check | Command or method | Expected result |
|---|---|---|
| Focused command tests | `python -m unittest tests.test_external_dependencies -v` | all pass without network, including stale-plan and transaction rollback/retry cases |
| Existing contract tests | course/VNU/discovery tests | existing behavior preserved |
| Static/docs/sanitize | `make static-check`, `make docs-check`, `make sanitize-check` | all pass |
| Full suite | `make test` | all pass |
| Real dry run | `provision-external --json` | no mutations, bounded plan + digest |
| Real application | confirmed `provision-external --apply` | both verified generations active |
| Target propagation | selected-skill install + precheck | all native target artifacts and imports ready |
| OpenClaw check | read-only provider/image probe | package statuses reported separately |
