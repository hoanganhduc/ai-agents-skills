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

# Trusted-local children also run inside a systemd user scope, so the runner
# needs a live user manager and its session bus. The socket usually exists once
# linger is enabled, but the runner service starts jobs without the endpoints in
# the environment, which leaves the runtime unable to enforce any limit.
bus="/run/user/$(id -u)/bus"
if [ -S "$bus" ]; then
  export XDG_RUNTIME_DIR="/run/user/$(id -u)"
  export DBUS_SESSION_BUS_ADDRESS="unix:path=$bus"
  if [ -n "${GITHUB_ENV:-}" ]; then
    echo "XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR" >> "$GITHUB_ENV"
    echo "DBUS_SESSION_BUS_ADDRESS=$DBUS_SESSION_BUS_ADDRESS" >> "$GITHUB_ENV"
  fi
  echo "user session bus: $bus (exported)"
else
  echo "user session bus: missing - resource enforcement tests will skip"
fi

# Report the exact predicates the trusted-local tests gate on, so a skip caused
# by the masks or by the user manager is distinguishable from a skip caused by
# bubblewrap itself. -B keeps the probe from leaving a __pycache__ directory in
# the canonical tree, which the runtime inventory check denies.
python3 -B - <<'PY'
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

try:
    limits = resources.preflight_resource_backend(30, role="primary")
except Exception as exc:  # noqa: BLE001
    print(f"trusted-local resource enforcement: unavailable ({exc})")
else:
    print(f"trusted-local resource enforcement: functional tasks_max={limits['tasks_max']}")
PY
