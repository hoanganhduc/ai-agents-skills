# OpenGauss on Windows

## Native Windows

**Unsupported** for live `gauss` execution. AAS still installs the inert skill/runtime helper so agents see policy and doctor output (`native_windows_live: unsupported`).

Do not claim a native `gauss.exe` success path.

## Supported path: WSL2

1. Install/enable WSL2 Ubuntu if needed: `wsl --install -d Ubuntu`
2. Inside WSL (Linux home path, not `/mnt/c/...` for best performance):

```bash
git clone https://github.com/math-inc/OpenGauss.git ~/OpenGauss
cd ~/OpenGauss
./scripts/install.sh
```

For later live auto mode, AAS, the Lean project, and OpenGauss should share the **same WSL distro** unless a tested cross-substrate adapter exists.
