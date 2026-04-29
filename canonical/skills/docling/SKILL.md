---
name: docling
description: Use when the user wants to parse, convert, chunk, or structurally analyze PDFs, DOCX, PPTX, HTML, images, audio transcripts, or similar documents with Docling. Prefer this skill for local document parsing before ad hoc text extraction.
metadata:
  short-description: Local document intelligence via Docling
---

# Docling

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
bash ~/.codex/runtime/run_skill.sh skills/docling/run_docling.sh convert   --source "/path/to/file.pdf"   --to json   --pipeline standard
```

### Analyze structure

```bash
bash ~/.codex/runtime/run_skill.sh skills/docling/run_docling.sh extract   --source "/path/to/file.pdf"
```

Emits JSON with counts and basic structural signals such as headings, tables, pictures, and pages.

### Chunk

```bash
bash ~/.codex/runtime/run_skill.sh skills/docling/run_docling.sh chunk   --source "/path/to/file.pdf"   --mode hierarchical
```

## Recommended settings

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

Use `DOCLING_ARTIFACTS_PATH` when models are prefetched or when you want offline behavior.

### Pipeline choices

- `standard` pipeline: default for born-digital PDFs and CPU-friendly conversions
- `vlm` pipeline: for harder layouts, handwriting, formulas, or image-heavy pages

### Important options

- OCR: `do_ocr`
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
- Only use remote inference or API-backed vision models when explicitly needed.
- Treat `enable_remote_services=True` as an intentional opt-in.
- For review workflows, use Docling for parsing but keep review judgment in `paper-review` or `annotated-review`.

## Integration guidance

- `openclaw-research`: use this skill for local PDF/document parsing before ad hoc extraction.
- `paper-review`: prefer this skill when a retrieved PDF/book file is available.
- `annotated-review`: use this skill for structural extraction before annotation/review when helpful.

## Supporting references

Open these only when relevant:

- `references/pipelines.md`
- `references/settings.md`
- `references/chunking.md`
- `references/remote-services.md`
