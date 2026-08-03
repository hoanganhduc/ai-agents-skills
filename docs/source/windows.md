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

Use `--real-system` only when you intentionally want to write to the detected
Windows agent homes. The installer detects only agent homes that already exist
under `--root`, so fake-root tests must create `.codex`, `.claude`, or
`.deepseek` before planning or applying. A fake root with no detected agent
homes produces no install actions and does not create managed installer state.

For WSL-backed tools, the relevant check is whether `wsl.exe` exists and the
command is available inside the default WSL distro. For example, `sage-runtime`
may be satisfied by `sage` inside WSL even if no native Windows `sage.exe`
exists.

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
