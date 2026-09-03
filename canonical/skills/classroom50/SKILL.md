---
name: classroom50
description: "Route Classroom50 workflows through the restricted agent entrypoint. Explicitly confirmed assignment add/update may run only through a pre-reviewed, checksum-pinned, exact-scope script; all other raw teacher/student CLI use and destructive operations remain forbidden."
user-invocable: true
disable-model-invocation: false
metadata: {"requires":{"bins":["gh"]}}
---

Use this skill when the user asks about Classroom50, foundation50 classroom tooling, GitHub Classroom alternatives for VNU courses, C50 roster sync, listing C50 classrooms/assignments, or exporting a Classroom50-compatible roster CSV.

## Core rules

- Do not fork or reimplement Classroom50 / `gh teacher` in this skill.
- On POSIX, require the dedicated course environment and use it for every
  adapter command. OpenClaw's locked sandbox owns an image-local environment;
  normal host agents use the restored home environment:

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

- **Always** use the agent entrypoint for ordinary Classroom50 operations. The
  sole agent-side remote-write exception is an assignment script satisfying
  every condition in **Explicitly confirmed assignment-write exception** below.
- Do not invoke arbitrary raw `gh teacher`, a raw `gh-teacher` executable, or
  any `gh student` command. The exact direct readiness probe
  `gh teacher --help` remains allowed. A qualifying assignment-write script may
  internally invoke its checksum-pinned `gh-teacher` executable only for the
  reviewed `assignment add` operations; this does not authorize ad-hoc CLI
  construction or any other teacher/student command.
- Do **not** push a roster or otherwise change remote membership, invite,
  unenroll, remove an assignment, create/remove/teardown a classroom, download
  submissions, collect submissions or scores, run student acceptance/submission
  commands, or pass confirmation-skip flags. Read-only roster/list operations,
  local roster sync, and local CSV export remain on the restricted agent
  entrypoint.
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

## Explicitly confirmed assignment-write exception

This is the only agent-side Classroom50 remote-mutation path. It permits
assignment registration or an explicitly intended replacement, both implemented
upstream as `assignment add`; it does not expose a general teacher CLI.

An agent may execute one reviewed local script once only after the user directly
authorizes the agent to run that exact script and exact assignment scope. A
direct imperative such as “run it” is sufficient when the immediately identified
script and scope are unambiguous. Approval to plan, prepare, inspect, or test the
script is not execution authorization. One authorization may cover the complete
reviewed assignment set; do not require confirmation for each assignment.

Before execution, verify all of the following and fail closed if any check fails:

1. Bind authorization to the script’s absolute path, SHA-256, exact organization,
   exact classroom, exact assignment slugs, and create-versus-replace intent.
   Any script or scope change voids the authorization.
2. The script is a regular non-symlink file owned by the expected local account,
   has one hard link, and is not group- or world-writable.
3. Launch it with an allowlisted clean environment through an absolute shell
   path, with `BASH_ENV=/dev/null`; clearing `BASH_ENV` or imported functions
   from inside the script is not sufficient. Preserve only variables required
   for the reviewed authentication and locale.
4. The script uses an absolute `gh-teacher` executable whose version and
   publisher checksum were reviewed and whose checksum is reverified immediately
   before execution. It must not download code, source another file, use `eval`,
   self-modify, accept free-form arguments, or obtain its target or mutation
   command from ambient environment variables.
5. The authenticated GitHub identity and organization allowlist match the
   reviewed values. The script verifies the classroom’s immutable identity and
   configuration path, not merely its display name.
6. The only permitted remote write is `assignment add` for the reviewed slugs in
   the reviewed classroom. A new assignment must be absent before creation. An
   existing assignment may be replaced only when replacement was explicitly
   authorized and the script verifies its complete expected pre-state.
7. Maintain an exclusive-writer window for the target classroom configuration
   throughout the run. The upstream optimistic-rebase writer cannot prevent a
   concurrent same-slug web or CLI update from being overwritten.
8. Pin and verify every template revision and teacher-side tests payload used by
   the assignment. Reject unexpected or duplicate assignment slugs.
9. For every write, record the configuration head before and after and verify a
   single-parent change affecting only the reviewed classroom’s
   `assignments.json`. Verify the final exact assignment inventory and settings.
   Production classrooms may be read for before/after drift checks but must
   never be write targets.
10. Stop on the first failed or indeterminate write. Do not automatically rerun
    the script. Reconcile through the restricted read-only entrypoint first; a
    further execution requires renewed direct authorization.

This exception never covers assignment removal, roster or membership mutation,
invitations, classroom creation or teardown, submission/score collection,
downloads, repository deletion or permission changes, production-course writes,
or any `gh student` operation.

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
- In OpenClaw's `/workspace` sandbox, the restoring system must provide the
  image-local course environment, the teacher extension below
  `/workspace/.local/share/gh/extensions/`, and a private GitHub CLI config
  projection below `/workspace/.config/gh/`.
- Secrets and GitHub auth come from the existing `gh` login / environment; this skill does not provide secret setup instructions.
