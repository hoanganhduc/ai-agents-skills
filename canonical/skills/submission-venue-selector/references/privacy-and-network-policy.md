# Privacy And Network Policy

The draft is treated as unpublished by default.

Comparator-paper evidence may be collected only through redacted
provider/cache/fixture provenance. Missing comparator evidence must fail closed
instead of exposing raw draft text to improve venue matching.

Before live provider calls:

1. Run extraction locally.
2. Generate redacted `queries.jsonl`.
3. Run `privacy-gate`.
4. Require explicit `--allow-network --allow-provider <name>`.

Every network-capable subcommand must verify the current workspace has an ok
privacy gate before making provider calls. `run` ordering alone is not enough.

Forbidden by default:

- raw draft text in durable artifacts
- raw API keys, auth headers, or emails in artifacts
- downloads
- Zotero mutations
- WebDAV writes
- provider calls during smoke tests
- raw provider query URLs in durable artifacts

Workspace rules:

- Create private run directories where supported.
- Reject workspaces inside the repo checkout, canonical runtime source, agent
  skill directories, or known synced folders unless explicitly overridden.
- Provide `purge` for derived private artifacts.
