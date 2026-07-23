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

Installation is partial by default: selecting one skill installs only that skill, its support files when the selected install mode needs them, and the managed instruction block for that installed or adopted skill. Skipped skills do not receive instruction blocks. Default `auto` mode links Claude skill files to `canonical/skills`, while Codex, DeepSeek, and Copilot receive reference adapters unless native loader evidence justifies a different policy. OpenCode, Grok, Kimi Code, and Antigravity receive copied regular files by default. Explicit `symlink`, `reference`, and `copy` modes force the same strategy for every agent. In `reference` mode, the installed `SKILL.md` is an adapter that points back to this repo; support files remain in `canonical/skills/<skill>/` instead of being copied into the agent home.

Some older local skill names are accepted as migration aliases. For example, `deep-research` maps to `deep-research-workflow`, `smart_model_router` maps to `model-router`, and `openclaw-research` maps to `source-research`. OpenClaw-style `self-improvement` and `self_improvement` map to `self-improving-agent`. Use `audit-system` and a reviewed `--migrate` plan before replacing legacy alias directories.

| Skill | Description | Profiles |
|---|---|---|
| `adversarial-boundary-gate` | Pre-delivery threat-model of trust boundaries and an abuse-case/injection check, delegating to a fresh-context security reviewer. | `serious-research`, `full-research` |
| `agent-group-discuss` | Multi-agent discussion, review, and research orchestration. | `multi-agent`, `serious-research`, `full-research` |
| `annotated-review` | Annotated paper review workflow when both annotation and review are requested. | `full-research` |
| `autonomous-research-loop` | Run bounded autonomous research iterations with evidence gates, recovery ledgers, and optional cross-agent handoffs; prefers host-owned multi-agent panel with single-path drive primary. | `research-core`, `serious-research`, `workflow-tools`, `multi-agent`, `full-research` |
| `autonomous-research-loop-runtime` | Offline runtime helper for loop ledgers plus headless drive and panel (host-owned multi-agent phases via --panel on, auto, or off). | `research-core`, `serious-research`, `workflow-tools`, `multi-agent`, `full-research` |
| `axiom-axle-mcp` | Optional inert setup helper for AxiomMath AXLE MCP formal-proof assistance. | `formal-research-remote`, `full-research` |
| `behavior-preserving-cleanup` | Clarity-only edit pass behind a comprehension gate with verify-after-each-change so behavior stays fixed. | `serious-research`, `full-research` |
| `calibre` | Calibre ebook lookup and library helper workflows. | `library`, `ebook`, `serious-research`, `full-research` |
| `classroom50` | Route Classroom50 (foundation50) instructor workflows through the course_hoanganhduc agent entrypoint: preflight, list classrooms/roster/assignments, roster sync into local DB, and C50 CSV export. Does not invoke raw gh teacher. | `course-management` |
| `course-canvas` | Route Canvas LMS course operations through the course_hoanganhduc canvas agent: preflight, list assignments/members, search users, and roster sync. Refuses unenroll, grade, invite, announce, messages, pages, and bulk download. | `course-management` |
| `course-db` | Route local course student-database operations through the course_hoanganhduc db agent: search, details, domain/duplicate/missing-id lists, roster and email export. Refuses interactive modify, restore, and destructive import apply. | `course-management` |
| `course-google-classroom` | Route Google Classroom operations through the course_hoanganhduc gclass agent: preflight, list courses/students, and roster sync. Refuses unenroll, grade, and submission download. | `course-management` |
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
| `hetzner-research-compute` | Route heavy CPU or high-memory compute to a disposable Hetzner Cloud server through the local broker, with agent-driven provision, run, collect, and destroy under hard cost caps. | `full-research` |
| `intent-interview` | Elicit and confirm real intent one question at a time before any brief, spec, or code. | `research-core`, `serious-research`, `full-research` |
| `kaggle-research-compute` | Route heavy compute to free Kaggle Kernels through the local broker, with agent-driven push, poll, fetch, and a multi-run resume loop across concurrent kernels; free CPU (quota-free) and GPU under a self-imposed weekly GPU-hour cap. | `full-research` |
| `lean-explore-mcp` | Optional inert LeanExplore MCP setup helper for Lean declaration search. | `formal-research`, `formal-research-remote`, `full-research` |
| `lean-formalization-intake` | Optional local-first Lean formalization intake and suitability decision workflow. | `formal-research`, `formal-research-remote`, `full-research` |
| `lean-strict-verification-gate` | Scanner-first Lean artifact verification gate that separates typecheck status from claim support. | `formal-research`, `formal-research-remote`, `full-research` |
| `manim-math-animation` | Render Manim math animations (handwritten-style equation Write, equation morphing, emphasis) to a silent clip normalized for splicing into slides-to-video or standalone use. | `media`, `full-research` |
| `modal-research-compute` | Route heavy compute through the unified local broker, including Modal-backed remote CPU, high-memory CPU, and GPU execution. | `full-research` |
| `model-router` | Choose an appropriate model, reasoning level, and role for subagents or multi-agent research work. | `workflow-tools`, `multi-agent`, `serious-research`, `full-research` |
| `opengauss` | Optional inert readiness helper for Math Inc. OpenGauss Lean prove/formalize workflows; live install is manual-native. | `formal-research`, `formal-research-remote`, `full-research` |
| `paper-lookup` | External paper metadata and discovery fallback. | `library`, `serious-research`, `full-research` |
| `paper-review` | Single-agent paper review workflow. | `serious-research`, `full-research` |
| `prose` | Structured reproducible research and workflow orchestration. | `multi-agent`, `serious-research`, `full-research` |
| `remote-bridge` | Cross-target remote control plane: Zulip default control plus optional Telegram mobile notify, mailbox approvals/instructions, and ARL drive integration. Not an OpenClaw skill target; optional dual-route /aas adapter is published from canonical runtime into OpenClaw workspace. |  |
| `research-briefing` | Scope nontrivial research before execution with evidence plan and workflow recommendation. | `research-core`, `serious-research`, `full-research` |
| `research-digest-wrapper` | Run tracked-topic research digests. | `digest`, `full-research` |
| `research-report-reviewer` | Review draft research reports for unsupported claims, ambiguity, and evidence gaps. | `research-core`, `serious-research`, `full-research` |
| `research-verification-gate` | Final evidence, date, and gap check before delivery. | `research-core`, `serious-research`, `full-research` |
| `rss-news-digest` | Run and manage RSS digest workflows. | `digest`, `full-research` |
| `sagemath` | Sage-backed math, graph theory, algebra, and verification. | `math`, `full-research` |
| `self-improving-agent` | Log durable learnings and propose canonical repo integration plans across install targets. | `full-research` |
| `send-email` | Send email over SMTP using only the Python standard library: plain-text and HTML bodies, attachments, cc/bcc, reply-to, dry-run preview, connection verification, and redacted config inspection. |  |
| `session-logs` | Search prior local agent session logs when explicitly requested. | `full-research` |
| `slides-to-video` | Turn prepared slides (PNG/PDF/PPTX) into a narrated, captioned video in a chosen language and presenter role using only free tools; three-phase human-in-the-loop with an approval gate before rendering. | `media`, `full-research` |
| `source-grounded-decisions` | Ground version- and spec-sensitive decisions in cited authoritative sources; flag when unverified. | `serious-research`, `full-research` |
| `source-research` | General web and source-gathering research workflow for current-information synthesis. | `research-core`, `serious-research`, `full-research` |
| `submission-venue-selector` | Evidence-gated journal and conference venue selection for scholarly drafts; deliverable rankings require comparator-paper evidence. | `serious-research`, `full-research` |
| `tikz-draw` | Structural TikZ figure generation, compile, review, and semantic checks. | `figure`, `full-research` |
| `url-to-screenshot` | Capture a URL to a clean PNG screenshot with browser detection, cookie-consent dismissal, viewport or full-page modes, timeouts, SSRF-safe URL admission, and blank-output verification across Linux, macOS, and Windows. | `media`, `full-research` |
| `url-to-screenshot-runtime` | Runtime engine for url-to-screenshot: headless-browser CDP capture, SSRF-safe URL admission, consent dismissal, blank-output detection, and an offline self-test of the deterministic core. | `media`, `full-research` |
| `venue-ranking-evidence` | Resolve partial journal and conference names and preserve source-specific rank/index observations. ICORE alone has built-in live edition discovery and verified browser-print proof; nine other built-ins accept authorized normalized imports without establishing latest status, and Conference Ranks remains legacy. | `serious-research`, `full-research` |
| `vnthuquan` | Vietnam Thu Quan ebook discovery, validation, dry-run download, and Calibre dry-run handoff. | `ebook`, `full-research` |
| `vnu-eoffice` | Route VNU eOffice requests to an existing vnu_eoffice package or CLI: monitor updates, list latest incoming/outgoing documents, search by keyword, download attachments, and send requested files through Telegram. |  |
| `workspace-rearranger` | Plan safe workspace organization with dry-run first, explicit apply, and no silent deletion. | `workflow-tools`, `serious-research`, `full-research` |
| `zotero` | Zotero paper search, retrieval, ingest, and collection workflow. | `library`, `serious-research`, `full-research` |

Related pages: [Installation](installation.md), [Verification](verification.md), [Agent Locations](agent-locations.md).
