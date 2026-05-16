"""Inserts lineno + todonotes + metadata box into a LaTeX source tree copy."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from typing import List, Optional, Tuple

from critic import (
    severity_color_latex,
    status_color_latex,
    status_emoji,
    fmt_datetime,
    fmt_line_range,
)

try:
    from pylatexenc.latexencode import utf8tolatex
except ImportError:
    def utf8tolatex(s, non_ascii_only=False, **kwargs):  # type: ignore
        return s


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------

def find_root_tex(source_dir: str) -> Optional[str]:
    """Return path to the .tex file containing \\documentclass, or None."""
    for dirpath, _dirs, files in os.walk(source_dir):
        for fname in sorted(files):
            if fname.endswith(".tex"):
                fpath = os.path.join(dirpath, fname)
                try:
                    content = open(fpath, encoding="utf-8", errors="replace").read()
                    if "\\documentclass" in content:
                        return fpath
                except OSError:
                    pass
    return None


def find_all_tex(source_dir: str) -> List[str]:
    """Walk source_dir recursively, collect all .tex files."""
    result = []
    for dirpath, _dirs, files in os.walk(source_dir):
        for fname in sorted(files):
            if fname.endswith(".tex"):
                result.append(os.path.join(dirpath, fname))
    return result


# ---------------------------------------------------------------------------
# Preamble / document-start injection
# ---------------------------------------------------------------------------

def _detect_two_column(tex_content: str) -> bool:
    """Return True if the document uses two-column layout."""
    # Check documentclass options
    m = re.search(r"\\documentclass\s*\[([^\]]*)\]", tex_content)
    if m:
        opts = m.group(1)
        if "twocolumn" in opts:
            return True
    # Check for \twocolumn command
    if "\\twocolumn" in tex_content:
        return True
    return False


def inject_preamble(tex_content: str, two_column: bool = False) -> str:
    """Add todonotes / lineno / xcolor after \\documentclass line if not already present."""
    if "todonotes" in tex_content and "lineno" in tex_content:
        return tex_content  # already injected

    two_col = two_column or _detect_two_column(tex_content)

    todo_pkg = (
        "\\usepackage[colorinlistoftodos,prependcaption,textwidth=\\columnwidth]{todonotes}"
        if two_col
        else "\\usepackage[colorinlistoftodos,prependcaption]{todonotes}"
    )

    inject_block = "\n".join([
        todo_pkg,
        "\\usepackage{lineno}",
        "\\usepackage{xcolor}",
        "",
    ])

    # Insert after the \documentclass{...} line (handle optional args too)
    # Match \documentclass[...]{...} or \documentclass{...}
    pattern = r"(\\documentclass(?:\[[^\]]*\])?\{[^}]*\}[^\n]*\n)"
    m = re.search(pattern, tex_content)
    if m:
        insert_pos = m.end()
        # Don't double-inject
        if "\\usepackage{lineno}" not in tex_content:
            tex_content = tex_content[:insert_pos] + inject_block + tex_content[insert_pos:]
    return tex_content


def _escape_for_latex(text: str) -> str:
    """Escape a plain-text string for use inside LaTeX."""
    return utf8tolatex(text, non_ascii_only=False)


def build_metadata_box(
    meta: dict,
    verification: Optional[dict],
    trust_verification: Optional[dict],
    annotation_counts: dict,
    verification_counts: dict,
    trust_counts: dict,
) -> str:
    """Return the full \\noindent\\colorbox{gray!12}{...} metadata block."""
    lines: List[str] = []
    lines.append(r"\noindent\colorbox{gray!12}{%")
    lines.append(r"  \begin{minipage}{\dimexpr\linewidth-2\fboxsep\relax}")
    lines.append(r"  \vspace{3pt}%")
    lines.append(r"  {\ttfamily\footnotesize%")
    lines.append(r"  \textbf{ANNOTATED REVIEW}\\[2pt]%")

    # Date
    date_str = fmt_datetime(meta.get("reviewed_at", ""))
    lines.append(f"  Date:\\hspace{{2.6em}}{_escape_for_latex(date_str)}\\\\%")

    # Reviewer count
    agents = meta.get("agents", [])
    lines.append(f"  Reviewers:\\hspace{{0.4em}}{len(agents)}\\\\%")

    # Each reviewer
    for i, ag in enumerate(agents, 1):
        role = _escape_for_latex(ag.get("role", ""))
        model = _escape_for_latex(ag.get("model", ""))
        thinking = _escape_for_latex(ag.get("thinking", ""))
        lines.append(
            f"  \\hspace{{2em}}[{i}] {role}\\hfill {model}\\quad thinking: {thinking}\\\\%"
        )

    # Verifier
    if verification:
        vag = verification.get("agent", {})
        vrole = _escape_for_latex(vag.get("role", ""))
        vmodel = _escape_for_latex(vag.get("model", ""))
        vthinking = _escape_for_latex(vag.get("thinking", ""))
        verified_at = fmt_datetime(verification.get("verified_at", ""))
        lines.append(
            f"  Verifier:\\hspace{{1em}}{vrole}\\hfill {vmodel}\\\\%"
        )
        lines.append(
            f"  \\hspace{{6.1em}}verified {_escape_for_latex(verified_at)}\\hfill thinking: {vthinking}\\\\%"
        )

    # Trust
    if trust_verification:
        tag = trust_verification.get("agent", {})
        trole = _escape_for_latex(tag.get("role", ""))
        tmodel = _escape_for_latex(tag.get("model", ""))
        tthinking = _escape_for_latex(tag.get("thinking", ""))
        checked_at = fmt_datetime(trust_verification.get("verified_at", ""))
        lines.append(
            f"  Trust:\\hspace{{2.1em}}{trole}\\hfill {tmodel}\\\\%"
        )
        lines.append(
            f"  \\hspace{{6.1em}}checked {_escape_for_latex(checked_at)}\\hfill thinking: {tthinking}\\\\%"
        )

    # Focus
    focus = _escape_for_latex(meta.get("focus", "all"))
    lines.append(f"  Focus:\\hspace{{2.4em}}{focus}\\\\%")

    # Issues
    ac = annotation_counts
    lines.append(
        f"  Issues:\\hspace{{1.8em}}{ac['critical']} critical~~/ "
        f"~~{ac['major']} major~~/ ~~{ac['minor']} minor~~/ ~~{ac['suggestion']} suggestion\\\\%"
    )

    # Verified (if verification present)
    if verification:
        vc = verification_counts
        lines.append(
            f"  Verified:\\hspace{{1em}}{vc['confirmed']} confirmed~~/ "
            f"~~{vc['disputed']} disputed~~/ ~~{vc['partial']} partial~~/ ~~{vc['additions']} additions\\\\%"
        )

    # Trust summary (if trust_verification present)
    if trust_verification:
        tc = trust_counts
        lines.append(
            f"  Trust:\\hspace{{2.1em}}{tc['verified']} verified~~/ "
            f"~~{tc['unverified']} unverified~~/ ~~{tc['suspicious']} suspicious%"
        )
    else:
        # Remove trailing \\ from last line if needed
        if lines and lines[-1].endswith("\\\\%"):
            lines[-1] = lines[-1][:-3] + "%"

    lines.append(r"  }%")
    lines.append(r"  \vspace{3pt}%")
    lines.append(r"  \end{minipage}%")
    lines.append(r"}")
    return "\n".join(lines)


def inject_document_start(
    tex_content: str,
    meta: dict,
    verification: Optional[dict],
    trust_verification: Optional[dict],
    annotation_counts: dict,
    verification_counts: dict,
    trust_counts: dict,
) -> str:
    """After \\begin{document}, inject linenumbers + metadata box + \\listoftodos."""
    inject_lines = [
        "\\linenumbers",
        "\\setlength{\\linenumbersep}{2pt}",
        "% ── Review metadata header ──────────────────────────────────────",
        build_metadata_box(
            meta, verification, trust_verification,
            annotation_counts, verification_counts, trust_counts
        ),
        "\\vspace{1em}",
        "% ────────────────────────────────────────────────────────────────",
        "\\listoftodos\\newpage",
    ]
    inject_block = "\n".join(inject_lines) + "\n"

    # Find \begin{document}
    m = re.search(r"(\\begin\{document\}\s*\n)", tex_content)
    if m:
        insert_pos = m.end()
        tex_content = tex_content[:insert_pos] + inject_block + tex_content[insert_pos:]
    return tex_content


# ---------------------------------------------------------------------------
# Conflict + math wrapping
# ---------------------------------------------------------------------------

def handle_todo_conflict(tex_content: str) -> Tuple[str, bool]:
    """Check if \\todo is defined before todonotes loads.

    If conflict: inject alias after todonotes usepackage.
    Returns (modified_content, has_conflict).
    """
    has_conflict = bool(
        re.search(r"\\(?:new|renew|provide)command\s*\{?\\todo\b", tex_content)
    )
    if has_conflict:
        # Inject alias definition right after todonotes usepackage line
        alias_def = "\\NewDocumentCommand{\\reviewtodo}{O{} m}{\\todo[#1]{#2}}\n"
        m = re.search(r"(\\usepackage.*?todonotes.*?\n)", tex_content)
        if m:
            insert_pos = m.end()
            tex_content = tex_content[:insert_pos] + alias_def + tex_content[insert_pos:]
    return tex_content, has_conflict


def wrap_display_math(tex_content: str) -> str:
    """Wrap display math environments in \\begin{linenomath*}...\\end{linenomath*}."""
    envs = [
        "align", "align\\*",
        "equation", "equation\\*",
        "gather", "gather\\*",
        "multline", "multline\\*",
        "flalign", "flalign\\*",
        "alignat", "alignat\\*",
    ]
    for env_pattern in envs:
        # Build plain env name for use in replacement
        env_name_raw = env_pattern.replace("\\*", "*")
        begin_pat = r"(\\begin\{" + env_pattern + r"\})"
        end_pat = r"(\\end\{" + env_pattern + r"\})"

        # Skip if already wrapped
        already_wrapped_pat = r"\\begin\{linenomath\*\}\s*\\begin\{" + env_pattern + r"\}"
        if re.search(already_wrapped_pat, tex_content):
            continue

        tex_content = re.sub(
            begin_pat,
            r"\\begin{linenomath*}\n\1",
            tex_content,
        )
        tex_content = re.sub(
            end_pat,
            r"\1\n\\end{linenomath*}",
            tex_content,
        )

    return tex_content


# ---------------------------------------------------------------------------
# Todo building
# ---------------------------------------------------------------------------

_TYPE_LABELS = {
    "logic": "Logic Error",
    "math": "Math Error",
    "consistency": "Consistency",
    "notation": "Notation",
    "presentation": "Presentation",
    "missing": "Missing",
    "unsupported": "Unsupported",
}


def build_reviewer_todo(ann: dict, todo_macro: str = "\\todo") -> str:
    """Build \\todo[inline,color=<color>]{...} for a reviewer annotation."""
    color = severity_color_latex(ann.get("severity", "minor"))
    line_range = fmt_line_range(ann)
    severity_upper = ann.get("severity", "").upper()
    type_label = _TYPE_LABELS.get(ann.get("type", ""), ann.get("type", "").title())
    title = ann.get("title", "")
    body = ann.get("body", "")

    label = f"[{severity_upper}, {line_range} — {type_label}: {_escape_for_latex(title)}]"
    body_escaped = _escape_for_latex(body)

    return (
        f"{todo_macro}[inline,color={color}]{{%\n"
        f"  \\textbf{{{_escape_for_latex(label)}}}\\\\[2pt]\n"
        f"  {body_escaped}\n"
        f"}}"
    )


def build_verifier_todo(ann: dict, result: dict, todo_macro: str = "\\todo") -> str:
    """Build verifier response \\todo."""
    status = result.get("status", "")
    color = status_color_latex(status)
    emoji = status_emoji(status)
    status_upper = status.upper()
    line_range = fmt_line_range(ann)
    comment = result.get("comment", "")
    comment_escaped = _escape_for_latex(comment)

    label = f"[{emoji} {status_upper} — Independent Verifier, {line_range}]"

    return (
        f"{todo_macro}[inline,color={color}]{{%\n"
        f"  \\textbf{{{_escape_for_latex(label)}}}\\\\[2pt]\n"
        f"  {comment_escaped}\n"
        f"}}"
    )


def build_trust_warning_todo(ref: dict, todo_macro: str = "\\todo") -> str:
    """Build trust warning \\todo for unverified/suspicious references."""
    status = ref.get("status", "unverified")
    citation = ref.get("citation", "")
    note = ref.get("note", "")

    if status == "unverified":
        color = "red!40"
        label = "[\\textbf{\\textwarning} UNVERIFIED REFERENCE — Trust Verifier]"
        label_text = "[WARNING UNVERIFIED REFERENCE -- Trust Verifier]"
    else:  # suspicious
        color = "orange!35"
        label_text = "[WARNING SUSPICIOUS REFERENCE -- Trust Verifier]"
        label = "[\\textbf{\\textwarning} SUSPICIOUS REFERENCE — Trust Verifier]"

    citation_escaped = _escape_for_latex(citation)
    note_escaped = _escape_for_latex(note)

    return (
        f"{todo_macro}[inline,color={color}]{{%\n"
        f"  \\textbf{{{_escape_for_latex(label_text)}}}\\\\[2pt]\n"
        f"  Citation: ``{citation_escaped}''\\\\[2pt]\n"
        f"  {note_escaped}\n"
        f"}}"
    )


def build_verifier_addition_todo(issue: dict, todo_macro: str = "\\todo") -> str:
    """Build verifier addition \\todo."""
    color = "cyan!20"
    line_range = fmt_line_range(issue)
    severity_upper = issue.get("severity", "").upper()
    title = issue.get("title", "")
    body = issue.get("body", "")

    label = f"[+ VERIFIER ADDITION -- {severity_upper}, {line_range}]"
    body_escaped = _escape_for_latex(body)

    return (
        f"{todo_macro}[inline,color={color}]{{%\n"
        f"  \\textbf{{{_escape_for_latex(label)}}}\\\\[2pt]\n"
        f"  {body_escaped}\n"
        f"}}"
    )


# ---------------------------------------------------------------------------
# Insertion logic
# ---------------------------------------------------------------------------

def find_insertion_point(tex_lines: List[str], quote: str) -> Optional[int]:
    """Search for quote[:40] as substring in lines; return 0-based line index."""
    search_str = quote[:40]
    for i, line in enumerate(tex_lines):
        if search_str in line:
            return i
    return None


def annotate_file(
    tex_content: str,
    annotations: list,
    verification_results: dict,
    trust_refs: dict,
    additional_issues: list,
    todo_macro: str = "\\todo",
    file_path: Optional[str] = None,
    source_dir: Optional[str] = None,
) -> str:
    """Annotate a single .tex file content. Returns modified content."""
    lines = tex_content.split("\n")

    # Determine which annotations target this file
    def _ann_matches_file(ann: dict) -> bool:
        ann_file = ann.get("file")
        if ann_file is None:
            return True  # null file = all files
        if file_path is None:
            return True
        # Compare relative paths
        if source_dir:
            rel = os.path.relpath(file_path, source_dir)
            return rel == ann_file or os.path.basename(file_path) == os.path.basename(ann_file)
        return os.path.basename(file_path) == os.path.basename(ann_file)

    # Build list of (line_index, todos_to_insert) pairs
    # We insert bottom-up to avoid shifting line numbers
    insertions: List[Tuple[int, List[str]]] = []

    # Process primary annotations
    for i, ann in enumerate(annotations):
        if not _ann_matches_file(ann):
            continue

        line_idx = find_insertion_point(lines, ann.get("quote", ""))
        if line_idx is None:
            continue

        todos = []

        # 1. Reviewer todo
        todos.append(build_reviewer_todo(ann, todo_macro))

        # 2. Trust warnings for refs cited in reviewer body (after reviewer todo)
        ann_key = f"annotation_{i}"
        for ref in trust_refs.get(ann_key, []):
            if ref.get("status") in ("unverified", "suspicious"):
                todos.append(build_trust_warning_todo(ref, todo_macro))

        # 3. Verifier todo (if result exists)
        result = verification_results.get(i)
        if result:
            todos.append(build_verifier_todo(ann, result, todo_macro))

            # 4. Trust warnings for refs cited in verifier comment
            ver_key = f"verification_result_{i}"
            for ref in trust_refs.get(ver_key, []):
                if ref.get("status") in ("unverified", "suspicious"):
                    todos.append(build_trust_warning_todo(ref, todo_macro))

        insertions.append((line_idx, todos))

    # Process additional verifier issues targeting this file
    for issue in additional_issues:
        if not _ann_matches_file(issue):
            continue

        line_idx = find_insertion_point(lines, issue.get("quote", ""))
        if line_idx is None:
            continue

        todos = [build_verifier_addition_todo(issue, todo_macro)]
        insertions.append((line_idx, todos))

    # Sort insertions descending by line number (bottom-up insertion)
    insertions.sort(key=lambda x: x[0], reverse=True)

    # Perform insertions
    for line_idx, todos in insertions:
        insert_text = "\n".join(todos) + "\n"
        lines.insert(line_idx, insert_text)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tree annotation
# ---------------------------------------------------------------------------

def annotate_tree(source_dir: str, review_data: dict) -> str:
    """Copy source_dir to <source_dir>_annotated/ and inject all annotations.

    Returns path to the annotated directory.
    """
    annotated_dir = source_dir.rstrip("/").rstrip("\\") + "_annotated"

    # Remove existing annotated dir if present
    if os.path.exists(annotated_dir):
        shutil.rmtree(annotated_dir)
    shutil.copytree(source_dir, annotated_dir)

    meta = review_data.get("meta", {})
    annotations = review_data.get("annotations", [])
    verification = review_data.get("verification")
    trust_verification = review_data.get("trust_verification")

    from critic import count_annotations, count_verification, count_trust
    annotation_counts = count_annotations(annotations)
    verification_counts = count_verification(verification)
    trust_counts = count_trust(trust_verification)

    # Build verification results map: annotation_index -> result dict
    ver_results_map: dict = {}
    additional_issues: list = []
    if verification:
        for result in verification.get("results", []):
            idx = result.get("annotation_index")
            if idx is not None:
                ver_results_map[idx] = result
        additional_issues = verification.get("additional_issues", [])

    # Build trust refs map: cited_in -> list of refs
    trust_refs_map: dict = {}
    if trust_verification:
        for ref in trust_verification.get("references_checked", []):
            cited_in = ref.get("cited_in", "")
            if cited_in not in trust_refs_map:
                trust_refs_map[cited_in] = []
            trust_refs_map[cited_in].append(ref)

    # Find root tex file in annotated dir
    root_tex = find_root_tex(annotated_dir)
    if not root_tex:
        return annotated_dir

    # Process root tex: preamble + document start
    root_content = open(root_tex, encoding="utf-8", errors="replace").read()
    root_content = inject_preamble(root_content)
    root_content, has_conflict = handle_todo_conflict(root_content)
    root_content = wrap_display_math(root_content)
    todo_macro = "\\reviewtodo" if has_conflict else "\\todo"
    root_content = inject_document_start(
        root_content, meta, verification, trust_verification,
        annotation_counts, verification_counts, trust_counts,
    )

    # Annotate root tex
    root_content = annotate_file(
        root_content, annotations, ver_results_map, trust_refs_map, additional_issues,
        todo_macro=todo_macro, file_path=root_tex, source_dir=annotated_dir,
    )
    with open(root_tex, "w", encoding="utf-8") as f:
        f.write(root_content)

    # Process all other tex files
    for tex_path in find_all_tex(annotated_dir):
        if tex_path == root_tex:
            continue
        try:
            content = open(tex_path, encoding="utf-8", errors="replace").read()
            content = wrap_display_math(content)
            content = annotate_file(
                content, annotations, ver_results_map, trust_refs_map, additional_issues,
                todo_macro=todo_macro, file_path=tex_path, source_dir=annotated_dir,
            )
            with open(tex_path, "w", encoding="utf-8") as f:
                f.write(content)
        except OSError:
            pass

    return annotated_dir


# ---------------------------------------------------------------------------
# Pre-compile (lined preview)
# ---------------------------------------------------------------------------

def precompile_preview(source_dir: str) -> Tuple[Optional[str], Optional[str]]:
    """Copy source to <source_dir>_lined_preview/, inject lineno only, compile.

    Returns (pdf_path_or_none, error_summary_or_none).
    """
    preview_dir = source_dir.rstrip("/").rstrip("\\") + "_lined_preview"

    if os.path.exists(preview_dir):
        shutil.rmtree(preview_dir)
    shutil.copytree(source_dir, preview_dir)

    root_tex = find_root_tex(preview_dir)
    if not root_tex:
        return None, "No root .tex file found in source directory"

    # Read and inject minimal preamble (lineno only, no todonotes)
    content = open(root_tex, encoding="utf-8", errors="replace").read()

    # Inject \usepackage{lineno} after \documentclass
    if "\\usepackage{lineno}" not in content:
        pattern = r"(\\documentclass(?:\[[^\]]*\])?\{[^}]*\}[^\n]*\n)"
        m = re.search(pattern, content)
        if m:
            insert_pos = m.end()
            content = content[:insert_pos] + "\\usepackage{lineno}\n" + content[insert_pos:]

    # Wrap display math to avoid lineno conflicts
    content = wrap_display_math(content)

    # Inject \linenumbers after \begin{document}
    if "\\linenumbers" not in content:
        m = re.search(r"(\\begin\{document\}\s*\n)", content)
        if m:
            insert_pos = m.end()
            content = content[:insert_pos] + "\\linenumbers\n\\setlength{\\linenumbersep}{2pt}\n" + content[insert_pos:]

    with open(root_tex, "w", encoding="utf-8") as f:
        f.write(content)

    root_tex_name = os.path.basename(root_tex)

    if not (shutil.which("pdflatex") or shutil.which("lualatex") or shutil.which("xelatex")):
        return None, "LaTeX engine not found"
    if not shutil.which("latexmk"):
        return None, "latexmk not found"

    try:
        result = subprocess.run(
            ["latexmk", "-pdf", "-interaction=nonstopmode", "-f", root_tex_name],
            cwd=preview_dir,
            capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        return None, "pre-compile timed out after 120s"
    except Exception as e:
        return None, f"pre-compile failed: {e}"

    pdf_name = root_tex_name.replace(".tex", ".pdf")
    pdf_path = os.path.join(preview_dir, pdf_name)

    if os.path.exists(pdf_path):
        return pdf_path, None

    # Extract error summary
    log_output = result.stdout + result.stderr
    error_lines = []
    for line in log_output.splitlines():
        if line.startswith("!"):
            error_lines.append(line)
            if len(error_lines) >= 5:
                break
    error_summary = "\n".join(error_lines) if error_lines else "pre-compile failed (no PDF produced)"
    return None, error_summary
