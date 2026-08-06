#!/bin/bash -p
set -euo pipefail

# Compatibility alias for the authenticated queue producer. It accepts the
# same bounded JSON stdin request as send_file.sh; delivery metadata is never
# accepted through argv.
if [ "$#" -ne 0 ]; then
  printf '%s\n' '{"status":"error","message":"send_telegram.sh accepts JSON stdin only"}'
  exit 2
fi

unset AAS_SECRETS_FILE OPENCLAW_SECRETS_FILE AAS_SKILL_SECRETS_FILE
unset AAS_PROVIDER_SECRETS_FILE AAS_COMPUTE_SECRETS_FILE
unset AAS_ZOTERO_SECRETS_FILE REMOTE_BRIDGE_SECRETS_FILE SEND_EMAIL_SECRETS_FILE
unset HCLOUD_TOKEN HCLOUD_SSH_KEYS KAGGLE_API_TOKEN TELEGRAM_BOT_TOKEN ZULIP_API_KEY
unset PYTHONPATH PYTHONHOME PYTHONSTARTUP PYTHONINSPECT PYTHONWARNINGS PYTHONBREAKPOINT
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
SCRIPT_DIR="$(cd -- "$script_parent" && builtin pwd -P)"
SENDER="$SCRIPT_DIR/send_file.sh"
export AAS_ZOTERO_DELIVERY_DIR="$SCRIPT_DIR"
secure_shell_loader='import os,stat,sys; p=os.path.abspath(sys.argv[1]); q=os.stat(p,follow_symlinks=False); f=os.open(p,os.O_RDONLY|getattr(os,"O_NOFOLLOW",0)|getattr(os,"O_NONBLOCK",0)); b=os.fstat(f); ok=stat.S_ISREG(b.st_mode) and b.st_nlink==1 and b.st_uid in {0,os.geteuid()} and not (stat.S_IMODE(b.st_mode)&0o022) and (q.st_dev,q.st_ino)==(b.st_dev,b.st_ino) and b.st_size<=1048576; ok or (_ for _ in ()).throw(RuntimeError("file-delivery entrypoint is unavailable or untrusted")); d=b""; rem=b.st_size; exec("while rem:\n c=os.read(f,min(65536,rem))\n c or (_ for _ in ()).throw(RuntimeError(\"file-delivery entrypoint was truncated\"))\n d+=c; rem-=len(c)"); a=os.fstat(f); (b.st_dev,b.st_ino,b.st_size,b.st_mtime_ns,b.st_ctime_ns,b.st_nlink)==(a.st_dev,a.st_ino,a.st_size,a.st_mtime_ns,a.st_ctime_ns,a.st_nlink) or (_ for _ in ()).throw(RuntimeError("file-delivery entrypoint changed while reading")); request_fd=os.dup(0); os.set_inheritable(request_fd,True); m=os.memfd_create("aas-send-file",0); os.write(m,d)==len(d) or (_ for _ in ()).throw(RuntimeError("short protected script write")); os.lseek(m,0,os.SEEK_SET); os.dup2(m,0); allowed=("HOME","LANG","LC_ALL","TZ","AAS_RUNTIME_WORKSPACE","OPENCLAW_WORKSPACE","AAS_FILE_DELIVERY_SECRETS_FILE","AAS_ZOTERO_DELIVERY_DIR"); env={k:os.environ[k] for k in allowed if os.environ.get(k)}; env.update({"PATH":"/usr/bin:/bin","AAS_FILE_DELIVERY_REQUEST_FD":str(request_fd)}); os.execve("/bin/bash",["/bin/bash","-p","-s","--","send_file.sh"],env)'
exec /usr/bin/python3 -I -c "$secure_shell_loader" "$SENDER"
