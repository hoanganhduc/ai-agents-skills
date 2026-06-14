# Skills

A skill is an installable agent capability. Each skill has one canonical name in this repository, one canonical body under `canonical/skills/<skill>/`, and generated target files for every supported agent that is detected on the machine.

Use this page when you already know which capability you want. Use [Profiles](profiles.md) when you want a bundle, and [Optional Artifacts](artifacts.md) when you want templates, personas, or command-style entrypoints in addition to skills.

Common commands:

```bash
make list-skills
make plan ARGS="--skill zotero"
make install ARGS="--skills zotero,docling --dry-run"
make lifecycle-test ARGS="--scenario clean-auto --platform-shape linux"
make fake-root-lifecycle ARGS="--skill zotero --platform-shape linux"
```

Installation is partial by default: selecting one skill installs only that skill, its support files when the selected install mode needs them, and the managed instruction block for that installed or adopted skill. Skipped skills do not receive instruction blocks. Default `auto` mode links Claude skill files to `canonical/skills`, while Codex, DeepSeek, and Copilot receive reference adapters unless native loader evidence justifies a different policy. OpenCode and Antigravity receive copied regular files by default. Explicit `symlink`, `reference`, and `copy` modes force the same strategy for every agent. In `reference` mode, the installed `SKILL.md` is an adapter that points back to this repo; support files remain in `canonical/skills/<skill>/` instead of being copied into the agent home.

Some older local skill names are accepted as migration aliases. For example, `deep-research` maps to `deep-research-workflow`, `smart_model_router` maps to `model-router`, and `openclaw-research` maps to `source-research`. OpenClaw-style `self-improvement` and `self_improvement` map to `self-improving-agent`. Use `audit-system` and a reviewed `--migrate` plan before replacing legacy alias directories.

| Skill | Description | Profiles |
|---|---|---|
| `adversarial-boundary-gate` | Pre-delivery threat-model of trust boundaries and an abuse-case/injection check, delegating to a fresh-context security reviewer. | `serious-research`, `full-research` |
| `agent-group-discuss` | Multi-agent discussion, review, and research orchestration. | `multi-agent`, `serious-research`, `full-research` |
| `annotated-review` | Annotated paper review workflow when both annotation and review are requested. | `full-research` |
| `autonomous-research-loop` | Run bounded autonomous research iterations with evidence gates, recovery ledgers, and optional cross-agent handoffs. | `research-core`, `serious-research`, `workflow-tools`, `multi-agent`, `full-research` |
| `autonomous-research-loop-runtime` | Offline runtime helper for autonomous research loop ledger initialization, iteration appends, validation, status, and selftest. | `research-core`, `serious-research`, `workflow-tools`, `multi-agent`, `full-research` |
| `axiom-axle-mcp` | Optional inert setup helper for AxiomMath AXLE MCP formal-proof assistance. | `formal-research-remote`, `full-research` |
| `behavior-preserving-cleanup` | Clarity-only edit pass behind a comprehension gate with verify-after-each-change so behavior stays fixed. | `serious-research`, `full-research` |
| `calibre` | Calibre ebook lookup and library helper workflows. | `library`, `ebook`, `serious-research`, `full-research` |
| `cross-agent-delegation` | Cross-agent delegation packet contract for bounded parent-controlled handoffs. | `multi-agent`, `serious-research`, `full-research` |
| `database-lookup` | Structured public scientific, biomedical, regulatory, materials, and economic database lookups. | `document`, `serious-research`, `full-research` |
| `decision-doubt-loop` | In-flight fresh-context adversarial review of a non-trivial decision before it stands. | `serious-research`, `full-research`, `multi-agent` |
| `deep-research-workflow` | Phased source-preserving research workflow: search, analyze, write, with citation handoff. | `research-core`, `serious-research`, `full-research` |
| `digest-bridge` | Convert digest output into paper retrieval manifests. | `digest`, `full-research` |
| `docling` | Parse, convert, OCR, chunk, and analyze documents. | `document`, `serious-research`, `full-research` |
| `draft-writing` | Claim-preserving draft writing workflow for controlled rewriting, polishing, and revision audits. | `writing-workflow`, `full-research` |
| `formal-skeleton-helper` | Generate minimal Lean-style theorem skeletons, namespace wrappers, and formal statement stubs. | `workflow-tools`, `math`, `formal-research`, `formal-research-remote`, `serious-research`, `full-research` |
| `get-available-resources` | Detect CPU, memory, disk, and optional accelerator availability before heavy local work. | `workflow-tools`, `serious-research`, `full-research` |
| `getscipapers-requester` | External paper retrieval fallback after local library checks. | `library`, `serious-research`, `full-research` |
| `graph-verifier` | Lightweight graph sanity checks. | `math`, `full-research` |
| `intent-interview` | Elicit and confirm real intent one question at a time before any brief, spec, or code. | `research-core`, `serious-research`, `full-research` |
| `lean-explore-mcp` | Optional inert LeanExplore MCP setup helper for Lean declaration search. | `formal-research`, `formal-research-remote`, `full-research` |
| `lean-formalization-intake` | Optional local-first Lean formalization intake and suitability decision workflow. | `formal-research`, `formal-research-remote`, `full-research` |
| `lean-strict-verification-gate` | Scanner-first Lean artifact verification gate that separates typecheck status from claim support. | `formal-research`, `formal-research-remote`, `full-research` |
| `modal-research-compute` | Route heavy compute jobs to Modal through a local broker. | `full-research` |
| `model-router` | Choose an appropriate model, reasoning level, and role for subagents or multi-agent research work. | `workflow-tools`, `multi-agent`, `serious-research`, `full-research` |
| `paper-lookup` | External paper metadata and discovery fallback. | `library`, `serious-research`, `full-research` |
| `paper-review` | Single-agent paper review workflow. | `serious-research`, `full-research` |
| `prose` | Structured reproducible research and workflow orchestration. | `multi-agent`, `serious-research`, `full-research` |
| `research-briefing` | Scope nontrivial research before execution with evidence plan and workflow recommendation. | `research-core`, `serious-research`, `full-research` |
| `research-digest-wrapper` | Run tracked-topic research digests. | `digest`, `full-research` |
| `research-report-reviewer` | Review draft research reports for unsupported claims, ambiguity, and evidence gaps. | `research-core`, `serious-research`, `full-research` |
| `research-verification-gate` | Final evidence, date, and gap check before delivery. | `research-core`, `serious-research`, `full-research` |
| `rss-news-digest` | Run and manage RSS digest workflows. | `digest`, `full-research` |
| `sagemath` | Sage-backed math, graph theory, algebra, and verification. | `math`, `full-research` |
| `self-improving-agent` | Log durable learnings and propose canonical repo integration plans across install targets. | `full-research` |
| `session-logs` | Search prior local agent session logs when explicitly requested. | `full-research` |
| `source-grounded-decisions` | Ground version- and spec-sensitive decisions in cited authoritative sources; flag when unverified. | `serious-research`, `full-research` |
| `source-research` | General web and source-gathering research workflow for current-information synthesis. | `research-core`, `serious-research`, `full-research` |
| `submission-venue-selector` | Evidence-gated journal and conference venue selection for scholarly drafts; deliverable rankings require comparator-paper evidence. | `serious-research`, `full-research` |
| `tikz-draw` | Structural TikZ figure generation, compile, review, and semantic checks. | `figure`, `full-research` |
| `vnthuquan` | Vietnam Thu Quan ebook discovery, validation, dry-run download, and Calibre dry-run handoff. | `ebook`, `full-research` |
| `vnu-eoffice` | Route VNU eOffice requests to an existing vnu_eoffice package or CLI: monitor updates, list latest incoming/outgoing documents, search by keyword, download attachments, and send requested files through Telegram. |  |
| `workspace-rearranger` | Plan safe workspace organization with dry-run first, explicit apply, and no silent deletion. | `workflow-tools`, `serious-research`, `full-research` |
| `zotero` | Zotero paper search, retrieval, ingest, and collection workflow. | `library`, `serious-research`, `full-research` |

Related pages: [Installation](installation.md), [Verification](verification.md), [Agent Locations](agent-locations.md).
