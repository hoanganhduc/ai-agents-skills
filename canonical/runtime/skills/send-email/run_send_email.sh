#!/bin/bash -p
set -euo pipefail

email_pointer="${SEND_EMAIL_SECRETS_FILE:-}"
configured_python="${AAS_RUNTIME_PYTHON:-}"
configured_home="${HOME:-}"

# Keep only the dedicated structured-authority selector. Generic and ambient
# SMTP credentials are removed before any external executable can run.
unset AAS_SECRETS_FILE OPENCLAW_SECRETS_FILE AAS_SKILL_SECRETS_FILE
unset AAS_PROVIDER_SECRETS_FILE AAS_COMPUTE_SECRETS_FILE
unset SEND_EMAIL_SECRETS_FILE
unset SMTP_HOST SMTP_PORT SMTP_USER SMTP_PASSWORD SMTP_FROM SMTP_SECURITY SMTP_TIMEOUT SMTP_ACCOUNT
unset SMTP_FROM_NAME SMTP_REPLY_TO SMTP_CC SMTP_BCC SMTP_SIGNATURE SMTP_SIGNATURE_HTML
unset SMTP_REPLY_TO_SELF SMTP_BCC_SELF SMTP_PGP_SIGN SMTP_PGP_KEY SMTP_PGP_PASSPHRASE SMTP_GNUPG_HOME
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
SCRIPT="$SCRIPT_DIR/send_email.py"

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
  [ "$links" -eq 1 ] && [ "$kind" = "regular file" ]
}

PYTHON="$(/usr/bin/readlink -f -- /usr/bin/python3 2>/dev/null || true)"
case "$PYTHON" in /usr/bin/python3|/usr/bin/python3.*) ;; *) PYTHON= ;; esac
if [ -z "$PYTHON" ] || ! trusted_regular_file "$PYTHON" || [ ! -x "$PYTHON" ]; then
  printf 'attested system Python runtime is unavailable\n' >&2
  exit 127
fi
if [ -n "$configured_python" ] && [ ! "$configured_python" -ef "$PYTHON" ] 2>/dev/null; then
  managed_selector=""
  case "$configured_home" in
    /*) managed_selector="$configured_home/.local/share/coding-system/python-closure/shared/bin/python" ;;
  esac
  if [ -z "$managed_selector" ] || [ "$configured_python" != "$managed_selector" ]; then
    printf 'AAS_RUNTIME_PYTHON is not an approved absolute runtime selector\n' >&2
    exit 127
  fi
  # send_email.py is stdlib-only.  The exact CSR selector is advisory until
  # its closure is qualified; it is never opened or executed here.
fi

exec {AAS_SEND_EMAIL_PYTHON_FD}<"$PYTHON"
if [ -e "/proc/self/fd/$AAS_SEND_EMAIL_PYTHON_FD" ]; then
  PYTHON="/proc/self/fd/$AAS_SEND_EMAIL_PYTHON_FD"
elif [ -e "/dev/fd/$AAS_SEND_EMAIL_PYTHON_FD" ]; then
  PYTHON="/dev/fd/$AAS_SEND_EMAIL_PYTHON_FD"
else
  printf 'attested system Python runtime could not be descriptor-bound\n' >&2
  exit 127
fi
export AAS_RUNTIME_PYTHON="$PYTHON"
[ -n "$email_pointer" ] && export SEND_EMAIL_SECRETS_FILE="$email_pointer"

secure_loader='import os,stat,sys; p=os.path.abspath(sys.argv[1]); q=os.stat(p,follow_symlinks=False); f=os.open(p,os.O_RDONLY|getattr(os,"O_NOFOLLOW",0)|getattr(os,"O_NONBLOCK",0)|getattr(os,"O_CLOEXEC",0)); b=os.fstat(f); ok=stat.S_ISREG(b.st_mode) and b.st_nlink==1 and b.st_uid in {0,os.geteuid()} and not (stat.S_IMODE(b.st_mode)&0o022) and (q.st_dev,q.st_ino)==(b.st_dev,b.st_ino) and b.st_size<=16777216; ok or (_ for _ in ()).throw(RuntimeError("runtime helper is unavailable or untrusted")); d=b""; rem=b.st_size; exec("while rem:\n c=os.read(f,min(65536,rem))\n c or (_ for _ in ()).throw(RuntimeError(\"runtime helper was truncated\"))\n d+=c; rem-=len(c)"); a=os.fstat(f); (b.st_dev,b.st_ino,b.st_size,b.st_mtime_ns,b.st_ctime_ns,b.st_nlink)==(a.st_dev,a.st_ino,a.st_size,a.st_mtime_ns,a.st_ctime_ns,a.st_nlink) or (_ for _ in ()).throw(RuntimeError("runtime helper changed while reading")); c=compile(d,p,"exec"); sys.argv=[p,*sys.argv[2:]]; g={"__name__":"__main__","__file__":p,"__package__":None,"__cached__":None}; exec(c,g,g)'
exec "$PYTHON" -I -c "$secure_loader" "$SCRIPT" "$@"
