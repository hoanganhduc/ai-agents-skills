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

**Package dependency:** the host Python environment used by the agent must be
able to import `course_hoanganhduc` (for example
`pip install -e /path/to/course_management_toolkit`). Classroom50 also needs
GitHub CLI with the Classroom50 **teacher** extension installed for live
`gh teacher` calls.

## Skills and entrypoints

| Skill | Entrypoint | Typical use |
|-------|------------|-------------|
| `classroom50` | `python3 -m course_hoanganhduc.c50_agent` | foundation50 Classroom50 roster list/sync/export |
| `course-canvas` | `python3 -m course_hoanganhduc.canvas_agent` | Canvas list/sync/search (read-oriented) |
| `course-google-classroom` | `python3 -m course_hoanganhduc.gclass_agent` | Google Classroom list/sync |
| `course-db` | `python3 -m course_hoanganhduc.db_agent` | Local students.db search and export |

See each skill’s `SKILL.md` under `canonical/skills/<name>/` for full command
lists and natural-language routing.

### Example agent commands

```bash
# Classroom50
python3 -m course_hoanganhduc.c50_agent preflight
python3 -m course_hoanganhduc.c50_agent list-classrooms --org ORG
python3 -m course_hoanganhduc.c50_agent sync --org ORG --classroom SHORT --db students.db
python3 -m course_hoanganhduc.c50_agent export --db students.db --out classroom50_roster.csv

# Canvas
python3 -m course_hoanganhduc.canvas_agent preflight
python3 -m course_hoanganhduc.canvas_agent list-members [--course-id ID]
python3 -m course_hoanganhduc.canvas_agent sync [--course-id ID]

# Google Classroom
python3 -m course_hoanganhduc.gclass_agent preflight
python3 -m course_hoanganhduc.gclass_agent list-courses
python3 -m course_hoanganhduc.gclass_agent sync --course-id ID

# Local DB
python3 -m course_hoanganhduc.db_agent search "keyword"
python3 -m course_hoanganhduc.db_agent export-roster --db students.db
```

Agent modules set `COURSE_AGENT_MODE=1` automatically. Do not invoke raw
`gh teacher` / `gh student` from the Classroom50 skill.

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
- [Dependencies](dependencies.md) (`course-hoanganhduc-python-package`, `github-cli`)
- Upstream toolkit (human CLI + agent modules):
  <https://github.com/hoanganhduc/course_management_toolkit>
- Toolkit usage notes: README and `docs/usage.rst` in that repository
  (Classroom50 flags and agent entrypoints).
