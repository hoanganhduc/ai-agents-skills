# Canonical Skills

This directory contains sanitized reusable skill bodies. The manifests describe
which skills exist, which agents they support, and which dependencies they need;
the canonical skill folders hold the instructions and supporting references that
are linked, referenced, or copied into each agent target depending on install
mode.

Import rules:

- keep reusable instructions, references, templates, and safe helper scripts
- remove credentials, user paths, local cache paths, session logs, downloaded
  papers/books, and private library data
- normalize names to the canonical kebab-case names in `manifest/skills.yaml`
- put target-specific differences under `targets/<agent>/` instead of forking
  the canonical skill whenever possible

Common canonical skill subtrees:

- `references/`: supporting docs that the skill may open on demand.
- `scripts/`: safe helper scripts that runtime-backed skills can call.
- `assets/`: reusable templates or static files used by the skill.
- `agents/`: target-specific persona or adapter material when a skill needs it.

The installer treats non-`SKILL.md` text files under a canonical skill as
support files for symlink/copy installs. Reference installs write only an
adapter `SKILL.md` into the agent home and keep support material in this repo.
Non-text support files are not copied by the current importer path.

The installer adds a small managed marker when copying a canonical `SKILL.md`
into an agent. Canonical source files should not contain local ownership state.
