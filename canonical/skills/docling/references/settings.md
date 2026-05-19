# Docling settings

The managed runtime keeps Docling local-only by default. Use local files,
local OCR engines, and local model artifacts. Remote service fields, OCR.space
settings, endpoints, provider URLs, and secret-bearing keys are rejected by the
runtime config loader.

OCR.space is Phase 2 only. A future online adapter must be explicit, account
for request size/rate limits, and use OCR Engine 3 for paper extraction quality.
Splitting a PDF into one image per page can help with per-request size and
timeout behavior, but it does not bypass account-level quota, rate, or
concurrency limits.

Important environment variables and settings to expose in workflows:

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

Important pipeline options:

- `do_ocr`
- `ocr_mode`
- `ocr_engine`
- `ocr_lang`
- `force_full_page_ocr`
- `do_table_structure`
- `table_structure_options.do_cell_matching`
- `table_structure_options.mode`
- `document_timeout`
- `page_range`
- `max_num_pages`
- `max_file_size`
- `enable_remote_services`
- `do_code_enrichment`
- `do_formula_enrichment`
- `do_picture_classification`
- `do_picture_description`
