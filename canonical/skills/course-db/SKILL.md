---
name: course-db
description: "Route local course student-database operations through the course_hoanganhduc db agent: search, details, domain/duplicate/missing-id lists, roster and email export. Refuses interactive modify, restore, and destructive import apply."
user-invocable: true
disable-model-invocation: false
---

Use this skill when the user asks about the local course student database / roster file: search students, show details, list by email domain, find duplicate names or missing IDs, export roster CSV or emails, or count students.

## Core rules

- Always use the agent entrypoint through the dedicated course environment:

```bash
course_python="$HOME/.course_venv/bin/python"
if [ ! -x "$course_python" ]; then
  printf '%s\n' 'TECHNICAL_FAIL: dedicated course interpreter is missing' >&2
  exit 1
fi
"$course_python" -m course_hoanganhduc.db_agent <command> [options]
```

- On native Windows, require `& "$env:USERPROFILE\.course_venv\Scripts\python.exe" -m course_hoanganhduc.db_agent <command> [options]` in PowerShell.

- Read/search/export only. Do **not** run interactive modify, DB restore, or bulk import apply from this skill.
- Default DB path is `students.db` in the working directory; pass `--db` when needed.
- If `course_hoanganhduc` is not importable, report the missing package.

## Common commands

```bash
"$course_python" -m course_hoanganhduc.db_agent count [--db students.db]
"$course_python" -m course_hoanganhduc.db_agent search "keyword" [--db students.db]
"$course_python" -m course_hoanganhduc.db_agent details "name|id|email" [--db students.db]
"$course_python" -m course_hoanganhduc.db_agent list-email-domain gmail.com
"$course_python" -m course_hoanganhduc.db_agent list-duplicate-names
"$course_python" -m course_hoanganhduc.db_agent list-missing-ids [--which all|google|canvas|student]
"$course_python" -m course_hoanganhduc.db_agent export-roster [--out classroom_roster.csv]
"$course_python" -m course_hoanganhduc.db_agent export-emails [--out emails.txt]
```

Refused: `modify`, `restore-db`, `import-apply`, `delete`.

## Natural-language routing

- "how many students?" → `count`
- "find student X" → `search` / `details`
- "who has gmail?" → `list-email-domain`
- "export roster" → `export-roster`
- "edit the database interactively" → refuse; human `course --modify` only

## Target notes

- Works offline on the local student store (pickle/SQLite as used by the toolkit).
- Do not hardcode user-specific paths.
