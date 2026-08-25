# External Dependency Bootstrap Tasks

- [x] Confirm source pins, package metadata, local skill execution contracts,
      and installer dependency behavior.
- [x] Run a fresh-context security decision review and revise the design.
- [x] Define scope, target matrix, safety boundary, and verification plan.
- [x] Add manifest and command implementation.
- [x] Add focused tests and generated documentation.
- [x] Run dry-run and repository verification.
- [x] Apply the approved host plan and propagate the five dependent skills.
- [x] Verify OpenClaw separately without changing its environment.
- [x] Record executed checks, platform gaps, and residual supply-chain limits.

## Execution record

- Host provisioning: the confirmed plan for `course-management,vnu-eoffice`
  revalidated both existing generations and activated their recorded receipts.
  The dedicated host pointers import `course_hoanganhduc` and `vnu_eoffice`
  (plus `requests` and `bs4`) successfully.
- Native dependent-skill precheck: `classroom50`, `course-canvas`, `course-db`,
  `course-google-classroom`, and `vnu-eoffice` report their required host
  dependencies as available. Their installed artifacts also passed the
  selected-skill verification (85 artifacts across detected native targets).
- OpenClaw (read-only): its active sandbox can import the image-local
  `vnu_eoffice` checkout and run the adapter help/no-network doctor path.
  Live authentication and remote eOffice operations were deliberately not run.
  The same sandbox has no `course_hoanganhduc` package, course virtual
  environment, or four course-skill directories; host availability is not
  evidence of OpenClaw availability.
- Verification: focused dependency/runtime-inventory checks, static/docs/
  sanitization checks, and a clean `make test` run passed (3,247 tests;
  35 expected skips).
- Residual boundary: static third-party wheel locks currently cover only
  `linux-aarch64`; other platforms fail closed. Pinned source/build code is
  not OS-sandboxed, despite its scrubbed environment and isolated working
  directory.
