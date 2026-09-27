# Paper jobs and public-source preparation

The `lax-paper-workflow` template is the job contract. `lax-paper-artifact` and
the runtime's `paper-template/` remain the single repository-layout template.
No automatic proof converter or publication command is introduced.

From the trusted checkout, the new entrypoints are under
`canonical/runtime/skills/lax-formalization/`; installed copies are under
`workspace/skills/lax-formalization/` in the runtime root. Use the admitted Python
interpreter or the platform runtime runner for these Python entrypoints.

| Helper | Verbs |
|---|---|
| `public_source.py` | `inventory`, `closure`, `plan`, `approve`, `export` |
| `workflow_check.py` | `snapshot`, `readiness`, `render-papers`, `public-summary`, `public-bundle` |
| `tex_sanitize.py` | Bounded library called by the exporter's `tex` transformation |

Read `--help` on the helper/subcommand before composing arguments. Controller
directories must be owner-only on POSIX and disjoint from source/public roots;
private `--out` records must be new. Export uses descriptor-bound POSIX operations
and refuses native Windows mutation. Inventory/import analysis is advisory;
dynamic dependencies need review and an isolated build. No helper fetches,
initializes Git, compiles candidate code or uploads anything.

Schema sources are in the pinned checkout's `canonical/schemas/lax/`:
`workflow-job.schema.json`, `public-source-plan.schema.json`,
`paper-correspondence.schema.json`, `public-review.schema.json`,
`paper-build-review.schema.json`, and `paper-versions.schema.json`.
Runtime validators enforce the boundaries without a third-party schema library.

Readiness is an actual conjunction of existing exact-source verification,
accepted correspondence and privacy records. It refuses stale hashes, a changed
README commit, same producer/reviewer run, candidate-owned controller records,
unbound paper content, or a requested paper build which was skipped. Trusted
controller provenance is an explicit assumption, not a cryptographic identity
system. An agent must perform and retain the review before recording acceptance.

The ordinary verifier's `semantic_review` request remains unchanged. The workflow
adds paper/reviewer bindings beside it rather than inventing unsupported request
keys. A successful CI machine summary still says `semantic_status: pending`.

The TeX sanitizer is a qualified lexical transformation, not a general TeX
interpreter or perfect privacy detector. It refuses conditionals/category-code
changes/dynamic inputs and unsupported inline-verbatim commands. Literal text
and custom macros/packages still need review. Resolve unsupported input in a
private staged copy, never by changing the research source or trusting a regex.

Before the first public push, review the actual source/history, file inventory,
commit metadata, workflows and evidence. The CI bundle check constrains outgoing
diagnostics/file names but cannot reverse disclosure of source already pushed.
Both Lean and TeX builds require qualified containment from the first execution.
