# ARL Notify v2.1 — operator_full body (pack-owned)

**Schema:** `aas.autoloop.notify.v2` / `2.1`  
**Module:** `canonical/runtime/skills/autonomous-research-loop-runtime/notify_v2.py`  
**Profiles:** `operator_full` (default) · `legacy` (opt-out)

Loop identity only (`research_title`, `job_slug`) lives in `failover.json` / `notify.json`.  
The body layout is **not** a per-loop template file.

## Section order (`operator_full`)

1. Title  
2. Status (iteration · loop · review)  
3. Goal  
4. Completed  
5. Results  
6. Current  
7. Decision  
8. Decision reason  
9. Plan  
10. Progress  
11. Started  
12. Finished  
13. Executor  
14. Driver agent  
15. Panel agents  
16. Other agents  
17. Compute  
18. Runtime errors *(omitted when none and host-reported)*  
19. Review failures *(omitted when none and host-reported)*  

## Honesty rules

| Situation | Rendered text |
|-----------|----------------|
| No claims this event | `No claims banked.` / `No claims banked by this event.` |
| Issues not filled (legacy) | `Not recorded (legacy/unreported)` |
| Host asserts zero issues | section **omitted** (not “None” spam) |
| Decision mid-attempt | `Pending (not finalized)` |
| Started unknown | `Not recorded` (never invent from finish time) |
| Sensitive output blocked | code `sensitive_output` only — no raw dump |

## Profile selection

1. `notify.json` → `body_profile`  
2. `loop_state.standing_orders.notify.body_profile`  
3. `AAS_AUTOLOOP_NOTIFY_BODY_PROFILE`  
4. default `operator_full`  

Profile is stored on the envelope as `presentation.body_profile` so remote-bridge re-renders consistently.

## Install

Update **both** installed copies (must hash equal):

- `…/autonomous-research-loop-runtime/notify_v2.py`  
- `…/remote-bridge/notify_v2.py`  

Adoption is process reload, not directory rewrite. Silent loops stay silent.

## Example (markdown sketch)

```text
✅ **Clawfree reconfiguration — Iteration 376 succeeded**

**Status**: Iteration SUCCESS · Loop RUNNING · Review PASSED

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
**Other agents**: None
**Compute**: None
```
