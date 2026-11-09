---
name: graph-verifier
description: Use when the user wants a quick sanity check for a finite graph claim, construction, or encoding using the lightweight OpenClaw verifier.
metadata:
  short-description: Lightweight graph claim verification
---

# Graph Verifier


## Windows Runtime Commands

On native Windows, use the managed Windows runner and the native runtime command target. Set `$runtime` to the installed runtime root. Multi-agent installs usually use `%LOCALAPPDATA%\ai-agents-skills\runtime`. Then run:

```powershell
$runtime = if ($env:AAS_RUNTIME_ROOT) { $env:AAS_RUNTIME_ROOT } else { "$env:LOCALAPPDATA\ai-agents-skills\runtime" }
& "$runtime\run_skill.ps1" "skills/graph-verifier/graph_verifier.py" <args>
```

POSIX examples below use `run_skill.sh` and `.sh` command targets; use the Windows command target above on native Windows.

This uses the managed ai-agents-skills runtime copy of the graph verifier workflow.

## When to use

- sanity-check a small graph claim
- inspect a finite construction
- validate an edge list, adjacency map, or graph encoding
- check simple properties such as connectivity or bipartiteness

For heavier graph-theoretic or algebraic computations, route to `sagemath` instead.

## Base path

- `$AAS_RUNTIME_ROOT/workspace/skills/graph-verifier/`

Use the managed runtime runner rather than invoking `run_graph_verifier.sh` directly.
Set `AAS_RUNTIME_ROOT` to the installed runtime root before using the shared
runner directly.

Shared runner:

- `runtime_root="${AAS_RUNTIME_ROOT:?Set AAS_RUNTIME_ROOT to the installed runtime root}"; bash "$runtime_root/run_skill.sh"`

## Workflow

1. Save JSON input to `/tmp/graph_input.json`.
2. Run the verifier with `--input` pointing at that file.
3. Read the JSON result from stdout.

Supported shapes include `graph_data`, `edges`, `adjacency`, and optional `expected` values.

An input is required. `--demo` checks the built-in Petersen graph instead, to
show the output format; it cannot be combined with `--input`.

## Input failures

When there is nothing to check, the verifier prints
`{"ok": false, "error": "...", "verdict": "no_verdict"}` and exits 0. This
covers no `--input` or an empty one, a path that is missing or not a regular
file, an empty file, malformed JSON, a payload that is not a JSON object or has
none of `graph_data`, `edges` and `adjacency`, a graph networkx cannot build
(the error starts with `invalid graph input`), and an `expected` value that is
neither an object nor `null`. `expected: null` means no expected values. A
`no_verdict` result neither supports nor refutes anything.

## Output

A run that read a graph prints `ok: true` and these fields:

- `n`, `m`, `connected`, `bipartite`, `tree`, `planar`, `diameter`, `girth`,
  `degree_sequence` and `small_n_ok`: the properties, `null` when not computed.
- `property_status`: `checked`, `skipped` or `error` for each property above.
  `planar` is skipped above 1000 vertices or 5000 edges, and `diameter` for a
  disconnected graph or one with more than 300 vertices. `girth` is skipped for
  a graph without edges and is `null` with status `checked` for an acyclic
  graph; networkx releases without `nx.girth` (2.8.8 has none) report `error`.
- `degree_sequence` lists the first 50 degrees in decreasing order;
  `degree_sequence_length` and `degree_sequence_truncated` give the full length
  and whether the list was cut. An expected `degree_sequence` is compared with
  the whole sequence, but a mismatch shows the printed part.
- `input_source`: `file`, or `builtin_demo` under `--demo`.
- `unchecked_expected_keys`: expected keys that name no property above, or
  whose property was skipped or failed. They are never mismatches.
- `mismatches`: present only when a checked property differs from its
  expected value.
- `matches_expected`: true only when nothing mismatches and no expected key is
  unchecked, so it is also true for an empty `expected` map.
- `claim_evaluated`: always false. The `claim` text is echoed, never checked.
- `computation_evidence`: a record of the check in the form deep-research
  computation evidence uses.

`computation_evidence` describes the `expected` map, not the claim. It carries
`tool_name`, `tool_version` (the networkx version), `input_encoding_ref` (the
sha256 of the input file's bytes, or `builtin:petersen_graph` under `--demo`),
`checked_domain`, `graph_model_assumptions`, `resource_bounds`,
`enumeration_method`, `timeout_status`, `result_status`, `coverage_status`,
`evaluated_predicate` (the `expected` map as JSON with sorted keys), a one-line
`outcome` and `limitations`. The limitations always include "the claim text is
not evaluated" and name each skipped or failed property.

- `result_status` is `failed` when a checked property mismatches, `passed` when
  `expected` is non-empty and every expected key was checked and matches, and
  `partial` otherwise, including for an empty `expected` map.
- `coverage_status` is `exhaustive` when `expected` is non-empty and no key is
  unchecked; the block then also carries an `exhaustiveness_argument`.
  Otherwise it is `partial`.

## Core command

```bash
runtime_root="${AAS_RUNTIME_ROOT:?Set AAS_RUNTIME_ROOT to the installed runtime root}"
bash "$runtime_root/run_skill.sh" skills/graph-verifier/run_graph_verifier.sh --input /tmp/graph_input.json
```

To see the output format on the built-in Petersen graph:

```bash
bash "$runtime_root/run_skill.sh" skills/graph-verifier/run_graph_verifier.sh --demo
```

## Recommended templates

When this skill is involved, consider this workflow template (install via
the `workflow-templates` artifact profile, or `--with-deps` to pull backing skills):

- `tikz-figure-verification-runbook` -- Bounded draw-compile-verify-redraw loop for a TikZ figure that guarantees it is free of overlap, wrong meaning, and bad layout, with Sage-assisted graph realization and fresh-agent visual confirmation before the strict approval gate.
