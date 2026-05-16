"""Enumerate chromatic polynomials for all graphs on n vertices."""
import json, os, sys

n = int(os.environ.get('SAGE_PARAM_N', '5'))
output_file = os.environ.get('SAGE_OUTPUT', '')

from sage.parallel.decorate import parallel

@parallel(ncpus=3)
def compute_chromatic(G_edges, n_verts):
    G = Graph(n_verts)
    G.add_edges(G_edges)
    return {
        'edges': list(G_edges),
        'chromatic_polynomial': str(G.chromatic_polynomial()),
        'chromatic_number': int(G.chromatic_number()),
    }

all_graphs = list(graphs(n))
inputs = [(list(G.edges(labels=False)), n) for G in all_graphs]

results = []
for inp, out in compute_chromatic(inputs):
    results.append(out)
    sys.stderr.write(f"\rProcessed {len(results)}/{len(all_graphs)} graphs")
    sys.stderr.flush()

sys.stderr.write("\n")

summary = {
    'n': n,
    'total_graphs': len(all_graphs),
    'results': sorted(results, key=lambda r: r['chromatic_number']),
}

if output_file:
    with open(output_file, 'w') as f:
        json.dump(summary, f, indent=2)
    print(f"Saved to {output_file}")
else:
    print(json.dumps(summary, indent=2))
