# Browsers and platforms

Browser detection is detect-only and never installs anything. Resolution order:

1. `URL_TO_SCREENSHOT_BROWSER` (or the `--browser` flag) — an explicit path.
2. `PATH` command names, per OS.
3. Per-OS install-location candidates (with env-var expansion and globbing).

If nothing is found, detection is fail-soft: the record reports `status=missing`
and capture returns `BLOCKED_ENVIRONMENT`. Only `doctor` reports real readiness.

## Per-OS candidates

- **Linux:** PATH `chromium`, `chromium-browser`, `google-chrome`,
  `google-chrome-stable`, `chrome`, `microsoft-edge`, `microsoft-edge-stable`;
  then `/usr/bin/...`, `/snap/bin/chromium`, `/opt/google/chrome/chrome`,
  `/opt/microsoft/msedge/msedge`.
- **macOS:** PATH names, then
  `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`,
  `/Applications/Chromium.app/Contents/MacOS/Chromium`,
  `/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge`.
- **Windows:** `%PROGRAMFILES%` / `%PROGRAMFILES(X86)%` / `%LOCALAPPDATA%` paths
  for `Google\Chrome\Application\chrome.exe`, `Chromium\Application\chrome.exe`,
  `Microsoft\Edge\Application\msedge.exe`; plus PATH `chrome.exe`, `msedge.exe`,
  `chromium.exe`.

The detector is parameterized by an injectable `os_name` and `candidate_root`,
so the offline self-test and unit tests deterministically exercise all three OS
layouts (including the Windows `%PROGRAMFILES(X86)%` globs and the macOS
app-bundle paths) from a single Linux host.

## Process control

Process launch and kill are platform-split (`u2s.procctl`):

- **POSIX:** `start_new_session=True` plus `os.killpg(SIGTERM -> SIGKILL)`.
- **Windows:** `CREATE_NEW_PROCESS_GROUP` plus a `taskkill /T /F` reap of the
  Chromium process tree.

Every platform-specific API is referenced only inside an `os.name`-guarded
branch, so `import u2s.procctl` succeeds on every OS. The per-run
`tempfile.mkdtemp(prefix="url2png_")` profile directory is removed in a `finally`
block with locked-file-tolerant retry. Windows job-object/`taskkill` reaping and
locked-file cleanup are verified only by manual Windows runs.
