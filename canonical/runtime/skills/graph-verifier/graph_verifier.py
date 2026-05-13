#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
from typing import Optional

try:
    import networkx as nx
except Exception as e:
    print(json.dumps({"ok": False, "error": f"networkx import failed: {e}"}, indent=2))
    raise SystemExit(1)


def load_payload(path_str: Optional[str]):
    if not path_str:
        return {}
    p = Path(path_str)
    if not p.exists():
        return {}
    text = p.read_text(encoding="utf-8").strip()
    return json.loads(text) if text else {}


def build_graph(payload):
    if "graph_data" in payload:
        return nx.node_link_graph(payload["graph_data"])
    if "edges" in payload:
        graph = nx.Graph()
        graph.add_edges_from(payload["edges"])
        if "nodes" in payload:
            graph.add_nodes_from(payload["nodes"])
        return graph
    if "adjacency" in payload:
        return nx.Graph(payload["adjacency"])
    return nx.petersen_graph()


def safe_diameter(graph):
    if graph.number_of_nodes() == 0:
        return 0
    if not nx.is_connected(graph) or graph.number_of_nodes() > 300:
        return None
    return nx.diameter(graph)


def safe_girth(graph):
    if graph.number_of_nodes() == 0 or graph.number_of_edges() == 0:
        return None
    try:
        return nx.girth(graph)
    except Exception:
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=None)
    args = parser.parse_args()
    payload = load_payload(args.input)
    claim = payload.get("claim", "")
    expected = payload.get("expected", {})

    try:
        graph = build_graph(payload)
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"invalid graph input: {e}"}, indent=2))
        return

    undirected = graph.to_undirected() if getattr(graph, "is_directed", lambda: False)() else graph
    planarity = None
    if undirected.number_of_nodes() <= 1000 and undirected.number_of_edges() <= 5000:
        try:
            planarity = nx.check_planarity(undirected, counterexample=False)[0]
        except Exception:
            planarity = None

    result = {
        "ok": True,
        "claim": claim,
        "n": undirected.number_of_nodes(),
        "m": undirected.number_of_edges(),
        "connected": nx.is_connected(undirected) if undirected.number_of_nodes() > 0 else True,
        "bipartite": nx.is_bipartite(undirected),
        "tree": nx.is_tree(undirected) if undirected.number_of_nodes() > 0 else True,
        "planar": planarity,
        "diameter": safe_diameter(undirected),
        "girth": safe_girth(undirected),
        "degree_sequence": sorted((degree for _, degree in undirected.degree()), reverse=True)[:50],
        "small_n_ok": undirected.number_of_nodes() <= 1000,
    }

    mismatches = {}
    for key, want in expected.items():
        if key in result and result[key] != want:
            mismatches[key] = {"expected": want, "actual": result[key]}
    result["matches_expected"] = not mismatches
    if mismatches:
        result["mismatches"] = mismatches

    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
