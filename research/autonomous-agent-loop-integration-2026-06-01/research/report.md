# Autonomous Research Loop Integration Plan

## Scope And Limits

This report analyzes ECC's `continuous-agent-loop` skill, adjacent autonomous
loop skills, official/provider workflow surfaces for Codex, Claude, Copilot,
and DeepSeek/CodeWhale, and this repository's current research workflow. The
goal is a plan, not an implementation. The updated scope also covers every
current install target and every supported OS/substrate shape in this repo:
Codex, Claude, DeepSeek, Copilot, OpenClaw fake roots, Linux, macOS, Windows,
WSL, and mounted-profile path checks.

External sources and the first local evidence pass were inspected on
2026-06-01. The install-target and OpenClaw/runtime-backed plan repair pass was
performed on 2026-06-02. Provider capabilities, model availability, and CLI
behavior are time-sensitive and must be rechecked before implementation. No
live external provider CLI was launched. No code or target adapter behavior was
changed.

## Evidence Summary

- ECC's current `continuous-agent-loop` is a compact taxonomy of loop patterns,
  quality gates, session persistence, and failure recovery. Its fuller
  `autonomous-loops` predecessor is Claude-specific and includes concrete
  `claude -p` and PR-loop patterns. Treat these as design input, not a portable
  runtime to import unchanged. Sources: S1, S2.
- Similar skills converge on bounded iteration, file-backed state, evaluator or
  acceptance checks, quality gates, explicit stop criteria, and recovery logs.
  Sources: S3-S6.
- Official provider surfaces differ. Codex has skills, planning/review/session
  controls, subagents, MCP, and automations; Claude has skills, subagents,
  hooks, settings, max-turn/budget CLI flags, and JSON output; Copilot has
  cloud agents, skills, custom agents, MCP, and repository PR workflows;
  DeepSeek is primarily model/API and tool-calling infrastructure, with
  CodeWhale as the relevant local coding-agent harness. Sources: S7-S16.
- This repo already has the core spine: research briefing, deep-research
  ledgers, source/claim/guard/delivery files, multi-agent discussion templates,
  external delegation policy, runtime wrappers, target adapters, and precheck
  logic. Sources: S17-S20.
- The multi-agent panel converged on a narrow v1 research-loop controller,
  not a general free-running agent launcher. Source: S21.
- The installer has explicit target and OS behavior: Codex, DeepSeek, and
  Copilot default to reference adapters; Claude defaults to symlinked skills;
  OpenClaw is explicit fake-root-only; runtime runners split between POSIX
  `run_skill.sh` and Windows `run_skill.ps1` / `run_skill.bat`; and lifecycle
  tests accept `--platform-shape linux|macos|windows|wsl|all`. Source: S22.
- Current planner rules block OpenClaw skill-file installation when the skill is
  runtime-backed, block OpenClaw reference/symlink modes, and block OpenClaw
  support files unless they have target-support metadata. Therefore the
  all-target plan must not make the canonical OpenClaw-visible skill itself
  runtime-backed. Source: S23.

## Recommendation

Create a new canonical instruction skill named `autonomous-research-loop`.

Keep `autonomous-agent-loop` and `continuous-agent-loop` as aliases, search
terms, and compatibility language, but do not make them the primary v1 scope.
The narrower name is important because this repository's safety model is
research-ledger-first and parent-owned: external agents may produce evidence,
but they should not become self-authorizing controllers.

To satisfy all install targets, keep `autonomous-research-loop` non-runtime-
backed and support-file-free in v1. Put executable helpers in a separate
runtime companion, tentatively `autonomous-research-loop-runtime`, or route
them through an existing runtime-backed skill. The companion must not include
OpenClaw in `supported_agents` until neutral OpenClaw runtime evidence exists.

## V1 Skill Contract

The skill should guide a bounded research loop with a required loop contract:

- `goal`
- `success_criteria`
- `max_iterations`
- `max_wall_time`
- `max_tokens`
- `max_usd`
- `max_depth`
- `max_hops`
- `max_child_workers`
- `plateau_rule`
- `stop_on_guard_fail`
- `stop_on_missing_evidence`
- `stop_on_scope_change`

The runtime state should be file-backed and compatible with the existing
deep-research workflow:

- `loop_state.json`
- `iterations.jsonl`
- `budget.json`
- `recovery.md`
- references to `sources.jsonl`, `claims.jsonl`, `guards.jsonl`,
  `delivery.json`, and optional `delegation/` artifacts

Minimum iteration fields:

- `iteration_id`
- `mode`
- `objective`
- `input_refs`
- `actions_taken`
- `outputs`
- `source_ids_added_or_changed`
- `claim_ids_added_or_changed`
- `evidence_ids_added_or_changed`
- `guard_refs`
- `budget_snapshot_ref`
- `decision`
- `stop_reason`
- `remaining_gaps`

## Loop Modes

V1 should ship four modes:

1. `scout-loop`: bounded discovery of candidate sources and preliminary `S*`
   records.
2. `analysis-loop`: claim/evidence refinement, conflict tracking, and guard
   updates.
3. `review-repair-loop`: draft review, parent-approved repair, and recheck.
4. `panel-loop`: prepare and validate parent-owned `agent-group-discuss`
   artifacts. It must not dispatch subagents itself.

Defer digest/watch loops, CI/PR loops, and general autonomous coding loops until
the research-loop contract has stable tests and adoption.

## Runtime Boundary

The v1 executable helper, if implemented, should be a separate offline-only
runtime companion. It may:

- initialize/scaffold a loop run directory
- append iteration records
- validate loop contracts and JSONL ledgers
- show status and readiness
- run selftests against local sample artifacts

It must not:

- dispatch provider CLIs
- spawn agents
- access the network
- mutate repository code or target adapters
- write outside the declared run directory
- copy provider config, credentials, approval receipts, runtime authority,
  session IDs, or model settings into delegation packets

Live provider work should remain parent-owned through existing
`agent-group-discuss` or `delegate-agent` paths, after explicit confirmation and
provider probes.

## Target Matrix

| Target | V1 integration stance | Required install behavior | Important limit |
|---|---|---|
| Codex | Canonical skill plus Codex-facing guidance for `/goal`, `/plan`, `/review`, subagents, skills, and research ledgers. | `supported_agents` includes `codex`; auto mode writes a reference adapter under `~/.codex/skills/<skill>/SKILL.md`; optional persona is TOML; entrypoint alias is a reference doc. | Do not rely on uninspected future Codex surfaces. Recheck current model/tool availability before implementation. |
| Claude | Symlink/native-skill-friendly adapter, optional command guidance for `claude -p`, budgets, hooks, JSON output, and subagents. | `supported_agents` includes `claude`; auto mode symlinks the skill where supported; optional persona is Markdown subagent; entrypoint alias may be a command file. | Claude-specific command syntax should live in target guidance, not the canonical skill body. |
| DeepSeek/CodeWhale | Reference adapter for DeepSeek API/tool-calling concepts plus CodeWhale harness guidance where locally available. | `supported_agents` includes `deepseek`; auto mode writes a reference adapter under `~/.deepseek/skills/<skill>/SKILL.md`; optional persona/entrypoint are reference prompts/docs. | Do not claim a native DeepSeek skill-loader; CodeWhale is third-party and must be probed before live support claims. |
| Copilot | Personal adapter/profile and optional skill/custom-agent guidance. | Include `copilot` as an explicit adapter target if the manifest supports the skill for all targets; auto mode writes `~/.copilot/skills/<skill>/SKILL.md`; optional persona is `.agent.md`. | Do not write repo-level `.github/*` assets in v1 and do not claim local parity with Copilot cloud agent execution. |
| OpenClaw | Restricted compatibility target only. | Include `autonomous-research-loop` only if the canonical skill has no runtime manifest entry and no support files; copy mode in explicit fake roots only. Exclude any runtime companion from OpenClaw until neutral runtime evidence exists. | No real `.openclaw` writes, no runtime-backed skill install, no instruction blocks, no symlink/reference mode, and no native loader claim until OpenClaw target evidence exists. |

OpenClaw is not default-detected. It needs an explicit OpenClaw-target check;
the normal fake-root lifecycle command without an OpenClaw agent filter is not
evidence that OpenClaw behavior was exercised.

## OS And Substrate Matrix

| OS/substrate | Required implementation support | Verification before readiness claim |
|---|---|---|
| Linux | POSIX runtime runner support through `run_skill.sh`; LF shell files; normal `make` wrappers. | `make lifecycle-test ARGS="--matrix default --platform-shape linux"`, `make fake-root-lifecycle ARGS="--skill autonomous-research-loop --platform-shape linux"`, and, after the companion declares smoke coverage, `make runtime-smoke ARGS="--skills autonomous-research-loop-runtime"`. |
| macOS | Same POSIX runner path as Linux, with macOS platform-shape coverage and no Linux-only path assumptions. | `make lifecycle-test ARGS="--matrix default --platform-shape macos"` and `make fake-root-lifecycle ARGS="--skill autonomous-research-loop --platform-shape macos"`; native macOS runtime smoke is required before claiming native execution. |
| Native Windows | PowerShell and CMD runtime entrypoints for the companion helper; CRLF for Windows launcher files; no POSIX-only helper assumption. | `make.bat lifecycle-test --matrix default --platform-shape windows`, `make.bat fake-root-lifecycle --skill autonomous-research-loop --platform-shape windows`, and, after the companion declares smoke coverage, `make.bat runtime-smoke --skills autonomous-research-loop-runtime`. |
| WSL | POSIX runtime behavior with WSL path conventions; do not treat WSL-backed tools as native Windows tools. | `make lifecycle-test ARGS="--matrix default --platform-shape wsl"` plus dependency/precheck evidence from the WSL substrate. |
| Mounted Windows profile from Linux/WSL | Reference adapters must point to paths readable by the target runtime; otherwise use native Windows checkout or copy mode. | Treat Linux-hosted `--platform-shape windows` checks as install-shape evidence only. Native Windows `make.bat` remains required for native execution claims. |
| Git Bash/MSYS | May run POSIX helpers, but the target shape is still Windows when the agent runtime is native Windows. | Do not accept Git Bash success alone as native Windows support; confirm with PowerShell/CMD runner checks. |

## All-Target Manifest Requirements

The new skill should not be marked "works for all targets" unless the
implementation includes:

- `manifest/skills.yaml`: for `autonomous-research-loop`, `supported_agents`
  deliberately covers `codex`, `claude`, `deepseek`, `copilot`, and `openclaw`
  only if the canonical skill remains non-runtime-backed and support-file-free.
  For `autonomous-research-loop-runtime`, `supported_agents` should cover
  `codex`, `claude`, `deepseek`, and `copilot`, but not `openclaw`.
- `manifest/profiles.yaml`: add the skill to `research-core`,
  `serious-research`, `full-research`, and probably `workflow-tools`; include
  `multi-agent` only if `panel-loop` ships in v1.
- `manifest/runtime.yaml`: declare portable runtime files only for the runtime
  companion, not the OpenClaw-visible canonical skill. Include Linux, macOS,
  WSL, and Windows files, including `.sh`, `.ps1`, and `.bat` entrypoints.
  Runtime files must remain root-scoped, never installed inside per-agent skill
  folders.
- Target adapters: verify rendered `SKILL.md` output for Codex, Claude,
  DeepSeek, Copilot, and explicit OpenClaw fake-root paths.
- Target documentation: document asymmetric behavior instead of flattening all
  agents into one capability claim.

## Implementation Phases

1. Attribution, target, and OS spec:
   inspect upstream licenses and write a short implementation spec that
   distinguishes adapted ideas from copied text. The spec must include the
   target x OS matrix above and explicitly state which claims require native
   OS evidence.
2. Canonical skill:
   add `canonical/skills/autonomous-research-loop/SKILL.md` with aliases,
   trigger rules, loop contract, mode selection, safety gates, and handoff
   guidance to `deep-research-workflow`, `research-report-reviewer`,
   `research-verification-gate`, and `agent-group-discuss`.
3. Offline runtime companion:
   add a separate runtime-backed companion, tentatively
   `autonomous-research-loop-runtime`, with POSIX and Windows helper entrypoints
   under the runtime workspace, for example `run_autonomous_research_loop.sh`,
   `run_autonomous_research_loop.ps1`, and
   `run_autonomous_research_loop.bat`, with `init`, `append-iteration`,
   `validate`, `status`, and `selftest`. Do not support OpenClaw for this
   companion until neutral runtime evidence exists.
4. Manifests and profiles:
   wire the non-runtime canonical skill into `manifest/skills.yaml` and the
   appropriate install profiles, likely `workflow-tools`, `serious-research`,
   `full-research`, `research-core`, and optionally `multi-agent` if
   `panel-loop` ships. Wire the runtime companion separately into
   `manifest/skills.yaml` and `manifest/runtime.yaml` only if executable helper
   files are implemented. Copilot should be included as an adapter target.
   OpenClaw should be included only for the non-runtime canonical skill and
   only if its fake-root-only, no-support-file, and runtime-blocked constraints
   are satisfied.
5. Target adapters:
   add Codex, Claude, DeepSeek/CodeWhale, Copilot, and OpenClaw target guidance
   with the asymmetric limits above.
6. Docs and tests:
   update workflow docs, add sample loop fixtures, test JSONL validation,
   verify offline helper behavior, and run the repo's relevant installer/runtime
   checks.
7. All-target and all-OS acceptance:
   run the fake-root and lifecycle matrix before readiness claims:

   ```bash
   make lifecycle-test ARGS="--matrix default --platform-shape all"
   make fake-root-lifecycle ARGS="--skill autonomous-research-loop --platform-shape all"
   make test
   make docs-check
   ```

   If the runtime companion is implemented and declares an offline smoke
   contract, also run:

   ```bash
   make runtime-smoke ARGS="--skills autonomous-research-loop-runtime"
   ```

   Because OpenClaw is explicit-only, also run an explicit OpenClaw fake-root
   check through the direct CLI form, with global `--agents` before the
   subcommand:

   ```bash
   python3 -m installer.ai_agents_skills \
     --agents openclaw \
     fake-root-lifecycle \
     --skill autonomous-research-loop \
     --platform-shape all
   ```

   On native Windows, also run:

   ```bat
   make.bat lifecycle-test --matrix default --platform-shape windows
   make.bat fake-root-lifecycle --skill autonomous-research-loop --platform-shape windows
   make.bat test
   ```

   If the runtime companion is implemented and declares an offline smoke
   contract, also run:

   ```bat
   make.bat runtime-smoke --skills autonomous-research-loop-runtime
   ```

   If native Windows OpenClaw fake-root coverage is required, use the direct
   Python CLI with global `--agents openclaw` before `fake-root-lifecycle`,
   because the Make wrapper form passes arguments after the subcommand.

   Run a native macOS check before claiming native macOS runtime execution.
8. Optional provider probe phase:
   only after user opt-in, run dry-run and then live probes for transport,
   auth/config presence without secret disclosure, latest model, highest
   reasoning/thinking, output contract, final marker, timeout behavior,
   file-read fidelity, and nested-worker constraints.

## Deferred Or Unsafe For V1

- A free-running "never stop" loop.
- Runtime helper execution of external CLIs.
- Provider support claims based only on CLI detection.
- Repository-level Copilot cloud-agent assets.
- DeepSeek native-skill claims beyond API and CodeWhale-harness guidance.
- Real `.openclaw` writes or OpenClaw runtime-backed skill installs.
- All-OS readiness claims based only on Linux-hosted fake-root shape tests.
- Runtime-smoke readiness before the runtime companion has a manifest-declared
  offline smoke contract.
- Importing upstream skill text before license/attribution review.

## Delivery Check

Evidence coverage: S1-S23 cover the requested ECC skill, similar skills,
official provider docs, local repo workflow surfaces, local precheck output, and
multi-agent panel consensus. S22 specifically covers install targets,
artifact surfaces, OS/substrate distinctions, runtime runner split, lifecycle
matrix commands, and OpenClaw fake-root-only constraints. S23 covers the
planner/test evidence that runtime-backed skills are currently blocked for
OpenClaw.

Date coverage: external/provider sources were inspected on 2026-06-01; the
local plan repair and OpenClaw/runtime-backed evidence pass was inspected on
2026-06-02.

Decision: ready with caveats as an integration plan. Not ready as an
implementation or a live cross-provider runtime support claim.

Remaining gaps: license/attribution review, code-level task breakdown, live
provider probes, native Windows checks, native macOS checks, and any OpenClaw
native-target promotion are intentionally deferred to the implementation phase.
