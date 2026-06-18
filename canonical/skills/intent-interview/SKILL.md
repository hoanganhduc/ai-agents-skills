---
name: intent-interview
description: Use when an ask is underspecified, or you catch yourself inferring what the user wants, before any brief, spec, plan, or code. Elicits the real intent one question at a time, each with your best guess attached, until you can predict the user's answers, then restates a confirmed intent for explicit sign-off.
metadata:
  short-description: Elicit and confirm real intent before work begins
---

# Intent Interview

Use this as an interactive gate before `research-briefing`, the engineering Spec
step, or any expensive work, when what the user wants is not yet clear. It closes
the gap between what was asked and what is actually wanted while changing course is
still free.

## When to use

- an ask is underspecified ("build me X", "research Y") with no who / why / success
- you notice yourself silently filling in ambiguous requirements
- the user invokes it ("interview me", "are we sure?", "stress-test my thinking")
- building the wrong thing would cost more than asking

## When not to use

- the request is already specific, or the user gave a detailed plan that still holds
- trivial tasks answerable directly
- non-interactive runs with no user to answer — instead state assumptions and proceed

## Method

1. **Hypothesize.** State your one-sentence best read of what the user wants, with a
   confidence (0-100%). Below ~70%, name the missing field: who it is for, why now,
   what success looks like, or the binding constraint.
2. **Interview, one question at a time.** Ask exactly one focused question per turn,
   each with your current guess attached, then wait:
   - `Q:` one question
   - `GUESS:` your hypothesis and the reasoning behind it

   Asking several questions at once defeats the purpose — the user should react to a
   guess, not generate from scratch.
3. **Probe want vs. should-want.** When an answer sounds like convention or
   buzzwords, ask once: "If you didn't have to justify this to anyone, what would you
   actually want?"
4. **Restate.** Once you can predict the user's next few answers, write a six-line
   restate in their words: Outcome / User / Why now / Success / Constraint /
   Out-of-scope. The Out-of-scope line is mandatory.
5. **Confirm.** Gate on an explicit yes. "Sounds good", "whatever you think", and
   silence are not yes.

## Output contract

Produce a short visible section titled `Intent Check`:

- `Hypothesis` and `Confidence`
- `Confirmed intent` — the six-line restate
- `Status` — CONFIRMED | NEEDS-INPUT | BLOCKED-NON-INTERACTIVE

Hand the confirmed intent to `research-briefing` (research) or the engineering Spec
step. See `references/intent-interview-template.md` for the exact shapes.

## Guardrails

- one question per turn, and always attach your guess
- never treat a non-answer as approval
- stop interviewing once you can predict the answers — do not over-question
- if no user can answer, state assumptions explicitly and proceed under them

## Recommended templates

When this skill is involved, consider these workflow templates (install via
the `workflow-templates` artifact profile, or `--with-deps` to pull backing skills):

- `reversible-decision-memo` -- Evidence-grounded decision record with named alternatives, source-cited rationale, reversibility class and trip-wires, and a fresh-context adversarial confirmation before the decision stands.
