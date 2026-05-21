# External CLI Agents

This reference covers parent-owned external CLI participants in
`agent-group-discuss`. It is not a `cross-agent-delegation` packet contract, a
credential store, a provider router, a queue, or a runtime broker.

Use this file only when a logical AGD role is assigned to an `external_cli`
participant instead of a `codex_spawned` participant.

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
  "provider": "claude | deepseek | copilot | other",
  "cli_name": "string",
  "cli_version": "string or unknown",
  "profile_source": "probe artifact ref",
  "observed_at": "ISO 8601 timestamp",
  "expires_at": "ISO 8601 timestamp or null",
  "cwd_assumptions": "string",
  "auth_status": "available | missing | unknown | not_checked",
  "config_status": "available | missing | unknown | not_checked",
  "input_transports_tested": ["stdin", "prompt_file", "file_read", "inline_excerpt"],
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
- smoke prompt
- output contract probe: JSON if supported, otherwise a strict parseable
  envelope with a unique final marker
- input transport probe for the transport the role will use
- timeout/final-marker probe
- truncation detection

Required when the role expects local file inspection:

- file-read fidelity probe with a sentinel, line count, and selected-line check
- fallback inline-excerpt probe when file reads fail or are unsupported

For long prompts or long drafts, avoid shell argument transport. Use stdin,
prompt files, or bounded chunks with a manifest.

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
