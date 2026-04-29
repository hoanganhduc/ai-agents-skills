---
name: openclaw-research
description: Use when the user wants research, source gathering, current-information lookups, cross-source synthesis, or extraction from URLs/PDFs/videos. Mirrors OpenClaw's research workflow in Codex using web search, page inspection, local tools, and sub-agents.
metadata:
  short-description: OpenClaw-style research workflow for Codex
---

# OpenClaw Research

Use this as the default research router for Codex.

## Supporting files

Open these only when relevant:

- `references/specialist-subagents.md` for focused single-specialist delegation briefs imported from the local Claude setup

## Tool mapping

- OpenClaw `web_search` -> Codex `web.search_query`
- OpenClaw `web_fetch` -> Codex `web.open`
- OpenClaw task/session spawning -> Codex `spawn_agent`
- OpenClaw `exec` -> Codex `functions.exec_command`

## Live workspace routing imported from the bot

- Generic paper, article, preprint, DOI, ISBN, and book retrieval or sharing requests route to `zotero` first.
- Explicit Calibre-library ebook operations route to `calibre`.
- External paper or book retrieval routes to `getscipapers_requester` only if Zotero does not satisfy the request or the user explicitly wants a download from outside the library.
- Multi-agent discussion/review/research routes to `agent_group_discuss` by default and `prose` for more structured workflows.
- Annotated paper review routes to `annotated-review` only when the user explicitly asks for both annotation and review.
- Review-only paper tasks route to `paper-review`.
- Topic/news digests route to `research_digest_wrapper` or `rss_news_digest`.
- Digest-to-paper extraction routes to `digest_bridge`.
- Explicit phased deep research with structured source handoff routes to `deep-research-workflow`.
- Explicit TikZ drawing, refactoring, extraction, compile, or diagram-review requests route to `tikz-draw`.
- Small graph-theoretic verification routes to `graph_verifier`.
- Mathematical research tasks that need heavy graph-theoretic, combinatorial, algebraic, or spectral computation route to `sagemath`.

## Default workflow

1. If the request is current, says "latest", "today", "verify", or otherwise depends on recent facts, browse first.
2. Start with focused search queries, not long prompts.
3. Open the best primary sources, not just search snippets.
4. Extract only the parts needed for the answer.
5. Synthesize after evidence collection, not before.
6. Cite concrete sources and dates whenever the answer depends on live or specific facts.

## Research heuristics

- Prefer primary sources: official docs, vendor pages, papers, standards, government sites.
- Use domain filters when the source class is obvious.
- For PDFs, use `web.open` and `web.screenshot` when needed.
- For local files, use `functions.exec_command` instead of browsing.
- Separate sourced facts from your own inference.
- Prefer `deep-research-workflow` over this skill when the user explicitly wants phased search -> analysis -> writing with source preservation across stages.
- Prefer `tikz-draw` for explicit TikZ or structural-diagram requests, including research-derived figures that already have a narrowed brief.
- If a research task needs a diagram, keep the research in `deep-research-workflow` first and hand off to `tikz-draw` only after analysis identifies a concrete figure worth generating.
- For literature retrieval requests, check the relevant local library workflow before browsing or external download.
- For review tasks that need a paper/book, use the lookup order `zotero` -> `calibre` -> online fallback.
- For digest follow-up, use `digest_bridge` to extract identifiers before bulk paper retrieval.
- For explicit Calibre ebook-library workflows, prefer `calibre` over improvised filesystem search.
- For mathematical verification, prefer local Python for small checks and route to `sagemath` when the computation needs SageMath-native capabilities.
- Never use `curl`, `wget`, ad hoc browser fetches, or direct publisher-site HTTP requests as a substitute for the library tools on paper/book retrieval tasks.

## Parallelism

If the task has distinct independent threads, spawn sub-agents in parallel.

Good candidates:
- source discovery vs synthesis plan
- product A vs product B
- multiple independent subtopics

Do not spawn agents for the immediate blocking step if local work is faster.

## Focused specialist delegation

When the task needs one narrow specialist rather than a full panel, reuse the
briefs in `references/specialist-subagents.md`.

Good matches:

- literature survey or citation hunting -> `literature-scout`
- small-case exploration or counterexample search -> `math-explorer`
- single-reviewer manuscript critique -> `paper-reviewer`
- adversarial checking of one proof/lemma -> `proof-checker`

## Route to narrower skills

- Use `zotero` for generic paper, DOI, ISBN, book, and paper-library retrieval or sharing requests, including "find/get/send/share this paper or book".
- Use `docling` for local parsing, conversion, chunking, and structure-aware analysis of PDFs, office documents, HTML, and image-backed documents.
- Use `get-available-resources` before heavy local parsing or compute when resource constraints may materially affect the approach.
- Use `database-lookup` for structured public scientific, regulatory, and economic database queries.
- Use `paper-lookup` for external literature metadata/discovery after the library-first workflow and before external retrieval.
- Use `deep-research-workflow` for single-agent phased deep research that needs explicit source handoff across search, analysis, and final writing.
- Use `tikz-draw` for explicit TikZ figure generation, refactoring, extraction, compile, or review work, especially when the output should follow a structure-first brief -> spec -> render flow.
- Use `calibre` for explicit Calibre library operations: ebook search/get by ID, sending Calibre-managed books, add/update, tags, shelves, sync, remove, convert, export, doctor, and clean.
- Use `getscipapers_requester` only for external retrieval or DOI/ISBN resolution after Zotero does not satisfy the request, or when the user explicitly wants an outside download rather than a library copy. For review tasks that need a document, check `calibre` after `zotero` and before this fallback.
- If an externally retrieved book file should be added to the ebook library, hand off to `calibre` for the final add/update step.
- Use `annotated-review` only for annotate+review paper flows.
- Use `paper-review` for single-agent review-only paper flows such as review, critique, hard review, or issue-finding requests.
- Use `agent_group_discuss` for conversational multi-agent discussion, review, or research panels.
- Use `prose` when the user wants explicit multi-agent research-and-synthesis orchestration.
- Use `research_digest_wrapper` and `rss_news_digest` for tracked-topic and RSS-based research updates.
- Use `graph_verifier` for lightweight graph sanity checks before escalating to SageMath.
- Use `digest_bridge` to turn digest results into identifiers and manifests for retrieval.
- Use `sagemath` for graph invariants, algebraic checks, spectral computations, exhaustive counterexample search, and other math tasks beyond lightweight local tooling.
