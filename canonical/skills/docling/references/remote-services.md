# Docling remote services

Remote services are not enabled in the normal managed runtime path.

The runtime config loader rejects endpoints, provider URLs, OCR.space fields,
tokens, and other secret-bearing remote settings. It also forces Docling
pipeline options to `enable_remote_services=False`.

OCR.space is the only supported online OCR fallback, and only through explicit
CLI flags:

```bash
bash ~/.codex/runtime/run_skill.sh skills/docling/run_docling.sh convert \
  --source "/path/to/file.pdf" \
  --ocr-fallback ocrspace \
  --allow-remote-ocr
```

Use online OCR only when:

- local models are too slow or unavailable
- the user explicitly wants remote inference
- the environment already provides a trusted compatible endpoint
- request limits, retry behavior, redaction, and cost controls are specified

The OCR.space adapter uses OCR Engine 3 for paper extraction quality. Splitting
PDFs into one image per page can make requests smaller and recoverable per
page, but it cannot bypass account-level quota, rate, or concurrency limits.

Live smoke testing is available through a separate explicit command:

```bash
bash ~/.codex/runtime/run_skill.sh skills/docling/run_docling.sh ocrspace-smoke \
  --allow-remote-ocr
```

The smoke command generates a synthetic one-page PDF and uploads only that
generated page. It is intentionally excluded from default post-install runtime
smoke because normal smoke contracts are offline and forbid live API calls,
real secrets, and network access.
