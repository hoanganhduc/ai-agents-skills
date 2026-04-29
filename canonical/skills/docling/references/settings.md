# Docling settings

Important environment variables and settings to expose in workflows:

- `DOCLING_ARTIFACTS_PATH`
- `DOCLING_PERF_PAGE_BATCH_SIZE`
- `DOCLING_PERF_DOC_BATCH_SIZE`
- `DOCLING_PERF_DOC_BATCH_CONCURRENCY`
- `DOCLING_INFERENCE_COMPILE_TORCH_MODELS`
- `DOCLING_DEVICE`
- `DOCLING_NUM_THREADS`
- `OMP_NUM_THREADS`

Important pipeline options:

- `do_ocr`
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
