# Manual OpenGauss workflows (MVP)

## Preconditions

- Existing Lake project
- OpenGauss installed (manual)
- Backend authenticated (claude-code and/or codex)
- `lean-formalization-intake` decision `proceed` when used in research loops

## Recommended commands (upstream REPL)

1. `gauss`
2. `/project init` or `/project use`
3. `/prove <scope>` or `/draft <topic>`
4. Export or copy changed `.lean` files into the research workspace
5. Run AAS `lean-strict-verification-gate`
6. Lead/human statement-equivalence before claim-support language

## Do not

- Use `/swarm` unbounded without budgets
- Cite job success as "proved"
- Auto-launch via AAS until `headless_qualified` + headless driver exist
