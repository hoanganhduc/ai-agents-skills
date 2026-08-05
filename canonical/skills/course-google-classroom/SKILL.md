---
name: course-google-classroom
description: "Route Google Classroom operations through the course_hoanganhduc gclass agent: preflight, list courses/students, and roster sync. Refuses unenroll, grade, and submission download."
user-invocable: true
disable-model-invocation: false
---

Use this skill when the user asks about Google Classroom: listing courses or students, syncing the Classroom roster into the local student database, or checking credential/token file presence.

## Core rules

- Always use the agent entrypoint through the dedicated course environment:

```bash
if [ "${HOME:-}" = /workspace ] && [ "${OPENCLAW_WORKSPACE:-}" = /workspace ]; then
  course_python=/opt/coding-system/python-closure/course-management/bin/python
else
  course_python="$HOME/.course_venv/bin/python"
fi
if [ "${HOME:-}" = /workspace ] && [ "${OPENCLAW_WORKSPACE:-}" = /workspace ]; then
  classroom_credentials=/workspace/.config/course/google-classroom/credentials.json
  classroom_token=/workspace/.config/course/google-classroom/token.pickle
else
  classroom_credentials="$HOME/.config/course/google-classroom/credentials.json"
  classroom_token="$HOME/.config/course/google-classroom/token.pickle"
fi
if [ ! -x "$course_python" ]; then
  printf '%s\n' 'TECHNICAL_FAIL: dedicated course interpreter is missing' >&2
  exit 1
fi
export GOOGLE_CLASSROOM_CREDENTIALS="${GOOGLE_CLASSROOM_CREDENTIALS:-$classroom_credentials}"
export GOOGLE_CLASSROOM_TOKEN="${GOOGLE_CLASSROOM_TOKEN:-$classroom_token}"
"$course_python" -m course_hoanganhduc.gclass_agent <command> [options]
```

- On native Windows, use the same host-owned defaults before invoking the
  dedicated interpreter:

```powershell
if (-not $env:GOOGLE_CLASSROOM_CREDENTIALS) {
  $env:GOOGLE_CLASSROOM_CREDENTIALS = "$env:USERPROFILE\.config\course\google-classroom\credentials.json"
}
if (-not $env:GOOGLE_CLASSROOM_TOKEN) {
  $env:GOOGLE_CLASSROOM_TOKEN = "$env:USERPROFILE\.config\course\google-classroom\token.pickle"
}
& "$env:USERPROFILE\.course_venv\Scripts\python.exe" -m course_hoanganhduc.gclass_agent <command> [options]
```

- Do **not** run `course --unenroll-google-classroom`, `--grade-google-classroom`, or `--download-google-classroom-submissions` from this skill.
- When a course id is required in agent mode, set:

```bash
export GCLASS_COURSE_ALLOWLIST=<course-id>[,other-ids]
```

- Never print OAuth client secrets or token pickle contents. Preflight only checks path existence.
- If `course_hoanganhduc` is not importable, report the missing package.

## Common commands

```bash
"$course_python" -m course_hoanganhduc.gclass_agent preflight
"$course_python" -m course_hoanganhduc.gclass_agent list-courses [--credentials PATH] [--token PATH]
"$course_python" -m course_hoanganhduc.gclass_agent list-students --course-id ID
"$course_python" -m course_hoanganhduc.gclass_agent sync --course-id ID [--db students.db]
```

Refused: `unenroll`, `grade`, `download`.

## Natural-language routing

- "list my Google Classroom courses" → `list-courses`
- "list students in course X" → `list-students --course-id X`
- "sync Google Classroom roster" → `sync --course-id X`
- "grade / unenroll / download GC submissions" → refuse; human interactive `course` CLI only

## Target notes

- Host agents default to `~/.config/course/google-classroom/credentials.json`
  and `~/.config/course/google-classroom/token.pickle` unless the corresponding
  environment variables are already set.
- OpenClaw's locked sandbox uses the image-local course environment under
  `/opt/coding-system/python-closure/course-management` and defaults to the
  restored `/workspace/.config/course/google-classroom/{credentials.json,token.pickle}`
  projection.
- Do not hardcode user-specific absolute paths into the skill body.
