---
name: course-canvas
description: "Route Canvas LMS course operations through the course_hoanganhduc canvas agent: preflight, list assignments/members, search users, and roster sync. Refuses unenroll, grade, invite, announce, messages, pages, and bulk download."
user-invocable: true
disable-model-invocation: false
---

Use this skill when the user asks about Canvas LMS for their course: listing assignments or members, searching users, syncing the Canvas roster into the local student database, or checking whether Canvas config is present.

## Core rules

- Always use the agent entrypoint (sets agent mode) through the dedicated
  course environment:

```bash
if [ "${HOME:-}" = /workspace ] && [ "${OPENCLAW_WORKSPACE:-}" = /workspace ]; then
  course_python=/opt/coding-system/python-closure/course-management/bin/python
else
  course_python="$HOME/.course_venv/bin/python"
fi
if [ ! -x "$course_python" ]; then
  printf '%s\n' 'TECHNICAL_FAIL: dedicated course interpreter is missing' >&2
  exit 1
fi
"$course_python" -m course_hoanganhduc.canvas_agent <command> [options]
```

- On native Windows, require `& "$env:USERPROFILE\.course_venv\Scripts\python.exe" -m course_hoanganhduc.canvas_agent <command> [options]` in PowerShell.

- Do **not** call unconstrained `course --unenroll-canvas`, `--grade-canvas-assignment`, invites, announcements, page edits, or bulk downloads from this skill.
- In agent mode, if a course id is used, set:

```bash
export CANVAS_COURSE_ALLOWLIST=<course-id>[,other-ids]
```

Empty allowlist fails closed when a course id is required.
- Never print Canvas API keys or tokens. Preflight only reports whether settings are set.
- If `course_hoanganhduc` is not importable, report the missing package instead of claiming Canvas access.

## Common commands

```bash
"$course_python" -m course_hoanganhduc.canvas_agent preflight
"$course_python" -m course_hoanganhduc.canvas_agent list-assignments [--course-id ID] [--category NAME]
"$course_python" -m course_hoanganhduc.canvas_agent list-members [--course-id ID]
"$course_python" -m course_hoanganhduc.canvas_agent search-user "name or email" [--course-id ID]
"$course_python" -m course_hoanganhduc.canvas_agent sync [--course-id ID] [--db students.db]
```

Refused by design: `unenroll`, `grade`, `invite`, `announce`, `download`, `messages`, `pages`.

## Natural-language routing

- "is Canvas configured?" → `preflight`
- "list Canvas assignments" → `list-assignments`
- "who is in the Canvas course?" → `list-members`
- "sync Canvas roster" → `sync`
- "unenroll / grade on Canvas" → refuse; tell the user to use the interactive `course` CLI as a human

## Target notes

- Canvas URL/token/course defaults come from the toolkit config/settings, not this skill body.
- OpenClaw's locked sandbox uses the image-local course environment under
  `/opt/coding-system/python-closure/course-management`; its restoring system
  owns any private course-config projection into the workspace.
- Do not hardcode user-specific paths.
