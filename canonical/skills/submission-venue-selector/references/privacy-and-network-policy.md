# Privacy And Network Policy

The draft is treated as unpublished by default.

Before live provider calls:

1. Run extraction locally.
2. Generate redacted `queries.jsonl`.
3. Run `privacy-gate`.
4. Require explicit `--allow-network`.

Forbidden by default:

- raw draft text in durable artifacts
- raw API keys, auth headers, or emails in artifacts
- downloads
- Zotero mutations
- WebDAV writes
- provider calls during smoke tests

Workspace rules:

- Create private run directories where supported.
- Reject workspaces inside the repo checkout, canonical runtime source, agent
  skill directories, or known synced folders unless explicitly overridden.
- Provide `purge` for derived private artifacts.
