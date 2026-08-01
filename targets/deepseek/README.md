# DeepSeek Target

Generated DeepSeek skills are adapters. They use canonical names but must not
claim Codex or Claude-specific metadata is enforced by DeepSeek.

The npm-distributed TUI formerly named `deepseek-tui` is now `codewhale`.
Installer and delegation internals keep the logical `deepseek` target key for
compatibility, but CLI discovery should prefer `codewhale` / `codewhale-tui`
commands and treat older `deepseek` command names as fallback aliases.

CodeWhale reads its model endpoint from the `DEEPSEEK_BASE_URL` environment
variable in its non-interactive `exec` path; the config-file `base_url` is
honored only by the interactive TUI. So a valid API key is not sufficient on its
own: without `DEEPSEEK_BASE_URL` the headless call has no endpoint and prints
`DEBUG DEEPSEEK_BASE_URL not set` with no output. The delegation dispatcher
defaults `DEEPSEEK_BASE_URL` to `https://api.deepseek.com` when it is unset, and
the external-agent precheck reports it under `endpoint` so `doctor` flags a
missing endpoint instead of reporting the provider ready on the API key alone.
Set `DEEPSEEK_BASE_URL` explicitly to route through a different (e.g. proxy)
endpoint.

## Headless delegation transport (host-probed 2026-07-30)

`codewhale exec` takes the prompt as a positional argv value; it does not read
the user prompt from stdin (a piped prompt exits with a usage error). The
managed dispatcher therefore delivers deepseek prompts runtime-argv style, as
the trailing positional argument, and inserts `--model` before the `exec`
subcommand when the template does not pin one (`--reasoning-effort` stays an
exec-subcommand flag in the operator template). codewhale 0.9.1 exits 0 with
empty stdout/stderr when the prompt exceeds a few thousand characters (probed:
2400 completed, ~3500 failed silently), so the dispatcher fails closed above
2,000 characters with `shell_argument_limit` instead of recording a silent
empty participant; keep briefs compact until a fixed CLI version is verified.
A zero-byte successful result validates as `empty_stdout`.

In auto mode, DeepSeek skill files remain reference adapters until native
loader evidence proves another mode. DeepSeek personas and entrypoint aliases
are reference prompts/docs, not claims that DeepSeek enforces Codex or Claude
registration semantics.

Zotero and Calibre adapters should remain thin. They must not hardcode local
database or library paths, and they should route through the shared
profile-aware runtime commands used by the other agents.

## Autonomous loop enforcement

DeepSeek has no built-in autonomous-loop enforcement: skills are adapters with
no managed settings/hook surface or headless runner here. Loop stop-conditions
remain policy-based via the installed `autonomous-loop-enforcement` rule. See the
repo [Architecture](../../docs/architecture.md) for the full per-target matrix.
