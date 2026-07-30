#!/usr/bin/env bash
# Install bubblewrap and report whether this runner can actually contain a
# primary process. Containment gates the trusted-local runtime tests and every
# headless driver iteration, so a runner where bubblewrap is installed but
# cannot create a user namespace silently converts coverage into skips. This
# script prints the verdict so that loss is visible in the job log.
set -uo pipefail

probe_containment() {
  bwrap --die-with-parent --new-session --unshare-ipc --unshare-pid \
    --bind / / --proc /proc --dev /dev -- /bin/true
}

sudo apt-get update
sudo apt-get install -y bubblewrap

# The trusted-local control-plane masks need the per-user runtime directory,
# which only exists once systemd has a user session for this account. The
# runner service starts jobs without one.
sudo loginctl enable-linger "$(id -un)" || echo "linger: unavailable"

echo "runner user: $(id -un) uid=$(id -u)"
ls -ld "/run/user/$(id -u)" || echo "per-user runtime directory: missing"
for knob in kernel.apparmor_restrict_unprivileged_userns user.max_user_namespaces kernel.unprivileged_userns_clone; do
  value="$(sysctl -n "$knob" 2>/dev/null)" || value="unavailable"
  echo "$knob = $value"
done

if probe_containment; then
  echo "containment: functional"
else
  echo "containment: bubblewrap cannot spawn; relaxing the unprivileged user-namespace restriction"
  sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0 || echo "sysctl: knob absent"
  if probe_containment; then
    echo "containment: functional after relaxing the restriction"
  else
    echo "containment: UNAVAILABLE - containment tests will skip and driver smoke checks will report skipped"
  fi
fi

# Report the exact predicate the trusted-local tests gate on, so a skip caused
# by the masks is distinguishable from a skip caused by bubblewrap itself.
python3 - <<'PY'
import pathlib
import subprocess
import sys

sys.path.insert(0, "canonical/runtime/skills/autonomous-research-loop-runtime")
import provider_resources as resources

try:
    command = resources.trusted_local_containment_command(
        ["/bin/true"], cwd=pathlib.Path.cwd().resolve()
    )
except Exception as exc:  # noqa: BLE001
    print(f"trusted-local containment: unavailable ({exc})")
else:
    completed = subprocess.run(command, capture_output=True, text=True)
    detail = " ".join((completed.stderr or "").split())[:200]
    print(f"trusted-local containment: rc={completed.returncode} {detail}")
PY
