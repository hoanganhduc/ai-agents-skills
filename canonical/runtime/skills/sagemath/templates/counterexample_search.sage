"""Search for counterexamples to a graph conjecture on small graphs.

Set environment variables:
  SAGE_PARAM_N_MAX: max vertices to check (default: 7)
  SAGE_PARAM_CONJECTURE: Python expression using G (default: "G.chromatic_number() <= G.clique_number() + 1")

The search re-enumerates every graph graphs(n) yields for n = 1..n_max, one per
isomorphism class; it does not check a given witness. The printed JSON carries a
computation_evidence block in the form deep-research computation evidence uses.
"""
import hashlib, json, os, sys

n_max = int(os.environ.get('SAGE_PARAM_N_MAX', '7'))
conjecture = os.environ.get('SAGE_PARAM_CONJECTURE', 'G.chromatic_number() <= G.clique_number() + 1')

LISTED_MAX = 20   # counterexamples listed; every one is counted

counterexamples = []
counterexamples_found = 0
total_enumerated = 0
total_evaluated = 0
evaluation_errors = 0
error_samples = []

for n in range(1, n_max + 1):
    for G in graphs(n):
        total_enumerated += 1
        try:
            holds = bool(eval(conjecture))
        except Exception as e:
            # Neither a pass nor a counterexample: the graph was not evaluated.
            evaluation_errors += 1
            error_samples.append(f"n={n}: {type(e).__name__}: {e}")
            continue
        total_evaluated += 1
        if holds:
            continue
        counterexamples_found += 1
        if len(counterexamples) < LISTED_MAX:
            witness = {
                'n': n,
                'edges': list(G.edges(labels=False)),
                'chromatic_number': None,
                'clique_number': None,
            }
            # A witness stays listed when its invariants cannot be computed.
            try:
                witness['chromatic_number'] = int(G.chromatic_number())
                witness['clique_number'] = int(G.clique_number())
            except Exception as e:
                witness['witness_detail_error'] = f"{type(e).__name__}: {e}"
            counterexamples.append(witness)
    sys.stderr.write(
        f"\rn={n}: {total_enumerated} enumerated, {total_evaluated} evaluated, "
        f"{evaluation_errors} evaluation errors, {counterexamples_found} counterexamples"
    )
    sys.stderr.flush()

sys.stderr.write("\n")

# Nothing enumerated (n_max below 1) is not a pass.
complete = evaluation_errors == 0 and total_enumerated > 0
if counterexamples_found > 0:
    result_status = 'failed'
    outcome = f"conjecture refuted: false on {counterexamples_found} of {total_evaluated} evaluated graphs on 1..{n_max} vertices"
elif total_enumerated == 0:
    result_status = 'partial'
    outcome = f"nothing checked: no graph was enumerated for n = 1..{n_max}"
else:
    result_status = 'passed' if complete else 'partial'
    outcome = f"no counterexample among {total_evaluated} evaluated graphs on 1..{n_max} vertices"
if evaluation_errors > 0:
    outcome += f"; evaluating the conjecture raised on {evaluation_errors} of {total_enumerated} graphs"

limitations = [
    f"bounded: only graphs on 1..{n_max} vertices were checked; nothing is shown for larger graphs",
    "each isomorphism class is checked through one representative, so listed counterexamples carry "
    "Sage's vertex labels and a conjecture that depends on labels is checked on one labelling only",
    "the conjecture is evaluated by Python eval without Sage's preparser: its literals are Python numbers "
    "and ^ means XOR, not a power",
]
if counterexamples_found > LISTED_MAX:
    limitations.append(f"only the first {LISTED_MAX} of {counterexamples_found} counterexamples are listed")
if evaluation_errors > 0:
    limitations.append(
        f"{evaluation_errors} of {total_enumerated} graphs raised an error when the conjecture was evaluated; "
        "they are neither confirmed nor refuted"
    )
if any('witness_detail_error' in witness for witness in counterexamples):
    limitations.append("some listed counterexamples lack chromatic and clique numbers because computing them raised")

evidence = {
    'tool_name': 'sagemath counterexample_search.sage',
    'tool_version': str(version()),
    'input_encoding_ref': 'sha256:' + hashlib.sha256(conjecture.encode('utf-8')).hexdigest(),
    'checked_domain': f"simple graphs on 1..{n_max} vertices, one per isomorphism class",
    'graph_model_assumptions': (
        "finite simple undirected graphs (no loops, multiple edges or edge labels), "
        "one representative per isomorphism class as Sage graphs(n) yields them"
    ),
    'resource_bounds': (
        f"n_max={n_max}; at most {LISTED_MAX} counterexamples listed, all counted; wall time bounded only by "
        "the caller, by default the run_sage wrapper's --timeout of 300 s, and a run stopped that way prints no JSON"
    ),
    'enumeration_method': 'Sage graphs(n)',
    'timeout_status': 'completed',
    'result_status': result_status,
    'coverage_status': 'bounded_complete' if complete else 'partial',
    'evaluated_predicate': conjecture,
    'outcome': outcome,
    'limitations': limitations,
}
if complete:
    evidence['exhaustiveness_argument'] = (
        f"graphs(n) yields every simple graph on n vertices up to isomorphism, and the conjecture was "
        f"evaluated without error on all {total_evaluated} graphs it yielded for n = 1..{n_max}"
    )

# Sage's preparser turns the integer literals above into Integer objects, which
# json.dumps rejects, so every count is printed through int().
result = {
    'conjecture': conjecture,
    'n_max': n_max,
    'total_enumerated': int(total_enumerated),
    'total_evaluated': int(total_evaluated),
    'evaluation_errors': int(evaluation_errors),
    'evaluation_error_samples': error_samples,
    # Deprecated alias of total_evaluated, kept for one release.
    'total_checked': int(total_evaluated),
    'counterexamples_found': int(counterexamples_found),
    'counterexamples_truncated': bool(counterexamples_found > LISTED_MAX),
    'counterexamples': counterexamples,
    'computation_evidence': evidence,
}
print(json.dumps(result, indent=2))
