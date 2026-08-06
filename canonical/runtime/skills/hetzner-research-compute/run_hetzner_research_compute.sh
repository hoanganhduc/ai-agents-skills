#!/bin/bash -p
set -euo pipefail

compute_pointer="${AAS_COMPUTE_SECRETS_FILE:-}"
hcloud_token="${HCLOUD_TOKEN:-}"
hcloud_ssh_keys="${HCLOUD_SSH_KEYS:-}"
unset AAS_COMPUTE_SECRETS_FILE HCLOUD_TOKEN HCLOUD_SSH_KEYS

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
  if [ -n "$compute_pointer" ] || [ -n "$hcloud_token" ] || [ -n "$hcloud_ssh_keys" ]; then
    credential_present=1
  fi
  if [ "$credential_present" -eq 1 ]; then
    case "$configured" in
      ""|/*) ;;
      *)
        printf 'compute secret load requires a trusted already-resolved Python runtime\n' >&2
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
      printf 'compute secret load requires a trusted managed or system Python runtime\n' >&2
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
    printf 'hetzner-research-compute requires a usable Python runtime\n' >&2
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

resolve_pinned_tool() {
  local env_name tool configured candidate candidate_dir
  env_name="$1"
  tool="$2"
  configured="${!env_name:-}"
  if [ -n "$configured" ]; then
    case "$configured" in
      /*) ;;
      *) return 2 ;;
    esac
  fi
  for candidate in "/usr/bin/$tool" "/bin/$tool"; do
    if [ ! -f "$candidate" ] || [ ! -x "$candidate" ]; then
      continue
    fi
    if [ -n "$configured" ] && [ ! "$configured" -ef "$candidate" ]; then
      continue
    fi
    candidate_dir="$(cd -- "$(dirname -- "$candidate")" && pwd -P)"
    printf '%s/%s\n' "$candidate_dir" "$(basename -- "$candidate")"
    return 0
  done
  [ -z "$configured" ] && return 1
  return 2
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
if { [ -n "$compute_pointer" ] || [ -n "$hcloud_token" ] || [ -n "$hcloud_ssh_keys" ]; } &&
  ! bind_selected_python_inode; then
  printf 'Hetzner credential launch could not bind the trusted Python inode.\n' >&2
  exit 127
fi
export AAS_RUNTIME_PYTHON="$PYTHON"

for tool_spec in \
  AAS_HETZNER_HCLOUD_BIN:hcloud \
  AAS_HETZNER_SSH_BIN:ssh \
  AAS_HETZNER_SCP_BIN:scp \
  AAS_HETZNER_RSYNC_BIN:rsync \
  AAS_HETZNER_SSH_KEYGEN_BIN:ssh-keygen; do
  tool_env="${tool_spec%%:*}"
  tool_name="${tool_spec#*:}"
  tool_path="$(resolve_pinned_tool "$tool_env" "$tool_name" || true)"
  if [ -n "${!tool_env:-}" ] && [ -z "$tool_path" ]; then
    printf '%s does not identify a trusted system executable\n' "$tool_env" >&2
    exit 127
  fi
  if [ -n "$tool_path" ]; then
    printf -v "$tool_env" '%s' "$tool_path"
    export "$tool_env"
  else
    unset "$tool_env"
  fi
done

unset PYTHONHOME PYTHONPATH PYTHONSTARTUP PYTHONINSPECT
export PATH=/usr/bin:/bin
unset AAS_SKILL_SECRETS_FILE AAS_PROVIDER_SECRETS_FILE
unset AXLE_API_KEY LEANEXPLORE_API_KEY OCR_SPACE_API_KEY OCR_SPACE_KEY
unset OCRSPACE_API_KEY OCRSPACE_KEY OPENCLAW_S2_API_KEY PATENTSVIEW_API_KEY
unset SEMANTIC_SCHOLAR_API_KEY UNPAYWALL_EMAIL ZENODO_TOKEN
unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN CLAUDE_API_KEY
unset CLAUDE_CODE_OAUTH_TOKEN COPILOT_GITHUB_TOKEN COPILOT_PROVIDER_API_KEY
unset COPILOT_PROVIDER_BEARER_TOKEN DEEPSEEK_API_KEY GEMINI_API_KEY
unset GH_TOKEN GITHUB_TOKEN GOOGLE_API_KEY GROK_API_KEY KIMI_API_KEY
unset MOONSHOT_API_KEY OPENAI_API_KEY OPENCODE_API_KEY XAI_API_KEY

command=("$PYTHON" -I "$ROOT/hetzner_research_compute.py" "$@")
if [ -n "$compute_pointer" ]; then
  secret_loader="$(resolve_secret_loader || true)"
  if [ -z "$secret_loader" ]; then
    printf 'managed compute secret loader is unavailable\n' >&2
    exit 127
  fi
  export AAS_COMPUTE_SECRETS_FILE="$compute_pointer"
  command=(
    "$PYTHON" -I "$secret_loader"
    --pointer-env AAS_COMPUTE_SECRETS_FILE
    --allow-key HCLOUD_TOKEN
    --allow-key HCLOUD_SSH_KEYS
    --allow-key KAGGLE_API_TOKEN
    --allow-key KAGGLE_CONFIG_DIR
    --export-key HCLOUD_TOKEN
    --export-key HCLOUD_SSH_KEYS
    --retain-env PYTHONPATH
    --retain-env AAS_AUTOLOOP_COMPUTE_WORKSPACE
    --retain-env AAS_HETZNER_HCLOUD_BIN
    --retain-env AAS_HETZNER_SSH_BIN
    --retain-env AAS_HETZNER_SCP_BIN
    --retain-env AAS_HETZNER_RSYNC_BIN
    --retain-env AAS_HETZNER_SSH_KEYGEN_BIN
    -- "${command[@]}"
  )
else
  [ -n "$hcloud_token" ] && export HCLOUD_TOKEN="$hcloud_token"
  [ -n "$hcloud_ssh_keys" ] && export HCLOUD_SSH_KEYS="$hcloud_ssh_keys"
  unset KAGGLE_API_TOKEN KAGGLE_CONFIG_DIR
fi

exec "${command[@]}"
