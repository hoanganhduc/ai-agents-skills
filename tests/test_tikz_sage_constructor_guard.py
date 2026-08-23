"""Regressions for the Sage graph constructor guard in tikz-draw.

``run_sage_graph_query`` builds a Sage script that evaluates the constructor
string a brief asked for::

    G = eval(constructor, {'graphs': graphs, 'Graph': Graph, '__builtins__': {}}, {})

The mapping used to omit ``__builtins__``. ``eval`` fills that key in with the
real builtins module whenever it is absent, so naming only ``graphs`` and
``Graph`` restricted nothing -- ``__import__`` was one nested call away.

The only thing standing in front of that ``eval`` was a regex::

    ^(graphs\\.[A-Za-z_][A-Za-z0-9_]*\\([^"'=;`]*\\)|Graph\\([A-Za-z0-9_{}\\[\\](),:.\\s-]*\\))$

It banned quotes, ``=``, ``;`` and backticks, but allowed ``_``, ``.``, ``+``,
digits and nested parentheses inside the argument list, which is enough to
spell a call without ever typing a quote::

    graphs.PetersenGraph(open(chr(47)+chr(116)+...,chr(119)).write(chr(88)))

Both brief fields that carry a constructor reach it -- ``graph_constructor``
and a ``sage: ...`` line in ``content_requirements`` -- so a brief, which is
file- or agent-supplied data, could run arbitrary code in the Sage subprocess.

The same regex rejected every string parameter, because ``serialize_graph_param``
renders those with ``json.dumps`` and the quotes it emits were banned. The
string branch was unreachable, and the failure blamed the constructor rather
than naming the parameter.
"""

from __future__ import annotations

import inspect
import unittest

from tests.test_never_matching_predicates import RUNTIME_SKILLS, load_module


SKILL_DIR = RUNTIME_SKILLS / "tikz-draw"
backend = load_module(
    "sage_graph_backend", SKILL_DIR / "sage_graph_backend.py", extra_syspath=SKILL_DIR
)


# A quote-free expression that reaches __import__. Built the way an attacker
# would have to build it, so the test keeps failing if the ban list is the only
# thing tightened.
QUOTE_FREE_PAYLOAD = (
    "graphs.PetersenGraph(__import__(chr(111)+chr(115)).system(chr(105)+chr(100)))"
)


class AcceptedConstructorTests(unittest.TestCase):
    """Anchors the suite: the guard is not simply rejecting everything."""

    def test_real_constructors_are_accepted(self):
        for expression in (
            "graphs.PetersenGraph()",
            "graphs.JohnsonGraph(5, 2)",
            "graphs.CompleteBipartiteGraph(3, 4)",
            "graphs.RandomGNP(10, 0.5)",
            "graphs.CycleGraph(-3)",
            "Graph({0: [1, 2]})",
            "Graph([[0, 1], [1, 2]])",
        ):
            with self.subTest(expression=expression):
                self.assertTrue(backend.is_safe_graph_expression(expression))

    def test_normalization_still_builds_them(self):
        self.assertEqual(
            backend.normalize_graph_constructor("JohnsonGraph", [5, 2]),
            "graphs.JohnsonGraph(5, 2)",
        )
        self.assertEqual(
            backend.normalize_graph_constructor("graphs.PetersenGraph()", None),
            "graphs.PetersenGraph()",
        )


class RejectedConstructorTests(unittest.TestCase):
    def test_nested_calls_are_rejected(self):
        for expression in (
            QUOTE_FREE_PAYLOAD,
            "Graph(breakpoint())",
            "Graph(exit())",
            "graphs.CycleGraph(__import__(chr(111)))",
            "graphs.CycleGraph(open(chr(47)))",
        ):
            with self.subTest(expression=expression):
                self.assertNotIn('"', expression, "payload must not need a quote")
                self.assertFalse(backend.is_safe_graph_expression(expression))

    def test_attribute_and_name_arguments_are_rejected(self):
        for expression in (
            "graphs.CycleGraph(().__class__)",
            "graphs.CycleGraph(graphs)",
            "graphs.CycleGraph(*[1])",
        ):
            with self.subTest(expression=expression):
                self.assertFalse(backend.is_safe_graph_expression(expression))

    def test_only_the_sage_graph_surface_is_callable(self):
        for expression in (
            "os.system(1)",
            "print(1)",
            "graphs2.CycleGraph(1)",
            "graphs.CycleGraph(1).copy()",
            "1",
            "",
        ):
            with self.subTest(expression=expression):
                self.assertFalse(backend.is_safe_graph_expression(expression))


class BriefReachabilityTests(unittest.TestCase):
    """Both documented ways to name a constructor must refuse the payload."""

    def _brief(self, **extra) -> dict:
        brief = {
            "title": "figure",
            "purpose": "illustrate",
            "graph_request": "petersen graph",
            "graph_mode": "sage",
        }
        brief.update(extra)
        return brief

    def test_graph_constructor_field_is_refused(self):
        with self.assertRaises(SystemExit):
            backend.extract_graph_query(
                self._brief(graph_constructor=QUOTE_FREE_PAYLOAD)
            )

    def test_sage_content_requirement_is_refused(self):
        with self.assertRaises(SystemExit):
            backend.extract_graph_query(
                self._brief(content_requirements=[f"sage: {QUOTE_FREE_PAYLOAD}"])
            )

    def test_a_real_constructor_still_routes(self):
        query = backend.extract_graph_query(
            self._brief(graph_constructor="graphs.JohnsonGraph(5, 2)")
        )
        self.assertEqual(query["constructor"], "graphs.JohnsonGraph(5, 2)")
        self.assertEqual(query["graph_route_status"], "SAGE_ASSISTED_GRAPH_PATH")


class EvalEnvironmentTests(unittest.TestCase):
    def test_the_generated_script_denies_builtins(self):
        source = inspect.getsource(backend.run_sage_graph_query)
        self.assertIn("'__builtins__': {}", source)

    def test_that_mapping_really_hides_import(self):
        mapping = {"graphs": None, "Graph": None, "__builtins__": {}}
        with self.assertRaises(NameError):
            eval("__import__", mapping, {})  # noqa: S307 - the point of the test
        self.assertIs(eval("__import__", {"graphs": None}, {}), __import__)


class StringParameterTests(unittest.TestCase):
    """serialize_graph_param's string branch was unreachable behind the regex."""

    def test_a_string_parameter_now_survives_normalization(self):
        self.assertEqual(
            backend.normalize_graph_constructor("JohnsonGraph", ["circular"]),
            'graphs.JohnsonGraph("circular")',
        )

    def test_mixed_scalar_parameters_render(self):
        self.assertEqual(
            backend.normalize_graph_constructor("SomeGraph", [3, "abc", True, None]),
            'graphs.SomeGraph(3, "abc", True, None)',
        )

    def test_a_string_parameter_cannot_smuggle_a_call(self):
        rendered = backend.normalize_graph_constructor(
            "SomeGraph", ["__import__(chr(111))"]
        )
        self.assertEqual(rendered, 'graphs.SomeGraph("__import__(chr(111))")')
        self.assertTrue(backend.is_safe_graph_expression(rendered))
        # It is inert: a literal, not a call.
        import ast

        argument = ast.parse(rendered, mode="eval").body.args[0]
        self.assertIsInstance(argument, ast.Constant)


if __name__ == "__main__":
    unittest.main()
