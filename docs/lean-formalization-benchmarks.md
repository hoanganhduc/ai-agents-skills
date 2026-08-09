# Lean formalization benchmarks

Reference for selecting held-out benchmark sets when testing Lean 4
formalization routines — in particular closed-book runs of the autonomous
research loop (ARL) formal lane. Compiled 2026-08 from primary sources
(GitHub repos and arXiv papers) during the T0–T4 closed-book benchmark
campaign; sizes and contamination judgments are as of that survey and decay
over time.

How to run a closed-book benchmark against the ARL loop:
`canonical/templates/sample-arl-headless-driver-with-formal/hermetic_benchmark_env.inc.sh`
(Preset A) and the lane matrix in
`canonical/runtime/skills/autonomous-research-loop-runtime/force-loop/OPERATOR_RUNBOOK.md`
("Banked launch presets (claude)").

## What each set tests

### Fixed-statement proving (miniF2F-style)

| Set | Size | Notes |
|-----|------|-------|
| [PutnamBench](https://github.com/trishullab/PutnamBench) | 672 Lean 4 theorems, growing yearly | Undergraduate competition (Putnam), multilingual (Lean 4, Isabelle, Coq/Rocq). **Strongest anti-leak governance of any set**: statements only, the community is explicitly asked never to publish formal proofs, leaderboard managed privately. Factored numerical answers support the harder "produce answer + proof" agentic mode. Residual leak: a few dozen solved proofs in prover papers' artifacts. |
| [FormalMATH](https://github.com/Sphere-AI-Lab/FormalMATH-Bench) | 5,560 verified statements (FormalMATH-Lite subset for cheap runs) | Olympiad through undergraduate, broad domains, statement-only, no reference proofs published. Large enough for statistically meaningful pass@k. Caveat: statements are autoformalized (residual semantic-fidelity risk); discriminates well (~16% best at release). |
| [FATE-M/H/X](https://github.com/frenzymath/FATE) | ~100 per tier ([arXiv:2511.02872](https://arxiv.org/abs/2511.02872)) | Abstract/commutative algebra at escalating difficulty: textbook (M), graduate (H), PhD-qualifying-and-beyond (X). Expert-formalized, partly beyond current Mathlib — near-zero contamination as of mid-2026. Best frontier-difficulty option beyond competition math. |
| [miniF2F-v2](https://arxiv.org/abs/2511.03108) | ~488 re-verified statements (v2s simplified / v2c competition-faithful) | The corrected miniF2F: v1 has >50% misaligned/flawed statements and is **the most contaminated benchmark in existence** (full proof dumps from DeepSeek, Goedel, Kimina, InternLM on GitHub/HF). v2 ships verified reference proofs, so keep the proofs directory out of the agent's reachable scope. Use only as a legacy comparison point. |
| [ProverBench](https://github.com/deepseek-ai/DeepSeek-Prover-V2) | 325 problems | 15 recent AIME 24/25 (low contamination at release, decaying) + 310 textbook problems. Static snapshot; fine as a quick agentic-loop smoke test. |
| [MathOlympiadBench](https://huggingface.co/datasets/Goedel-LM/MathOlympiadBench) | 360 human-verified olympiad problems | Sourced from Compfiles/IMOSLLean4 where complete public solutions exist — blocklist those repos in the sandbox. |
| [CombiBench](https://github.com/MoonshotAI/CombiBench/) | 100 combinatorics problems (36 IMO) | Proving **and** fill-in-the-blank answer construction (45% need answer-then-prove); Fine-Eval harness has with/without-solution ablations. Very hard (best ~7/100 at release). Mask the repo's answers file. |
| [FIMO](https://github.com/liuchengwucn/FIMO) | 149 IMO-shortlist statements | Lean 3 only, minimally maintained — effectively deprecated for Lean 4 work. |

### Repo/context-level (closest to an agentic formalization loop)

| Set | Size | Notes |
|-----|------|-------|
| [miniCTX](https://cmu-l3.github.io/minictx/) | 762 theorems | Theorems depending on new in-project definitions (PNT, PFR, recent Mathlib, HTPI, HepLean, SciLean) with tens-of-thousands-token context; refresh-from-post-cutoff-projects design is itself an anti-contamination mechanism. Ground-truth proofs are in the public source repos — sandbox network access, pin to provided context. Harness: cmu-l3/minictx-eval via Lean REPL. |
| [APE-Bench I](https://github.com/xinhjBrant/APE-Bench_I) | Thousands of file-level tasks ([arXiv:2504.19110](https://arxiv.org/abs/2504.19110)) | Proof **engineering**: realistic NL-instructed edits (feature addition, refactoring, bug fixing) mined from real Mathlib4 commits, verified by compiler + LLM judge. The closest thing to benchmarking a formalization *workflow*. Pin the agent to a pre-commit checkout; block Mathlib master. |

### Autoformalization (NL → formal statement)

| Set | Size | Notes |
|-----|------|-------|
| ProofNet# ([PAug/ProofNetSharp](https://huggingface.co/datasets/PAug/ProofNetSharp), from [arXiv:2406.07222](https://arxiv.org/abs/2406.07222)) | 371 undergraduate problems | Use this, not raw Lean 4 ports of [ProofNet](https://github.com/zhangir-azerbayev/ProofNet) (~32% of ported entries have formalization mistakes; original is unmaintained Lean 3). No formal proofs in the repo (low leak for proving); hide the reference formal column when evaluating autoformalization. |
| [RLM25 / RLMEval](https://github.com/augustepoiroux/RLMEval) | 619 pairs | Research-level, repo-grounded statement autoformalization from 6 real formalization projects — matches the ARL autoformalization lane (T0-style tasks). |

### Premise selection / retrieval

| Set | Size | Notes |
|-----|------|-------|
| [LeanDojo Benchmark 4](https://leandojo.org/) | 122,517 mathlib4 theorems, 259,580 tactics | Fine-grained premise annotations, `novel_premises` split, regenerable at any mathlib commit (good for post-cutoff freshness). Every ground-truth proof is public mathlib: treat results as retrieval/tactic-prediction metrics, **never** as contamination-safe end-to-end proving. |

### Non-Lean complement

[MathConstruct](https://arxiv.org/abs/2502.10197) (121 constructive problems,
programmatic Python verifiers, auto-perturbed variants): the perturbation
mechanism is a genuine anti-memorization control worth borrowing; the
Enumerate-Conjecture-Prove pipeline ([arXiv:2505.18492](https://arxiv.org/abs/2505.18492))
bridges it to Lean. Long tail with low current contamination:
IndiMathBench (312 Indian-olympiad Lean 4 theorems, [arXiv:2512.00997](https://arxiv.org/abs/2512.00997)),
CAM-Bench (computational/applied math), and
google-deepmind/formal-conjectures (uniquely leak-proof — no known proofs
exist — but success is not expected).

## Leak-risk map (for agents with repo/web access)

- **Solutions co-located with statements — sandbox network access and
  blocklist the source repos**: Compfiles, Lean Workbook, miniF2F-v2
  reference proofs, CombiBench answers file, APE-Bench ground-truth commits,
  miniCTX/LeanDojo source repositories.
- **Statements-only, low proof-leak**: PutnamBench (explicit
  no-public-proofs policy), FormalMATH, ProofNet#, ProverBench, FATE,
  formal-conjectures.
- **Contaminated regardless of runtime controls** (proof dumps ubiquitous in
  training data): miniF2F v1.

## Do not use as held-out evals

- **[Compfiles](https://github.com/dwrensha/compfiles)** (~232 problems,
  ~173 with complete solutions): solutions live in the same public repo by
  design and are in training corpora. Use as a statement source with proofs
  stripped, or as a grading oracle.
- **[Lean Workbook (+Plus)](https://github.com/InternLM/InternLM-Math)**
  (~140K pairs): a training/expert-iteration corpus in every recent prover's
  training data, with found proofs published beside statements. Usable as
  curriculum or as a source of *unproved* challenge statements only.
- **LeanDojo end-to-end proving**: public ground-truth proofs (see above).
- **miniF2F v1**: contaminated and ~50% flawed statements.

## Recommended stack for the ARL formal lane

| Lane | Sets |
|------|------|
| Proving under anti-leak controls | PutnamBench + FormalMATH(-Lite) + FATE |
| Agentic/repo dimension | miniCTX + APE-Bench I |
| Autoformalization | ProofNet# + RLM25 |
| Premise-selection components | LeanDojo Benchmark 4 |
| Legacy comparison only | miniF2F-v2 |

The 2026-08 T0–T4 campaign predates this survey's recommendations and used
miniF2F single statements (T1–T4) plus an informal-spec autoformalization
task (T0) under the Preset A hermetic recipe; future campaigns should draw
from the recommended stack above.
