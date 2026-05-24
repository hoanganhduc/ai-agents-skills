# Semantic Regression Suite

This directory contains the persistent semantic-regression corpus for the current
TikZ semantic verifier.

Scope:

- supported good cases for the current render-generated `flowchart`, `dag`, and `tree` families
- mutation cases that preserve the original semantic target and intentionally change rendered output
- design cases that check visual-semantic contracts for graph/proof/reduction figures
- one fail-closed unsupported-family boundary case
- strict approval expectations for `approve`, including source-only bypass prevention, rendered overlap status, scoped design status, symmetry-contract status, and blocked states when available

The suite definition is the source of truth for regression expectations. Compiled
artifacts are generated at run time by `semantic_regression_runner.py` and are not
checked into the repository.

Use `--strict-approval` when implementation changes affect approval semantics.
