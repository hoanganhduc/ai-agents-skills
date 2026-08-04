---
name: classroom50
description: "Route Classroom50 (foundation50) instructor workflows through the course_hoanganhduc agent entrypoint: readiness, list classrooms/roster/assignments, roster sync into local DB, and C50 CSV export. Raw gh teacher is limited to a non-mutating help probe."
user-invocable: true
disable-model-invocation: false
metadata: {"requires":{"bins":["gh"]}}
---

Use this skill when the user asks about Classroom50, foundation50 classroom tooling, GitHub Classroom alternatives for VNU courses, C50 roster sync, listing C50 classrooms/assignments, or exporting a Classroom50-compatible roster CSV.

## Core rules

- Do not fork or reimplement Classroom50 / `gh teacher` in this skill.
- On POSIX, require the dedicated course environment and use it for every
  adapter command:

```bash
course_python="$HOME/.course_venv/bin/python"
if [ ! -x "$course_python" ]; then
  printf '%s\n' 'TECHNICAL_FAIL: dedicated course interpreter is missing' >&2
  exit 1
fi
"$course_python" -m course_hoanganhduc.c50_agent <command> [options]
```

- On native Windows, resolve the same dedicated environment in PowerShell:

```powershell
$coursePython = "$env:USERPROFILE\.course_venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $coursePython -PathType Leaf)) {
  throw "TECHNICAL_FAIL: dedicated course interpreter is missing"
}
& $coursePython -m course_hoanganhduc.c50_agent <command> [options]
```

- **Always** use the agent entrypoint for Classroom50 operations; it sets agent mode automatically.
- Raw `gh teacher` is allowed only as the exact non-mutating readiness probe
  `gh teacher --help`. Do not run any other raw `gh teacher` or `gh student`
  command from this skill (humans may use them outside the skill).
- Do **not** download submissions via this skill (human-only; not agent-safe).
- Do **not** push roster, invite, unenroll, teardown, or pass confirm-skip flags.
- Require org allowlist in the environment for agent ops:

```bash
export CLASSROOM50_ORG_ALLOWLIST=my-org
# COURSE_C50_AGENT_MODE is set by c50_agent automatically
```

- If `course_hoanganhduc` is not importable, report the missing package in the
  selected course interpreter instead of claiming Classroom50 access. Do not
  silently install or switch environments.
- If readiness fails because the teacher extension is missing, report the
  missing `classroom50-teacher-extension`. For a manual, unpinned installation,
  the upstream command is `gh extension install foundation50/gh-teacher`;
  restoration systems own release pins and checksums.
- Never print tokens, PATs, or service secrets.

## Safe doctor and readiness

The doctor path is non-mutating. In Bash, first resolve `course_python` as
shown above, then run this fail-closed function:

```bash
classroom50_doctor() {
  local failed=0
  gh teacher --help >/dev/null || {
    printf '%s\n' 'TECHNICAL_FAIL: gh teacher is unavailable' >&2
    failed=1
  }
  "$course_python" -c 'import course_hoanganhduc' || {
    printf '%s\n' 'TECHNICAL_FAIL: course package is unavailable' >&2
    failed=1
  }
  "$course_python" -m course_hoanganhduc.c50_agent --help >/dev/null || {
    printf '%s\n' 'TECHNICAL_FAIL: Classroom50 adapter is unavailable' >&2
    failed=1
  }
  gh auth status || {
    printf '%s\n' 'REAUTH_REQUIRED: GitHub authentication is unavailable' >&2
    failed=1
  }
  if [ -n "${CLASSROOM50_ORG_ALLOWLIST:-}" ]; then
    printf '%s\n' 'CLASSROOM50_ORG_ALLOWLIST=CONFIGURED'
  else
    printf '%s\n' 'CLASSROOM50_ORG_ALLOWLIST=NOT_CONFIGURED' >&2
    failed=1
  fi
  return "$failed"
}
classroom50_doctor
```

In native Windows PowerShell, use the equivalent fail-closed checks:

```powershell
$doctorFailed = $false
gh teacher --help *> $null
if ($LASTEXITCODE -ne 0) {
  [Console]::Error.WriteLine("TECHNICAL_FAIL: gh teacher is unavailable")
  $doctorFailed = $true
}
& $coursePython -c "import course_hoanganhduc"
if ($LASTEXITCODE -ne 0) {
  [Console]::Error.WriteLine("TECHNICAL_FAIL: course package is unavailable")
  $doctorFailed = $true
}
& $coursePython -m course_hoanganhduc.c50_agent --help *> $null
if ($LASTEXITCODE -ne 0) {
  [Console]::Error.WriteLine("TECHNICAL_FAIL: Classroom50 adapter is unavailable")
  $doctorFailed = $true
}
gh auth status
if ($LASTEXITCODE -ne 0) {
  [Console]::Error.WriteLine("REAUTH_REQUIRED: GitHub authentication is unavailable")
  $doctorFailed = $true
}
if ([string]::IsNullOrWhiteSpace($env:CLASSROOM50_ORG_ALLOWLIST)) {
  [Console]::Error.WriteLine("CLASSROOM50_ORG_ALLOWLIST=NOT_CONFIGURED")
  $doctorFailed = $true
} else {
  Write-Output "CLASSROOM50_ORG_ALLOWLIST=CONFIGURED"
}
if ($doctorFailed) { throw "Classroom50 doctor failed" }
```

Never add `--show-token` to `gh auth status`, and never echo the allowlist
value. Classify failures without guessing:

- missing extension, interpreter, or import: `TECHNICAL_FAIL`
- failed or expired GitHub authentication: `REAUTH_REQUIRED`
- absent allowlist: `NOT_CONFIGURED`

After the local doctor passes, the adapter preflight is the only live readiness
probe. It may make read-only GitHub requests but does not mutate classroom data:

```bash
"$course_python" -m course_hoanganhduc.c50_agent preflight
```

```powershell
& $coursePython -m course_hoanganhduc.c50_agent preflight
```

Report `READY` only when the local checks, authentication, allowlist, and live
adapter preflight all pass.

## Common agent commands

```bash
"$course_python" -m course_hoanganhduc.c50_agent preflight
"$course_python" -m course_hoanganhduc.c50_agent list-classrooms --org ORG
"$course_python" -m course_hoanganhduc.c50_agent list-roster --org ORG --classroom SHORT
"$course_python" -m course_hoanganhduc.c50_agent list-assignments --org ORG --classroom SHORT
"$course_python" -m course_hoanganhduc.c50_agent sync --org ORG --classroom SHORT --db students.db --report report.json
"$course_python" -m course_hoanganhduc.c50_agent export --db students.db --out classroom50_roster.csv
```

```powershell
& $coursePython -m course_hoanganhduc.c50_agent preflight
& $coursePython -m course_hoanganhduc.c50_agent list-classrooms --org ORG
& $coursePython -m course_hoanganhduc.c50_agent list-roster --org ORG --classroom SHORT
& $coursePython -m course_hoanganhduc.c50_agent list-assignments --org ORG --classroom SHORT
& $coursePython -m course_hoanganhduc.c50_agent sync --org ORG --classroom SHORT --db students.db --report report.json
& $coursePython -m course_hoanganhduc.c50_agent export --db students.db --out classroom50_roster.csv
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
