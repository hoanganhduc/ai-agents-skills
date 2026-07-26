# formal_env.inc.sh — non-secret env fragment for ARL headless supervisors
#
# Source from an existing project supervisor / LAUNCH.sh before calling
# autonomous-research-loop-runtime `drive`. Does NOT duplicate drive logic.
#
# SECURITY: source this file from an install-owned or operator-owned path
# (templates/, supervisor repo outside the agent workspace). Do NOT source an
# agent-writable copy under $LOOP_DIR unless the host re-copies it each launch
# from a trusted origin. Prefer apply_formal_settings.py + drive CLI flags.
#
# Glossary:
#   - Headless / force-driven ARL  = unattended `drive` loop (this supervisor)
#   - formal_policy=force          = host formal hygiene tick credits (optional)
#     Those two "force" words are NOT the same thing.
#
# Secrets: never source key files here. If LeanExplore is needed, the operator
# must pre-export LEANEXPLORE_API_KEY via their secret manager before launch.
# Do not print env dumps.

# Default sample policy is "on" (prompt binding + F1–F7 positions when path is
# formal-track). Override before sourcing, or export after.
export AAS_AUTOLOOP_FORMAL_POLICY="${AAS_AUTOLOOP_FORMAL_POLICY:-on}"
export AAS_AUTOLOOP_FORMAL_PROJECT="${AAS_AUTOLOOP_FORMAL_PROJECT:-formal/}"

# Optional aggressive hygiene (host tick after each ok iteration). Leave unset
# unless you intentionally want formal_policy=force + host scan.
# export AAS_AUTOLOOP_FORMAL_POLICY=force
# export AAS_AUTOLOOP_FORMAL_FORCE=1
# export AAS_AUTOLOOP_FORMAL_FORCE_CREDITS=3
# export AAS_AUTOLOOP_FORMAL_TYPECHECK=0

# Path steal remains refused in MVP even if set true.
export AAS_AUTOLOOP_FORMAL_ALLOW_PATH_STEAL="${AAS_AUTOLOOP_FORMAL_ALLOW_PATH_STEAL:-0}"
