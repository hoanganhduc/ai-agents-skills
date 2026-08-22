"""The notify body block is one rendering, shared by every channel.

``format_notify_body_block`` used to take a keyword-only ``style`` documented as
``markdown`` (Zulip) or ``plain`` (Telegram after HTML-escape of each line by the
caller).  Both callers passed their own value.  The single branch that read it was::

    if style == "markdown":
        rendered.append(f"{bullet} {piece}")
    else:
        rendered.append(f"{bullet} {piece}")

-- the same append either way, over a bullet fixed at ``"•"`` a few lines above.  So
the parameter offered the callers a channel distinction the function never made, and
the tests that exercised it only ever passed ``"markdown"``, which is why nothing
noticed.  These tests pin what is actually true: one block, both channels, and no
parameter the body does not read.
"""

from __future__ import annotations

import ast
import importlib.util
import inspect
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = (
    REPO_ROOT
    / "canonical"
    / "runtime"
    / "skills"
    / "autonomous-research-loop-runtime"
)
HELPER = RUNTIME_DIR / "autonomous_research_loop_runtime.py"


def _load_runtime():
    # The runtime imports its own siblings by bare name (arl_credential_client and
    # others), so the directory has to be importable before the module body runs.
    # This is what the runtime's own test helper does, and it is left in place: the
    # module keeps importing siblings lazily long after this function returns.
    if str(RUNTIME_DIR) not in sys.path:
        sys.path.insert(0, str(RUNTIME_DIR))
    spec = importlib.util.spec_from_file_location("arl_notify_under_test", HELPER)
    if spec is None or spec.loader is None:  # pragma: no cover - packaging accident
        raise AssertionError(f"could not load {HELPER}")
    module = importlib.util.module_from_spec(spec)
    saved = sys.modules.get(spec.name)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        if saved is None:
            sys.modules.pop(spec.name, None)
        else:
            sys.modules[spec.name] = saved
    return module


ARL = _load_runtime()

# Three clauses, each comfortably long enough that the splitter keeps them apart.
MULTI = (
    "First clause here is long enough; "
    "Second clause also long enough; "
    "Third clause also long enough"
)

NOTIFY_ARGS = {
    "loop_name": "audit-loop",
    "event": "iteration_ok",
    "iteration": 2,
    "max_iter": 5,
    "remaining": 3,
    "decision": "continue",
    "status": "ok",
    "objective": MULTI,
    "output": "",
}


class NotifyBodyBlockTests(unittest.TestCase):
    def test_a_multi_item_body_becomes_a_bullet_list(self) -> None:
        block = ARL.format_notify_body_block(MULTI)
        lines = block.splitlines()
        self.assertEqual(len(lines), 3, block)
        for line in lines:
            self.assertTrue(line.startswith("• "), line)

    def test_a_single_item_body_is_left_alone(self) -> None:
        block = ARL.format_notify_body_block("One clause and nothing else")
        self.assertEqual(block, "One clause and nothing else")
        self.assertNotIn("•", block)

    def test_an_empty_body_renders_to_nothing(self) -> None:
        self.assertEqual(ARL.format_notify_body_block("   "), "")
        self.assertEqual(ARL.format_notify_body_block(""), "")

    def test_the_budget_truncates_the_last_piece_it_can_afford(self) -> None:
        block = ARL.format_notify_body_block(MULTI, max_chars=60)
        self.assertLess(len(block), len(ARL.format_notify_body_block(MULTI)))
        self.assertTrue(block.startswith("• First clause"), block)


class OneBlockForEveryChannelTests(unittest.TestCase):
    """The claim the removed parameter made, tested against the real callers."""

    def _bullets(self, text: str) -> list[str]:
        return [line for line in text.splitlines() if line.lstrip().startswith("•")]

    def test_both_channels_carry_the_same_bullets_for_the_same_body(self) -> None:
        zulip = ARL.format_progress_notify_text(**NOTIFY_ARGS)
        telegram = ARL.format_progress_notify_telegram_html(**NOTIFY_ARGS)
        block = ARL.format_notify_body_block(MULTI, max_chars=600)
        expected = block.splitlines()
        self.assertEqual(len(expected), 3, block)
        # The Zulip body embeds the block verbatim.
        self.assertIn(block, zulip)
        # Telegram escapes each line for HTML; the bullets survive that untouched,
        # which is the whole reason neither channel needed its own rendering.
        self.assertEqual(self._bullets(telegram), expected)
        self.assertEqual(self._bullets(zulip), expected)

    def test_the_bullet_is_not_html_escaped_away(self) -> None:
        telegram = ARL.format_progress_notify_telegram_html(**NOTIFY_ARGS)
        self.assertIn("• First clause here is long enough", telegram)
        self.assertNotIn("&bull;", telegram)


class NoInertParameterTests(unittest.TestCase):
    """A parameter the body never reads promises the caller something nothing keeps.

    Asserted structurally rather than by name, so it also catches the next one.
    """

    def _function_node(self, name: str) -> ast.FunctionDef:
        tree = ast.parse(HELPER.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return node
        raise AssertionError(f"{name} is not defined in {HELPER.name}")

    def test_every_parameter_of_the_block_formatter_is_read(self) -> None:
        node = self._function_node("format_notify_body_block")
        declared = {
            arg.arg
            for arg in (*node.args.args, *node.args.posonlyargs, *node.args.kwonlyargs)
        }
        loaded = {
            inner.id
            for statement in node.body
            for inner in ast.walk(statement)
            if isinstance(inner, ast.Name) and isinstance(inner.ctx, ast.Load)
        }
        self.assertEqual(declared - loaded, set(), f"declared: {sorted(declared)}")

    def test_no_caller_passes_a_channel_selector(self) -> None:
        with self.assertRaises(TypeError):
            ARL.format_notify_body_block(MULTI, style="markdown")
        self.assertNotIn(
            "style", inspect.signature(ARL.format_notify_body_block).parameters
        )

    def test_the_block_formatter_has_no_branch_with_identical_arms(self) -> None:
        node = self._function_node("format_notify_body_block")
        for inner in ast.walk(node):
            if isinstance(inner, ast.If) and inner.orelse:
                body = "\n".join(ast.dump(s) for s in inner.body)
                orelse = "\n".join(ast.dump(s) for s in inner.orelse)
                self.assertNotEqual(
                    body,
                    orelse,
                    f"line {inner.lineno}: both arms of "
                    f"`if {ast.unparse(inner.test)}` do the same thing",
                )


if __name__ == "__main__":
    unittest.main()
