#!/bin/bash -p
set -euo pipefail

calibre_pointer="${AAS_CALIBRE_SECRETS_FILE:-}"
configured_python="${AAS_RUNTIME_PYTHON:-}"
runtime_workspace="${AAS_RUNTIME_WORKSPACE:-${OPENCLAW_WORKSPACE:-}}"
gdrive_credentials="${GDRIVE_CREDENTIALS:-}"
calibre_folder_id="${CALIBRE_GDRIVE_FOLDER_ID:-}"

unset AAS_SECRETS_FILE OPENCLAW_SECRETS_FILE AAS_SKILL_SECRETS_FILE
unset AAS_COMPUTE_SECRETS_FILE AAS_PROVIDER_SECRETS_FILE
unset AAS_CALIBRE_SECRETS_FILE AAS_ZOTERO_SECRETS_FILE
unset AAS_FILE_DELIVERY_SECRETS_FILE REMOTE_BRIDGE_SECRETS_FILE SEND_EMAIL_SECRETS_FILE
unset ZOTERO_API_KEY WEBDAV_PASSWORD GDRIVE_CREDENTIALS CALIBRE_GDRIVE_FOLDER_ID
unset SEMANTIC_SCHOLAR_API_KEY UNPAYWALL_EMAIL ZENODO_TOKEN
unset SMTP_HOST SMTP_PORT SMTP_USER SMTP_PASSWORD SMTP_FROM SMTP_SECURITY SMTP_TIMEOUT SMTP_ACCOUNT
unset ZULIP_ORG_URL ZULIP_SITE ZULIP_EMAIL ZULIP_API_KEY TELEGRAM_BOT_TOKEN
unset HCLOUD_TOKEN HCLOUD_SSH_KEYS KAGGLE_API_TOKEN KAGGLE_CONFIG_DIR
unset MODAL_TOKEN_ID MODAL_TOKEN_SECRET OPENAI_API_KEY ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN
unset CLAUDE_API_KEY CLAUDE_CODE_OAUTH_TOKEN COPILOT_GITHUB_TOKEN COPILOT_PROVIDER_API_KEY
unset COPILOT_PROVIDER_BEARER_TOKEN GEMINI_API_KEY GOOGLE_API_KEY DEEPSEEK_API_KEY XAI_API_KEY
unset GROK_API_KEY KIMI_API_KEY MOONSHOT_API_KEY OPENCODE_API_KEY GH_TOKEN GITHUB_TOKEN
unset PYTHONPATH PYTHONHOME PYTHONSTARTUP PYTHONINSPECT PYTHONWARNINGS PYTHONBREAKPOINT
unset VIRTUAL_ENV __PYVENV_LAUNCHER__ NODE_OPTIONS NODE_PATH
unset LD_PRELOAD LD_LIBRARY_PATH LD_AUDIT DYLD_INSERT_LIBRARIES DYLD_LIBRARY_PATH
unset BASH_ENV ENV CDPATH GLOBIGNORE 2>/dev/null || true
export PATH=/usr/bin:/bin

script_path="${BASH_SOURCE[0]:-$0}"
runtime_command_fd="${AAS_RUNTIME_COMMAND_FD:-}"
if [[ "$runtime_command_fd" =~ ^[0-9]+$ ]] && \
   { [ "$script_path" = "/proc/self/fd/$runtime_command_fd" ] || [ "$script_path" = "/dev/fd/$runtime_command_fd" ]; }; then
  script_path="${AAS_RUNTIME_COMMAND_PATH:-$script_path}"
fi
unset AAS_RUNTIME_COMMAND_FD AAS_RUNTIME_COMMAND_PATH
case "$script_path" in */*) script_parent="${script_path%/*}" ;; *) script_parent=. ;; esac
SKILL_DIR="$(cd -- "$script_parent" && builtin pwd -P)"
DEFAULT_WORKSPACE="$(cd -- "$SKILL_DIR/../.." && builtin pwd -P)"
if [ -z "$runtime_workspace" ]; then
  runtime_workspace="$DEFAULT_WORKSPACE"
fi
case "$runtime_workspace" in /*) ;; *) printf 'AAS_RUNTIME_WORKSPACE must be absolute\n' >&2; exit 127 ;; esac

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
  printf 'attested system Python runtime is unavailable\n' >&2
  exit 127
fi
if [ -n "$configured_python" ] && [ ! "$configured_python" -ef "$PYTHON" ]; then
  printf 'AAS_RUNTIME_PYTHON does not match the attested system Python runtime\n' >&2
  exit 127
fi
exec {AAS_CALIBRE_PYTHON_FD}<"$PYTHON"
if [ -e "/proc/self/fd/$AAS_CALIBRE_PYTHON_FD" ]; then
  PYTHON="/proc/self/fd/$AAS_CALIBRE_PYTHON_FD"
elif [ -e "/dev/fd/$AAS_CALIBRE_PYTHON_FD" ]; then
  PYTHON="/dev/fd/$AAS_CALIBRE_PYTHON_FD"
else
  printf 'attested system Python runtime could not be descriptor-bound\n' >&2
  exit 127
fi

[ -n "$calibre_pointer" ] && export AAS_CALIBRE_SECRETS_FILE="$calibre_pointer"
[ -n "$gdrive_credentials" ] && export GDRIVE_CREDENTIALS="$gdrive_credentials"
[ -n "$calibre_folder_id" ] && export CALIBRE_GDRIVE_FOLDER_ID="$calibre_folder_id"
export AAS_RUNTIME_WORKSPACE="$runtime_workspace"
export OPENCLAW_WORKSPACE="$runtime_workspace"
export AAS_RUNTIME_PYTHON="$PYTHON"
export PYTHONDONTWRITEBYTECODE=1 PYTHONUTF8=1 PYTHONIOENCODING=utf-8

secure_loader='import os,stat,sys; p=os.path.abspath(sys.argv[1]); q=os.stat(p,follow_symlinks=False); f=os.open(p,os.O_RDONLY|getattr(os,"O_NOFOLLOW",0)|getattr(os,"O_NONBLOCK",0)|getattr(os,"O_CLOEXEC",0)); b=os.fstat(f); ok=stat.S_ISREG(b.st_mode) and b.st_nlink==1 and b.st_uid in {0,os.geteuid()} and not (stat.S_IMODE(b.st_mode)&0o022) and (q.st_dev,q.st_ino)==(b.st_dev,b.st_ino) and b.st_size<=16777216; ok or (_ for _ in ()).throw(RuntimeError("runtime helper is unavailable or untrusted")); d=b""; rem=b.st_size; exec("while rem:\n c=os.read(f,min(65536,rem))\n c or (_ for _ in ()).throw(RuntimeError(\"runtime helper was truncated\"))\n d+=c; rem-=len(c)"); a=os.fstat(f); (b.st_dev,b.st_ino,b.st_size,b.st_mtime_ns,b.st_ctime_ns,b.st_nlink)==(a.st_dev,a.st_ino,a.st_size,a.st_mtime_ns,a.st_ctime_ns,a.st_nlink) or (_ for _ in ()).throw(RuntimeError("runtime helper changed while reading")); c=compile(d,p,"exec"); sys.argv=[p,*sys.argv[2:]]; g={"__name__":"__main__","__file__":p,"__package__":None,"__cached__":None}; exec(c,g,g)'
exec "$PYTHON" -I -c "$secure_loader" "$SKILL_DIR/cal.py" "$@"
