---
name: zenodo-artifact
description: Prepare and validate an explicit source-and-evidence bundle and metadata for optional Zenodo archival. Use for research software artifacts; this runtime does not upload, reserve a DOI, or publish.
---

# Zenodo artifact

Prepare Zenodo as a secondary archive without requiring a personal Lean library.
A DOI identifies an archived object; it does not verify its mathematical claims.

1. Select a clean source revision and its exact verification report. Preserve
   source, dependency and tool identities; label pending semantic review.
2. Prepare JSON software metadata with title, version, description, license and
   named creators. Keep original-paper credit distinct from formalizer credit.
3. Run `prepare` to an explicitly selected new directory outside the source tree,
   then `validate`. Review the actual archived file set and checksums.
4. Produce a publication plan. Stop there until a later publication request.

```bash
bash "${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh" \
  skills/zenodo-artifact/run_zenodo_artifact.sh doctor
```

Runtime verbs: `prepare --project P --evidence E --metadata M --out O`,
`validate --dir O`, `restore --bundle O --out NEW`,
`publication-plan --metadata M`, and offline `doctor`.
They do not require or read service credentials. Upload, draft creation, login
and publication are not implemented operations in this milestone.

The bundle defaults to source plus evidence, not an offline toolchain/dependency
distribution. Review `REPRODUCE.md`; an exported source ZIP lacks Git history.
Restore a safe isolated Git context when needed, preserving the distinction
between original and synthetic commit identities.

Read [metadata and later delivery](references/delivery.md) for CFF, GitHub
integration, and the documented API sequence. Use the JSON-compatible YAML CFF template for offline scalar/credit consistency
checks. Full CFF-schema validation remains a separate offline check. A matching
checksum proves byte consistency, not certificate authenticity: supply reports
produced by the trusted supervisor, never a candidate-authored receipt.

Offline PowerShell entrypoint (live Lean verification requires Linux/WSL):

```powershell
$runtime = if ($env:AAS_RUNTIME_ROOT) { $env:AAS_RUNTIME_ROOT } else { "$env:LOCALAPPDATA\ai-agents-skills\runtime" }
& "$runtime\run_skill.ps1" "skills/zenodo-artifact/run_zenodo_artifact.ps1" doctor
```
