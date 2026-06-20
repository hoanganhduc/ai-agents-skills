# Language And File Style Rules

This file is kept as a compatibility router for older skills and installed
targets that still refer to `language-style-rules.md`.

## Current Sources

- General writing style now lives in
  `canonical/instructions/writing-style-settings.md`.
- Mathematical and LaTeX manuscript style now lives in
  `canonical/instructions/math-manuscript-style.md`.
- Claim-preserving rewrite discipline remains in
  `canonical/instructions/claim-preserving-writing.md`.

## Compatibility Behavior

When a workflow asks for `language-style-rules.md`, load
`writing-style-settings.md` first. If the task is a mathematical, TCS,
graph-theoretic, formal-proof, or LaTeX manuscript task, also load
`math-manuscript-style.md`.

This router intentionally avoids duplicating normative rule text. The migration
ledger `writing-style-migration-ledger.json` records where the previous rules
went and why they remain active, were split, or were merged.
