# Codex Target

Generated Codex artifacts use canonical skill names and the Codex-compatible
`SKILL.md` directory layout. User-level skills target `~/.codex/skills` in this
setup. `.agents/skills` is treated as an optional workspace or compatibility
location when it is explicitly detected or requested.

Zotero and Calibre integrations must call the shared profile-aware runtime
commands. Codex target files should not hardcode Zotero or Calibre library
paths; they should rely on the selected local-library profile manifest and the
shared safety gates.
