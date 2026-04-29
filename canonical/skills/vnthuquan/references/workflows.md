# vnthuquan Workflows

This reference contains operational details for the `vnthuquan` skill. Keep
`SKILL.md` concise and load this file when a task needs workflow details.

## Read-Only Discovery

Use the runtime wrapper through the Codex runner:

```bash
bash ~/.codex/runtime/run_skill.sh skills/vnthuquan/run_vnthuquan.sh search "QUERY" --json
```

Useful commands:

- `diagnose --json`
- `doctor --json`
- `mirrors list --json`
- `mirrors check --json`
- `config path --json`
- `config show --json`
- `categories list --json`
- `categories show CATEGORY --json`
- `formats --json`
- `list latest --limit 10 --json`
- `search "QUERY" --json`
- `show --title "TITLE" --links --json`
- `archive list --json`
- `completion bash`

## Controlled Downloads

Downloads are dry-run by default unless `--execute --yes` is present.

Dry-run one title:

```bash
bash ~/.codex/runtime/run_skill.sh skills/vnthuquan/run_vnthuquan.sh download --title "Mưa Đỏ" --format epub --dry-run --json
```

Execute one title:

```bash
bash ~/.codex/runtime/run_skill.sh skills/vnthuquan/run_vnthuquan.sh download --title "Mưa Đỏ" --format epub --execute --yes --json
```

The wrapper consumes `--yes` and must not forward it to the native package.
Executed downloads use the wrapper-managed archive path unless execution is
refused. `--no-archive` is refused for executed downloads.

Supported download formats are `epub`, `pdf`, `text`, and `audio`. Other site
formats are discovery-only until the package exposes a validation workflow for
them.

## Queue Workflow

Create a bounded dry-run queue:

```bash
bash ~/.codex/runtime/run_skill.sh skills/vnthuquan/run_vnthuquan.sh queue --query "Kim Dung" --limit 5 --format epub --json
```

Queue creation writes a timestamped manifest under `~/.codex/runs/vnthuquan/`
unless `--manifest PATH` is supplied. Listing queues require `--limit` or
`--pages` so the crawl is bounded.

Execute a queue:

```bash
bash ~/.codex/runtime/run_skill.sh skills/vnthuquan/run_vnthuquan.sh execute-queue ~/.codex/runs/vnthuquan/queue-YYYYMMDD-HHMMSS-NNNNNN.json --jobs 1 --yes --json
```

Queue execution writes a result log under the run directory and returns
`total`, `succeeded`, `failed`, and `skipped`.

Recover failed queue items:

```bash
bash ~/.codex/runtime/run_skill.sh skills/vnthuquan/run_vnthuquan.sh requeue-failed ~/.codex/runs/vnthuquan/queue-result-YYYYMMDD-HHMMSS-NNNNNN.json --json
```

The retry manifest contains only failed, non-skipped items that still have a
selector in the queue result.

## Validation

Validate a local file:

```bash
bash ~/.codex/runtime/run_skill.sh skills/vnthuquan/run_vnthuquan.sh validate PATH --json
```

The wrapper defaults to native `--format auto` when no format is supplied.

## Calibre Handoff

Start with a non-mutating dry-run:

```bash
bash ~/.codex/runtime/run_skill.sh skills/vnthuquan/run_vnthuquan.sh add-to-calibre PATH --dry-run --json
```

Behavior:

- validates the local `vnthuquan` file first
- accepts only EPUB/PDF files
- rejects text/audio archives until a conversion workflow exists
- runs `cal doctor` with a timeout before handoff
- reads duplicate candidates from the local Calibre cache
- returns the generated Calibre duplicate-search command for manual checking
- runs `cal add PATH --title ... --author ... --tag ... --dry-run`

Real Calibre writes are allowed only after the dry-run result and duplicate
candidates have been reviewed:

```bash
bash ~/.codex/runtime/run_skill.sh skills/vnthuquan/run_vnthuquan.sh add-to-calibre PATH --execute --yes --duplicates-reviewed --json
```

Write behavior:

- runs the same validation, `cal doctor`, duplicate cache lookup, and Calibre
  dry-run preflight immediately before the write
- refuses execution without `--yes`
- refuses execution without `--duplicates-reviewed`
- refuses execution if duplicate lookup is unavailable
- refuses execution when duplicate candidates exist unless `--allow-duplicate`
  is present
- runs one bounded Calibre write attempt and does not retry automatically
- records the write result under `~/.codex/runs/vnthuquan/` and appends an
  audit line to `~/.codex/state/vnthuquan/calibre-writes.jsonl`
- returns the Calibre ID, Drive path, metadata, and recovery notes when the
  Calibre write returns them

The Calibre CLI currently emits JSON by default but does not accept `--json`.
The `vnthuquan` wrapper normalizes Calibre stdout/stderr into its own JSON
payload.

## State Layout

Local Codex paths:

- run directory: `~/.codex/runs/vnthuquan/`
- state directory: `~/.codex/state/vnthuquan/`
- config path: `~/.codex/state/vnthuquan/config.json`
- archive path: `~/.codex/state/vnthuquan/downloads.jsonl`
- cache path: `~/.codex/state/vnthuquan/http-cache.json`

Run directories are for temporary manifests and transcripts. State directories
are for persistent wrapper-managed config, archive, and cache files.

## JSON Normalization

The wrapper returns normalized JSON when `--json` is requested:

- adds `target`, `command`, `wrapper_version`, and `vnthuquan_version`
- maps native package `version` to `vnthuquan_version`
- maps native mirror `elapsed_seconds` to `latency_ms`
- adds counts for result lists where practical
- preserves raw package output under `package_payload` when useful

## Routing And Safety

- Generic paper/DOI/ISBN/book retrieval should route through Zotero and the
  existing library workflows, not this skill.
- Use Calibre only after a validated `vnthuquan` download or explicit user
  request for Calibre integration.
- Do not guess among ambiguous title matches. Show candidates and ask.
- Mutating config, changing mirrors, executing downloads, overwriting files,
  queue execution, and Calibre writes require explicit confirmation.
- Calibre writes require reviewing a dry-run and duplicate candidates first.
  Use `--allow-duplicate` only when duplicate candidates are intentionally
  accepted as a separate library entry.
- Queue execution uses the wrapper archive path and default jobs value unless
  the user supplies a bounded `--jobs` value.
- The default wrapper config sets a cache path under the Codex state directory,
  `cache_ttl_seconds` to 300, and `request_interval_seconds` to 0.4.
- Peer Calibre calls must use bounded timeouts; direct `cal search` can refresh
  a stale Drive-backed cache and may hang.

## Recovery

For Calibre write failures, do not run automatic retries. Review the returned
`calibre_write_result`, run the Calibre `doctor` or `sync` workflow if needed,
and repeat the `vnthuquan add-to-calibre --dry-run` review before making a
second execute attempt.
