from __future__ import annotations

from textwrap import dedent
from typing import Any


MANAGED_MARKER = "Managed by ai-agents-skills"


def render_skill_md(skill: str, spec: dict[str, Any], agent: str) -> str:
    description = spec["description"]
    optional = spec.get("optional_capabilities", [])
    optional_text = "\n".join(f"- {item}" for item in optional) or "- none"
    return dedent(
        f"""\
        ---
        name: {skill}
        description: {description}
        ---

        # {skill}

        {MANAGED_MARKER}. Generated target: {agent}.

        ## Purpose

        {description}

        ## Canonical Name

        Use `{skill}` as the skill name, folder name, and frontmatter name in all
        supported agents after migration.

        ## Optional Capabilities

        {optional_text}

        ## Notes

        This generated adapter is intentionally thin. It points the agent at the
        canonical workflow while preserving per-agent installation boundaries.
        """
    )


def block_id(skill: str) -> str:
    return f"ai-agents-skills:{skill}"


def render_instruction_block(skill: str, spec: dict[str, Any]) -> str:
    bid = block_id(skill)
    return dedent(
        f"""\
        <!-- {bid}:start -->
        - `{skill}`: {spec['description']}
        <!-- {bid}:end -->
        """
    )


def replace_or_append_block(content: str, skill: str, block: str) -> str:
    bid = block_id(skill)
    start = f"<!-- {bid}:start -->"
    end = f"<!-- {bid}:end -->"
    if start in content and end in content:
        before, rest = content.split(start, 1)
        _, after = rest.split(end, 1)
        return before.rstrip() + "\n\n" + block.strip() + "\n" + after
    sep = "\n\n" if content.strip() else ""
    return content.rstrip() + sep + block.strip() + "\n"
