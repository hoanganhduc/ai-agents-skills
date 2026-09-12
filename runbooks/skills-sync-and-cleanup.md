# Runbook: sync, update and clean an ai-agents-skills installation

## Context

`ai-agents-skills` renders one canonical skill catalog into many coding-agent homes. Over time
an installation drifts three ways at once: the clone falls behind the remote, installed
artifacts go stale or become orphaned, and the installer's own journal grows with no prune
mechanism.

This runbook is the repeatable procedure for bringing any such installation back in line. It is
written to be run by any coding agent, on any host OS, against any chosen subset of install
targets.

It deliberately carries **no host data** — no usernames, no absolute home paths, no artifact
counts, no journal sizes from any particular machine. Every quantity is something the operator
measures on the system in front of them, using the commands given here. This is also a hard
requirement, not a preference: `make sanitize-check` runs `has_sensitive_material()` over
everything the repository ships, and home-shaped paths fail it.

## How to run this document

Written for an operator or a coding agent with no prior context on the installation.

1. **Nothing here is a literal command.** Anything in `<angle brackets>` or `$UPPERCASE` is a
   placeholder you substitute from the Parameters table or from a measurement. A command still
   containing a placeholder is not ready to run.
2. **Work the phases in order.** Phase 3 is the only one that writes to an agent home; Phase 4b
   is the only one that deletes. Everything else is read-only and safe to repeat.
3. **Read the four decision rules (R1–R4) before Phase 3.** They are where installations get
   broken, and each one tells you how to measure the answer rather than assume it.
4. **Measure, do not carry numbers over.** This document states no counts, sizes or paths from
   any installation on purpose. If a step needs a number, it gives you the command that produces
   it. A figure from a previous run is not evidence about this one.
5. **Stop conditions are hard stops**, not warnings. If one triggers, fix the cause before
   continuing; do not force past it with `--force`-style flags or by editing state by hand.
6. **Never adopt, move or delete anything the audit reports as `extra_local`.** Those are the
   user's own skills and vendor-shipped ones. The installer reports them precisely so you leave
   them alone.
7. **When a check fails, establish whether it already failed before you started.** Phase 5 lists
   the failures that are commonly pre-existing.

## Parameters

Set these once; every command below refers to them.

| Parameter | Meaning |
|---|---|
| `REPO` | the clone being installed from |
| `ROOT` | home whose agent directories are the install targets |
| `AGENTS` | comma-separated targets to act on |
| `PROFILE` | skill selector, e.g. `research-core` or `complete-restore` |
| `ARTIFACTS` | optional artifact selector |
| `RUNTIME_ROOT` | where runtime files live; see R3 |
| `KEEP_DAYS` | journal retention window |

Launcher per host: `make <cmd> ARGS="..."` or `./installer/bootstrap.sh <cmd> ...` on POSIX;
`./make.ps1 <cmd> ...` on native Windows; `python3 -m installer.ai_agents_skills ...` when you
need to pass a `--root` the wrappers do not assume.

## Decision rules

These are the parts that are easy to get wrong. Each is a rule plus the check that settles it,
so the runbook never depends on remembering one system's answers.

### R1 — Choose the write host

The installer refuses every applied mutation when `os.name == "nt"`
(`apply.py` → `windows_security.require_handle_bound_mutation`). It is not gated by
`--real-system`, has no environment override, and is pinned by
`tests/test_windows_mutation_gate.py`. The reason is a TOCTOU gap: validation is handle-bound,
mutation is by pathname.

```sh
<launcher> --run-python -c "import os; print(os.name)"     # 'nt' -> this host cannot apply
```

| Host | Plan / audit / verify | Apply |
|---|---|---|
| Linux, macOS | yes | yes |
| WSL, targeting its own home | yes | yes |
| WSL, targeting a mounted Windows profile | yes | yes, subject to the mount check and R2 |
| Native Windows | yes | **no** |

Mounted-profile precondition — without `metadata` every path reports mode `0777` and the POSIX
ownership checks refuse it:

```sh
mount | grep ' /mnt/<drive> '        # must list metadata
```

Pass `--platform windows` so Windows-inapplicable artifacts stay excluded. Tripwire from
`docs/windows.md`: **any `create` action for a `*.sh` support file means `--platform` was not
honoured — stop.**

### R2 — Force copy mode when installer host and agent runtime differ

This is the rule that silently breaks installations. Two of the three install modes embed a
filesystem path into the installed artifact:

| Mode | What lands in the agent home | Embeds a path? |
|---|---|---|
| `copy` | real files | no |
| `reference` | a thin adapter naming the canonical file | **yes, in its text** |
| `symlink` | a link into the repo | **yes, as the link target** |

An embedded path is written the way the **installer host** sees it. When the **agent runtime**
runs on a different OS, that path is unreachable and every affected skill breaks silently: a
POSIX mount path means nothing to a native Windows agent, and a Windows drive path means nothing
to a Linux one. Nothing errors at install time; the agent simply stops finding the skill.

It also trips the repo's own tripwire — `sanitize.py` classifies mounted-profile and
drive-letter home paths as leaked homes, so `verify` fails `no-secret-leak` on those artifacts.

**Rule.** Installer host OS = agent runtime OS → leave `--install-mode` at its default (`auto`).
They differ → pass `--install-mode copy`, but **only on a run scoped to the affected agents**.
The flag is global; an unscoped run converts every other target's mode too, which can quietly
end symlink-backed live tracking for agents that were fine.

Detect each agent's current mode before deciding:

```sh
# symlink?
find "$ROOT/<agent home>/skills" -name 'SKILL.md' -type l | wc -l
# reference?
grep -l 'Install mode: reference' "$ROOT/<agent home>"/skills/*/SKILL.md | wc -l
# neither -> copy
```

Then split the work into at most two runs: same-host-safe agents at `auto`, path-embedding
agents at `copy`.

**Prove it in a fake root before touching a real home.** Install one affected agent there from
the intended host, then read back what was embedded:

```sh
readlink <fake root>/<agent home>/skills/<skill>/SKILL.md
grep -o '`[^`]*canonical/skills[^`]*`' <fake root>/<agent home>/skills/<skill>/SKILL.md
```

If either names a path the agent runtime cannot open, the mode is wrong for this combination.

### R3 — Pass `RUNTIME_ROOT` explicitly

Runtime files are root-scoped and the default resolves differently per platform. The symptom of
getting it wrong is `runtime-file` artifacts reported *unmanaged* with `target path exists and
differs from runtime source`. Pass the value the existing installation already uses so a
cross-host run does not re-plan the whole runtime tree:

```sh
<launcher> --run-python -c "import json,sys; d=json.load(open(sys.argv[1])); print({a.get('runtime_root') for a in d['artifacts'] if a.get('runtime_root')})" "$ROOT/.ai-agents-skills/state.json"
```

**Read the result carefully: this set can hold more than one entry.** A Codex-only install and
a later multi-agent install use different defaults, so an installation that grew over time ends
up with runtime files under two roots. That is not a display quirk — it makes
`installed-runtime-smoke --require-complete-coverage` fail with
`failure_kind: runtime-state-coverage`, *"managed runtime state does not match the current
manifest closure"*, and the report lists every root it found.

If the set has one entry, pass it as `RUNTIME_ROOT`. If it has more, decide deliberately which
root is canonical and consolidate before relying on the coverage check; passing one of them
silently re-plans the other's files. Do not treat a multi-root result as normal.

With no existing installation, the default is the Codex runtime directory for a Codex-only
install and the platform user-data directory otherwise.

### R4 — Journal retention

The journal directory holds `state.json`, `runs/` and `backups/`. Nothing prunes them.
`backups/` is what `uninstall` and `rollback` restore from; `runs/` is what
`rollback --run <id>` addresses.

**Rule.** Never delete a run whose id appears anywhere in `state.json` — including inside nested
`previous_state_artifact` chains, which carry ids of superseded installs and are easy to miss.
Delete only run files that are unreferenced **and** older than `KEEP_DAYS`. Keep `backups/`
unless the operator explicitly accepts losing restore capability for those runs. Never hand-edit
`state.json`.

Expect the journal to be internally inconsistent before you start — run files with no record,
backup directories belonging to no listed run. That is precisely why deleting by date alone is
forbidden. An orphaned `.state.json.*.tmp` beside `state.json` is a failed atomic write and is
always safe to remove.

## Phases

### Phase 0 — Snapshot

1. Copy `state.json` aside. It is the only artifact that cannot be rebuilt from the repo.
2. List untracked work in `REPO` and test each path against the incoming revision **before**
   merging, so the pull cannot collide with uncommitted work:
   ```sh
   git -C "$REPO" status --short
   git -C "$REPO" cat-file -e "origin/main:<path>"   # non-zero exit = safe
   ```
3. `git -C "$REPO" status --short` must show no modified tracked files.

### Phase 1 — Pull

```sh
git -C "$REPO" fetch origin
git -C "$REPO" rev-list --count origin/main..main     # non-zero = local commits; resolve first
git -C "$REPO" log --oneline main..origin/main
git -C "$REPO" diff --stat main..origin/main -- manifest/ canonical/
git -C "$REPO" merge --ff-only origin/main
```

`--ff-only` deliberately: a refusal means the branch diverged and needs a real decision, not an
implicit merge commit.

### Phase 2 — Re-baseline

Numbers from any previous run are stale. Re-measure before acting, and diff the catalog so
"there are new skills" is a measured claim rather than an assumption — a sync can easily turn
out to add none:

```sh
<launcher> doctor
<launcher> precheck --profile "$PROFILE"
<launcher> audit-system --profile "$PROFILE" --migration-report --json
<launcher> plan --profile "$PROFILE" --json
git -C "$REPO" diff <old>..<new> -- manifest/skills.yaml
```

Record: skills added and removed, action counts by operation, `missing_by_agent`, legacy
aliases, and `extra_local` per agent. **`extra_local` is user-owned** — vendor-shipped skills
and unrelated toolkits live there and must never be adopted or deleted. Nothing is applied here.

### Phase 3 — Install and update

1. Settle R2 in a fake root for every agent whose mode embeds a path.
2. Dry-run each group, and read it.
3. Retire legacy aliases if the audit found any: `plan --agents <a> --migrate --json`, review the
   `legacy-skill-file` actions, then apply with `--migrate`.
4. Apply one group at a time. `state.json` is rewritten after every action, so concurrent applies
   corrupt it.

```sh
# group A — agents whose current mode is safe from this host
<launcher> plan --agents "$SAME_HOST_AGENTS" --profile "$PROFILE" \
  --artifact-profile "$ARTIFACTS" --runtime-profile full \
  --runtime-root "$RUNTIME_ROOT" --require-all-requested-agents --json

# group B — agents needing copy mode under R2
<launcher> plan --agents "$CROSS_HOST_AGENTS" --profile "$PROFILE" \
  --artifact-profile "$ARTIFACTS" --install-mode copy --json

AAS_INSTALL_CONFIRM="I understand the installation and uninstall process" \
<launcher> install --agents <group> ... \
  --require-complete-install --apply --real-system --post-install-smoke verify
```

Stop conditions:

- a `create` action for a `*.sh` support file → `--platform` not honoured (R1)
- `unmanaged` artifacts remaining after `RUNTIME_ROOT` is set (R3)
- any action inside an OpenClaw workspace that is a sync replica
- `--require-complete-install` fails → a requested agent home is missing; fix it, do not force

`--dry-run` and `--apply` are mutually exclusive; the CLI rejects the combination.

### Phase 4 — Clean

**4a. Obsolete managed artifacts.** These appear as `remove-obsolete` actions and are the
installer's job, not yours. Confirm the applied run consumed them. If it did not, use
`uninstall --agents <a> --artifacts <ids>`; deleting files by hand strands their journal records.

**4b. Journal.** Apply R4: enumerate referenced run ids from `state.json`, print the exact list
that will be deleted, then delete. Never sweep by glob.

**4c. Leave alone.** `extra_local` skills, the live runtime root, and anything the audit reports
as unmanaged.

### Phase 5 — Verify

```sh
<launcher> verify
<launcher> audit-system --profile "$PROFILE" --json        # expect status != drift-detected
<launcher> installed-runtime-smoke --require-complete-coverage
<launcher> smoke --skills <a few changed skills>
```

**Run all four before making any change and keep the output.** They are comparisons, not pass
marks, and an installation that has been in use for a while will usually fail at least one of
them for reasons that have nothing to do with this run. Treating a non-zero exit here as "the
sync broke it" is the single easiest way to misread this procedure.

Two pre-existing failures to expect and not misdiagnose:

- `verify` reporting `installed-signature-match` / `source-hash-match` false does **not** by
  itself mean the file was edited locally. Open the file and compare its bytes against the
  source its record names before concluding anything.
- `installed-runtime-smoke --require-complete-coverage` exits 1 with
  `failure_kind: runtime-state-coverage` whenever runtime files are split across more than one
  root. That is R3's problem, not a fault introduced by installing.

A clean install into a fresh fake root verifies without failures, so when something does fail,
the cause is almost always specific to this installation's history.

Then spot-check by hand: one updated `SKILL.md` per install mode in use matches its source, and
each selected agent's instruction file exists and carries its managed markers.

### Phase 6 — Cross-OS coverage

```sh
<launcher> fake-root-lifecycle --profile "$PROFILE" --platform-shape all
<launcher> lifecycle-test --matrix default --platform-shape all
<launcher> test
```

`--platform-shape all` covers linux, macos, windows and wsl against fake roots, which is how a
single host proves the procedure for the others. **These need a POSIX host**: they perform real
applies into fake roots and R1's gate does not exempt fake roots, so they exit non-zero on
native Windows — despite being listed as native-Windows commands in `docs/windows.md` and
`make.ps1 help`, which is a documentation bug worth reporting upstream.

### Phase 7 — Record

Commit the runbook with this run's measured numbers kept **outside** the repository — a local
note, an issue, wherever the operator keeps run logs. Committing measurements taken from a real
home is what `make sanitize-check` exists to stop.

## Target and path reference

`openclaw` is excluded from `install` on purpose — real OpenClaw writes go through its own
reviewed manifest workflow (`openclaw-*-manifest`).

| Target | POSIX home suffix | Windows profile suffix |
|---|---|---|
| codex | `.codex` | `.codex` |
| claude | `.claude` | `.claude` |
| deepseek | `.deepseek` | `.deepseek` |
| copilot | `.copilot` | `.copilot` |
| opencode | `.config/opencode` | `.config\opencode` |
| antigravity | `.gemini/antigravity-cli` | `.gemini\antigravity-cli` |
| grok | `.grok` | `.grok` |
| kimi | `.kimi-code` | `.kimi-code` |

Each is relative to `ROOT`. Absent agents are simply not detected; that is not an error.
`--require-all-requested-agents` turns absence into a failure when every named target must exist.

## Rollback

| Phase | Reverse with |
|---|---|
| 1 pull | `git -C "$REPO" reset --hard <pre-pull sha>` |
| 3 install | `rollback --run <run_id> --apply --real-system` from a POSIX host, then `verify` |
| 4b journal | not recoverable — which is why Phase 0 copies `state.json` and R4 keeps `backups/` |

## Two hypotheses this procedure does not rely on

Both were held during earlier work on this runbook and both were falsified cheaply in a fake
root. They are recorded so nobody re-derives them.

- *"Applying from a host that sees a different path shape forks the journal."* It does not. A
  copy of an installation, planned against a root that does not match the recorded paths, still
  resolved every artifact as managed with no duplicate records. Do not build a path-migration
  step on this premise.
- *"A failing `verify` means the file was edited by hand."* It does not. Failing signature and
  source-hash checks occur on files that are byte-identical to their source. Compare bytes
  before concluding.
