#!/usr/bin/env python3
"""Entry point for annotated-review skill — CLI argument parsing + orchestration."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from typing import List, Optional, Tuple

# Ensure our skill dir is in path for sibling imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from critic import (
    validate_review,
    count_annotations,
    count_verification,
    count_trust,
    fmt_datetime,
)


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _output(data: dict) -> None:
    print(json.dumps(data, ensure_ascii=False))


def _progress(msg: str) -> None:
    print(f"[{msg}]", file=sys.stderr)


# ---------------------------------------------------------------------------
# Compilation helpers
# ---------------------------------------------------------------------------

def extract_latex_warnings(log_text: str) -> List[str]:
    """Extract non-fatal warning lines from LaTeX log."""
    warnings = []
    for line in log_text.splitlines():
        if "Warning" in line or "warning" in line:
            warnings.append(line.strip())
    return warnings[:20]  # cap to avoid flooding


def extract_compile_error(log_path: str) -> str:
    """Extract first fatal error block from compile.log."""
    try:
        lines = open(log_path, encoding="utf-8", errors="replace").readlines()
    except OSError:
        return "unknown compile error (log not found)"

    capturing = False
    result: List[str] = []
    for line in lines:
        if line.startswith("!"):
            capturing = True
        if capturing:
            result.append(line.rstrip())
            if len(result) >= 6:
                break
    return "\n".join(result) if result else "unknown compile error"


def attempt_autofix(annotated_dir: str, root_tex_name: str, error: str) -> Optional[str]:
    """Return fix description if a fix was applied, else None."""
    root_path = os.path.join(annotated_dir, root_tex_name)
    try:
        content = open(root_path, encoding="utf-8", errors="replace").read()
    except OSError:
        return None

    if "Option clash" in error and "todonotes" in error:
        new = content.replace(
            "\\documentclass",
            "\\PassOptionsToPackage{colorinlistoftodos,prependcaption}{todonotes}\n\\documentclass",
            1,
        )
        if new != content:
            with open(root_path, "w", encoding="utf-8") as f:
                f.write(new)
            return "PassOptionsToPackage for todonotes"

    if "Runaway argument" in error or "Paragraph ended" in error:
        # re-escape retry signal — already escaped, but force a recompile
        return "re-escape retry"

    return None


def compile_latex(
    annotated_dir: str,
    root_tex_name: str,
) -> Tuple[Optional[str], Optional[str], List[str]]:
    """Run latexmk. Returns (pdf_path_or_none, error_or_none, warnings_list)."""
    import subprocess

    if not (
        shutil.which("pdflatex")
        or shutil.which("lualatex")
        or shutil.which("xelatex")
    ):
        return None, "engine not found", []

    if not shutil.which("latexmk"):
        return None, "latexmk not found", []

    log_path = os.path.join(annotated_dir, "compile.log")
    try:
        result = subprocess.run(
            ["latexmk", "-pdf", "-interaction=nonstopmode", "-f", root_tex_name],
            cwd=annotated_dir,
            capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        return None, "compile timed out after 120s", []
    except Exception as e:
        return None, f"compile failed: {e}", []

    log_content = result.stdout + result.stderr
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(log_content)

    pdf_name = root_tex_name.replace(".tex", ".pdf")
    pdf_path = os.path.join(annotated_dir, pdf_name)

    if os.path.exists(pdf_path):
        warnings = extract_latex_warnings(log_content)
        return pdf_path, None, warnings

    # Fatal error — attempt auto-fix and retry once
    error = extract_compile_error(log_path)
    fixed = attempt_autofix(annotated_dir, root_tex_name, error)
    if fixed:
        try:
            result2 = subprocess.run(
                ["latexmk", "-pdf", "-interaction=nonstopmode", "-f", root_tex_name],
                cwd=annotated_dir,
                capture_output=True, text=True, timeout=120,
            )
            log2 = result2.stdout + result2.stderr
            with open(log_path, "a", encoding="utf-8") as f:
                f.write("\n--- AUTO-FIX RETRY ---\n" + log2)
            if os.path.exists(pdf_path):
                return pdf_path, None, [f"Auto-fix applied: {fixed}"]
        except Exception:
            pass

    return None, error, []


# ---------------------------------------------------------------------------
# Telegram send helper
# ---------------------------------------------------------------------------

def _send_file(
    file_path: str,
    channel: str,
    target: str,
    title: str = "",
) -> dict:
    """Send file via send_file.sh. Fall back to reporting path."""
    import subprocess

    # Look for send_file.sh in the zotero skill dir
    zotero_dir = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "zotero"
    )
    script = os.path.join(zotero_dir, "send_file.sh")

    if not os.path.exists(script):
        # Fallback: just report path
        return {"status": "ok", "file_path": file_path, "note": "send_file.sh not available"}

    try:
        proc = subprocess.run(
            [script, channel, target, file_path, title or os.path.basename(file_path)],
            capture_output=True, text=True, timeout=180,
        )
        try:
            return json.loads(proc.stdout.strip())
        except (json.JSONDecodeError, ValueError):
            msg = proc.stdout.strip() or proc.stderr.strip() or f"exit {proc.returncode}"
            return {"status": "error", "message": msg}
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "send_file.sh timed out (180s)"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ---------------------------------------------------------------------------
# Zotero helpers
# ---------------------------------------------------------------------------

def _resolve_zotero_key(
    args,
    zotero_config_path: str,
) -> Optional[str]:
    """Resolve --zotero-key or --zotero-doi to a Zotero item key."""
    if not os.path.exists(zotero_config_path):
        return None

    zotero_dir = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "zotero"
    )
    if zotero_dir not in sys.path:
        sys.path.insert(0, zotero_dir)

    try:
        from lib.config import load_config
        from lib.zotero_client import ZoteroClient
    except ImportError:
        return None

    config = load_config(config_path=zotero_config_path, require=["ZOTERO_API_KEY"])
    zot = ZoteroClient(config)

    if args.zotero_key:
        return args.zotero_key

    if args.zotero_doi:
        item = zot.search_by_doi(args.zotero_doi)
        if item:
            return item["key"]
        return None

    return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="annotated-review: produce annotated PDF/LaTeX/Zotero from review JSON"
    )
    parser.add_argument("--review-file", metavar="FILE", help="Path to review JSON file")
    parser.add_argument("--source", metavar="DIR", help="LaTeX source directory")
    parser.add_argument("--pdf", metavar="FILE", help="PDF file path")
    parser.add_argument("--zotero-key", metavar="KEY", help="Zotero item key (explicit opt-in)")
    parser.add_argument("--zotero-doi", metavar="DOI", help="Zotero item DOI (explicit opt-in)")
    parser.add_argument(
        "--merged-pdf", action="store_true",
        help="Produce merged PDF (annotated + companion)"
    )
    parser.add_argument(
        "--store-annotated", action="store_true",
        help="Upload annotated PDF to Zotero WebDAV (requires --zotero-key/--doi)"
    )
    parser.add_argument(
        "--send", nargs=2, metavar=("CHANNEL", "SENDER_ID"),
        help="Send output file: --send telegram <sender_id>"
    )
    parser.add_argument(
        "--precompile", action="store_true",
        help="Pre-compile only: inject lineno + compile, print lined_preview.pdf path"
    )
    parser.add_argument(
        "--precompile-only", action="store_true",
        help="Alias for --precompile"
    )
    parser.add_argument(
        "--paper-title", metavar="TITLE", default="",
        help="Paper title for metadata header"
    )

    args = parser.parse_args()

    # ── Pre-compile mode ────────────────────────────────────────────────────
    if args.precompile or args.precompile_only:
        if not args.source:
            _output({"status": "error", "message": "--source is required for --precompile", "code": "ARGS"})
            sys.exit(1)
        _progress("Pre-compiling with line numbers...")
        from latex_annotator import precompile_preview
        pdf_path, error = precompile_preview(args.source)
        if pdf_path:
            _output({"status": "ok", "action": "precompile", "pdf": pdf_path})
            print(pdf_path)  # also print bare path for easy shell capture
        else:
            _output({"status": "error", "action": "precompile", "error": error})
            sys.exit(1)
        return

    # ── Normal review mode ──────────────────────────────────────────────────
    if not args.review_file:
        parser.print_help()
        sys.exit(1)

    if not os.path.exists(args.review_file):
        _output({
            "status": "error",
            "message": f"Review file not found: {args.review_file}",
            "code": "FILE_NOT_FOUND",
        })
        sys.exit(1)

    # Load review JSON
    try:
        with open(args.review_file, encoding="utf-8") as f:
            review_data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        _output({
            "status": "error",
            "message": f"Failed to load review file: {e}",
            "code": "JSON_PARSE_ERROR",
        })
        sys.exit(1)

    # Validate
    errors = validate_review(review_data)
    if errors:
        _output({
            "status": "error",
            "message": "Review JSON validation failed",
            "code": "VALIDATION_ERROR",
            "errors": errors,
        })
        sys.exit(1)

    meta = review_data.get("meta", {})
    annotations = review_data.get("annotations", [])
    verification = review_data.get("verification")
    trust_verification = review_data.get("trust_verification")

    annotation_counts = count_annotations(annotations)
    verification_counts = count_verification(verification)
    trust_counts = count_trust(trust_verification)

    paper_title = args.paper_title or ""
    warnings: List[str] = []

    outputs = {
        "latex_pdf": None,
        "pdf_markup": None,
        "companion_html": None,
        "companion_pdf": None,
        "zotero_note_key": None,
        "zotero_parent_key": None,
        "stored_attachment": None,
    }
    compile_error = None
    pre_compile_error = None

    # ── LaTeX source path ───────────────────────────────────────────────────
    if args.source:
        if not os.path.isdir(args.source):
            _output({
                "status": "error",
                "message": f"Source directory not found: {args.source}",
                "code": "FILE_NOT_FOUND",
            })
            sys.exit(1)

        _progress("Annotating LaTeX source tree...")
        from latex_annotator import annotate_tree, find_root_tex
        try:
            annotated_dir = annotate_tree(args.source, review_data)
        except Exception as e:
            warnings.append(f"LaTeX annotation failed: {e}")
            annotated_dir = None

        if annotated_dir:
            root_tex = find_root_tex(annotated_dir)
            if root_tex:
                root_tex_name = os.path.basename(root_tex)
                _progress(f"Compiling annotated LaTeX ({root_tex_name})...")
                pdf_path, err, compile_warnings = compile_latex(annotated_dir, root_tex_name)
                if pdf_path:
                    outputs["latex_pdf"] = pdf_path
                    warnings.extend(compile_warnings)
                    _progress(f"Annotated PDF: {pdf_path}")
                else:
                    compile_error = err
                    _progress(f"LaTeX compile failed: {err}")
                    warnings.append(f"LaTeX compile failed: {err}")
            else:
                compile_error = "No root .tex file found in annotated directory"
                warnings.append(compile_error)
        else:
            compile_error = "LaTeX annotation step failed"

    # ── PDF path ─────────────────────────────────────────────────────────────
    if args.pdf:
        if not os.path.exists(args.pdf):
            _output({
                "status": "error",
                "message": f"PDF not found: {args.pdf}",
                "code": "FILE_NOT_FOUND",
            })
            sys.exit(1)

        _progress("Marking up PDF...")
        from pdf_annotator import annotate_pdf
        result = annotate_pdf(args.pdf, review_data, paper_title=paper_title)
        if "error" in result:
            warnings.append(f"PDF markup failed: {result.get('message', result['error'])}")
        else:
            outputs["pdf_markup"] = result.get("pdf")
            _progress(f"Marked PDF: {outputs['pdf_markup']}")

    # ── Companion HTML (always produced) ────────────────────────────────────
    companion_dir = os.path.dirname(args.pdf or args.source or args.review_file)
    companion_path = os.path.join(companion_dir, "annotated_review_companion.html")
    _progress("Generating companion HTML...")
    try:
        from pdf_annotator import produce_companion_html
        produce_companion_html(review_data, companion_path, paper_title=paper_title)
        outputs["companion_html"] = companion_path
        _progress(f"Companion HTML: {companion_path}")
    except Exception as e:
        warnings.append(f"Companion HTML failed: {e}")

    # ── Merged PDF ───────────────────────────────────────────────────────────
    if args.merged_pdf and (outputs["latex_pdf"] or outputs["pdf_markup"]):
        best_pdf = outputs["latex_pdf"] or outputs["pdf_markup"]
        if best_pdf:
            merged_path = best_pdf.replace(".pdf", "_merged.pdf")
            try:
                from pdf_annotator import produce_merged_pdf
                result_path = produce_merged_pdf(best_pdf, companion_path, merged_path)
                if result_path:
                    outputs["companion_pdf"] = result_path
            except Exception as e:
                warnings.append(f"Merged PDF failed: {e}")

    # ── Zotero integration (strict opt-in) ────────────────────────────────────
    if args.zotero_key or args.zotero_doi:
        # Locate config
        zotero_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "zotero"
        )
        zotero_config_path = os.path.join(zotero_dir, "config.json")

        if not os.path.exists(zotero_config_path):
            warnings.append("Zotero config not found — skipping Zotero integration")
        else:
            if zotero_dir not in sys.path:
                sys.path.insert(0, zotero_dir)
            try:
                parent_key = _resolve_zotero_key(args, zotero_config_path)
                if not parent_key:
                    warnings.append("Could not resolve Zotero item key — skipping Zotero")
                else:
                    outputs["zotero_parent_key"] = parent_key
                    _progress(f"Zotero parent key: {parent_key}")

                    # Build note HTML
                    from zotero_note import (
                        build_note_html, split_note_if_needed,
                        create_zotero_note, tag_parent_item,
                    )
                    html_content = build_note_html(review_data, paper_title=paper_title)
                    parts = split_note_if_needed(html_content)

                    # Get date string for tag
                    reviewed_at = meta.get("reviewed_at", "")
                    date_str = reviewed_at[:10] if reviewed_at else datetime.now(timezone.utc).strftime("%Y-%m-%d")

                    note_keys = []
                    for part_idx, part_html in enumerate(parts):
                        _progress(f"Creating Zotero note part {part_idx + 1}/{len(parts)}...")
                        result = create_zotero_note(
                            parent_key, part_html, date_str, zotero_config_path
                        )
                        if result and "successful" in result:
                            note_item = list(result["successful"].values())[0]
                            note_key = note_item.get("key", note_item.get("data", {}).get("key", ""))
                            note_keys.append(note_key)

                    if note_keys:
                        outputs["zotero_note_key"] = note_keys[0] if len(note_keys) == 1 else note_keys
                        # Tag parent item
                        try:
                            tag_parent_item(parent_key, zotero_config_path)
                        except Exception as e:
                            warnings.append(f"Failed to tag parent item: {e}")

                    # Store annotated PDF if requested
                    if args.store_annotated:
                        annotated_pdf = outputs.get("latex_pdf") or outputs.get("pdf_markup")
                        if annotated_pdf and os.path.exists(annotated_pdf):
                            try:
                                from lib.webdav import WebDAVClient
                                from lib.config import load_config
                                from lib.zotero_client import ZoteroClient

                                config = load_config(
                                    config_path=zotero_config_path,
                                    require=["ZOTERO_API_KEY"],
                                )
                                zot_client = ZoteroClient(config)

                                att_template = zot_client.zot.item_template("attachment", "imported_file")
                                fname = os.path.basename(annotated_pdf)
                                att_template["title"] = fname
                                att_template["filename"] = fname
                                att_template["parentItem"] = parent_key
                                att_template["contentType"] = "application/pdf"
                                att_template["tags"] = [{"tag": "annotated-review-pdf"}]
                                att_result = zot_client._retry(
                                    zot_client.zot.create_items, [att_template]
                                )
                                att_key = None
                                if att_result and "successful" in att_result:
                                    att_item = list(att_result["successful"].values())[0]
                                    att_key = att_item.get("key", att_item.get("data", {}).get("key", ""))

                                if att_key and config.get("webdav_url") and config.get("WEBDAV_PASSWORD"):
                                    webdav = WebDAVClient(config)
                                    webdav.upload(att_key, annotated_pdf, fname)
                                    outputs["stored_attachment"] = att_key
                                    _progress(f"Stored annotated PDF as attachment {att_key}")
                            except Exception as e:
                                warnings.append(f"Failed to store annotated PDF: {e}")
                        else:
                            warnings.append("--store-annotated: no annotated PDF available")

            except Exception as e:
                warnings.append(f"Zotero integration failed: {e}")

    # ── Send best available output ────────────────────────────────────────────
    if args.send:
        channel, sender_id = args.send
        best_file = (
            outputs.get("latex_pdf")
            or outputs.get("pdf_markup")
            or outputs.get("companion_html")
        )
        if best_file:
            _progress(f"Sending {os.path.basename(best_file)} to {channel} {sender_id}...")
            send_result = _send_file(best_file, channel, sender_id, paper_title)
            if send_result.get("status") == "error":
                warnings.append(f"Send failed: {send_result.get('message', '')}")
        else:
            warnings.append("Nothing to send — all outputs failed")

    # ── Determine status ──────────────────────────────────────────────────────
    any_output = any(
        v for k, v in outputs.items()
        if k not in ("zotero_parent_key",)
    )
    status = "ok" if any_output else "error"

    _output({
        "status": status,
        "action": "review",
        "paper_title": paper_title,
        "annotation_count": annotation_counts,
        "verification_count": verification_counts,
        "trust_count": trust_counts,
        "outputs": outputs,
        "compile_error": compile_error,
        "pre_compile_error": pre_compile_error,
        "warnings": warnings,
    })

    if status == "error":
        sys.exit(1)


if __name__ == "__main__":
    main()
