# Autonomous Loop Enforcement

This rule governs when an autonomous loop may end. It exists because an agent
running a loop must not stop for its own convenience. It applies whenever an
autonomous loop is active, including every loop started through the
`autonomous-research-loop` skill and its runtime helper, in any domain
(research, formalization, migration, audit, refactor, and similar).

## Stop policy (strict priority order)

**Priority 0 — user requirements override everything.** Whatever the user
stated when starting the loop (a loop count, a budget, a goal, a deadline, or an
explicit "stop when X") is captured at init into `loop_state.stop_conditions`
and is the only authority that can shorten or lengthen the defaults below. If the
user gave a requirement, follow it exactly.

**Defaults — used only when the user gave no overriding requirement.** Stop the
loop only when one of these is true:

1. the required number of loops is reached (if no count was set, this never
   fires);
2. credit or quota runs out, or a user-set spend cap is hit;
3. the loop's stated goal is fully resolved, confirmed by a machine-checkable
   success check, not by the agent's opinion;
4. the user sends a message asking to stop, pause, or switch tasks.

**Hard rule: nothing else is a valid stop.** No checkpoints, no "this is hard,"
no "I think we should pause here," no asking permission to continue, no stopping
to deliver a recommendation. The plateau, evidence-gap, and repeated-blocker
heuristics from general loop guidance are subordinate to this policy: under the
defaults they do not end the loop; they downgrade the iteration decision to
`revise` or `delegate` and the loop continues. A genuine blocker is recorded and
the loop continues unless it also satisfies one of conditions 1 through 4.

## What "fully resolved" means (condition 3)

Goal resolution is observable, not asserted. The loop's success check must exit
zero, or the iteration must cite a local machine-checkable proof or success
artifact. "I believe the goal is met" is not resolution. This covers the goal of
the work and the goal of running the loop, in whatever domain the loop serves.

## Honest condition 4 in headless runs

In interactive sessions a user stop message is honored immediately. In headless
batch runs there is no chat channel, so condition 4 is reachable only through the
`STOP_REQUESTED` sentinel or an explicit kill switch. Do not claim a headless
loop is listening for chat; it is not.

## Escape hatches (always available)

A loop must never become impossible to stop. These free a session even when the
runtime or hook is broken, and each is checked before any block decision:

- `AUTOLOOP_DISABLE=1` in the environment allows turn-end immediately.
- Removing the loop's registry entry returns the session to dormant.
- A `STOP_REQUESTED` sentinel is treated as condition 4 and allows turn-end.
- A `PAUSE` sentinel suspends enforcement without ending the loop; removing it
  resumes.

## Enforcement is fail-open

Enforcement is shared by one arbiter: the runtime `done` check, which the Stop
hook and the headless driver both call. The Stop hook blocks turn-end only when
the arbiter reports an active, not-done, not-paused loop for this session's
project root. On any error, timeout, missing pointer, missing runtime,
unparseable state, or re-entrant invocation, enforcement allows turn-end. A
broken enforcer must never trap a session; it must release it.
