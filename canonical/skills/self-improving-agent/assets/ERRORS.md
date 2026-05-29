# ERRORS.md template

```markdown
## [ERR-YYYYMMDD-XXX] command_or_skill

**Logged**: ISO-8601 timestamp
**Priority**: high
**Status**: pending

### Summary
Brief description of the failure.

### Error
    actual error output

### Context
- Command or operation attempted
- Relevant inputs or parameters

### Suggested Fix
Probable resolution or next debugging step.

### Canonical Integration Plan
- Related Skills: skill-name | none | unknown
- Related Settings Or Artifacts: manifest/profile/runtime/docs/tests | none
- Affected Install Targets: codex | claude | deepseek | copilot | openclaw | not_applicable
- Affected OS/Substrates: linux | macos | windows | wsl | git-bash-msys | mounted-windows | not_applicable
- Canonical Repo Change: canonical/..., manifest/..., installer/..., docs/..., tests/...
- Docs And Generated Outputs: update generator/manual docs and run `make docs` | not needed
- Verification Plan: focused tests, runtime smoke, lifecycle, native OS checks
- Blocked Or Unsupported Targets: explicit limits and remaining unverified areas

### Metadata
- Reproducible: yes | no | unknown
- Related Files: path/to/file

---
```
