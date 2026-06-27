# URL to Screenshot Entrypoint

Route requests to capture a web page (an `http` or `https` URL) to a clean PNG
screenshot to the `url-to-screenshot` skill. Use for viewport or full-page
captures, with cookie-consent dismissal, a specific viewport size or
device-scale, timeouts, and SSRF-safe URL admission, across Linux, macOS, and
Windows.

The executable engine ships as the `url-to-screenshot-runtime` skill. The flow
detects an installed browser, admits the URL through a fail-closed SSRF gate,
captures over headless Chromium, dismisses cookie-consent overlays, detects blank
output, and ends with an explicit `verify` gate that is the only thing allowed to
declare success.

Run `doctor` first to confirm a browser is present: `file-exists` and
`offline-smoke` verification passing does not imply a browser is installed. Only
`doctor` reports real capture readiness.
