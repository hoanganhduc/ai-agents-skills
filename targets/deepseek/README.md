# DeepSeek Target

Generated DeepSeek skills are adapters. They use canonical names but must not
claim Codex or Claude-specific metadata is enforced by DeepSeek.

The npm-distributed TUI formerly named `deepseek-tui` is now `codewhale`.
Installer and delegation internals keep the logical `deepseek` target key for
compatibility, but CLI discovery should prefer `codewhale` / `codewhale-tui`
commands and treat older `deepseek` command names as fallback aliases.

In auto mode, DeepSeek skill files remain reference adapters until native
loader evidence proves another mode. DeepSeek personas and entrypoint aliases
are reference prompts/docs, not claims that DeepSeek enforces Codex or Claude
registration semantics.

Zotero and Calibre adapters should remain thin. They must not hardcode local
database or library paths, and they should route through the shared
profile-aware runtime commands used by the other agents.
