# hermetic_benchmark_env.inc.sh — Preset A: hermetic closed-book benchmark (claude)
#
# The exact recipe that passed the 2026-08 closed-book Lean formalization
# benchmarks (T0–T4: informal-spec autoformalization plus miniF2F statements,
# all host-authored sorry_free_artifact with a real lake build and clean leak
# audits). Source from an install-owned or operator-owned path before calling
# autonomous-research-loop-runtime `drive`; see formal_env.inc.sh for the
# trusted-path rules that apply to every fragment in this pack.
#
# LANE: strict-isolated, NON-attested drive lane only. AAS_AUTOLOOP_ARGS_CLAUDE
# is a custom argument override, and the runtime refuses it fail-closed the
# moment the provider is host-attested ("a host-attested provider cannot use
# custom argument overrides") — which Goal-Focus enforce campaigns and the
# scripted force-loop require. Do not source this fragment into a force-loop
# or enforce lane; see force-loop/OPERATOR_RUNBOOK.md "Banked launch presets".
#
# (a) Launch under a scrubbed environment. The tested keep-list:
#
#     env -i HOME="$HOME" PATH="$PATH" LANG="$LANG" TERM="$TERM" \
#         TMPDIR="$TMPDIR" USER="$USER" PYTHONDONTWRITEBYTECODE=1 \
#         bash -c 'source hermetic_benchmark_env.inc.sh && python3 .../autonomous_research_loop_runtime.py drive ...'
#
#     env -i is not optional hygiene: the primary prompt-privacy gate
#     (assert_panel_prompt_safe) scans the iteration prompt against inherited
#     secret-shaped env values, and PANEL_SECRET_ENV_NAME matches any name
#     containing SESSION — an inherited CLAUDE_CODE_SESSION_ID whose UUID
#     appears in a session-scoped loop path trips
#     primary_prompt_privacy_gate_failed and kills the launch.
#
# (b) Tested drive flags:
#
#     drive --provider claude --iteration-timeout 3600 --max-failures 3 \
#           --panel off --notify off --formal-policy on --formal-project .
#
#     Init the loop with: --goal-focus-mode off --formal-policy on
#     --formal-project .  (closed-book rules also injected into
#     loop_state.standing_orders.compute; keep goals text authoritative).
#
# (c) NOTE — F2' library-first suspension is a PROMPT-LEVEL convention, not a
#     runtime flag. BINDING_BLOCK always emits the library-first clause when
#     formal policy is on and the path is formal-track; the benchmark's
#     closed-book behavior held only because the goal/success-criteria wording
#     overrode it AND no library skill was reachable (Skill disallowed below,
#     env -i above). Reproduce both, or accept the moot F2' text in the prompt.

# Closed-book primary: no web, no subagents, no skills, no user-level config
# (--setting-sources project strips user CLAUDE.md and user skills — verified
# by probe), no MCP servers beyond project config (--strict-mcp-config).
export AAS_AUTOLOOP_ARGS_CLAUDE='-p {prompt} --dangerously-skip-permissions --strict-mcp-config --setting-sources project --disallowedTools WebSearch WebFetch Task Agent Skill'

export AAS_AUTOLOOP_FORMAL_POLICY=on
export AAS_AUTOLOOP_FORMAL_PROJECT=.
