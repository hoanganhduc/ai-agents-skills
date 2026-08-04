# Course Management Skills

Agent skills that route teaching workflows through the
[`course_management_toolkit`](https://github.com/hoanganhduc/course_management_toolkit)
Python package (`course_hoanganhduc`). They are installed together via the
`course-management` profile, or individually with `--skill`.

These skills are **agent entrypoints** with restricted surfaces. Destructive
LMS operations (unenroll, grade-apply, invite, teardown, bulk download of
submissions) are refused on the agent path; use the interactive `course` CLI as
a human when those are required.

## Install

```bash
# from ai-agents-skills repo root
make plan ARGS="--profile course-management"
make install ARGS="--profile course-management"
```

Or select skills explicitly:

```bash
make plan ARGS="--skills classroom50,course-canvas,course-google-classroom,course-db"
```

**Package dependency:** the dedicated `~/.course_venv` environment must be able
to import `course_hoanganhduc`. On native Windows the matching path is
`%USERPROFILE%\.course_venv\Scripts\python.exe`. Classroom50 also needs GitHub
CLI with the Classroom50 **teacher** extension installed. Restoration systems
should install a pinned extension release; the manual upstream command is
`gh extension install foundation50/gh-teacher`.

## Skills and entrypoints

| Skill | Entrypoint | Typical use |
|-------|------------|-------------|
| `classroom50` | `~/.course_venv/bin/python -m course_hoanganhduc.c50_agent` | foundation50 Classroom50 roster list/sync/export |
| `course-canvas` | `~/.course_venv/bin/python -m course_hoanganhduc.canvas_agent` | Canvas list/sync/search (read-oriented) |
| `course-google-classroom` | `~/.course_venv/bin/python -m course_hoanganhduc.gclass_agent` | Google Classroom list/sync |
| `course-db` | `~/.course_venv/bin/python -m course_hoanganhduc.db_agent` | Local students.db search and export |

See each skill’s `SKILL.md` under `canonical/skills/<name>/` for full command
lists and natural-language routing.

### Example agent commands

```bash
course_python="$HOME/.course_venv/bin/python"
test -x "$course_python" || { printf '%s\n' 'dedicated course interpreter missing' >&2; exit 1; }

# Classroom50
"$course_python" -m course_hoanganhduc.c50_agent preflight
"$course_python" -m course_hoanganhduc.c50_agent list-classrooms --org ORG
"$course_python" -m course_hoanganhduc.c50_agent sync --org ORG --classroom SHORT --db students.db
"$course_python" -m course_hoanganhduc.c50_agent export --db students.db --out classroom50_roster.csv

# Canvas
"$course_python" -m course_hoanganhduc.canvas_agent preflight
"$course_python" -m course_hoanganhduc.canvas_agent list-members [--course-id ID]
"$course_python" -m course_hoanganhduc.canvas_agent sync [--course-id ID]

# Google Classroom
"$course_python" -m course_hoanganhduc.gclass_agent preflight
"$course_python" -m course_hoanganhduc.gclass_agent list-courses
"$course_python" -m course_hoanganhduc.gclass_agent sync --course-id ID

# Local DB
"$course_python" -m course_hoanganhduc.db_agent search "keyword"
"$course_python" -m course_hoanganhduc.db_agent export-roster --db students.db
```

Agent modules set `COURSE_AGENT_MODE=1` automatically. The Classroom50 skill
may invoke exactly `gh teacher --help` as a non-mutating readiness check; all
other raw `gh teacher` and every raw `gh student` invocation remain forbidden.

## Allowlists (agent mode)

Agent entrypoints force agent mode. When a course or org id is required:

| Variable | Used by |
|----------|---------|
| `CLASSROOM50_ORG_ALLOWLIST` | `classroom50` / `c50_agent` |
| `CANVAS_COURSE_ALLOWLIST` | `course-canvas` / `canvas_agent` |
| `GCLASS_COURSE_ALLOWLIST` | `course-google-classroom` / `gclass_agent` |

Empty allowlist with a required id fails closed.

## What is refused on the agent path

| Surface | Refused (use interactive `course` CLI as a human) |
|---------|---------------------------------------------------|
| Classroom50 | submission download |
| Canvas | unenroll, grade, invite, announce, messages, pages, bulk download |
| Google Classroom | unenroll, grade, submission download |
| Local DB | interactive modify, restore-db, import-apply, delete |

## Related docs

- [Skills catalog](skills.md)
- [Profiles](profiles.md)
- [Dependencies](dependencies.md) (`course-hoanganhduc-python-package`,
  `github-cli`, `classroom50-teacher-extension`)
- Upstream toolkit (human CLI + agent modules):
  <https://github.com/hoanganhduc/course_management_toolkit>
- Toolkit usage notes: README and `docs/usage.rst` in that repository
  (Classroom50 flags and agent entrypoints).
