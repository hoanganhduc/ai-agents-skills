# Docling remote services

Remote services are not enabled in the managed Phase 1 runtime.

The runtime config loader rejects endpoints, provider URLs, OCR.space fields,
tokens, and other secret-bearing remote settings. It also forces Docling
pipeline options to `enable_remote_services=False`.

Only add remote inference or online OCR as a future explicit adapter when:

- local models are too slow or unavailable
- the user explicitly wants remote inference
- the environment already provides a trusted compatible endpoint
- request limits, retry behavior, redaction, and cost controls are specified

For OCR.space specifically, a future adapter must use OCR Engine 3 for paper
extraction quality. Splitting PDFs into one image per page can make requests
smaller and recoverable per page, but it cannot bypass account-level quota,
rate, or concurrency limits.
