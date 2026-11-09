"""Offline tests for the Sage counterexample-search template.

``counterexample_search.sage`` runs under SageMath, which these tests never
start. They execute its source as Python with stub ``graphs()`` and
``version()`` functions, so they pin how the template counts, lists and reports
what it found, not how Sage's graph routines behave. One test also wraps the
template's integer literals the way Sage's preparser does, because the
preparser turns them into Sage ``Integer`` objects, which ``json.dumps``
rejects; it emulates that part of the preparser and nothing else.
"""

from __future__ import annotations

import ast
import contextlib
import functools
import hashlib
import importlib.util
import io
import json
import operator
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

# The evidence checks load deep-research's validator in this process.
sys.dont_write_bytecode = True

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS = REPO_ROOT / "canonical" / "runtime" / "skills"
TEMPLATE = SKILLS / "sagemath" / "templates" / "counterexample_search.sage"
STUB_VERSION = "SageMath version 0.0 (test stub)"


class StubGraph:
    """The few Sage graph methods the template and these conjectures call."""

    def __init__(self, order, edges, chromatic, clique, invariants_raise=False):
        self._order = order
        self._edges = list(edges)
        self._chromatic = chromatic
        self._clique = clique
        self._invariants_raise = invariants_raise

    @classmethod
    def edgeless(cls, order):
        return cls(order, [], 1, 1)

    @classmethod
    def complete(cls, order, invariants_raise=False):
        edges = [(u, v) for u in range(order) for v in range(u + 1, order)]
        return cls(order, edges, order, order, invariants_raise)

    def order(self):
        return self._order

    def size(self):
        return len(self._edges)

    def edges(self, labels=True):
        return [(u, v, None) if labels else (u, v) for u, v in self._edges]

    def chromatic_number(self):
        if self._invariants_raise:
            raise RuntimeError("stub chromatic_number failed")
        return self._chromatic

    def clique_number(self):
        return self._clique


def edgeless_and_complete(n):
    """Stub ``graphs(n)``: the edgeless graph, and the complete graph when n >= 2.

    Up to ``n_max`` it yields 2 * n_max - 1 graphs, one edgeless graph per order.
    """
    yield StubGraph.edgeless(n)
    if n >= 2:
        yield StubGraph.complete(n)


def with_a_broken_witness(n):
    """Like ``edgeless_and_complete``, but K2's chromatic number raises."""
    yield StubGraph.edgeless(n)
    if n >= 2:
        yield StubGraph.complete(n, invariants_raise=n == 2)


def _plain(value):
    if isinstance(value, SageInteger):
        return value.value
    if isinstance(value, int):
        return value
    return None


def _arithmetic(op, reflected=False):
    def method(self, other):
        plain = _plain(other)
        if plain is None:
            return NotImplemented
        return SageInteger(op(plain, self.value) if reflected else op(self.value, plain))

    return method


def _comparison(op):
    def method(self, other):
        plain = _plain(other)
        if plain is None:
            return NotImplemented
        return op(self.value, plain)

    return method


class SageInteger:
    """Stand-in for Sage's ``Integer``: an integer type that is not a Python int."""

    def __init__(self, value):
        self.value = int(value)

    def __index__(self):
        return self.value

    __int__ = __index__

    def __hash__(self):
        return hash(self.value)

    def __bool__(self):
        return bool(self.value)

    def __str__(self):
        return str(self.value)

    __repr__ = __str__

    def __format__(self, spec):
        return format(self.value, spec)

    def __neg__(self):
        return SageInteger(-self.value)

    __add__ = _arithmetic(operator.add)
    __radd__ = _arithmetic(operator.add, reflected=True)
    __sub__ = _arithmetic(operator.sub)
    __rsub__ = _arithmetic(operator.sub, reflected=True)
    __mul__ = _arithmetic(operator.mul)
    __rmul__ = _arithmetic(operator.mul, reflected=True)
    __floordiv__ = _arithmetic(operator.floordiv)
    __rfloordiv__ = _arithmetic(operator.floordiv, reflected=True)
    __eq__ = _comparison(operator.eq)
    __ne__ = _comparison(operator.ne)
    __lt__ = _comparison(operator.lt)
    __le__ = _comparison(operator.le)
    __gt__ = _comparison(operator.gt)
    __ge__ = _comparison(operator.ge)


class IntegerLiterals(ast.NodeTransformer):
    """Wrap every int literal in ``Integer(...)``, as Sage's preparser does."""

    def visit_Constant(self, node):
        if type(node.value) is int:
            call = ast.Call(func=ast.Name(id="Integer", ctx=ast.Load()), args=[node], keywords=[])
            return ast.copy_location(call, node)
        return node


def run_template(conjecture, n_max, family=edgeless_and_complete, preparse=False):
    """Run the template with stub Sage functions and return the JSON it prints."""
    tree = ast.parse(TEMPLATE.read_text(encoding="utf-8"), filename=str(TEMPLATE))
    namespace = {"__name__": "__main__", "graphs": family, "version": lambda: STUB_VERSION}
    if preparse:
        tree = ast.fix_missing_locations(IntegerLiterals().visit(tree))
        namespace["Integer"] = SageInteger
    code = compile(tree, str(TEMPLATE), "exec")
    environment = {"SAGE_PARAM_N_MAX": str(n_max), "SAGE_PARAM_CONJECTURE": conjecture}
    stdout, stderr = io.StringIO(), io.StringIO()
    with mock.patch.dict(os.environ, environment):
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exec(code, namespace)
    return json.loads(stdout.getvalue())


@functools.lru_cache(maxsize=None)
def deep_research_workflow():
    path = SKILLS / "deep-research-workflow" / "deep_research_workflow.py"
    spec = importlib.util.spec_from_file_location("drw_sage_template_block", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def computation_evidence_errors(block):
    """Return what deep-research's computation-evidence validator reports for ``block``."""
    errors = []
    deep_research_workflow().validate_computation_evidence(block, Path("evidence.jsonl"), errors, 1)
    return errors


class CounterexampleTemplateTests(unittest.TestCase):
    """The template's JSON must say how much it checked and what it found.

    A graph on which the conjecture raised is neither a pass nor a
    counterexample, and a counterexample stays listed even when its invariants
    cannot be computed.
    """

    def test_evaluation_errors_are_counted_apart(self):
        """An exception while evaluating the conjecture is an error, not a checked graph."""
        result = run_template("1 / G.size() > 0", n_max=7)

        self.assertEqual(result["evaluation_errors"], 7)
        self.assertEqual(result["total_enumerated"], 13)
        self.assertEqual(result["total_evaluated"] + result["evaluation_errors"], result["total_enumerated"])
        self.assertLess(result["total_evaluated"], result["total_enumerated"])
        block = result["computation_evidence"]
        self.assertEqual((block["result_status"], block["coverage_status"]), ("partial", "partial"))
        self.assertNotIn("exhaustiveness_argument", block)
        self.assertEqual(computation_evidence_errors(block), [])

        cases = {
            # Seven errors: five messages are kept and the rest dropped.
            "dropped": ("1 / G.size() > 0", 7, 5, True),
            # Three short messages fit.
            "kept": ("1 / G.size() > 0", 3, 3, False),
            # One message longer than 200 characters is cut.
            "cut": ("int('9' * 300 + 'x') > 0", 1, 1, True),
        }
        for label, (conjecture, n_max, kept, truncated) in cases.items():
            with self.subTest(samples=label):
                result = run_template(conjecture, n_max=n_max)
                samples = result["evaluation_error_samples"]
                self.assertEqual(len(samples), kept)
                for sample in samples:
                    self.assertRegex(sample, r"^n=\d+: (ZeroDivisionError|ValueError): ")
                    self.assertLessEqual(len(sample), 200)
                self.assertIs(result["evaluation_error_samples_truncated"], truncated)
                if label == "cut":
                    self.assertEqual(len(samples[0]), 200)

    def test_a_clean_run_is_bounded_complete_and_validates(self):
        conjecture = "G.size() <= G.order() * (G.order() - 1) // 2"
        result = run_template(conjecture, n_max=5)

        block = result["computation_evidence"]
        self.assertEqual((block["result_status"], block["coverage_status"]), ("passed", "bounded_complete"))
        self.assertTrue(block["exhaustiveness_argument"].strip())
        self.assertEqual(block["tool_name"], "sagemath counterexample_search.sage")
        self.assertEqual(block["tool_version"], STUB_VERSION)
        digest = hashlib.sha256(conjecture.encode("utf-8")).hexdigest()
        self.assertEqual(block["input_encoding_ref"], f"sha256:{digest}")
        self.assertEqual(block["evaluated_predicate"], conjecture)
        self.assertEqual(block["checked_domain"], "simple graphs on 1..5 vertices, one per isomorphism class")
        self.assertEqual(block["enumeration_method"], "Sage graphs(n)")
        self.assertEqual(block["timeout_status"], "completed")
        self.assertNotIn("\n", block["outcome"])
        self.assertTrue(block["limitations"])
        self.assertTrue(all(isinstance(item, str) and item for item in block["limitations"]))
        self.assertEqual(computation_evidence_errors(block), [])
        self.assertEqual(
            (result["total_enumerated"], result["total_evaluated"], result["evaluation_errors"]), (9, 9, 0)
        )
        self.assertEqual(result["counterexamples_found"], 0)
        self.assertIs(result["counterexamples_truncated"], False)

    def test_counterexamples_fail_the_conjecture_and_the_list_is_capped(self):
        """All 25 counterexamples are counted, the first 20 are listed.

        counterexamples_found already counted every counterexample before this
        change, so that assertion and the 20-entry list are regression checks;
        the truncation flag and the failed status are new.
        """
        result = run_template("G.order() < 0", n_max=13)

        self.assertEqual(result["counterexamples_found"], 25)
        self.assertEqual(len(result["counterexamples"]), 20)
        self.assertIs(result["counterexamples_truncated"], True)
        block = result["computation_evidence"]
        self.assertEqual(block["result_status"], "failed")
        self.assertTrue(block["outcome"].startswith("conjecture refuted"), block["outcome"])
        self.assertEqual(block["coverage_status"], "bounded_complete")
        self.assertEqual(computation_evidence_errors(block), [])

    def test_a_witness_whose_invariants_raise_is_still_listed(self):
        result = run_template("G.size() != 1", n_max=3, family=with_a_broken_witness)

        self.assertEqual(result["counterexamples_found"], 1)
        [witness] = result["counterexamples"]
        self.assertEqual((witness["n"], witness["edges"]), (2, [[0, 1]]))
        self.assertIn("RuntimeError: stub chromatic_number failed", witness["witness_detail_error"])
        self.assertLessEqual(len(witness["witness_detail_error"]), 200)
        self.assertEqual(result["evaluation_errors"], 0)
        self.assertEqual(result["computation_evidence"]["result_status"], "failed")

    def test_total_checked_is_an_alias_of_total_evaluated(self):
        """total_checked used to count every enumerated graph, raising ones included."""
        result = run_template("1 / G.size() > 0", n_max=3)

        # K2 and K3 evaluate; the three edgeless graphs raise.
        self.assertEqual(result["total_checked"], 2)
        self.assertEqual(result["total_checked"], result["total_evaluated"])
        self.assertNotEqual(result["total_checked"], result["total_enumerated"])

    def test_counts_print_as_json_under_preparsed_integer_literals(self):
        """Under Sage the counts start from Integer(0), which json.dumps rejects.

        The template converts every count with int() before printing. Here its
        integer literals are wrapped as the preparser wraps them; the stand-in
        Integer, like Sage's, is not a Python int.
        """
        result = run_template("1 / G.size() > 1", n_max=4, preparse=True)

        counts = {
            key: result[key]
            for key in (
                "total_enumerated",
                "total_evaluated",
                "evaluation_errors",
                "total_checked",
                "counterexamples_found",
            )
        }
        self.assertEqual(
            counts,
            {
                "total_enumerated": 7,
                "total_evaluated": 3,
                "evaluation_errors": 4,
                "total_checked": 3,
                "counterexamples_found": 3,
            },
        )

    def test_an_empty_bound_checks_nothing_and_does_not_pass(self):
        """With n_max below 1 no graph is enumerated, so nothing passed."""
        result = run_template("G.size() >= 0", n_max=0)

        block = result["computation_evidence"]
        self.assertEqual(result["total_enumerated"], 0)
        self.assertEqual((block["result_status"], block["coverage_status"]), ("partial", "partial"))
        self.assertNotIn("exhaustiveness_argument", block)
        self.assertEqual(computation_evidence_errors(block), [])


if __name__ == "__main__":
    unittest.main()
