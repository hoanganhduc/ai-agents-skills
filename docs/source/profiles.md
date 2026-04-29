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
| `full-research` | All research-related skills. | `*` |
| `library` | Paper and ebook library workflows. | `zotero`, `calibre`, `getscipapers-requester`, `paper-lookup` |
| `math` | Math and graph verification workflows. | `sagemath`, `graph-verifier` |
| `multi-agent` | Multi-agent and structured workflow orchestration. | `agent-group-discuss`, `prose`, `model-router` |
| `research-core` | Default research planning, source gathering, report review, and delivery verification. | `research-briefing`, `deep-research-workflow`, `source-research`, `research-report-reviewer`, `research-verification-gate` |
| `workflow-tools` | Reusable planning helpers for resources, model routing, formal skeletons, and workspace organization. | `get-available-resources`, `model-router`, `formal-skeleton-helper`, `workspace-rearranger` |

Related pages: [Skills](skills.md), [Optional Artifacts](artifacts.md), [Dependencies](dependencies.md), [Installation](installation.md).
