# Native Lean on an authorized remote executor

Ordinary Lean/Lake remains the default artifact format. The optional Kaggle CPU
executor runs the existing strict gate; it does not create Lax packages or claim
Lax Docker isolation. Later Lax conversion uses `lax-paper-workflow` with
`from-existing-lean`.

## Select the execution policy

The formal policy accepts `execution_backend: "local"` (default) or
`"kaggle-cpu"`, plus `remote_request` for the latter. Equivalent host environment
keys are `AAS_AUTOLOOP_FORMAL_EXECUTION_BACKEND` and
`AAS_AUTOLOOP_FORMAL_REMOTE_REQUEST`. They pass through the existing policy
merge and host pin; a candidate cannot change the pinned request mid-run.
Existing Lax-layout jobs keep their Lax executor and refuse this native remote
backend.

An explicit backend still obeys `compute_policy.json`, `standing_orders.compute`
and `current_plan.compute_policy`. Explicit allowlists intersect, an empty list
denies execution, and any deny wins. Registration binds the policy projection
and plan revision and rechecks them before dispatch. `STOP_REQUESTED`, `PAUSE`
and `BLOCKED` prevent new remote work. A changed policy preserves existing
attempts for host reconciliation; it never resets accounting.

The request is a private host-owned `remote_lean_request.v1` JSON file outside
the candidate repository. Its fields are:

| Fields | Meaning |
|---|---|
| `schema_version`, `project_root`, `owner` | Exact native project and reviewed Kaggle owner |
| `state_root` | Private per-job controller directory outside the project |
| `compute_config`, `compute_config_sha256` | Exact host compute configuration and digest |
| `source_files` | Explicit relative source/config inventory; no `.git` or `.lake` uploads |
| `protected_files` | SHA-256 map for toolchain, lockfile and reviewed Lake configuration |
| `bootstrap_script`, `bootstrap_sha256` | Private host script preparing the selected dependencies |
| `gate_sha256` | Installed strict-gate script digest, not a candidate-selected checker |
| `targets` | Explicit fully qualified theorem declarations |
| `lean_executable`, `lake_executable` | Relative executable paths prepared by bootstrap |
| `lean_version`, `lake_version` | Exact expected `--version` output |
| `dependency_files` | Explicit relative dependency/executable paths and expected SHA-256 values |
| `enable_internet` | Explicit request; requires host `[kaggle].allow_internet = true` |
| `require_kernel` | Whether a separate kernel replay is required |
| `checker_executable`, `checker_sha256`, `kernel_modules` | Required when kernel replay is selected |
| `max_attempts`, `timeout_seconds` | Bounded verification attempts and per-phase timeout, including setup |
| `submission_authorized` | Host task authorization; a value inside candidate data cannot supply it |

Use actual inspected hashes and versions, not placeholders or guessed pins.
`source_files` includes `lean-toolchain`, `lake-manifest.json` and the Lake file.
Bootstrap installs into the isolated job workspace so the declared relative
executables and dependency files can be checked before and after verification.
The explicit dependency inventory does not claim a complete fingerprint of the
entire Kaggle image. Allow time and disk for downloads and compilation.

## Host execution and result admission

Live execution requires the existing credential broker's parent authority and
a qualified POSIX main-thread controller. The broker registers the pinned
request, config and scope, then runs only the fixed controller with Kaggle-only
credential projection. Worker tokens cannot register, advance or validate a
formal job. The remote job never receives the broker token or host signing key.

The broker retains authenticated state checkpoints before publishing mirrored
files. Status polling and saved-response recovery use the exact accepted Kaggle
version, never latest or an automatic second submission. Interrupted mirror
publication is repaired from the protected checkpoint. A submission with no
durable acceptance response remains unknown, with no repush.

Formal terminal evaluation returns pending until the selected build and audit
phases have finished and the host has admitted the bound evidence. Finalization
starts a distinct verification attempt and resumes it across polls/restarts;
it does not rerun the content-review panel. Legacy append uses
`resume-legacy-verification --dir LOOP`. Exit 19 means pending; exit 21 means
incomplete. Both preserve the candidate/append and stop automatic producer
failover. User stop/pause and required compute policy remain in force.

Host receipt admission is authenticated by the broker, not by file permissions,
model prose, a descriptor-provided key or self-consistent hashes. The signing
authority stays in the stable private host-authority directory, outside release
code and excluded from contained worker/controller views. A missing key for
existing signed state is not repaired by silently issuing a new identity.

For deep research, `verification_source: "host_verified_remote_lean"` references
a local `host_remote_lean_reference.v1` descriptor containing only
`receipt_path`, `receipt_sha256`, `project_root` and `schema_version`. Validation
requires the separately held host authority; a candidate-created descriptor
alone never promotes support. The Lean statement reference also identifies the
declaration and source bytes. Correspondence review remains a separate gate.

Internet is kernel-wide, not restricted to setup by this interface. Native
project code, bootstrap and the selected toolchain are trusted execution inputs;
the receipt explicitly discloses those assumptions. Lax readiness, remote
publication and cross-family review are not implied by native proof checking.

## Qualification and installation

Offline tests use fixed local provider substitutes and do not authenticate,
submit kernels or create real signing keys. Live Kaggle image compatibility and
performance require a separately scoped qualification run. Native Windows and
non-main-thread live SDK execution are currently refused before authentication;
offline planning remains available. Runtime installation copies the required
adapters without enabling a paid lane or installing optional SDK packages.
