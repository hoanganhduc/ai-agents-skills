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
Read-only inventory admits credential-free `git@host:owner/repo` and
`ssh://git@host/owner/repo` origins on the supported Git hosts without fetching
or editing the source config. Other configuration admission checks still apply.
Publication/verification sources continue to require canonical HTTPS origins.

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

## Published PDFs and interactive paper presentation

Record separately: the reference document read for correspondence, whether its
PDF will be distributed, and whether the user wants Lax's interactive paper
view. They are different choices. An original PDF can stay private while a
clean LaTeX rendition is submitted; neither the original PDF nor private plans
and reviews must be copied into the publication repository.

For Lax 0.1.48, `paper` is optional and accepts a LaTeX folder/entry file/engine.
The archive compiles it and derives marker coordinates. An existing PDF plus
a hand-written coordinate map is not the supported native input. A PDF link
with a statement/page/declaration table remains useful documentation, but is
not the same interactive surface. Ground later versions in their own spec.

When reproducing a published article:

- Inspect the actual license and publisher policy; “open access” alone does not
  establish permission. Preserve credit, license notices and a DOI/source link,
  and identify changes. Check third-party style/font/figure rights separately.
- Treat the selected published document as authoritative. Old TeX is a source
  candidate, not authority for its own wording or theorem numbering. Do not
  revise scientific prose merely to match a writing-style preference.
- Compare the complete rendition: prose, hypotheses, formulas, figures/captions,
  numbering, references and acknowledgments. Text extraction can reorder math
  or lose accents/bars; supplement it with visual inspection. Build success
  does not prove correspondence. Keep original and generated PDF hashes distinct.
- Use a visible rendition notice, in the location the user chose; a footer can
  carry the source DOI and license. Assert content equivalence only after an
  actual review. Rebuild and recheck the final notice/marker-bearing version.
- Remove private exchanges and revision markup only in the staged copy. When
  unwrapping a macro, preserve TeX grouping/token boundaries: deleting braces
  can turn `\cong{F}` into the unrelated control sequence `\congF`. The bounded
  sanitizer removes comments; it does not implement arbitrary macro rewriting.

Marker IDs name concepts, annotated proofs or submissions, not individual
statement axioms. A theorem with a complexity conclusion is not fully covered
merely because its structural lemma has a proof. Mark only the supported
passage and disclose the rest of the scope.

Use upstream `lax build` and the renderer to validate a native preview. Its
local paper build needs a qualified TeX sandbox; the supplied Lean executor
alone has no TeX. In 0.1.48 local build produces the marked PDF, while reflow
derivation is not enabled by default on that path. Check marker counts,
placement and actual viewer interaction, and name the surface exercised.
The generated site may call the public comments service: private local tests
should block external requests as well as use an empty browser profile.

References: [Lax paper specification](https://github.com/lax-archive/lax/blob/v0.1.48/spec.md#papers),
[live introduction](https://laxarchive.org/lax-242665/paper.html), and
[CC BY 4.0 conditions](https://creativecommons.org/licenses/by/4.0/legalcode.en)
when that is the license actually supplied with the selected article.
