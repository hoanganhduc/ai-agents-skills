"""Check reconfiguration properties of graph colorings.

Set environment variables:
  SAGE_PARAM_N: number of vertices (default: 5)
  SAGE_PARAM_K: number of colors (default: 3)
  SAGE_PARAM_GRAPH: graph type (default: "CycleGraph")
"""
import json, os, sys
from itertools import product

n = int(os.environ.get('SAGE_PARAM_N', '5'))
k = int(os.environ.get('SAGE_PARAM_K', '3'))
graph_type = os.environ.get('SAGE_PARAM_GRAPH', 'CycleGraph')

G = getattr(graphs, graph_type)(n)
chi = int(G.chromatic_number())

print(f"Graph: {graph_type}({n}), chi={chi}, checking {k}-colorings")

if k < chi:
    print(json.dumps({
        'graph': f'{graph_type}({n})',
        'k': k,
        'chromatic_number': int(chi),
        'message': f'No proper {k}-colorings exist (chi={chi})',
        'num_colorings': int(0),
    }, indent=2))
else:
    # Count proper k-colorings
    chrom_poly = G.chromatic_polynomial()
    num_colorings = int(chrom_poly(k))

    # Build reconfiguration graph (Kempe adjacency)
    # Two colorings are adjacent if they differ on exactly one vertex
    colorings = []
    vertices = list(G.vertices())
    edges_list = list(G.edges(labels=False))

    for coloring in product(range(k), repeat=n):
        proper = True
        for u, v in edges_list:
            ui = vertices.index(u)
            vi = vertices.index(v)
            if coloring[ui] == coloring[vi]:
                proper = False
                break
        if proper:
            colorings.append(coloring)

    # Build reconfig graph
    R = Graph()
    for i in range(len(colorings)):
        R.add_vertex(i)
    for i in range(len(colorings)):
        for j in range(i+1, len(colorings)):
            diff = sum(1 for a, b in zip(colorings[i], colorings[j]) if a != b)
            if diff == 1:
                R.add_edge(i, j)

    components = R.connected_components()

    print(json.dumps({
        'graph': f'{graph_type}({n})',
        'k': k,
        'chromatic_number': int(chi),
        'num_colorings': int(num_colorings),
        'reconfig_vertices': int(R.num_verts()),
        'reconfig_edges': int(R.num_edges()),
        'reconfig_components': int(len(components)),
        'reconfig_diameter': int(R.diameter()) if R.is_connected() else int(-1),
        'is_connected': bool(R.is_connected()),
    }, indent=2))
