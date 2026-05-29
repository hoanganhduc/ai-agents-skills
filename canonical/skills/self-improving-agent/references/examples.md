# Compact examples

## Learning with canonical integration plan

```markdown
## [LRN-20260410-001] routing

**Logged**: 2026-04-10T15:00:00Z
**Priority**: high
**Status**: pending

### Summary
Review-only paper requests should not trigger annotated review.

### Details
The correct split is review-only -> paper-review, annotate+review -> annotated-review.

### Suggested Action
Update the affected skill-routing docs and add a focused regression check.

### Canonical Integration Plan
- Related Skills: paper-review, annotated-review, zotero, calibre
- Related Settings Or Artifacts: canonical skill docs, generated docs, tests
- Affected Install Targets: codex, claude, deepseek; copilot skill adapter only; openclaw blocked unless current install-target gates allow it
- Affected OS/Substrates: not_applicable for routing; run installer shape checks on linux/windows/all
- Canonical Repo Change: update `canonical/skills/paper-review/SKILL.md`, `canonical/skills/annotated-review/SKILL.md`, and relevant tests
- Docs And Generated Outputs: update `installer/ai_agents_skills/docs.py` only if public docs change, then run `make docs`
- Verification Plan: focused tests plus `make fake-root-lifecycle ARGS="--skill paper-review --platform-shape all"`
- Blocked Or Unsupported Targets: do not claim OpenClaw real-system behavior without native target evidence

### Metadata
- Source: user_feedback
- Related Files: canonical/skills/paper-review/SKILL.md
- Tags: routing, review
```

## Error with OS-specific limits

```markdown
## [ERR-20260410-001] windows-helper

**Logged**: 2026-04-10T15:05:00Z
**Priority**: high
**Status**: pending

### Summary
A helper command documented with a POSIX `bash` path failed for a native Windows target.

### Error
    'bash' is not recognized as an internal or external command

### Context
- Command or operation attempted: run a skill helper from native CMD
- Relevant inputs or parameters: Windows target, reference install mode

### Suggested Fix
Move the helper behind the managed runtime with `.sh`, `.ps1`, and `.bat` wrappers, or document that the helper is POSIX-only.

### Canonical Integration Plan
- Related Skills: self-improving-agent
- Related Settings Or Artifacts: manifest/runtime.yaml, runtime smoke, docs
- Affected Install Targets: codex, claude, deepseek, copilot skill adapter; openclaw runtime-backed skills currently blocked
- Affected OS/Substrates: linux, macos, windows, wsl, git-bash-msys
- Canonical Repo Change: add portable runtime helper files under `canonical/runtime/skills/self-improving-agent/`
- Docs And Generated Outputs: update generated installation/verification docs and run `make docs`
- Verification Plan: `make runtime-smoke ARGS="--skills self-improving-agent"` plus native Windows `make.bat` checks before claiming native execution
- Blocked Or Unsupported Targets: mounted Windows profile checks do not prove native Windows execution

### Metadata
- Reproducible: yes
- Related Files: canonical/skills/self-improving-agent/SKILL.md
```
