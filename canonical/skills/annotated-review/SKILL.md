---
name: annotated-review
description: Use only when the user explicitly mentions both annotation and review for a paper task. Produces annotated review outputs and supports an explicit add-to-Zotero step when the user asks for it.
metadata:
  short-description: Annotate+review paper workflow
---

# Annotated Review

This adapts the live OpenClaw paper-review workflow.

## Trigger rule

Use this skill only when the request explicitly contains both ideas:

- "annotate" (or "annotation" / "annotated"), and
- "review"

Both ideas must be present in the user request. Review intent alone is not enough.

Examples that should trigger this skill:

- annotate and review this paper
- give me an annotated review
- annotate this paper, then review it
- annotate this paper and add the review to Zotero

Examples that should **not** trigger this skill by themselves:

- review this paper
- critique this paper
- hard review
- find issues in this paper
- multi-agent review
- review and add to Zotero

Routing rule for review-only requests:

- if the user asks only for a review, use the normal single-agent review flow via `paper-review`
- if the user asks for a multi-agent review, use `agent_group_discuss`
- do not auto-route review-only requests to `annotated-review`

## Strict Zotero rule

Zotero is off by default for reviews.

- "Review this paper" -> do not use this skill automatically
- "Review and add to Zotero" -> still do not use this skill unless annotation is also requested

Do not touch Zotero unless the user explicitly asks.

## Document lookup order for review tasks

If a review task requires locating the paper or book itself and the user did not
already provide the source file/path, use this lookup order:

1. check the Zotero library
2. if not found there, check the Calibre library
3. only if neither library has the document, look online

For review tasks, do not go to online retrieval before checking both local
libraries.

## Base path

- `~/.codex/runtime/workspace/skills/annotated-review/`

Use the Codex runtime runner rather than invoking `run_review.sh` directly. The runner sets
the same workspace environment Claude uses.

Shared runner:

- `bash ~/.codex/runtime/run_skill.sh`

## Workflow imported from the bot

1. Review / annotate the paper.
2. Run an independent verification pass.
3. Run a trust-verification / citation-check pass.
4. Execute the review script against the paper or source tree.
5. If explicitly requested, store the resulting note in Zotero.

## Codex adaptation

- Use `spawn_agent` for the independent verifier and trust-verifier when the task is large enough to benefit.
- Keep the main synthesis local.
- Use `functions.exec_command` for the live review scripts.
- If the paper/book is not already attached or given by path, route document lookup as:
  `zotero` -> `calibre` -> online fallback.
- Do not use this skill for review-only requests; reserve it for requests that
  explicitly mention both annotate/annotation and review.

## Execution patterns

```bash
bash ~/.codex/runtime/run_skill.sh skills/annotated-review/run_review.sh --precompile --source <path>
```

```bash
bash ~/.codex/runtime/run_skill.sh skills/annotated-review/run_review.sh --review-file /tmp/review.json --pdf <file>
```

```bash
bash ~/.codex/runtime/run_skill.sh skills/annotated-review/run_review.sh --review-file /tmp/review.json --source <dir> --zotero-key <key>
```

## Output rule

Companion review artifacts are still useful even if LaTeX compilation fails. Report the best available artifact and any compile error explicitly.
