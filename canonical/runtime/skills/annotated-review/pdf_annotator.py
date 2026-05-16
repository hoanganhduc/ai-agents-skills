"""PyMuPDF-based PDF markup and companion HTML generation."""

from __future__ import annotations

import os
import textwrap
from typing import Dict, List, Optional, Tuple

from critic import (
    severity_color_latex,
    severity_emoji,
    status_color_latex,
    status_emoji,
    fmt_datetime,
    fmt_line_range,
    count_annotations,
    count_verification,
    count_trust,
)

# PyMuPDF color maps (RGB 0-1 floats)
_SEVERITY_RGB = {
    "critical":   (1.0, 0.4, 0.4),
    "major":      (1.0, 0.7, 0.4),
    "minor":      (1.0, 1.0, 0.5),
    "suggestion": (0.6, 0.8, 1.0),
}

_STATUS_RGB = {
    "confirmed": (0.5, 1.0, 0.5),
    "disputed":  (0.85, 0.6, 0.85),
    "partial":   (0.6, 0.9, 0.9),
    "additional":(0.7, 1.0, 1.0),
}

_TRUST_UNVERIFIED_RGB = (1.0, 0.4, 0.4)
_TRUST_SUSPICIOUS_RGB = (1.0, 0.75, 0.4)


def _wrap_text(text: str, width: int = 120) -> str:
    """Pre-wrap text to fit in Courier 7pt textbox."""
    return "\n".join(textwrap.wrap(text, width))


# ---------------------------------------------------------------------------
# Metadata page
# ---------------------------------------------------------------------------

def prepend_metadata_page(
    doc,
    meta: dict,
    verification: Optional[dict],
    trust_verification: Optional[dict],
    annotation_counts: dict,
    verification_counts: dict,
    trust_counts: dict,
    paper_title: str = "",
) -> None:
    """Insert a metadata page at position 0 in the fitz document."""
    try:
        import fitz
    except ImportError:
        return

    orig_width = doc[0].rect.width if len(doc) > 0 else 595
    page = doc.new_page(pno=0, width=orig_width, height=842)

    # Build text lines
    lines = [
        "ANNOTATED REVIEW",
        f"Paper:     {paper_title}" if paper_title else "",
        f"Date:      {fmt_datetime(meta.get('reviewed_at', ''))}",
        f"Reviewers: {len(meta.get('agents', []))}",
    ]
    lines = [l for l in lines if l]  # remove empty

    for i, ag in enumerate(meta.get("agents", []), 1):
        role = ag.get("role", "")
        model = ag.get("model", "")
        thinking = ag.get("thinking", "")
        lines.append(f"  [{i}] {role:<26}{model:<24}thinking: {thinking}")

    if verification:
        vag = verification.get("agent", {})
        lines.append(
            f"Verifier:  {vag.get('role', ''):<26}{vag.get('model', ''):<24}thinking: {vag.get('thinking', '')}"
        )
        lines.append(f"           verified {fmt_datetime(verification.get('verified_at', ''))}")

    if trust_verification:
        tag = trust_verification.get("agent", {})
        lines.append(
            f"Trust:     {tag.get('role', ''):<26}{tag.get('model', ''):<24}thinking: {tag.get('thinking', '')}"
        )
        lines.append(f"           checked  {fmt_datetime(trust_verification.get('verified_at', ''))}")

    lines.append(f"Focus:     {meta.get('focus', 'all')}")

    ac = annotation_counts
    lines.append(
        f"Issues:    {ac['critical']} critical  /  {ac['major']} major"
        f"  /  {ac['minor']} minor  /  {ac['suggestion']} suggestion"
    )

    if verification:
        vc = verification_counts
        lines.append(
            f"Verified:  {vc['confirmed']} confirmed  /  {vc['disputed']} disputed"
            f"  /  {vc['partial']} partial  /  {vc['additions']} additions"
        )

    if trust_verification:
        tc = trust_counts
        lines.append(
            f"Trust:     {tc['verified']} verified  /  {tc['unverified']} unverified"
            f"  /  {tc['suspicious']} suspicious"
        )

    # Draw light gray background rect
    margin = 40
    rect = fitz.Rect(margin, margin, orig_width - margin, margin + len(lines) * 10 + 30)
    page.draw_rect(rect, color=(0.85, 0.85, 0.85), fill=(0.93, 0.93, 0.93))

    # Insert text
    text_rect = fitz.Rect(margin + 5, margin + 5, orig_width - margin - 5, margin + len(lines) * 10 + 25)
    text_content = "\n".join(lines)
    page.insert_textbox(
        text_rect,
        text_content,
        fontname="Courier",
        fontsize=7,
        color=(0, 0, 0),
    )


# ---------------------------------------------------------------------------
# Page markup
# ---------------------------------------------------------------------------

def markup_page(
    page,
    annotations_on_page: List[Tuple[int, dict]],
    verification_results_map: Dict[int, dict],
    trust_refs_map: Dict[str, list],
    page_offset: int = 1,
) -> None:
    """Add highlights and sticky notes to a PDF page."""
    try:
        import fitz
    except ImportError:
        return

    for ann_idx, ann in annotations_on_page:
        quote = ann.get("quote", "")[:40]
        severity = ann.get("severity", "minor")
        color = _SEVERITY_RGB.get(severity, (0.8, 0.8, 0.8))

        # Search for quote text
        quads = page.search_for(quote)

        if quads:
            # Highlight matched text
            highlight = page.add_highlight_annot(quads)
            highlight.set_colors(stroke=color)
            highlight.update()
            # Use first match position for sticky note
            rect = quads[0].rect if hasattr(quads[0], 'rect') else fitz.Rect(quads[0])
            note_point = fitz.Point(rect.x0, rect.y0)
        else:
            # No match: place at top of page
            note_point = fitz.Point(50, 50 + ann_idx * 20)

        # Reviewer sticky note
        line_range = fmt_line_range(ann)
        severity_upper = ann.get("severity", "").upper()
        type_val = ann.get("type", "")
        title = ann.get("title", "")
        body = ann.get("body", "")
        reviewer_content = (
            f"[{severity_upper}, {line_range} — {type_val.title()}: {title}]\n\n{body}"
        )
        reviewer_note = page.add_text_annot(note_point, _wrap_text(reviewer_content, 80), icon="Note")
        reviewer_note.set_colors(stroke=color, fill=color)
        reviewer_note.update()

        # Trust warnings on reviewer body
        ann_key = f"annotation_{ann_idx}"
        for ref in trust_refs_map.get(ann_key, []):
            if ref.get("status") in ("unverified", "suspicious"):
                trust_point = fitz.Point(note_point.x + 15, note_point.y)
                trust_color = _TRUST_UNVERIFIED_RGB if ref["status"] == "unverified" else _TRUST_SUSPICIOUS_RGB
                trust_content = (
                    f"[WARNING {ref['status'].upper()} REFERENCE — Trust Verifier]\n"
                    f"Citation: {ref.get('citation', '')}\n"
                    f"{ref.get('note', '')}"
                )
                trust_note = page.add_text_annot(trust_point, _wrap_text(trust_content, 80), icon="Note")
                trust_note.set_colors(stroke=trust_color, fill=trust_color)
                trust_note.update()

        # Verifier sticky note
        result = verification_results_map.get(ann_idx)
        if result:
            status = result.get("status", "")
            ver_color = _STATUS_RGB.get(status, (0.8, 0.8, 0.8))
            status_upper = status.upper()
            emoji = status_emoji(status)
            ver_content = (
                f"[{emoji} {status_upper} — Independent Verifier, {line_range}]\n\n"
                f"{result.get('comment', '')}"
            )
            ver_point = fitz.Point(note_point.x + 8, note_point.y + 8)
            ver_note = page.add_text_annot(ver_point, _wrap_text(ver_content, 80), icon="Note")
            ver_note.set_colors(stroke=ver_color, fill=ver_color)
            ver_note.update()

            # Trust warnings on verifier comment
            ver_key = f"verification_result_{ann_idx}"
            for ref in trust_refs_map.get(ver_key, []):
                if ref.get("status") in ("unverified", "suspicious"):
                    trust_point2 = fitz.Point(ver_point.x + 15, ver_point.y)
                    trust_color2 = (
                        _TRUST_UNVERIFIED_RGB if ref["status"] == "unverified"
                        else _TRUST_SUSPICIOUS_RGB
                    )
                    trust_content2 = (
                        f"[WARNING {ref['status'].upper()} REFERENCE — Trust Verifier]\n"
                        f"Citation: {ref.get('citation', '')}\n"
                        f"{ref.get('note', '')}"
                    )
                    trust_note2 = page.add_text_annot(
                        trust_point2, _wrap_text(trust_content2, 80), icon="Note"
                    )
                    trust_note2.set_colors(stroke=trust_color2, fill=trust_color2)
                    trust_note2.update()


# ---------------------------------------------------------------------------
# Companion HTML
# ---------------------------------------------------------------------------

_HTML_SEVERITY_BG = {
    "critical":   "#ffcccc",
    "major":      "#ffe0cc",
    "minor":      "#fff9cc",
    "suggestion": "#cce0ff",
}

_HTML_STATUS_BG = {
    "confirmed": "#ccffcc",
    "disputed":  "#f0ccf0",
    "partial":   "#ccf0f0",
    "additional":"#ccffff",
}


def _html_metadata_table(
    meta: dict,
    verification: Optional[dict],
    trust_verification: Optional[dict],
    annotation_counts: dict,
    verification_counts: dict,
    trust_counts: dict,
    paper_title: str = "",
) -> str:
    rows = []
    if paper_title:
        rows.append(f"<tr><td><code>Paper</code></td><td><code>{_he(paper_title)}</code></td></tr>")
    rows.append(f"<tr><td><code>Date</code></td><td><code>{_he(fmt_datetime(meta.get('reviewed_at', '')))}</code></td></tr>")
    rows.append(f"<tr><td><code>Reviewers</code></td><td><code>{len(meta.get('agents', []))}</code></td></tr>")
    for i, ag in enumerate(meta.get("agents", []), 1):
        rows.append(
            f"<tr><td><code>[{i}]</code></td><td><code>"
            f"{_he(ag.get('role',''))} &mdash; {_he(ag.get('model',''))} &mdash; thinking: {_he(ag.get('thinking',''))}"
            f"</code></td></tr>"
        )
    if verification:
        vag = verification.get("agent", {})
        rows.append(
            f"<tr><td><code>Verifier</code></td><td><code>"
            f"{_he(vag.get('role',''))} &mdash; {_he(vag.get('model',''))} &mdash; thinking: {_he(vag.get('thinking',''))}"
            f"</code></td></tr>"
        )
        rows.append(
            f"<tr><td><code>Verified</code></td><td><code>"
            f"{_he(fmt_datetime(verification.get('verified_at', '')))}"
            f"</code></td></tr>"
        )
    if trust_verification:
        tag = trust_verification.get("agent", {})
        rows.append(
            f"<tr><td><code>Trust</code></td><td><code>"
            f"{_he(tag.get('role',''))} &mdash; {_he(tag.get('model',''))} &mdash; thinking: {_he(tag.get('thinking',''))}"
            f"</code></td></tr>"
        )
        rows.append(
            f"<tr><td><code>Checked</code></td><td><code>"
            f"{_he(fmt_datetime(trust_verification.get('verified_at', '')))}"
            f"</code></td></tr>"
        )
    rows.append(
        f"<tr><td><code>Focus</code></td><td><code>{_he(meta.get('focus', 'all'))}</code></td></tr>"
    )
    ac = annotation_counts
    rows.append(
        f"<tr><td><code>Issues</code></td><td><code>"
        f"{ac['critical']} critical / {ac['major']} major / {ac['minor']} minor / {ac['suggestion']} suggestion"
        f"</code></td></tr>"
    )
    if verification:
        vc = verification_counts
        rows.append(
            f"<tr><td><code>Verified</code></td><td><code>"
            f"{vc['confirmed']} confirmed / {vc['disputed']} disputed / {vc['partial']} partial / {vc['additions']} additions"
            f"</code></td></tr>"
        )
    if trust_verification:
        tc = trust_counts
        rows.append(
            f"<tr><td><code>Trust</code></td><td><code>"
            f"{tc['verified']} verified / {tc['unverified']} unverified / {tc['suspicious']} suspicious"
            f"</code></td></tr>"
        )
    return "<table border='1' cellpadding='4' cellspacing='0'>\n" + "\n".join(rows) + "\n</table>"


def _he(s: str) -> str:
    """HTML-escape a string."""
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def produce_companion_html(review_data: dict, output_path: str, paper_title: str = "") -> str:
    """Write a self-contained HTML companion file. Returns output path."""
    meta = review_data.get("meta", {})
    annotations = review_data.get("annotations", [])
    verification = review_data.get("verification")
    trust_verification = review_data.get("trust_verification")

    annotation_counts = count_annotations(annotations)
    verification_counts = count_verification(verification)
    trust_counts = count_trust(trust_verification)

    # Build verification results map
    ver_results_map: Dict[int, dict] = {}
    additional_issues: list = []
    if verification:
        for result in verification.get("results", []):
            idx = result.get("annotation_index")
            if idx is not None:
                ver_results_map[idx] = result
        additional_issues = verification.get("additional_issues", [])

    # Build trust refs map
    trust_refs_map: Dict[str, list] = {}
    if trust_verification:
        for ref in trust_verification.get("references_checked", []):
            cited_in = ref.get("cited_in", "")
            if cited_in not in trust_refs_map:
                trust_refs_map[cited_in] = []
            trust_refs_map[cited_in].append(ref)

    html_parts = [
        "<!DOCTYPE html><html><head><meta charset='utf-8'>",
        f"<title>Annotated Review{' — ' + _he(paper_title) if paper_title else ''}</title>",
        "<style>",
        "body { font-family: sans-serif; max-width: 900px; margin: 0 auto; padding: 20px; }",
        "table { border-collapse: collapse; margin-bottom: 1em; }",
        "td, th { padding: 4px 8px; border: 1px solid #ccc; }",
        "code { background: #f5f5f5; padding: 2px 4px; }",
        ".ann-block { margin: 1em 0; padding: 10px; border-radius: 4px; }",
        ".ver-block { margin: 0.5em 0; padding: 8px; border-radius: 4px; font-size: 0.95em; }",
        ".trust-warn { background: #ffeecc; border-left: 4px solid #ff8800; padding: 6px; margin: 4px 0; font-size: 0.9em; }",
        "h2 { margin-top: 2em; }",
        "h3 { margin-top: 0; font-size: 1em; }",
        "</style></head><body>",
        f"<h1>Annotated Review{' &mdash; ' + _he(paper_title) if paper_title else ''}</h1>",
        _html_metadata_table(
            meta, verification, trust_verification,
            annotation_counts, verification_counts, trust_counts, paper_title
        ),
        "<hr>",
    ]

    # Annotations
    for i, ann in enumerate(annotations):
        severity = ann.get("severity", "minor")
        bg_color = _HTML_SEVERITY_BG.get(severity, "#f5f5f5")
        emoji = severity_emoji(severity)
        line_range = fmt_line_range(ann)
        type_val = ann.get("type", "")
        title = ann.get("title", "")
        body = ann.get("body", "")

        html_parts.append(
            f"<div class='ann-block' style='background:{bg_color};'>"
        )
        html_parts.append(
            f"<h3>{_he(emoji)} {_he(severity.upper())} [{_he(line_range)}] "
            f"&mdash; {_he(type_val.title())}: {_he(title)}</h3>"
        )
        html_parts.append(f"<p>{_he(body)}</p>")

        # Trust warnings for reviewer body
        ann_key = f"annotation_{i}"
        for ref in trust_refs_map.get(ann_key, []):
            if ref.get("status") in ("unverified", "suspicious"):
                html_parts.append(
                    f"<div class='trust-warn'>"
                    f"<strong>&#9888; {_he(ref['status'].upper())} REFERENCE</strong>: "
                    f"{_he(ref.get('citation', ''))}<br>"
                    f"<em>{_he(ref.get('note', ''))}</em></div>"
                )

        # Verifier response
        result = ver_results_map.get(i)
        if result:
            status = result.get("status", "")
            ver_bg = _HTML_STATUS_BG.get(status, "#f0f0f0")
            ver_emoji = status_emoji(status)
            ver_comment = result.get("comment", "")
            html_parts.append(
                f"<div class='ver-block' style='background:{ver_bg};'>"
                f"<strong>{_he(ver_emoji)} {_he(status.upper())} (Independent Verifier):</strong> "
                f"{_he(ver_comment)}</div>"
            )
            # Trust warnings for verifier comment
            ver_key = f"verification_result_{i}"
            for ref in trust_refs_map.get(ver_key, []):
                if ref.get("status") in ("unverified", "suspicious"):
                    html_parts.append(
                        f"<div class='trust-warn'>"
                        f"<strong>&#9888; {_he(ref['status'].upper())} REFERENCE</strong>: "
                        f"{_he(ref.get('citation', ''))}<br>"
                        f"<em>{_he(ref.get('note', ''))}</em></div>"
                    )

        html_parts.append("</div><hr>")

    # Additional verifier issues
    if additional_issues:
        html_parts.append("<h2>&#10133; Additional Issues &mdash; Independent Verifier</h2>")
        for issue in additional_issues:
            severity = issue.get("severity", "minor")
            bg_color = _HTML_SEVERITY_BG.get(severity, "#f5f5f5")
            emoji = severity_emoji(severity)
            line_range = fmt_line_range(issue)
            type_val = issue.get("type", "")
            title = issue.get("title", "")
            body = issue.get("body", "")
            html_parts.append(
                f"<div class='ann-block' style='background:{bg_color};'>"
                f"<h3>&#10133; {_he(emoji)} {_he(severity.upper())} [{_he(line_range)}] "
                f"&mdash; {_he(type_val.title())}: {_he(title)}</h3>"
                f"<p>{_he(body)}</p></div><hr>"
            )

    # Trust verification summary
    if trust_verification:
        html_parts.append("<h2>&#9888; Trust Verification &mdash; Reference Check</h2>")
        tag = trust_verification.get("agent", {})
        tv_summary_rows = [
            f"<tr><td><code>Agent</code></td><td><code>"
            f"{_he(tag.get('role',''))} &mdash; {_he(tag.get('model',''))} &mdash; thinking: {_he(tag.get('thinking',''))}"
            f"</code></td></tr>",
            f"<tr><td><code>Checked</code></td><td><code>{_he(fmt_datetime(trust_verification.get('verified_at','')))}</code></td></tr>",
        ]
        tc = trust_counts
        tv_summary_rows.append(
            f"<tr><td><code>Result</code></td><td><code>"
            f"{tc['total']} references: {tc['verified']} verified / {tc['unverified']} unverified / {tc['suspicious']} suspicious"
            f"</code></td></tr>"
        )
        html_parts.append(
            "<table border='1' cellpadding='4' cellspacing='0'>\n"
            + "\n".join(tv_summary_rows) + "\n</table>"
        )

        unverified_refs = [
            r for r in trust_verification.get("references_checked", [])
            if r.get("status") in ("unverified", "suspicious")
        ]
        if unverified_refs:
            html_parts.append("<h3>&#9888; Unverified / Suspicious References</h3>")
            for ref in unverified_refs:
                status_label = ref.get("status", "").upper()
                html_parts.append(
                    f"<p><strong>{_he(ref.get('citation', ''))}</strong> "
                    f"(cited in {_he(ref.get('cited_in', ''))})<br>"
                    f"<em style='color:{'red' if ref.get('status') == 'unverified' else 'orange'};'>"
                    f"[{status_label}]</em> {_he(ref.get('note', ''))}</p>"
                )
        else:
            tc = trust_counts
            html_parts.append(f"<p>&#9989; All {tc['total']} references verified.</p>")

    html_parts.append("</body></html>")

    os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(html_parts))

    return output_path


# ---------------------------------------------------------------------------
# PDF annotation
# ---------------------------------------------------------------------------

def annotate_pdf(
    pdf_path: str,
    review_data: dict,
    output_path: Optional[str] = None,
    paper_title: str = "",
) -> dict:
    """Open PDF, prepend metadata page, markup pages, save annotated copy.

    Returns dict with keys: pdf, pages, or error.
    """
    try:
        import fitz
    except ImportError:
        return {"error": "pymupdf_not_installed", "message": "PyMuPDF (fitz) is not installed"}

    if not os.path.exists(pdf_path):
        return {"error": "file_not_found", "message": f"PDF not found: {pdf_path}"}

    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        return {"error": "open_failed", "message": str(e)}

    # Check for text layer: try first 3 pages
    has_text = False
    for page_num in range(min(3, len(doc))):
        text = doc[page_num].get_text().strip()
        if text:
            has_text = True
            break

    if not has_text:
        doc.close()
        return {"error": "no_text_layer", "message": "No text layer detected — run ocrmypdf first"}

    meta = review_data.get("meta", {})
    annotations = review_data.get("annotations", [])
    verification = review_data.get("verification")
    trust_verification = review_data.get("trust_verification")

    annotation_counts = count_annotations(annotations)
    verification_counts = count_verification(verification)
    trust_counts = count_trust(trust_verification)

    # Build verification results map
    ver_results_map: Dict[int, dict] = {}
    additional_issues: list = []
    if verification:
        for result in verification.get("results", []):
            idx = result.get("annotation_index")
            if idx is not None:
                ver_results_map[idx] = result
        additional_issues = verification.get("additional_issues", [])

    # Build trust refs map
    trust_refs_map: Dict[str, list] = {}
    if trust_verification:
        for ref in trust_verification.get("references_checked", []):
            cited_in = ref.get("cited_in", "")
            if cited_in not in trust_refs_map:
                trust_refs_map[cited_in] = []
            trust_refs_map[cited_in].append(ref)

    # Prepend metadata page (page 0)
    prepend_metadata_page(
        doc, meta, verification, trust_verification,
        annotation_counts, verification_counts, trust_counts,
        paper_title=paper_title,
    )

    # Page offset = 1 because we inserted one page at beginning
    page_offset = 1

    # Group annotations by page number
    page_annotations: Dict[int, List[Tuple[int, dict]]] = {}
    for i, ann in enumerate(annotations):
        page_num = ann.get("page", 1)
        adjusted_page = page_num - 1 + page_offset  # 0-based with offset
        if adjusted_page not in page_annotations:
            page_annotations[adjusted_page] = []
        page_annotations[adjusted_page].append((i, ann))

    # Group additional issues by page
    additional_by_page: Dict[int, List[dict]] = {}
    for issue in additional_issues:
        page_num = issue.get("page", 1)
        adjusted_page = page_num - 1 + page_offset
        if adjusted_page not in additional_by_page:
            additional_by_page[adjusted_page] = []
        additional_by_page[adjusted_page].append(issue)

    # Markup pages
    for page_idx in range(len(doc)):
        anns_on_page = page_annotations.get(page_idx, [])
        add_issues_on_page = additional_by_page.get(page_idx, [])

        if not anns_on_page and not add_issues_on_page:
            continue

        page = doc[page_idx]

        if anns_on_page:
            markup_page(page, anns_on_page, ver_results_map, trust_refs_map, page_offset)

        # Additional verifier issues
        for issue in add_issues_on_page:
            quote = issue.get("quote", "")[:40]
            quads = page.search_for(quote)
            add_color = _STATUS_RGB.get("additional", (0.7, 1.0, 1.0))

            if quads:
                try:
                    import fitz
                    highlight = page.add_highlight_annot(quads)
                    highlight.set_colors(stroke=add_color)
                    highlight.update()
                    rect = quads[0].rect if hasattr(quads[0], 'rect') else fitz.Rect(quads[0])
                    note_point = fitz.Point(rect.x0, rect.y0)
                except Exception:
                    note_point = fitz.Point(50, 50)
            else:
                import fitz
                note_point = fitz.Point(50, 50)

            severity_upper = issue.get("severity", "").upper()
            line_range = fmt_line_range(issue)
            title = issue.get("title", "")
            body = issue.get("body", "")
            content = (
                f"[VERIFIER ADDITION — {severity_upper}, {line_range}]\n"
                f"{title}\n\n{body}"
            )
            try:
                note = page.add_text_annot(note_point, _wrap_text(content, 80), icon="Note")
                note.set_colors(stroke=add_color, fill=add_color)
                note.update()
            except Exception:
                pass

    # Determine output path
    if output_path is None:
        base, ext = os.path.splitext(pdf_path)
        output_path = base + "_annotated" + ext

    try:
        doc.save(output_path)
        n_pages = len(doc)
        doc.close()
        return {"pdf": output_path, "pages": n_pages}
    except Exception as e:
        doc.close()
        return {"error": "save_failed", "message": str(e)}


# ---------------------------------------------------------------------------
# Merged PDF (placeholder)
# ---------------------------------------------------------------------------

def produce_merged_pdf(
    marked_pdf_path: str,
    companion_html_path: str,
    output_path: str,
) -> Optional[str]:
    """Merge marked PDF + companion. Currently copies marked PDF as output.

    A full implementation would render companion HTML to PDF then merge.
    """
    import shutil
    import logging
    logging.warning(
        "produce_merged_pdf: companion HTML to PDF merge not available; "
        "copying annotated PDF as merged output."
    )
    if marked_pdf_path and os.path.exists(marked_pdf_path):
        shutil.copy2(marked_pdf_path, output_path)
        return output_path
    return None
