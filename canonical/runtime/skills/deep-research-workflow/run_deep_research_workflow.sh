#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
WORKSPACE="${AAS_RUNTIME_WORKSPACE:-${OPENCLAW_WORKSPACE:-$(cd "$SCRIPT_DIR/../.." && pwd -P)}}"
TEMPLATE_DIR="$WORKSPACE/templates"
sources_tpl="$TEMPLATE_DIR/deep-research-sources.md"
analysis_tpl="$TEMPLATE_DIR/deep-research-analysis.md"
report_tpl="$TEMPLATE_DIR/deep-research-report.md"

usage() {
  cat <<'EOF'
usage: run_deep_research_workflow.sh <doctor|init> [args...]

Subcommands:
  doctor
      verify the deep-research templates exist

  init [--dir DIR] [--subdir NAME] [--force]
      initialize scaffold files:
        DIR/NAME/sources.md
        DIR/NAME/analysis.md
        DIR/NAME/report.md
EOF
}

check_file() {
  if [[ -f "$1" ]]; then
    printf 'OK\t%s\n' "$1"
    return 0
  fi
  printf 'MISSING\t%s\n' "$1" >&2
  return 1
}

copy_file() {
  local source="$1"
  local target="$2"
  local force="$3"
  if [[ ! -f "$source" ]]; then
    printf 'missing template: %s\n' "$source" >&2
    return 1
  fi
  if [[ -e "$target" && "$force" != "1" ]]; then
    printf 'refusing to overwrite existing file without --force: %s\n' "$target" >&2
    return 1
  fi
  cp "$source" "$target"
  printf 'WROTE\t%s\n' "$target"
}

cmd="${1:-}"
if [[ -z "$cmd" ]]; then
  usage
  exit 1
fi
shift

case "$cmd" in
  doctor)
    missing=0
    check_file "$sources_tpl" || missing=1
    check_file "$analysis_tpl" || missing=1
    check_file "$report_tpl" || missing=1
    exit "$missing"
    ;;
  init)
    target_dir="."
    subdir="research"
    force=0
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --dir)
          [[ $# -ge 2 ]] || { echo "--dir requires a value" >&2; exit 1; }
          target_dir="$2"
          shift 2
          ;;
        --subdir)
          [[ $# -ge 2 ]] || { echo "--subdir requires a value" >&2; exit 1; }
          subdir="$2"
          shift 2
          ;;
        --force)
          force=1
          shift
          ;;
        -h|--help)
          usage
          exit 0
          ;;
        *)
          printf 'unknown argument: %s\n' "$1" >&2
          usage
          exit 1
          ;;
      esac
    done
    out_dir="$target_dir/$subdir"
    mkdir -p "$out_dir"
    copy_file "$sources_tpl" "$out_dir/sources.md" "$force"
    copy_file "$analysis_tpl" "$out_dir/analysis.md" "$force"
    copy_file "$report_tpl" "$out_dir/report.md" "$force"
    ;;
  -h|--help)
    usage
    ;;
  *)
    printf 'unknown subcommand: %s\n' "$cmd" >&2
    usage
    exit 1
    ;;
esac
