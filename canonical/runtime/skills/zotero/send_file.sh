#!/bin/bash -p
set -euo pipefail

# Agent-facing file-delivery entrypoint. Every channel crosses the authenticated
# host queue; network credentials and delivery CLIs remain exclusively on the
# host-worker side of that boundary.

if [ "$#" -ne 0 ]; then
  printf '%s\n' '{"status":"error","message":"send_file.sh accepts one bounded JSON request on stdin, never delivery metadata in argv"}'
  exit 2
fi
delivery_dir_hint="${AAS_ZOTERO_DELIVERY_DIR:-}"
request_fd="${AAS_FILE_DELIVERY_REQUEST_FD:-}"
queue_authority="${AAS_FILE_DELIVERY_SECRETS_FILE:-}"
runtime_workspace="${AAS_RUNTIME_WORKSPACE:-${OPENCLAW_WORKSPACE:-/workspace}}"
runtime_home="${HOME:-}"
runtime_lang="${LANG:-}"
runtime_lc_all="${LC_ALL:-}"
runtime_tz="${TZ:-}"

# Generic, provider, skill, and channel credentials must never reach the
# producer. The only retained authority is the exact queue capability pointer.
unset AAS_SECRETS_FILE OPENCLAW_SECRETS_FILE AAS_SKILL_SECRETS_FILE
unset AAS_PROVIDER_SECRETS_FILE AAS_COMPUTE_SECRETS_FILE
unset AAS_ZOTERO_SECRETS_FILE REMOTE_BRIDGE_SECRETS_FILE SEND_EMAIL_SECRETS_FILE
unset AAS_ZOTERO_DELIVERY_DIR
unset AAS_FILE_DELIVERY_REQUEST_FD
unset HCLOUD_TOKEN HCLOUD_SSH_KEYS KAGGLE_API_TOKEN
unset TELEGRAM_BOT_TOKEN ZULIP_API_KEY ZULIP_EMAIL ZULIP_ORG_URL ZULIP_SITE
unset PYTHONPATH PYTHONHOME PYTHONSTARTUP PYTHONINSPECT PYTHONWARNINGS PYTHONBREAKPOINT
unset LD_PRELOAD LD_LIBRARY_PATH LD_AUDIT DYLD_INSERT_LIBRARIES DYLD_LIBRARY_PATH
unset BASH_ENV ENV CDPATH GLOBIGNORE NODE_OPTIONS NODE_PATH 2>/dev/null || true

# Remove every exported caller value, including provider credentials whose
# names this package does not know. Re-export only the queue capability and the
# small set of non-secret runtime values needed below.
for exported_name in $(compgen -e); do
  unset "$exported_name" 2>/dev/null || true
done
if [ -n "$runtime_home" ]; then export HOME="$runtime_home"; fi
if [ -n "$runtime_lang" ]; then export LANG="$runtime_lang"; fi
if [ -n "$runtime_lc_all" ]; then export LC_ALL="$runtime_lc_all"; fi
if [ -n "$runtime_tz" ]; then export TZ="$runtime_tz"; fi
if [ -n "$queue_authority" ]; then
  export AAS_FILE_DELIVERY_SECRETS_FILE="$queue_authority"
fi

export PATH=/usr/bin:/bin
SCRIPT_PATH="${BASH_SOURCE[0]:-$0}"
runtime_command_fd="${AAS_RUNTIME_COMMAND_FD:-}"
if [[ "$runtime_command_fd" =~ ^[0-9]+$ ]] && \
   { [ "$SCRIPT_PATH" = "/proc/self/fd/$runtime_command_fd" ] || [ "$SCRIPT_PATH" = "/dev/fd/$runtime_command_fd" ]; }; then
  SCRIPT_PATH="${AAS_RUNTIME_COMMAND_PATH:-$SCRIPT_PATH}"
fi
unset AAS_RUNTIME_COMMAND_FD AAS_RUNTIME_COMMAND_PATH
if [ -n "$delivery_dir_hint" ]; then
  case "$delivery_dir_hint" in /*) SCRIPT_PARENT="$delivery_dir_hint" ;; *) SCRIPT_PARENT=__invalid__ ;; esac
else
  case "$SCRIPT_PATH" in
    */*) SCRIPT_PARENT="${SCRIPT_PATH%/*}" ;;
    *) SCRIPT_PARENT=. ;;
  esac
fi
SCRIPT_DIR="$(cd -- "$SCRIPT_PARENT" && pwd -P)"
WORKSPACE="$runtime_workspace"
SCRIPT="$SCRIPT_DIR/send_queue.py"

trusted_regular_file() {
  local candidate="$1" metadata owner mode links kind current_uid
  metadata="$(/usr/bin/stat -c '%u:%a:%h:%F' -- "$candidate" 2>/dev/null || \
    /usr/bin/stat -f '%u:%Lp:%l:%HT' "$candidate" 2>/dev/null || true)"
  IFS=: read -r owner mode links kind <<< "$metadata"
  case "$kind" in
    "Regular File") kind="regular file" ;;
  esac
  current_uid="$(/usr/bin/id -u)"
  case "$owner" in 0|"$current_uid") ;; *) return 1 ;; esac
  [[ "$mode" =~ ^[0-7]{3,4}$ ]] || return 1
  (( (8#$mode & 8#022) == 0 )) || return 1
  [ "$kind" = "regular file" ] || return 1
  [ "$owner" = 0 ] || [ "$links" -eq 1 ]
}

PYTHON="$(/usr/bin/readlink -f -- /usr/bin/python3 2>/dev/null || true)"
case "$PYTHON" in /usr/bin/python3|/usr/bin/python3.*) ;; *) PYTHON= ;; esac
if [ -z "$PYTHON" ] || ! trusted_regular_file "$PYTHON" || [ ! -x "$PYTHON" ]; then
  printf '%s\n' '{"status":"error","message":"Attested system Python runtime is unavailable"}'
  exit 127
fi

secure_loader='import os,stat,sys; p=os.path.abspath(sys.argv[1]); q=os.stat(p,follow_symlinks=False); f=os.open(p,os.O_RDONLY|getattr(os,"O_NOFOLLOW",0)|getattr(os,"O_NONBLOCK",0)|getattr(os,"O_CLOEXEC",0)); b=os.fstat(f); ok=stat.S_ISREG(b.st_mode) and b.st_nlink==1 and b.st_uid in {0,os.geteuid()} and not (stat.S_IMODE(b.st_mode)&0o022) and (q.st_dev,q.st_ino)==(b.st_dev,b.st_ino) and b.st_size<=16777216; ok or (_ for _ in ()).throw(RuntimeError("runtime helper is unavailable or untrusted")); d=b""; rem=b.st_size; exec("while rem:\n c=os.read(f,min(65536,rem))\n c or (_ for _ in ()).throw(RuntimeError(\"runtime helper was truncated\"))\n d+=c; rem-=len(c)"); a=os.fstat(f); (b.st_dev,b.st_ino,b.st_size,b.st_mtime_ns,b.st_ctime_ns,b.st_nlink)==(a.st_dev,a.st_ino,a.st_size,a.st_mtime_ns,a.st_ctime_ns,a.st_nlink) or (_ for _ in ()).throw(RuntimeError("runtime helper changed while reading")); c=compile(d,p,"exec"); sys.argv=[p,*sys.argv[2:]]; g={"__name__":"__main__","__file__":p,"__package__":None,"__cached__":None}; exec(c,g,g)'
if [ -n "$request_fd" ]; then
  case "$request_fd" in *[!0-9]*|0|1|2) printf '%s\n' '{"status":"error","message":"invalid private request descriptor"}'; exit 2 ;; esac
  exec "$PYTHON" -I -c "$secure_loader" "$SCRIPT" submit \
    --workspace "$WORKSPACE" --request-json-stdin <&"$request_fd"
fi
exec "$PYTHON" -I -c "$secure_loader" "$SCRIPT" submit \
  --workspace "$WORKSPACE" --request-json-stdin
