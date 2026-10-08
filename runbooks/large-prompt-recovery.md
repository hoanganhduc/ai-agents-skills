# Large prompt delivery recovery

The TSOuterplanar custom driver exposed a subprocess input defect on Linux with
Python 3.12. Its first `communicate(prompt, timeout=2)` could time out while
stdin was only partly written. Retrying with `communicate(None, ...)` did not
resume the remaining input or close stdin. A delayed reader reproduced the
failure with the actual driver; a short prompt completed successfully.

The historical Claude calls timed out without a stream trace. The reproduced
defect is a strong explanation, but their precise input timing is unrecorded.

## Scope and acceptance

The canonical runtime supplies `process_input.prepared_stdin` for custom
drivers that monitor processes with file-backed output. The helper prepares a
private temporary input stream before launch. The caller passes that stream
to `Popen` and polls `wait`, preserving its existing output-size limit, timeout,
process-group cleanup and provider policy. Prompts stay out of argv and env.
The input stream closes on success and failure. Empty input reaches EOF.

The progress view separately states when a drive invocation stopped and gives
its stop reason. A resumable loop may retain logical status `running`; this
does not establish process liveness.

The change does not alter model choice, scientific acceptance, research budgets,
retry accounting or Goal Focus enforcement. A provider probe
and an actual campaign continuation are required before claiming recovery.

The native check also exposed OAuth refresh loss between isolated role calls.
The native client refreshed a private credential copy, while a later call
copied an expired original and failed authentication. A request using the
refreshed copy passed. `claude_credentials.credential_session` retains refreshed
OAuth data in a campaign-private checkpoint under a host lock. The checkpoint
is initialized from an explicitly selected source, and subsequent role calls
use that checkpoint. Global native credentials are not overwritten. Other
native clients sharing the same refresh-token lineage can still invalidate it;
that external concurrency is outside the campaign lock.

Native calls use private streaming logs so initialization, tool activity and
provider events remain observable before the final result. The managed
`claude_output.result_envelope` parser requires exactly one terminal result
and rejects partial or malformed streams. The adapter retains its separate
schema and model checks. Raw events remain private.

## Tasks and verification

- [x] Add the managed helper and a delayed-reader regression larger than a pipe.
- [x] Check Unicode, EOF, temporary-file permissions and failure cleanup.
- [x] Test explicit stopped-driver presentation with a still-running loop.
- [x] Propagate only reviewed files through installer state; preserve other work.
- [x] Update the custom driver and test its output cap and timeout cleanup.
- [x] Verify private credential rotation, failure cleanup and serialization.
- [x] Propagate the credential helper and verify native authentication.
- [x] Verify a full-size Claude request and resume the existing pending iteration.
- [x] Propagate the streaming parser and retain strict result validation.

The Linux campaign verification passed 61 tests after credential and stream integration.
A native 136 KB input probe returned both endpoint markers, and the actual
Navigator subsequently returned a schema-validated research result. The
campaign moved to its next Claude control phase. These checks establish
transport recovery, not mathematical completion or cross-platform execution.

The source change is developed in an isolated worktree because the main
checkout contains unrelated changes. Install plans and changed-file hashes
must be checked before applying to existing runtime targets. Linux execution
is checked locally; other OS execution remains unverified. Native Windows
installer restrictions remain in effect.
