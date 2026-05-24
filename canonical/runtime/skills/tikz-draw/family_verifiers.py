#!/usr/bin/env python3
from __future__ import annotations

from collections import Counter, defaultdict
import re
from typing import Any

from shapely.geometry import LineString, Point, box  # type: ignore


SUPPORTED_SEMANTIC_FAMILIES = ("flowchart", "dag", "tree", "commutative", "graph")

EDGE_ENDPOINT_TOLERANCE_PT = 12.0
EDGE_LABEL_TOLERANCE_PT = 10.0
CONTAINMENT_TOLERANCE_PT = 0.5
COMMUTATIVE_NODE_COUNT = 4
COMMUTATIVE_ARROWHEAD_TOLERANCE_PT = 12.0
COMMUTATIVE_SLOT_ORDER = ("a", "b", "c", "d")
GRAPH_NODE_MATCH_TOLERANCE = 0.28


def bbox_tuple(bbox: dict[str, Any]) -> tuple[float, float, float, float]:
    return (
        float(bbox["x0"]),
        float(bbox["y0"]),
        float(bbox["x1"]),
        float(bbox["y1"]),
    )


def bbox_union(boxes: list[dict[str, Any]]) -> dict[str, float]:
    x0 = min(float(item["x0"]) for item in boxes)
    y0 = min(float(item["y0"]) for item in boxes)
    x1 = max(float(item["x1"]) for item in boxes)
    y1 = max(float(item["y1"]) for item in boxes)
    return {
        "x0": x0,
        "y0": y0,
        "x1": x1,
        "y1": y1,
        "width": x1 - x0,
        "height": y1 - y0,
    }


def bbox_center(bbox: dict[str, Any]) -> tuple[float, float]:
    return ((float(bbox["x0"]) + float(bbox["x1"])) / 2.0, (float(bbox["y0"]) + float(bbox["y1"])) / 2.0)


def point_distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def point_pair_payload(args: list[dict[str, Any]]) -> tuple[tuple[float, float], tuple[float, float]]:
    start_payload, end_payload = args
    return (
        (float(start_payload["x"]), float(start_payload["y"])),
        (float(end_payload["x"]), float(end_payload["y"])),
    )


def normalize_label(value: str | None) -> str | None:
    if value is None:
        return None
    raw = str(value).strip()
    texish = any(token in raw for token in ("$", "_", "^", "\\"))
    normalized = raw.strip("$")
    compact_math = normalized.replace("{", "").replace("}", "").replace(" ", "")
    sub_sup = re.fullmatch(r"([A-Za-z]+)_([A-Za-z0-9,]+)\^([A-Za-z0-9,]+)", compact_math)
    if sub_sup:
        return "".join((sub_sup.group(1), sub_sup.group(3), sub_sup.group(2)))
    sup_sub = re.fullmatch(r"([A-Za-z]+)\^([A-Za-z0-9,]+)_([A-Za-z0-9,]+)", compact_math)
    if sup_sub:
        return "".join((sup_sub.group(1), sup_sub.group(2), sup_sub.group(3)))
    normalized = re.sub(r"_\{([^}]*)\}", r" \1", normalized)
    normalized = re.sub(r"_([A-Za-z0-9,]+)", r" \1", normalized)
    normalized = re.sub(r"\^\{([^}]*)\}", r"\1", normalized)
    normalized = re.sub(r"\^([A-Za-z0-9,]+)", r"\1", normalized)
    normalized = normalized.replace("{", "").replace("}", "")
    normalized = re.sub(r"\\[A-Za-z]+", "", normalized)
    normalized = " ".join(normalized.split()).strip()
    tokens = normalized.split()
    if texish or (
        1 < len(tokens) <= 3
        and all(re.fullmatch(r"[A-Za-z0-9,]+", token) and len(token) <= 4 for token in tokens)
        and any(len(token) > 1 for token in tokens)
    ):
        normalized = normalized.replace(" ", "")
    return normalized or None


def words_bounds(words: list[dict[str, Any]]) -> dict[str, float]:
    return bbox_union([item["bbox"] for item in words])


def word_line_clusters(words: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for word in words:
        grouped[int(word["line"])].append(word)
    clusters: list[list[dict[str, Any]]] = []
    for _line, line_words in sorted(grouped.items()):
        ordered = sorted(line_words, key=lambda item: float(item["bbox"]["x0"]))
        current: list[dict[str, Any]] = []
        current_bounds: dict[str, float] | None = None
        for word in ordered:
            if current and current_bounds is not None:
                gap = float(word["bbox"]["x0"]) - float(current_bounds["x1"])
                if gap > 9.0:
                    clusters.append(current)
                    current = []
                    current_bounds = None
            current.append(word)
            current_bounds = words_bounds(current)
        if current:
            clusters.append(current)
    return clusters


def clusters_are_math_fragments(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> bool:
    left_bounds = words_bounds(left)
    right_bounds = words_bounds(right)
    combined = [*left, *right]
    combined_bounds = words_bounds(combined)
    if float(combined_bounds["width"]) > 45.0 or float(combined_bounds["height"]) > 24.0:
        return False
    x_overlap_or_near = min(float(left_bounds["x1"]), float(right_bounds["x1"])) - max(
        float(left_bounds["x0"]), float(right_bounds["x0"])
    ) >= -3.0
    if not x_overlap_or_near:
        return False
    compact = "".join(
        str(word.get("text", ""))
        for word in sorted(combined, key=lambda item: (int(item["line"]), int(item["word"])))
    ).strip()
    return bool(re.fullmatch(r"[A-Za-z0-9_{}^=,+\-()\\| ]{1,28}", compact))


def visual_label_clusters(words: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    clusters = word_line_clusters(words)
    changed = True
    while changed:
        changed = False
        merged_clusters: list[list[dict[str, Any]]] = []
        used: set[int] = set()
        for left_index, left in enumerate(clusters):
            if left_index in used:
                continue
            merged = list(left)
            used.add(left_index)
            for right_index, right in enumerate(clusters[left_index + 1 :], start=left_index + 1):
                if right_index in used:
                    continue
                if clusters_are_math_fragments(merged, right):
                    merged.extend(right)
                    used.add(right_index)
                    changed = True
            merged_clusters.append(merged)
        clusters = merged_clusters
    return clusters


def merge_words_into_lines(page: dict[str, Any]) -> list[dict[str, Any]]:
    by_block: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for word in page.get("words", []):
        by_block[int(word["block"])].append(word)

    lines: list[dict[str, Any]] = []
    for block, block_words in sorted(by_block.items()):
        for cluster_index, words in enumerate(visual_label_clusters(block_words)):
            words_sorted = sorted(words, key=lambda item: (int(item["line"]), int(item["word"])))
            text = " ".join(str(item["text"]) for item in words_sorted).strip()
            line_numbers = sorted({int(item["line"]) for item in words_sorted})
            lines.append(
                {
                    "text": text,
                    "bbox": bbox_union([item["bbox"] for item in words_sorted]),
                    "block": block,
                    "line": line_numbers[0] if len(line_numbers) == 1 else -1,
                    "word_count": len(words_sorted),
                    "cluster": cluster_index,
                }
            )
    return lines


def classify_shape_kind(drawing: dict[str, Any]) -> str:
    dashes = drawing.get("dashes")
    if dashes not in (None, "[] 0", []):
        return "groupbox"
    ops = [item["op"] for item in drawing.get("items", [])]
    if ops == ["l", "l", "l", "l"]:
        return "diamond"
    if ops == ["c", "c", "c", "c"]:
        return "circle"
    return "box"


def candidate_shapes(page: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for index, drawing in enumerate(page.get("drawings", [])):
        ops = [item["op"] for item in drawing.get("items", [])]
        rect = drawing.get("rect")
        if not rect:
            continue
        if ops == ["l"]:
            continue
        shape_like = ops == ["l", "l", "l", "l"] or (len(ops) >= 4 and set(ops).issubset({"l", "c"}))
        if not shape_like:
            continue
        if float(rect["width"]) < 8.0 or float(rect["height"]) < 8.0:
            continue
        candidates.append(
            {
                "index": index,
                "kind": classify_shape_kind(drawing),
                "bbox": rect,
                "geom": box(*bbox_tuple(rect)),
                "drawing": drawing,
            }
        )
    return candidates


def normalize_point_map(points: dict[str, tuple[float, float]], *, flip_y: bool = False) -> dict[str, tuple[float, float]]:
    xs = [value[0] for value in points.values()]
    ys = [value[1] for value in points.values()]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    span_x = max(max_x - min_x, 1e-9)
    span_y = max(max_y - min_y, 1e-9)
    normalized: dict[str, tuple[float, float]] = {}
    for key, (x, y) in points.items():
        x_norm = (x - min_x) / span_x
        y_norm = (y - min_y) / span_y
        if flip_y:
            y_norm = 1.0 - y_norm
        normalized[key] = (x_norm, y_norm)
    return normalized


def greedy_point_match(
    expected: dict[str, tuple[float, float]],
    actual: dict[str, tuple[float, float]],
) -> tuple[dict[str, str], dict[str, float], set[str], set[str]]:
    remaining_expected = set(expected)
    remaining_actual = set(actual)
    mapping: dict[str, str] = {}
    distances: dict[str, float] = {}
    while remaining_expected and remaining_actual:
        best_actual = None
        best_expected = None
        best_distance = None
        for actual_id in remaining_actual:
            for expected_id in remaining_expected:
                distance = point_distance(actual[actual_id], expected[expected_id])
                if best_distance is None or distance < best_distance:
                    best_actual = actual_id
                    best_expected = expected_id
                    best_distance = distance
        assert best_actual is not None and best_expected is not None and best_distance is not None
        mapping[best_actual] = best_expected
        distances[best_actual] = best_distance
        remaining_actual.remove(best_actual)
        remaining_expected.remove(best_expected)
    return mapping, distances, remaining_expected, remaining_actual


def recover_nodes(page: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    lines = merge_words_into_lines(page)
    shapes = candidate_shapes(page)
    nodes: list[dict[str, Any]] = []
    free_lines: list[dict[str, Any]] = []
    for line in lines:
        line_geom = box(*bbox_tuple(line["bbox"]))
        containing: list[dict[str, Any]] = []
        for shape in shapes:
            if shape["geom"].buffer(CONTAINMENT_TOLERANCE_PT).contains(line_geom):
                containing.append(shape)
        non_group = [shape for shape in containing if shape["kind"] != "groupbox"]
        if not non_group:
            free_lines.append(line)
            continue
        chosen = min(
            non_group,
            key=lambda item: (
                float(item["bbox"]["width"]) * float(item["bbox"]["height"]),
                item["index"],
            ),
        )
        nodes.append(
            {
                "label": normalize_label(line["text"]),
                "text_bbox": line["bbox"],
                "shape_bbox": chosen["bbox"],
                "shape_kind": chosen["kind"],
                "shape_index": chosen["index"],
                "center": bbox_center(chosen["bbox"]),
            }
        )
    return nodes, free_lines, shapes


def nearest_node(point_payload: dict[str, Any], nodes: list[dict[str, Any]]) -> tuple[dict[str, Any], float] | None:
    point = Point(float(point_payload["x"]), float(point_payload["y"]))
    best: tuple[dict[str, Any], float] | None = None
    for node in nodes:
        geom = box(*bbox_tuple(node["shape_bbox"]))
        distance = point.distance(geom)
        if best is None or distance < best[1]:
            best = (node, distance)
    return best


def assign_labels_to_edges(edges: list[dict[str, Any]], free_lines: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    used_lines: set[int] = set()
    for line_index, line in enumerate(free_lines):
        center = Point(*bbox_center(line["bbox"]))
        candidates: list[tuple[int, float, float]] = []
        for edge_index, edge in enumerate(edges):
            distance = edge["geom"].distance(center)
            tolerance = float(edge.get("label_tolerance_pt", EDGE_LABEL_TOLERANCE_PT))
            if distance > tolerance:
                continue
            candidates.append((edge_index, distance, edge["geom"].length))
        if not candidates:
            continue
        best_distance = min(item[1] for item in candidates)
        near = [item for item in candidates if item[1] <= best_distance + 2.0]
        edge_index, _distance, _length = max(near, key=lambda item: (item[2], -item[1]))
        if edges[edge_index].get("label") is None:
            edges[edge_index]["label"] = normalize_label(line["text"])
            used_lines.add(line_index)
    remaining_lines = [line for index, line in enumerate(free_lines) if index not in used_lines]
    return edges, remaining_lines


def recover_edges(page: dict[str, Any], nodes: list[dict[str, Any]], free_lines: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    edges: list[dict[str, Any]] = []
    for drawing in page.get("drawings", []):
        ops = [item["op"] for item in drawing.get("items", [])]
        if ops == ["l"]:
            args = drawing["items"][0].get("args", [])
            if len(args) != 2:
                continue
            edge_points = args
        elif ops == ["c"]:
            args = drawing["items"][0].get("args", [])
            if len(args) != 4:
                continue
            edge_points = args
        else:
            continue
        start_payload, end_payload = edge_points[0], edge_points[-1]
        nearest_start = nearest_node(start_payload, nodes)
        nearest_end = nearest_node(end_payload, nodes)
        if nearest_start is None or nearest_end is None:
            continue
        start_node, start_distance = nearest_start
        end_node, end_distance = nearest_end
        if start_distance > EDGE_ENDPOINT_TOLERANCE_PT or end_distance > EDGE_ENDPOINT_TOLERANCE_PT:
            continue
        if start_node["label"] == end_node["label"]:
            continue
        edge_geom = LineString(
            [
                (float(point["x"]), float(point["y"]))
                for point in edge_points
            ]
        )
        edge = {
            "from_label": start_node["label"],
            "to_label": end_node["label"],
            "label": None,
            "geom": edge_geom,
            "drawing_rect": drawing["rect"],
        }
        if ops == ["c"]:
            edge["label_tolerance_pt"] = 30.0
        edges.append(edge)
    edges, remaining_lines = assign_labels_to_edges(edges, free_lines)
    for edge in edges:
        edge.pop("geom", None)
    return edges, remaining_lines


def recover_commutative_nodes(page: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    lines = merge_words_into_lines(page)
    ranked = sorted(
        lines,
        key=lambda item: (
            float(item["bbox"]["height"]),
            float(item["bbox"]["width"]),
            -bbox_center(item["bbox"])[1],
        ),
        reverse=True,
    )
    node_lines = ranked[:COMMUTATIVE_NODE_COUNT]
    if len(node_lines) < COMMUTATIVE_NODE_COUNT:
        return {}, lines

    top_rows = sorted(node_lines, key=lambda item: (bbox_center(item["bbox"])[1], bbox_center(item["bbox"])[0]))
    top = sorted(top_rows[:2], key=lambda item: bbox_center(item["bbox"])[0])
    bottom = sorted(top_rows[2:4], key=lambda item: bbox_center(item["bbox"])[0])
    slot_map = {
        "a": top[0],
        "b": top[1],
        "c": bottom[0],
        "d": bottom[1],
    }
    used = {id(item) for item in node_lines}
    free_lines = [line for line in lines if id(line) not in used]
    nodes = {
        slot: {
            "slot": slot,
            "label": normalize_label(line["text"]),
            "text_bbox": line["bbox"],
            "center": bbox_center(line["bbox"]),
        }
        for slot, line in slot_map.items()
    }
    return nodes, free_lines


def commutative_pair_midpoint(nodes: dict[str, dict[str, Any]], pair: tuple[str, str]) -> tuple[float, float]:
    first = nodes[pair[0]]["center"]
    second = nodes[pair[1]]["center"]
    return ((first[0] + second[0]) / 2.0, (first[1] + second[1]) / 2.0)


def recover_commutative_edges(
    page: dict[str, Any],
    nodes: dict[str, dict[str, Any]],
    free_lines: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if any(slot not in nodes for slot in COMMUTATIVE_SLOT_ORDER):
        return [], free_lines

    line_drawings: list[dict[str, Any]] = []
    arrowheads: list[dict[str, Any]] = []
    for drawing in page.get("drawings", []):
        ops = [item["op"] for item in drawing.get("items", [])]
        if ops == ["l"]:
            args = drawing["items"][0].get("args", [])
            if len(args) == 2:
                start, end = point_pair_payload(args)
                orientation = "horizontal" if abs(start[0] - end[0]) >= abs(start[1] - end[1]) else "vertical"
                line_drawings.append(
                    {
                        "drawing": drawing,
                        "start": start,
                        "end": end,
                        "midpoint": ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0),
                        "orientation": orientation,
                    }
                )
        elif ops == ["c", "c"]:
            arrowheads.append(
                {
                    "drawing": drawing,
                    "center": bbox_center(drawing["rect"]),
                }
            )

    edges: list[dict[str, Any]] = []
    used_arrowheads: set[int] = set()
    horizontal_pairs = (("a", "b"), ("c", "d"))
    vertical_pairs = (("a", "c"), ("b", "d"))
    for line in line_drawings:
        candidate_pairs = horizontal_pairs if line["orientation"] == "horizontal" else vertical_pairs
        pair = min(candidate_pairs, key=lambda item: point_distance(line["midpoint"], commutative_pair_midpoint(nodes, item)))
        head_index: int | None = None
        head_center: tuple[float, float] | None = None
        best_distance: float | None = None
        for index, arrowhead in enumerate(arrowheads):
            if index in used_arrowheads:
                continue
            start_distance = point_distance(arrowhead["center"], line["start"])
            end_distance = point_distance(arrowhead["center"], line["end"])
            distance = min(start_distance, end_distance)
            if best_distance is None or distance < best_distance:
                best_distance = distance
                head_index = index
                head_center = arrowhead["center"]
        if head_index is None or best_distance is None or best_distance > COMMUTATIVE_ARROWHEAD_TOLERANCE_PT:
            continue
        used_arrowheads.add(head_index)
        if line["orientation"] == "horizontal":
            left_slot, right_slot = pair
            assert head_center is not None
            from_slot, to_slot = (
                (left_slot, right_slot) if head_center[0] > line["midpoint"][0] else (right_slot, left_slot)
            )
        else:
            top_slot, bottom_slot = pair
            assert head_center is not None
            from_slot, to_slot = (
                (top_slot, bottom_slot) if head_center[1] > line["midpoint"][1] else (bottom_slot, top_slot)
            )
        edge_geom = LineString([line["start"], line["end"]])
        edges.append(
            {
                "from_label": nodes[from_slot]["label"],
                "to_label": nodes[to_slot]["label"],
                "label": None,
                "geom": edge_geom,
                "drawing_rect": line["drawing"]["rect"],
                "slot_pair": pair,
            }
        )

    edges, remaining_lines = assign_labels_to_edges(edges, free_lines)
    for edge in edges:
        edge.pop("geom", None)
    return edges, remaining_lines


def recover_graph_nodes(page: dict[str, Any]) -> list[dict[str, Any]]:
    circles = [shape for shape in candidate_shapes(page) if shape["kind"] == "circle"]
    ordered = sorted(circles, key=lambda item: (bbox_center(item["bbox"])[1], bbox_center(item["bbox"])[0]))
    return [
        {
            "id": f"actual_{index}",
            "shape_bbox": circle["bbox"],
            "shape_kind": "circle",
            "shape_index": circle["index"],
            "center": bbox_center(circle["bbox"]),
        }
        for index, circle in enumerate(ordered)
    ]


def recover_graph_edges(page: dict[str, Any], nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    seen_pairs: set[frozenset[str]] = set()
    for drawing in page.get("drawings", []):
        ops = [item["op"] for item in drawing.get("items", [])]
        if ops != ["l"]:
            continue
        args = drawing["items"][0].get("args", [])
        if len(args) != 2:
            continue
        start_payload, end_payload = args
        nearest_start = nearest_node(start_payload, nodes)
        nearest_end = nearest_node(end_payload, nodes)
        if nearest_start is None or nearest_end is None:
            continue
        start_node, start_distance = nearest_start
        end_node, end_distance = nearest_end
        if start_distance > EDGE_ENDPOINT_TOLERANCE_PT or end_distance > EDGE_ENDPOINT_TOLERANCE_PT:
            continue
        if start_node["id"] == end_node["id"]:
            continue
        pair = frozenset({start_node["id"], end_node["id"]})
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        edges.append(
            {
                "node_ids": sorted(pair),
                "drawing_rect": drawing["rect"],
            }
        )
    return edges


def expected_node_map(spec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {normalize_label(node["label"]): node for node in spec.get("nodes", [])}


def expected_edges(spec: dict[str, Any]) -> list[dict[str, Any]]:
    node_by_id = {node["id"]: normalize_label(node["label"]) for node in spec.get("nodes", [])}
    expected: list[dict[str, Any]] = []
    for edge in spec.get("edges", []):
        expected.append(
            {
                "from_label": node_by_id.get(edge["from"]),
                "to_label": node_by_id.get(edge["to"]),
                "label": normalize_label(edge.get("label")),
            }
        )
    return expected


def make_mismatch(code: str, message: str, **payload: Any) -> dict[str, Any]:
    mismatch = {"code": code, "message": message}
    mismatch.update(payload)
    return mismatch


def compare_nodes(spec: dict[str, Any], actual_nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    mismatches: list[dict[str, Any]] = []
    expected = expected_node_map(spec)
    actual_by_label: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for node in actual_nodes:
        actual_by_label[node["label"]].append(node)

    duplicates = {label: count for label, count in Counter(node["label"] for node in actual_nodes).items() if count > 1}
    for label, count in sorted(duplicates.items()):
        mismatches.append(
            make_mismatch(
                "DUPLICATE_VISIBLE_LABEL",
                f"visible label '{label}' appears {count} times",
                label=label,
                count=count,
            )
        )

    expected_labels = set(expected)
    actual_labels = set(actual_by_label)
    for label in sorted(expected_labels - actual_labels):
        mismatches.append(make_mismatch("MISSING_NODE", f"expected node '{label}' is missing", label=label))
    for label in sorted(actual_labels - expected_labels):
        mismatches.append(make_mismatch("EXTRA_NODE", f"unexpected node '{label}' is present", label=label))

    for label in sorted(expected_labels & actual_labels):
        expected_node = expected[label]
        actual_node = actual_by_label[label][0]
        expected_style = expected_node.get("style")
        if expected_style == "decision" and actual_node["shape_kind"] != "diamond":
            mismatches.append(
                make_mismatch(
                    "WRONG_NODE_TYPE",
                    f"node '{label}' should be a decision diamond",
                    label=label,
                    expected_type="decision",
                    actual_type=actual_node["shape_kind"],
                )
            )
    return mismatches


def compare_commutative_nodes(spec: dict[str, Any], actual_slots: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    mismatches: list[dict[str, Any]] = []
    expected_by_slot = {str(node["id"]): normalize_label(node["label"]) for node in spec.get("nodes", [])}

    duplicates = {
        label: count for label, count in Counter(node["label"] for node in actual_slots.values()).items() if count > 1
    }
    for label, count in sorted(duplicates.items()):
        mismatches.append(
            make_mismatch(
                "DUPLICATE_VISIBLE_LABEL",
                f"visible label '{label}' appears {count} times",
                label=label,
                count=count,
            )
        )

    for slot in COMMUTATIVE_SLOT_ORDER:
        expected_label = expected_by_slot.get(slot)
        if expected_label is None:
            continue
        actual = actual_slots.get(slot)
        if actual is None or actual.get("label") is None:
            mismatches.append(
                make_mismatch("MISSING_NODE", f"expected node '{expected_label}' is missing", label=expected_label, slot=slot)
            )
            continue
        actual_label = normalize_label(actual.get("label"))
        if actual_label != expected_label:
            mismatches.append(
                make_mismatch("MISSING_NODE", f"expected node '{expected_label}' is missing", label=expected_label, slot=slot)
            )
            mismatches.append(
                make_mismatch("EXTRA_NODE", f"unexpected node '{actual_label}' is present", label=actual_label, slot=slot)
            )
    return mismatches


def compare_graph_structure(spec: dict[str, Any], actual_nodes: list[dict[str, Any]], actual_edges: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    mismatches: list[dict[str, Any]] = []
    expected_nodes = {
        str(node["id"]): {
            "id": str(node["id"]),
            "label": normalize_label(node.get("label")),
            "position": tuple((node.get("metadata") or {}).get("graph_position", [0.0, 0.0])),
        }
        for node in spec.get("nodes", [])
    }
    expected_points = normalize_point_map(
        {node_id: (float(info["position"][0]), float(info["position"][1])) for node_id, info in expected_nodes.items()},
        flip_y=False,
    )
    actual_points = normalize_point_map(
        {node["id"]: (float(node["center"][0]), float(node["center"][1])) for node in actual_nodes},
        flip_y=True,
    )
    mapping, distances, unmatched_expected, unmatched_actual = greedy_point_match(expected_points, actual_points)

    for expected_id in sorted(unmatched_expected):
        label = expected_nodes[expected_id]["label"] or expected_id
        mismatches.append(make_mismatch("MISSING_NODE", f"expected graph node '{label}' is missing", label=label))
    for actual_id in sorted(unmatched_actual):
        mismatches.append(make_mismatch("EXTRA_NODE", f"unexpected graph node '{actual_id}' is present", label=actual_id))
    for actual_id, distance in distances.items():
        if distance > GRAPH_NODE_MATCH_TOLERANCE:
            expected_id = mapping[actual_id]
            label = expected_nodes[expected_id]["label"] or expected_id
            mismatches.append(
                make_mismatch(
                    "WRONG_NODE_POSITION",
                    f"graph node '{label}' is too far from the expected layout position",
                    label=label,
                    measured_distance=distance,
                    tolerance=GRAPH_NODE_MATCH_TOLERANCE,
                )
            )

    expected_edge_sets = {frozenset({str(edge["from"]), str(edge["to"])}) for edge in spec.get("edges", [])}
    actual_edge_sets: set[frozenset[str]] = set()
    recovered_edges: list[dict[str, Any]] = []
    for edge in actual_edges:
        actual_ids = edge["node_ids"]
        if any(actual_id not in mapping for actual_id in actual_ids):
            continue
        expected_pair = frozenset({mapping[actual_ids[0]], mapping[actual_ids[1]]})
        actual_edge_sets.add(expected_pair)
        recovered_edges.append({"node_ids": sorted(expected_pair), "drawing_rect": edge["drawing_rect"]})

    for edge in sorted(expected_edge_sets - actual_edge_sets, key=lambda item: sorted(item)):
        left, right = sorted(edge)
        mismatches.append(
            make_mismatch(
                "MISSING_EDGE",
                f"expected graph edge {left} -- {right} is missing",
                edge=f"{left} -- {right}",
            )
        )
    for edge in sorted(actual_edge_sets - expected_edge_sets, key=lambda item: sorted(item)):
        left, right = sorted(edge)
        mismatches.append(
            make_mismatch(
                "EXTRA_EDGE",
                f"unexpected graph edge {left} -- {right} is present",
                edge=f"{left} -- {right}",
            )
        )

    return mismatches, {
        "node_mapping": mapping,
        "node_distances": distances,
        "nodes": actual_nodes,
        "edges": recovered_edges,
    }


def compare_edges(spec: dict[str, Any], actual_edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    mismatches: list[dict[str, Any]] = []
    expected = expected_edges(spec)
    used_actual: set[int] = set()
    for expected_edge in expected:
        exact = [
            index
            for index, edge in enumerate(actual_edges)
            if edge["from_label"] == expected_edge["from_label"]
            and edge["to_label"] == expected_edge["to_label"]
            and normalize_label(edge.get("label")) == expected_edge["label"]
        ]
        if exact:
            used_actual.add(exact[0])
            continue

        same_direction = [
            index
            for index, edge in enumerate(actual_edges)
            if edge["from_label"] == expected_edge["from_label"] and edge["to_label"] == expected_edge["to_label"]
        ]
        if same_direction:
            actual_edge = actual_edges[same_direction[0]]
            used_actual.add(same_direction[0])
            mismatches.append(
                make_mismatch(
                    "WRONG_EDGE_LABEL",
                    f"edge {expected_edge['from_label']} -> {expected_edge['to_label']} has the wrong label",
                    edge=f"{expected_edge['from_label']} -> {expected_edge['to_label']}",
                    expected_label=expected_edge["label"],
                    actual_label=normalize_label(actual_edge.get("label")),
                )
            )
            continue

        reversed_direction = [
            index
            for index, edge in enumerate(actual_edges)
            if edge["from_label"] == expected_edge["to_label"] and edge["to_label"] == expected_edge["from_label"]
        ]
        if reversed_direction:
            actual_edge = actual_edges[reversed_direction[0]]
            used_actual.add(reversed_direction[0])
            mismatches.append(
                make_mismatch(
                    "REVERSED_EDGE",
                    f"edge direction is reversed for {expected_edge['from_label']} -> {expected_edge['to_label']}",
                    expected_edge=f"{expected_edge['from_label']} -> {expected_edge['to_label']}",
                    actual_edge=f"{actual_edge['from_label']} -> {actual_edge['to_label']}",
                )
            )
            continue

        mismatches.append(
            make_mismatch(
                "MISSING_EDGE",
                f"expected edge {expected_edge['from_label']} -> {expected_edge['to_label']} is missing",
                edge=f"{expected_edge['from_label']} -> {expected_edge['to_label']}",
                expected_label=expected_edge["label"],
            )
        )

    for index, edge in enumerate(actual_edges):
        if index in used_actual:
            continue
        mismatches.append(
            make_mismatch(
                "EXTRA_EDGE",
                f"unexpected edge {edge['from_label']} -> {edge['to_label']} is present",
                edge=f"{edge['from_label']} -> {edge['to_label']}",
                actual_label=normalize_label(edge.get("label")),
            )
        )
    return mismatches


def verify_rendered_family(spec: dict[str, Any], render_semantics: dict[str, Any]) -> dict[str, Any]:
    family = spec.get("diagram_family")
    if family not in SUPPORTED_SEMANTIC_FAMILIES:
        return {
            "supported_family": False,
            "mismatches": [],
            "mismatch_codes": [],
            "recovered": {},
        }

    page = render_semantics["pages"][0]
    extra_recovered: dict[str, Any] = {}
    if family == "commutative":
        actual_slots, free_lines = recover_commutative_nodes(page)
        actual_edges, remaining_lines = recover_commutative_edges(page, actual_slots, free_lines)
        mismatches = compare_commutative_nodes(spec, actual_slots)
        mismatches.extend(compare_edges(spec, actual_edges))
        ignored_free_text: list[str] = []
        unmatched_free_text = [line["text"] for line in remaining_lines]
        recovered_nodes: Any = actual_slots
    elif family == "graph":
        actual_nodes = recover_graph_nodes(page)
        actual_edges = recover_graph_edges(page, actual_nodes)
        mismatches, graph_recovered = compare_graph_structure(spec, actual_nodes, actual_edges)
        ignored_free_text = []
        unmatched_free_text = []
        recovered_nodes = graph_recovered["nodes"]
        actual_edges = graph_recovered["edges"]
        extra_recovered = {
            "node_mapping": graph_recovered["node_mapping"],
            "node_distances": graph_recovered["node_distances"],
        }
    else:
        actual_nodes, free_lines, _shapes = recover_nodes(page)
        actual_edges, remaining_lines = recover_edges(page, actual_nodes, free_lines)
        mismatches = compare_nodes(spec, actual_nodes)
        mismatches.extend(compare_edges(spec, actual_edges))

        expected_group_labels = {normalize_label(group.get("label")) for group in spec.get("groups", []) if group.get("label")}
        ignored_free_text = [line["text"] for line in remaining_lines if normalize_label(line["text"]) in expected_group_labels]
        unmatched_free_text = [
            line["text"] for line in remaining_lines if normalize_label(line["text"]) not in expected_group_labels
        ]
        recovered_nodes = actual_nodes
        extra_recovered = {}

    return {
        "supported_family": True,
        "mismatches": mismatches,
        "mismatch_codes": sorted({item["code"] for item in mismatches}),
        "recovered": {
            "nodes": recovered_nodes,
            "edges": actual_edges,
            "ignored_free_text": ignored_free_text,
            "unmatched_free_text": unmatched_free_text,
            **extra_recovered,
        },
    }
