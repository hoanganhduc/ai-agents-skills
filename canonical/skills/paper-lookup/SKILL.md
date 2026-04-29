---
name: paper-lookup
description: Use as a metadata and discovery fallback for papers, preprints, citations, DOIs, PMIDs, and open-access signals after local library routing is exhausted or when external paper discovery is explicitly needed.
metadata:
  short-description: External paper metadata and discovery fallback
---

# Paper Lookup

Use this skill for external literature discovery and metadata lookup.

## Important routing boundary

Do **not** replace the existing library-first workflow.

Keep this order:
- `zotero` first for paper/library retrieval
- `calibre` second for review tasks needing a local document
- `paper-lookup` for external metadata/discovery
- `getscipapers_requester` for actual external retrieval when needed

## Good use cases

- find papers on a topic
- resolve a DOI or PMID
- check whether a paper has an open-access location
- search major literature APIs before retrieval

## High-value references

- `references/pubmed.md`
- `references/pmc.md`
- `references/arxiv.md`
- `references/biorxiv.md`
- `references/crossref.md`
- `references/openalex.md`
- `references/semantic-scholar.md`
- `references/unpaywall.md`
