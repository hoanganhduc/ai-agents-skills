# Linux

Linux checks resolve logical tools from installed commands, repo-local runtimes,
and user overrides such as `AAS_PYTHON` or `AAS_SAGE`. `precheck` also checks
selected optional Python packages where a skill declares them.

Common commands:

```bash
make doctor
make precheck ARGS="--profile research-core"
make plan ARGS="--profile research-core"
make install ARGS="--profile research-core --dry-run"
make lifecycle-test ARGS="--matrix default --platform-shape linux"
make fake-root-lifecycle ARGS="--profile research-core --platform-shape linux"
make verify ARGS="--root <fake-or-real-root>"
```

Useful overrides:

- `AAS_PYTHON`: preferred Python interpreter for `python-runtime` checks
- `AAS_SAGE`: preferred SageMath executable for `sage-runtime` checks
- `PATH`: command discovery for TeX, Git, ripgrep, OCR, Calibre, and other tools

The Linux path is also used when inspecting a mounted Windows profile from WSL
or a Linux host. In that case, native Windows executables may be reported as
`present-unverified` because they can be found but not safely executed from the
current substrate.

Related pages: [Dependencies](dependencies.md), [Windows](windows.md),
[Installation](installation.md), [Troubleshooting](troubleshooting.md).
