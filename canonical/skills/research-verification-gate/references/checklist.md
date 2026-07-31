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

Compact output shape:

```text
Delivery Check
- Status: READY | NOT READY
- Confirmed: ...
- Gaps: ...
- Next step: ...
```
