#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
DEFAULT_WORKSPACE="$(cd -- "$SCRIPT_DIR/../.." && pwd -P)"
WORKSPACE="${AAS_RUNTIME_WORKSPACE:-${OPENCLAW_WORKSPACE:-$DEFAULT_WORKSPACE}}"
BASE="$WORKSPACE/skills/rss-news-digest"
DIGEST_DIR="$WORKSPACE/data/research/rss/digests"
SUMMARY="$DIGEST_DIR/last-summary.md"

mkdir -p "$DIGEST_DIR"

# Run the digest for all tags and prioritize ai_research profile
bash "$BASE/run_rss_news_digest.sh" run --all-tags --profile ai_research

# Build the raw top-item view from validated JSON sidecars. Markdown digest
# files are display-only and must never be parsed as a machine protocol. The
# recurring wrapper replaces last-summary.md without accumulating manual
# timestamped history.
bash "$BASE/run_rss_news_digest.sh" summarize-sidecars --no-history

echo "WROTE_SUMMARY:${SUMMARY}"
