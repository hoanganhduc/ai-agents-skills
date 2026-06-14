# Profiles

A profile is a named bundle of skills. Profiles are the easiest way to install a coherent workflow without listing every skill manually. The default profile is `research-core`; the broadest profile is `full-research`.

Profiles do not automatically install optional artifacts. Add `--artifact-profile ...` when you also want templates, personas, entrypoint aliases, or management notices.

Common commands:

```bash
make precheck ARGS="--profile research-core"
make plan ARGS="--profile research-core"
make install ARGS="--profile research-core --dry-run"
make plan ARGS="--profile library --artifact-profile research-entrypoints --with-deps"
```

| Profile | Description | Skills |
|---|---|---|
| `digest` | Tracked-topic and RSS digest workflows. | `research-digest-wrapper`, `rss-news-digest`, `digest-bridge` |
| `document` | Document conversion and structured database lookup. | `docling`, `database-lookup` |
| `ebook` | Ebook discovery and library handoff. | `calibre`, `vnthuquan` |
| `figure` | Structural figure generation and checking. | `tikz-draw` |
| `formal-research` | Optional Lean formalization lane with local checks and declaration-search setup. | `formal-skeleton-helper`, `lean-formalization-intake`, `lean-explore-mcp`, `lean-strict-verification-gate` |
| `formal-research-remote` | Optional remote formal lane setup for LeanExplore and AXLE MCP plus local formal-lane skills; install is inert and does not start remote services. | `formal-skeleton-helper`, `lean-formalization-intake`, `lean-explore-mcp`, `lean-strict-verification-gate`, `axiom-axle-mcp` |
| `full-research` | All research-related skills. | `autonomous-research-loop`, `autonomous-research-loop-runtime`, `deep-research-workflow`, `source-research`, `research-briefing`, `research-report-reviewer`, `research-verification-gate`, `draft-writing`, `zotero`, `calibre`, `getscipapers-requester`, `paper-lookup`, `submission-venue-selector`, `database-lookup`, `docling`, `get-available-resources`, `formal-skeleton-helper`, `lean-formalization-intake`, `lean-explore-mcp`, `lean-strict-verification-gate`, `axiom-axle-mcp`, `model-router`, `workspace-rearranger`, `research-digest-wrapper`, `rss-news-digest`, `digest-bridge`, `tikz-draw`, `sagemath`, `graph-verifier`, `agent-group-discuss`, `prose`, `cross-agent-delegation`, `modal-research-compute`, `paper-review`, `annotated-review`, `vnthuquan`, `self-improving-agent`, `session-logs`, `intent-interview`, `decision-doubt-loop`, `source-grounded-decisions` |
| `library` | Paper and ebook library workflows. | `zotero`, `calibre`, `getscipapers-requester`, `paper-lookup` |
| `math` | Math and graph verification workflows. | `sagemath`, `graph-verifier`, `formal-skeleton-helper` |
| `multi-agent` | Multi-agent and structured workflow orchestration. | `agent-group-discuss`, `prose`, `model-router`, `cross-agent-delegation`, `autonomous-research-loop`, `autonomous-research-loop-runtime`, `decision-doubt-loop` |
| `research-core` | Default research planning, source gathering, report review, and delivery verification. | `research-briefing`, `autonomous-research-loop`, `autonomous-research-loop-runtime`, `deep-research-workflow`, `source-research`, `research-report-reviewer`, `research-verification-gate`, `intent-interview` |
| `serious-research` | Source-preserving research workflow with local libraries, document parsing, validation, multi-agent orchestration, and Lean skeleton support only; use formal-research for Lean verification. | `research-briefing`, `autonomous-research-loop`, `autonomous-research-loop-runtime`, `deep-research-workflow`, `source-research`, `research-report-reviewer`, `research-verification-gate`, `zotero`, `calibre`, `getscipapers-requester`, `paper-lookup`, `submission-venue-selector`, `docling`, `database-lookup`, `paper-review`, `agent-group-discuss`, `prose`, `model-router`, `cross-agent-delegation`, `get-available-resources`, `formal-skeleton-helper`, `workspace-rearranger`, `intent-interview`, `decision-doubt-loop`, `source-grounded-decisions` |
| `workflow-tools` | Reusable planning helpers for resources, model routing, formal skeletons, and workspace organization. | `autonomous-research-loop`, `autonomous-research-loop-runtime`, `get-available-resources`, `model-router`, `formal-skeleton-helper`, `workspace-rearranger` |
| `writing-workflow` | Claim-preserving draft writing, rewriting, and revision-audit workflow. | `draft-writing` |

Related pages: [Skills](skills.md), [Optional Artifacts](artifacts.md), [Dependencies](dependencies.md), [Installation](installation.md).
