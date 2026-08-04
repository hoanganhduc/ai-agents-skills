# Restore target contract

`manifest/target-state.yaml` is the target-neutral contract between this skill
installer and a host restoration system.  Version 3 declares, for every
installer target, the agent home, native CLI candidates, logical runtime
requirements, credential authority locations, and readiness evidence expected
after restoration.

One installer target may expose multiple independently verified native CLI
surfaces. In particular, the Antigravity adapter declares separate `agy` and
Gemini CLI probes and separate credential authorities; success of one must not
hide a missing or expired session for the other.

The manifest contains paths and policy only.  Credential values are forbidden.
The `ai-agents-skills` installer continues to own skill rendering and loader
verification; the host restoration system owns operating-system packages,
agent CLI installation, credential recovery, and service activation.

A restored target is not ready merely because its skill files exist.  The host
must also prove the locked CLI version, the target's managed skill visibility,
the declared runtime smoke checks, and the applicable structural/native
authentication status.  Provider credit exhaustion is recorded separately and
does not turn a structurally correct target into a technical failure.
