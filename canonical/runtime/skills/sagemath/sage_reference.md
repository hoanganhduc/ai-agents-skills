# SageMath Quick Reference (v10.8)

## Graph methods (70 relevant)

```
G.adjacency_matrix()                G.laplacian_matrix()
G.automorphism_group()              G.matching()
G.center()                          G.matching_polynomial()
G.characteristic_polynomial()       G.minimal_dominating_sets()
G.chromatic_index()                 G.multicommodity_flow()
G.chromatic_number()                G.nowhere_zero_flow()
G.chromatic_polynomial()            G.odd_girth()
G.chromatic_quasisymmetric_function()  G.perfect_matchings()
G.chromatic_symmetric_function()    G.periphery()
G.clique_maximum()                  G.planar_dual()
G.clique_number()                   G.radius()
G.clique_polynomial()               G.seidel_adjacency_matrix()
G.coloring(algorithm="DLX")         G.spectral_radius()
G.diameter()                        G.treewidth()
G.dominating_set()                  G.tutte_polynomial()
G.eccentricity()                    G.tutte_symmetric_function()
G.edge_connectivity()               G.vertex_connectivity()
G.flow()                            G.vertex_cover()
G.fractional_chromatic_index()      G.weighted_adjacency_matrix()
G.fractional_chromatic_number()
G.fractional_clique_number()        G.has_perfect_matching()
G.genus()                           G.independent_set()
G.girth()                           G.is_circular_planar()
G.greedy_dominating_set()           G.is_hamiltonian()
G.hamiltonian_cycle()               G.is_isomorphic(H)
G.hamiltonian_path()                G.is_planar()
G.independent_set()                 G.is_projective_planar()
```

## Graph generators (280 available, common ones)

```
graphs.CompleteGraph(n)              graphs.PetersenGraph()
graphs.CycleGraph(n)                graphs.KneserGraph(n,k)
graphs.PathGraph(n)                 graphs.JohnsonGraph(n,k)
graphs.Grid2dGraph(m,n)             graphs.GeneralizedPetersenGraph(n,k)
graphs.CompleteBipartiteGraph(m,n)  graphs.CirculantGraph(n, [1,2])
graphs.CompleteMultipartiteGraph(L)  graphs.RandomGNP(n, p)
graphs.BalancedTree(r,h)            graphs.ChvatalGraph()
graphs.CubeGraph(n)                 graphs.CoxeterGraph()
graphs.StarGraph(n)                 graphs.DesarguesGraph()
```

## Verified examples

### Petersen graph
- Chromatic polynomial: `x^10 - 15*x^9 + 105*x^8 - 455*x^7 + 1353*x^6 - 2861*x^5 + 4275*x^4 - 4305*x^3 + 2606*x^2 - 704*x`
- Chromatic number: 3
- Automorphism group: S5 (order 120)
- Treewidth: 4, Girth: 5, Diameter: 2
- Independence number: 4, Not planar
- Isomorphic to KneserGraph(5,2): True

### Cycle C5
- Chromatic polynomial: `x^5 - 5*x^4 + 10*x^3 - 10*x^2 + 4*x`
- Tutte polynomial: `x^4 + x^3 + x^2 + x + y`
- Characteristic polynomial: `x^5 - 5*x^3 + 5*x - 2`

### Complete K4
- Chromatic polynomial: `x^4 - 6*x^3 + 11*x^2 - 6*x`
- Tutte polynomial: `x^3 + y^3 + 3*x^2 + 4*x*y + 3*y^2 + 2*x + 2*y`

### Finite field GF(7)
- det([[1,2],[3,4]]) = 5
