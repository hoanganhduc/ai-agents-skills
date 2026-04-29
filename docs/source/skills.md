# Skills

A skill is an installable agent capability. Each skill has one canonical name in this repository, one canonical body under `canonical/skills/<skill>/`, and generated target files for every supported agent that is detected on the machine.

Use this page when you already know which capability you want. Use [Profiles](profiles.md) when you want a bundle, and [Optional Artifacts](artifacts.md) when you want templates, personas, or command-style entrypoints in addition to skills.

Common commands:

```bash
make list-skills
make plan ARGS="--skill zotero"
make install ARGS="--skills zotero,docling --dry-run"
make verify ARGS="--skill zotero --root /tmp/aas-fake-home"
```

Installation is partial by default: selecting one skill installs only that skill, its support files when the selected install mode needs them, and the managed instruction block for that installed or adopted skill. Skipped skills do not receive instruction blocks. Default `symlink` mode points agent skill files at `canonical/skills`; `reference` mode writes thin adapters that tell agents where to read the canonical skill; `copy` mode writes regular files.

| Skill | Description | Profiles |
|---|---|---|
| `agent-group-discuss` | Multi-agent discussion, review, and research orchestration. | `multi-agent`, `full-research` |
| `annotated-review` | Annotated paper review workflow when both annotation and review are requested. | `full-research` |
| `calibre` | Calibre ebook lookup and library helper workflows. | `library`, `ebook`, `full-research` |
| `database-lookup` | Structured public scientific, biomedical, regulatory, materials, and economic database lookups. | `document`, `full-research` |
| `deep-research-workflow` | Phased source-preserving research workflow: search, analyze, write, with citation handoff. | `research-core`, `full-research` |
| `digest-bridge` | Convert digest output into paper retrieval manifests. | `digest`, `full-research` |
| `docling` | Parse, convert, OCR, chunk, and analyze documents. | `document`, `full-research` |
| `formal-skeleton-helper` | Generate minimal Lean-style theorem skeletons, namespace wrappers, and formal statement stubs. | `workflow-tools`, `math`, `full-research` |
| `get-available-resources` | Detect CPU, memory, disk, and optional accelerator availability before heavy local work. | `workflow-tools`, `full-research` |
| `getscipapers-requester` | External paper retrieval fallback after local library checks. | `library`, `full-research` |
| `graph-verifier` | Lightweight graph sanity checks. | `math`, `full-research` |
| `modal-research-compute` | Route heavy compute jobs to Modal through a local broker. | `full-research` |
| `model-router` | Choose an appropriate model, reasoning level, and role for subagents or multi-agent research work. | `workflow-tools`, `multi-agent`, `full-research` |
| `paper-lookup` | External paper metadata and discovery fallback. | `library`, `full-research` |
| `paper-review` | Single-agent paper review workflow. | `full-research` |
| `prose` | Structured reproducible research and workflow orchestration. | `multi-agent`, `full-research` |
| `research-briefing` | Scope nontrivial research before execution with evidence plan and workflow recommendation. | `research-core`, `full-research` |
| `research-digest-wrapper` | Run tracked-topic research digests. | `digest`, `full-research` |
| `research-report-reviewer` | Review draft research reports for unsupported claims, ambiguity, and evidence gaps. | `research-core`, `full-research` |
| `research-verification-gate` | Final evidence, date, and gap check before delivery. | `research-core`, `full-research` |
| `rss-news-digest` | Run and manage RSS digest workflows. | `digest`, `full-research` |
| `sagemath` | Sage-backed math, graph theory, algebra, and verification. | `math`, `full-research` |
| `self-improving-agent` | Log durable learnings, failures, and missing capabilities. | `full-research` |
| `session-logs` | Search prior local agent session logs when explicitly requested. | `full-research` |
| `source-research` | General web and source-gathering research workflow for current-information synthesis. | `research-core`, `full-research` |
| `tikz-draw` | Structural TikZ figure generation, compile, review, and semantic checks. | `figure`, `full-research` |
| `vnthuquan` | Vietnam Thu Quan ebook discovery, validation, dry-run download, and Calibre dry-run handoff. | `ebook`, `full-research` |
| `workspace-rearranger` | Plan safe workspace organization with dry-run first, explicit apply, and no silent deletion. | `workflow-tools`, `full-research` |
| `zotero` | Zotero paper search, retrieval, ingest, and collection workflow. | `library`, `full-research` |

Related pages: [Installation](installation.md), [Verification](verification.md), [Agent Locations](agent-locations.md).
