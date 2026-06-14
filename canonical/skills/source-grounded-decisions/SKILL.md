---
name: source-grounded-decisions
description: Use before a version- or spec-sensitive decision — framework or library code, a CLI flag, a config schema field, or a named standard/edition — when correctness depends on the exact version. Detect the version, ground the decision in the authoritative source, and cite it; flag the assumption when no source is available.
metadata:
  short-description: Ground version- and spec-sensitive decisions in cited sources
---

# Source-Grounded Decisions

Do not implement version- or spec-sensitive details from memory — training data goes
stale and APIs, flags, and schemas change. Ground the decision in an authoritative
source the user can check, and cite it. Composes with `research-verification-gate`
and `claim-preserving-writing`.

## When to use

- writing framework or library code where the version determines the correct pattern
- a CLI flag, environment variable, or config field whose name or behavior is
  version-specific
- a named standard, schema, or data edition (an API field, a format, a regulation)
- a research step whose correctness depends on the exact tool or data version

## When not to use

- version-agnostic logic (loops, data structures, renames, moving files)
- the user explicitly wants speed over verification

## Method

1. **Detect the version.** Read the authority pointer for the task: the dependency
   file (`package.json`, `pyproject.toml`, `go.mod`, `Cargo.toml`, `Gemfile`) for
   code; `--version` or the man page for a CLI; the named edition for a standard.
   State it explicitly, e.g. `DETECTED: React 19.1, Vite 6.2`. If it is missing or
   ambiguous, ask rather than guess — the version determines which pattern is right.
2. **Fetch the authoritative source** for the specific feature: the relevant
   official doc page, spec section, or man entry, pinned to the detected version.
   Prefer official docs, then the primary spec, then maintainer source, over blog
   posts or memory.
3. **Ground the decision** in what the source actually says; do not pattern-match
   from memory or carry a pattern across versions.
4. **Cite or flag.** Cite the source (page or section, and version) next to the
   decision. If no authoritative source is reachable, mark the decision
   `UNVERIFIED — assumed` rather than presenting it as established.

## Output contract

Next to each version-sensitive decision, show the detected version and the cited
source, or an explicit `UNVERIFIED — assumed` flag.

## Guardrails

- ask for the version rather than guess when it is ambiguous
- one authoritative source beats three blog posts
- never present an unverified version-sensitive choice as established — flag it
