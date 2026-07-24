# Provider credit and quota exhaustion

This rule covers **agent-provider** credit, usage limits, and rate limits
(Claude, Codex, CodeWhale/DeepSeek, Kimi, Grok, Copilot, Antigravity, and
similar CLIs). It does **not** replace compute-lane budget gates (Modal USD,
Kaggle GPU-hours, Hetzner EUR) in `compute-offload-routing.md`.

It applies whenever a multi-agent or autonomous loop dispatches work to one or
more external agent providers: `autonomous-research-loop` / runtime `drive` and
`panel`, `agent-group-discuss`, parent-owned `delegate-agent` dispatch, and any
workflow that uses `cross-agent-delegation` packets toward a live recipient.

## Classification (host-verified)

Record the class from the provider's own stderr/stdout or exit text. Do not
infer credit exhaustion from a silent hang or empty reply alone.

| Class | Examples | Treat as |
|-------|----------|----------|
| **quota_or_credit** | usage limit, rate limit, 429, out of credits, billing, insufficient credit | Pause that **provider**, not the research goal |
| **transport** | DNS, network disconnect, ENOTIMP, connection refused | Retry or different-family fallback; not a credit stop |
| **empty_or_unusable** | exit 0 with preamble-only / no verdict | Mark unusable; do not count as credit |
| **binary_missing** | provider CLI not on PATH | `provider_unavailable`; fix install or exclude |

## Policy (strict)

1. **Provider credit outage is not a research stop.** Under
   `autonomous-loop-enforcement.md`, a user spend **cap** or exhausted loop
   budget fields may stop the loop. A single CLI's usage limit does **not**
   mean the open question is solved or abandoned.
2. **Exclude, do not thrash.** When a provider is credit-exhausted, remove it
   from the active roster **immediately** (panel list, AGD invite list,
   intended CAD recipient, drive primary) for the rest of the run or until the
   operator restores credits. Do not keep inviting it every cycle.
3. **Primary failover before infinite wait.** For ARL `drive`:
   - Prefer switching `--provider` to a still-funded family (or an operator
     ordered fallback list) over waiting forever on the same exhausted primary.
   - `quota_wait` / pause-and-retry is correct **only** when no alternate
     primary is configured or remaining, or the operator explicitly chose
     wait-only (`--max-quota-waits 0` with no fallback).
   - When waiting, re-check `STOP_REQUESTED` / `PAUSE` / `done` each cycle.
4. **Panel and multi-agent rosters.** Host panel (`panel.json` /
   `standing_orders.panel`) and AGD invite lists should set
   `exclude_until_credit` (or an equivalent exclude list) for exhausted
   providers. Remaining providers must still satisfy different-family rules
   when those rules are enabled; if they cannot, fail closed on the multi-agent
   gate and continue single-path host work only when standing orders allow.
5. **Cross-agent delegation packets.** Credit exhaustion is a **parent
   re-target** event, not a packet-schema failure and not permission for the
   child to invent authority. The parent records the outage, picks another
   recipient or defers, and does not put credentials or billing recovery into
   the packet.
6. **Never launder credit failure into evidence.** "Provider X had no credits"
   is an operational note, not a mathematical or manuscript result.
7. **Secrets.** Do not log API keys, account cookies, or full billing dumps.
   Status-only phrases from the provider error text are enough.

## Config surfaces (ARL / panel)

Preferred loop-local fields (any one is enough if documented for the run):

```json
{
  "providers": ["claude", "codewhale"],
  "exclude_until_credit": ["codex", "kimi"],
  "primary_provider": "claude",
  "primary_fallback": ["claude", "codewhale"]
}
```

- `exclude_until_credit`: providers skipped by panel dispatch and recommended
  for AGD/CAD recipient choice until the operator removes them.
- `primary_provider` / `primary_fallback`: operator guidance for `drive`
  restart; runtime may document but need not auto-restart mid-process.
- Env (panel providers only): `AAS_AUTOLOOP_PANEL_PROVIDERS=claude,codewhale`
  overrides the invite list for a session without editing files.

## Operator checklist when credits run out

1. Confirm class = `quota_or_credit` from a concrete log line (not guesswork).
2. Update `panel.json` / standing orders: drop or exclude the exhausted
   provider(s).
3. If the **drive primary** is exhausted: stop that drive process and restart
   with `--provider <funded>`; do not leave `max-quota-waits 0` spinning on a
   known dead primary when alternatives exist.
4. Notify (optional remote-bridge) with a short operational status; do not claim
   research progress.
5. When credits return: remove from `exclude_until_credit`, restore roster, and
   re-enable the preferred primary only if still desired.

## Related

- `autonomous-loop-enforcement.md` — loop stop priority (user caps vs defaults)
- `compute-offload-routing.md` — compute-lane budgets (not agent CLI credits)
- `cross-provider-delegation.md` — probes and multi-provider research policy
- `cross-agent-delegation` skill — inert packets; parent owns re-target
- `autonomous-research-loop-runtime` — `drive` `quota_wait`, panel dispatch
