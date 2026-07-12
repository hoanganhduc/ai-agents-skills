---
name: classroom50
description: "Route Classroom50 (foundation50) instructor workflows through the course_hoanganhduc agent entrypoint: preflight, list classrooms/roster/assignments, roster sync into local DB, and C50 CSV export. Does not invoke raw gh teacher."
user-invocable: true
disable-model-invocation: false
metadata: {"requires":{"bins":["python3","gh"]}}
---

Use this skill when the user asks about Classroom50, foundation50 classroom tooling, GitHub Classroom alternatives for VNU courses, C50 roster sync, listing C50 classrooms/assignments, or exporting a Classroom50-compatible roster CSV.

## Core rules

- Do not fork or reimplement Classroom50 / `gh teacher` in this skill.
- **Always** use the agent entrypoint (sets agent mode automatically):

```bash
python3 -m course_hoanganhduc.c50_agent <command> [options]
```

- Do **not** run raw `gh teacher` or `gh student` from this skill (humans may use them outside the skill).
- Do **not** download submissions via this skill (human-only; not agent-safe).
- Do **not** push roster, invite, unenroll, teardown, or pass confirm-skip flags.
- Require org allowlist in the environment for agent ops:

```bash
export CLASSROOM50_ORG_ALLOWLIST=my-org
# COURSE_C50_AGENT_MODE is set by c50_agent automatically
```

- If `course_hoanganhduc` is not importable, report the missing package (`pip install -e` the course_management_toolkit checkout) instead of claiming Classroom50 access.
- If preflight fails because the teacher extension is missing, report that the Classroom50 teacher `gh` extension must be installed (see foundation50/classroom50 CLI docs) rather than inventing API calls.
- Never print tokens, PATs, or service secrets.

## Common agent commands

```bash
python3 -m course_hoanganhduc.c50_agent preflight
python3 -m course_hoanganhduc.c50_agent list-classrooms --org ORG
python3 -m course_hoanganhduc.c50_agent list-roster --org ORG --classroom SHORT
python3 -m course_hoanganhduc.c50_agent list-assignments --org ORG --classroom SHORT
python3 -m course_hoanganhduc.c50_agent sync --org ORG --classroom SHORT --db students.db --report report.json
python3 -m course_hoanganhduc.c50_agent export --db students.db --out classroom50_roster.csv
```

`download` via the agent entrypoint is refused by design.

## Optional human CLI (outside agent entry)

For interactive human operators only, the full `course` CLI may expose:

- `--download-classroom50` with `--classroom50-assignment` and `--classroom50-download-dest`
- Other Classroom50 list/sync/export flags mirroring the agent surface

Prefer the agent entrypoint for agent sessions.

## Natural-language routing

- "whoami / is Classroom50 auth ok?" → `preflight`
- "list C50 classrooms in ORG" → `list-classrooms --org ORG`
- "list roster for classroom X" → `list-roster --org … --classroom X`
- "sync Classroom50 roster into my DB" → `sync …`
- "export C50 CSV" → `export …`
- "download submissions" → explain human-only; do not run agent download

## Target notes

- This skill is target-adaptable; do not hardcode user-specific checkout paths.
- Secrets and GitHub auth come from the existing `gh` login / environment; this skill does not provide secret setup instructions.
