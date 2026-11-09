# Delivery Checklist

Mark the answer `READY` only if all of these are true:

- scope matched the original request
- major claims have evidence or are clearly labeled inference
- present structured artifacts such as `sources.jsonl`, `claims.jsonl`,
  `guards.jsonl`, `delivery.json`, source ledgers, or evidence maps were checked
- time-sensitive statements include dates where needed
- requested format/style context was checked for blog, article, report, or other
  format-matched writing
- exclusions and unresolved gaps are visible
- no hidden dependency on unchecked material scope remains
- load-bearing sources were read whole, or the partial read is disclosed —
  check tool payloads for `complete: false`, capped output, and partial
  retrievals rather than assuming a result is the full document
- residual uncertainty is listed for every unfinished or disputed load-bearing
  claim (do not erase disagreements with unanimous soft prose)
- multi-LLM LGTM / same-family agreement alone is **not** treated as banked
  support; require different-family re-derivation and/or a machine-checkable
  artifact for banking language
- review rounds that only reword without new evidence do not count as progress
  toward READY; escalate unfinished rather than “approve until green”
- checks that ended without a verdict (timeout, output cap, missing tool,
  resource veto) are listed under `Checks not run` and never counted as
  refutation or support
- feedback (a bare scan, a typecheck without trust-base evidence, `exact?`,
  LeanExplore, AXLE, OpenGauss) is not acceptance; formal support needs a
  promotion that deep-research `validate` accepts
- when a structured run exists, `delivery-check` output is quoted verbatim and
  its `NOT READY` is never upgraded

Compact output shape:

```text
Delivery Check
- Status: READY | NOT READY
- Confirmed: ...
- Gaps: ...
- Checks not run: ...
- Residual uncertainty: ...
- Next step: ...
```
