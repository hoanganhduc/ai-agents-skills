# OpenGauss evidence policy

## Types

| Type | Meaning | May promote claim support? |
|------|---------|----------------------------|
| `opengauss_run` | Harness/job provenance (run id, workflow, pins, harvest paths) | **No** |
| Local `formal_check` | Output of `lean-strict-verification-gate` (scan / typecheck / inventory) | Contributes to formal statement status only |
| Claim support | deep-research `CLAIM_SUPPORT_STATUSES` | `supports_claim_after_equivalence_review` requires lead/human review |

## Required honesty

- Missing OpenGauss / Lean → `tool_unavailable` or intake `defer` — **not** a failed theorem.
- Typecheck with `sorry`/`admit` → incomplete formalization, not proved.
- Typecheck without lead equivalence → formal statement may typecheck; informal claim C is not supported.
- Source-scan trust base ≠ transitive `#print axioms` inventory.

## Forbidden phrasing

- “OpenGauss proved …”
- “Machine-checked theorem C” without claim-support evidence
- Citing only job success / swarm completion as proof

## Review

- Producer (Gauss launcher/backend family) never confirms claim support.
- Confirmer must not treat Gauss transcripts as authority; prefer clean artifact + informal claim.
