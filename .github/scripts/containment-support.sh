#!/usr/bin/env bash
# Install bubblewrap and report whether this runner can actually contain a
# primary process. Containment gates the trusted-local runtime tests and every
# headless driver iteration, so a runner where bubblewrap is installed but
# cannot create a user namespace silently converts coverage into skips.
#
# A skip is not a failure, so a runner that loses containment leaves the job
# green while the tests that would have caught a containment defect no longer
# run. This script therefore treats an unmet predicate as an error rather than a
# log line. Set AAS_CONTAINMENT_REQUIRED=0 on a runner where the reduced
# coverage is understood and accepted; the verdict is still printed either way.
set -uo pipefail

required="${AAS_CONTAINMENT_REQUIRED:-1}"
losses=()

note_loss() {
  losses+=("$1")
  echo "coverage loss: $1"
}

probe_containment() {
  bwrap --die-with-parent --new-session --unshare-ipc --unshare-pid \
    --bind / / --proc /proc --dev /dev -- /bin/true
}

sudo apt-get update || note_loss "apt-get update failed"
sudo apt-get install -y bubblewrap || note_loss "bubblewrap install failed"

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
    note_loss "bubblewrap cannot spawn; containment tests would skip"
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
  note_loss "user session bus missing; resource enforcement tests would skip"
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

unmet = 0

try:
    command = resources.trusted_local_containment_command(
        ["/bin/true"], cwd=pathlib.Path.cwd().resolve()
    )
except Exception as exc:  # noqa: BLE001
    print(f"trusted-local containment: unavailable ({exc})")
    unmet += 1
else:
    completed = subprocess.run(command, capture_output=True, text=True)
    detail = " ".join((completed.stderr or "").split())[:200]
    print(f"trusted-local containment: rc={completed.returncode} {detail}")
    if completed.returncode != 0:
        unmet += 1

try:
    limits = resources.preflight_resource_backend(30, role="primary")
except Exception as exc:  # noqa: BLE001
    print(f"trusted-local resource enforcement: unavailable ({exc})")
    unmet += 1
else:
    print(f"trusted-local resource enforcement: functional tasks_max={limits['tasks_max']}")

sys.exit(1 if unmet else 0)
PY
probe_status=$?
if [ "$probe_status" -ne 0 ]; then
  note_loss "trusted-local predicates unmet on this runner"
fi

if [ "${#losses[@]}" -eq 0 ]; then
  echo "containment support: complete"
  exit 0
fi

echo "containment support: ${#losses[@]} predicate(s) unmet"
for loss in "${losses[@]}"; do
  echo "  - $loss"
done
if [ "$required" = "0" ]; then
  echo "AAS_CONTAINMENT_REQUIRED=0: continuing with reduced coverage"
  exit 0
fi
echo "Set AAS_CONTAINMENT_REQUIRED=0 to accept the reduced coverage."
exit 1
