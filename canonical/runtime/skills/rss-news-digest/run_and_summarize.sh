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

# Build a short summary (top 5 lines per tag: title + url if present)
rm -f "${SUMMARY}" || true
echo "# RSS Digest Summary - $(date -u +'%Y-%m-%d %H:%M:%S UTC')" > "${SUMMARY}"
for f in "${DIGEST_DIR}"/rss-*.md; do
  [ -f "$f" ] || continue
  tag=$(basename "$f" .md | sed 's/^rss-//')
  [[ "$tag" == "all" ]] && continue
  printf '\n## %s\n' "${tag}" >> "${SUMMARY}"
  grep -E "^## [0-9]+\." "$f" | sed 's/^## [0-9]*\. /- /' | sed -n '1,5p' >> "${SUMMARY}" || true
done

# Timestamped copy for history
ts=$(date -u +"%Y%m%dT%H%M%SZ")
cp "${SUMMARY}" "${DIGEST_DIR}/summary-${ts}.md" || true

echo "WROTE_SUMMARY:${SUMMARY}"
