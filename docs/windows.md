# Windows

Windows is multi-substrate. Native Windows PowerShell, Git Bash/MSYS, WSL,
and remote services are checked separately. SageMath is usually WSL-backed and
must not be treated as a normal Windows package.

Use `./make.ps1 precheck` before installation. The precheck reports whether each
dependency is native Windows, WSL-backed, missing, degraded, or manual. A
missing DeepSeek home on Windows is not an error; DeepSeek-specific artifacts
and dependencies are skipped when the agent is absent.
`./make.ps1` runs in the current PowerShell 5.1+ or PowerShell 7+ session. If
PowerShell is unavailable, use the POSIX bootstrap script from a compatible shell.

Native Windows is currently dry-run-only for installer-managed target mutation.
Commands that request apply, uninstall, rollback, OpenClaw target writes, or
Antigravity settings writes fail closed until the pathname mutation is bound to
the same Windows handle used for reparse-point, owner, and DACL validation.

Common commands from a native Windows shell:

```powershell
./make.ps1 doctor
./make.ps1 precheck --profile research-core
./make.ps1 plan --profile research-core
./make.ps1 install --profile research-core --dry-run
./make.ps1 lifecycle-test --matrix default --platform-shape windows
./make.ps1 fake-root-lifecycle --profile research-core --platform-shape windows
./make.ps1 verify --root <fake-or-real-root>
./make.ps1 docs
./make.ps1 sanitize-check
./make.ps1 test
```

Do not use `--apply` or `--real-system` on native Windows while this gate is in
place. The installer still detects only agent homes that already exist under
`--root`, so fake-root dry-runs must create `.codex`, `.claude`, or `.deepseek`
before planning. A fake root with no detected agent homes produces no actions.

## Runtime paths that differ on native Windows

Four runtime paths diverge from POSIX. Each is the supported native behaviour,
not a degradation to work around.

- **Secret-bearing launches require a trusted interpreter.** When a
  `*_SECRETS_FILE` pointer, `LEANEXPLORE_API_KEY`, or
  `AAS_RUNTIME_REQUIRE_TRUSTED=1` is present, `run_python.ps1` accepts only a
  Python that is both in a trusted location and pinned by
  `AAS_WINDOWS_PYTHON_SHA256` and `AAS_WINDOWS_PYTHON_SIGNER_THUMBPRINT`. Two
  locations qualify: an install under Program Files, and a versioned directory
  at the system drive root such as `C:\Python313`. The drive-root form is held
  to a stricter rule because `C:\` lets any authenticated user create a
  sibling directory: the interpreter and its directory must be owned by
  SYSTEM, Administrators, or TrustedInstaller and must grant write access to
  nobody else, not even the calling user. A per-user install never qualifies.
  Stale inherited `Authenticated Users` entries left on files by an earlier
  install are the usual cause of a rejection; clear them with
  `icacls "C:\Python313\*" /reset /t /c /q` from an elevated shell, which
  re-inherits the directory's own protected DACL. Do not run `/reset` against
  the directory itself, which would make it inherit a writable entry from the
  drive root.
- **Secret loading uses PowerShell.** `load_secret_env.py` refuses to run under
  `os.name == "nt"` and raises `SecretEnvError`. Native Windows loads
  credentials through `load_secret_env.ps1` instead, which validates the secret
  file's own owner and DACL before exporting anything.
- **The exact-generation credential broker is unavailable.** The broker speaks
  over `socket.AF_UNIX`, which CPython does not expose on Windows. Callers gate
  on `broker_active()`, which stays false because nothing sets
  `AAS_ARL_BROKER_SOCKET`, so panel and compute launches take the direct
  execution path. There is no named-pipe broker in this version.
- **Force-loop always runs in the foreground.** `--detach` is accepted but has
  no effect: with the default `--backend auto`, Windows selects `foreground`.
  Requesting `--backend posix_detach` explicitly fails with `posix_detach is not
  available on Windows; use foreground`. There is no Windows Service backend.

For WSL-backed tools, the relevant check is whether `wsl.exe` exists and the
command is available inside the default WSL distro. For example, `sage-runtime`
may be satisfied by `sage` inside WSL even if no native Windows `sage.exe`
exists.

Native Windows and WSL share one runtime root, so a WSL install legitimately
leaves POSIX-only artifacts such as `run_skill.sh` in a root that a later
Windows run also owns. `installed-runtime-smoke` reports these under
`runtime_state_foreign_platform_records` rather than counting them as
unmanaged extras.

When a Windows profile is inspected from Linux through a mounted drive,
`precheck` also looks for official or common native install locations such as
`C:\Python3*`, per-user Python installs, `C:\texlive\*\bin\windows`, and
MiKTeX roots. For SageMath, it checks current local WSL/Linux paths first when
the precheck itself is running from that substrate, then mounted WSL rootfs
locations when they exist. If only a WSL distro `ext4.vhdx` is visible, the
result is degraded: the distro exists, but Sage inside the image cannot be
verified without WSL, a local WSL filesystem, or a mounted rootfs.

Practical interpretation:

- missing DeepSeek on Windows means DeepSeek targets and dependencies are ignored
- native Python and TeX can be detected from common install roots even when
  inspected from Linux
- WSL-backed SageMath should be verified from WSL or native Windows when a
  mounted profile reports only degraded evidence

Related pages: [Dependencies](dependencies.md), [Installation](installation.md),
[Agent Locations](agent-locations.md), [Troubleshooting](troubleshooting.md).
