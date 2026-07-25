<!-- Managed by ai-agents-skills. Generated target: grok. Source: references/deferred-roadmap.md. -->

# Deferred roadmap (Phase D)

These items are **not** implemented. Ship only after agent failure logs show
residual demand beyond ICORE + DOAJ + import paths, and only under the same
evidence and privacy constraints as the rest of this skill.

## Public machine-readable lives (candidates)

| Source ID | Condition to implement |
|---|---|
| `jufo` | Stable public API or documented bulk export; reviewed parser; always honest freshness labels; no scrape of gated HTML |
| `norwegian-register` | Same bar as JUFO |

Acceptance for any new public live adapter:

1. Official domain allowlist + HTTPS + size/time bounds.
2. Fail-closed parse when required columns/fields are missing.
3. Does not set `verified-current` without a reviewed edition-discovery design
   comparable to ICORE.
4. Does not enable proof without a reviewed association adapter.
5. Success on the new source alone must not mark multi-source delivery `ready`
   when other requested sources are blocked.

## Licensed API design (candidates)

| Source ID | Notes |
|---|---|
| `scopus` | Elsevier API under institutional license only |
| `clarivate-jcr` | Clarivate subscription API only |
| `wos-mjl` | Clarivate / MJL authorized access only |

Requirements before any licensed adapter code:

1. Explicit policy review and secret-handling design (env-only credentials;
   never argv, logs, artifacts, or git).
2. No HTML scraping; no browser-profile or cookie reuse.
3. Observations remain source-specific; no composite score.
4. Imports/`currentness-unconfirmed` remain valid fallback.
5. Proof stays out of scope unless a separate public-page proof design is
   reviewed (unlikely for licensed UIs).

## Identity enrichment (optional)

Open identity sources (ISSN registries, DBLP keys, Wikidata) may later improve
cross-source coalesce. They must not invent ranks or upgrade freshness.

## Explicitly rejected forever (unless policy changes)

- Scraping Scopus / JCR / WoS / gated SCImago HTML.
- Composite ICORE+CCF+SJR+JCR scores.
- Promoting import mtime or filename to `verified-current`.
