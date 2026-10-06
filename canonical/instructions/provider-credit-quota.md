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

Record the class only from host-captured provider diagnostics: a structured error
envelope or an anchored CLI error on the separate stderr transport after a
nonzero exit. Model narration, stdout, silence, and empty replies cannot
establish credit exhaustion. Unsupported diagnostic formats remain ordinary
failures until an adapter is verified.

| Class | Examples | Treat as |
|-------|----------|----------|
| **hard_quota** | HTTP 402, insufficient_quota, exhausted credits, weekly/monthly usage cap | Exclude immediately and persist until an explicit operator restoration. Drive exits **18**, without a retry or probe. |
| **quota** | HTTP 429, rate_limit_error, too many requests | Bounded transient throttling. Drive exits **5** when the configured wait or persistent attempt cap is reached; the supervisor may temporarily exclude this provider. |
| **auth_or_session** | 401 Unauthorized, token_invalidated, refresh_token revoked, sign in again | Re-auth offline or rotate primary; **not** a credit pause (drive exit **7**) |
| **transport** | DNS, network disconnect, ENOTIMP, connection refused | Retry same primary / different-family fallback; not a credit stop |
| **empty_or_unusable** | exit 0 with preamble-only / no verdict | Mark unusable; do not count as credit |
| **binary_missing** | provider CLI not on PATH | `provider_unavailable` (exit **6**); fix install or exclude |

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
   - **Preferred unattended mechanism:** outer supervisor pack
     (`arl_drive_supervisor.sh` + `{loop}/failover.json` `primary_order` /
     alias `primary_fallback`). The supervisor is the sole consumer of that
     order; stock `drive` stays single-provider. On exit 5/6/7 it
     **temporarily excludes** the unavailable primary. Exit 18 separately
     persists the hard exclusion and may sync it to the panel.
   - Ordinary execution attempts are capped at three per pending iteration and
     phase, persisted through restarts and provider rotation. A lower explicit
     cap is honored. `--max-quota-waits 0` does not remove that execution cap.
   - Hard quota exits 18 immediately; it never enters a wait/retry path. A hard
     exclusion has no automatic TTL. Soft throttling, missing binaries, and
     authentication failures do not create permanent credit exclusions.
   - Waiting checks `STOP_REQUESTED` / `PAUSE`; new workers receive at most the
     remaining wall budget after reserving cleanup time. This reservation is
     not a claim of an independently enforced absolute cleanup deadline.
4. **Panel and multi-agent rosters.** Host panel (`panel.json` /
   `standing_orders.panel`) and AGD invite lists should set
   `exclude_until_credit` (or an equivalent exclude list) for exhausted
   providers. An empty filtered roster remains empty, including smoke probes.
   Explicit available providers remain selectable, but relisting an excluded
   provider does not restore it. Remaining providers must still satisfy different-family rules
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
  "providers": ["codex", "claude", "grok", "opencode", "antigravity", "copilot", "kimi", "deepseek"],
  "exclude_until_credit": [],
  "primary_provider": "claude",
  "primary_order": [
    "claude",
    "codex",
    "grok",
    "opencode",
    "antigravity",
    "copilot",
    "kimi",
    "deepseek"
  ]
}
```

- `providers`: host panel invite list (default:
  `codex, claude, grok, opencode, antigravity, copilot, kimi, deepseek`).
- `exclude_until_credit`: providers skipped by panel dispatch and recommended
  for AGD/CAD recipient choice until the operator removes them.
- `primary_provider` / `primary_fallback` / `primary_order`: preferred list for
  the **outer supervisor** (`failover.json`). Default example order:
  `claude, codex, grok, opencode, antigravity, copilot, kimi, deepseek`.
  Stock `drive` ignores these fields until an optional future in-process
  `--provider-order` lands. (`primary_fallback` is an alias of `primary_order`.)
- `research_title` / `job_slug` (same file or `notify.json`): research-topic
  notify identity for Zulip/Telegram (not generic “loop”).
- Env (panel providers only): `AAS_AUTOLOOP_PANEL_PROVIDERS=codex,claude,grok`
  overrides the invite list for a session without editing files.

## Operator checklist when credits run out

1. Confirm `hard_quota` from an admitted host diagnostic; distinguish soft throttling.
2. Update `panel.json` / standing orders: drop or exclude the exhausted
   provider(s).
3. If the **drive primary** is exhausted: stop that drive process and restart
   with `--provider <funded>`; do not leave `max-quota-waits 0` spinning on a
   known dead primary when alternatives exist. Clear any permanent supervisor
   exclusion only after the operator has restored the provider.
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
