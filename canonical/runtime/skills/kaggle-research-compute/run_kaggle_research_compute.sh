#!/bin/bash -p
set -euo pipefail
compute_pointer="${AAS_COMPUTE_SECRETS_FILE:-}"

unset AAS_COMPUTE_SECRETS_FILE
unset AAS_SECRETS_FILE OPENCLAW_SECRETS_FILE AAS_SKILL_SECRETS_FILE AAS_PROVIDER_SECRETS_FILE
unset AAS_CALIBRE_SECRETS_FILE AAS_ZOTERO_SECRETS_FILE AAS_FILE_DELIVERY_SECRETS_FILE
unset REMOTE_BRIDGE_SECRETS_FILE SEND_EMAIL_SECRETS_FILE
unset AXLE_API_KEY LEANEXPLORE_API_KEY OCR_SPACE_API_KEY OCR_SPACE_KEY OCRSPACE_API_KEY OCRSPACE_KEY
unset OPENCLAW_S2_API_KEY S2_API_KEY PATENTSVIEW_API_KEY SEMANTIC_SCHOLAR_API_KEY UNPAYWALL_EMAIL ZENODO_TOKEN
unset ZOTERO_API_KEY WEBDAV_PASSWORD GDRIVE_CREDENTIALS CALIBRE_GDRIVE_FOLDER_ID
unset SMTP_HOST SMTP_PORT SMTP_USER SMTP_PASSWORD SMTP_FROM SMTP_SECURITY SMTP_TIMEOUT SMTP_ACCOUNT
unset ZULIP_ORG_URL ZULIP_SITE ZULIP_EMAIL ZULIP_API_KEY TELEGRAM_BOT_TOKEN
unset OPENAI_API_KEY ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN CLAUDE_API_KEY CLAUDE_CODE_OAUTH_TOKEN
unset COPILOT_GITHUB_TOKEN COPILOT_PROVIDER_API_KEY COPILOT_PROVIDER_BEARER_TOKEN
unset GEMINI_API_KEY GOOGLE_API_KEY DEEPSEEK_API_KEY XAI_API_KEY GROK_API_KEY
unset KIMI_API_KEY MOONSHOT_API_KEY OPENCODE_API_KEY GH_TOKEN GITHUB_TOKEN
unset HCLOUD_TOKEN HCLOUD_SSH_KEYS
unset KAGGLE_API_TOKEN KAGGLE_CONFIG_DIR KAGGLE_USERNAME KAGGLE_KEY
unset MODAL_TOKEN_ID MODAL_TOKEN_SECRET

unset PYTHONHOME PYTHONPATH PYTHONSTARTUP PYTHONINSPECT PYTHONWARNINGS PYTHONBREAKPOINT
unset LD_PRELOAD LD_LIBRARY_PATH LD_AUDIT DYLD_INSERT_LIBRARIES DYLD_LIBRARY_PATH
unset BASH_ENV ENV CDPATH GLOBIGNORE NODE_OPTIONS NODE_PATH 2>/dev/null || true
export PATH=/usr/bin:/bin
script_path="${BASH_SOURCE[0]:-$0}"
runtime_command_fd="${AAS_RUNTIME_COMMAND_FD:-}"
if [[ "$runtime_command_fd" =~ ^[0-9]+$ ]] && \
   { [ "$script_path" = "/proc/self/fd/$runtime_command_fd" ] || [ "$script_path" = "/dev/fd/$runtime_command_fd" ]; }; then
  script_path="${AAS_RUNTIME_COMMAND_PATH:-$script_path}"
fi
unset AAS_RUNTIME_COMMAND_FD AAS_RUNTIME_COMMAND_PATH
case "$script_path" in */*) script_parent="${script_path%/*}" ;; *) script_parent=. ;; esac
ROOT="$(cd -- "$script_parent" && builtin pwd -P)"
WORKSPACE_ROOT="$(cd -- "$ROOT/../.." && builtin pwd -P)"

trusted_python_metadata_ok() {
  local candidate="$1" metadata owner mode links current_uid
  metadata="$(/usr/bin/stat -Lc '%u:%a:%h' -- "$candidate" 2>/dev/null || \
    /usr/bin/stat -Lf '%u:%Lp:%l' "$candidate" 2>/dev/null || true)"
  IFS=: read -r owner mode links <<< "$metadata"
  current_uid="$(/usr/bin/id -u 2>/dev/null || true)"
  case "$owner" in
    0|"$current_uid") ;;
    *) return 1 ;;
  esac
  [[ "$mode" =~ ^[0-7]{3,4}$ ]] || return 1
  (( (8#$mode & 8#022) == 0 )) || return 1
  [[ "$links" =~ ^[0-9]+$ ]] || return 1
  if [ "$owner" != "0" ] && [ "$links" -ne 1 ]; then
    return 1
  fi
  return 0
}

bind_selected_python_inode() {
  local selected="$PYTHON"
  exec {AAS_PYTHON_EXEC_FD}<"$selected" || return 1
  if [ -e "/proc/self/fd/$AAS_PYTHON_EXEC_FD" ]; then
    PYTHON="/proc/self/fd/$AAS_PYTHON_EXEC_FD"
  elif [ -e "/dev/fd/$AAS_PYTHON_EXEC_FD" ]; then
    PYTHON="/dev/fd/$AAS_PYTHON_EXEC_FD"
  else
    exec {AAS_PYTHON_EXEC_FD}<&-
    return 1
  fi
  [ "$PYTHON" -ef "$selected" ]
}

resolve_python() {
  local configured resolved resolved_dir candidate credential_present
  configured="${AAS_RUNTIME_PYTHON:-}"
  credential_present=0
  if [ -n "$compute_pointer" ]; then
    credential_present=1
  fi
  if [ "$credential_present" -eq 1 ]; then
    case "$configured" in
      ""|/*) ;;
      *)
        printf 'credential-bearing Kaggle launch requires a trusted already-resolved Python runtime\n' >&2
        return 127
        ;;
    esac
    resolved=""
    for candidate in /usr/bin/python3; do
      if [ ! -f "$candidate" ] || [ ! -x "$candidate" ]; then
        continue
      fi
      if ! trusted_python_metadata_ok "$candidate"; then
        continue
      fi
      if [ -n "$configured" ] && [ ! "$configured" -ef "$candidate" ]; then
        continue
      fi
      resolved="${configured:-$candidate}"
      break
    done
    if [ -z "$resolved" ]; then
      printf 'credential-bearing Kaggle launch requires a trusted managed or system Python runtime\n' >&2
      return 127
    fi
  elif [ -n "$configured" ]; then
    case "$configured" in
      /*) resolved="$configured" ;;
      */*)
        printf 'AAS_RUNTIME_PYTHON must name an absolute path or command name.\n' >&2
        return 127
        ;;
      *)
        resolved="$(command -v "$configured" 2>/dev/null || true)"
        ;;
    esac
  else
    resolved="$(command -v python3 2>/dev/null || command -v python 2>/dev/null || true)"
  fi
  if [ -z "$resolved" ] || [ ! -f "$resolved" ] || [ ! -x "$resolved" ]; then
    printf 'kaggle-research-compute requires a usable Python runtime\n' >&2
    return 127
  fi
  case "$resolved" in
    /proc/self/fd/*|/dev/fd/*)
      printf '%s\n' "$resolved"
      return 0
      ;;
  esac
  resolved_dir="$(cd -- "$(dirname -- "$resolved")" && pwd -P)"
  printf '%s/%s\n' "$resolved_dir" "$(basename -- "$resolved")"
}

resolve_secret_loader() {
  local candidate candidate_dir resolved
  for candidate in \
    "$ROOT/../../../load_secret_env.py" \
    "$ROOT/../../runners/load_secret_env.py"; do
    candidate_dir="$(dirname -- "$candidate")"
    if [ ! -d "$candidate_dir" ]; then
      continue
    fi
    resolved="$(cd -- "$candidate_dir" && pwd -P)/$(basename -- "$candidate")"
    if [ -f "$resolved" ] && [ ! -L "$resolved" ]; then
      printf '%s\n' "$resolved"
      return 0
    fi
  done
  return 1
}

export CODEX_CALLER_CWD="${CODEX_CALLER_CWD:-${OLDPWD:-$PWD}}"

if ! PYTHON="$(resolve_python)"; then
  exit 127
fi
if [ -n "$compute_pointer" ] && ! bind_selected_python_inode; then
  printf 'Kaggle credential launch could not bind the trusted Python inode.\n' >&2
  exit 127
fi
export AAS_RUNTIME_PYTHON="$PYTHON"
[ -n "$compute_pointer" ] && export AAS_COMPUTE_SECRETS_FILE="$compute_pointer"
trusted_pythonpath="$WORKSPACE_ROOT"
IFS=: read -r -a python_entries <<< "${PYTHONPATH:-}"
for python_entry in ${python_entries[@]+"${python_entries[@]}"}; do
  if [ -d "$python_entry" ]; then
    python_entry_real="$(cd -- "$python_entry" && pwd -P)"
    case "$python_entry_real/" in
      "$WORKSPACE_ROOT"/*) trusted_pythonpath="$trusted_pythonpath:$python_entry_real" ;;
    esac
  fi
done
export PYTHONPATH="$trusted_pythonpath"
unset PYTHONHOME PYTHONSTARTUP PYTHONINSPECT
unset AAS_SKILL_SECRETS_FILE AAS_PROVIDER_SECRETS_FILE
unset AXLE_API_KEY LEANEXPLORE_API_KEY OCR_SPACE_API_KEY OCR_SPACE_KEY
unset OCRSPACE_API_KEY OCRSPACE_KEY OPENCLAW_S2_API_KEY PATENTSVIEW_API_KEY
unset SEMANTIC_SCHOLAR_API_KEY UNPAYWALL_EMAIL ZENODO_TOKEN
unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN CLAUDE_API_KEY
unset CLAUDE_CODE_OAUTH_TOKEN COPILOT_GITHUB_TOKEN COPILOT_PROVIDER_API_KEY
unset COPILOT_PROVIDER_BEARER_TOKEN DEEPSEEK_API_KEY GEMINI_API_KEY
unset GH_TOKEN GITHUB_TOKEN GOOGLE_API_KEY GROK_API_KEY KIMI_API_KEY
unset MOONSHOT_API_KEY OPENAI_API_KEY OPENCODE_API_KEY XAI_API_KEY
command=("$PYTHON" "$ROOT/kaggle_research_compute.py" "$@")
if [ -n "${AAS_COMPUTE_SECRETS_FILE:-}" ]; then
  secret_loader="$(resolve_secret_loader || true)"
  if [ -z "$secret_loader" ]; then
    printf 'managed compute secret loader is unavailable\n' >&2
    exit 127
  fi
  command=(
    "$PYTHON" -I "$secret_loader"
    --pointer-env AAS_COMPUTE_SECRETS_FILE
    --allow-key HCLOUD_TOKEN
    --allow-key HCLOUD_SSH_KEYS
    --allow-key KAGGLE_API_TOKEN
    --allow-key KAGGLE_CONFIG_DIR
    --export-key KAGGLE_API_TOKEN
    --export-key KAGGLE_CONFIG_DIR
    --retain-env PYTHONPATH
    --retain-env AAS_AUTOLOOP_COMPUTE_WORKSPACE
    -- "${command[@]}"
  )
else
  unset AAS_COMPUTE_SECRETS_FILE
  unset HCLOUD_TOKEN HCLOUD_SSH_KEYS KAGGLE_API_TOKEN KAGGLE_CONFIG_DIR
fi

exec "${command[@]}"
