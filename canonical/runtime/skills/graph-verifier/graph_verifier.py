#!/usr/bin/env python3
import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Optional

try:
    import networkx as nx
except Exception as e:
    print(json.dumps({"ok": False, "error": f"networkx import failed: {e}"}, indent=2))
    raise SystemExit(1)


DEMO_INPUT_REF = "builtin:petersen_graph"
DEGREE_SEQUENCE_SHOWN = 50


class NoVerdict(Exception):
    """An input the verifier cannot check; the run ends without a verdict."""


class Skipped(Exception):
    """A property the verifier does not compute for this graph."""


def load_payload(path_str: Optional[str]):
    """Return the input object and a sha256 reference to the bytes it was read from."""
    if not path_str:
        raise NoVerdict("no input: pass --input FILE, or --demo to check the built-in Petersen graph")
    p = Path(path_str)
    if not p.is_file():
        raise NoVerdict(f"input file not found or not a regular file: {path_str}")
    data = p.read_bytes()
    text = data.decode("utf-8").strip()
    if not text:
        raise NoVerdict("invalid graph input: the input file is empty")
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise NoVerdict("invalid graph input: the input is not a JSON object")
    return payload, "sha256:" + hashlib.sha256(data).hexdigest()


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
    raise NoVerdict("invalid graph input: the input has none of graph_data, edges or adjacency")


def planarity(graph):
    if graph.number_of_nodes() > 1000 or graph.number_of_edges() > 5000:
        raise Skipped("more than 1000 vertices or 5000 edges")
    return nx.check_planarity(graph, counterexample=False)[0]


def require_vertices(graph):
    """Return ``graph`` when it has a vertex; connectivity, treeness and diameter are undefined otherwise."""
    # Decided here rather than left to networkx's exceptions for the null
    # graph, so the status does not depend on the networkx release.
    if graph.number_of_nodes() == 0:
        raise Skipped("undefined for a graph with no vertices")
    return graph


def safe_diameter(graph):
    require_vertices(graph)
    if graph.number_of_nodes() > 300:
        raise Skipped("more than 300 vertices")
    if not nx.is_connected(graph):
        raise Skipped("the graph is disconnected")
    return nx.diameter(graph)


def safe_girth(graph):
    if graph.number_of_nodes() == 0 or graph.number_of_edges() == 0:
        raise Skipped("the graph has no edges")
    girth = nx.girth(graph)
    # networkx gives an acyclic graph girth inf, which json.dumps would print
    # as Infinity, a token JSON does not have; null stands for it instead.
    return None if math.isinf(girth) else girth


def measure(compute):
    """Return (value, status, reason) for one property; reason is None when checked."""
    try:
        return compute(), "checked", None
    except Skipped as e:
        return None, "skipped", str(e)
    except Exception as e:
        return None, "error", f"{type(e).__name__}: {e}"[:200]


def no_verdict(error):
    print(json.dumps({"ok": False, "error": error, "verdict": "no_verdict"}, indent=2))


def computation_evidence(*, input_ref, domain, graph, expected, mismatches, unchecked, measured):
    """The record a caller can attach to a claim: what was checked, how, and with what result."""
    exhaustive = bool(expected) and not unchecked
    if mismatches:
        result_status = "failed"
    elif exhaustive:
        result_status = "passed"
    else:
        result_status = "partial"
    if not expected:
        outcome = "no expected values given; graph properties are reported without a verdict"
    elif not mismatches and not unchecked:
        outcome = "every expected value was checked and matches"
    else:
        parts = []
        if mismatches:
            parts.append("expected values contradicted: " + json.dumps(sorted(mismatches)))
        if unchecked:
            parts.append("expected values not checked: " + json.dumps(sorted(unchecked)))
        outcome = "; ".join(parts)
    block = {
        "tool_name": "graph-verifier",
        "tool_version": f"networkx {nx.__version__}",
        "input_encoding_ref": input_ref,
        "checked_domain": f"{domain} ({graph.number_of_nodes()} vertices, {graph.number_of_edges()} edges)",
        "graph_model_assumptions": (
            f"read as an undirected networkx {type(graph).__name__} with the vertices, edges and "
            "self-loops networkx builds from the input; a directed input is made undirected"
        ),
        "resource_bounds": (
            "planarity only up to 1000 vertices and 5000 edges; diameter only for connected graphs "
            "up to 300 vertices; no time or memory limit inside the verifier"
        ),
        "enumeration_method": "none: each property is computed directly on the one input graph with networkx",
        "timeout_status": "completed",
        "result_status": result_status,
        "coverage_status": "exhaustive" if exhaustive else "partial",
        "evaluated_predicate": json.dumps(expected, sort_keys=True),
        "outcome": outcome,
        "limitations": ["the claim text is not evaluated"] + [
            f"{key} {status}: {reason}" for key, (_, status, reason) in measured.items() if status != "checked"
        ],
    }
    if exhaustive:
        block["exhaustiveness_argument"] = (
            "every expected key names a property the verifier computed exactly on the whole input "
            "graph, and every expected value was compared; nothing was sampled or bounded"
        )
    return block


def main():
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--input", default=None)
    source.add_argument("--demo", action="store_true", help="check the built-in Petersen graph")
    args = parser.parse_args()
    try:
        # Reading the file belongs inside this guard: a hand-written input is as
        # likely to be malformed JSON as it is to describe a graph wrongly, and
        # the workflow tells the caller to read the result from stdout, which a
        # traceback leaves empty.
        if args.demo:
            payload, input_ref, graph = {}, DEMO_INPUT_REF, nx.petersen_graph()
        else:
            payload, input_ref = load_payload(args.input)
            graph = build_graph(payload)
        expected = payload.get("expected")
        if expected is None:
            expected = {}
        elif not isinstance(expected, dict):
            raise NoVerdict("invalid expected values: expected must be a JSON object or null")
    except NoVerdict as e:
        no_verdict(str(e))
        return
    except Exception as e:
        no_verdict(f"invalid graph input: {e}")
        return
    claim = payload.get("claim", "")

    undirected = graph.to_undirected() if getattr(graph, "is_directed", lambda: False)() else graph
    n = undirected.number_of_nodes()
    computations = {
        "n": undirected.number_of_nodes,
        "m": undirected.number_of_edges,
        "connected": lambda: nx.is_connected(require_vertices(undirected)),
        "bipartite": lambda: nx.is_bipartite(undirected),
        "tree": lambda: nx.is_tree(require_vertices(undirected)),
        "planar": lambda: planarity(undirected),
        "diameter": lambda: safe_diameter(undirected),
        "girth": lambda: safe_girth(undirected),
        "degree_sequence": lambda: sorted((degree for _, degree in undirected.degree()), reverse=True),
        "small_n_ok": lambda: n <= 1000,
    }
    measured = {key: measure(compute) for key, compute in computations.items()}
    values = {key: value for key, (value, _, _) in measured.items()}
    degrees = values["degree_sequence"]
    result = {
        "ok": True,
        "claim": claim,
        "input_source": "builtin_demo" if args.demo else "file",
        **values,
        "degree_sequence": None if degrees is None else degrees[:DEGREE_SEQUENCE_SHOWN],
        "degree_sequence_length": None if degrees is None else len(degrees),
        "degree_sequence_truncated": None if degrees is None else len(degrees) > DEGREE_SEQUENCE_SHOWN,
        "property_status": {key: status for key, (_, status, _) in measured.items()},
    }

    # Only a property computed on this graph can match or contradict an expected
    # value. Any other expected key, unknown or skipped or failed, is unchecked.
    mismatches = {}
    unchecked = []
    for key, want in expected.items():
        if result["property_status"].get(key) != "checked":
            unchecked.append(key)
        elif values[key] != want:
            mismatches[key] = {"expected": want, "actual": result[key]}
    result["unchecked_expected_keys"] = sorted(unchecked)
    result["matches_expected"] = not mismatches and not unchecked
    if mismatches:
        result["mismatches"] = mismatches
    result["claim_evaluated"] = False
    result["computation_evidence"] = computation_evidence(
        input_ref=input_ref,
        domain="the built-in Petersen graph" if args.demo else "the one graph in the input file",
        graph=undirected,
        expected=expected,
        mismatches=mismatches,
        unchecked=unchecked,
        measured=measured,
    )

    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
