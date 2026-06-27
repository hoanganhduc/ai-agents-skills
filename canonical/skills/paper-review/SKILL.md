---
name: paper-review
description: Use for review-only requests for papers or books when the user did not explicitly ask for annotation. Handles the normal single-agent review flow.
metadata:
  short-description: Single-agent paper review workflow
---

# Paper Review

Use this skill for the normal single-agent review flow.

## Trigger rule

Use this skill when the user asks for a review-only pass such as:

- review this paper
- critique this paper
- hard review
- find issues in this paper
- review and add to Zotero

Do **not** use this skill when the user explicitly asks for both annotation and review.
In that case, use `annotated-review` instead.

If the user explicitly asks for multiple agents, a panel, or a multi-agent review,
use `agent-group-discuss` instead of this skill.

## Document lookup order for review tasks

If the user did not already provide a source path, attached file, PDF, or source tree:

1. check `zotero`
2. if not found there, check `calibre`
3. only if neither library has the document, use an online path such as `getscipapers-requester`

For review tasks, do not go online before checking both local libraries.

## Document parsing preference

When you have the document as a local PDF, office file, HTML export, or image-backed scan, prefer `docling` for structure-aware parsing before relying on ad hoc plain-text extraction.

Use Docling especially when the review depends on:

- section hierarchy
- table extraction
- figure or picture detection
- reading order in complex layouts
- OCR on scanned pages

## Writing Style Gate

Before writing review prose, load `writing-style-settings.md` and record the
active style profile for the review artifact. If the paper is a mathematical,
TCS, graph-theoretic, Lean, or LaTeX manuscript, also load
`math-manuscript-style.md`. Final review artifacts should record
`style_profile_ref`, `active_overlays`, `active_requirement_ids`, and
`style_applied`; do not treat a bare `style_applied: true` assertion as
evidence that the style policy was loaded.

## Zotero rule

Zotero note storage is off by default for review-only requests.

- "Review this paper" -> review only, no Zotero write
- "Review and add to Zotero" -> do the review first, and only add/store in Zotero if the review workflow explicitly supports it and the user asked for it

Do not touch Zotero beyond lookup/retrieval unless the user explicitly asks.

## Review expectations

- Keep the review single-agent by default.
- Focus on correctness, argument quality, clarity, missing assumptions, and important edge cases.
- When useful, use the imported `references/common_issues.md` and `references/reporting_standards.md` as internal checklists.
- Summarize the main issues clearly, with evidence from the provided or retrieved document.
- If the document cannot be found in Zotero or Calibre, report that before attempting online retrieval.
- If you need a narrow internal checklist for proof auditing or single-reviewer critique, adapt `source-research/references/specialist-subagents.md` without turning the task into a multi-agent run unless the user asked for one.
- For review-only requests, stop after the review. Do not annotate, store notes,
  patch manuscripts, retrieve extra nonessential artifacts, or begin fixes
  unless the user explicitly asks for those actions.

## Recommended output format

### Summary

- paper title, authors, venue/year when available
- overall assessment
- active writing-style profile and overlays, if the review is stored as an
  artifact

### Issues

For each issue:

- **Severity**: critical / major / minor / suggestion
- **Type**: logic / math / consistency / notation / presentation / missing / unsupported
- **Location**: page, section, line, or paragraph reference
- **Quote**: short supporting quote when helpful
- **Description**: what fails and why

### Strengths

- key contributions
- what works well

### Recommended actions

- prioritized fixes, highest severity first

## Routing boundary

- review-only -> this skill
- annotate + review -> `annotated-review`
- multi-agent review -> `agent-group-discuss`

## Recommended templates

When this skill is involved, consider these workflow templates (install via
the `workflow-templates` artifact profile, or `--with-deps` to pull backing skills):

- `cross-agent-adversarial-review` -- Producer-never-confirmer adversarial review of a paper, proof, or code artifact across agent families with a fresh-agent confirmation gate.
