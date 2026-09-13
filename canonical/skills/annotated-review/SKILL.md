---
name: annotated-review
description: Use only when the user explicitly mentions both annotation and review for a paper task. Produces annotated review outputs and supports an explicit add-to-Zotero step when the user asks for it.
metadata:
  short-description: Annotate+review paper workflow
---

# Annotated Review


## Windows Runtime Commands

On native Windows, use the managed Windows runner and the native runtime command target. Set `$runtime` to the installed runtime root. Multi-agent installs usually use `%LOCALAPPDATA%\ai-agents-skills\runtime`. Then run:

```powershell
$runtime = if ($env:AAS_RUNTIME_ROOT) { $env:AAS_RUNTIME_ROOT } else { "$env:LOCALAPPDATA\ai-agents-skills\runtime" }
& "$runtime\run_skill.ps1" "skills/annotated-review/review.py" <args>
```

POSIX examples below use `run_skill.sh` and `.sh` command targets; use the Windows command target above on native Windows.

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
- if the user asks for a multi-agent review, use `agent-group-discuss`
- do not auto-route review-only requests to `annotated-review`

If the request names Mathematical Reviews/MathSciNet or zbMATH, an assignment
from either service, or a copy supplied by either service, load
`mathscinet-zbmath-review-style.md` and complete its provenance gate before
library lookup, attachment opening, parsing, annotation, compilation, logging,
or delegation. A restricted or unresolved assignment must stop before content
access; annotation does not create an exception.

If `mathscinet-zbmath-review-style.md` is unavailable in the current install,
stop before content access, ref resolution, tool invocation, prompt assembly,
artifact persistence, or delegation. Do not reconstruct its rules from memory.

Even when a separately obtained public copy is authorized for content
processing, do not compile it merely because this skill is active. Compilation
requires separate user authorization and a containment review; the
bibliographic-review writing route never supplies that authority.

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

- `$AAS_RUNTIME_WORKSPACE/skills/annotated-review/`

Use the managed runtime runner rather than invoking `run_review.sh` directly. The runner sets
the same workspace environment Claude uses.

Shared runner:

- `bash "${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh"`

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
- Before producing review prose or a stored note, load
  `writing-style-settings.md` and record the active style profile. For
  mathematical, TCS, graph-theoretic, Lean, or LaTeX manuscripts, also load
  `math-manuscript-style.md`.

## Execution patterns

```bash
bash "${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh" skills/annotated-review/run_review.sh --precompile --source <path>
```

```bash
bash "${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh" skills/annotated-review/run_review.sh --review-file /tmp/review.json --pdf <file>
```

```bash
bash "${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh" skills/annotated-review/run_review.sh --review-file /tmp/review.json --source <dir> --zotero-key <key>
```

## Output rule

Companion review artifacts are still useful even if LaTeX compilation fails. Report the best available artifact and any compile error explicitly.
Final review or annotation artifacts should include `style_profile_ref`,
`policy_hash`, `active_overlays`, `active_requirement_ids`, and
`style_applied`; a bare
`style_applied: true` value is not enough unless it is backed by the workflow's
record of the loaded policy and selected requirements.

When an annotation body or review finding proposes replacement prose, follow
the Writing Recommendation Contract in `writing-review.md`. Tie literal text to
the affected frozen claim IDs, evidence refs, and active requirement IDs, and
flag changes to claims, caveats, citations, or support. The recommendation is
advisory; do not mutate the authoritative source during a review-only phase.

## Recommended templates

When this skill is involved, consider these workflow templates (install via
the `workflow-templates` artifact profile, or `--with-deps` to pull backing skills):

- `cross-agent-adversarial-review` -- Producer-never-confirmer adversarial review of a paper, proof, or code artifact across agent families with a fresh-agent confirmation gate.
- `writing-review` -- Independent review-to-revision handoff using existing claim ledgers, V1 packets, and parent-owned acceptance.
