#!/bin/bash -p
set +x
set -euo pipefail

# Capture the one authorized credential with shell builtins, then remove every
# credential-bearing variable before path, interpreter, or helper discovery.
lean_explore_api_key="${LEANEXPLORE_API_KEY:-}"
lean_explore_site_packages="${AAS_LEANEXPLORE_SITE_PACKAGES:-}"
unset LEANEXPLORE_API_KEY AAS_SECRETS_FILE OPENCLAW_SECRETS_FILE
unset AAS_LEANEXPLORE_SITE_PACKAGES AAS_LEANEXPLORE_KEY_FD AAS_LEANEXPLORE_SITE_FD
unset AAS_LEANEXPLORE_CLOSURE_FD AAS_LEANEXPLORE_SITE_RELATIVE
unset AAS_LEANEXPLORE_WRAPPER_PATH
unset AAS_SKILL_SECRETS_FILE AAS_COMPUTE_SECRETS_FILE AAS_PROVIDER_SECRETS_FILE
unset AAS_CALIBRE_SECRETS_FILE AAS_ZOTERO_SECRETS_FILE
unset AAS_FILE_DELIVERY_SECRETS_FILE REMOTE_BRIDGE_SECRETS_FILE SEND_EMAIL_SECRETS_FILE
unset AXLE_API_KEY OCR_SPACE_API_KEY OCR_SPACE_KEY OCRSPACE_API_KEY OCRSPACE_KEY
unset OPENCLAW_S2_API_KEY S2_API_KEY PATENTSVIEW_API_KEY SEMANTIC_SCHOLAR_API_KEY
unset UNPAYWALL_EMAIL ZENODO_TOKEN ZOTERO_API_KEY WEBDAV_PASSWORD GDRIVE_CREDENTIALS
unset CALIBRE_GDRIVE_FOLDER_ID SMTP_PASSWORD ZULIP_API_KEY TELEGRAM_BOT_TOKEN
unset HCLOUD_TOKEN HCLOUD_SSH_KEYS KAGGLE_API_TOKEN KAGGLE_CONFIG_DIR
unset MODAL_TOKEN_ID MODAL_TOKEN_SECRET OPENAI_API_KEY ANTHROPIC_API_KEY
unset GEMINI_API_KEY GOOGLE_API_KEY DEEPSEEK_API_KEY XAI_API_KEY GROK_API_KEY
unset KIMI_API_KEY MOONSHOT_API_KEY OPENCODE_API_KEY GH_TOKEN GITHUB_TOKEN
unset PYTHONHOME PYTHONPATH PYTHONSTARTUP PYTHONINSPECT PYTHONWARNINGS PYTHONBREAKPOINT
unset LD_PRELOAD LD_LIBRARY_PATH LD_AUDIT DYLD_INSERT_LIBRARIES DYLD_LIBRARY_PATH
unset BASH_ENV ENV CDPATH GLOBIGNORE NODE_OPTIONS NODE_PATH 2>/dev/null || true
export PATH=/usr/bin:/bin

case "$lean_explore_api_key" in
  *$'\n'*|*$'\r'*)
    printf 'LeanExplore credential contains an unsupported line break.\n' >&2
    exit 2
    ;;
esac
if [ "${#lean_explore_api_key}" -gt 4096 ]; then
  printf 'LeanExplore credential exceeds the supported length.\n' >&2
  exit 2
fi
for argument in "$@"; do
  case "$argument" in
    --api-key|--api-key=*)
      printf 'LeanExplore credentials must be supplied through the managed environment authority, never argv.\n' >&2
      exit 2
      ;;
  esac
done

script_path="${BASH_SOURCE[0]:-$0}"
runtime_command_fd="${AAS_RUNTIME_COMMAND_FD:-}"
if [[ "$runtime_command_fd" =~ ^[0-9]+$ ]] && \
   { [ "$script_path" = "/proc/self/fd/$runtime_command_fd" ] || [ "$script_path" = "/dev/fd/$runtime_command_fd" ]; }; then
  script_path="${AAS_RUNTIME_COMMAND_PATH:-$script_path}"
fi
unset AAS_RUNTIME_COMMAND_FD AAS_RUNTIME_COMMAND_PATH
case "$script_path" in */*) script_parent="${script_path%/*}" ;; *) script_parent=. ;; esac
SCRIPT_DIR="$(cd -- "$script_parent" && builtin pwd -P)"
SCRIPT="$SCRIPT_DIR/lean_explore_mcp.py"
WRAPPER="$SCRIPT_DIR/run_lean_explore_mcp.sh"
# Patchable only in ephemeral test copies; production credential launches use
# the root-owned exact CSR generation.
lean_explore_exact_generation_enforcement=1

trusted_metadata() {
  local candidate="$1" expected_type="$2" metadata owner mode links actual_type current_uid
  metadata="$(/usr/bin/stat -Lc '%u:%a:%h:%F' -- "$candidate" 2>/dev/null || true)"
  IFS=: read -r owner mode links actual_type <<< "$metadata"
  current_uid="$(/usr/bin/id -u 2>/dev/null || true)"
  case "$owner" in 0|"$current_uid") ;; *) return 1 ;; esac
  [[ "$mode" =~ ^[0-7]{3,4}$ ]] || return 1
  (( (8#$mode & 8#022) == 0 )) || return 1
  [[ "$links" =~ ^[0-9]+$ ]] || return 1
  if [ "$expected_type" = file ]; then
    [ "$actual_type" = "regular file" ] || return 1
    [ "$owner" = 0 ] || [ "$links" -eq 1 ] || return 1
  else
    [ "$actual_type" = directory ] || return 1
  fi
}

root_owned_metadata() {
  local candidate="$1" expected_type="$2" metadata owner mode links actual_type
  metadata="$(/usr/bin/stat -Lc '%u:%a:%h:%F' -- "$candidate" 2>/dev/null || true)"
  IFS=: read -r owner mode links actual_type <<< "$metadata"
  [ "$owner" = 0 ] || return 1
  [[ "$mode" =~ ^[0-7]{3,4}$ ]] || return 1
  (( (8#$mode & 8#022) == 0 )) || return 1
  if [ "$expected_type" = file ]; then
    [ "$actual_type" = "regular file" ] && [ "$links" = 1 ]
  else
    [ "$actual_type" = directory ]
  fi
}

trusted_directory_chain() {
  local current="$1" metadata owner mode actual_type current_uid writable protected
  current_uid="$(/usr/bin/id -u 2>/dev/null || true)"
  while :; do
    [ ! -L "$current" ] || return 1
    metadata="$(/usr/bin/stat -Lc '%u:%a:%F' -- "$current" 2>/dev/null || true)"
    IFS=: read -r owner mode actual_type <<< "$metadata"
    case "$owner" in 0|"$current_uid") ;; *) return 1 ;; esac
    [ "$actual_type" = directory ] || return 1
    [[ "$mode" =~ ^[0-7]{3,4}$ ]] || return 1
    writable=$((8#$mode & 8#022))
    protected=$((8#$mode & 8#1000))
    if [ "$writable" -ne 0 ] && { [ "$owner" != 0 ] || [ "$protected" -eq 0 ]; }; then
      return 1
    fi
    [ "$current" = / ] && return 0
    current="${current%/*}"
    [ -n "$current" ] || current=/
  done
}

if [ ! -f "$SCRIPT" ] || [ -L "$SCRIPT" ] || ! trusted_metadata "$SCRIPT" file; then
  printf 'runtime helper is unavailable or untrusted.\n' >&2
  exit 127
fi
if [ "$lean_explore_exact_generation_enforcement" -eq 1 ] && \
   [ -n "$lean_explore_api_key" ] && ! root_owned_metadata "$SCRIPT" file; then
  printf 'credential-bearing LeanExplore launch requires an immutable exact-generation helper.\n' >&2
  exit 127
fi

credential_present=0
[ -n "$lean_explore_api_key" ] && credential_present=1
configured_python="${AAS_RUNTIME_PYTHON:-}"
if [ "$credential_present" -eq 1 ]; then
  case "$configured_python" in ""|/*) ;; *)
    printf 'credential-bearing LeanExplore launch requires an already-resolved Python runtime.\n' >&2
    exit 127
    ;;
  esac
  system_python=/usr/bin/python3
  if [ ! -f "$system_python" ] || [ ! -x "$system_python" ] || \
     ! trusted_metadata "$system_python" file; then
    printf 'credential-bearing LeanExplore launch requires the trusted system Python runtime.\n' >&2
    exit 127
  fi
  if [ -n "$configured_python" ] && [ ! "$configured_python" -ef "$system_python" ]; then
    printf 'credential-bearing LeanExplore launch rejected the selected Python runtime.\n' >&2
    exit 127
  fi
  selected_python="${configured_python:-$system_python}"
  exec {AAS_LEANEXPLORE_PYTHON_FD}<"$selected_python"
  if [ -e "/proc/self/fd/$AAS_LEANEXPLORE_PYTHON_FD" ]; then
    PYTHON="/proc/self/fd/$AAS_LEANEXPLORE_PYTHON_FD"
  elif [ -e "/dev/fd/$AAS_LEANEXPLORE_PYTHON_FD" ]; then
    PYTHON="/dev/fd/$AAS_LEANEXPLORE_PYTHON_FD"
  else
    printf 'credential-bearing LeanExplore launch could not bind Python.\n' >&2
    exit 127
  fi
  [ "$PYTHON" -ef "$system_python" ] || exit 127
  exec {AAS_LEANEXPLORE_SCRIPT_FD}<"$SCRIPT"
  if [ -e "/proc/self/fd/$AAS_LEANEXPLORE_SCRIPT_FD" ]; then
    SCRIPT="/proc/self/fd/$AAS_LEANEXPLORE_SCRIPT_FD"
  elif [ -e "/dev/fd/$AAS_LEANEXPLORE_SCRIPT_FD" ]; then
    SCRIPT="/dev/fd/$AAS_LEANEXPLORE_SCRIPT_FD"
  else
    printf 'credential-bearing LeanExplore launch could not bind its helper.\n' >&2
    exit 127
  fi
else
  if [ -n "$configured_python" ]; then
    case "$configured_python" in
      /*) PYTHON="$configured_python" ;;
      */*) printf 'AAS_RUNTIME_PYTHON must be an absolute path or command name.\n' >&2; exit 127 ;;
      *) PYTHON="$(command -v "$configured_python" 2>/dev/null || true)" ;;
    esac
  else
    PYTHON="$(command -v python3 2>/dev/null || command -v python 2>/dev/null || true)"
  fi
  if [ -z "$PYTHON" ] || [ ! -f "$PYTHON" ] || [ ! -x "$PYTHON" ]; then
    printf 'error: no usable Python runtime found. Set AAS_RUNTIME_PYTHON or install Python 3.\n' >&2
    exit 127
  fi
fi

if [ "${1:-}" = serve ]; then
  if [ -z "$lean_explore_site_packages" ]; then
    printf 'LeanExplore MCP serve requires AAS_LEANEXPLORE_SITE_PACKAGES for exact 1.2.1.\n' >&2
    exit 78
  fi
  case "$lean_explore_site_packages" in
    /*/site-packages) ;;
    *) printf 'LeanExplore site-packages must be an absolute site-packages directory.\n' >&2; exit 78 ;;
  esac
  current_uid="$(/usr/bin/id -u)"
  passwd_record="$(/usr/bin/getent passwd "$current_uid" 2>/dev/null || true)"
  IFS=: read -r _ _ _ _ _ account_home _ <<< "$passwd_record"
  if [ -z "$account_home" ] || [ "${account_home#/}" = "$account_home" ]; then
    printf 'LeanExplore could not resolve the account home.\n' >&2
    exit 78
  fi
  closure_root="$account_home/.local/share/coding-system/python-closure/lean-explore"
  case "$lean_explore_site_packages" in
    "$closure_root"/lib/python3.[0-9]/site-packages|"$closure_root"/lib/python3.[0-9][0-9]/site-packages|"$closure_root"/lib/python3.[0-9][0-9][0-9]/site-packages) ;;
    *) printf 'LeanExplore requires the exact CSR lean-explore closure path.\n' >&2; exit 78 ;;
  esac
  if [ ! -d "$closure_root" ] || [ -L "$closure_root" ] || \
     ! root_owned_metadata "$closure_root" directory || \
     [ ! -f "$closure_root/.coding-system-python-closure.json" ] || \
     ! root_owned_metadata "$closure_root/.coding-system-python-closure.json" file; then
    printf 'LeanExplore CSR closure root or marker is unavailable or untrusted.\n' >&2
    exit 78
  fi
  exec {AAS_LEANEXPLORE_CLOSURE_FD}<"$closure_root"
  if [ -e "/proc/self/fd/$AAS_LEANEXPLORE_CLOSURE_FD" ]; then
    lean_explore_bound_root="/proc/self/fd/$AAS_LEANEXPLORE_CLOSURE_FD"
  elif [ -e "/dev/fd/$AAS_LEANEXPLORE_CLOSURE_FD" ]; then
    lean_explore_bound_root="/dev/fd/$AAS_LEANEXPLORE_CLOSURE_FD"
  else
    printf 'LeanExplore closure cannot be descriptor-bound on this host.\n' >&2
    exit 78
  fi
  [ "$lean_explore_bound_root" -ef "$closure_root" ] || exit 78
  AAS_LEANEXPLORE_SITE_RELATIVE="${lean_explore_site_packages#"$closure_root"/}"
  export AAS_LEANEXPLORE_CLOSURE_FD AAS_LEANEXPLORE_SITE_RELATIVE
fi

export PYTHONDONTWRITEBYTECODE=1 PYTHONUTF8=1 PYTHONIOENCODING=utf-8
if [ -n "$lean_explore_api_key" ]; then
  exec {AAS_LEANEXPLORE_KEY_FD}<<<"$lean_explore_api_key"
  export AAS_LEANEXPLORE_KEY_FD
fi
export AAS_LEANEXPLORE_WRAPPER_PATH="$WRAPPER"
exec "$PYTHON" -I "$SCRIPT" "$@"
