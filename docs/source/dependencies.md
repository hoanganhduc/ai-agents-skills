# Dependencies

| Logical Tool | Description |
|---|---|
| `calibre-cli` | Calibre command line tools for ebook metadata and conversion. |
| `git-cli` | Git command line client for repository-backed workflows and GitHub publishing. |
| `github-cli` | GitHub CLI for workflows that need local Actions, PR, issue, or authentication commands. |
| `node-runtime` | Node runtime for MCP servers that use npx. |
| `nvidia-smi-tool` | NVIDIA GPU inspection tool used by resource preflight when available. |
| `ocr-runtime` | OCR command line runtime, normally Tesseract. |
| `powershell-runtime` | PowerShell runtime for Windows bootstrap and argument forwarding. |
| `python-runtime` | Python runtime with venv, pip, and ssl support. |
| `ripgrep-cli` | ripgrep command line search used by local research and session-inspection workflows. |
| `rocm-smi-tool` | AMD ROCm GPU inspection tool used by resource preflight when available. |
| `sage-runtime` | SageMath runtime, local on Linux or WSL-backed on Windows. |
| `tex-runtime` | TeX engine for TikZ compile checks. |
| `wsl-runtime` | Windows Subsystem for Linux runtime. |

## Packages And Services

| Dependency | Type | Detail |
|---|---|---|
| `calibre-cli` | `tool` | calibre-cli |
| `docling-mcp-python-package` | `python` | docling_mcp |
| `docling-python-package` | `python` | docling |
| `ebooklib-python-package` | `python` | ebooklib |
| `feedparser-python-package` | `python` | feedparser |
| `git-cli` | `tool` | git-cli |
| `github-cli` | `tool` | github-cli |
| `google-api-python-client-package` | `python` | googleapiclient |
| `google-auth-python-package` | `python` | google.oauth2 |
| `modal-auth` | `remote-service` | remote-service |
| `modal-python-package` | `python` | modal |
| `networkx-python-package` | `python` | networkx |
| `numpy-python-package` | `python` | numpy |
| `nvidia-smi-tool` | `tool` | nvidia-smi-tool |
| `ocr-runtime` | `tool` | ocr-runtime |
| `pdfplumber-python-package` | `python` | pdfplumber |
| `psutil-python-package` | `python` | psutil |
| `pylatexenc-python-package` | `python` | pylatexenc |
| `pymupdf-python-package` | `python` | fitz |
| `pypdf2-python-package` | `python` | PyPDF2 |
| `pytest-python-package` | `python` | pytest |
| `pyzotero-python-package` | `python` | pyzotero |
| `rapidocr-python-package` | `python` | rapidocr |
| `requests-python-package` | `python` | requests |
| `responses-python-package` | `python` | responses |
| `ripgrep-cli` | `tool` | ripgrep-cli |
| `rocm-smi-tool` | `tool` | rocm-smi-tool |
| `shapely-python-package` | `python` | shapely |
| `svgelements-python-package` | `python` | svgelements |
| `torch-python-package` | `python` | torch |
| `torchvision-python-package` | `python` | torchvision |
| `zotero-credentials` | `remote-service` | remote-service |

## Current Linux And Windows Config Inventory

This sanitized inventory is derived from the maintainer's current Linux
and Windows Codex, Claude, and DeepSeek configs. It intentionally excludes
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

### Extra Software

| Software | Requirement | Linux | Windows | Used By |
|---|---|---|---|---|
| `bash-or-posix-shell` | required on Linux and inside WSL-backed Windows flows | Used by shared runtime runner and shell skill wrappers. | Used through WSL for SageMath and other Linux-substrate commands. | `runtime wrappers`, `sagemath`, `make` |
| `calibre-cli` | optional for richer ebook library operations | calibredb and ebook-convert on PATH. | calibredb.exe and ebook-convert.exe on PATH. | `calibre`, `vnthuquan` |
| `docling-cli` | optional CLI layer for docling workflows | Current Claude docs use <LINUX_HOME>/.local/share/docling-venv/bin/docling. | Current Claude docs use <WINDOWS_HOME>/.venv-docling/Scripts/docling.exe. | `docling` |
| `git-cli` | required for repository workflows and publishing | git on PATH. | git.exe or git on PATH. | `GitHub workflows`, `repo install/update examples` |
| `github-cli` | optional for GitHub workflows that need local gh commands | gh on PATH with auth configured when needed. | gh.exe or gh on PATH with auth configured when needed. | `github`, `gh-fix-ci`, `yeet` |
| `gpu-inspection-tools` | optional resource preflight enhancement | nvidia-smi for NVIDIA or rocm-smi for AMD when present. | nvidia-smi.exe or rocm-smi.exe when present; WSL GPU visibility depends on host driver support. | `get-available-resources` |
| `make-or-command-wrapper` | optional convenience entrypoint | make invokes installer commands. | make.bat invokes installer commands without requiring GNU Make. | `installation` |
| `modal-cli` | optional until submit/deploy/wait/fetch are used | Installed by the modal Python package and authenticated with modal token set/new. | Installed into the agent virtualenv; wrappers add the venv Scripts directory to PATH. | `modal-research-compute` |
| `node-runtime` | required for Node-backed MCP servers and optional Zotero translation-server workflows | Node.js 18+ with npm. | Node.js 18+ with npm/npx; Windows Codex config uses npx for the sequential-thinking MCP server. | `Codex MCP`, `zotero translation server` |
| `ocr-runtime` | optional for scanned-document OCR | Tesseract with tessdata available; current Claude docling docs use TESSDATA_PREFIX=/usr/share/tessdata/. | Current Windows docling flow prefers rapidocr Python extras; Tesseract may be used through WSL if needed. | `docling` |
| `powershell-runtime` | required for Windows bootstrap and Windows wrapper execution | not required | PowerShell 5.1+ or PowerShell 7+. | `make.bat`, `installer bootstrap`, `Windows runtime wrappers` |
| `python-runtime` | required for runtime-backed skills and the installer | Native Python 3.10+ detected from environment override, repo venv, python3, or python. | Native Python 3.10+ detected from environment override, repo venv, py -3, python.exe, or python. | `installer`, `zotero`, `calibre`, `docling`, `get-available-resources`, `research-digest-wrapper`, `rss-news-digest`, `digest-bridge`, `tikz-draw`, `graph-verifier`, `annotated-review`, `modal-research-compute`, `session-logs` |
| `ripgrep-cli` | optional but expected by local search/session workflows | rg on PATH. | rg.exe or rg on PATH. | `session-logs`, `research workflows`, `repo inspection` |
| `sagemath` | required for the sagemath skill and optional Sage-backed graph/TikZ workflows | Native executable via SAGE_BIN, sage on PATH, or a local Sage install. | WSL-backed SageMath inside Ubuntu 24.04. | `sagemath`, `tikz-draw`, `openclaw/source research math verification` |
| `tex-runtime` | required for TikZ compile checks and optional annotated-review LaTeX/PDF output | TeX Live or compatible distribution providing pdflatex, lualatex, or xelatex. | MiKTeX, TeX Live, or compatible distribution providing pdflatex.exe, lualatex.exe, or xelatex.exe. | `tikz-draw`, `annotated-review` |
| `wsl-runtime` | required when a Windows skill delegates to Linux-only tools | not applicable | Current SageMath flow uses direct WSL execution with an Ubuntu 24.04 distro. Docker is explicitly not required by the current Windows Sage config. | `sagemath`, `tikz-draw optional Sage graph mode` |

### Python Packages

| Package | Import | Requirement | Platforms | Used By |
|---|---|---|---|---|
| `PyMuPDF` | `fitz` | pymupdf; tikz semantic verifier pins PyMuPDF==1.27.2.2 | `linux`, `windows` | `annotated-review`, `tikz-draw` |
| `PyPDF2` | `PyPDF2` | PyPDF2>=3.0.0 | `linux`, `windows` | `zotero`, `calibre` |
| `docling` | `docling` | docling>=2.88.0,<3 | `linux`, `windows` | `docling` |
| `docling-mcp` | `docling_mcp` | docling-mcp | `windows` | `docling MCP integration` |
| `ebooklib` | `ebooklib` | ebooklib>=0.18 | `linux`, `windows` | `calibre EPUB metadata` |
| `feedparser` | `feedparser` | feedparser | `linux`, `windows` | `research-digest-wrapper`, `rss-news-digest` |
| `google-api-python-client` | `googleapiclient` | google-api-python-client>=2.100.0 | `linux`, `windows` | `calibre Google Drive sync`, `zotero Google Drive helpers` |
| `google-auth` | `google.oauth2` | google-auth>=2.23.0 | `linux`, `windows` | `calibre Google Drive sync`, `zotero Google Drive helpers` |
| `local-getscipapers-helper` | `redacted` | optional local helper package with a maintainer-specific import name | `windows` | `zotero metadata fallback` |
| `modal` | `modal` | modal | `linux`, `windows` | `modal-research-compute` |
| `networkx` | `networkx` | networkx | `linux`, `windows`, `remote-modal` | `graph-verifier`, `modal-research-compute` |
| `numpy` | `numpy` | numpy==1.26.4 for TikZ semantic verifier; numpy in Modal CPU image | `linux`, `windows`, `remote-modal` | `tikz-draw`, `modal-research-compute` |
| `pdfplumber` | `pdfplumber` | pdfplumber>=0.10.0 | `linux`, `windows` | `zotero` |
| `psutil` | `psutil` | psutil | `linux`, `windows` | `get-available-resources` |
| `pylatexenc` | `pylatexenc` | pylatexenc | `linux`, `windows` | `annotated-review` |
| `pytest` | `pytest` | pytest>=7.0.0 | `linux`, `windows` | `zotero test suite` |
| `pyzotero` | `pyzotero` | pyzotero>=1.10.0 | `linux`, `windows` | `zotero` |
| `rapidocr` | `rapidocr` | docling[rapidocr] extra | `windows` | `docling OCR` |
| `requests` | `requests` | requests>=2.28.0 | `linux`, `windows` | `zotero`, `calibre`, `research-digest-wrapper` |
| `responses` | `responses` | responses>=0.23.0 | `linux`, `windows` | `zotero test suite` |
| `shapely` | `shapely` | shapely==2.1.2 for TikZ semantic verifier | `linux`, `windows` | `tikz-draw` |
| `svgelements` | `svgelements` | svgelements==1.9.6 for optional SVG parsing parity | `linux`, `windows` | `tikz-draw` |
| `torch` | `torch` | torch CPU wheel for Windows docling; torch in Modal GPU image | `windows`, `remote-modal` | `docling Windows setup`, `modal GPU jobs` |
| `torchvision` | `torchvision` | torchvision CPU wheel for Windows docling | `windows` | `docling Windows setup` |

### Node Packages

| Package | Requirement | Used By | Notes |
|---|---|---|---|
| `sequential-thinking-mcp` | @modelcontextprotocol/server-sequential-thinking via npx | `Windows Codex MCP sequentialThinking server` | Windows Codex config.toml. |
| `zotero-translation-server` | Vendored Zotero translation-server package.json | `zotero metadata translation` | Linux Codex Zotero translation-server-src/package.json. Runtime deps: `aws-sdk`, `config`, `iconv-lite`, `jsdom`, `koa`, `koa-bodyparser`, `koa-route`, `md5`, `request`, `request-promise-native`, `serverless-http`, `w3c-xmlserializer`, `wicked-good-xpath`, `xregexp`, `yargs` |

### Manual Integrations

| Integration | Description | Used By |
|---|---|---|
| `github` | GitHub app/CLI authentication is configured outside this repo. | `github`, `gh-fix-ci`, `yeet` |
| `google-drive` | Google Drive service-account or OAuth credentials are configured outside this repo. | `calibre`, `zotero optional Google Drive helpers` |
| `modal` | Modal token file and workspace credentials are configured outside this repo. | `modal-research-compute` |
| `provider-configs` | OpenAI, Claude, DeepSeek, and other provider auth/config files are intentionally excluded. | `agent frontends` |
| `zotero` | Zotero API/library/WebDAV credentials are configured outside this repo. | `zotero`, `annotated-review`, `paper-review` |

### Windows Substrate Notes

- SageMath is WSL-backed on the inspected Windows config, not native Windows.
- Docling OCR is Windows-native through rapidocr extras; Tesseract is a Linux/WSL option.
- Docker is explicitly not required by the inspected Windows Sage config.
- Windows wrapper commands may use PowerShell, .bat launchers, native Python venvs, and WSL in the same workflow.


Dependencies are declared as logical capabilities rather than personal
paths. `precheck` resolves them from environment overrides, repo-local
runtimes, `PATH`, native Windows commands, Python imports, remote-service
placeholders, and WSL-backed commands where appropriate.
