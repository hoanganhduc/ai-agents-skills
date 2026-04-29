# Python Quality Gates

Use project-local tooling when present. Prefer the narrowest check that proves
the changed behavior, then broaden only when the change touches shared code.

Common checks:

```bash
python -m compileall <package-or-dir>
python -m unittest
python -m pytest
```

Use whichever command the repository already documents or configures. Do not
invent a new formatting or linting stack unless the project already uses it.

Report:

- command run
- result
- skipped checks and why
- remaining risk
