#!/bin/bash -p
# Thin POSIX wrapper → force_loop_cli.py
set -euo pipefail
compute_pointer="${AAS_COMPUTE_SECRETS_FILE:-}"
provider_pointer="${AAS_PROVIDER_SECRETS_FILE:-}"
unset AAS_SECRETS_FILE OPENCLAW_SECRETS_FILE AAS_SKILL_SECRETS_FILE
unset AAS_COMPUTE_SECRETS_FILE AAS_PROVIDER_SECRETS_FILE REMOTE_BRIDGE_SECRETS_FILE
unset AAS_CALIBRE_SECRETS_FILE AAS_ZOTERO_SECRETS_FILE AAS_FILE_DELIVERY_SECRETS_FILE
unset REMOTE_BRIDGE_SECRETS_FILE SEND_EMAIL_SECRETS_FILE
unset AXLE_API_KEY LEANEXPLORE_API_KEY OCR_SPACE_API_KEY OCR_SPACE_KEY OCRSPACE_API_KEY OCRSPACE_KEY
unset OPENCLAW_S2_API_KEY S2_API_KEY PATENTSVIEW_API_KEY SEMANTIC_SCHOLAR_API_KEY UNPAYWALL_EMAIL ZENODO_TOKEN
unset ZOTERO_API_KEY WEBDAV_PASSWORD GDRIVE_CREDENTIALS CALIBRE_GDRIVE_FOLDER_ID
unset SMTP_HOST SMTP_PORT SMTP_USER SMTP_PASSWORD SMTP_FROM SMTP_SECURITY SMTP_TIMEOUT SMTP_ACCOUNT
unset ZULIP_ORG_URL ZULIP_SITE ZULIP_EMAIL ZULIP_API_KEY TELEGRAM_BOT_TOKEN
unset HCLOUD_TOKEN HCLOUD_SSH_KEYS KAGGLE_API_TOKEN KAGGLE_CONFIG_DIR
unset MODAL_TOKEN_ID MODAL_TOKEN_SECRET
unset OPENAI_API_KEY ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN CLAUDE_API_KEY CLAUDE_CODE_OAUTH_TOKEN
unset COPILOT_GITHUB_TOKEN COPILOT_PROVIDER_API_KEY COPILOT_PROVIDER_BEARER_TOKEN
unset GEMINI_API_KEY GOOGLE_API_KEY DEEPSEEK_API_KEY XAI_API_KEY GROK_API_KEY
unset KIMI_API_KEY MOONSHOT_API_KEY OPENCODE_API_KEY GH_TOKEN GITHUB_TOKEN
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
SCRIPT_DIR="$(cd -- "$script_parent" && builtin pwd -P)"
PYTHON="${AAS_RUNTIME_PYTHON:-}"

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

# bash 4.1+ allocates the descriptor number itself via ``exec {var}<``.
# Older substrates (macOS /bin/bash 3.2) parse ``{var}`` as a literal command
# word, so a deterministic script-global counter supplies high descriptor
# numbers there.  Descriptors opened with ``exec`` stay open across the final
# exec either way, so bound descriptors survive into the launched child.
bind_selected_python_next_fd=200

# ``test -ef`` resolves Linux's /proc/self/fd symlinks to the underlying
# file, so equality there compares device and inode exactly.  BSD fdesc
# nodes (macOS /dev/fd) report the fdesc device with the underlying inode,
# so ``-ef`` can never match through them; for those paths identity is the
# strongest tuple fdesc proxies faithfully -- inode, owner, link count, and
# file type.  Mode is excluded because fdesc synthesizes it from the
# descriptor's open flags.
bound_descriptor_matches_selected() {
  local descriptor_path="$1" selected_path="$2" descriptor_id selected_id
  if [ "$descriptor_path" -ef "$selected_path" ]; then
    return 0
  fi
  case "$descriptor_path" in
    /dev/fd/*) ;;
    *) return 1 ;;
  esac
  descriptor_id="$(/usr/bin/stat -Lc '%i:%u:%h:%F' -- "$descriptor_path" 2>/dev/null || \
    /usr/bin/stat -Lf '%i:%u:%l:%HT' "$descriptor_path" 2>/dev/null || true)"
  selected_id="$(/usr/bin/stat -Lc '%i:%u:%h:%F' -- "$selected_path" 2>/dev/null || \
    /usr/bin/stat -Lf '%i:%u:%l:%HT' "$selected_path" 2>/dev/null || true)"
  [ -n "$descriptor_id" ] && [ "$descriptor_id" = "$selected_id" ]
}

# BSD fdesc nodes also synthesize their permission bits from the descriptor's
# open flags, so execve on a read-only /dev/fd path is denied no matter what
# mode the underlying file carries.  Where /proc is unavailable a launch
# therefore execs the attested real path -- the descriptor stays bound for
# identity attestation -- while Linux /proc paths continue to use the bound
# inode exactly.
exec_path_for_bound() {
  local bound="$1" attested="$2"
  case "$bound" in
    /dev/fd/*) printf '%s\n' "$attested" ;;
    *) printf '%s\n' "$bound" ;;
  esac
}

bind_selected_python_inode() {
  local selected="$PYTHON"
  if [ "${BASH_VERSINFO[0]}" -gt 4 ] || \
     { [ "${BASH_VERSINFO[0]}" -eq 4 ] && [ "${BASH_VERSINFO[1]}" -ge 1 ]; }; then
    exec {AAS_PYTHON_EXEC_FD}<"$selected" || return 1
  else
    AAS_PYTHON_EXEC_FD="$bind_selected_python_next_fd"
    bind_selected_python_next_fd=$((bind_selected_python_next_fd + 1))
    eval "exec ${AAS_PYTHON_EXEC_FD}<\"\$selected\"" || return 1
  fi
  if [ -e "/proc/self/fd/$AAS_PYTHON_EXEC_FD" ]; then
    PYTHON="/proc/self/fd/$AAS_PYTHON_EXEC_FD"
  elif [ -e "/dev/fd/$AAS_PYTHON_EXEC_FD" ]; then
    PYTHON="/dev/fd/$AAS_PYTHON_EXEC_FD"
  else
    eval "exec ${AAS_PYTHON_EXEC_FD}<&-"
    return 1
  fi
  bound_descriptor_matches_selected "$PYTHON" "$selected"
}

has_runtime_credentials() {
  [ -n "$compute_pointer" ] || [ -n "$provider_pointer" ]
}

# start and replace both hand credentials to a launched loop; every other
# subcommand runs with the pointers and provider tokens scrubbed.
credential_subcommand() {
  case "${1:-}" in
    start|replace) return 0 ;;
    *) return 1 ;;
  esac
}

resolve_python() {
  local configured resolved resolved_dir candidate credential_present
  configured="${AAS_RUNTIME_PYTHON:-}"
  credential_present=0
  if has_runtime_credentials; then
    credential_present=1
  fi
  if [ "$credential_present" -eq 1 ]; then
    case "$configured" in
      ""|/*) ;;
      *)
        printf 'force-loop secret load requires a trusted already-resolved Python runtime\n' >&2
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
      printf 'force-loop secret load requires a trusted managed or system Python runtime\n' >&2
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
    printf 'force-loop requires a usable Python runtime\n' >&2
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

if ! PYTHON="$(resolve_python)"; then
  exit 127
fi
resolved_python="$PYTHON"
if credential_subcommand "${1:-}" && has_runtime_credentials && ! bind_selected_python_inode; then
  printf 'Credential-bearing force-loop launch could not bind the trusted Python inode.\n' >&2
  exit 127
fi
PYTHON_EXEC="$(exec_path_for_bound "$PYTHON" "$resolved_python")"
# Children re-exec this interpreter by name, and a descriptor alias is not
# resolvable from a subprocess, so hand them the real path; this launch still
# runs through the bound inode via PYTHON_EXEC.
export AAS_RUNTIME_PYTHON="$resolved_python"
[ -n "$compute_pointer" ] && export AAS_COMPUTE_SECRETS_FILE="$compute_pointer"
[ -n "$provider_pointer" ] && export AAS_PROVIDER_SECRETS_FILE="$provider_pointer"
unset AAS_SKILL_SECRETS_FILE
unset AXLE_API_KEY LEANEXPLORE_API_KEY OCR_SPACE_API_KEY OCR_SPACE_KEY
unset OCRSPACE_API_KEY OCRSPACE_KEY OPENCLAW_S2_API_KEY PATENTSVIEW_API_KEY
unset SEMANTIC_SCHOLAR_API_KEY UNPAYWALL_EMAIL ZENODO_TOKEN

python_args=()
if has_runtime_credentials; then
  unset PYTHONHOME PYTHONPATH PYTHONSTARTUP PYTHONINSPECT
  python_args=(-I)
fi

if ! credential_subcommand "${1:-}"; then
  unset AAS_COMPUTE_SECRETS_FILE AAS_PROVIDER_SECRETS_FILE REMOTE_BRIDGE_SECRETS_FILE
  unset HCLOUD_TOKEN HCLOUD_SSH_KEYS KAGGLE_API_TOKEN KAGGLE_CONFIG_DIR
  unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN CLAUDE_API_KEY
  unset CLAUDE_CODE_OAUTH_TOKEN COPILOT_GITHUB_TOKEN COPILOT_PROVIDER_API_KEY
  unset COPILOT_PROVIDER_BEARER_TOKEN DEEPSEEK_API_KEY
  unset GEMINI_API_KEY GH_TOKEN GITHUB_TOKEN GOOGLE_API_KEY GROK_API_KEY
  unset KIMI_API_KEY MOONSHOT_API_KEY OPENAI_API_KEY OPENCODE_API_KEY XAI_API_KEY
fi
exec "$PYTHON_EXEC" ${python_args[@]+"${python_args[@]}"} "$SCRIPT_DIR/force_loop_cli.py" "$@"
