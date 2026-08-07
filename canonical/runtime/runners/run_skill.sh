#!/bin/bash -p
set -euo pipefail

# Capture selectors using shell builtins, then remove all credential material
# before path resolution or any external executable can run.
caller_path="${PATH:-/usr/bin:/bin}"
skill_pointer="${AAS_SKILL_SECRETS_FILE:-}"
compute_pointer="${AAS_COMPUTE_SECRETS_FILE:-}"
provider_pointer="${AAS_PROVIDER_SECRETS_FILE:-}"
calibre_pointer="${AAS_CALIBRE_SECRETS_FILE:-}"
zotero_pointer="${AAS_ZOTERO_SECRETS_FILE:-}"
delivery_pointer="${AAS_FILE_DELIVERY_SECRETS_FILE:-}"
remote_pointer="${REMOTE_BRIDGE_SECRETS_FILE:-}"
email_pointer="${SEND_EMAIL_SECRETS_FILE:-}"
unset AAS_SECRETS_FILE OPENCLAW_SECRETS_FILE AAS_SKILL_SECRETS_FILE
unset AAS_COMPUTE_SECRETS_FILE AAS_PROVIDER_SECRETS_FILE
unset AAS_CALIBRE_SECRETS_FILE AAS_ZOTERO_SECRETS_FILE
unset AAS_FILE_DELIVERY_SECRETS_FILE REMOTE_BRIDGE_SECRETS_FILE SEND_EMAIL_SECRETS_FILE

ambient_secret_keys=(
  AXLE_API_KEY LEANEXPLORE_API_KEY OCR_SPACE_API_KEY OCR_SPACE_KEY OCRSPACE_API_KEY OCRSPACE_KEY
  OPENCLAW_S2_API_KEY S2_API_KEY PATENTSVIEW_API_KEY SEMANTIC_SCHOLAR_API_KEY UNPAYWALL_EMAIL ZENODO_TOKEN
  ZOTERO_API_KEY WEBDAV_PASSWORD GDRIVE_CREDENTIALS CALIBRE_GDRIVE_FOLDER_ID
  SMTP_HOST SMTP_PORT SMTP_USER SMTP_PASSWORD SMTP_FROM SMTP_SECURITY SMTP_TIMEOUT SMTP_ACCOUNT
  SMTP_FROM_NAME SMTP_REPLY_TO SMTP_CC SMTP_BCC SMTP_SIGNATURE SMTP_SIGNATURE_HTML
  SMTP_REPLY_TO_SELF SMTP_BCC_SELF SMTP_PGP_SIGN SMTP_PGP_KEY SMTP_PGP_PASSPHRASE SMTP_GNUPG_HOME
  ZULIP_ORG_URL ZULIP_SITE ZULIP_EMAIL ZULIP_API_KEY ZULIP_CONTROL_STREAM ZULIP_TOPIC_PREFIX
  ZULIP_ALLOWED_USER_IDS TELEGRAM_BOT_TOKEN TELEGRAM_ALLOWED_CHAT_IDS TELEGRAM_ALLOWED_USER_IDS
  TELEGRAM_MODE HCLOUD_TOKEN HCLOUD_SSH_KEYS KAGGLE_API_TOKEN KAGGLE_CONFIG_DIR KAGGLE_USERNAME KAGGLE_KEY
  MODAL_TOKEN_ID MODAL_TOKEN_SECRET OPENAI_API_KEY ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN
  CLAUDE_API_KEY CLAUDE_CODE_OAUTH_TOKEN COPILOT_GITHUB_TOKEN COPILOT_PROVIDER_API_KEY
  COPILOT_PROVIDER_BEARER_TOKEN GEMINI_API_KEY GOOGLE_API_KEY DEEPSEEK_API_KEY XAI_API_KEY
  GROK_API_KEY KIMI_API_KEY MOONSHOT_API_KEY OPENCODE_API_KEY GH_TOKEN GITHUB_TOKEN
  GROQ_API_KEY TOGETHER_API_KEY OPENROUTER_API_KEY
)
for key in "${ambient_secret_keys[@]}"; do
  unset "$key"
done
unset BASH_ENV ENV CDPATH GLOBIGNORE 2>/dev/null || true
export PATH=/usr/bin:/bin

script_path="${BASH_SOURCE[0]}"
case "$script_path" in
  */*) runtime_parent="${script_path%/*}" ;;
  *) runtime_parent=. ;;
esac
# Installed runtimes place this launcher at the runtime root.  CSR's immutable
# exact-pin generation retains the canonical source layout, where the launcher
# lives in ``runners/`` beside ``workspace/``.  Credential launches use only
# the latter; mutable installed copies remain available for noncredential work.
runtime_parent_real="$(cd -- "$runtime_parent" && builtin pwd -P)"
source_layout=0
if [ "${runtime_parent_real##*/}" = runners ] && \
   [ -d "$runtime_parent_real/../skills" ]; then
  runtime_root="$(cd -- "$runtime_parent_real/.." && builtin pwd -P)"
  source_layout=1
else
  runtime_root="$runtime_parent_real"
fi
if [ "$source_layout" -eq 1 ]; then
  default_workspace="$runtime_root"
else
  default_workspace="$runtime_root/workspace"
fi
workspace="$default_workspace"
if [ "${AAS_ALLOW_EXTERNAL_RUNTIME_WORKSPACE:-}" = "1" ] && [ -n "${AAS_RUNTIME_WORKSPACE:-}" ]; then
  workspace="$AAS_RUNTIME_WORKSPACE"
fi

if [ "$#" -lt 1 ]; then
  printf 'usage: %s <runtime-relative-script> [args...]\n' "$0" >&2
  exit 2
fi
command_rel="$1"
shift
case "$command_rel" in
  /*|*..*|*\\*)
    printf 'refusing unsafe runtime command path: %s\n' "$command_rel" >&2
    exit 2
    ;;
esac

command_path="$workspace/$command_rel"
runtime_real="$(cd -- "$runtime_root" && pwd -P)"
default_workspace_real="$(cd -- "$default_workspace" && pwd -P)"
workspace_real="$(cd -- "$workspace" && pwd -P)"
command_dir="${command_path%/*}"
if [ ! -d "$command_dir" ]; then
  printf 'runtime command directory not found: %s\n' "$command_dir" >&2
  exit 127
fi
command_dir_real="$(cd -- "$command_dir" && pwd -P)"
case "$command_dir_real/" in
  "$workspace_real"/*) ;;
  *)
    printf 'refusing runtime command outside workspace: %s\n' "$command_path" >&2
    exit 2
    ;;
esac
if [ ! -f "$command_path" ] || [ -L "$command_path" ]; then
  printf 'runtime command must be a regular non-link file: %s\n' "$command_path" >&2
  exit 127
fi

export AAS_RUNTIME_ROOT="$runtime_real"
export AAS_RUNTIME_WORKSPACE="$workspace_real"
export OPENCLAW_WORKSPACE="$workspace_real"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

# Secrets are empty-by-default for every runtime command, including commands
# that have no credential projection contract.  A mapped command may republish
# only its exact subset below.  The final three provider names are deliberately
# scrub-only: no managed AAS consumer currently accepts them as authorities.
default_private_projection() {
  local current="$1" relative="$2" candidate
  if [ -n "$current" ] || [ -z "${HOME:-}" ]; then
    printf '%s\n' "$current"
    return
  fi
  candidate="$HOME/.config/ai-agents-skills/$relative"
  if [ -e "$candidate" ] || [ -L "$candidate" ]; then
    printf '%s\n' "$candidate"
  fi
}

credential_contract=0
allow_managed_selector_advisory=0
projection_pointer_env=AAS_UNUSED_SECRETS_FILE
projection_format=env
projection_no_load=1
projection_retain_pointer=0
projection_allow_keys=()
projection_export_keys=()
projection_retain_env=()
arl_credential_broker=0

select_flat_projection() {
  projection_pointer_env="$1"
  projection_format="$2"
  projection_no_load=0
  projection_retain_pointer=0
  shift 2
  projection_allow_keys=("$@")
  projection_export_keys=("$@")
}

case "$command_rel" in
  skills/axiom-axle-mcp/run_axiom_axle_mcp.sh)
    credential_contract=1
    [ -n "$skill_pointer" ] && export AAS_SKILL_SECRETS_FILE="$skill_pointer"
    select_flat_projection AAS_SKILL_SECRETS_FILE env AXLE_API_KEY
    ;;
  skills/lean-explore-mcp/run_lean_explore_mcp.sh)
    credential_contract=1
    [ -n "$skill_pointer" ] && export AAS_SKILL_SECRETS_FILE="$skill_pointer"
    select_flat_projection AAS_SKILL_SECRETS_FILE env LEANEXPLORE_API_KEY
    projection_retain_env+=(AAS_LEANEXPLORE_SITE_PACKAGES)
    ;;
  skills/docling/run_docling.sh)
    credential_contract=1
    [ -n "$skill_pointer" ] && export AAS_SKILL_SECRETS_FILE="$skill_pointer"
    select_flat_projection AAS_SKILL_SECRETS_FILE env \
      OCR_SPACE_API_KEY OCR_SPACE_KEY OCRSPACE_API_KEY OCRSPACE_KEY
    projection_retain_env+=(
      AAS_DOCLING_PRESET DOCLING_PRESET DOCLING_DEVICE DOCLING_NUM_THREADS
      DOCLING_ARTIFACTS_PATH
    )
    ;;
  skills/research-digest-wrapper/run_research_digest.sh)
    credential_contract=1
    [ -n "$skill_pointer" ] && export AAS_SKILL_SECRETS_FILE="$skill_pointer"
    select_flat_projection AAS_SKILL_SECRETS_FILE env OPENCLAW_S2_API_KEY
    ;;
  skills/submission-venue-selector/run_submission_venue_selector.sh)
    credential_contract=1
    [ -n "$skill_pointer" ] && export AAS_SKILL_SECRETS_FILE="$skill_pointer"
    select_flat_projection AAS_SKILL_SECRETS_FILE env SEMANTIC_SCHOLAR_API_KEY UNPAYWALL_EMAIL
    ;;
  skills/lean-research-library/run_lean_research_library.sh)
    credential_contract=1
    [ -n "$skill_pointer" ] && export AAS_SKILL_SECRETS_FILE="$skill_pointer"
    select_flat_projection AAS_SKILL_SECRETS_FILE env ZENODO_TOKEN
    ;;
  skills/zotero/run_zot.sh)
    credential_contract=1
    zotero_pointer="$(default_private_projection "$zotero_pointer" zotero-secrets.json)"
    [ -n "$zotero_pointer" ] && export AAS_ZOTERO_SECRETS_FILE="$zotero_pointer"
    select_flat_projection AAS_ZOTERO_SECRETS_FILE json \
      ZOTERO_API_KEY WEBDAV_PASSWORD GDRIVE_CREDENTIALS SEMANTIC_SCHOLAR_API_KEY
    ;;
  skills/calibre/run_cal.sh)
    credential_contract=1
    calibre_pointer="$(default_private_projection "$calibre_pointer" calibre-secrets.json)"
    [ -n "$calibre_pointer" ] && export AAS_CALIBRE_SECRETS_FILE="$calibre_pointer"
    select_flat_projection AAS_CALIBRE_SECRETS_FILE json \
      GDRIVE_CREDENTIALS CALIBRE_GDRIVE_FOLDER_ID
    ;;
  skills/send-email/run_send_email.sh|skills/send-email/send_email.py)
    credential_contract=1
    allow_managed_selector_advisory=1
    [ -n "$email_pointer" ] && export SEND_EMAIL_SECRETS_FILE="$email_pointer"
    projection_pointer_env=SEND_EMAIL_SECRETS_FILE
    projection_retain_pointer=1
    projection_retain_env+=(SEND_EMAIL_ADDRESS_BOOK)
    ;;
  skills/remote-bridge/run_remote_bridge.sh|skills/remote-bridge/remote_bridge.py|skills/remote-bridge/dispatch_aas.py)
    credential_contract=1
    allow_managed_selector_advisory=1
    [ -n "$remote_pointer" ] && export REMOTE_BRIDGE_SECRETS_FILE="$remote_pointer"
    projection_pointer_env=REMOTE_BRIDGE_SECRETS_FILE
    projection_retain_pointer=1
    projection_retain_env+=(
      AAS_REMOTE_STRICT_NOTIFY_CHANNEL AAS_REMOTE_JOB_ID AAS_REMOTE_PROVIDER
      AAS_REMOTE_WORKSPACE AAS_REMOTE_BRIDGE_STATE AAS_REMOTE_ALLOW_LOCAL_CLI
    )
    ;;
  skills/vnthuquan/run_vnthuquan.sh|skills/vnthuquan/vnthuquan_wrapper.py)
    credential_contract=1
    calibre_pointer="$(default_private_projection "$calibre_pointer" calibre-secrets.json)"
    [ -n "$calibre_pointer" ] && export AAS_CALIBRE_SECRETS_FILE="$calibre_pointer"
    projection_retain_env+=(VNTHUQUAN_TARGET)
    ;;
  skills/zotero/send_file.sh)
    credential_contract=1
    [ -n "$delivery_pointer" ] && export AAS_FILE_DELIVERY_SECRETS_FILE="$delivery_pointer"
    ;;
  skills/zotero/send_telegram.sh|skills/zotero/send_queue_worker.sh|skills/zotero/send_queue.py)
    credential_contract=1
    ;;
  skills/modal-research-compute/run_modal_research_compute.sh|skills/kaggle-research-compute/run_kaggle_research_compute.sh|skills/hetzner-research-compute/run_hetzner_research_compute.sh|skills/hetzner-research-compute/run_hetzner_reaper.sh)
    credential_contract=1
    [ -n "$compute_pointer" ] && export AAS_COMPUTE_SECRETS_FILE="$compute_pointer"
    projection_pointer_env=AAS_COMPUTE_SECRETS_FILE
    projection_retain_pointer=1
    projection_retain_env+=(
      AAS_AUTOLOOP_COMPUTE_WORKSPACE
      AAS_HETZNER_HCLOUD_BIN AAS_HETZNER_SSH_BIN AAS_HETZNER_SCP_BIN
      AAS_HETZNER_RSYNC_BIN AAS_HETZNER_SSH_KEYGEN_BIN
    )
    ;;
  skills/autonomous-research-loop-runtime/run_autonomous_research_loop.sh|skills/autonomous-research-loop-runtime/force-loop/run_force_loop.sh)
    credential_contract=1
    [ -n "$compute_pointer" ] && export AAS_COMPUTE_SECRETS_FILE="$compute_pointer"
    [ -n "$provider_pointer" ] && export AAS_PROVIDER_SECRETS_FILE="$provider_pointer"
    projection_pointer_env=AAS_PROVIDER_SECRETS_FILE
    projection_retain_pointer=1
    if [ -n "$compute_pointer" ] || [ -n "$provider_pointer" ]; then
      arl_credential_broker=1
    fi
    projection_retain_env+=(
      AAS_COMPUTE_SECRETS_FILE AAS_REMOTE_STRICT_NOTIFY_CHANNEL
      AAS_FORCE_LOOP_POLICY_FILE AAS_FORCE_LOOP_COMPUTE_LANES
      AAS_AUTOLOOP_COMPUTE_WORKSPACE
      AAS_AUTOLOOP_GOAL_PRIORITY AAS_AUTOLOOP_NOTIFY
      AAS_AUTOLOOP_FORMAL_POLICY AAS_AUTOLOOP_FORMAL_TYPECHECK
      AAS_AUTOLOOP_PANEL AAS_AUTOLOOP_PANEL_PROVIDERS
      AAS_AUTOLOOP_PRIMARY_PROVIDER AAS_AUTOLOOP_NOTIFY_BODY_PROFILE
      AAS_AUTOLOOP_HOST_MEDIATED_SUBMISSION AAS_ALLOW_RAW_NOTIFY_CMD
      AAS_AUTOLOOP_NOTIFY_CMD AAS_AUTOLOOP_CANDIDATE_ID
      AAS_AUTOLOOP_DISPATCH_ID AAS_AUTOLOOP_EVIDENCE_DIR
      AAS_AUTOLOOP_EVIDENCE_ROOT AAS_AUTOLOOP_EXTERNAL_NOTIFY_EGRESS
      AAS_AUTOLOOP_EXTERNAL_PANEL_EGRESS AAS_AUTOLOOP_REGISTRY
      AAS_AUTOLOOP_RUN_ID AAS_AUTOLOOP_RESEARCH_TITLE
      AAS_AUTOLOOP_RESOURCE_ADDRESS_SPACE_MIB
      AAS_AUTOLOOP_RESOURCE_CPU_QUOTA_PERCENT AAS_AUTOLOOP_RESOURCE_CPU_SECONDS
      AAS_AUTOLOOP_RESOURCE_FILE_SIZE_MIB AAS_AUTOLOOP_RESOURCE_MAX_PROCESSES
      AAS_AUTOLOOP_RESOURCE_MEMORY_MIB AAS_AUTOLOOP_RESOURCE_OPEN_FILES
      AAS_AUTOLOOP_RESOURCE_OUTPUT_MIB AAS_AUTOLOOP_RESOURCE_SWAP_MIB
    )
    ;;
esac

# A legacy skill pointer on an unmapped command has an empty schema.  A
# non-empty file is rejected by the strict loader; it is never broadly exposed.
if [ "$credential_contract" -eq 0 ] && [ -n "$skill_pointer" ]; then
  credential_contract=1
  export AAS_SKILL_SECRETS_FILE="$skill_pointer"
  projection_pointer_env=AAS_SKILL_SECRETS_FILE
  projection_no_load=0
fi
if [ "$credential_contract" -eq 0 ] && { [ -n "$compute_pointer" ] || [ -n "$provider_pointer" ]; }; then
  credential_contract=1
fi

# This literal is intentionally patchable only in ephemeral unit-test copies.
# Production launchers must execute credential consumers from CSR's root-owned,
# content-addressed AAS component generation, never a same-UID runtime copy.
credential_runtime_enforcement=1

root_owned_metadata() {
  local candidate="$1" expected_type="$2" metadata owner mode links actual_type
  metadata="$(/usr/bin/stat -Lc '%u:%a:%h:%F' -- "$candidate" 2>/dev/null || \
    /usr/bin/stat -Lf '%u:%Lp:%l:%HT' "$candidate" 2>/dev/null || true)"
  IFS=: read -r owner mode links actual_type <<< "$metadata"
  case "$actual_type" in
    "Regular File") actual_type="regular file" ;;
    Directory) actual_type=directory ;;
  esac
  [ "$owner" = 0 ] || return 1
  [[ "$mode" =~ ^[0-7]{3,4}$ ]] || return 1
  (( (8#$mode & 8#022) == 0 )) || return 1
  [[ "$links" =~ ^[0-9]+$ ]] || return 1
  if [ "$expected_type" = file ]; then
    [ "$actual_type" = "regular file" ] && [ "$links" -eq 1 ]
  else
    [ "$actual_type" = directory ]
  fi
}

trusted_credential_runtime_generation() {
  local component_root pin manifest current expected
  if ! [[ "$runtime_real" =~ ^/usr/local/libexec/coding-system/components/ai-agents-skills/([0-9a-f]{40})/canonical/runtime$ ]]; then
    return 1
  fi
  pin="${BASH_REMATCH[1]}"
  component_root="${runtime_real%/canonical/runtime}"
  [ "${component_root##*/}" = "$pin" ] || return 1
  manifest="$component_root/manifest/credential-runtime.json"
  root_owned_metadata "$manifest" file || return 1
  for expected in "$runtime_real" "$workspace_real" "$command_path"; do
    current="$expected"
    while :; do
      [ ! -L "$current" ] || return 1
      if [ "$current" = "$command_path" ]; then
        root_owned_metadata "$current" file || return 1
      else
        root_owned_metadata "$current" directory || return 1
      fi
      [ "$current" = "$component_root" ] && break
      current="${current%/*}"
      [ -n "$current" ] || return 1
      case "$current/" in "$component_root"/|"$component_root"/*) ;; *) return 1 ;; esac
    done
  done
}

trusted_metadata() {
  local candidate="$1" expected_type="$2" metadata owner mode links current_uid
  metadata="$(/usr/bin/stat -Lc '%u:%a:%h:%F' -- "$candidate" 2>/dev/null || \
    /usr/bin/stat -Lf '%u:%Lp:%l:%HT' "$candidate" 2>/dev/null || true)"
  IFS=: read -r owner mode links actual_type <<< "$metadata"
  case "$actual_type" in
    "Regular File") actual_type="regular file" ;;
    Directory) actual_type=directory ;;
  esac
  current_uid="$(/usr/bin/id -u)"
  case "$owner" in 0|"$current_uid") ;; *) return 1 ;; esac
  [[ "$mode" =~ ^[0-7]{3,4}$ ]] || return 1
  (( (8#$mode & 8#022) == 0 )) || return 1
  [[ "$links" =~ ^[0-9]+$ ]] || return 1
  if [ "$expected_type" = file ]; then
    [ "$actual_type" = "regular file" ] || return 1
    [ "$links" -eq 1 ] || [ "$owner" = 0 ] || return 1
  else
    [ "$actual_type" = directory ] || return 1
  fi
}

trusted_command_chain() {
  local current="$command_path"
  while :; do
    [ ! -L "$current" ] || return 1
    if [ "$current" = "$command_path" ]; then
      trusted_metadata "$current" file || return 1
    else
      trusted_metadata "$current" directory || return 1
    fi
    [ "$current" = "$workspace_real" ] && break
    current="$(dirname -- "$current")"
    case "$current/" in "$workspace_real"/|"$workspace_real"/*) ;; *) return 1 ;; esac
  done
}

trusted_runtime_file_chain() {
  local candidate="$1" current="$1"
  while :; do
    [ ! -L "$current" ] || return 1
    if [ "$current" = "$candidate" ]; then
      trusted_metadata "$current" file || return 1
    else
      trusted_metadata "$current" directory || return 1
    fi
    [ "$current" = "$runtime_real" ] && break
    current="$(dirname -- "$current")"
    case "$current/" in "$runtime_real"/|"$runtime_real"/*) ;; *) return 1 ;; esac
  done
}

system_python_path() {
  local candidate canonical current configured managed_selector
  candidate=/usr/bin/python3
  [ -x "$candidate" ] || return 1
  canonical="$(/usr/bin/readlink -f -- "$candidate")"
  case "$canonical" in /usr/bin/python3|/usr/bin/python3.*) ;; *) return 1 ;; esac
  trusted_metadata "$canonical" file || return 1
  current="$(dirname -- "$canonical")"
  while :; do
    trusted_metadata "$current" directory || return 1
    [ "$current" = / ] && break
    current="$(dirname -- "$current")"
  done
  configured="${AAS_RUNTIME_PYTHON:-}"
  if [ -n "$configured" ] && [ ! "$configured" -ef "$canonical" ] 2>/dev/null; then
    managed_selector=""
    case "${HOME:-}" in
      /*) managed_selector="$HOME/.local/share/coding-system/python-closure/shared/bin/python" ;;
    esac
    # CSR may render the managed selector before its closure lock is qualified.
    # Credential projection itself is stdlib-only, so accept only that exact
    # declarative selector while continuing to execute descriptor-bound system
    # Python.  The unqualified/caller-owned path is never opened.
    if [ "$allow_managed_selector_advisory" -ne 1 ] || \
       [ -z "$managed_selector" ] || [ "$configured" != "$managed_selector" ]; then
      return 1
    fi
  fi
  printf '%s\n' "$canonical"
}

# bash 4.1+ allocates the descriptor number itself via ``exec {var}<``.
# Older substrates (macOS /bin/bash 3.2) parse ``{var}`` as a literal command
# word, so a deterministic script-global counter supplies high descriptor
# numbers there.  Descriptors opened with ``exec`` stay open across the final
# exec either way, so bound descriptors survive into the launched child.
bind_regular_file_next_fd=200

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

# BSD fdesc nodes also synthesize their permission bits from the
# descriptor's open flags, so execve on a read-only /dev/fd path is denied
# no matter what mode the underlying file carries, and opening a /dev/fd
# path duplicates the descriptor with a shared file offset, so an
# interpreter that reads its script argument twice sees end-of-file on
# the second pass.  Where /proc is unavailable a launch therefore execs
# and reads the attested real paths -- each descriptor stays bound for
# identity attestation -- while Linux /proc paths continue to use the
# bound inode exactly.
exec_path_for_bound() {
  local bound="$1" attested="$2"
  case "$bound" in
    /dev/fd/*) printf '%s\n' "$attested" ;;
    *) printf '%s\n' "$bound" ;;
  esac
}

bind_regular_file() {
  local selected="$1" variable="$2" fd_variable="${3:-}" bound value
  if [ "${BASH_VERSINFO[0]}" -gt 4 ] || \
     { [ "${BASH_VERSINFO[0]}" -eq 4 ] && [ "${BASH_VERSINFO[1]}" -ge 1 ]; }; then
    exec {bound}<"$selected" || return 1
  else
    bound="$bind_regular_file_next_fd"
    bind_regular_file_next_fd=$((bind_regular_file_next_fd + 1))
    eval "exec ${bound}<\"\$selected\"" || return 1
  fi
  if [ -e "/proc/self/fd/$bound" ]; then
    printf -v "$variable" '%s' "/proc/self/fd/$bound"
  elif [ -e "/dev/fd/$bound" ]; then
    printf -v "$variable" '%s' "/dev/fd/$bound"
  else
    eval "exec ${bound}<&-"
    return 1
  fi
  value="${!variable}"
  if ! bound_descriptor_matches_selected "$value" "$selected" || \
     ! trusted_metadata "$value" file; then
    eval "exec ${bound}<&-"
    return 1
  fi
  if [ -n "$fd_variable" ]; then
    printf -v "$fd_variable" '%s' "$bound"
  fi
}

python_command=""
command_command=""
command_fd=""
if [ "$credential_contract" -eq 1 ]; then
  if [ "${AAS_ALLOW_EXTERNAL_RUNTIME_WORKSPACE:-}" = "1" ] && \
     [ "$workspace_real" != "$default_workspace_real" ]; then
    printf 'credential-bearing launch refuses an external runtime workspace\n' >&2
    exit 127
  fi
  if [ "$credential_runtime_enforcement" -eq 1 ] && \
     ! trusted_credential_runtime_generation; then
    printf 'credential-bearing launch requires a root-owned exact AAS component generation\n' >&2
    exit 127
  fi
  if ! trusted_command_chain; then
    printf 'credential-bearing launch requires an owner-controlled managed command chain\n' >&2
    exit 127
  fi
  if ! bind_regular_file "$command_path" command_command command_fd; then
    printf 'credential-bearing launch could not descriptor-bind the managed command\n' >&2
    exit 127
  fi
  command_command="$(exec_path_for_bound "$command_command" "$command_path")"
  export AAS_RUNTIME_COMMAND_FD="$command_fd"
  export AAS_RUNTIME_COMMAND_PATH="$command_path"
  selected_python="$(system_python_path || true)"
  if [ -z "$selected_python" ] || ! bind_regular_file "$selected_python" python_command; then
    printf 'credential-bearing launch requires the attested system Python runtime\n' >&2
    exit 127
  fi
  python_command="$(exec_path_for_bound "$python_command" "$selected_python")"
  export AAS_RUNTIME_PYTHON="$python_command"
  export PATH=/usr/bin:/bin
  unset PYTHONPATH PYTHONHOME PYTHONSTARTUP PYTHONINSPECT PYTHONWARNINGS PYTHONBREAKPOINT
  unset VIRTUAL_ENV __PYVENV_LAUNCHER__ NODE_OPTIONS NODE_PATH
  unset LD_PRELOAD LD_LIBRARY_PATH LD_AUDIT DYLD_INSERT_LIBRARIES DYLD_LIBRARY_PATH
  unset BASH_ENV ENV CDPATH GLOBIGNORE 2>/dev/null || true
else
  # The caller's tool search path is permitted only after every selector and
  # ambient credential has been removed. Noncredential commands retain the
  # historical extensible-runtime behavior without exposing prelude secrets.
  export PATH="$caller_path"
  if [ -n "${AAS_RUNTIME_PYTHON:-}" ]; then
    case "$AAS_RUNTIME_PYTHON" in
      /*) python_command="$AAS_RUNTIME_PYTHON" ;;
      */*) printf 'AAS_RUNTIME_PYTHON must be an absolute path or command name.\n' >&2; exit 127 ;;
      *) python_command="$(command -v "$AAS_RUNTIME_PYTHON" 2>/dev/null || true)" ;;
    esac
  elif command -v python3 >/dev/null 2>&1; then
    python_command="$(command -v python3)"
  fi
  if [ -n "$python_command" ]; then
    if [ ! -f "$python_command" ] || [ ! -x "$python_command" ]; then
      printf 'AAS_RUNTIME_PYTHON does not name an executable file or command.\n' >&2
      exit 127
    fi
    py_ver="$("$python_command" -I -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || true)"
    if ! [[ "$py_ver" =~ ^3\.([0-9]{2,3})$ ]] || (( 10#${BASH_REMATCH[1]} < 10 )); then
      printf 'Selected Python runtime must report version 3.10 or newer.\n' >&2
      exit 127
    fi
    python_bin="$(cd -- "$(dirname -- "$python_command")" && pwd -P)"
    python_command="$python_bin/$(basename -- "$python_command")"
    site_packages="$workspace_real/.local/lib/python${py_ver}/site-packages"
    dist_packages="$workspace_real/.local/local/lib/python${py_ver}/dist-packages"
    local_bin="$workspace_real/.local/bin"
    alt_bin="$workspace_real/.local/local/bin"
    mkdir -p "$site_packages" "$dist_packages" "$local_bin" "$alt_bin"
    export AAS_RUNTIME_PYTHON="$python_command"
    export PYTHONPATH="$site_packages:$dist_packages:$workspace_real/.local:${PYTHONPATH:-}"
    export PATH="$python_bin:$local_bin:$alt_bin:${PATH}"
  fi
fi

if [ "$credential_contract" -eq 1 ]; then
  if [ "$arl_credential_broker" -eq 1 ]; then
    broker_path="$runtime_real/runners/arl_credential_broker.py"
    if [ ! -f "$broker_path" ]; then
      broker_path="$runtime_real/arl_credential_broker.py"
    fi
    if [ ! -f "$broker_path" ] || ! trusted_runtime_file_chain "$broker_path"; then
      printf 'managed ARL credential broker is unavailable or untrusted\n' >&2
      exit 127
    fi
    broker_command=""
    if ! bind_regular_file "$broker_path" broker_command; then
      printf 'managed ARL credential broker could not be descriptor-bound\n' >&2
      exit 127
    fi
    broker_command="$(exec_path_for_bound "$broker_command" "$broker_path")"
    exec "$python_command" -I "$broker_command" --entry "$command_command" -- "$@"
  fi
  secret_loader="$runtime_real/load_secret_env.py"
  if [ ! -f "$secret_loader" ]; then
    secret_loader="$runtime_real/runners/load_secret_env.py"
  fi
  if [ ! -f "$secret_loader" ] || ! trusted_runtime_file_chain "$secret_loader"; then
    printf 'managed credential projection loader is unavailable or untrusted\n' >&2
    exit 127
  fi
  loader_command=""
  if ! bind_regular_file "$secret_loader" loader_command; then
    printf 'managed credential projection loader could not be descriptor-bound\n' >&2
    exit 127
  fi
  loader_command="$(exec_path_for_bound "$loader_command" "$secret_loader")"
  loader_args=(--pointer-env "$projection_pointer_env" --format "$projection_format" --export-subset)
  [ "$projection_no_load" -eq 1 ] && loader_args+=(--no-load)
  [ "$projection_retain_pointer" -eq 1 ] && loader_args+=(--retain-pointer)
  for key in ${projection_allow_keys[@]+"${projection_allow_keys[@]}"}; do loader_args+=(--allow-key "$key"); done
  for key in ${projection_export_keys[@]+"${projection_export_keys[@]}"}; do loader_args+=(--export-key "$key"); done
  for key in ${projection_retain_env[@]+"${projection_retain_env[@]}"}; do loader_args+=(--retain-env "$key"); done
  scrub_keys=(
    "${ambient_secret_keys[@]}"
    SHELLOPTS BASHOPTS BASH_ENV ENV PYTHONPATH PYTHONHOME PYTHONSTARTUP PYTHONINSPECT
    LD_PRELOAD LD_LIBRARY_PATH LD_AUDIT DYLD_INSERT_LIBRARIES DYLD_LIBRARY_PATH NODE_OPTIONS NODE_PATH
    AAS_SECRETS_FILE OPENCLAW_SECRETS_FILE AAS_SKILL_SECRETS_FILE
  )
  for key in "${scrub_keys[@]}"; do loader_args+=(--scrub-key "$key"); done
  exec "$python_command" -I "$loader_command" "${loader_args[@]}" -- "$command_command" "$@"
fi

exec "$command_path" "$@"
