#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


RENDER_SEMANTICS_SCHEMA_VERSION = "render-semantics.v1"
EXTRACTOR_VERSION = "pymupdf-page-ir.v1"
FLOAT_PRECISION = 6


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def round_float(value: float | int) -> float:
    if value is None:
        return 0.0
    return round(float(value), FLOAT_PRECISION)


def normalize_x(value: float | int, page_width: float) -> float:
    if page_width <= 0:
        return 0.0
    return round_float(float(value) / page_width)


def normalize_y(value: float | int, page_height: float) -> float:
    if page_height <= 0:
        return 0.0
    return round_float(float(value) / page_height)


def point_payload(point: Any, page_width: float, page_height: float) -> dict[str, float]:
    return {
        "x": round_float(point.x),
        "y": round_float(point.y),
        "x_norm": normalize_x(point.x, page_width),
        "y_norm": normalize_y(point.y, page_height),
    }


def rect_payload(rect: Any, page_width: float, page_height: float) -> dict[str, float]:
    x0 = round_float(rect.x0)
    y0 = round_float(rect.y0)
    x1 = round_float(rect.x1)
    y1 = round_float(rect.y1)
    return {
        "x0": x0,
        "y0": y0,
        "x1": x1,
        "y1": y1,
        "width": round_float(x1 - x0),
        "height": round_float(y1 - y0),
        "x0_norm": normalize_x(x0, page_width),
        "y0_norm": normalize_y(y0, page_height),
        "x1_norm": normalize_x(x1, page_width),
        "y1_norm": normalize_y(y1, page_height),
    }


def quad_payload(quad: Any, page_width: float, page_height: float) -> dict[str, Any]:
    return {
        "ul": point_payload(quad.ul, page_width, page_height),
        "ur": point_payload(quad.ur, page_width, page_height),
        "ll": point_payload(quad.ll, page_width, page_height),
        "lr": point_payload(quad.lr, page_width, page_height),
    }


def serialize_pdf_value(value: Any, page_width: float, page_height: float) -> Any:
    if value is None:
        return None
    if hasattr(value, "x") and hasattr(value, "y"):
        return point_payload(value, page_width, page_height)
    if hasattr(value, "x0") and hasattr(value, "y0") and hasattr(value, "x1") and hasattr(value, "y1"):
        return rect_payload(value, page_width, page_height)
    if hasattr(value, "ul") and hasattr(value, "ur") and hasattr(value, "ll") and hasattr(value, "lr"):
        return quad_payload(value, page_width, page_height)
    if isinstance(value, tuple):
        return [serialize_pdf_value(item, page_width, page_height) for item in value]
    if isinstance(value, list):
        return [serialize_pdf_value(item, page_width, page_height) for item in value]
    if isinstance(value, dict):
        return {str(key): serialize_pdf_value(item, page_width, page_height) for key, item in sorted(value.items())}
    if isinstance(value, float):
        return round_float(value)
    return value


def color_payload(color: Any) -> list[float] | None:
    if color is None:
        return None
    if isinstance(color, (tuple, list)):
        return [round_float(component) for component in color]
    return [round_float(color)]


def drawing_payload(drawing: dict[str, Any], page_width: float, page_height: float, index: int) -> dict[str, Any]:
    items = []
    for item in drawing.get("items", []):
        op = item[0] if item else None
        args = item[1:] if len(item) > 1 else []
        items.append(
            {
                "op": op,
                "args": serialize_pdf_value(list(args), page_width, page_height),
            }
        )
    payload = {
        "primitive_id": f"drawing-{drawing.get('seqno', index)}",
        "seqno": drawing.get("seqno"),
        "type": drawing.get("type"),
        "rect": serialize_pdf_value(drawing.get("rect"), page_width, page_height),
        "items": items,
        "close_path": bool(drawing.get("closePath", False)),
        "even_odd": bool(drawing.get("even_odd", False)),
        "fill_opacity": round_float(drawing.get("fill_opacity", 0.0)),
        "stroke_opacity": round_float(drawing.get("stroke_opacity", 0.0)),
        "width": round_float(drawing.get("width", 0.0)),
        "line_cap": serialize_pdf_value(drawing.get("lineCap"), page_width, page_height),
        "line_join": drawing.get("lineJoin"),
        "dashes": drawing.get("dashes"),
        "color": color_payload(drawing.get("color")),
        "fill": color_payload(drawing.get("fill")),
    }
    return payload


def word_payload(word: tuple[Any, ...], page_width: float, page_height: float) -> dict[str, Any]:
    x0, y0, x1, y1, text, block_no, line_no, word_no = word
    bbox = rect_payload(type("RectLike", (), {"x0": x0, "y0": y0, "x1": x1, "y1": y1})(), page_width, page_height)
    return {
        "primitive_id": f"word-{int(block_no)}-{int(line_no)}-{int(word_no)}",
        "text": text,
        "bbox": bbox,
        "block": int(block_no),
        "line": int(line_no),
        "word": int(word_no),
    }


def extract_pdf_render_semantics(pdf_path: Path, manifest_path: Path | None = None) -> dict[str, Any]:
    import fitz  # type: ignore

    doc = fitz.open(pdf_path)
    try:
        pages: list[dict[str, Any]] = []
        for page_index, page in enumerate(doc):
            page_rect = page.rect
            page_width = float(page_rect.width)
            page_height = float(page_rect.height)
            drawings = sorted(page.get_drawings(), key=lambda item: (item.get("seqno", -1), json.dumps(item.get("rect"), default=str)))
            words = sorted(page.get_text("words"), key=lambda item: (item[5], item[6], item[7], item[1], item[0], item[4]))
            pages.append(
                {
                    "page_index": page_index,
                    "width": round_float(page_width),
                    "height": round_float(page_height),
                    "bbox": rect_payload(page_rect, page_width, page_height),
                    "drawings": [
                        drawing_payload(drawing, page_width, page_height, index)
                        for index, drawing in enumerate(drawings)
                    ],
                    "words": [word_payload(word, page_width, page_height) for word in words],
                }
            )
    finally:
        doc.close()

    return {
        "schema_version": RENDER_SEMANTICS_SCHEMA_VERSION,
        "extractor_version": EXTRACTOR_VERSION,
        "pdf": str(pdf_path),
        "pdf_sha256": file_sha256(pdf_path),
        "manifest": str(manifest_path) if manifest_path else None,
        "page_count": len(pages),
        "normalization": {
            "coordinate_space": "page_points",
            "origin": "top_left",
            "float_precision": FLOAT_PRECISION,
            "normalized_axes": ["x_norm", "y_norm"],
        },
        "pages": pages,
    }
