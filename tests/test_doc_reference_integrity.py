"""Canonical instructions must name only mechanisms that exist.

An instruction that names a runtime command, a verb, a script or a template
reads as an order to use it. When the named thing is missing, an agent either
invents it or reports a step it could not take. Four checks guard canonical
markdown:

(a) a runtime command written as ``skills/<name>/<file>`` is a path declared in
    ``manifest/runtime.yaml``, as a source or as an installed target;
(b) a subcommand written right after the strict-gate, deep-research or
    autonomous-research-loop wrapper is a root subcommand of that runtime's
    argparse (read statically with ``ast``);
(c) a script name (``*.py``, ``*.sh``, ``*.ps1``) is a file tracked in this
    repository, or an allowlisted name with a recorded reason;
(d) every template in agent-group-discuss ``TEMPLATES.md`` has an execution
    plan and a Quick reference row in ``EXECUTION.md`` and an auto-selection
    row in ``SKILL.md``, and the template count announced in ``EXECUTION.md``
    matches.

The parsers are deliberately narrow; they read the forms the docs use today.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "canonical"
AGD = CANONICAL / "skills" / "agent-group-discuss"

# Wrapper script stem -> runtime module whose argparse defines its subcommands.
WRAPPER_RUNTIMES = {
    "run_deep_research_workflow": "canonical/runtime/skills/deep-research-workflow/deep_research_workflow.py",
    "run_lean_strict_verification_gate": "canonical/runtime/skills/lean-strict-verification-gate/lean_strict_verification_gate.py",
    "run_autonomous_research_loop": "canonical/runtime/skills/autonomous-research-loop-runtime/autonomous_research_loop_runtime.py",
}

# Script names that canonical docs mention on purpose although the repository
# does not ship them. Each entry carries its reason; delete an entry once the
# reason stops holding.
ALLOWED_ABSENT_SCRIPTS = {
    # A project's own launcher: the sample ARL headless driver wraps an
    # existing one ("existing project supervisor / LAUNCH.sh").
    "LAUNCH.sh": "a project's own launcher, wrapped by the sample ARL driver",
    # The executable each Hetzner job bundle carries; the user builds it per job.
    "run.sh": "the executable inside a user-built Hetzner job bundle",
    # An example output path for `gen --output` in manim-math-animation.
    "scene.py": "an example output path for the manim generator",
    # A loop-owned wrapper the operator writes to export tokens before launch.
    "with_compute_env.sh": "an operator-written, loop-owned token wrapper",
}

# Templates allowed to lack an execution plan, a Quick reference row or an
# auto-selection row, each with its reason.
ALLOWED_INCOMPLETE_TEMPLATES = {
    # An AAS-only template; its execution plan, Quick reference row and
    # SKILL auto-selection row are left for a separate request.
    "Single-Decision Doubt Review": "AAS-only template; plan and table rows left for a separate request",
}

NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}

RUNTIME_COMMAND = re.compile(
    r"(?<![A-Za-z0-9_.-])skills/([a-z0-9][a-z0-9-]*)/([A-Za-z0-9_.-]+\.(?:sh|ps1|py))\b"
)
SUBCOMMAND = re.compile(
    r"(" + "|".join(WRAPPER_RUNTIMES) + r")\.(?:sh|ps1)[\"'`]?[ \t]+([a-z][a-z0-9-]*)"
)
SCRIPT_NAME = re.compile(r"(?<![A-Za-z0-9_.\-/])([A-Za-z0-9_][A-Za-z0-9_.-]*\.(?:py|sh|ps1))\b")


def canonical_markdown() -> dict[str, str]:
    return {
        path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(CANONICAL.glob("**/*.md"))
    }


def declared_runtime_paths() -> set[str]:
    """Runtime paths declared in manifest/runtime.yaml: sources and installed targets.

    A target is relative to the runtime root (``workspace/skills/...``); docs name
    it as ``skills/...``, and it may come from another skill's source.
    """
    manifest = json.loads((ROOT / "manifest" / "runtime.yaml").read_text(encoding="utf-8"))
    paths: set[str] = set()

    def walk(value: object) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "source" and isinstance(item, str):
                    paths.add(item)
                if key == "target" and isinstance(item, str):
                    paths.add(item.removeprefix("workspace/"))
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(manifest)
    return paths


def _assigned_names(node: ast.Assign) -> set[str]:
    return {target.id for target in node.targets if isinstance(target, ast.Name)}


def argparse_names(module_path: str) -> set[str]:
    """Root subcommand names (and aliases) of a runtime module's argparse.

    Only ``add_parser`` calls on the subparsers object of an
    ``argparse.ArgumentParser`` count; nested verbs such as ARL's
    ``goal-focus replan`` are not root subcommands.
    """
    tree = ast.parse((ROOT / module_path).read_text(encoding="utf-8"))
    assigns = [node for node in ast.walk(tree) if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)]
    roots: set[str] = set()
    for node in assigns:
        func = node.value.func
        if isinstance(func, ast.Attribute) and func.attr == "ArgumentParser":
            roots |= _assigned_names(node)
    root_subparsers: set[str] = set()
    for node in assigns:
        func = node.value.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "add_subparsers"
            and isinstance(func.value, ast.Name)
            and func.value.id in roots
        ):
            root_subparsers |= _assigned_names(node)
    names: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_parser"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in root_subparsers
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            names.add(node.args[0].value)
            for keyword in node.keywords:
                if keyword.arg == "aliases" and isinstance(keyword.value, (ast.List, ast.Tuple)):
                    names |= {item.value for item in keyword.value.elts if isinstance(item, ast.Constant) and isinstance(item.value, str)}
    return names


def tracked_basenames() -> set[str]:
    try:
        completed = subprocess.run(
            ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True
        )
        entries = [os.fsdecode(item) for item in completed.stdout.split(b"\0") if item]
    except (OSError, subprocess.CalledProcessError):  # pragma: no cover - no git
        entries = [
            os.path.join(dirpath, name)
            for dirpath, dirnames, filenames in os.walk(ROOT)
            if ".git" not in Path(dirpath).parts
            for name in filenames
        ]
    return {os.path.basename(entry) for entry in entries}


def undeclared_runtime_commands(text: str, sources: set[str]) -> set[str]:
    return {
        f"skills/{match.group(1)}/{match.group(2)}"
        for match in RUNTIME_COMMAND.finditer(text)
        if f"skills/{match.group(1)}/{match.group(2)}" not in sources
    }


def unknown_subcommands(text: str, names: dict[str, set[str]]) -> set[tuple[str, str]]:
    return {
        (match.group(1), match.group(2))
        for match in SUBCOMMAND.finditer(text)
        if match.group(2) not in names[match.group(1)]
    }


def absent_scripts(text: str, basenames: set[str]) -> set[str]:
    return {
        match.group(1)
        for match in SCRIPT_NAME.finditer(text)
        if match.group(1) not in basenames and match.group(1) not in ALLOWED_ABSENT_SCRIPTS
    }


def section(text: str, heading: str) -> str:
    """The body of a ``## heading`` section, up to the next level-2 heading."""
    start = text.index(heading)
    following = re.search(r"^## ", text[start + len(heading):], re.M)
    end = start + len(heading) + following.start() if following else len(text)
    return text[start:end]


def table_first_cells(text: str) -> list[str]:
    cells = []
    for line in text.splitlines():
        if not line.startswith("|") or set(line) <= set("|- :"):
            continue
        cells.append(line.split("|")[1].strip())
    return cells[1:]  # drop the header row


def template_structure_problems(templates_md: str, execution_md: str, skill_md: str) -> dict[str, list[str]]:
    templates = re.findall(r"^## Template: (.+?)\s*$", templates_md, re.M)
    plans = re.findall(r"^### \d+\. (.+?)\s*$", section(execution_md, "## Template execution plans"), re.M)
    quick = table_first_cells(section(execution_md, "## Quick reference"))
    auto = re.findall(r"^\|[^|]*\|\s*`([^`]+)`\s*\|\s*$", skill_md, re.M)
    problems: dict[str, list[str]] = {}
    for template in templates:
        missing = []
        if template not in plans:
            missing.append("execution plan")
        if not any(cell and template.startswith(cell) for cell in quick):
            missing.append("Quick reference row")
        if template not in auto:
            missing.append("auto-selection row")
        if missing:
            problems[template] = missing
    announced = re.search(r"running the (\w+) imported research templates", execution_md)
    if announced is None or NUMBER_WORDS.get(announced.group(1)) != len(templates):
        problems["<announced count>"] = [
            f"EXECUTION.md announces {announced.group(1) if announced else 'no count'}, TEMPLATES.md has {len(templates)}"
        ]
    return problems


class DocReferenceIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.docs = canonical_markdown()
        cls.sources = declared_runtime_paths()
        cls.names = {stem: argparse_names(path) for stem, path in WRAPPER_RUNTIMES.items()}
        cls.basenames = tracked_basenames()

    def test_runtime_commands_are_declared_in_the_runtime_manifest(self) -> None:
        broken = {
            path: sorted(found)
            for path, text in self.docs.items()
            if (found := undeclared_runtime_commands(text, self.sources))
        }
        self.assertEqual({}, broken, broken)

    def test_subcommands_exist_in_the_runtime_argparse(self) -> None:
        broken = {
            path: sorted(found)
            for path, text in self.docs.items()
            if (found := unknown_subcommands(text, self.names))
        }
        self.assertEqual({}, broken, broken)

    def test_script_names_are_repository_files_or_allowlisted(self) -> None:
        broken = {
            path: sorted(found)
            for path, text in self.docs.items()
            if (found := absent_scripts(text, self.basenames))
        }
        self.assertEqual({}, broken, broken)

    def test_agd_templates_have_plans_quick_reference_and_auto_selection(self) -> None:
        problems = template_structure_problems(
            (AGD / "TEMPLATES.md").read_text(encoding="utf-8"),
            (AGD / "EXECUTION.md").read_text(encoding="utf-8"),
            (AGD / "SKILL.md").read_text(encoding="utf-8"),
        )
        unexpected = {name: gaps for name, gaps in problems.items() if name not in ALLOWED_INCOMPLETE_TEMPLATES}
        self.assertEqual({}, unexpected, unexpected)

    def test_the_scans_reach_the_docs(self) -> None:
        """A clean result only means something if the scans found references to check."""
        commands = sum(len(RUNTIME_COMMAND.findall(text)) for text in self.docs.values())
        subcommands = sum(len(SUBCOMMAND.findall(text)) for text in self.docs.values())
        scripts = sum(len(SCRIPT_NAME.findall(text)) for text in self.docs.values())
        self.assertGreater(commands, 50)
        self.assertGreater(subcommands, 20)
        self.assertGreater(scripts, 50)
        self.assertIn("delivery-check", self.names["run_deep_research_workflow"])
        self.assertIn("scan", self.names["run_lean_strict_verification_gate"])
        self.assertIn("drive", self.names["run_autonomous_research_loop"])

    def test_inserted_defects_would_be_caught(self) -> None:
        self.assertEqual(
            {"skills/deep-research-workflow/run_missing.sh"},
            undeclared_runtime_commands("bash run_skill.sh skills/deep-research-workflow/run_missing.sh", self.sources),
        )
        self.assertEqual(
            {("run_deep_research_workflow", "frobnicate")},
            unknown_subcommands("skills/deep-research-workflow/run_deep_research_workflow.sh frobnicate --dir x", self.names),
        )
        self.assertEqual({"verify_everything.py"}, absent_scripts("Run `verify_everything.py` first.", self.basenames))
        # A nested verb named without its parent command is not a root subcommand.
        self.assertEqual(
            {("run_autonomous_research_loop", "replan")},
            unknown_subcommands("skills/autonomous-research-loop-runtime/run_autonomous_research_loop.sh replan --dir x", self.names),
        )
        # An installed target sourced from another skill is a valid runtime path.
        self.assertEqual(set(), undeclared_runtime_commands("python3 skills/remote-bridge/notify_v2.py", self.sources))
        templates_md = (AGD / "TEMPLATES.md").read_text(encoding="utf-8") + "\n## Template: Phantom Panel\n\nNo plan.\n"
        problems = template_structure_problems(
            templates_md,
            (AGD / "EXECUTION.md").read_text(encoding="utf-8"),
            (AGD / "SKILL.md").read_text(encoding="utf-8"),
        )
        self.assertEqual(["execution plan", "Quick reference row", "auto-selection row"], problems.get("Phantom Panel"))
        self.assertIn("<announced count>", problems)

    def test_the_allowlists_stay_honest(self) -> None:
        """An allowlisted name that starts existing, or stops being mentioned, is a stale exemption."""
        mentioned = {match.group(1) for text in self.docs.values() for match in SCRIPT_NAME.finditer(text)}
        for name in ALLOWED_ABSENT_SCRIPTS:
            self.assertNotIn(name, self.basenames, name)
            self.assertIn(name, mentioned, name)
        problems = template_structure_problems(
            (AGD / "TEMPLATES.md").read_text(encoding="utf-8"),
            (AGD / "EXECUTION.md").read_text(encoding="utf-8"),
            (AGD / "SKILL.md").read_text(encoding="utf-8"),
        )
        for template in ALLOWED_INCOMPLETE_TEMPLATES:
            self.assertIn(template, problems, template)


if __name__ == "__main__":
    unittest.main()
