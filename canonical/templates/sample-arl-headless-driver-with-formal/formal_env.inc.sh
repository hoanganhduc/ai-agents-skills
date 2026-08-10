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
# Secrets: never source key files here, and do not print env dumps.
#
# LEANEXPLORE_API_KEY cannot be delivered from this fragment, or from any other
# env fragment. The primary child environment is strictly allowlist-built by
# build_primary_child_env (PRIMARY_BASE_ENV_ALLOWLIST, AAS_RUNTIME_*, and the
# attestation-gated provider/compute credential lists); LEANEXPLORE_API_KEY is
# on none of them, so pre-exporting it before launch is stripped and LeanExplore
# stays unconfigured. The only sanctioned channel is the per-server "env" block
# of a curated MCP config — see production_formalization_env.inc.sh and
# curated_mcp.claude.example.json in this pack for the lane rules and the
# operator-owned 0600 placement that channel requires.

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
