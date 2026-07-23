# External CLI Agents

This reference covers parent-owned external CLI participants in
`agent-group-discuss`. It is not a `cross-agent-delegation` packet contract, a
credential store, a provider router, a queue, or a runtime broker.

Use this file only when a logical AGD role is assigned to an `external_cli`
participant instead of a `codex_spawned` participant.

The managed CLI entrypoint is:

```bash
./installer/bootstrap.sh delegate-agent --provider auto --task-file <task.md> --dry-run
```

Actual process launch requires `--allow-external-cli`. Research launch also
requires a resolved latest model, highest thinking/reasoning value, and an
explicit provider dispatch command such as `AAS_CLAUDE_DISPATCH_COMMAND`.

## Scope

An external CLI participant is an executable endpoint that may contribute to a
discussion, review, research, validation, judge, or synthesis role. Its output
is untrusted until the parent orchestrator validates the output contract,
evidence policy, limitations, and artifacts.

Do not store raw CLI commands, service identifiers, absolute paths, stdout,
stderr, timeout traces, session IDs, provider configs, credentials, or
environment snapshots in `cross-agent-delegation` packets. Store those details
only in the parent run directory and refer to them through inert artifact refs
when needed.

## Capability Profile

Before an external CLI participant is used for a role, create a capability
profile in the AGD run directory. Profiles are observations with timestamps,
not permanent provider facts.

Required profile fields:

```json
{
  "profile_id": "provider-or-cli-profile-slug",
  "provider": "claude | deepseek | copilot | antigravity | grok | kimi | other",
  "cli_name": "string",
  "cli_version": "string or unknown",
  "profile_source": "probe artifact ref",
  "observed_at": "ISO 8601 timestamp",
  "expires_at": "ISO 8601 timestamp or null",
  "cwd_assumptions": "string",
  "auth_status": "available | missing | unknown | not_checked",
  "config_status": "available | missing | unknown | not_checked",
  "input_transports_tested": ["stdin", "prompt_file", "runtime_argv_prompt", "file_read", "inline_excerpt"],
  "output_modes_tested": ["json", "text", "parseable_envelope"],
  "file_read_fidelity": "passed | failed | not_needed | not_checked",
  "timeout_behavior": "completed | timed_out | not_checked",
  "truncation_status": "not_observed | observed | not_checked",
  "validated_capabilities": ["string"],
  "blocked_capabilities": ["string"],
  "limitations": ["string"]
}
```

Reject stale profiles when the CLI version, auth state, working directory, input
transport, or output mode differs from the observed profile.

## Mandatory Probes

Run only the probes needed for the role, but fail closed for any capability the
role depends on.

Required for every external CLI participant:

- version or help probe
- auth/config availability probe when the CLI needs credentials
- latest model and highest thinking/reasoning selection probe for research
  tasks
- smoke prompt
- output contract probe: JSON if supported, otherwise a strict parseable
  envelope with a unique final marker
- input transport probe for the transport the role will use
- timeout/final-marker probe
- truncation detection

Required when the role expects local file inspection:

- file-read fidelity probe with a sentinel, line count, and selected-line check
- fallback inline-excerpt probe when file reads fail or are unsupported

Required when a manager role may launch child workers:

- same-provider same-model child dispatch probe
- child output contract probe
- evidence that child workers can be kept one level deep

## Managed Dispatcher

`delegate-agent` is the parent-owned subprocess adapter for external CLI
participants. It:

- selects providers from `manifest/delegation.yaml` when `--provider auto` is
  used
- blocks live external execution unless `--allow-external-cli` is supplied
- blocks research execution unless latest-model and highest-thinking settings
  are resolved for the provider
- sends bounded prompts over stdin
- requires a JSON envelope plus final marker
- writes run artifacts under `.ai-agents-skills/delegation-runs/<run-id>/`
- returns parsed results and validation status, not raw stdout/stderr

Provider dispatch commands are intentionally configured outside the repo with
environment variables, for example:

```bash
export AAS_CLAUDE_DISPATCH_COMMAND='claude --print --model {model}'
export AAS_CLAUDE_LATEST_MODEL='<current-latest-model>'
export AAS_CLAUDE_HIGHEST_THINKING='xhigh'
```

For Antigravity, the managed fallback dispatch shape is `agy --print`.
Research runs should still configure the command explicitly, for example:

```bash
export AAS_ANTIGRAVITY_DISPATCH_COMMAND='agy --print --model {model}'
export AAS_ANTIGRAVITY_LATEST_MODEL='<current-latest-model>'
export AAS_ANTIGRAVITY_HIGHEST_THINKING='high'
```

The dispatcher does not use `ANTIGRAVITY_LS_ADDRESS`; that variable belongs to
language-server integrations outside this CLI subprocess adapter.

For Grok, the managed dispatch shape is `grok --prompt-file /dev/stdin`.
The dispatcher delivers the prompt on stdin, and grok's `-p`/`--single` requires
the prompt as an argv value (it does not read stdin), so the prompt is passed as a
file read from fd 0; a bare `grok --single` here exits 2 with the prompt never
sent.

Automatic Grok selection is bare-first and model-gated:

1. When a concrete latest model is resolved, run bare `grok models` and parse
   only exact anchored available-model rows of the form `* <model>` or
   `* <model> (default)`. Prose, the `Default model:` header, substrings,
   non-zero exits, timeouts, and unrecognized output do not confirm membership.
   On POSIX, invoke model probes, remote readiness checks, and actual Grok
   children with umask `0077` so Grok-created cache files are private even when
   the caller's ambient umask is permissive.
2. If the exact resolved model is listed, dispatch through bare Grok and add
   `--model <resolved-model>`.
3. Only when bare Grok is missing or that exact membership probe is not
   confirmed may automatic selection consider `grok-remote`. The proxy must
   then pass its managed-profile readiness probe and report the same model.
4. Without a resolved model, automatic selection uses bare Grok only, records
   the model probe as not performed, and does not authorize proxy fallback.

Generic provider prechecks are bare-only. They do not discover, version-probe,
or execute `grok-remote`; proxy discovery exists only inside step 3 after a
valid exact model has already authorized fallback.

The dispatcher invokes an automatically selected `grok-remote` route-neutrally:
it neither sets `GROK_MULTI_SESSION` nor adds `--vpn`, `--host`, `--iphone`, or
`--ios`. A bare proxy command delegates route selection to its active managed
profile. Concurrent Grok participants must resolve the same ready profile and
concrete model. Unlike other research providers, Grok does not require an
explicit dispatch-command variable because its automatic route is resolved and
model-pinned by these probes; latest-model and highest-thinking values remain
mandatory.

Operator overrides remain authoritative. `AAS_GROK_DISPATCH_COMMAND` is
preserved verbatim, and `AAS_GROK` selects its exact binary. An explicit bare
command with a resolved model must pass the same membership probe but is never
silently replaced with the proxy. An explicit proxy command still requires its
managed-profile readiness/model check. For example, this deliberately forces
the proxy and disables automatic bare-first selection:

```bash
export AAS_GROK_DISPATCH_COMMAND='grok-remote --prompt-file /dev/stdin --model {model}'
export AAS_GROK_LATEST_MODEL='<current-latest-model>'
export AAS_GROK_HIGHEST_THINKING='high'
```

For a direct manual invocation outside the dispatcher, use the equivalent
shape:

```bash
grok-remote -m <concrete-model> --prompt-file <path>
```

For proxy fallback or an explicit proxy command, the parent runs
`grok-remote doctor --json`, requires the
`grok-remote.profile-status.v1` contract to report `ready` or `degraded`, and
checks its `model_id` against the resolved model. `blocked`, `unconfigured`,
invalid, inconsistent, or timed-out results fail closed. Only the contract's
sanitized profile, release, model, rung, and reason fields enter parent-owned
capability metadata; endpoints, ports, and node identities do not. The
dispatcher first requires `--help` to advertise that exact command, so an older
proxy fails closed without receiving `doctor` as ordinary Grok input.

Grok authenticates through an interactive OIDC session rather than an API-key
environment variable, so the dispatcher does not read a Grok token from the
environment. Local read-only diagnostics (`grok inspect`) resolve a bare `grok`
so they never bring up the `grok-remote` tunnel.

For Kimi Code CLI, the managed dispatch shape is **runtime argv prompt**:
`kimi -p <prompt>` is appended by the dispatcher after the prompt is known
(Kimi has no `--prompt-file`). Capability profiles must record
`runtime_argv_prompt`, not `stdin`. Research runs require
`AAS_KIMI_DISPATCH_COMMAND` plus resolved model metadata
(`AAS_KIMI_LATEST_MODEL`). Auth is config/credentials under `~/.kimi-code` and
must never enter packets. Long prompts must stay within the dispatcher argv
budget (`KIMI_MAX_PROMPT_CHARS`, currently 24_000); do not invent temp prompt
files without a verified Kimi file-based flag.

```bash
export AAS_KIMI_DISPATCH_COMMAND='kimi'
export AAS_KIMI_LATEST_MODEL='<model-alias>'
export AAS_KIMI_HIGHEST_THINKING='high'
```

#### Kimi one-shot / AGD panel rules (host-observed)

These rules come from a live AGD strategy-panel failure (Kimi Code CLI ≥0.29).
They apply to parent-owned launches, not only `delegate-agent`.

1. **Do not combine `-p` / `--prompt` with agent-mode flags.**  
   Observed hard error: `Cannot combine --prompt with --yolo.`  
   Forbidden alongside one-shot `-p` for panel/discussion roles:
   `-y`, `--yolo`, `--auto` (and any flag that enables multi-turn tool autonomy
   for the same invocation). Use pure one-shot:
   `kimi -p "<prompt>" --output-format text` (optional `-m <model>`).

2. **Keep one-shot prompts compact.**  
   Full evidence dumps (25k–100k characters) are a poor fit for argv transport
   and encourage long tool-using digressions. For AGD Round-1 style roles:
   - put a **compressed brief** (≤ ~8–12k chars) in the prompt body, or
   - pass **inert path refs** and a short task contract, and accept that
     file-read fidelity must be freshly probed if the role depends on tools.

3. **Prefer text one-shot for discussion/falsifier roles.**  
   When the contract is “Markdown final answer only,” do not start Kimi in
   interactive or auto tool mode. Long stderr monologues without a final
   stdout body are a **participant failure** (`output_parse_failed` /
   `timeout_no_final` / empty stdout), not usable panel evidence.

4. **Stdout is the contract surface.**  
   Capture `stdout` as the reply; treat `stderr` as diagnostics only. If
   stdout is empty/short and stderr is long, mark the participant
   `invalid` or `partial` and continue the panel with other agents—do not
   silently promote stderr thinking as the result packet body.

5. **Dispatcher / template hygiene.**  
   `AAS_KIMI_DISPATCH_COMMAND` must not itself include `-p`, `--prompt`,
   `-y`, `--yolo`, or `--auto`. The managed path appends `-p <prompt>` (and
   optional `-m`) at execution time. Pre-baking those flags causes flag
   conflicts or double-prompt errors.

Diagnostic codes (in addition to the shared taxonomy): `kimi_flag_conflict`
when the CLI rejects prompt+agent-mode combinations;
`shell_argument_limit` when the prompt exceeds the argv budget.

Do not hardcode provider model names into shared templates unless a specific
target system has just probed and recorded that model as current.

For long prompts or long drafts on providers that support stdin or prompt
files, prefer those transports. **Kimi is the exception:** it remains argv
`-p` only—compact or chunk with a manifest; do not fall back to stdin as if
it were Claude/Grok.

## Artifact Layout

Store external CLI artifacts under the AGD run directory:

```text
profiles/<participant_id>.json
probes/<participant_id>/...
raw/<participant_id>/stdout.txt
raw/<participant_id>/stderr.txt
raw/<participant_id>/command-shape.txt
parsed/<participant_id>.json
validation/<participant_id>.json
transport_manifest.json
timeout_events.jsonl
truncation_events.jsonl
```

`command-shape.txt` may describe the invocation class and flags, but must not
include credentials, secrets, raw private paths, or service identifiers that
would be unsafe to forward.

Maintain a parent-owned `evidence-map.jsonl` for evidence mapping when external
CLI findings are used by a research workflow. Each row should bind participant
id, role, parsed finding id, validation artifact, source artifact refs,
redaction status, parent disposition, and the target research `evidence.jsonl`
id if accepted. Treat a stale capability profile, missing validation artifact,
missing redaction record, or unmapped finding as a recovery item, not as usable
evidence.

## Failure Taxonomy

Use stable diagnostic codes in participant state and validation artifacts:

- `smoke_failed`
- `auth_missing`
- `config_missing`
- `input_transport_failed`
- `shell_argument_limit`
- `unsupported_attachment`
- `file_read_fidelity_failed`
- `output_parse_failed`
- `text_renderer_malformed`
- `timeout_no_final`
- `truncated_output`
- `nonzero_exit`
- `missing_artifact`
- `evidence_contract_failed`
- `stale_capability_profile`
- `kimi_flag_conflict`

## Role-Aware Evidence Policy

Discussion roles may provide arguments or preferences, but factual, source,
code, artifact, and mathematical claims still need evidence refs or explicit
limitations.

Review and research roles must cite the supplied chunk, source, file, or
artifact refs for each substantive finding.

Judge and synthesis roles are advisory unless the parent validates the cited
evidence and conflict ledger. An external CLI judge must not promote a claim to
accepted final status by self-report.

## Provider-Specific Notes

Provider notes must be recorded as observed capability profiles with source and
timestamp. Do not phrase them as permanent facts.

Initial observations from prior diagnostics:

- Claude: smoke and scoped file-read probes can work; broad whole-draft
  max-effort runs need chunking, final markers, and timeout handling.
- DeepSeek: model smoke can work; path-based local file reads require a fresh
  fidelity probe and should fall back to inline excerpts when fidelity fails.
- Copilot: evidence-bearing runs should prefer parseable JSON when available;
  text rendering and attachment/file-read behavior require fresh probes.
