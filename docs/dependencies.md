# Dependencies

| Logical Tool | Description |
|---|---|
| `calibre-cli` | Calibre command line tools for ebook metadata and conversion. |
| `node-runtime` | Node runtime for MCP servers that use npx. |
| `ocr-runtime` | OCR command line runtime, normally Tesseract. |
| `powershell-runtime` | PowerShell runtime for Windows bootstrap and argument forwarding. |
| `python-runtime` | Python runtime with venv, pip, and ssl support. |
| `sage-runtime` | SageMath runtime, local on Linux or WSL-backed on Windows. |
| `tex-runtime` | TeX engine for TikZ compile checks. |
| `wsl-runtime` | Windows Subsystem for Linux runtime. |

## Packages And Services

| Dependency | Type | Detail |
|---|---|---|
| `calibre-cli` | `tool` | calibre-cli |
| `docling-python-package` | `python` | docling |
| `modal-auth` | `remote-service` | remote-service |
| `networkx-python-package` | `python` | networkx |
| `ocr-runtime` | `tool` | ocr-runtime |
| `zotero-credentials` | `remote-service` | remote-service |

Dependencies are declared as logical capabilities rather than personal
paths. `precheck` resolves them from environment overrides, repo-local
runtimes, `PATH`, native Windows commands, Python imports, remote-service
placeholders, and WSL-backed commands where appropriate.
