"""Bounded UTF-8 TeX comment sanitation, never execution or privacy acceptance.

Qualified syntax uses ordinary TeX category codes. Dynamic token/category-code
changes and author-note macros require a separate review. Literal/verbatim text
is retained and still needs a content/privacy review.
"""
from __future__ import annotations

import re

POLICY = "tex-comments.v1"
OPAQUE_ENVS = {"verbatim", "verbatim*", "Verbatim", "lstlisting", "minted"}
REFUSED_COMMANDS = {
    "catcode", "endlinechar", "escapechar", "newlinechar", "scantokens",
    "csname", "obeylines", "obeyspaces", "makeactive", "ExplSyntaxOn",
    "directlua", "luaexec", "openin", "openout", "read", "write", "special",
    "todo", "marginpar", "marginnote", "added", "deleted", "replaced",
    "includecomment", "excludecomment", "DefineVerbatimEnvironment",
    "lstinline", "mintinline", "Verb", "SaveVerb", "EscVerb",
}
STATIC_INPUTS = {"input", "include", "bibliography", "addbibresource", "includegraphics"}


def sanitize_tex(text: str, *, keep_comment_lines: list[int] | None = None) -> dict:
    if not isinstance(text, str) or "\0" in text or "^^" in text:
        raise ValueError("tex-unsupported-token-encoding")
    keep = set(keep_comment_lines or [])
    if any(type(n) is not int or n < 1 for n in keep):
        raise ValueError("tex-invalid-comment-allowlist")
    out: list[str] = []; removed = 0; blocks = 0; i = 0; line = 1
    seen_keep: set[int] = set(); marker_stack: list[str] = []
    includes: list[str] = []
    while i < len(text):
        c = text[i]
        if c == "%":
            end = text.find("\n", i)
            if end < 0: end = len(text)
            comment = text[i + 1:end].rstrip("\r")
            stripped = comment.strip()
            marker = re.fullmatch(r"lax (begin ([A-Za-z_][\w.']*|lax-[1-9][0-9]*)|end)", stripped)
            if stripped.startswith("lax"):
                if marker is None or text[text.rfind("\n", 0, i) + 1:i].strip():
                    raise ValueError("tex-invalid-lax-marker")
                if stripped == "lax end":
                    if not marker_stack: raise ValueError("tex-unbalanced-lax-marker")
                    marker_stack.pop()
                else: marker_stack.append(stripped)
                out.append(text[i:end])
            elif line in keep:
                seen_keep.add(line); out.append(text[i:end])
            else:
                if re.search(r"copyright|SPDX-License-Identifier|licensed under", comment, re.I):
                    raise ValueError("tex-license-comment-needs-review")
                # Retain percent: deleting it introduces an end-of-line space.
                out.append("%")
                if comment: removed += 1
            i = end; continue
        if c != "\\":
            out.append(c); line += c == "\n"; i += 1; continue
        cmd = re.match(r"\\([A-Za-z@]+|[^\n])", text[i:])
        if cmd is None: raise ValueError("tex-incomplete-control-sequence")
        name = cmd[1]; end = i + len(cmd[0])
        if name in REFUSED_COMMANDS or name.startswith("if") or name in {"else", "fi", "newif"}:
            raise ValueError("tex-command-needs-review")
        if name == "verb":
            if end < len(text) and text[end] == "*": end += 1
            if end >= len(text) or text[end].isspace() or text[end].isalpha():
                raise ValueError("tex-invalid-verbatim")
            close = text.find(text[end], end + 1)
            if close < 0 or "\n" in text[end:close]: raise ValueError("tex-invalid-verbatim")
            out.append(text[i:close + 1]); i = close + 1; continue
        if name in {"begin", "end"}:
            env = re.match(r"\{([A-Za-z*]+)\}", text[end:])
            if env is None: raise ValueError("tex-dynamic-environment-needs-review")
            if name == "begin" and env[1] in OPAQUE_ENVS | {"comment"}:
                start = end + len(env[0]); terminator = "\\end{" + env[1] + "}"
                close = text.find(terminator, start)
                if env[1] == "comment":
                    line_start = text.rfind("\n", 0, i) + 1
                    line_end = text.find("\n", start)
                    if text[line_start:i].strip() or line_end < 0 or text[start:line_end].strip():
                        raise ValueError("tex-comment-delimiter-needs-review")
                    match = re.search(r"(?m)^\\end\{comment\}\r?$", text[start:])
                    if match is None: raise ValueError("tex-unclosed-environment")
                    close = start + match.start()
                if close < 0: raise ValueError("tex-unclosed-environment")
                segment = text[i:close + len(terminator)]
                if env[1] == "comment":
                    if "\\begin{comment}" in text[start:close]:
                        raise ValueError("tex-nested-comment-needs-review")
                    # Empty comment tokens suppress each removed line ending.
                    out.append("%\n" * segment.count("\n")); blocks += 1
                else: out.append(segment)
                line += segment.count("\n"); i = close + len(terminator); continue
        if name in STATIC_INPUTS:
            arg = re.match(r"\s*(?:\[[^\]\n]*\]\s*)?\{([^{}\n]+)\}", text[end:])
            if arg is None or re.search(r"[\\%#$~]", arg[1]):
                raise ValueError("tex-dynamic-input-needs-review")
            includes.extend(x.strip() for x in arg[1].split(","))
        out.append(text[i:end]); i = end
    if marker_stack: raise ValueError("tex-unbalanced-lax-marker")
    if seen_keep != keep: raise ValueError("tex-unused-comment-allowlist")
    return {"policy": POLICY, "text": "".join(out), "removed_comments": removed,
            "removed_blocks": blocks, "input_candidates": includes,
            "privacy_review_required": True}
