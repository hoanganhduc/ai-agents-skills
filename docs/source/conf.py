"""Sphinx configuration for AI Agents Skills documentation."""

from __future__ import annotations

from datetime import datetime

project = "AI Agents Skills"
author = "AI Agents Skills maintainers"
release = "0.1.0"
version = release

extensions = [
    "myst_parser",
    "sphinx.ext.autosectionlabel",
]

source_suffix = {
    ".md": "markdown",
}

myst_enable_extensions = [
    "colon_fence",
    "deflist",
    "substitution",
]

templates_path = ["_templates"]
exclude_patterns: list[str] = ["_build", "Thumbs.db", ".DS_Store"]
html_theme = "sphinx_rtd_theme"
html_static_path = ["_static"]
rst_epilog = f".. |last_updated| replace:: {datetime.now().strftime('%Y-%m-%d')}"
autosectionlabel_prefix_document = True
