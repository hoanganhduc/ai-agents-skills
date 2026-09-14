# MathSciNet And zbMATH Review Style

This overlay governs the writing of public bibliographic reviews for
Mathematical Reviews/MathSciNet and zbMATH. It extends
`writing-style-settings.md` and `math-manuscript-style.md`. It is not the
ordinary `paper-review` workflow and is not a referee-report template.

Record this overlay in `active_overlays` as
`mathscinet-zbmath-review-style`. If the item concerns graph theory or
combinatorics, keep `graph-combinatorics-style` active as well.

## Authority Labels

Rules below are labeled `[MR]`, `[zbMATH]`, `[both]`, `[heuristic]`, or
`[local safeguard]`. Do not silently harmonize service-specific rules.

Current service guidance outranks historical writing books, community advice,
and examples. Record the source URL, access date, and stated guide version when
a decision depends on current length, language, classification, formatting,
retention, or submission requirements.

- Mathematical Reviews Guide for Reviewers, page states "Updated February
  2015" and was accessed 2026-09-13:
  `https://mathscinet.ams.org/mresubs/guide-reviewers.html`.
- zbMATH Guide for Reviewers, indexed as updated 2024-11-01:
  `https://zbmath.org/static/guide-for-reviewers-letter.pdf`.
- zbMATH review template:
  `https://ctan.org/pkg/zbmath-review-template`.

MathOverflow discussions, AMS exceptional-review posts, and books by Holst,
Knuth--Larrabee--Roberts, Krantz, Strunk--White, or Trzeciak are candidate
examples and explanatory sources. They do not create venue policy.

## Material Source Boundary

A request to write a Mathematical Reviews/MathSciNet or zbMATH review does not
by itself imply that the reviewed item was supplied by either service. Do not
require a provenance declaration or delay opening a local, user-provided, or
public item merely because its acquisition path was not recorded. Treat the
item as ordinary user-provided review material unless the user states, or the
artifact itself indicates, that it was supplied by a reviewing service.

Do not add access, transfer, compilation, delegation, retention, or deletion
restrictions unless an explicit user instruction or a directly verified source
term requires them.

## Restricted Material

`[MR]` The current Mathematical Reviews guide states that material provided by
Mathematical Reviews must not be uploaded to an LLM or other AI tool, while
grammar checking of the reviewer-authored review is permitted.

`[zbMATH]` Service-provided electronic books and articles are for reviewing
only and may not be circulated; publisher terms may also require deletion after
submission. The inspected guide does not state an AI-specific prohibition
equivalent to the Mathematical Reviews rule.

## Review Purpose And Shape

`[both]` The primary purpose is to help a reader decide whether to consult the
original item. A useful default order is:

1. the context or problem;
2. the main result;
3. the principal method or proof idea, when feasible;
4. the relation to prior or parallel work;
5. an evidence-bound assessment, limitation, or audience note when useful.

State the main result precisely enough for a field-familiar reader. If a full
statement would require extensive notation or formulas, prefer a few accurate,
relatively nontechnical sentences. Include only the definitions and proof ideas
needed to understand the contribution.

Form a working one-sentence contribution thesis: what the item adds or
clarifies, and why that contribution may matter to the database's intended
reader. The thesis need not be an explicit recommendation or use promotional
language; a precise account of the main result may itself carry the thesis.

Select results in proportion to the item and to the review's purpose. A few
representative results with distinct explanatory roles are often more useful
than a theorem-by-theorem inventory. One central result can be enough, while a
complete account can be appropriate when the item contains only a few results.

When the review asserts importance, novelty, or broader significance, give the
verified concrete basis: for example, a resolved question, a new framework or
method, a sharp boundary or transition, a useful connection, or a
classification result. An accurate descriptive account of a contribution does
not need to be inflated into a claim of importance.

## Independence From The Abstract

`[zbMATH]` Do not submit a verbatim copy or minor variation of the author's
abstract or summary. The review must add useful context, detail, method, or
relation to the literature. Clearly identify an extensive direct quotation when
the source terms and venue permit it.

`[MR]` An independent, insightful review is preferred. Recommending the author
summary or publication without a review is a service-specific fallback to use
sparingly, not a drafting shortcut.

## Post-Publication Stance And Criticism Admission

`[local safeguard]` Treat both workflows as post-publication bibliographic
review, not as a pre-submission acceptance report. Do not organize the text as
a defect list, request revisions, or give an accept/reject verdict. A factual
correction or a consequential limitation may still be included when it is
verified and helps the reader use the published item accurately.

Before admitting a mathematical criticism, inspect the exact reviewed version
and the relevant definitions, conventions, standing assumptions, local
hypotheses, and surrounding argument. Distinguish an assumption established by
the paper's context from a new hypothesis invented only to rescue the claim.
Classify the issue as a false statement, a scope limitation, a proof gap, a
typographical problem, or unresolved uncertainty, and require precise evidence
for the chosen classification.

Do not present a counterexample as a refutation when an identifiable contextual
assumption or stable convention rules it out. Conversely, do not silently add
a new assumption merely because it would make the statement true. If an
established contextual assumption materially narrows how readers can use the
result, describe the scope accurately rather than alleging a false theorem.

A gap in the displayed proof does not by itself prove that the theorem is
false. Include such a gap in a public review only when it is fully verified,
consequential for readers, and described according to the evidence actually
available.

OCR, plain-text extraction, or memory alone is insufficient evidence for a
wording-sensitive mathematical criticism. Check an authorized page image or a
source that reliably corresponds to the exact reviewed version. TeX from a
different version may clarify notation but must not override the reviewed
publication. If notation was renamed across versions, record the correspondence
and disclose it when the distinction matters to the criticism.

## Evaluation And Criticism

Keep the factual account primary. A positive or negative evaluation is optional
and must be distinguishable from the bibliographic and mathematical record. Do
not treat a review as advertising, an endorsement, or an appreciation letter.

Make criticism objective, precise, documented, and civil. A claim of error must
identify where the problem occurs and provide a counterexample, an exact
supporting reference, or equivalent evidence. A claim of duplication or
significant overlap must cite the earlier work specifically. Do not launder an
unchecked suspicion into smoother prose.

`[zbMATH]` A zbMATH review is not a referee report. The item has already been
published, so do not recommend changes that should have been made before
publication.

## Related Work And Discoverability

Include related work, later developments, alternative terminology, search
terms, prerequisites, or an accessibility note only when they help the reader
and have been verified. Choose a few decision-relevant connections rather than
reproducing a bibliography.

Verify citations against public or otherwise available primary sources. Mark a
citation `unchecked` when the evidence is incomplete. A citation-integrity
packet can structure the evidence, but it does not retrieve or verify a source.

## Platform-Specific Delivery

`[MR]` Follow the current MR guide for English, length, book-review limits,
complete reference data and MR numbers, Mathematics Subject Classification,
and PDF/LaTeX proofreading. The guide currently describes ordinary reviews as
ranging from a few lines to about 600 words and book reviews as normally shorter
than 600 words with a maximum of 1500; recheck these values before relying on
them.

`[zbMATH]` Follow the current zbMATH guide for accepted languages, length,
translated-title checking, MSC2020, keywords, and standard LaTeX. Do not define
custom macros. Consult the live guide and CTAN template for the supported
package set and typesetting details instead of copying a version-sensitive list
into this policy. Avoid tables, large diagrams, drawings, and formulas of minor
importance when the current guide so directs.

## Examples As Heuristics

Exceptional examples may illustrate early context, minimal definitions,
prerequisites, a reading roadmap, a carefully chosen theorem, a proof method,
related resources, and precise criticism. They do not require length, humor,
anecdotes, a scathing tone, or imitation of the reviewer's voice.

Inspect a small authorized set of examples when the format is unfamiliar or an
editorial choice remains unresolved. Record the structural technique that is
useful, not distinctive wording. Do not require an example search for every
review, and do not treat an exceptional review as representative of ordinary
service expectations. Current service guidance still outranks examples.

For a book, do not default to a chapter-by-chapter report. Situate the book in
its subject, then select the decision-relevant aspects: intended audience and
prerequisites; scope and organization; proof and exposition quality; examples
and exercises; figures; index, glossary, and notation aids; bibliography,
attribution, and currency. The review remains about the book, not an unrelated
essay about the field.

## Scientific Prose And LaTeX

Apply the inherited scientific-prose and LaTeX rules in
`math-manuscript-style.md`. In particular, use `\emph{}` at the first defining
occurrence of a term and not as a quota or repeated decoration. Keep
mathematical notation in math mode, prefer service-standard LaTeX, and do not
introduce custom macros when the service forbids them.

## Final Check

Before delivery, confirm that:

- the review request was not treated as evidence that the item was supplied by
  a reviewing service;
- service-supplied restrictions were applied only when the user or the
  artifact identified a service-supplied copy;
- the review adds value beyond the abstract;
- a factual, nonpromotional contribution thesis explains why the intended
  reader may wish to consult the item;
- result selection is proportionate and does not default to a theorem
  inventory;
- context, result, and method are proportionate to the item;
- facts, evaluations, and uncertainty are distinguishable;
- claims of importance, novelty, criticism, and related work have precise
  evidence;
- mathematical criticism was checked against the exact reviewed version and
  its relevant context rather than OCR alone;
- notation correspondences across versions are recorded when they affect a
  claim;
- `\emph{}` appears at first defining occurrences rather than as repeated
  decoration;
- service-specific language, length, LaTeX, MSC, keyword, and reference rules
  were checked against the current guide;
- inclusion or exclusion of reviewer identity, portal data, and confidential
  correspondence follows explicit user instructions and directly verified
  source terms;
- the active style profile and requirement IDs are recorded.
