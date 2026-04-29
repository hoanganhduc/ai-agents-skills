# Scrapling Integration

Use specialized browser or extraction tooling only when ordinary web access,
HTML parsing, or document parsing is insufficient.

Good candidates:

- JavaScript-heavy pages where content is not in static HTML
- pages with repeated anti-bot rendering changes
- extraction that needs stable selectors across many pages

Avoid browser-heavy extraction when:

- a public API or structured export exists
- a small manual source check is enough
- credentials, private pages, or terms restrictions make automation risky

Keep evidence records: URL, retrieval date, extraction method, and fields
used. Do not store cookies, tokens, or private session data in managed files.
