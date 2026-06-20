---
name: research-briefing
description: Use when starting a nontrivial research task to frame scope, success criteria, evidence plan, and the right downstream workflow before expensive browsing or multi-agent work begins.
metadata:
  short-description: Brief a research task before execution
---

# Research Briefing

Use this as a lightweight gate before nontrivial research.

## When to use

- the task needs deeper research, not a quick factual answer
- the user asks for a report, survey, comparison, or careful investigation
- the task may branch into `source-research`, `deep-research-workflow`, `prose`, or `agent-group-discuss`
- you want to surface scope, assumptions, and evidence needs before spending time

## When not to use

- trivial lookups that can be answered directly
- after the user already approved a detailed research plan and nothing material changed

## Output contract

Produce a short visible section titled `Research Brief`.

Keep it brief and include:

- `Goal` — what question the work must answer
- `Scope` — what is in and out
- `Constraints` — time, tools, source class, or formatting limits
- `Context/style artifacts` — prior posts, templates, house style, examples,
  source ledgers, or supplied materials to inspect before drafting or matching a
  publication format
- `Style profile` — whether `writing-style-settings.md` and any overlay such as
  `math-manuscript-style.md` will be active for downstream writing, plus the
  expected `style_profile_ref`, `active_overlays`, and `active_requirement_ids`
- `Evidence plan` — primary source types and verification expectations
- `Workflow` — which downstream research skill or path to use
- `Risks` — likely ambiguity, missing evidence, or live-data concerns

Use the compact template in `references/brief-template.md` when helpful.

## Guardrails

- keep the brief short enough to read in a few seconds
- state assumptions explicitly instead of hiding them in later research
- for writing or publication-format tasks, inspect old posts, templates, house
  style, and supplied examples before drafting; if they are absent, say so and
  state the style assumption before writing
- for downstream writing, require the selected workflow to load
  `writing-style-settings.md` and record `style_profile_ref`,
  `active_overlays`, `active_requirement_ids`, and `style_applied` before final
  output
- if the task is simple, say so and skip heavyweight planning
- if the user already provided a plan, validate and tighten it rather than replacing it
