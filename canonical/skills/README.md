# Canonical Skills

This directory contains sanitized reusable skill bodies. The manifests describe
which skills exist, which agents they support, and which dependencies they need;
the canonical skill folders hold the instructions and supporting references that
are copied into each agent target.

Import rules:

- keep reusable instructions, references, templates, and safe helper scripts
- remove credentials, user paths, local cache paths, session logs, downloaded
  papers/books, and private library data
- normalize names to the canonical kebab-case names in `manifest/skills.yaml`
- put target-specific differences under `targets/<agent>/` instead of forking
  the canonical skill whenever possible

The installer adds a small managed marker when copying a canonical `SKILL.md`
into an agent. Canonical source files should not contain local ownership state.
