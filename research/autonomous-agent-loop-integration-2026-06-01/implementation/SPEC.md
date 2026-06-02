# Specification

## Goal

Implement the approved autonomous research loop integration as a support-file-free canonical skill plus an optional runtime-backed companion. The canonical skill must be installable for Codex, Claude, DeepSeek, Copilot, and explicit OpenClaw fake roots. The runtime companion must provide offline helper commands for non-OpenClaw targets only.

## Scope

- In scope:
  - Add `autonomous-research-loop` as a canonical instruction skill.
  - Add `autonomous-research-loop-runtime` as a separate runtime-backed companion.
  - Wire manifests and profiles for current install targets and OS shapes.
  - Add focused tests for OpenClaw separation, runtime platform files, smoke selection, and helper behavior.
- Out of scope:
  - Live provider CLI dispatch.
  - Real `.openclaw` writes.
  - Repository-level Copilot `.github/*` artifacts.
  - Native Windows/macOS execution in this Linux session.

## Assumptions

- OpenClaw fake-root support requires the canonical skill to have no runtime manifest entry and no support files.
- Runtime helpers can be provided through a separate skill that excludes OpenClaw.
- Runtime smoke can use the existing generic smoke contract plus a focused validation branch.

## Interfaces

- `canonical/skills/autonomous-research-loop/SKILL.md`
- `canonical/skills/autonomous-research-loop-runtime/SKILL.md`
- `canonical/runtime/skills/autonomous-research-loop-runtime/*`
- `manifest/skills.yaml`
- `manifest/profiles.yaml`
- `manifest/runtime.yaml`
- `installer/ai_agents_skills/runtime_smoke.py`
- `tests/`

## Acceptance Criteria

- `autonomous-research-loop` installs as a skill-file for explicit OpenClaw fake roots.
- `autonomous-research-loop-runtime` is not supported for OpenClaw and is runtime-backed for Codex, Claude, DeepSeek, and Copilot.
- Runtime helper supports `init`, `append-iteration`, `validate`, `status`, and `selftest`.
- Runtime smoke can select and execute the companion skill.
- Manifest validation and focused tests pass.

## Verification

- `python3 -m unittest tests.test_autonomous_research_loop -v`
- `python3 -m installer.ai_agents_skills --json runtime-smoke --skills autonomous-research-loop-runtime`
- `python3 -m installer.ai_agents_skills docs-check`
- `bash ~/.codex/runtime/run_skill.sh skills/deep-research-workflow/run_deep_research_workflow.sh validate --dir research/autonomous-agent-loop-integration-2026-06-01/research`

## Risks

- Generated docs may need refresh after manifest changes.
- Native Windows/macOS execution remains unverified in this session.
- OpenClaw support must remain fake-root-only until separate native evidence exists.
