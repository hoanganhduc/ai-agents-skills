# Compact examples

## Learning

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
Update AGENTS.md and the affected skill docs.
```

## Error

```markdown
## [ERR-20260410-001] ssh

**Logged**: 2026-04-10T15:05:00Z
**Priority**: medium
**Status**: pending

### Summary
Remote inspection failed due to DNS resolution inside the sandbox.

### Suggested Fix
Retry with approved escalated SSH.
```
