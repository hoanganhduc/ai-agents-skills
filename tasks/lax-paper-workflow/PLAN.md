# Implementation plan

| Phase | Work | Verification |
|---|---|---|
| P0 | Job/data contracts and spec | Boundaries/acceptance explicit |
| P1 | Inventory, selection, new-tree export | Seen-to-fail regressions; no source writes/history |
| P2 | Bounded TeX sanitation | Comments/percent/verbatim/markers/unknown syntax tests |
| P3 | Reuse mapping, scaffold, paper metadata | Selected proof retained; unrelated broken module excluded |
| P4 | Trusted preparation and execution routing | Existing isolated executor; no candidate network setup |
| P5 | Readiness, evidence and version bindings | Wrong paper/source/scope/reviewer/dependencies refused |
| P6 | CI and optional archive integration | Raw failure evidence never uploaded automatically |
| P7 | Manifests, runbook, docs, installer | Partial install, docs/static/sanitizer checks |
| P8 | Independent forward test + real reference subset | New repo, replay, comparison, bundle restore |

Implementation stays single-path. Independent agents are used only for the
approved fresh review, blind solver and forward-test responsibilities. Record
each check and unresolved limit in PROGRESS.md; do not turn skips into passes.

Controller-private artifacts live under the ignored `.codex/runs/` tree, never
in a generated public repository. Runnable fixture sources can be checked in
only if synthetic or appropriately attributed public inputs.
