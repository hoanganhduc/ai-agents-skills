#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  review_pending.sh [WORKSPACE_OR_LEARNINGS_DIR] [--high-only]
  review_pending.sh --dir <DIR> [--high-only]
  review_pending.sh --help

Behavior:
  - If DIR ends with .learnings, use it directly.
  - Otherwise use DIR/.learnings.
  - Default DIR is the current working directory.
EOF
}

TARGET=""
HIGH_ONLY=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir)
      TARGET="${2:-}"
      shift 2
      ;;
    --high-only)
      HIGH_ONLY=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      if [[ -n "$TARGET" ]]; then
        echo "error: multiple target directories provided" >&2
        usage >&2
        exit 2
      fi
      TARGET="$1"
      shift
      ;;
  esac
done

if [[ -z "$TARGET" ]]; then
  TARGET="$PWD"
fi

if [[ "${TARGET##*/}" == ".learnings" ]]; then
  LEARNINGS_DIR="$TARGET"
else
  LEARNINGS_DIR="$TARGET/.learnings"
fi

if [[ ! -d "$LEARNINGS_DIR" ]]; then
  echo "No .learnings directory found at: $LEARNINGS_DIR"
  echo "Tip: create .learnings/ and populate LEARNINGS.md, ERRORS.md, and FEATURE_REQUESTS.md as needed."
  exit 0
fi

python3 - "$LEARNINGS_DIR" "$HIGH_ONLY" <<'PY'
import re
import sys
from pathlib import Path

learnings_dir = Path(sys.argv[1])
high_only = sys.argv[2].lower() == "true"
files = ["ERRORS.md", "LEARNINGS.md", "FEATURE_REQUESTS.md"]
header_re = re.compile(r"^## \[(.+?)\]\s*(.*)$")

def parse_entries(path: Path):
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()
    entries = []
    current = None
    for line in lines:
        m = header_re.match(line)
        if m:
            if current:
                entries.append(current)
            current = {
                "id": m.group(1),
                "title": m.group(2).strip(),
                "priority": None,
                "status": None,
            }
            continue
        if current is None:
            continue
        if line.startswith("**Priority**:"):
            current["priority"] = line.split(":", 1)[1].strip().lower()
        elif line.startswith("**Status**:"):
            current["status"] = line.split(":", 1)[1].strip().lower()
    if current:
        entries.append(current)
    return entries

icon_map = {
    "critical": "!!",
    "high": "! ",
    "medium": "- ",
    "low": "  ",
    None: "  ",
}

all_entries = {}
for filename in files:
    path = learnings_dir / filename
    all_entries[filename] = parse_entries(path)

pending_total = 0
promoted_total = 0
resolved_total = 0

print(f"=== Pending Learnings Review ===")
print(f"Directory: {learnings_dir}")
print()

for filename in files:
    entries = all_entries[filename]
    pending = [e for e in entries if e["status"] == "pending"]
    promoted = [e for e in entries if e["status"] == "promoted"]
    resolved = [e for e in entries if e["status"] == "resolved"]
    pending_total += len(pending)
    promoted_total += len(promoted)
    resolved_total += len(resolved)

    shown = pending
    if high_only:
        shown = [e for e in pending if e["priority"] in {"high", "critical"}]

    print(f"--- {filename} ---")
    if shown:
        for e in shown:
            icon = icon_map.get(e["priority"], "  ")
            label = e["id"]
            if e["title"]:
                label += f"] {e['title']}" if not label.endswith("]") else f" {e['title']}"
            print(f"  {icon} [{label}")
    else:
        if high_only:
            print("  (no high/critical pending items)")
        else:
            print("  (no pending items)")
    print()

print(f"Total: {pending_total} pending, {promoted_total} promoted, {resolved_total} resolved")
print()
print("Actions:")
print("  Resolve:  change **Status**: pending -> resolved and add a short resolution note")
print("  Promote:  distill durable rules into ~/.codex/memories/, a SKILL.md, or ~/.codex/AGENTS.md")
print("  Log new:  append a structured entry to .learnings/{LEARNINGS,ERRORS,FEATURE_REQUESTS}.md")
PY
