# Windows

Windows is multi-substrate. Native Windows, PowerShell/CMD, Git Bash/MSYS, WSL,
and remote services are checked separately. SageMath is usually WSL-backed and
must not be treated as a normal Windows package.

Use `make.bat precheck` before installation. The precheck reports whether each
dependency is native Windows, WSL-backed, missing, degraded, or manual. A
missing DeepSeek home on Windows is not an error; DeepSeek-specific artifacts
and dependencies are skipped when the agent is absent.

For WSL-backed tools, the relevant check is whether `wsl.exe` exists and the
command is available inside the default WSL distro. For example, `sage-runtime`
may be satisfied by `sage` inside WSL even if no native Windows `sage.exe`
exists.
