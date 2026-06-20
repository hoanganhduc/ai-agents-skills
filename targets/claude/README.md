# Claude Target

Generated Claude artifacts use canonical skill names. Legacy slash commands can
remain as aliases, but the installed skill folder and `SKILL.md` frontmatter
should use the canonical name.

In auto mode, Claude skill files are symlinked to canonical repo skill files
when the filesystem supports it. Claude personas are Markdown subagent files,
and entrypoint aliases are installed as command files.

Zotero and Calibre integrations must not maintain separate path assumptions in
Claude commands or skills. They should call the shared profile-aware wrappers
and defer library authority decisions to the generated local-library profile.

## Autonomous loop enforcement

Claude is the one target with built-in interactive enforcement. When the
autonomous-research-loop runtime is installed, the installer merges one managed
`hooks.Stop` entry into `~/.claude/settings.json` (the `settings-hook-merge`
surface). The entry is tagged `_managedBy`/`_id`, is idempotent, and is removed
on uninstall; user-authored hooks are preserved. The hook is fail-open: set
`AUTOLOOP_DISABLE=1`, remove the loop's registry entry, or create a
`STOP_REQUESTED` sentinel in the loop directory to release a session. Headless
batch runs use `autoloop_driver.sh`, which sets `AUTOLOOP_DRIVER=1` so the
interactive hook stands down. See [Architecture](../../docs/architecture.md) for
the full stop policy and the honest per-target enforcement matrix.
