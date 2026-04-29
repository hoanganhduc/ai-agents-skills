# AI Agents Skills

Shared, manifest-driven skills and settings for Codex, Claude, and DeepSeek.

This repo is a generator and installer, not a copied dotfiles folder. It uses
canonical skill names, generates per-agent adapters, supports partial installs,
detects legacy/self-contained installs, and verifies only installed managed
skills.

## Quick Start

Linux:

```bash
make doctor
make list-skills
make plan ARGS="--profile research-core"
make install ARGS="--profile research-core --dry-run"
make install ARGS="--profile research-core --apply --root /tmp/aas-fake-home"
make verify ARGS="--root /tmp/aas-fake-home"
```

Windows:

```bat
make.bat doctor
make.bat list-skills
make.bat plan --profile research-core
make.bat install --profile research-core --dry-run
make.bat install --profile research-core --apply --root %TEMP%\aas-fake-home
make.bat verify --root %TEMP%\aas-fake-home
```

Real-system writes require explicit `--apply --real-system`. Tests and examples
use fake roots. Existing unmanaged files are skipped by default; use `--adopt`,
`--backup-replace`, or `--migrate` only after reviewing `plan` output.

## Profiles

| Profile | Description | Skills |
|---|---|---|
| `digest` | Tracked-topic and RSS digest workflows. | `research-digest-wrapper`, `rss-news-digest`, `digest-bridge` |
| `document` | Document conversion and structured database lookup. | `docling`, `database-lookup` |
| `ebook` | Ebook discovery and library handoff. | `calibre`, `vnthuquan` |
| `figure` | Structural figure generation and checking. | `tikz-draw` |
| `full-research` | All research-related skills. | `*` |
| `library` | Paper and ebook library workflows. | `zotero`, `calibre`, `getscipapers-requester`, `paper-lookup` |
| `math` | Math and graph verification workflows. | `sagemath`, `graph-verifier` |
| `multi-agent` | Multi-agent and structured workflow orchestration. | `agent-group-discuss`, `prose` |
| `research-core` | Default research planning, source gathering, report review, and delivery verification. | `research-briefing`, `deep-research-workflow`, `openclaw-research`, `research-report-reviewer`, `research-verification-gate` |

## Skills

| Skill | Description | Profiles |
|---|---|---|
| `agent-group-discuss` | Multi-agent discussion, review, and research orchestration. | `multi-agent`, `full-research` |
| `annotated-review` | Annotated paper review workflow when both annotation and review are requested. | `full-research` |
| `calibre` | Calibre ebook lookup and library helper workflows. | `library`, `ebook`, `full-research` |
| `database-lookup` | Structured public scientific, biomedical, regulatory, materials, and economic database lookups. | `document`, `full-research` |
| `deep-research-workflow` | Phased source-preserving research workflow: search, analyze, write, with citation handoff. | `research-core`, `full-research` |
| `digest-bridge` | Convert digest output into paper retrieval manifests. | `digest`, `full-research` |
| `docling` | Parse, convert, OCR, chunk, and analyze documents. | `document`, `full-research` |
| `getscipapers-requester` | External paper retrieval fallback after local library checks. | `library`, `full-research` |
| `graph-verifier` | Lightweight graph sanity checks. | `math`, `full-research` |
| `modal-research-compute` | Route heavy compute jobs to Modal through a local broker. | `full-research` |
| `openclaw-research` | General web and source-gathering research workflow for current-information synthesis. | `research-core`, `full-research` |
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
| `tikz-draw` | Structural TikZ figure generation, compile, review, and semantic checks. | `figure`, `full-research` |
| `vnthuquan` | Vietnam Thu Quan ebook discovery, validation, dry-run download, and Calibre dry-run handoff. | `ebook`, `full-research` |
| `zotero` | Zotero paper search, retrieval, ingest, and collection workflow. | `library`, `full-research` |
