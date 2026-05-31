# Provider Policy

The runtime helper owns provider access. Existing skills such as `paper-lookup`
are routing/reference guidance, not executable provider clients.

Provider records must describe capabilities instead of relying on a linear
fallback order:

- `resolve_by_doi`
- `resolve_by_title`
- `venue_recent_by_source`
- `citation_refs`
- `citation_citers`
- `biomed_related`
- `preprint_published_link`
- `oa_status`

Comparator-paper evidence must come from provider/cache/fixture provenance with
source IDs, query IDs, year windows, and evidence levels. Provider records that
do not implement those capabilities must not be used for deliverable ranking.

Network rules:

- Default to offline/cache/fixture mode.
- Require a prior ok `privacy-gate`, `--allow-network`, and explicit
  `--allow-provider <name>` for live calls. There is no implicit default
  provider.
- Use HTTPS-only provider URLs and bounded timeouts.
- Enforce provider allowlists, request counters, response byte caps, pagination
  caps, and retry/backoff limits.
- Store symbolic credential status only, never tokens, keys, or emails.
- Store redacted query/source artifacts only; do not persist raw query URLs.
- Treat Unpaywall as DOI-first OA metadata only; never fetch PDFs.

Provider caveats:

- Crossref, OpenAlex, Semantic Scholar, PubMed/PMC, arXiv, bioRxiv, and
  Unpaywall expose different capabilities. Do not treat them as
  interchangeable.
- PubMed related-article results are not citation edges.
- Preprint servers and repositories are evidence sources, not submission
  venues unless explicitly allowed.
- Unimplemented providers must be marked `unsupported`, `configured_missing`,
  or `skipped`; they must not advertise usable live capabilities.
