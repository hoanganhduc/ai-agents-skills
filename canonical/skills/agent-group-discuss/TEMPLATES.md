# Research Templates

Use `EXECUTION.md` for the actual Codex round topology, role-prompt structure, timeouts, and orchestration rules.

The user can request a template by name, or the orchestrator can auto-select based on task signals. If auto-selecting, briefly state which template was chosen and why before proceeding. The user can override.

## Template auto-selection

| Task signal | Recommended template |
|-------------|---------------------|
| "verify my proof", "check this theorem", "stress-test", "find holes" | **Lakatos Proof and Refutation** |
| "attack this problem", "explore complexity", "is this hard or easy", "open problem" | **Polya Multi-Strategy Problem Solving** |
| "review my draft", "pre-submission review", "check exposition", "camera-ready" | **Knuth Structured Manuscript Review** |
| General math/TCS claim, algorithm analysis, combinatorial argument | **Structured Research Team** |
| Token sliding/jumping, reduction proof, gadget verification, reconfiguration, PSPACE reduction | **Graph Reconfiguration Specialist** |
| "formalize this lemma", "Lean proof", "fix this sorry", "formalization" | **Lean Formalization Team** |

If multiple templates match, prefer the more domain-specific one.

## Template chaining

When a task spans multiple concerns, chain templates as sequential phases within a single run. Each phase uses one template, and the output of one phase feeds into the next.

Common chains:

| Task | Phase 1 | Phase 2 | Phase 3 |
|------|---------|---------|---------|
| "Verify this reduction and review the draft" | Graph Reconfiguration Specialist | Knuth Structured Manuscript Review | — |
| "Explore this problem, then formalize" | Polya Multi-Strategy Problem Solving | Structured Research Team | Lean Formalization Team |
| "Check my proof, fix it, then prepare for submission" | Lakatos Proof and Refutation | Graph Reconfiguration Specialist | Knuth Structured Manuscript Review |
| "Verify a gadget family and formalize the key lemma" | Graph Reconfiguration Specialist | Lean Formalization Team | — |

How chaining works:

1. Run Phase 1 to completion.
2. Pass only accepted claims and strongest surviving proof skeletons into the next phase.
3. Keep per-phase round files.
4. Combine all phase ledgers in the final output.

## Mandatory plan

Before any template begins, the orchestrator must show:

- model assigned to each role
- reasoning tier and reasoning effort
- estimated time per role and total time
- execution order by round

## Mandatory preamble

Before any high-stakes research template begins, the orchestrator must also produce a Step 0 restatement:

1. rewrite the target claim in exact mathematical terms
2. list all assumptions explicitly
3. separate what is given, to be proved, and conjectured
4. identify the notation and definitions in use

## Stop rule

If a decisive counterexample or fatal gap is found during any round:

1. stop defending the broken claim
2. switch to diagnosis
3. determine the strongest defensible corrected claim
4. do not continue expanding a broken proof across further rounds

## Default research output format

The final synthesis for research-mode templates must contain:

1. `Accepted`
2. `Rejected`
3. `Unresolved`
4. `Strongest surviving proof skeleton`
5. `Verification status`
6. `Single recommended next action`

---

## Template: Lakatos Proof and Refutation

Based on Lakatos, *Proofs and Refutations*.

When to use:

- stress-testing a new theorem or proof draft
- finding edge cases before submission

Mode: `review`
Profile: `math-heavy`
Interaction: `debate`

Roles:

| # | Role | Reasoning | Task |
|---|------|-----------|------|
| 1 | Prover | R4 | Present the proof, defend the argument, propose fixes when flaws are found |
| 2 | Counterexample Hunter | R4 | Search systematically for counterexamples and boundary cases. Use `sagemath` when useful. |
| 3 | Monster-Barrer / Refiner | R3 | Propose refined theorem statements or restricted hypotheses after failures are found |
| 4 | Formalist | R4 | Check logical structure, assumptions, quantifiers, and hidden dependencies |

Rounds:

Round 1:

- Prover: present the claim and proof sketch, with dependency structure
- Counterexample Hunter: identify likely failure points and candidate counterexamples
- Monster-Barrer: identify boundary conditions and likely hypothesis gaps
- Formalist: list explicit and implicit assumptions and unjustified steps

Round 2:

- each role responds to the strongest findings from Round 1

Round 3:

- Formalist or orchestrator synthesizes the strongest surviving claim and remaining gaps

---

## Template: Polya Multi-Strategy Problem Solving

Based on Pólya, *How to Solve It* and *Mathematics and Plausible Reasoning*.

When to use:

- attacking an open problem or conjecture
- exploring the complexity boundary

Mode: `research`
Profile: `premium`
Interaction: `star`

Roles:

| # | Role | Reasoning | Task |
|---|------|-----------|------|
| 1 | Specializer | R3 | Attack restricted instances and easy-case boundaries. Use `sagemath` for experiments when needed. |
| 2 | Generalizer | R4 | Connect to known techniques, dichotomies, and neighboring results |
| 3 | Reducer | R4 | Assume hardness and search for promising reduction sources and gadget outlines |

Rounds:

Round 1:

- Specializer: characterize easy and hard restricted cases
- Generalizer: list promising known techniques and likely obstacles
- Reducer: list reduction sources and sketch the top candidate

Round 2:

- each role critiques or refines its direction after seeing the others' findings

Round 3:

- orchestrator produces a ranked list of approaches, expected difficulty, and recommended next step

---

## Template: Knuth Structured Manuscript Review

Based on Knuth et al., *Mathematical Writing*.

When to use:

- reviewing a paper draft before submission
- preparing a camera-ready version
- responding to referee reports

Mode: `review`
Profile: `premium`
Interaction: `panel_judge`

Roles:

| # | Role | Reasoning | Task |
|---|------|-----------|------|
| 1 | Correctness Reviewer | R4 | Read proofs line by line, verify claims, and flag correctness issues by severity |
| 2 | Exposition Reviewer | R3 | Review clarity, structure, notation, motivation, and readability |
| 3 | Literature Reviewer | R3 | Review novelty claims, related work positioning, and citation accuracy |

Rounds:

Round 1:

- Correctness: report issues with section, severity, and concrete fix suggestions
- Exposition: report readability and explanation issues with rewrite suggestions
- Literature: report novelty or citation issues and missing references

Round 2:

- orchestrator reconciles overlap and produces a single prioritized action list:
  - critical correctness issues
  - significant exposition problems
  - missing or wrong citations
  - minor issues
  - cosmetic suggestions

For Codex review outputs, findings should still be ordered by severity.

---

## Template: Structured Research Team

When to use:

- verifying a specific claim, proof, reduction, or structural characterization
- handling a high-stakes correctness review

Mode: `research`
Profile: `math-heavy`
Interaction: `star`

Roles:

| # | Role | Reasoning | Task |
|---|------|-----------|------|
| 1 | Builder | R4 | Propose the strongest plausible proof strategy, algorithm, reduction, or characterization |
| 2 | Breaker | R4 | Search aggressively for hidden assumptions, counterexamples, missing directions, and boundary failures |
| 3 | Alternative Builder | R4 | Produce a genuinely different route and explain why it is meaningfully different |
| 4 | Referee / Verifier | R4 | Accept only what survives objections and explicit checks |

Rounds:

Round 1:

- Builder outputs exact claim, strategy, intermediate lemmas, fragile step, and suggested external checks
- Breaker outputs strongest objection, failing step, counterexample candidate, fatality assessment, and smallest fix
- Alternative Builder outputs a different route, its bottleneck, advantage, and failure mode

Round 2:

- roles critique only decisive issues in the others' proposals

Round 3:

- orchestrator runs external verification where feasible:
  - `sagemath` brute force or enumeration
  - local search for counterexamples
  - dependency audit
  - SAT/SMT/ILP if available
  - citation verification

Round 4:

- optional repair round only if a local repair is concrete and credible

Hard rules:

- correctness over elegance
- distinguish proved, heuristic, conjectural, and unverified claims
- check both directions of equivalences
- check boundary cases explicitly
- prefer a weaker correct theorem over a stronger broken one

---

## Template: Graph Reconfiguration Specialist

Domain-specific variant of the Structured Research Team for token sliding, token jumping, gadget verification, and reconfiguration complexity.

When to use:

- proving PSPACE or NP hardness for a reconfiguration problem
- repairing a broken reduction
- verifying a gadget family
- checking a theorem in a draft
- analyzing a reconfiguration algorithm

Mode: `research`
Profile: `math-heavy`
Interaction: `star`

Roles:

| # | Role | Reasoning | Task |
|---|------|-----------|------|
| 1 | Constructor | R4 | Propose the strongest plausible proof strategy, reduction, algorithm, or repaired statement |
| 2 | Adversary | R4 | Search aggressively for counterexamples, hidden assumptions, broken directions, and nonlocal failures |
| 3 | Auditor | R4 | Perform both local/interface and global/correspondence audits |
| 4 | Referee / Verifier | R4 | Maintain the claim ledger and downgrade claims immediately when checks fail |

Claim ledger fields:

- ID
- statement
- status: proved / refuted / unclear / heuristic
- confidence
- dependencies
- verification type
- owner
- notes

Rounds:

Round 1:

- Constructor outputs exact claim version, outline, lemmas, fragile step, suggested checks, and whether weakening may be needed
- Adversary outputs strongest objection, suspicious step, counterexample candidate, fatal vs repairable assessment, and smallest plausible fix
- Auditor outputs:
  - local/interface audit
  - global/correspondence audit

Round 2:

- Adversary and Auditor critique Constructor
- Constructor responds only to decisive issues

Round 3:

- orchestrator runs verification using:
  - `sagemath` for brute force, invariant checks, and enumeration
  - `graph_verifier` for lighter sanity checks
  - structural checks for graph-class membership and size claims
  - bibliographic checks
  - formal checks when formalization is part of the task

Round 4:

- optional repair round only for a concrete local repair

Typed verifier table:

| Check type | Target | Passed/Failed/Not run | Limitations |
|-----------|--------|----------------------|-------------|
| Computational | gadget X on n<=6 | passed | n<=6 only |
| Structural | class-preservation check | passed | — |
| Bibliographic | theorem citation | unchecked | source incomplete |
| Formal | lemma scaffold | not run | — |

Failure taxonomy:

- local gadget unsoundness
- local gadget incompleteness
- cross-gadget interference
- illegal move admitted
- legal move missing
- state correspondence broken
- equivalence overstated
- graph-class preservation broken
- planarity or orientation broken
- polynomial-size claim broken
- imported lemma or citation unsupported

Hard rules:

- separate construction, local behavior, completeness, soundness, noninterference, and size preservation
- never merge prose polishing with proof repair
- stabilize correctness first

---

## Template: Lean Formalization Team

When to use:

- formalizing a specific lemma that has already been proved on paper
- debugging a stuck Lean proof
- decomposing a large formal goal into smaller pieces

Mode: `research`
Profile: `math-heavy`
Interaction: `star`

Roles:

| # | Role | Reasoning | Task |
|---|------|-----------|------|
| 1 | Informal Planner | R4 | Decompose the lemma into minimal subclaims and proof strategy |
| 2 | Formalizer | R4 | Write a conservative compiling Lean scaffold with placeholders |
| 3 | Missing-Lemma Miner | R3 | List needed helper lemmas and likely library matches |
| 4 | Repair Agent | R3 | Classify blockers such as syntax, coercions, missing lemmas, or overly strong statements |
| 5 | Checker | R4 | Decide whether the goal is complete, fixable, or blocked |

Rounds:

Round 1:

- Informal Planner decomposes the lemma
- Formalizer drafts the scaffold
- Missing-Lemma Miner lists helper lemmas

Round 2:

- Repair Agent classifies blockers
- Checker evaluates which placeholders are closable and which indicate deeper issues

Codex adaptation:

- use `formal_skeleton_helper` when a scaffold is needed quickly
- use local Lean tooling through `functions.exec_command` if the environment supports it
- distinguish mathematical gaps from formalization friction

Final output:

- Lean file or scaffold status
- list of missing lemmas
- assessment of whether formalization reveals a gap in the paper proof
