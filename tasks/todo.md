# Tasks

- [x] Task: Refresh lifecycle artifacts.
  - Acceptance: `SPEC.md`, `tasks/plan.md`, and `tasks/todo.md` describe
    force-managed autonomous loop enforcement.
  - Verify: files are updated in this patch.
  - Files: `SPEC.md`, `tasks/plan.md`, `tasks/todo.md`

- [x] Task: Add the stop-policy instruction rule.
  - Acceptance: the policy (priority 0 plus four defaults, hard rule, escapes,
    fail-open) is installed as agent behavior and registered in manifests.
  - Verify: targeted tests inspect canonical text; manifest membership is valid.
  - Files: `canonical/instructions/autonomous-loop-enforcement.md`,
    `manifest/artifacts.yaml`

- [x] Task: Add the runtime enforcement engine.
  - Acceptance: arm/disarm/active/done/hook-check exist; `done` is derived with
    strict precedence; the hook path is fail-open; schema carries
    `stop_conditions` and `success_check`.
  - Verify: enforcement unit tests pass.
  - Files: `canonical/runtime/skills/autonomous-research-loop-runtime/autonomous_research_loop_runtime.py`,
    `tests/test_autonomous_research_loop.py`

- [ ] Task: Subordinate shipped plateau/blocker/evidence-gap stops.
  - Acceptance: under the defaults these no longer end the loop.
  - Verify: a negative test proves they do not fire by default.
  - Files: `canonical/skills/autonomous-research-loop/SKILL.md`, runtime defaults.

- [x] Task: Build the JSON-merge core (pure logic).
  - Acceptance: idempotent upsert + removal of one tagged managed hook entry;
    a populated `settings.json` round-trips exactly; non-JSON is refused.
  - Verify: `tests/test_json_merge.py` (14 tests) green, incl. populated
    round-trip and user-hook preservation.
  - Files: `installer/ai_agents_skills/json_merge.py`, `tests/test_json_merge.py`

- [x] Task: Adversarial security/correctness audit of the enforcement core.
  - Acceptance: audit the runtime enforcement + json_merge for settings
    corruption, fail-open correctness, path trust, races, and bypass; fix the
    HIGH findings with regression tests.
  - Verify: 40 autoloop+json_merge tests green, incl. new B2 and A1 regressions.
  - Fixed: B2/E2 (require-user-stop-only now overrides agent-written terminal
    status), A1 (round trip preserves user-authored empty containers), A3
    (atomic `write_json`), C1 (re-resolve `entry_root` at match time), E4
    (absolute sentinel path in the kill-switch message).

- [ ] Task: Apply deferred security-hardening from the audit (lower severity).
  - A2: report a conflict instead of silently overwriting a settings entry that
    collides on the managed marker (needs a managed-hash).
  - D1: treat "pid recorded but no heartbeat" as not-live (PID-reuse).
  - D2: exclusive lock around arm's scan-and-write (TOCTOU).
  - D3: give the PAUSE sentinel a TTL so a stale file can't disable enforcement.
  - E3: document loop-dir 0700 as the sentinel trust boundary.
  - Verify: targeted unit test per item.
  - Files: `autonomous_research_loop_runtime.py`, `json_merge.py`, docs.

- [x] Task: Wire the JSON-merge apply/uninstall mechanism through the lifecycle.
  - Acceptance: a `json-merge` action kind applies via load->merge->atomic-write
    (backup first, refuse non-JSON/symlinked/dir) and uninstall removes only the
    tagged entry via a dedicated `merge-remove` origin/operation (never whole-file
    restore), restoring a populated `settings.json` exactly.
  - Verify: `tests/test_settings_hook_merge.py` (3 tests) green, incl. a user
    hook added AFTER install surviving uninstall; full installer suite green.
  - Files: `apply.py` (`apply_json_merge_action`, route, preflight,
    `uninstall_origin`), `lifecycle.py` (`plan_uninstall_action`,
    `apply_uninstall_action`, `_apply_merge_remove`).

- [x] Task: Register the Stop-hook surface honestly (make it active).
  - Acceptance: a fail-open Stop-hook wrapper ships + installs; planner emits the
    `json-merge` action for claude (only when the runtime is selected) with the
    real wrapper command; honest `target_surfaces` row (claude SUPPORTED via
    `json-merge`; codex/opencode remain driver-only — TOML config, not JSON).
  - Verify: dry-run `plan` shows the exact settings.json merge (user content
    preserved); 6 wrapper tests + 2 planner tests green; full installer suite
    green (incl. the audit-system None-classification fix).
  - Files: `canonical/runtime/.../autoloop_stop_hook.sh`, `manifest/runtime.yaml`,
    `planner.py`, `target_surfaces.py`, `apply.py`, generated docs.

- [x] Task: Phase C (part 1) — headless driver + core docs.
  - Acceptance: a generic `autoloop_driver.sh` (derives done from the runtime,
    `AUTOLOOP_DRIVER=1` exemption, per-iteration timeout, fail-safe stop, stops
    after K failures), packaged in the runtime manifest; architecture +
    surfaces docs and the claude README document the honest matrix.
  - Verify: 5 driver tests green; full installer suite green; `make docs` clean.
  - Files: `canonical/runtime/.../autoloop_driver.sh`, `manifest/runtime.yaml`,
    `installer/ai_agents_skills/docs.py`, `targets/claude/README.md`.

- [ ] Task: Phase C (part 2) — breadth + parity.
  - Honest enforcement note in the other 6 target READMEs (codex/opencode
    driver-only; copilot/deepseek/openclaw/antigravity: no built-in enforcement).
  - Windows `.ps1` Stop-hook wrapper + driver parity (currently linux/macos/wsl).
  - Rebuild the Sphinx site (`make docs-site`) if docs deps are available.
  - Then: user-gated real-system `apply` verifying the hook fires + fails open.

- [ ] Task: Add the hook template and headless driver.
  - Acceptance: a fail-open Stop-hook template and a generic driver with
    `.sh/.ps1/.bat` launchers, `AUTOLOOP_DRIVER` exemption, and least-privilege
    headless flags.
  - Verify: driver timeout, pause/resume, and sentinel tests pass.
  - Files: `canonical/templates/hooks/`, driver and launcher files.

- [ ] Task: Update docs and target READMEs; rebuild Sphinx.
  - Acceptance: docs and all target READMEs state the honest per-target matrix.
  - Verify: `make docs-check`; generated docs rebuilt from `docs/source/`.
  - Files: `docs/`, `docs/source/`, `targets/*/README.md`, `README.md`
