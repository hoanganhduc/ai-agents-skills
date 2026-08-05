#!/bin/bash -p
set -euo pipefail

# Host-side worker for the authenticated, at-most-once send queue. The worker
# accepts no credential pointer from its caller: the queue authority resolves
# only from the host account's canonical HOME. Channel credentials are resolved
# by the bound host delivery launcher, never from this process environment.
unset AAS_SECRETS_FILE OPENCLAW_SECRETS_FILE AAS_SKILL_SECRETS_FILE
unset AAS_PROVIDER_SECRETS_FILE AAS_COMPUTE_SECRETS_FILE
unset AAS_ZOTERO_SECRETS_FILE REMOTE_BRIDGE_SECRETS_FILE SEND_EMAIL_SECRETS_FILE
unset AAS_FILE_DELIVERY_SECRETS_FILE
unset HCLOUD_TOKEN HCLOUD_SSH_KEYS KAGGLE_API_TOKEN KAGGLE_CONFIG_DIR
unset TELEGRAM_BOT_TOKEN ZULIP_API_KEY ZULIP_EMAIL ZULIP_ORG_URL ZULIP_SITE
unset OPENAI_API_KEY ANTHROPIC_API_KEY GH_TOKEN GITHUB_TOKEN
unset PYTHONPATH PYTHONHOME PYTHONSTARTUP PYTHONINSPECT PYTHONWARNINGS PYTHONBREAKPOINT
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
SCRIPT="$SCRIPT_DIR/send_queue.py"
DEFAULT_WORKSPACE="$(cd -- "$SCRIPT_DIR/../.." && builtin pwd -P)"
WORKSPACE="${AAS_RUNTIME_WORKSPACE:-${OPENCLAW_WORKSPACE:-$DEFAULT_WORKSPACE}}"
runtime_home="${HOME:-}"
runtime_lang="${LANG:-}"
runtime_lc_all="${LC_ALL:-}"
runtime_tz="${TZ:-}"

# Do not project arbitrary host-service credentials into the queue worker.
# The host OpenClaw child resolves its own credentials from canonical HOME.
for exported_name in $(compgen -e); do
  unset "$exported_name" 2>/dev/null || true
done
if [ -n "$runtime_home" ]; then export HOME="$runtime_home"; fi
if [ -n "$runtime_lang" ]; then export LANG="$runtime_lang"; fi
if [ -n "$runtime_lc_all" ]; then export LC_ALL="$runtime_lc_all"; fi
if [ -n "$runtime_tz" ]; then export TZ="$runtime_tz"; fi
export PATH=/usr/bin:/bin

trusted_regular_file() {
  local candidate="$1" metadata owner mode links kind current_uid
  metadata="$(/usr/bin/stat -c '%u:%a:%h:%F' -- "$candidate" 2>/dev/null || true)"
  IFS=: read -r owner mode links kind <<< "$metadata"
  current_uid="$(/usr/bin/id -u)"
  case "$owner" in 0|"$current_uid") ;; *) return 1 ;; esac
  [[ "$mode" =~ ^[0-7]{3,4}$ ]] || return 1
  (( (8#$mode & 8#022) == 0 )) || return 1
  [ "$links" -eq 1 ] && [ "$kind" = "regular file" ]
}

PYTHON="$(/usr/bin/readlink -f -- /usr/bin/python3 2>/dev/null || true)"
case "$PYTHON" in /usr/bin/python3|/usr/bin/python3.*) ;; *) PYTHON= ;; esac
if [ -z "$PYTHON" ] || ! trusted_regular_file "$PYTHON" || [ ! -x "$PYTHON" ]; then
  printf 'send queue worker requires the attested system Python runtime\n' >&2
  exit 127
fi
if [ -n "${AAS_RUNTIME_PYTHON:-}" ] && [ ! "$AAS_RUNTIME_PYTHON" -ef "$PYTHON" ]; then
  printf 'AAS_RUNTIME_PYTHON does not match the attested system Python runtime\n' >&2
  exit 127
fi

secure_loader='import os,stat,sys; p=os.path.abspath(sys.argv[1]); q=os.stat(p,follow_symlinks=False); f=os.open(p,os.O_RDONLY|getattr(os,"O_NOFOLLOW",0)|getattr(os,"O_NONBLOCK",0)|getattr(os,"O_CLOEXEC",0)); b=os.fstat(f); ok=stat.S_ISREG(b.st_mode) and b.st_nlink==1 and b.st_uid in {0,os.geteuid()} and not (stat.S_IMODE(b.st_mode)&0o022) and (q.st_dev,q.st_ino)==(b.st_dev,b.st_ino) and b.st_size<=16777216; ok or (_ for _ in ()).throw(RuntimeError("runtime helper is unavailable or untrusted")); d=b""; rem=b.st_size; exec("while rem:\n c=os.read(f,min(65536,rem))\n c or (_ for _ in ()).throw(RuntimeError(\"runtime helper was truncated\"))\n d+=c; rem-=len(c)"); a=os.fstat(f); (b.st_dev,b.st_ino,b.st_size,b.st_mtime_ns,b.st_ctime_ns,b.st_nlink)==(a.st_dev,a.st_ino,a.st_size,a.st_mtime_ns,a.st_ctime_ns,a.st_nlink) or (_ for _ in ()).throw(RuntimeError("runtime helper changed while reading")); c=compile(d,p,"exec"); sys.argv=[p,*sys.argv[2:]]; g={"__name__":"__main__","__file__":p,"__package__":None,"__cached__":None}; exec(c,g,g)'
exec "$PYTHON" -I -c "$secure_loader" "$SCRIPT" \
  worker --workspace "$WORKSPACE" "$@"
