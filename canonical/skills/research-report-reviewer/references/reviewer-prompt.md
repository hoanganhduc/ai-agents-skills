# Reviewer Checklist

Review the draft against this list:

- Were the declared scope, exclusions, and requested format inspected?
- If present, do `sources.jsonl`, `claims.jsonl`, `guards.jsonl`,
  `delivery.json`, source ledgers, or evidence maps support the draft?
- Are the main claims still tied to evidence?
- Are time-sensitive facts dated clearly?
- Does the draft answer the stated question without wandering?
- Are key uncertainties or exclusions named?
- Is any recommendation stronger than the cited evidence allows?
- If replacement prose is proposed, does it satisfy the Writing Recommendation
  Contract in `writing-review.md`, identify affected claim/evidence/style refs,
  and remain advisory?
- **Undisclosed truncation (BLOCK if load-bearing):** does a claim rest on a
  source that was read only in part — a payload reporting `complete: false`,
  capped tool or subprocess output, a partial retrieval — without the draft
  saying so? Treat the claim as unsupported until the full source is read or
  the partial read is disclosed.
- For blog/article/report drafting, were prior posts, templates, style guides,
  or supplied examples inspected before writing?
- **Formal overclaim (BLOCK if present):** draft says proved/machine-checked/
  formalized claim C while citing only `opengauss_run`/job success, active
  `sorry`/`admit`, missing lead/human equivalence, or no local `formal_check`.
- **Formal FLAG:** formal work incomplete but language is hedged as partial.
- **No verdict read as a verdict (BLOCK):** does the draft report a timeout, an
  output cap, a missing tool, `command_failed` or a resource veto as a
  refutation or as support?
- **Feedback presented as acceptance (BLOCK):** does the draft present a bare
  scan, a typecheck without trust-base evidence, `exact?` output, or
  LeanExplore, AXLE or OpenGauss results as an accepted formal result?
- **Restated goal (FLAG):** does a lemma restate the goal, or does a step only
  pad the argument, while the draft presents it as progress?

Compact output shape:

```text
Review Findings
- Verdict: BLOCK | FLAG | PASS
- Findings:
  - ...
- Repairs:
  - ...
```

Use `BLOCK` when the draft should not be delivered yet, `FLAG` when it is usable with caveats, and `PASS` when no material issue remains.
