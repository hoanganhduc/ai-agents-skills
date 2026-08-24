---
name: digest-bridge
description: Use when the user wants to extract arXiv IDs or DOIs from research or RSS digests and turn them into getscipapers requests or manifests.
metadata:
  short-description: Bridge digest outputs into paper retrieval
---

# Digest Bridge


## Windows Runtime Commands

On native Windows, use the managed Windows runner and the native runtime command target. Set `$runtime` to the installed runtime root. Multi-agent installs usually use `%LOCALAPPDATA%\ai-agents-skills\runtime`. Then run:

```powershell
$runtime = if ($env:AAS_RUNTIME_ROOT) { $env:AAS_RUNTIME_ROOT } else { "$env:LOCALAPPDATA\ai-agents-skills\runtime" }
& "$runtime\run_skill.ps1" "skills/digest-bridge/digest_bridge.py" <args>
```

POSIX examples below use `run_skill.sh` and `.sh` command targets; use the Windows command target above on native Windows.

This uses the managed ai-agents-skills runtime copy of the digest bridge workflow.

## When to use

- scan research digests for papers
- scan RSS digests for papers
- create a getscipapers manifest from digest outputs
- request papers mentioned in recent digests

## Base path

- `$AAS_RUNTIME_WORKSPACE/skills/digest-bridge/`

This is a direct Python entry point, so run it from the managed ai-agents-skills runtime workspace with the workspace-local `PYTHONPATH`.

## Core commands

Use `functions.exec_command`.

```bash
cd "$AAS_RUNTIME_WORKSPACE" && PYTHONPATH="$AAS_RUNTIME_WORKSPACE/.local:${PYTHONPATH:-}" python3 skills/digest-bridge/digest_bridge.py scan
```

```bash
cd "$AAS_RUNTIME_WORKSPACE" && PYTHONPATH="$AAS_RUNTIME_WORKSPACE/.local:${PYTHONPATH:-}" python3 skills/digest-bridge/digest_bridge.py scan --source research --min-score 3
```

```bash
cd "$AAS_RUNTIME_WORKSPACE" && PYTHONPATH="$AAS_RUNTIME_WORKSPACE/.local:${PYTHONPATH:-}" python3 skills/digest-bridge/digest_bridge.py request --source research
```

```bash
cd "$AAS_RUNTIME_WORKSPACE" && PYTHONPATH="$AAS_RUNTIME_WORKSPACE/.local:${PYTHONPATH:-}" python3 skills/digest-bridge/digest_bridge.py request --source rss --watch
```

## Operational notes

- Use this after a digest run, not as a replacement for the digest itself.
- Runtime installation includes `getscipapers-requester` transitively because
  the bridge's durable manifest/watch handoff invokes that helper.
- The bridge consumes validated `digest-items.v1` JSON sidecars only. Markdown
  is display-only and is never scanned for identifiers.
- Every bridge JSON boundary rejects duplicate object members and non-standard
  constants such as `NaN` or `Infinity`. This applies to producer sidecars,
  helper acknowledgments, persisted manifest/watch artifacts, and the owned
  request ledger; malformed owned ledger bytes are preserved and block the
  helper before any handoff can be banked.
- RSS discovery is limited to the five producer-owned sidecars
  `rss-research`, `rss-events`, `rss-jobs`, `rss-general`, and `rss-video`.
  Each sidecar's top-level `source` must match its canonical filename owner;
  additional or relabeled JSON files cannot enter the handoff. Structured
  producer outcome fields must also establish that discovery/publication did
  not fail completely; research status requires exactly `arxiv`,
  `s2_recommend`, and `s2_search`, each with only `status` and `detail`.
  arXiv is never skippable; either Semantic Scholar source may be `skipped`
  when its required seed/topic input is absent. Successful-empty runs remain
  admissible.
- If a Markdown digest exists without its sidecar, or a sidecar is invalid or
  oversized, `scan` and `request` exit 2 with `invalid_digest_sidecar`. Rerun
  the corresponding producer; do not synthesize a sidecar from Markdown.
  Admission uses directory-entry metadata rather than target existence, so a
  broken canonical sidecar symlink or a link-like/non-directory RSS digest path
  fails closed instead of being mistaken for an optional missing producer.
  On Windows, link-like includes filesystem reparse points such as junctions,
  not only ordinary symlinks.
- arXiv IDs and DOIs are derived only from validated `arxiv.org` and `doi.org`
  links in the sidecar, never from titles, summaries, or feed text.
- Every handoff is canonicalized to the DOI form accepted by
  `getscipapers-requester`; versioned arXiv links and their DataCite DOI form
  are one request.
- `request` serializes the manifest/watch transition with the bridge ledger.
  It records identifiers only after the complete manifest and every requested
  watch succeed, retains the full bounded producer maximum, and fails closed
  if the ledger is corrupt, oversized, or symlinked.
- A helper exit code and JSON echo are not sufficient acknowledgment: the
  bridge bounded-reads the regular persisted manifest and verifies exact DOI
  coverage, then confirms every strict watch acknowledgment through the
  helper's locked `list-watches` ledger before advancing bridge state. A final
  durable watch must still be active, waiting, or posted, or have reached the
  successful `found` state; `failed` and `expired` transitions remain
  retryable and are never banked. A retry after a later item failed may accept
  and reuse an earlier exact-identity `found` watch without duplicating it.
  Durable records are semantically revalidated with the helper-equivalent
  canonical DOI/ISBN/search identity and both structured/current and legacy
  watch-key derivations. Creation/update/deadline/check/history timestamps must
  form a possible chronology; a shape-valid but stale-key, noncanonical, or
  time-inconsistent ledger record is never banked. Creation, update,
  last-check, and note-event times more than five minutes ahead of the bridge
  clock are also invalid; deadlines remain intentionally future-capable.
  Both the immediate echo and durable record must retain the exact requested
  `services: ["all"]` identity. Manifest echo
  and artifact reads use the helper's 16 MiB output contract, so the full
  3,000-item, 500-character ASCII DOI boundary remains admissible. DOI syntax
  is explicitly ASCII; Unicode `IGNORECASE` lookalikes cannot be case-folded
  into a different paper identifier.
  Helper stdout and combined stdout/stderr are drained concurrently under the
  endpoint contract; an overproducing or non-terminating child is stopped
  before any request is banked. One JSON protocol newline is framing rather
  than artifact content.
- Watch creation stops at the first failed helper call; later papers are not
  spawned after the transaction is already known to be retryable.
- The request ledger has its own 2 MiB reader/writer ceiling and compact UTF-8
  encoding, so the complete admitted maximum batch reloads and deduplicates on
  the next request instead of becoming unreadable after its first handoff.
- The getscipapers watch store independently locks every helper reader/writer,
  is byte/count bounded, and fails closed without replacing corrupt state. A
  watch-store failure leaves the bridge ledger unchanged for a safe retry.
- Explicit invalid helper configuration or unavailable configured manifest/
  watch storage fails the handoff; the bridge never banks output silently
  rerouted to the helper's temporary fallback root.
- Respect `--source` and `--min-score` filters instead of broad requests when the user wants a narrower batch.
- `scan` reports ledger matches as `already_requested` and never conflates
  never-requested papers excluded by `--min-score`, which are counted separately
  as `below_min_score`.
- If the user wants actual external retrieval, follow the manifest or request output into `getscipapers-requester`.
- `scan` is the dry-run discovery step; `request` is the transition into manifest/watch creation.
- If the manifest cannot be built, `request` exits 2 with `manifest_failed` and records
  nothing. The papers stay unrequested, so rerun it once getscipapers works rather than
  rescanning for them.
