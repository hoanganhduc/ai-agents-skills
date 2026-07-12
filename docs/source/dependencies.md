# Dependencies

Dependencies are logical capabilities used by skills and artifacts. The
installer does not hardcode personal paths; `precheck` resolves each
capability from environment overrides, repo-local runtimes, `PATH`,
Python import checks, native Windows locations, WSL-backed commands,
or remote-service placeholders.

Use this page to understand what software may be needed before an
install. Use [Profiles](profiles.md) or [Skills](skills.md) to see
which capabilities are selected for a workflow, and use
[Windows](windows.md) or [Linux](linux.md) for platform-specific
detection notes.

Minimum installer prerequisites:

- Python 3.10 or newer.
- A shell that can run the launcher: POSIX shell plus `make` on
  Linux/macOS, or `make.bat` with PowerShell on native Windows.
- Existing agent homes for any agents you want to install into. Missing
  agent homes are skipped rather than created implicitly.

Common commands:

```bash
make doctor ARGS="--profile research-core"
make precheck ARGS="--profile research-core"
make precheck ARGS="--profile full-research --interactive"
make precheck ARGS="--profile math --json"
```

Status vocabulary used by `precheck`:

- `present`: the capability was found and can be used from the current substrate.
- `missing`: the capability was not found and may need installation.
- `degraded`: the capability appears to exist, but some part could not be executed or fully inspected.
- `present-unverified`: the capability was found as a file or install root, but the current substrate cannot safely execute it.
- `manual`: the capability depends on credentials, local databases, or service setup outside this repo.

## Logical Tools

| Logical Tool | Description |
|---|---|
| `calibre-cli` | Calibre command line tools for ebook metadata and conversion. |
| `chromium-browser-system-tool` | Headless Chromium/Chrome/Edge for CDP-driven page screenshots. |
| `espeak-ng-system-tool` | eSpeak NG phonemizer used by offline TTS engines (Kokoro/Piper). |
| `ffmpeg-system-tool` | FFmpeg/ffprobe for video encoding, audio normalization, and duration probing. |
| `getscipapers-cli` | getscipapers console script from the maintainer fork, provisioned by the getscipapers-requester skill into a dedicated runtime-owned venv (~/.getscipapers_venv). |
| `git-cli` | Git command line client for repository-backed workflows and GitHub publishing. |
| `github-cli` | GitHub CLI for workflows that need local Actions, PR, issue, or authentication commands. |
| `gnupg-system-tool` | GnuPG (gpg) for optional PGP/MIME signing of outgoing email in send-email. |
| `imagemagick-system-tool` | ImageMagick convert/magick for optional post-capture cropping. |
| `lake-cli` | Lake command line executable for optional Lean project checks. |
| `lean-cli` | Lean command line executable for optional local formal typechecking. |
| `libreoffice-system-tool` | LibreOffice (soffice) headless for rendering PPTX decks to PDF. |
| `manim-tex-runtime` | LaTeX engine with dvisvgm and standalone/preview (plus cairo/pango) for Manim MathTex rendering. |
| `mathlib-cache` | Manually managed mathlib cache or project dependency state for optional Lean checks. |
| `node-runtime` | Node runtime for MCP servers that use npx. |
| `nvidia-smi-tool` | NVIDIA GPU inspection tool used by resource preflight when available. |
| `ocr-runtime` | OCR command line runtime, normally Tesseract. |
| `powershell-runtime` | PowerShell runtime for Windows bootstrap and argument forwarding. |
| `pptx-render-system-tool` | PPTX renderer: Microsoft PowerPoint on Windows or LibreOffice elsewhere. |
| `python-runtime` | Python runtime with venv, pip, and ssl support. |
| `ripgrep-cli` | ripgrep command line search used by local research and session-inspection workflows. |
| `rocm-smi-tool` | AMD ROCm GPU inspection tool used by resource preflight when available. |
| `sage-runtime` | SageMath runtime, local on Linux or WSL-backed on Windows. |
| `tex-runtime` | TeX engine for TikZ compile checks. |
| `uvx-cli` | uvx command for optional manual AXLE MCP server execution. |
| `wsl-runtime` | Windows Subsystem for Linux runtime. |

## Packages And Services

| Dependency | Type | Detail |
|---|---|---|
| `axle-auth` | `remote-service` | remote-service |
| `beautifulsoup4-python-package` | `python` | bs4 |
| `calibre-cli` | `tool` | calibre-cli |
| `chromium-browser-system-tool` | `tool` | chromium-browser-system-tool |
| `course-hoanganhduc-python-package` | `python` | course_hoanganhduc; candidate set `agent` |
| `docling-mcp-python-package` | `python` | docling_mcp; candidate set `docling` |
| `docling-python-package` | `python` | docling; candidate set `docling` |
| `ebooklib-python-package` | `python` | ebooklib |
| `edge-tts-python-package` | `python` | edge_tts |
| `espeak-ng-system-tool` | `tool` | espeak-ng-system-tool |
| `feedparser-python-package` | `python` | feedparser |
| `ffmpeg-system-tool` | `tool` | ffmpeg-system-tool |
| `getscipapers` | `tool` | getscipapers-cli |
| `git-cli` | `tool` | git-cli |
| `github-cli` | `tool` | github-cli |
| `gnupg-system-tool` | `tool` | gnupg-system-tool |
| `google-api-python-client-package` | `python` | googleapiclient |
| `google-auth-python-package` | `python` | google.oauth2 |
| `imagemagick-system-tool` | `tool` | imagemagick-system-tool |
| `kokoro-python-package` | `python` | kokoro |
| `lean-explore-local-cache` | `manual-data` | manual-data |
| `lean-explore-python-package` | `python` | lean_explore; candidate set `agent` |
| `leanexplore-auth` | `remote-service` | remote-service |
| `libreoffice-system-tool` | `tool` | libreoffice-system-tool |
| `manim-python-package` | `python` | manim |
| `manim-tex-runtime` | `tool` | manim-tex-runtime |
| `modal-auth` | `remote-service` | remote-service |
| `modal-python-package` | `python` | modal; candidate set `agent` |
| `networkx-python-package` | `python` | networkx |
| `numpy-python-package` | `python` | numpy |
| `nvidia-smi-tool` | `tool` | nvidia-smi-tool |
| `ocr-runtime` | `tool` | ocr-runtime |
| `pdf2image-python-package` | `python` | pdf2image |
| `pdfplumber-python-package` | `python` | pdfplumber |
| `pillow-python-package` | `python` | PIL |
| `piper-tts-python-package` | `python` | piper |
| `pptx-render-system-tool` | `tool` | pptx-render-system-tool |
| `psutil-python-package` | `python` | psutil |
| `pylatexenc-python-package` | `python` | pylatexenc |
| `pymupdf-python-package` | `python` | fitz |
| `pypdf2-python-package` | `python` | PyPDF2 |
| `pypdfium2-python-package` | `python` | pypdfium2; candidate set `docling` |
| `pytest-python-package` | `python` | pytest |
| `python-pptx-python-package` | `python` | pptx |
| `pyzotero-python-package` | `python` | pyzotero |
| `rapidocr-python-package` | `python` | rapidocr; candidate set `docling` |
| `requests-python-package` | `python` | requests |
| `responses-python-package` | `python` | responses |
| `ripgrep-cli` | `tool` | ripgrep-cli |
| `rocm-smi-tool` | `tool` | rocm-smi-tool |
| `shapely-python-package` | `python` | shapely |
| `soundfile-python-package` | `python` | soundfile |
| `svgelements-python-package` | `python` | svgelements |
| `telegram-bot-config` | `remote-service` | remote-service |
| `torch-python-package` | `python` | torch; candidate set `docling` |
| `torchvision-python-package` | `python` | torchvision; candidate set `docling` |
| `vnu-eoffice-python-package` | `python` | vnu_eoffice; candidate set `agent` |
| `websocket-client-python-package` | `python` | websocket |
| `zotero-credentials` | `remote-service` | remote-service |

## Current Linux And Windows Config Inventory

This sanitized inventory is derived from the maintainer's current Linux
and Windows target configs for Codex, Claude, DeepSeek, Copilot, OpenCode, and Antigravity where
present. It intentionally excludes
auth files, provider secrets, session/history/log files, local library
databases, caches, backups, and file-history snapshots.

Personal paths are represented as `<LINUX_HOME>` or `<WINDOWS_HOME>`.

Evidence inspected:

- Linux Codex AGENTS/instruction/skill docs
- Linux DeepSeek AGENTS/skill docs
- Linux Claude CLAUDE/command/skill docs
- Linux Codex runtime skill requirement files and Python imports
- Windows Codex AGENTS/config/skill docs
- Windows Claude CLAUDE/command/skill docs
- Windows Claude skill requirement files and Python imports
- npm metadata for the deepseek-tui to codewhale package rename

### Extra Software

| Software | Requirement | Linux | Windows | Used By |
|---|---|---|---|---|
| `bash-or-posix-shell` | required on Linux and inside WSL-backed Windows flows | Used by shared runtime runner and shell skill wrappers. | Used through WSL for SageMath and other Linux-substrate commands. | `runtime wrappers`, `sagemath`, `make` |
| `calibre-cli` | optional for richer ebook library operations | calibredb and ebook-convert on PATH. | calibredb.exe and ebook-convert.exe on PATH. | `calibre`, `vnthuquan` |
| `chromium-browser` | optional for url-to-screenshot page capture (headless CDP screenshot); reported by the doctor verb, not an install gate | chromium or google-chrome on PATH, e.g. apt-get install chromium. | chrome.exe or msedge.exe at the default Program Files path or on PATH. | `url-to-screenshot`, `url-to-screenshot-runtime` |
| `docling-cli` | optional CLI layer for docling workflows | Current Claude docs use <LINUX_HOME>/.local/share/docling-venv/bin/docling. | Current Claude docs use <WINDOWS_HOME>/.venv-docling/Scripts/docling.exe. | `docling` |
| `espeak-ng` | optional, for offline TTS (Kokoro/Piper phonemization) | espeak-ng on PATH, e.g. apt-get install espeak-ng. | espeak-ng on PATH or at the default winget path C:\Program Files\eSpeak NG\espeak-ng.exe. | `slides-to-video offline TTS` |
| `ffmpeg` | required for slides-to-video rendering (video encode, audio normalize, duration probe) | FFmpeg + ffprobe on PATH (LGPL build with libx264), e.g. apt-get install ffmpeg. | ffmpeg.exe + ffprobe.exe on PATH (LGPL build), via winget/choco or a static build. | `slides-to-video` |
| `git-cli` | required for repository workflows and publishing | git on PATH. | git.exe or git on PATH. | `GitHub workflows`, `repo install/update examples` |
| `github-cli` | optional for GitHub workflows that need local gh commands | gh on PATH with auth configured when needed. | gh.exe or gh on PATH with auth configured when needed. | `github`, `gh-fix-ci`, `yeet` |
| `gnupg` | optional; only needed for PGP/MIME email signing (send-email --sign) | gpg on PATH with your secret key in the keyring, e.g. apt-get install gnupg. | gpg.exe on PATH (Gpg4win), with your secret key imported. | `send-email` |
| `gpu-inspection-tools` | optional resource preflight enhancement | nvidia-smi for NVIDIA or rocm-smi for AMD when present. | nvidia-smi.exe or rocm-smi.exe when present; WSL GPU visibility depends on host driver support. | `get-available-resources` |
| `lake-cli` | optional for local Lean project checks; never installed by wrappers | Lake executable on PATH, via AAS_LAKE, or via an existing elan install. | Lake executable on PATH, via AAS_LAKE, or via an existing per-user elan install. | `lean-strict-verification-gate`, `lean-formalization-intake` |
| `lean-cli` | optional for local formal typechecking; never installed by wrappers | Lean 4 executable on PATH, via AAS_LEAN, or via an existing elan install. | Lean 4 executable on PATH, via AAS_LEAN, or via an existing per-user elan install. | `lean-strict-verification-gate` |
| `libreoffice` | optional PPTX renderer; not needed on Windows when Microsoft PowerPoint is installed | soffice/libreoffice on PATH, e.g. apt-get install libreoffice. | soffice.exe on PATH from a LibreOffice install. | `slides-to-video PPTX input` |
| `make-or-command-wrapper` | optional convenience entrypoint | make invokes installer commands. | make.bat invokes installer commands without requiring GNU Make. | `installation` |
| `manim-tex-runtime` | required for manim-math-animation rendering (heavier than plain tex-runtime) | LaTeX (texlive + texlive-latex-extra + cm-super) with dvisvgm and the standalone/preview packages, plus libcairo2-dev and libpango1.0-dev; e.g. apt-get install dvisvgm texlive texlive-latex-extra libcairo2-dev libpango1.0-dev. | MiKTeX/TeX Live providing latex + dvisvgm + standalone/preview; cairo/pango ship in the Manim Windows wheels. | `manim-math-animation` |
| `mathlib-cache` | optional manually prepared Lean dependency cache | Existing project-local mathlib cache or manually prepared Lake cache. | Existing project-local mathlib cache or manually prepared Lake cache. | `lean-strict-verification-gate`, `lean-formalization-intake` |
| `modal-cli` | optional until submit/deploy/wait/fetch are used | Installed by the modal Python package and authenticated with modal token set/new. | Installed into the agent virtualenv; wrappers add the venv Scripts directory to PATH. | `modal-research-compute` |
| `node-runtime` | required for Node-backed MCP servers and optional Zotero translation-server workflows | Node.js 18+ with npm. | Node.js 18+ with npm/npx; Windows Codex config uses npx for the sequential-thinking MCP server. | `Codex MCP`, `zotero translation server` |
| `ocr-runtime` | optional for scanned-document OCR | Tesseract with tessdata available; current Claude docling docs use TESSDATA_PREFIX=/usr/share/tessdata/. | Current Windows docling flow prefers rapidocr Python extras; Tesseract may be used through WSL if needed. | `docling` |
| `powershell-runtime` | required for Windows bootstrap and Windows wrapper execution | not required | PowerShell 5.1+ or PowerShell 7+. | `make.bat`, `installer bootstrap`, `Windows runtime wrappers` |
| `pptx-renderer` | optional, required only for PPTX input rendering | LibreOffice soffice/libreoffice on PATH, e.g. apt-get install libreoffice. | Microsoft PowerPoint from Microsoft Office via COM automation, or LibreOffice soffice.exe on PATH. | `slides-to-video PPTX input` |
| `python-runtime` | required for runtime-backed skills and the installer | Native Python 3.10+ detected from environment override, repo venv, python3, or python. | Native Python 3.10+ detected from environment override, repo venv, C:\Python3*, per-user Python installs, Program Files installs, py -3, python.exe, or python. | `installer`, `zotero`, `calibre`, `docling`, `get-available-resources`, `research-digest-wrapper`, `rss-news-digest`, `digest-bridge`, `tikz-draw`, `graph-verifier`, `submission-venue-selector`, `annotated-review`, `modal-research-compute`, `session-logs`, `lean-formalization-intake`, `lean-explore-mcp`, `lean-strict-verification-gate` |
| `ripgrep-cli` | optional but expected by local search/session workflows | rg on PATH. | rg.exe or rg on PATH. | `session-logs`, `research workflows`, `repo inspection` |
| `sagemath` | required for the sagemath skill and optional Sage-backed graph/TikZ workflows | Native executable via `AAS_SAGE`, `sage` on `PATH`, a local Sage install, or a Docker-backed wrapper that behaves like `sage` for `--version` and `-c` probes. | WSL-backed SageMath inside Ubuntu 24.04, detected through wsl.exe when runnable, current local WSL paths when precheck runs from WSL/Linux, mounted WSL rootfs paths when available, or an ext4.vhdx presence warning when the distro image is not inspectable. | `sagemath`, `tikz-draw`, `openclaw/source research math verification` |
| `tex-runtime` | required for TikZ compile checks and optional annotated-review LaTeX/PDF output | TeX Live or compatible distribution providing pdflatex, lualatex, or xelatex. | MiKTeX, TeX Live, or compatible distribution providing pdflatex.exe, lualatex.exe, or xelatex.exe. | `tikz-draw`, `annotated-review` |
| `uvx-cli` | optional manual command runner for AxiomMath AXLE MCP; never invoked by installer, precheck, or smoke | uvx on PATH for manual live AXLE MCP setup. | uvx.exe or uvx on PATH for manual live AXLE MCP setup. | `axiom-axle-mcp manual setup` |
| `wsl-runtime` | required when a Windows skill delegates to Linux-only tools | not applicable | Current SageMath flow uses direct WSL execution with an Ubuntu 24.04 distro. When precheck is run from WSL/Linux against a mounted Windows profile, the current local WSL filesystem is also inspected. Mounted rootfs directories are inspected when present; ext4.vhdx presence is reported as a degraded inspection gap because Sage inside the image cannot be verified without WSL, a local WSL filesystem, or a mounted rootfs. Docker is explicitly not required by the current Windows Sage config. | `sagemath`, `tikz-draw optional Sage graph mode` |

### Python Packages

| Package | Import | Requirement | Platforms | Used By |
|---|---|---|---|---|
| `Pillow` | `PIL` | Pillow>=10.2 | `linux`, `windows` | `slides-to-video` |
| `PyMuPDF` | `fitz` | pymupdf; tikz semantic verifier pins PyMuPDF==1.27.2.2 | `linux`, `windows` | `annotated-review`, `tikz-draw` |
| `PyPDF2` | `PyPDF2` | PyPDF2>=3.0.0 | `linux`, `windows` | `zotero`, `calibre` |
| `docling` | `docling` | docling>=2.88.0,<3 | `linux`, `windows` | `docling` |
| `docling-mcp` | `docling_mcp` | docling-mcp | `windows` | `docling MCP integration` |
| `ebooklib` | `ebooklib` | ebooklib>=0.18 | `linux`, `windows` | `calibre EPUB metadata` |
| `edge-tts` | `edge_tts` | edge-tts>=7,<8 | `linux`, `windows` | `slides-to-video` |
| `feedparser` | `feedparser` | feedparser | `linux`, `windows` | `research-digest-wrapper`, `rss-news-digest` |
| `google-api-python-client` | `googleapiclient` | google-api-python-client>=2.100.0 | `linux`, `windows` | `calibre Google Drive sync`, `zotero Google Drive helpers` |
| `google-auth` | `google.oauth2` | google-auth>=2.23.0 | `linux`, `windows` | `calibre Google Drive sync`, `zotero Google Drive helpers` |
| `kokoro` | `kokoro` | kokoro>=0.9.4 | `linux`, `windows` | `slides-to-video` |
| `lean-explore` | `lean_explore` | lean-explore | `linux`, `windows` | `lean-explore-mcp` |
| `local-getscipapers-helper` | `redacted` | optional local helper package with a maintainer-specific import name | `windows` | `zotero metadata fallback` |
| `manim` | `manim` | manim==0.20.1 on Python >=3.11; manim==0.19.1 on Python 3.10 | `linux`, `windows` | `manim-math-animation` |
| `modal` | `modal` | modal | `linux`, `windows` | `modal-research-compute` |
| `networkx` | `networkx` | networkx | `linux`, `windows`, `remote-modal` | `graph-verifier`, `modal-research-compute` |
| `numpy` | `numpy` | numpy==1.26.4 for TikZ semantic verifier; numpy in Modal CPU image | `linux`, `windows`, `remote-modal` | `tikz-draw`, `modal-research-compute` |
| `pdf2image` | `pdf2image` | pdf2image>=1.17 | `linux`, `windows` | `slides-to-video` |
| `pdfplumber` | `pdfplumber` | pdfplumber>=0.10.0 | `linux`, `windows` | `zotero` |
| `piper-tts` | `piper` | piper-tts>=1.2 | `linux`, `windows` | `slides-to-video` |
| `psutil` | `psutil` | psutil | `linux`, `windows` | `get-available-resources` |
| `pylatexenc` | `pylatexenc` | pylatexenc | `linux`, `windows` | `annotated-review` |
| `pypdfium2` | `pypdfium2` | pypdfium2>=4 | `linux`, `windows` | `docling OCR.space fallback PDF rendering` |
| `pytest` | `pytest` | pytest>=7.0.0 | `linux`, `windows` | `zotero test suite` |
| `python-pptx` | `pptx` | python-pptx>=1.0 | `linux`, `windows` | `slides-to-video` |
| `pyzotero` | `pyzotero` | pyzotero>=1.10.0 | `linux`, `windows` | `zotero` |
| `rapidocr` | `rapidocr` | docling[rapidocr] extra | `windows` | `docling OCR` |
| `requests` | `requests` | requests>=2.28.0 | `linux`, `windows` | `zotero`, `calibre`, `research-digest-wrapper` |
| `responses` | `responses` | responses>=0.23.0 | `linux`, `windows` | `zotero test suite` |
| `shapely` | `shapely` | shapely==2.1.2 for TikZ semantic verifier | `linux`, `windows` | `tikz-draw` |
| `soundfile` | `soundfile` | soundfile>=0.12 | `linux`, `windows` | `slides-to-video` |
| `svgelements` | `svgelements` | svgelements==1.9.6 for optional SVG parsing parity | `linux`, `windows` | `tikz-draw` |
| `tomli` | `tomli` | tomli>=2 on Python <3.11 for Docling TOML config parsing | `linux`, `windows` | `docling` |
| `torch` | `torch` | torch CPU wheel for Windows docling; torch in Modal GPU image | `windows`, `remote-modal` | `docling Windows setup`, `modal GPU jobs` |
| `torchvision` | `torchvision` | torchvision CPU wheel for Windows docling | `windows` | `docling Windows setup` |

### Node Packages

| Package | Requirement | Used By | Notes |
|---|---|---|---|
| `codewhale` | codewhale npm package; successor to deprecated deepseek-tui | `DeepSeek delegation provider CLI`, `DeepSeek target` | npm package metadata for codewhale and deepseek-tui rename. |
| `sequential-thinking-mcp` | @modelcontextprotocol/server-sequential-thinking via npx | `Windows Codex MCP sequentialThinking server` | Windows Codex config.toml. |
| `zotero-translation-server` | Vendored Zotero translation-server package.json | `zotero metadata translation` | Linux Codex Zotero translation-server-src/package.json. Runtime deps: `aws-sdk`, `config`, `iconv-lite`, `jsdom`, `koa`, `koa-bodyparser`, `koa-route`, `md5`, `request`, `request-promise-native`, `serverless-http`, `w3c-xmlserializer`, `wicked-good-xpath`, `xregexp`, `yargs` |

### Manual Integrations

| Integration | Description | Used By |
|---|---|---|
| `axiom-axle` | AxiomMath AXLE API key and MCP client configuration are configured manually outside this repo; helpers report presence only and never write config. | `axiom-axle-mcp` |
| `github` | GitHub app/CLI authentication is configured outside this repo. | `github`, `gh-fix-ci`, `yeet` |
| `github-copilot-cli` | Copilot CLI account, provider, and model entitlement state is detected as target precheck metadata when the Copilot target is detected or selected; known credential sources are reported by presence rather than value, config secret values are not read, and command arguments/version output are redacted. | `copilot target` |
| `google-drive` | Google Drive service-account or OAuth credentials are configured outside this repo. | `calibre`, `zotero optional Google Drive helpers` |
| `leanexplore` | LeanExplore API key, MCP client configuration, and local search data are configured manually outside this repo; helpers report presence only and never write config or download data. | `lean-explore-mcp` |
| `modal` | Modal token file and workspace credentials are configured outside this repo. | `modal-research-compute` |
| `provider-configs` | OpenAI, Claude, DeepSeek, Copilot, and other provider auth/config files are intentionally excluded. | `agent frontends` |
| `zotero` | Zotero API/library/WebDAV credentials are configured outside this repo. | `zotero`, `annotated-review`, `paper-review` |

### Windows Substrate Notes

- SageMath is WSL-backed on the inspected Windows config, not native Windows.
- Docling OCR is Windows-native through rapidocr extras; Tesseract is a Linux/WSL option.
- Docker is explicitly not required by the inspected Windows Sage config.
- Windows wrapper commands may use PowerShell, .bat launchers, native Python venvs, and WSL in the same workflow.


## Docling And OCR Runtime Notes

The managed Docling runtime is local-only by default. The wrappers
accept local paths for `doctor`, `convert`, `extract`, `chunk`, and `quality`,
load Docling lazily after argument/config validation, and reject URL
sources, network-style paths, HTML/Markdown inputs with remote assets,
remote service config fields, provider URLs, API tokens, and OCR.space
settings in config files.

Useful local OCR controls include:

- `--preset local-accurate` for normal high-quality local parsing.
- `--preset scan-heavy` for stronger scanned/image-backed paper OCR.
- `--ocr-mode never|auto|always` and `--force-full-page-ocr`.
- `--ocr-engine auto|easyocr|ocrmac|rapidocr|tesseract|tesserocr`.
- `--table-mode fast|accurate`, `--page-range`, `--max-num-pages`,
  and `--max-file-size` for bounded conversions.
- `quality --source <pdf>` to score local extraction quality before fallback.

Config discovery order is `--config`, `AAS_DOCLING_CONFIG`,
`DOCLING_CONFIG`, `$AAS_RUNTIME_WORKSPACE/config/docling.toml`, and
`$OPENCLAW_WORKSPACE/config/docling.toml` only when
`--allow-openclaw-config` is passed. `docling.example.toml` is the
tracked template; live configs, caches, downloaded PDFs, and runtime
data are not promoted into the managed runtime manifest.

OCR.space is available only as an explicit remote fallback: pass
`--ocr-fallback ocrspace --allow-remote-ocr` to `convert`, provide
`OCRSPACE_API_KEY` or `OCR_SPACE_API_KEY`, and use `--ocr-audit-output`
when you need an upload/quality audit. The fallback runs only when local
Docling conversion fails or the quality gate degrades. Splitting a PDF
into one image per page may help satisfy per-request size or timeout
limits, but it does not bypass account-level quota, rate, or concurrency
limits. The adapter redacts secrets and uses OCR Engine 3 for paper
extraction quality.

Live OCR.space smoke is separate from default post-install smoke. Run
`ocrspace-smoke --allow-remote-ocr` only when a real OCR.space key is
configured and a live remote request is acceptable. The command
generates and uploads a synthetic one-page PDF rather than user data.

## Slides-To-Video Runtime Notes

`slides-to-video` turns prepared slides (PNG/PDF/PPTX) into a narrated,
captioned MP4 using only free tools. `ffmpeg` is the one required system
tool (use an LGPL build); `espeak-ng` is needed for offline TTS and
Microsoft PowerPoint on Windows or LibreOffice elsewhere is needed only
for PPTX input. Python packages install into a dedicated
venv at `~/.local/share/slides-to-video-venv` via the `setup` subcommand;
the wrappers use `S2V_PYTHON` first, then auto-select that venv, then
fall back to `AAS_RUNTIME_PYTHON`.

Install the system tools first, then run `setup`, then `doctor`:
Debian/Ubuntu `sudo apt-get install ffmpeg espeak-ng libreoffice`,
Fedora `sudo dnf install ffmpeg espeak-ng libreoffice`, macOS
`brew install ffmpeg espeak-ng` (LibreOffice via cask), Windows
`winget install Gyan.FFmpeg eSpeak-NG.eSpeak-NG`. Microsoft Office
PowerPoint satisfies PPTX input on Windows; `espeak-ng` is only for
offline TTS.

It runs a three-phase, human-in-the-loop flow: `analyze` (ingest slides),
`draft` then `verbalize` (per-slide spoken transcript, math read aloud),
and `render` -- which is blocked until `approve` pins the transcript SHA,
and re-blocks automatically if the transcript changes afterward.

Timing is duration-driven: each slide's narration is synthesized,
normalized to WAV, measured with `ffprobe`, and its clip is set to exactly
that length, then clips concatenate losslessly. Captions (SRT + VTT) are
engine-agnostic and re-based on the same measured durations.

TTS uses a language-aware ladder: edge-tts (online, best) then offline
Kokoro/Piper, dropping engines without a voice for the language -- e.g.
Vietnamese routes to edge-tts `vi-VN` then Piper `vi_VN`, never Kokoro.
English and Vietnamese ship tuned lexicons (voices + spoken math); other
languages are supported generically via live edge-tts voice enumeration.
Tier-1 effects (Ken Burns, highlight, spotlight, laser, reveal) run as
ffmpeg filters on the slide pixels and need no slide source. A slide's
visual can also be a pre-rendered video clip (set `clip_path`, or insert
one with `add-interlude`) -- this is how a manim-math-animation clip is
mixed into a narrated deck: the segment runs to max(clip, narration),
narration stays in this skill's TTS ladder, and the concat stays drift-free.

The default `selftest` smoke is offline: it validates the deterministic
core (pairing, re-basing, the engine ladder, verbalization, effect
filtergraph building, captions, clip args, and the approval gate) with no
network, package install, ffmpeg, or TTS. Run `doctor` to report whether
ffmpeg, fonts, and the venv packages are present before a real render.

## Manim Math Animation Runtime Notes

`manim-math-animation` is the optional Manim companion to slides-to-video.
From a JSON scene spec (equations + optional title + emphasis) it generates
a Manim scene and renders a SILENT clip normalized to the slides-to-video
canonical profile (resolution, fps, yuv420p, silent 48 kHz stereo AAC), so
the clip splices into a deck without re-encoding. Narration stays in
slides-to-video (one timing owner per segment); Manim is rendered silent.

Animations: Write of typeset equations (handwriting feel),
TransformMatchingTex morphing between steps, and Indicate/Circumscribe/
Flash/Wiggle emphasis. Vietnamese/other-script prose uses the spec `title`
via Pango Text + a Unicode font; math (MathTex) is language-neutral.

Manim is heavier than the default smoke contract: it needs a LaTeX distro
with dvisvgm + the standalone/preview packages + cm-super, the cairo/pango
dev libraries, and ffmpeg (the `manim-tex-runtime` system dependency), plus
Manim CE in a dedicated venv at `~/.local/share/manim-math-animation-venv`
created by `setup`. The default `selftest` smoke is offline and presence-
free: it validates the scene-spec round-trip, the generated Manim source,
and the manim/ffmpeg argv builders with no Manim, LaTeX, or ffmpeg. Run
`doctor` to confirm the render toolchain before `render`; a real render is
intentionally not part of default CI smoke.

## Detection Notes

Python package checks use root-relative candidate sets, including
agent virtualenvs, user-local site-package directories, Codex runtime
site-package directories, dedicated Docling environments, official
Windows Python install roots, and per-user Windows package directories.
When inspecting a mounted Windows home from Linux, `precheck` can
verify package markers in `site-packages`, find common TeX Live and
MiKTeX install roots, detect Sage in the current WSL/Linux
filesystem, and detect mounted WSL rootfs Sage paths or WSL VHDX
presence. It still marks native Windows executables as
present-unverified instead of trying to execute them.

Related pages: [Installation](installation.md), [Windows](windows.md),
[Linux](linux.md), [Troubleshooting](troubleshooting.md).
