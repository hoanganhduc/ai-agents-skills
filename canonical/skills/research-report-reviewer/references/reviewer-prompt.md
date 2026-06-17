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
- For blog/article/report drafting, were prior posts, templates, style guides,
  or supplied examples inspected before writing?

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
