# Architecture

The manifests are the source of truth. The installer resolves canonical skills
to per-agent target artifacts and records ownership in a journal. Existing
unmanaged files are skipped by default. Agent-specific legacy locations, such as
Codex's historical `~/.codex/skills`, are detected during planning so existing
skills are not duplicated unless the user explicitly chooses `--migrate`.
