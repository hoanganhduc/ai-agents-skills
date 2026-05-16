"""Search for counterexamples to a graph conjecture on small graphs.

Set environment variables:
  SAGE_PARAM_N_MAX: max vertices to check (default: 7)
  SAGE_PARAM_CONJECTURE: Python expression using G (default: "G.chromatic_number() <= G.clique_number() + 1")
"""
import json, os, sys

n_max = int(os.environ.get('SAGE_PARAM_N_MAX', '7'))
conjecture = os.environ.get('SAGE_PARAM_CONJECTURE', 'G.chromatic_number() <= G.clique_number() + 1')

counterexamples = []
total_checked = 0

for n in range(1, n_max + 1):
    for G in graphs(n):
        total_checked += 1
        try:
            if not eval(conjecture):
                counterexamples.append({
                    'n': n,
                    'edges': list(G.edges(labels=False)),
                    'chromatic_number': int(G.chromatic_number()),
                    'clique_number': int(G.clique_number()),
                })
        except Exception as e:
            pass
    sys.stderr.write(f"\rChecked n={n}: {total_checked} graphs, {len(counterexamples)} counterexamples")
    sys.stderr.flush()

sys.stderr.write("\n")

result = {
    'conjecture': conjecture,
    'n_max': n_max,
    'total_checked': total_checked,
    'counterexamples_found': len(counterexamples),
    'counterexamples': counterexamples[:20],
}
print(json.dumps(result, indent=2))
