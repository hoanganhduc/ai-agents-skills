# Lax migration execution

Implementation authorized on 2026-09-27. The no-publication boundary in the
[plan](lax-formalization-migration-plan.md) remains active.

| Task | State | Evidence |
|---|---|---|
| T0 | complete | Scoped installed-file/state backups; 142 baseline tests pass; resource/Docker checks recorded privately |
| T1 | complete | Request/reuse/verification schemas; behavioral regressions and state-based retirement tests |
| T2 | complete | Pinned controlled executor; real True/open/rogue/native proof cases passed and the hardened template verification passed; independent review fixes applied |
| T3 | complete | Shared template, locked CI tools, local CI entrypoint; end-to-end local CI entrypoint passed |
| T4 | complete | Offline archive preparation, consistency validation and safe restore; 9 regression tests pass |
| T5 | complete | 5 new ARL tests + 36 policy and 74 terminal-state regressions; canonical routing updated |
| T6 | complete | Reference subset, blind candidate and restored bundle passed; weakened target refused; see benchmark report |
| T7 | complete | Nine agent targets and both runtime roots verified; old managed skill retired, five manual rules replaced, config deactivated; library sources preserved |
| T8 | complete within migration scope | Native benchmark/archive and focused checks passed; full-suite pre-existing vnthuquan/cache issues documented in verification report |

Baseline: repository `c2b68de26e8ed4f90e956107855624bf5bedf5db`, with only the
two planning documents untracked. The resource-wrapper entrypoint had a
pre-existing execute-permission error; the documented direct Python fallback
worked. No unrelated runtime permission was changed.

Private logs/backups are in the ignored `.codex/runs/lax-migration-20260927/`
directory. Do not publish them. Baseline suites: `test_lean_gate_scanner.py`
(85), `test_autoloop_formal_policy.py` (36), and
`test_lean_research_library_runtime.py` (21).
