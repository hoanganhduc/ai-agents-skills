#!/usr/bin/env bash
# Optional thin launcher: export formal env, then exec an existing supervisor.
#
# This is NOT a forked ARL driver. Prefer wiring formal_env.inc.sh into your
# project's LAUNCH.sh / supervisor.sh so there is one source of drive truth.
#
# Usage:
#   LOOP_DIR=/path/to/research_loop \
#   SUPERVISOR=/path/to/existing/supervisor.sh \
#   bash LAUNCH_with_formal_env.sh
#
# Or:
#   bash LAUNCH_with_formal_env.sh --loop /path/to/research_loop -- \
#     bash /path/to/supervisor.sh

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOOP_DIR="${LOOP_DIR:-}"
SUPERVISOR="${SUPERVISOR:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --loop) LOOP_DIR="$2"; shift 2 ;;
    --supervisor) SUPERVISOR="$2"; shift 2 ;;
    --) shift; break ;;
    *) break ;;
  esac
done

if [[ -z "${LOOP_DIR}" ]]; then
  echo "error: set LOOP_DIR or pass --loop <dir>" >&2
  exit 2
fi

# shellcheck source=/dev/null
source "${HERE}/formal_env.inc.sh"

# Prefer installed runtime when operators want a one-liner drive (not a fork).
# The ARL command path is credential-bearing, so run_skill.sh refuses to launch
# unless it is executing from a root-owned AAS component generation. Select on
# the credential-runtime manifest and keep the newest publish -- the directory
# is named for a git commit, so the name itself carries no ordering. The
# per-user runtime stays as the fallback for hosts with no component store.
RUNTIME_ROOT="${AAS_RUNTIME_ROOT:-${HOME}/.local/share/ai-agents-skills/runtime}"
RUN_SKILL="${RUNTIME_ROOT}/run_skill.sh"
newest=0
for gen in /usr/local/libexec/coding-system/components/ai-agents-skills/*/; do
  gen="${gen%/}"
  [[ -f "${gen}/manifest/credential-runtime.json" ]] || continue
  [[ -x "${gen}/canonical/runtime/runners/run_skill.sh" ]] || continue
  stamp="$(stat -c %Y "${gen}" 2>/dev/null || stat -f %m "${gen}" 2>/dev/null)" || continue
  [[ "${stamp:-0}" -gt "$newest" ]] || continue
  newest="$stamp"
  RUN_SKILL="${gen}/canonical/runtime/runners/run_skill.sh"
done
ARL_SH="skills/autonomous-research-loop-runtime/run_autonomous_research_loop.sh"

formal_drive_flags=()
if [[ -n "${AAS_AUTOLOOP_FORMAL_POLICY:-}" ]]; then
  formal_drive_flags+=(--formal-policy "${AAS_AUTOLOOP_FORMAL_POLICY}")
fi
if [[ -n "${AAS_AUTOLOOP_FORMAL_PROJECT:-}" ]]; then
  formal_drive_flags+=(--formal-project "${AAS_AUTOLOOP_FORMAL_PROJECT}")
fi
if [[ "${AAS_AUTOLOOP_FORMAL_FORCE:-0}" == "1" || "${AAS_AUTOLOOP_FORMAL_FORCE:-}" == "true" ]]; then
  formal_drive_flags+=(--formal-force-after-iteration)
fi
if [[ "${AAS_AUTOLOOP_FORMAL_TYPECHECK:-0}" == "1" || "${AAS_AUTOLOOP_FORMAL_TYPECHECK:-}" == "true" ]]; then
  formal_drive_flags+=(--formal-typecheck)
fi

if [[ -n "${SUPERVISOR}" ]]; then
  exec bash "${SUPERVISOR}" "$@"
fi

if [[ $# -gt 0 ]]; then
  exec "$@"
fi

# Fallback demo: drive with formal flags (requires provider env already set).
if [[ -x "${RUN_SKILL}" || -f "${RUN_SKILL}" ]]; then
  echo "Launching runtime drive with formal flags against ${LOOP_DIR}" >&2
  echo " formal flags: ${formal_drive_flags[*]:-<none>}" >&2
  # Caller must still pass --provider / --cmd; this is documentation-shaped.
  echo "error: pass SUPERVISOR=... or trailing command; refusing bare drive without --provider" >&2
  exit 2
fi

echo "error: no SUPERVISOR and no AAS_RUNTIME_ROOT skill runner" >&2
exit 2
