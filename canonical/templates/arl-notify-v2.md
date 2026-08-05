# ARL Notify v2.1 — operator body (pack-owned)

**Schema:** `aas.autoloop.notify.v2` / `2.1`  
**Module:** `canonical/runtime/skills/autonomous-research-loop-runtime/notify_v2.py`  
**Profiles:** `operator_full` (default) · `operator_compact` · `legacy` (opt-out)

Loop identity only (`research_title`, `job_slug`) lives in `failover.json` / `notify.json`.  
The body layout is **not** a per-loop template file.

## Section order (`operator_full`)

1. Title (status icon + research title)  
2. Status (iteration · loop · review)  
3. **Event time** (`occurred_at`, always when known)  
4. Goal *(omitted if empty / non-informative sentinel)*  
5. Completed  
6. Results  
7. Current  
8. Decision  
9. Decision reason  
10. Plan  
11. Progress  
12. Started *(only when `iteration.started_at` set)*  
13. Finished *(only when terminal with a real timestamp)*  
14. Executor  
15. Driver agent  
16. Panel agents  
17. Other agents  
18. Compute  
19. Runtime errors *(omitted when none or unreported)*  
20. Review failures *(omitted when none or unreported)*  

## `operator_compact`

Title, Status, Event time, Completed, Current, Plan, Progress, plus errors/failures when present.  
No Goal/Results/Decision trailer, no agent/compute noise.

## Honesty and omit-empty rules

| Situation | Behavior |
|-----------|----------|
| Empty / “Not recorded” / “Not recorded (legacy/unreported)” | **Omit the field line** (do not print noise) |
| In-flight finish | **Omit** Finished (do not print “Not finished”) |
| No claims this event | **Omit** Results when it would only say “No claims banked…” |
| Decision mid-wait | **Omit** Pending decision lines (Status already says WAITING) |
| Started unknown | **Omit** Started (never invent from finish time) |
| Event wall-clock | Always print **Event time** from `occurred_at` |
| Host asserts zero issues | section **omitted** |
| Sensitive output blocked | code `sensitive_output` only — no raw dump |
| Zulip freeform markup | Do **not** CommonMark-backslash-escape (`\_`, `\``, `\*` show literally on Zulip). Flatten newlines; neutralize `@` / `` ` `` / `**` / links with lookalikes |

## Remote vs local progress

`progress.jsonl` / `LIVE_STATUS.md` still record wait ticks.  
**Remote** (Zulip/Telegram) does **not** send `strategy_review_wait`, `goal_focus_wait`, or `result_review_wait` (replan loops re-emit them every ≥30s). Prefer outcomes: `iteration_ok` / rejected / failed, replan commits, quota/auth, terminal.

Supervisor no longer remote-notifies “driving with primary=…” immediately before drive; **`drive_start`** is the single start-class remote event.

## Channel policy (pointer)

This template owns the **body**; channel selection is remote-bridge policy.
Strict single-channel campaigns set `AAS_REMOTE_STRICT_NOTIFY_CHANNEL` in the
loop env (see remote-bridge `SKILL.md` and the force-loop `OPERATOR_RUNBOOK.md`).
The Zulip target stream is `zulip.control_stream` in the remote-bridge secrets
and must be an **existing** channel — the bridge never creates streams. A
launch preflight with a read-only live-auth check is the only step that
catches credentials that resolve structurally but fail authentication
(`OPERATOR_RUNBOOK.md`, "Notify channel and launch preflight").

## Profile selection

1. `notify.json` → `body_profile`  
2. `loop_state.standing_orders.notify.body_profile`  
3. `AAS_AUTOLOOP_NOTIFY_BODY_PROFILE`  
4. default `operator_full`  

Profile is stored on the envelope as `presentation.body_profile` so remote-bridge re-renders consistently.

## Install

Update **both** installed copies (must hash equal) via scoped skill install only:

- `…/autonomous-research-loop-runtime/notify_v2.py`  
- `…/remote-bridge/notify_v2.py`  

```bash
make install ARGS="--skills autonomous-research-loop-runtime,remote-bridge --apply --real-system"
```

Adoption is process reload, not directory rewrite. Silent loops stay silent.

## Example (markdown sketch)

```text
✅ **Clawfree reconfiguration — Iteration 376 succeeded**
**Status**: Iteration SUCCESS · Loop RUNNING · Review PASSED
**Event time**: 2026-08-01T16:25:14Z

**Goal**
Determine KR complexity on claw-free graphs for fixed k≥3.

**Completed**
Banked AND/OR differentiation with primary/independent agreement.

**Results**
Banked claims: iter370-and-or-primary, iter370-and-or-independent

**Current**
Campaign A3 premise pack under plan r18.

**Decision**
revise

**Decision reason**
Different-family review still open on the manuscript-native pack.

**Plan**
Route T3 enumeration to kaggle+modal.
**Progress**: 376/1000 iteration budget used (624 remaining); goal progress: …
**Started**: 2026-08-01T15:56:45Z
**Finished**: 2026-08-01T16:25:14Z · Duration: 28m 29s
**Executor**: codex
**Driver agent**: codex (gpt-5.6-sol)
**Panel agents**: codewhale
**Compute**: kaggle
```
