# production_formalization_env.inc.sh — Preset B: production formalization (claude)
#
# Open-book production lane: formal tools assist via a curated MCP config, the
# host-owned panel supplies multi-agent verification, and the F2' library-first
# gate stays active (BINDING_BLOCK emits it whenever formal policy is on and
# the path is formal-track — that is the intended production behavior).
# Source from an install-owned or operator-owned path; see formal_env.inc.sh
# for the trusted-path rules that apply to every fragment in this pack.
#
# LANE: strict-isolated, NON-attested drive lane only. Custom
# AAS_AUTOLOOP_ARGS_CLAUDE overrides are refused fail-closed for host-attested
# providers, and Goal-Focus enforce requires that attestation — so this preset
# is NOT expressible under enforce today. A first-class MCP-config knob for the
# attested lane is deliberately deferred (R1) pending its own adversarial
# security review: an MCP config grants live tool-server authority to an
# unattended agent. The scripted force-loop lane also has no MCP channel
# (run_force_loop.sh unsets LEANEXPLORE_API_KEY; the compute-lane allowlist
# has no MCP entry). See force-loop/OPERATOR_RUNBOOK.md "Banked launch presets".
#
# MCP CONFIG PLACEMENT (trust-critical): the curated config must live at an
# absolute operator-owned path OUTSIDE every loop tree, mode 0600 — never
# {dir}-relative, because the loop tree is agent-writable and the config names
# the tool servers the unattended agent may talk to.
#
# SERVER API KEYS (e.g. LEANEXPLORE_API_KEY): the ONLY sanctioned delivery
# channel is the per-server "env" block inside the curated MCP config file
# (see curated_mcp.claude.example.json). Extending an env -i keep-list can
# NEVER deliver such a key: the primary child environment is strictly
# allowlist-built by build_primary_child_env from PRIMARY_BASE_ENV_ALLOWLIST,
# AAS_RUNTIME_*, and attestation-gated provider/compute credential lists, and
# LEANEXPLORE_API_KEY is on none of them (in the non-attested lane even
# ANTHROPIC keys are stripped; claude authenticates from on-disk OAuth under
# HOME). This is why the config file's ownership and mode matter.
#
# Drive with --panel auto (or on): Task/Agent/Skill are disallowed for the
# primary below, so the host-owned panel is what supplies independent
# multi-agent verification of banked results.

# Operator-owned curated MCP config, outside every loop tree. Copy
# curated_mcp.claude.example.json somewhere operator-owned, fill it in,
# chmod 0600, and point at it here.
export AAS_ARL_MCP_CONFIG="${AAS_ARL_MCP_CONFIG:-/abs/operator-owned/curated_mcp.claude.json}"

# --strict-mcp-config keeps the curated file authoritative: user- and
# project-level MCP configs are ignored, so exactly the reviewed servers load.
export AAS_AUTOLOOP_ARGS_CLAUDE="-p {prompt} --dangerously-skip-permissions --strict-mcp-config --mcp-config ${AAS_ARL_MCP_CONFIG} --setting-sources project --disallowedTools WebSearch WebFetch Task Agent Skill"

export AAS_AUTOLOOP_FORMAL_POLICY=on
export AAS_AUTOLOOP_FORMAL_TYPECHECK=1
