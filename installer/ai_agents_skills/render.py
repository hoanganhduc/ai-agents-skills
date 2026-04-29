from __future__ import annotations

from textwrap import dedent
from typing import Any

from .manifest import REPO_ROOT


MANAGED_MARKER = "Managed by ai-agents-skills"


def render_skill_md(skill: str, spec: dict[str, Any], agent: str) -> str:
    canonical = load_canonical_skill(skill)
    if canonical is not None:
        return add_managed_header(canonical, agent)
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


def load_canonical_skill(skill: str) -> str | None:
    path = REPO_ROOT / "canonical" / "skills" / skill / "SKILL.md"
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def load_canonical_artifact(artifact_type: str, source: str) -> str:
    path = REPO_ROOT / "canonical" / artifact_source_dir(artifact_type) / source
    return path.read_text(encoding="utf-8")


def artifact_source_dir(artifact_type: str) -> str:
    return {
        "template": "templates",
        "instruction-doc": "instructions",
        "agent-persona": "personas",
        "entrypoint-alias": "entrypoints",
    }.get(artifact_type, artifact_type)


def render_artifact_content(
    artifact_type: str,
    name: str,
    spec: dict[str, Any],
    agent: str,
) -> str:
    raw = load_canonical_artifact(artifact_type, spec["source"])
    if artifact_type == "agent-persona":
        return render_persona(name, spec, agent, raw)
    if artifact_type == "entrypoint-alias":
        return render_entrypoint(name, spec, agent, raw)
    return add_managed_support_header(raw, agent, f"{artifact_type}:{spec['source']}")


def render_persona(name: str, spec: dict[str, Any], agent: str, body: str) -> str:
    instructions = body.strip()
    if agent == "codex":
        content = dedent(
            f'''\
            name = "{toml_escape(name)}"
            description = "{toml_escape(spec["description"])}"
            developer_instructions = """
            {toml_multiline_escape(instructions)}
            """
            '''
        )
        return add_managed_support_header(content, agent, f"agent-persona:{name}.toml")
    if agent == "claude":
        content = (
            f"---\n"
            f"name: {name}\n"
            f"description: {spec['description']}\n"
            f"---\n\n"
            f"{instructions}\n"
        )
        return add_managed_support_header(content, agent, f"agent-persona:{name}.md")
    content = dedent(
        f"""\
        # {name}

        DeepSeek persona reference. DeepSeek native persona-file loading has not
        been verified, so use this as a prompt/reference document rather than a
        guaranteed automatic agent registration.

        Description: {spec["description"]}

        {instructions}
        """
    )
    return add_managed_support_header(content, agent, f"agent-persona:{name}.md")


def render_entrypoint(name: str, spec: dict[str, Any], agent: str, body: str) -> str:
    skills = ", ".join(f"`{skill}`" for skill in spec.get("depends_on_skills", [])) or "the backing skill"
    if agent == "claude":
        content = (
            f"---\n"
            f"description: {spec['description']}\n"
            f"---\n\n"
            f"{body.strip()}\n\n"
            f"Backing skill: {skills}\n"
        )
    else:
        content = dedent(
            f"""\
            # {name}

            {body.strip()}

            Backing skill: {skills}

            This is a quick-action reference, not a native slash-command
            registration for this agent.
            """
        )
    return add_managed_support_header(content, agent, f"entrypoint-alias:{name}.md")


def toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def toml_multiline_escape(value: str) -> str:
    return value.replace('"""', '\\"\\"\\"')


def add_managed_header(content: str, agent: str) -> str:
    header = f"<!-- {MANAGED_MARKER}. Generated target: {agent}. -->"
    if MANAGED_MARKER in content:
        return content
    if content.startswith("---\n"):
        end = content.find("\n---", 4)
        if end != -1:
            insert_at = end + len("\n---")
            return content[:insert_at] + "\n\n" + header + content[insert_at:]
    return header + "\n\n" + content


def add_managed_support_header(content: str, agent: str, relative_path: str) -> str:
    if MANAGED_MARKER in content:
        return content
    marker = f"{MANAGED_MARKER}. Generated target: {agent}. Source: {relative_path}."
    if relative_path.endswith(".md"):
        header = f"<!-- {marker} -->"
        if content.startswith("---\n"):
            end = content.find("\n---", 4)
            if end != -1:
                insert_at = end + len("\n---")
                return content[:insert_at] + "\n\n" + header + content[insert_at:]
        return header + "\n\n" + content
    if relative_path.endswith((".sh", ".py", ".yaml", ".yml", ".sage", ".toml", ".ps1")):
        header = f"# {marker}"
        if content.startswith("#!"):
            first, _, rest = content.partition("\n")
            return first + "\n" + header + ("\n" + rest if rest else "\n")
        return header + "\n" + content
    return content


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
