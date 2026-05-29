---
name: docling
description: Use when the user wants to parse, convert, chunk, or structurally analyze PDFs, DOCX, PPTX, HTML, images, audio transcripts, or similar documents with Docling. Prefer this skill for local document parsing before ad hoc text extraction.
metadata:
  short-description: Local document intelligence via Docling
---

# Docling


## Windows Runtime Commands

On native Windows, use the managed Windows runner and the native runtime command target. For Codex-only installs the runtime is usually `%USERPROFILE%\.codex\runtime`; for multi-agent installs it is usually `%LOCALAPPDATA%\ai-agents-skills\runtime`. Set `$runtime` to the installed runtime root, then run:

```powershell
$runtime = if ($env:AAS_RUNTIME_ROOT) { $env:AAS_RUNTIME_ROOT } elseif (Test-Path "$env:USERPROFILE\.codex\runtime") { "$env:USERPROFILE\.codex\runtime" } else { "$env:LOCALAPPDATA\ai-agents-skills\runtime" }
& "$runtime\run_skill.bat" "skills/docling/run_docling.bat" <args>
& "$runtime\run_skill.bat" "skills/docling/run_docling.ps1" <args>
```

POSIX examples below use `run_skill.sh` and `.sh` command targets; use the Windows command target above on native Windows.

Use this skill for high-quality local document parsing and structured export.

## When to use

Use this skill when the user wants to:

- parse a PDF, DOCX, PPTX, HTML page, image, or similar document
- convert a document to Markdown, JSON, HTML, or plain text
- extract tables, headings, figures, formulas, or reading order
- chunk a document for RAG or downstream indexing
- inspect document structure before review or synthesis
- handle OCR-heavy or layout-heavy documents more robustly than plain text extraction

For paper retrieval, keep the existing routing order:

- `zotero` first
- `calibre` second for review tasks needing the document
- online fallback only after those library checks

Docling is the parsing layer **after** you have the document.

## Base paths

Skill docs:

- `~/.codex/skills/docling/`

Runtime files:

- `~/.codex/runtime/workspace/skills/docling/`

Installed Docling environment:

- `~/.local/share/docling-venv/`
- CLI: `~/.local/share/docling-venv/bin/docling`
- Python packages: `~/.local/share/docling-venv/lib/python3.10/site-packages/`

Shared runtime runner:

```bash
bash ~/.codex/runtime/run_skill.sh skills/docling/run_docling.sh <subcommand> [args...]
```

The runtime launcher currently delegates Docling execution to the dedicated
virtualenv above rather than a package copy under `~/.codex/runtime/workspace/.local/`.

## Supported runtime subcommands

### Doctor

```bash
bash ~/.codex/runtime/run_skill.sh skills/docling/run_docling.sh doctor
```

Checks whether Python imports and the `docling` CLI are available.
In this setup, that check should resolve against `~/.local/share/docling-venv`.

### Convert

```bash
bash ~/.codex/runtime/run_skill.sh skills/docling/run_docling.sh convert   --source "/path/to/file.pdf"   --to md
```

```bash
bash ~/.codex/runtime/run_skill.sh skills/docling/run_docling.sh convert   --source "/path/to/file.pdf"   --to json   --preset scan-heavy
```

Useful local quality controls:

- `--preset local-accurate`: default high-quality local parsing
- `--preset scan-heavy`: stronger OCR for scanned/image-heavy PDFs
- `--ocr-mode never|auto|always`
- `--ocr-engine auto|easyocr|ocrmac|rapidocr|tesseract|tesserocr`
- `--ocr-lang <lang>` repeated for multiple languages
- `--force-full-page-ocr`
- `--table-mode fast|accurate`
- `--page-range 1-8`
- `--max-num-pages <n>`
- `--output <path> --overwrite`

Optional remote fallback is explicit and never enabled by config:

```bash
bash ~/.codex/runtime/run_skill.sh skills/docling/run_docling.sh convert \
  --source "/path/to/file.pdf" \
  --to md \
  --preset scan-heavy \
  --ocr-fallback ocrspace \
  --allow-remote-ocr \
  --ocr-audit-output "/path/to/file.ocr-audit.json"
```

This runs local Docling first, evaluates extracted-text quality, and uploads
selected local PDF pages to OCR.space only if local conversion fails or the
quality gate degrades. It requires an OCR.space API key in `OCRSPACE_API_KEY`
or `OCR_SPACE_API_KEY`.

Live OCR.space smoke is also explicit and uses a generated synthetic PDF page,
not a user document:

```bash
bash ~/.codex/runtime/run_skill.sh skills/docling/run_docling.sh ocrspace-smoke \
  --allow-remote-ocr
```

This command is not part of default post-install smoke because it requires a
real API key and a live remote request.

### Analyze structure

```bash
bash ~/.codex/runtime/run_skill.sh skills/docling/run_docling.sh extract   --source "/path/to/file.pdf"
```

Emits JSON with counts and basic structural signals such as headings, tables, pictures, and pages.
The same `--config`, `--preset`, OCR, table, page, and limit options are accepted.

### OCR quality

```bash
bash ~/.codex/runtime/run_skill.sh skills/docling/run_docling.sh quality \
  --source "/path/to/file.pdf" \
  --preset scan-heavy
```

Reports a local quality score, characters/words per page, alphanumeric ratio,
replacement-character ratio, and reasons that would trigger fallback.

### Chunk

```bash
bash ~/.codex/runtime/run_skill.sh skills/docling/run_docling.sh chunk   --source "/path/to/file.pdf"   --mode hierarchical
```

The same `--config`, `--preset`, OCR, table, page, and limit options are accepted.

## Recommended settings

### Local-only policy

The managed runtime is local-only by default:

- document sources must be local paths, not URLs or network shares
- HTML/Markdown inputs with remote assets are rejected before conversion
- remote service fields such as endpoints, provider URLs, tokens, and OCR.space settings are rejected in config
- Docling pipeline options force `enable_remote_services=False`
- `vlm-local` requires a local `DOCLING_ARTIFACTS_PATH` or `artifacts_path`

OCR.space is available only as an explicit fallback path. It must be requested
with both `--ocr-fallback ocrspace` and `--allow-remote-ocr`; Docling config
files still cannot contain API keys, endpoints, OCR.space fields, or provider
URLs. The adapter uses OCR Engine 3 for paper extraction quality. Page-by-page
splitting can satisfy per-request size/timeout constraints, but it does not
remove account-level rate, quota, or concurrency limits.

### Config file

Pass a config explicitly:

```bash
bash ~/.codex/runtime/run_skill.sh skills/docling/run_docling.sh convert \
  --source "/path/to/file.pdf" \
  --config "/path/to/docling.toml" \
  --preset scan-heavy
```

Discovery order is:

1. `--config`
2. `AAS_DOCLING_CONFIG`
3. `DOCLING_CONFIG`
4. `$AAS_RUNTIME_WORKSPACE/config/docling.toml`
5. `$OPENCLAW_WORKSPACE/config/docling.toml` only with `--allow-openclaw-config`

Use `docling.example.toml` in the runtime skill directory as the starting template.

### Environment variables

Docling supports these environment variables directly or indirectly:

- `DOCLING_ARTIFACTS_PATH`
- `DOCLING_PERF_PAGE_BATCH_SIZE`
- `DOCLING_PERF_DOC_BATCH_SIZE`
- `DOCLING_PERF_DOC_BATCH_CONCURRENCY`
- `DOCLING_INFERENCE_COMPILE_TORCH_MODELS`
- `DOCLING_DEVICE`
- `DOCLING_NUM_THREADS`
- `OMP_NUM_THREADS`
- `AAS_DOCLING_CONFIG`
- `AAS_DOCLING_PRESET`
- `OCRSPACE_API_KEY` or `OCR_SPACE_API_KEY` for explicit OCR.space fallback

Use `DOCLING_ARTIFACTS_PATH` when models are prefetched or when you want offline behavior.

### Pipeline choices

- `standard` pipeline: default for born-digital PDFs and CPU-friendly conversions
- `vlm` pipeline: for harder layouts, handwriting, formulas, or image-heavy pages; requires local artifacts

### Important options

- OCR: `do_ocr`
- OCR mode: `never`, `auto`, or `always`
- local OCR engine: `auto`, `easyocr`, `rapidocr`, `tesseract`, `tesserocr`, or `ocrmac`
- tables: `do_table_structure`
- table matching: `table_structure_options.do_cell_matching`
- table mode: `FAST` vs `ACCURATE`
- document timeout: `document_timeout`
- page slicing: `page_range`
- file/page limits: `max_num_pages`, `max_file_size`
- remote inference gating: `enable_remote_services`
- enrichments:
  - `do_code_enrichment`
  - `do_formula_enrichment`
  - `do_picture_classification`
  - `do_picture_description`

## Safety notes

- Prefer local models and local parsing by default.
- Do not place API keys, endpoints, OCR.space fields, or provider URLs in Docling config.
- Use OCR.space only through explicit fallback flags when the user accepts remote page upload.
- For review workflows, use Docling for parsing but keep review judgment in `paper-review` or `annotated-review`.

## Integration guidance

- `source-research`: use this skill for local PDF/document parsing before ad hoc extraction.
- `paper-review`: prefer this skill when a retrieved PDF/book file is available.
- `annotated-review`: use this skill for structural extraction before annotation/review when helpful.

## Supporting references

Open these only when relevant:

- `references/pipelines.md`
- `references/settings.md`
- `references/chunking.md`
- `references/remote-services.md`
