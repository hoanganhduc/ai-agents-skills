# Deep Research Analysis

## Scope

Question: how to integrate an autonomous-loop skill, inspired by ECC
`continuous-agent-loop` and similar skills, into this repository's current
research workflow for Codex, Claude, Copilot, DeepSeek/CodeWhale, and all
current install-target/OS surfaces, including OpenClaw fake roots.

Time boundary: sources were inspected on 2026-06-01. Provider surfaces are
time-sensitive and should be rechecked before implementation. The
install-target repair pass for OpenClaw/runtime-backed behavior was performed
on 2026-06-02.

Exclusions: no repo implementation was performed. Reddit and paper sources were
excluded from the main analysis. Live Claude, Copilot, and DeepSeek/CodeWhale
provider probes were not run; this report only plans how those probes should be
gated before any active provider support claim. Native Windows/macOS execution
and real OpenClaw writes were not run.

## Claims Ledger

For structured runs, mirror claim records into `claims.jsonl` with stable
`claim_id` values (`C1`, `C2`, ...) and source or evidence links.

| Claim | Evidence IDs | Confidence | Gaps |
|---|---|---|---|
| C1: ECC's loop family is best treated as a pattern taxonomy plus production-stack sketch, not as a ready-to-run portable runtime. | S1, S2 | high | Need upstream raw snapshot if implementing attribution/license headers. |
| C2: Similar loop skills converge on bounded iteration, file-backed state, measurable acceptance/evaluator signals, quality gates, and explicit stop/recovery rules. | S3, S4, S5, S6 | medium | Some sources are registry mirrors rather than upstream raw files. |
| C3: Provider support is asymmetric: Codex/Claude/Copilot expose native skills and agent/subagent surfaces; DeepSeek is primarily a model/API backend, with CodeWhale as the relevant third-party coding harness in this repo. | S7-S16, S19 | high | DeepSeek/CodeWhale behavior should be probed locally before claiming runtime support. |
| C4: This repo already has the right integration spine: research briefing, deep-research ledgers, agent-group discussion, external CLI delegation policy, runtime wrappers, target adapters, and verification gates. | S17-S20 | high | Need implementation-specific file plan from panel and maintainer choice. |
| C5: Autonomous research loops should remain parent-owned and evidence-gated here; external provider outputs must enter as validated artifacts/evidence, not as self-authorizing conclusions. | S17, S18, S20 | high | Cross-provider live dispatch still needs explicit confirmation and runtime probes. |
| C6: The v1 canonical skill should be `autonomous-research-loop`, with broader loop names retained only as aliases/discovery text. | S17, S18, S21 | high | Maintainer may choose a different public name. |
| C7: Any runtime companion should be offline-only and should not dispatch provider CLIs, spawn agents, use the network, mutate the repo, or write outside the declared run directory. | S17, S18, S20, S21 | high | Later live dispatch would need separate opt-in integration. |
| C8: Target support claims must stay asymmetric across Codex, Claude, Copilot, and DeepSeek/CodeWhale. | S7-S16, S19, S21 | high | Live provider behavior was not probed. |
| C9: Implementation should proceed through attribution/spec, non-runtime canonical skill, optional separate runtime companion, manifest/profile wiring, target adapters/docs/tests, then optional dry-run delegation probes. | S17-S21, S23 | medium | Detailed task breakdown is a follow-up engineering phase. |
| C10: All-install-target coverage means Codex, Claude, DeepSeek, Copilot, and OpenClaw, with OpenClaw explicit fake-root-only and runtime-backed skills blocked until native evidence exists. | S18, S19, S22 | high | Real-system OpenClaw writes remain blocked. |
| C11: All-OS coverage requires POSIX and native Windows runtime entrypoints and separate Linux, macOS, Windows, WSL, and mounted-profile path-shape checks. | S22 | high | Native Windows and macOS were not executed in this session. |
| C12: Readiness should require lifecycle, fake-root, explicit OpenClaw fake-root coverage, runtime-smoke after a companion smoke contract, docs, tests, and native Windows gates before all-target/all-OS claims. | S22, S23 | high | Verification awaits implementation. |
| C13: The canonical skill must remain non-runtime-backed and support-file-free if it is to be installable in explicit OpenClaw fake roots under current planner rules. | S22, S23 | high | Future OpenClaw target evidence could change this. |
| C14: Runtime-smoke readiness should apply to a separate companion runtime helper only after it declares an offline smoke contract. | S22, S23 | high | The companion helper is not implemented yet. |
| C15: OpenClaw coverage requires explicit OpenClaw target selection because OpenClaw is not default-detected. | S22, S23 | high | Exact implementation command depends on whether the Make wrapper exposes global agent arguments. |

## Conflicts

| Conflict | Sources | Resolution or status |
|---|---|---|
| Some community skills say "never stop" once a loop begins, while this repo and provider docs emphasize permissions, budgets, hooks, review, and user confirmation. | S4 vs S7-S20 | Resolve in favor of this repo's stricter parent-owned budget/confirmation gates; "never stop" can only apply inside an explicitly budgeted run. |
| ECC's detailed predecessor is Claude-specific (`claude -p`, Claude Code commands), while the target repo supports Codex, Claude, DeepSeek, and Copilot. | S2 vs S17-S20 | Port the abstract loop primitives, not the Claude command syntax, into canonical skill plus target adapters/runtime helper. |
| Copilot can run autonomously in GitHub cloud agent, but this repo's personal Copilot target is adapter-only and does not write repository `.github/*` surfaces. | S12-S14 vs S19 | Treat Copilot support as reference/adapter plus optional future repository-level artifact plan, not automatic local runtime parity. |
| DeepSeek official docs provide model/API tool calling, while CodeWhale provides the actual coding-agent harness used by local target guidance. | S15 vs S16/S19 | Separate "DeepSeek backend capability" from "CodeWhale agent harness capability" in any support claim. |

## Findings

1. The integration should be a new canonical skill named
   `autonomous-research-loop`, rather than a direct import of ECC's
   `continuous-agent-loop`. The repo needs research ledgers, source checks,
   delegation validation, and final delivery gates that ECC does not encode in
   the compact canonical skill.
2. The skill should present a loop-selection matrix:
   `scout-loop`, `analysis-loop`, `review-repair-loop`, and `panel-loop`.
   `panel-loop` should prepare and validate parent-owned multi-agent artifacts,
   not dispatch subagents itself.
3. The runtime-backed portion should be small and safe: scaffold a loop run
   directory, validate config, append iteration records, record guard outputs,
   and report readiness. It should not run provider CLIs, spawn agents, use the
   network, mutate the repository, or write outside the run directory.
4. For Codex, the natural mapping is `/goal`, skills, subagents, plan/review,
   and automations. For Claude, map to skills, `claude -p`, subagents,
   hooks, max turns/budget, and permission modes. For Copilot, map to agent
   skills/custom agents/cloud-agent PR loops but do not assume the personal
   installer writes repo-level `.github` assets. For DeepSeek, map to API/tool
   capabilities plus CodeWhale harness behavior where locally available.
5. Implementation should be phased: attribution/spec first; then a non-runtime
   canonical skill, optional separate runtime companion, manifest/profile
   wiring, target adapters, docs/tests, and optional dry-run delegation probes.
6. The updated plan must treat "all targets and OSs" as an acceptance matrix:
   Codex, Claude, DeepSeek, Copilot, and OpenClaw each need an explicit artifact
   policy, while Linux, macOS, Windows, WSL, mounted Windows profiles, and Git
   Bash/MSYS each need an explicit verification interpretation.

## Remaining Checks

- If implementation follows, inspect license/attribution requirements for any
  imported or adapted skill text.
- Probe external providers with `delegate-agent --dry-run` and, only if the
  user opts in, live transport/output-contract checks.
- Run all-target and all-OS acceptance checks after implementation, including
  native Windows and native macOS checks before claiming native execution.
- Keep OpenClaw real-system writes blocked unless a separate native target
  evidence path approves them.
- Keep the OpenClaw-visible canonical skill out of `manifest/runtime.yaml`;
  use a separate non-OpenClaw runtime companion if executable helpers are
  needed.
- Run `deep-research-workflow validate` after final report artifacts are ready.
