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
| `formal-research` | Optional local-first Lean formalization lane for research claims. | `formal-skeleton-helper`, `lean-formalization-intake`, `lean-strict-verification-gate` |
| `formal-research-remote` | Reserved explicit remote formal lane profile; currently installs only local formal-lane skills until a separately reviewed remote adapter exists. | `formal-skeleton-helper`, `lean-formalization-intake`, `lean-strict-verification-gate` |
| `full-research` | All research-related skills. | `deep-research-workflow`, `source-research`, `research-briefing`, `research-report-reviewer`, `research-verification-gate`, `draft-writing`, `zotero`, `calibre`, `getscipapers-requester`, `paper-lookup`, `database-lookup`, `docling`, `get-available-resources`, `formal-skeleton-helper`, `lean-formalization-intake`, `lean-strict-verification-gate`, `model-router`, `workspace-rearranger`, `research-digest-wrapper`, `rss-news-digest`, `digest-bridge`, `tikz-draw`, `sagemath`, `graph-verifier`, `agent-group-discuss`, `prose`, `cross-agent-delegation`, `modal-research-compute`, `paper-review`, `annotated-review`, `vnthuquan`, `self-improving-agent`, `session-logs` |
| `library` | Paper and ebook library workflows. | `zotero`, `calibre`, `getscipapers-requester`, `paper-lookup` |
| `math` | Math and graph verification workflows. | `sagemath`, `graph-verifier`, `formal-skeleton-helper` |
| `multi-agent` | Multi-agent and structured workflow orchestration. | `agent-group-discuss`, `prose`, `model-router`, `cross-agent-delegation` |
| `research-core` | Default research planning, source gathering, report review, and delivery verification. | `research-briefing`, `deep-research-workflow`, `source-research`, `research-report-reviewer`, `research-verification-gate` |
| `serious-research` | Source-preserving research workflow with local libraries, document parsing, validation, and multi-agent orchestration. | `research-briefing`, `deep-research-workflow`, `source-research`, `research-report-reviewer`, `research-verification-gate`, `zotero`, `calibre`, `getscipapers-requester`, `paper-lookup`, `docling`, `database-lookup`, `paper-review`, `agent-group-discuss`, `prose`, `model-router`, `cross-agent-delegation`, `get-available-resources`, `formal-skeleton-helper`, `workspace-rearranger` |
| `workflow-tools` | Reusable planning helpers for resources, model routing, formal skeletons, and workspace organization. | `get-available-resources`, `model-router`, `formal-skeleton-helper`, `workspace-rearranger` |
| `writing-workflow` | Claim-preserving draft writing, rewriting, and revision-audit workflow. | `draft-writing` |

Related pages: [Skills](skills.md), [Optional Artifacts](artifacts.md), [Dependencies](dependencies.md), [Installation](installation.md).
