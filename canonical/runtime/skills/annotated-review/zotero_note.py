"""Zotero note HTML generation and creation for annotated-review."""

from __future__ import annotations

import os
import sys
from typing import List, Optional

from critic import (
    severity_emoji,
    status_emoji,
    fmt_datetime,
    fmt_line_range,
    count_annotations,
    count_verification,
    count_trust,
)

# ---------------------------------------------------------------------------
# sys.path setup — same pattern as zot.py
# ---------------------------------------------------------------------------

_SKILLS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ZOTERO_SKILL_DIR = os.path.join(_SKILLS_DIR, "zotero")
if _ZOTERO_SKILL_DIR not in sys.path:
    sys.path.insert(0, _ZOTERO_SKILL_DIR)


def _he(s: str) -> str:
    """HTML-escape a string."""
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------

def build_note_html(review_data: dict, paper_title: str = "") -> str:
    """Build full HTML for Zotero note (no <html>/<body> wrapper)."""
    meta = review_data.get("meta", {})
    annotations = review_data.get("annotations", [])
    verification = review_data.get("verification")
    trust_verification = review_data.get("trust_verification")

    annotation_counts = count_annotations(annotations)
    verification_counts = count_verification(verification)
    trust_counts = count_trust(trust_verification)

    # Build verification results map
    ver_results_map: dict = {}
    additional_issues: list = []
    if verification:
        for result in verification.get("results", []):
            idx = result.get("annotation_index")
            if idx is not None:
                ver_results_map[idx] = result
        additional_issues = verification.get("additional_issues", [])

    # Build trust refs map
    trust_refs_map: dict = {}
    if trust_verification:
        for ref in trust_verification.get("references_checked", []):
            cited_in = ref.get("cited_in", "")
            if cited_in not in trust_refs_map:
                trust_refs_map[cited_in] = []
            trust_refs_map[cited_in].append(ref)

    parts: List[str] = []

    # Title
    if paper_title:
        parts.append(f"<h2>Annotated Review &mdash; {_he(paper_title)}</h2>")
    else:
        parts.append("<h2>Annotated Review</h2>")

    # Metadata table
    parts.append(_metadata_table(
        meta, verification, trust_verification,
        annotation_counts, verification_counts, trust_counts
    ))
    parts.append("<hr>")

    # Annotations
    for i, ann in enumerate(annotations):
        severity = ann.get("severity", "minor")
        emoji = severity_emoji(severity)
        line_range = fmt_line_range(ann)
        type_val = ann.get("type", "")
        title = ann.get("title", "")
        body = ann.get("body", "")

        parts.append(
            f"<h3>{_he(emoji)} {_he(severity.upper())} [{_he(line_range)}] "
            f"&mdash; {_he(type_val.title())}: {_he(title)}</h3>"
        )
        parts.append(f"<p>{_he(body)}</p>")

        # Trust warnings for reviewer body
        ann_key = f"annotation_{i}"
        for ref in trust_refs_map.get(ann_key, []):
            if ref.get("status") in ("unverified", "suspicious"):
                parts.append(
                    f"<p><strong>&#9888; {_he(ref['status'].upper())} REFERENCE "
                    f"(Trust Verifier):</strong> {_he(ref.get('citation', ''))}<br>"
                    f"<em>{_he(ref.get('note', ''))}</em></p>"
                )

        # Verifier response
        result = ver_results_map.get(i)
        if result:
            ver_emoji = status_emoji(result.get("status", ""))
            status_upper = result.get("status", "").upper()
            comment = result.get("comment", "")
            parts.append(
                f"<p><strong>{_he(ver_emoji)} {_he(status_upper)} "
                f"(Independent Verifier):</strong> {_he(comment)}</p>"
            )
            # Trust warnings for verifier comment
            ver_key = f"verification_result_{i}"
            for ref in trust_refs_map.get(ver_key, []):
                if ref.get("status") in ("unverified", "suspicious"):
                    parts.append(
                        f"<p><strong>&#9888; {_he(ref['status'].upper())} REFERENCE "
                        f"(Trust Verifier):</strong> {_he(ref.get('citation', ''))}<br>"
                        f"<em>{_he(ref.get('note', ''))}</em></p>"
                    )

        parts.append("<hr>")

    # Additional verifier issues
    if additional_issues:
        parts.append("<h2>&#10133; Additional Issues &mdash; Independent Verifier</h2>")
        for issue in additional_issues:
            severity = issue.get("severity", "minor")
            emoji = severity_emoji(severity)
            line_range = fmt_line_range(issue)
            type_val = issue.get("type", "")
            title = issue.get("title", "")
            body = issue.get("body", "")
            parts.append(
                f"<h3>&#10133; {_he(emoji)} {_he(severity.upper())} [{_he(line_range)}] "
                f"&mdash; {_he(type_val.title())}: {_he(title)}</h3>"
            )
            parts.append(f"<p>{_he(body)}</p>")
            parts.append("<hr>")

    # Trust verification summary
    if trust_verification:
        parts.append("<h2>&#9888; Trust Verification &mdash; Reference Check</h2>")
        parts.append(_trust_summary_table(trust_verification, trust_counts))

        unverified_refs = [
            r for r in trust_verification.get("references_checked", [])
            if r.get("status") in ("unverified", "suspicious")
        ]
        if unverified_refs:
            parts.append("<h3>&#9888; Unverified / Suspicious References</h3>")
            for ref in unverified_refs:
                status_label = ref.get("status", "").upper()
                parts.append(
                    f"<p><strong>{_he(ref.get('citation', ''))}</strong> "
                    f"(cited in {_he(ref.get('cited_in', ''))})<br>"
                    f"[{_he(status_label)}] {_he(ref.get('note', ''))}</p>"
                )
        else:
            tc = trust_counts
            parts.append(f"<p>&#9989; All {tc['total']} references verified.</p>")

    return "\n".join(parts)


def _metadata_table(
    meta: dict,
    verification: Optional[dict],
    trust_verification: Optional[dict],
    annotation_counts: dict,
    verification_counts: dict,
    trust_counts: dict,
) -> str:
    rows = []
    rows.append(
        f"<tr><td><code>Date</code></td><td><code>"
        f"{_he(fmt_datetime(meta.get('reviewed_at', '')))}"
        f"</code></td></tr>"
    )
    agents = meta.get("agents", [])
    rows.append(
        f"<tr><td><code>Reviewers</code></td><td><code>{len(agents)}</code></td></tr>"
    )
    for i, ag in enumerate(agents, 1):
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
    return "<table>\n" + "\n".join(rows) + "\n</table>"


def _trust_summary_table(trust_verification: dict, trust_counts: dict) -> str:
    tag = trust_verification.get("agent", {})
    tc = trust_counts
    rows = [
        f"<tr><td><code>Agent</code></td><td><code>"
        f"{_he(tag.get('role',''))} &mdash; {_he(tag.get('model',''))} &mdash; thinking: {_he(tag.get('thinking',''))}"
        f"</code></td></tr>",
        f"<tr><td><code>Checked</code></td><td><code>"
        f"{_he(fmt_datetime(trust_verification.get('verified_at', '')))}"
        f"</code></td></tr>",
        f"<tr><td><code>Total</code></td><td><code>{tc['total']} references checked</code></td></tr>",
        f"<tr><td><code>Result</code></td><td><code>"
        f"{tc['verified']} verified / {tc['unverified']} unverified / {tc['suspicious']} suspicious"
        f"</code></td></tr>",
    ]
    return "<table>\n" + "\n".join(rows) + "\n</table>"


# ---------------------------------------------------------------------------
# Note splitting
# ---------------------------------------------------------------------------

def split_note_if_needed(html_content: str, limit: int = 190000) -> List[str]:
    """Split HTML content into 1 or 2 parts based on size limit."""
    if len(html_content) <= limit:
        return [html_content]

    # Simple split: find the boundary between critical/major and minor/suggestion
    # by looking for h3 tags with MINOR or SUGGESTION
    # Part 1: everything up to first minor/suggestion; Part 2: the rest
    import re

    # Find first minor/suggestion h3
    pattern = re.compile(
        r"(<h3>[^<]*(?:MINOR|SUGGESTION)[^<]*</h3>)",
        re.IGNORECASE,
    )
    m = pattern.search(html_content)
    if m:
        split_point = m.start()
    else:
        # No clean split point: split at ~limit
        split_point = limit

    # Extract metadata table from beginning
    meta_end = html_content.find("<hr>")
    if meta_end == -1:
        meta_section = ""
        meta_end = 0
    else:
        meta_section = html_content[:meta_end + 4]  # include <hr>

    part1 = html_content[:split_point].rstrip()
    if not part1.endswith("<hr>"):
        part1 += "\n<hr>"
    part1 = (
        "<h2>Annotated Review &mdash; Part 1/2 (Critical + Major)</h2>\n"
        + (meta_section if not part1.startswith(meta_section) else "")
        + part1
    )

    part2 = (
        "<h2>Annotated Review &mdash; Part 2/2 (Minor + Suggestions + Verifier Additions)</h2>\n"
        + meta_section
        + html_content[split_point:]
    )

    return [part1, part2]


# ---------------------------------------------------------------------------
# Zotero integration
# ---------------------------------------------------------------------------

def create_zotero_note(
    parent_key: str,
    html_content: str,
    date_str: str,
    zotero_config_path: str,
) -> dict:
    """Create a child note attached to parent_key in Zotero."""
    from lib.config import load_config
    from lib.zotero_client import ZoteroClient

    config = load_config(config_path=zotero_config_path, require=["ZOTERO_API_KEY"])
    zot_client = ZoteroClient(config)

    template = zot_client.zot.item_template("note")
    template["note"] = html_content
    template["parentItem"] = parent_key
    template["tags"] = [
        {"tag": "annotated-review"},
        {"tag": f"reviewed-{date_str}"},
    ]

    result = zot_client._retry(zot_client.zot.create_items, [template])
    return result


def get_existing_review_notes(parent_key: str, zotero_config_path: str) -> list:
    """Return all child notes with the 'annotated-review' tag."""
    from lib.config import load_config
    from lib.zotero_client import ZoteroClient

    config = load_config(config_path=zotero_config_path, require=["ZOTERO_API_KEY"])
    zot_client = ZoteroClient(config)

    children = zot_client.children(parent_key)
    existing = [
        c for c in children
        if c["data"].get("itemType") == "note"
        and "annotated-review" in [t["tag"] for t in c["data"].get("tags", [])]
    ]
    return existing


def tag_parent_item(parent_key: str, zotero_config_path: str) -> None:
    """Add 'reviewed' tag to the parent item."""
    from lib.config import load_config
    from lib.zotero_client import ZoteroClient

    config = load_config(config_path=zotero_config_path, require=["ZOTERO_API_KEY"])
    zot_client = ZoteroClient(config)

    parent = zot_client.get_item(parent_key)
    tags = parent["data"].get("tags", [])
    if not any(t["tag"] == "reviewed" for t in tags):
        tags.append({"tag": "reviewed"})
        parent["data"]["tags"] = tags
        zot_client.update_item(parent)
