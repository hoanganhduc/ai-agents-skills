# Local compute throttle contract

When heavy computation must run on the local machine (remote offload
unavailable: cloud minutes exhausted, broker down, credit unverifiable),
run it under this contract. Its purpose is to make overload shutdowns
impossible: a research session on a small box (2-4 cores) has been killed
by load spikes from concurrent unthrottled compute — every rule below maps
to one such incident.

## Hard rules

1. ONE local compute job at a time, machine-wide. Enforce with a lockfile
   created atomically (`os.open(path, O_CREAT|O_EXCL)`) under
   `tempfile.gettempdir()` — portable across OSes; a second runner must
   abort, not queue. Write the PID inside and treat a lock whose PID is
   dead as stale.
2. SINGLE-PROCESS execution. No `multiprocessing.Pool` (terminate-hangs
   orphan workers), no `-j` above 2, no nested parallelism. Leave at least
   half the cores permanently free for the interactive session and agents.
3. Deprioritize (per platform):
   - Linux: `nice -n 19` + `ionice -c3`.
   - macOS: `nice -n 19` + `taskpolicy -c background` (no ionice).
   - Windows: idle priority class — `start /LOW /B ...` or, from Python,
     `psutil.Process().nice(psutil.IDLE_PRIORITY_CLASS)`.
4. Per-config watchdog, portable form: run each unit as a CHILD SUBPROCESS
   with `subprocess.run(..., timeout=<cap>)` and kill on expiry — this works
   on every OS and also isolates memory. (`signal.alarm` is a Unix-only
   optimization; do not rely on it in shared drivers.) Memory ceiling:
   `resource.setrlimit(RLIMIT_AS, ...)` on Unix; on Windows monitor the
   child's RSS via `psutil` and kill above the cap (~60-75% of free RAM).
5. Load guard inside the runner, portable form: `os.getloadavg()` on
   Linux/macOS (1-min load > ~0.75x core count => sleep >= 2 min and
   re-check); on Windows (no loadavg) use `psutil.cpu_percent(interval=1)`
   sustained above ~75% as the equivalent signal. Check between units, at
   least once a minute.

## Chunked, resumable execution

- Split every suite into time-budgeted chunks (~10-15 min). A chunk ends
  CLEANLY at its budget: append-only JSONL checkpoints per config, plus a
  persisted pending list (grid minus done) so the next chunk resumes
  exactly. A session restart then costs at most one chunk.
- Run chunks from a detached trickle loop with a STOP sentinel file for
  clean shutdown, and a log the orchestrator can poll: `setsid nohup ... &`
  on Linux/macOS; on Windows, `start /B` a `pythonw` loop or register a
  low-priority Scheduled Task. The sentinel + JSONL checkpoint pattern is
  identical on every OS. Long suites trickle over days by design; that is
  the intended behavior, not a defect.

## Orchestrator duties (the research loop side)

- Concurrency picture at any moment: 1 nice'd compute chunk (background)
  + 1 reasoning agent + 1 light verification gate, MAXIMUM. Verifier
  machine re-runs are sample-sized locally; full re-verification waits for
  remote compute.
- Task packets must state the budget: "local checks under ~5 minutes;
  anything heavier goes through the throttled runner queue or is deferred
  with a written spec." Agents must never launch ad-hoc heavy processes.
- Heartbeats/monitors kill runaways and orphans — including processes
  owned by OTHER users blocking cores (use sudo where permitted; verify
  ownership before killing), and check the queue is advancing.
- If the box still approaches overload (load > core count for minutes),
  stop the queue via the sentinel first; investigate before resuming.

## Choosing the venue

Order of preference for heavy work: remote CI/offload (see
`github-actions-offload.md`, subject to minute budgets) or the Modal
broker (check credit first; do not run on unverifiable credit) -> the
throttled local queue (this contract) -> defer with an exact pending list.
Never "just run it" unthrottled locally: that is how sessions die.
