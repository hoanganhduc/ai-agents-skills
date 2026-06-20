# Plan

## Phases

A. Policy and runtime engine (repo-only).
   1. Refresh lifecycle artifacts for loop-enforcement.
   2. Add the canonical stop-policy instruction rule and register it in manifests.
   3. Add runtime arm/disarm/active/done/hook-check, schema, liveness, and
      spend/wall enforcement, with a fail-open hook path.
   4. Subordinate shipped plateau/blocker/evidence-gap stops under the policy.
B. Installer surface (the validated blocker).
   5. Build a `settings-json-merge` surface that upserts one tagged managed Stop
      hook and round-trips on uninstall.
   6. Add hook-capable target settings/hook artifact dirs, a manifest JSON-hook
      artifact kind, and renderer support.
C. Hook, driver, docs.
   7. Add the fail-open Stop-hook template.
   8. Add the generic driver and `.sh/.ps1/.bat` launchers with the
      `AUTOLOOP_DRIVER` exemption and least-privilege headless flags.
   9. Update docs and all target READMEs with the honest matrix; rebuild Sphinx.
  10. Run full tests, then installer `plan`/`audit-system`; show the `plan` diff
      before any `apply`.

## Dependencies

- Existing runtime iteration/budget/proof-artifact machinery and terminal-status
  guard.
- Existing manifest artifact system and generated-doc pipeline.
- Existing installer fake-root and render tests.
- The Markdown managed-block primitive (reused for the instruction rule only;
  not usable for JSON settings).

## Risks

- Risk: a buggy enforcer traps a session.
  - Mitigation: fail-open on every error plus three independent kill switches.
- Risk: the JSON-merge surface corrupts a populated user `settings.json`.
  - Mitigation: parse-or-refuse, back up before write, tag the managed entry,
    and require a round-trip uninstall test before shipping.
- Risk: over-claiming uniform enforcement across targets.
  - Mitigation: an honest per-target support matrix in docs and READMEs.
- Risk: shipped plateau stops contradict the policy.
  - Mitigation: subordinate them and add a negative test.

## Verification Checkpoints

- After the runtime engine: enforcement unit tests green.
- After the installer surface: round-trip apply/uninstall on a populated fake
  `settings.json`; manifest/render tests green.
- After docs: `make docs-check`.
- Final repo-only checks: full test suite, then `plan`/`audit-system` dry run.
