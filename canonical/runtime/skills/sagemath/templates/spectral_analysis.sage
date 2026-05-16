"""Spectral analysis of a graph family.

Set environment variables:
  SAGE_PARAM_FAMILY: graph family (default: "CycleGraph")
  SAGE_PARAM_N_MIN: min parameter (default: 3)
  SAGE_PARAM_N_MAX: max parameter (default: 10)
"""
import json, os, sys

family = os.environ.get('SAGE_PARAM_FAMILY', 'CycleGraph')
n_min = int(os.environ.get('SAGE_PARAM_N_MIN', '3'))
n_max = int(os.environ.get('SAGE_PARAM_N_MAX', '10'))

results = []
for n in range(n_min, n_max + 1):
    G = getattr(graphs, family)(n)
    eigenvalues = sorted([float(e) for e in G.adjacency_matrix().eigenvalues()], reverse=True)
    lap_eigenvalues = sorted([float(e) for e in G.laplacian_matrix().eigenvalues()])
    results.append({
        'n': n,
        'num_vertices': int(G.num_verts()),
        'num_edges': int(G.num_edges()),
        'characteristic_polynomial': str(G.characteristic_polynomial()),
        'spectral_radius': float(max(abs(e) for e in eigenvalues)),
        'algebraic_connectivity': float(lap_eigenvalues[1]) if len(lap_eigenvalues) > 1 else 0,
        'eigenvalues': eigenvalues,
    })
    sys.stderr.write(f"\r{family}({n}): done")
    sys.stderr.flush()

sys.stderr.write("\n")
print(json.dumps({'family': family, 'range': [n_min, n_max], 'results': results}, indent=2))
