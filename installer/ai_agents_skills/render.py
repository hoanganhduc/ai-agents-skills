from __future__ import annotations

import json
import re
from pathlib import Path
from textwrap import dedent
from typing import Any

from .manifest import REPO_ROOT


MANAGED_MARKER = "Managed by ai-agents-skills"

# Codex/runtime path tokens -> portable forms for copied non-Codex skill renders.
# Order matters: prefixed forms first, bare ".codex/runtime" last.
_RUNTIME_PATH_SUBSTITUTIONS = (
    (re.compile(r"~/\.codex/runtime"), "$AAS_RUNTIME_ROOT"),
    (re.compile(r"\$\{HOME\}/\.codex/runtime"), "$AAS_RUNTIME_ROOT"),
    (re.compile(r"\$HOME/\.codex/runtime"), "$AAS_RUNTIME_ROOT"),
    (re.compile(r"\$CODEX_HOME/runtime", re.I), "$AAS_RUNTIME_ROOT"),
    (re.compile(r"\$codex_home", re.I), "$AAS_RUNTIME_ROOT"),
    (re.compile(r"%USERPROFILE%\\\.?codex(?:\\runtime)?", re.I), "%AAS_RUNTIME_ROOT%"),
    (re.compile(r"\$env:USERPROFILE\\\.?codex\\runtime", re.I), "$env:AAS_RUNTIME_ROOT"),
    (re.compile(r"\.codex/runtime"), "$AAS_RUNTIME_ROOT"),
)

# The canonical Windows snippet resolves the runtime root with a fallback:
#   $runtime = if ($env:AAS_RUNTIME_ROOT) { ... } else { "$env:LOCALAPPDATA\..." }
# Rewriting the else-branch token by token would substitute the very variable the
# if-branch just tested as unset, leaving a fallback that always yields the empty
# string. So the expression is rewritten as a unit, and first, before the
# token-level entries below can reach inside it.
_OPENCLAW_POWERSHELL_RUNTIME_FALLBACK = (
    re.compile(
        r"if \(\$env:AAS_RUNTIME_ROOT\) \{ \$env:AAS_RUNTIME_ROOT \} else \{ "
        r'"\$env:LOCALAPPDATA\\ai-agents-skills\\runtime" \}'
    ),
    "if ($env:AAS_RUNTIME_ROOT) { $env:AAS_RUNTIME_ROOT } else { throw "
    '"AAS_RUNTIME_ROOT is unset: on OpenClaw the runtime root is resolved by the '
    'ai-agents-skills host broker" }',
)

# OpenClaw also cannot consume host-side shared runtime roots from inside the
# sandbox, so its stricter neutralizer rewrites those to broker-resolved env vars.
_OPENCLAW_RUNTIME_SUBSTITUTIONS = (
    (_OPENCLAW_POWERSHELL_RUNTIME_FALLBACK,)
    + _RUNTIME_PATH_SUBSTITUTIONS
    + (
        (re.compile(r"%LOCALAPPDATA%\\ai-agents-skills\\runtime", re.I), "%AAS_RUNTIME_ROOT%"),
        (re.compile(r"\$env:LOCALAPPDATA\\ai-agents-skills\\runtime", re.I), "$env:AAS_RUNTIME_ROOT"),
    )
)

_OPENCLAW_RUNTIME_NOTE = (
    "\n\n<!-- Managed by ai-agents-skills. OpenClaw runtime note. -->\n"
    "> On OpenClaw, this skill's runtime is provided by the ai-agents-skills host\n"
    "> broker. Invoke it through the broker endpoint in `$AAS_BROKER_ENDPOINT`;\n"
    "> `$AAS_RUNTIME_ROOT` references are resolved host-side by the broker, not from\n"
    "> inside the sandbox.\n"
)


def render_openclaw_runtime_neutral(content: str) -> str:
    """Make SKILL.md content machine-neutral for OpenClaw (issue 5).

    Substitutes Codex/runtime path tokens with portable forms and, when runtime
    references were present, appends the host-broker note. Always gated by
    ``path_leak_scan``: any residual machine-specific path raises (fail closed),
    so content that cannot be neutralized never reaches the synced OpenClaw tree.
    Content with no runtime references is returned byte-identical.
    """
    from .openclaw_target_paths import path_leak_scan

    neutral, changed = render_runtime_path_neutral(content, substitutions=_OPENCLAW_RUNTIME_SUBSTITUTIONS)
    if changed:
        neutral = neutral + _OPENCLAW_RUNTIME_NOTE
    leaks = path_leak_scan(neutral)
    if leaks:
        raise ValueError(f"OpenClaw runtime-neutral render still leaks machine paths: {leaks}")
    return neutral


def render_runtime_path_neutral(
    content: str,
    *,
    substitutions: tuple[tuple[re.Pattern[str], str], ...] = _RUNTIME_PATH_SUBSTITUTIONS,
) -> tuple[str, bool]:
    neutral = content
    changed = False
    for pattern, repl in substitutions:
        new = pattern.sub(repl, neutral)
        if new != neutral:
            neutral = new
            changed = True
    return neutral, changed


def render_skill_md(
    skill: str,
    spec: dict[str, Any],
    agent: str,
    antigravity_dirs: tuple[str, str] | None = None,
) -> str:
    canonical = load_canonical_skill(skill)
    if canonical is not None:
        content = add_managed_header(canonical, agent)
        if agent == "openclaw":
            return render_openclaw_runtime_neutral(content)
        if agent == "opencode":
            content, _ = render_runtime_path_neutral(content)
            return add_opencode_skill_note(content)
        if agent == "antigravity":
            content, _ = render_runtime_path_neutral(content)
            return add_antigravity_skill_note(content, antigravity_dirs)
        return content
    description = str(spec["description"])
    optional = spec.get("optional_capabilities", [])
    optional_text = "\n".join(f"- {item}" for item in optional) or "- none"
    return dedent(
        f"""\
        ---
        name: {yaml_scalar(skill)}
        description: {yaml_scalar(description)}
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


def canonical_skill_path(skill: str) -> Path:
    return REPO_ROOT / "canonical" / "skills" / skill / "SKILL.md"


def canonical_skill_dir(skill: str) -> Path:
    return REPO_ROOT / "canonical" / "skills" / skill


def render_reference_skill_md(
    skill: str,
    spec: dict[str, Any],
    agent: str,
    source_path: Path,
    antigravity_dirs: tuple[str, str] | None = None,
) -> str:
    display_source = display_path_for_agent(source_path)
    description = str(spec["description"])
    short_description = str(spec.get("short_description", description))
    safety_note = ""
    if skill == "submission-venue-selector":
        safety_note = (
            "\n\n"
            "        ## Mandatory Delivery Gate\n\n"
            "        Do not deliver a ranked venue shortlist unless every ranked venue\n"
            "        has comparator-paper evidence. Bibliography overlap and offline\n"
            "        placeholders are discovery signals only. If comparator evidence is\n"
            "        missing, report `incomplete analysis` and `not-ready`."
        )
    content = dedent(
        f"""\
        ---
        name: {yaml_scalar(skill)}
        description: {yaml_scalar(description)}
        metadata:
          short-description: {yaml_scalar(short_description)}
        ---

        <!-- {MANAGED_MARKER}. Generated target: {agent}. Install mode: reference. -->

        # {skill}

        This is a thin adapter for agents that cannot load symlinked skills.

        Canonical skill source:

        - `{display_source}`

        Before using this skill, read the canonical source file above and follow
        its instructions. Related reference files live next to that source file
        in the same skill directory.
        {safety_note}
        """
    )
    if agent == "openclaw":
        return render_openclaw_runtime_neutral(content)
    if agent == "antigravity":
        return add_antigravity_skill_note(content, antigravity_dirs)
    return content


def display_path_for_agent(path: Path) -> str:
    resolved = path.resolve()
    try:
        return "~/" + resolved.relative_to(Path.home().resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def load_canonical_skill(skill: str) -> str | None:
    path = canonical_skill_path(skill)
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
    if artifact_type == "instruction-doc" and agent == "antigravity":
        raw = add_antigravity_rule_frontmatter(name, spec, raw)
    return add_managed_support_header(raw, agent, f"{artifact_type}:{spec['source']}")


def add_antigravity_rule_frontmatter(name: str, spec: dict[str, Any], body: str) -> str:
    """Prefix an Antigravity plugin rule with the frontmatter its loader requires.

    Antigravity parses every ``.md`` under ``plugins/<name>/rules/`` and rejects
    the file outright when it carries no frontmatter, logging ``invalid
    frontmatter format`` to its own log and loading nothing.  Documents written
    without it are installed and verified perfectly well and still never reach
    the agent, because whether the consumer accepts an artifact is a question no
    installer check asks.

    ``name`` and ``description`` are the fields the vendor documents for plugin
    markdown, and the entrypoint aliases this installer writes for the same CLI
    use exactly that pair and parse cleanly.  ``add_managed_support_header``
    puts the managed marker after a frontmatter block when it finds one, so the
    marker stays where verification looks for it.
    """
    if body.startswith("---\n"):
        return body
    return (
        f"---\n"
        f"name: {yaml_scalar(name)}\n"
        f"description: {yaml_scalar(str(spec['description']))}\n"
        f"---\n\n"
        f"{body.lstrip()}"
    )


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
            f"name: {yaml_scalar(name)}\n"
            f"description: {yaml_scalar(str(spec['description']))}\n"
            f"---\n\n"
            f"{instructions}\n"
        )
        return add_managed_support_header(content, agent, f"agent-persona:{name}.md")
    if agent == "copilot":
        content = (
            f"---\n"
            f"name: {yaml_scalar(name)}\n"
            f"description: {yaml_scalar(str(spec['description']))}\n"
            f"target: github-copilot\n"
            f"tools: [\"*\"]\n"
            f"---\n\n"
            f"{instructions}\n"
        )
        return add_managed_support_header(content, agent, f"agent-persona:{name}.agent.md")
    if agent == "opencode":
        content = (
            f"---\n"
            f"name: {yaml_scalar(name)}\n"
            f"description: {yaml_scalar(str(spec['description']))}\n"
            f"mode: subagent\n"
            f"---\n\n"
            f"{instructions}\n"
        )
        return add_managed_support_header(content, agent, f"agent-persona:{name}.md")
    if agent == "antigravity":
        content = (
            f"---\n"
            f"name: {yaml_scalar(name)}\n"
            f"description: {yaml_scalar(str(spec['description']))}\n"
            f"target: antigravity\n"
            f"---\n\n"
            f"{instructions}\n"
        )
        return add_managed_support_header(content, agent, f"agent-persona:{name}.md")
    if agent == "grok":
        content = (
            f"---\n"
            f"name: {yaml_scalar(name)}\n"
            f"description: {yaml_scalar(str(spec['description']))}\n"
            f"---\n\n"
            f"{instructions}\n"
        )
        return add_managed_support_header(content, agent, f"agent-persona:{name}.md")
    if agent == "kimi":
        content = (
            f"---\n"
            f"name: {yaml_scalar(name)}\n"
            f"description: {yaml_scalar(str(spec['description']))}\n"
            f"---\n\n"
            f"{instructions}\n"
        )
        return add_managed_support_header(content, agent, f"agent-persona:{name}.md")
    if agent == "chatgpt-local-coder":
        content = (
            f"---\n"
            f"name: {yaml_scalar(name)}\n"
            f"description: {yaml_scalar(str(spec['description']))}\n"
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
            f"description: {yaml_scalar(str(spec['description']))}\n"
            f"---\n\n"
            f"{body.strip()}\n\n"
            f"Backing skill: {skills}\n"
        )
    elif agent == "opencode":
        content = (
            f"---\n"
            f"description: {yaml_scalar(str(spec['description']))}\n"
            f"---\n\n"
            f"{body.strip()}\n\n"
            f"Backing skill: {skills}\n"
        )
    elif agent == "grok":
        content = (
            f"---\n"
            f"description: {yaml_scalar(str(spec['description']))}\n"
            f"---\n\n"
            f"{body.strip()}\n\n"
            f"Backing skill: {skills}\n"
        )
    elif agent == "antigravity":
        content = (
            f"---\n"
            f"name: {yaml_scalar(name)}\n"
            f"description: {yaml_scalar(str(spec['description']))}\n"
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


def add_opencode_skill_note(content: str) -> str:
    note = dedent(
        """\

        ## OpenCode Runtime Notes

        This skill is installed as an OpenCode-native `SKILL.md`. For runtime-backed
        helpers, prefer the shared ai-agents-skills runtime root and the
        `AAS_RUNTIME_ROOT` override instead of assuming a Codex-specific runtime
        path.
        """
    )
    if "## OpenCode Runtime Notes" in content:
        return content
    if content.startswith("---\n"):
        end = content.find("\n---", 4)
        if end != -1:
            insert_at = end + len("\n---")
            return content[:insert_at] + note + content[insert_at:]
    return note.lstrip() + "\n\n" + content


# Where an unmigrated Antigravity home keeps the two managed trees. Both move when
# the vendor migrates a home, so these are only the fallback: the planner resolves
# the real pair and passes it in.
ANTIGRAVITY_DEFAULT_NOTE_DIRS = (
    "~/.gemini/antigravity-cli/skills/",
    "~/.gemini/antigravity-cli/plugins/ai-agents-skills/",
)


def add_antigravity_skill_note(content: str, dirs: tuple[str, str] | None = None) -> str:
    skills_dir, plugin_dir = dirs or ANTIGRAVITY_DEFAULT_NOTE_DIRS
    note = dedent(
        f"""\

        ## Antigravity CLI Runtime Notes

        This skill is installed as an Antigravity CLI global Markdown skill under
        `{skills_dir}`. Plugin payloads managed by this installer live under
        `{plugin_dir}`.
        """
    )
    if "## Antigravity CLI Runtime Notes" in content:
        return content
    if content.startswith("---\n"):
        end = content.find("\n---", 4)
        if end != -1:
            insert_at = end + len("\n---")
            return content[:insert_at] + note + content[insert_at:]
    return note.lstrip() + "\n\n" + content


# Agents whose homes contain no ~/.codex/runtime. Their SKILL.md has always been
# rewritten to the portable runtime root; support files land in the same installed
# directory and are read the same way, so a support file left un-neutralized
# documents a path that does not exist under the install it ships with.
RUNTIME_PATH_NEUTRAL_AGENTS = frozenset({"opencode", "antigravity", "openclaw"})


def render_support_file(content: str, agent: str, relative_path: str) -> str:
    """Render one canonical support file for an agent's installed skill directory."""
    if agent in RUNTIME_PATH_NEUTRAL_AGENTS:
        content, _ = render_runtime_path_neutral(content)
    return add_managed_support_header(content, agent, relative_path)


def toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def toml_multiline_escape(value: str) -> str:
    return value.replace('"""', '\\"\\"\\"')


def yaml_scalar(value: str) -> str:
    return json.dumps(value, ensure_ascii=True)


# The marker's whole job is to name the home a file was generated for, so a
# stale target is worse than no header at all. A canonical source can arrive
# carrying one -- a rendered copy committed back over its source is enough --
# and short-circuiting on the marker alone would then stamp every other agent's
# install with that source's target.
_GENERATED_TARGET_RE = re.compile(rf"({re.escape(MANAGED_MARKER)}\. Generated target: )([^.]+)(\.)")


def retarget_managed_header(content: str, agent: str) -> str:
    """Point an already-present managed header at the agent being rendered for."""
    return _GENERATED_TARGET_RE.sub(lambda m: f"{m.group(1)}{agent}{m.group(3)}", content, count=1)


def add_managed_header(content: str, agent: str) -> str:
    header = f"<!-- {MANAGED_MARKER}. Generated target: {agent}. -->"
    if MANAGED_MARKER in content:
        return retarget_managed_header(content, agent)
    if content.startswith("---\n"):
        end = content.find("\n---", 4)
        if end != -1:
            insert_at = end + len("\n---")
            return content[:insert_at] + "\n\n" + header + content[insert_at:]
    return header + "\n\n" + content


def add_managed_support_header(content: str, agent: str, relative_path: str) -> str:
    if MANAGED_MARKER in content:
        return retarget_managed_header(content, agent)
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
    extra = ""
    if skill == "submission-venue-selector":
        extra = (
            "\n        - `submission-venue-selector` delivery gate: ranked recommendations require "
            "comparator-paper evidence for every ranked venue; otherwise report "
            "`incomplete analysis` and `not-ready`."
        )
    return dedent(
        f"""\
        <!-- {bid}:start -->
        - `{skill}`: {spec['description']}
        {extra}
        <!-- {bid}:end -->
        """
    )


def render_management_notice(agent: str) -> str:
    bid = block_id("repo-management")
    body = dedent(
        """\
        ## ai-agents-skills management notice

        This agent home may contain files managed by the `ai-agents-skills`
        repository. The repository is the source for reusable skill bodies,
        optional workflow artifacts, dependency metadata, and installer state.
        Local agent directories remain runtime targets and may still contain
        user-owned files outside this managed block.

        Use `plan` or `audit-system` before applying changes. Uninstall and
        rollback remove only managed files and managed blocks recorded by this
        installer.
        """
    ).strip()
    if agent == "antigravity":
        body += "\n\n" + dedent(
            """\
            Antigravity CLI workspace guardrails:
            - Resolve the intended workspace from an explicit user path first, then
              the current working directory, then the enclosing git root.
            - If the active workspace is missing or ambiguous, stop and ask before
              editing files, running repo-changing commands, or falling back to a
              scratch directory.
            - Read workspace instruction files such as `GEMINI.md`, `AGENTS.md`,
              and relevant `.agents/` workflow docs before concluding that local
              instructions or skills are unavailable.
            - Treat planning as preview-first: summarize the plan and wait for
              explicit confirmation before execution.
            """
        ).strip()
    return (
        f"<!-- {bid}:start -->\n"
        f"{body}\n\n"
        f"Generated target: {agent}.\n"
        f"<!-- {bid}:end -->\n"
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
