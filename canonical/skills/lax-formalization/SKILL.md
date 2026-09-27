---
name: lax-formalization
description: Formalize mathematical results as Lax submissions, find reusable declarations, and independently verify selected Lax dependencies. Use for explicit paper-formalization work, not ordinary paper lookup or review.
---

# Lax formalization

Use the official Lax layout and validator while keeping independent verification
separate from archive registration. A registered result is a candidate, not a
proof certificate. Reading a paper alone does not authorize formalization.

For a full paper job, selected reuse from an existing Lean repository, or an
artifact update, use the `lax-paper-workflow` runbook. It produces a **new**
publication repository from read-only research source, with explicit file
selection and privacy/correspondence gates. See
[workflow helpers](references/workflow.md) for the executable interfaces.
Read that reference's paper-presentation section when the user wants a published
PDF reproduced or the interactive paper-to-formalization view.

1. Agree the claim scope, definitions and relevant transitive dependencies.
   Locate the source paper through the usual library-first workflow when needed.
2. Search the **pinned mathlib** first, then the Lax catalog. Check statement
   meaning, assumptions and environment compatibility; text search alone is not
   a proof or a complete absence check.
3. Freeze reviewed concepts before proof work. Use `lax init` to scaffold a
   local submission. The agent guide and specification bundled with the selected
   CLI are authoritative for its format; do not copy a stale private-library
   template or run `lake update` in a Lax package.
4. Independently rebuild/replay relevant Lax dependencies from pinned sources in
   the controlled executor. Missing checks remain missing evidence. See
   [verification](references/verification.md) for requests and status meanings.
5. Build proofs, check the whole target inventory, close the proof-obligation
   graph, and review correspondence with the original claims. Legitimate concept
   axioms must not conceal open assumptions or a circular proof.
6. Prepare a local artifact. `zenodo-artifact` provides an optional secondary
   archive; it does not certify mathematics. No private-library staging/intake
   is required.

## Runtime

```bash
bash "${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh" \
  skills/lax-formalization/run_lax_formalization.sh doctor
```

Commands: `doctor`, `search --query Q --database D --environment E`,
`verify --request R --out O`, `verify-dependency --request R --out O`, and
`publication-plan --request R --out O`. Output is JSON. Search/doctor are offline;
verification executes only through a previously provisioned executor. No command
logs in, submits, registers, publishes or provisions an executor implicitly.

Read [executor setup](references/executor.md) when Docker/tooling is not ready.
On native Windows, the PowerShell wrapper supports offline commands; live
verification uses a qualified Linux/WSL executor. Do not infer native support
from a fake-root installation test.

## Authoring and publication boundaries

Keep CI scripts and citation metadata outside `concepts/` and `proofs/`.
Use the `lax-paper-artifact` template for a shared source/CI/archive layout.
Keep generated `.lake`, manifests and build outputs out of submitted Git source.
Pin the exact environment; an upgrade is a reviewed port, not an automatic
mathlib bump in a registered artifact.

Publication is a separate authorized operation. The helper prepares a plan only.
The first Lax submission may add an issue binding: reverify its committed source
before a content submission. Permission for a draft does not imply registration.
See [publication](references/publication.md) before that later operation.

For a bounded independent evaluation, read
[benchmark discipline](references/benchmark.md). Do not pass a reference proof
to the solver or call the entire reference repository verified after checking a
small subset.

Offline PowerShell entrypoint (live Lean verification requires Linux/WSL):

```powershell
$runtime = if ($env:AAS_RUNTIME_ROOT) { $env:AAS_RUNTIME_ROOT } else { "$env:LOCALAPPDATA\ai-agents-skills\runtime" }
& "$runtime\run_skill.ps1" "skills/lax-formalization/run_lax_formalization.ps1" doctor
```
