# Task Plan

## Context

The research plan recommends a non-runtime `autonomous-research-loop` skill and a separate runtime companion to preserve OpenClaw fake-root compatibility while still providing offline helper commands.

## Steps

1. Add canonical skill files.
2. Add runtime helper files and wrappers.
3. Wire manifests and profiles.
4. Add focused tests.
5. Run targeted verification and update generated docs if required.

## Decisions

| Decision | Rationale | Status |
|---|---|---|
| Keep canonical skill support-file-free | Current OpenClaw planner blocks support files and runtime-backed skills. | Accepted |
| Add a separate runtime companion | Provides executable helpers without blocking OpenClaw canonical skill installs. | Accepted |
| Exclude OpenClaw from runtime companion | Runtime-backed OpenClaw skills require neutral runtime evidence. | Accepted |

## Verification Plan

| Check | Command or method | Expected result |
|---|---|---|
| Focused tests | `python3 -m unittest tests.test_autonomous_research_loop -v` | Pass |
| Runtime smoke | `python3 -m installer.ai_agents_skills --json runtime-smoke --skills autonomous-research-loop-runtime` | `status: ok` |
| Docs consistency | `python3 -m installer.ai_agents_skills docs-check` | Pass or identify generated docs to refresh |
| Research ledger validation | Deep research workflow `validate` command | Pass |

## Verification Results

| Check | Result |
|---|---|
| `python3 -m unittest tests.test_autonomous_research_loop -v` | Passed, 5 tests |
| `python3 -m installer.ai_agents_skills --json runtime-smoke --skills autonomous-research-loop-runtime` | Passed, `status: ok` |
| `python3 -m installer.ai_agents_skills docs-check` | Passed after regenerating manifest-derived docs |
| `bash ~/.codex/runtime/run_skill.sh skills/deep-research-workflow/run_deep_research_workflow.sh validate --dir research/autonomous-agent-loop-integration-2026-06-01/research` | Passed, `status: ok` |
| `python3 -m installer.ai_agents_skills --agents openclaw fake-root-lifecycle --skill autonomous-research-loop --platform-shape all` | Passed for Linux, macOS, Windows, and WSL fake-root shapes |
| `python3 -m installer.ai_agents_skills --agents codex,claude,deepseek,copilot fake-root-lifecycle --skill autonomous-research-loop-runtime --platform-shape all` | Passed for Linux, macOS, Windows, and WSL fake-root shapes |
| `python3 -m unittest -v` | Passed, 332 tests; 5 Windows-host-specific tests skipped |

## Remaining Gaps

- OpenClaw receives only the non-runtime canonical skill. The runtime companion is explicitly blocked for OpenClaw until neutral runtime evidence exists.
- The runtime helper is offline ledger support only. It does not dispatch provider CLIs or spawn subagents.
