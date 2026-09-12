# Runbooks

Operator procedures for running this repository against a real installation.

A runbook differs from the pages under `docs/`: `docs/` is generated from the manifests and
describes what the installer *is*, while a runbook is a hand-written sequence for getting a
particular job done safely, in order, with the checks that catch the ways it goes wrong.

Each one is written to be executed by an operator or a coding agent with no prior context, on
any host OS, against any chosen subset of install targets.

They carry no host data — no usernames, no absolute home paths, no counts or sizes measured on
someone's machine. Where a step needs a number it gives the command that produces it. This is
enforced: `make sanitize-check` runs `has_sensitive_material()` over everything the repository
ships, and home-shaped paths fail it. Keep run logs and measurements outside the repository.

| Runbook | Use when |
|---|---|
| [skills-sync-and-cleanup.md](skills-sync-and-cleanup.md) | An installation has fallen behind the remote, drifted from the catalog, or accumulated orphaned artifacts and journal files. Covers pull, re-baseline, install, cleanup and verification. |
