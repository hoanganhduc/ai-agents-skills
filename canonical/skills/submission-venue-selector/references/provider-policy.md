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

Network rules:

- Default to offline/cache/fixture mode.
- Require `--allow-network` for live calls.
- Use HTTPS-only provider URLs and bounded timeouts.
- Store symbolic credential status only, never tokens, keys, or emails.
- Treat Unpaywall as DOI-first OA metadata only; never fetch PDFs.

Provider caveats:

- Crossref, OpenAlex, Semantic Scholar, PubMed/PMC, arXiv, bioRxiv, and
  Unpaywall expose different capabilities. Do not treat them as
  interchangeable.
- PubMed related-article results are not citation edges.
- Preprint servers and repositories are evidence sources, not submission
  venues unless explicitly allowed.
