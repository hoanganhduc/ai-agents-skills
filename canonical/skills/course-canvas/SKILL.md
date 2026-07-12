---
name: course-canvas
description: "Route Canvas LMS course operations through the course_hoanganhduc canvas agent: preflight, list assignments/members, search users, and roster sync. Refuses unenroll, grade, invite, announce, messages, pages, and bulk download."
user-invocable: true
disable-model-invocation: false
metadata: {"requires":{"bins":["python3"]}}
---

Use this skill when the user asks about Canvas LMS for their course: listing assignments or members, searching users, syncing the Canvas roster into the local student database, or checking whether Canvas config is present.

## Core rules

- Always use the agent entrypoint (sets agent mode):

```bash
python3 -m course_hoanganhduc.canvas_agent <command> [options]
```

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
python3 -m course_hoanganhduc.canvas_agent preflight
python3 -m course_hoanganhduc.canvas_agent list-assignments [--course-id ID] [--category NAME]
python3 -m course_hoanganhduc.canvas_agent list-members [--course-id ID]
python3 -m course_hoanganhduc.canvas_agent search-user "name or email" [--course-id ID]
python3 -m course_hoanganhduc.canvas_agent sync [--course-id ID] [--db students.db]
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
- Do not hardcode user-specific paths.
