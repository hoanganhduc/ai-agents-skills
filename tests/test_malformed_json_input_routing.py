"""Regression: a malformed JSON input must not escape a skill's error envelope.

Every runtime here already reports a bad input as structured output -- zotero
prints ``{"status": "error", "code": "CONFIG_MISSING"}`` for a config that is
not there, graph-verifier prints ``{"ok": false, "error": "invalid graph
input"}`` for a payload it cannot build a graph from, opengauss prints
``{"error_code": "unknown_run"}`` for a run it has no state for. In each case
the ``json.loads`` that reads the file sat *outside* that handling, so the file
being present and unparseable produced a bare ``JSONDecodeError`` traceback:
empty stdout, a stack trace on stderr, and nothing for a caller to read.

That is the ordinary failure, not an exotic one. These files are hand-authored
between commands -- a tikz spec, a slides-to-video transcript, a loop's
failover seed -- or written non-atomically by the skill itself, so a truncated
one is what an interrupted run leaves behind.

Each test below drives the real CLI with a malformed input and asserts three
things: stdout parses as JSON, the envelope names the failure, and stderr
carries no traceback. The paired control drives the same command with a
well-formed input and pins that its behaviour did not move.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# The graph-verifier tests load deep-research's validator in this process.
sys.dont_write_bytecode = True

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS = REPO_ROOT / "canonical" / "runtime" / "skills"
WORKSPACE = REPO_ROOT / "canonical" / "runtime" / "workspace"

MALFORMED = '{\n  "a": 1,\n}\n'  # trailing comma: the commonest hand-edit slip


def run(args, *, cwd=None, env=None):
    """Run a runtime script and return (rc, stdout, stderr)."""
    full_env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    if env:
        full_env.update(env)
    proc = subprocess.run([sys.executable, *[str(a) for a in args]], cwd=cwd,
                          capture_output=True, text=True, encoding="utf-8", env=full_env, timeout=120)
    return proc.returncode, proc.stdout, proc.stderr


def assert_no_traceback(case, stderr):
    case.assertNotIn("Traceback (most recent call last)", stderr)
    case.assertNotIn("JSONDecodeError", stderr)


def computation_evidence_errors(block):
    """Return what deep-research's computation-evidence validator reports for ``block``."""
    path = SKILLS / "deep-research-workflow" / "deep_research_workflow.py"
    spec = importlib.util.spec_from_file_location("drw_graph_verifier_block", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    errors = []
    module.validate_computation_evidence(block, Path("evidence.jsonl"), errors, 1)
    return errors


def path_edges(vertices):
    """Edge list of a path on ``vertices`` vertices."""
    return [[i, i + 1] for i in range(vertices - 1)]


class GraphVerifierTests(unittest.TestCase):
    """The skill doc tells the caller to read the JSON result from stdout.

    That result must not claim more than was computed: an input the verifier
    cannot check ends with no verdict, and only a property computed on the
    input graph can match or contradict an expected value.
    """

    SCRIPT = SKILLS / "graph-verifier" / "graph_verifier.py"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _run(self, text):
        path = self.tmp / "input.json"
        path.write_text(text, encoding="utf-8")
        return run([self.SCRIPT, "--input", path])

    def test_a_well_formed_graph_is_still_verified(self):
        rc, out, err = self._run('{"edges": [[1,2],[2,3],[3,1]]}')
        self.assertEqual(rc, 0, err)
        payload = json.loads(out)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["connected"])
        self.assertEqual(payload["n"], 3)

    def test_a_well_formed_file_describing_no_graph_is_still_routed(self):
        rc, out, err = self._run('{"graph_data": {"not": "a node-link graph"}}')
        self.assertEqual(rc, 0, err)
        payload = json.loads(out)
        self.assertFalse(payload["ok"])
        self.assertIn("invalid graph input", payload["error"])

    def test_a_malformed_file_reaches_stdout_as_the_same_envelope(self):
        rc, out, err = self._run(MALFORMED)
        assert_no_traceback(self, err)
        payload = json.loads(out)  # this is what used to be empty
        self.assertFalse(payload["ok"])
        self.assertIn("invalid graph input", payload["error"])

    def _verify(self, payload):
        return self._run(json.dumps(payload))

    def test_input_failures_are_no_verdict_without_a_graph(self):
        """Nothing to check must not become a report on the built-in Petersen graph."""
        cases = {
            "no arguments": lambda: run([self.SCRIPT]),
            "an empty --input": lambda: run([self.SCRIPT, "--input", ""]),
            "a missing --input path": lambda: run([self.SCRIPT, "--input", self.tmp / "absent.json"]),
            "a directory as --input": lambda: run([self.SCRIPT, "--input", self.tmp]),
            "an empty file": lambda: self._run(""),
            "malformed JSON": lambda: self._run(MALFORMED),
            "a payload that is not an object": lambda: self._run("[[1, 2]]"),
            "a payload naming no graph": lambda: self._verify({"claim": "x"}),
            "an invalid graph": lambda: self._verify({"graph_data": {"not": "a node-link graph"}}),
            "expected values that are not a map": lambda: self._verify({"edges": [[1, 2]], "expected": [1]}),
        }
        for label, call in cases.items():
            with self.subTest(case=label):
                rc, out, err = call()
                self.assertEqual(rc, 0, err)
                assert_no_traceback(self, err)
                payload = json.loads(out)
                self.assertFalse(payload["ok"])
                self.assertEqual(payload.get("verdict"), "no_verdict")
                self.assertNotIn("n", payload)

    def test_demo_runs_the_petersen_graph(self):
        rc, out, err = run([self.SCRIPT, "--demo"])
        self.assertEqual(rc, 0, err)
        payload = json.loads(out)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["input_source"], "builtin_demo")
        self.assertEqual((payload["n"], payload["m"]), (10, 15))
        self.assertEqual(payload["computation_evidence"]["input_encoding_ref"], "builtin:petersen_graph")
        path = self.tmp / "input.json"
        path.write_text('{"edges": [[1, 2]]}', encoding="utf-8")
        rc, _out, err = run([self.SCRIPT, "--demo", "--input", path])
        self.assertEqual(rc, 2, "--demo and --input name two inputs; argparse refuses the pair")
        self.assertIn("not allowed with", err)

    def test_unchecked_expected_keys_are_never_mismatches(self):
        """A property the verifier did not compute neither matches nor contradicts."""
        triangle = {"edges": [[1, 2], [2, 3], [3, 1]]}
        cases = {
            "an unknown property": (triangle, "chromatic_number", 3),
            "the diameter of a disconnected graph": ({"edges": [[1, 2], [3, 4]]}, "diameter", None),
            "the diameter of a 301-vertex path": ({"edges": path_edges(301)}, "diameter", 300),
            "the girth of an edgeless graph": ({"edges": [], "nodes": [1, 2, 3]}, "girth", None),
            "planarity above the size cap": ({"edges": path_edges(1001)}, "planar", True),
        }
        for label, (graph, key, want) in cases.items():
            with self.subTest(case=label):
                rc, out, err = self._verify({**graph, "expected": {key: want}})
                self.assertEqual(rc, 0, err)
                payload = json.loads(out)
                self.assertNotIn(key, payload.get("mismatches", {}))
                self.assertFalse(payload["matches_expected"])
                self.assertIn(key, payload["unchecked_expected_keys"])
                block = payload["computation_evidence"]
                self.assertEqual((block["result_status"], block["coverage_status"]), ("partial", "partial"))

    def test_result_status_follows_checked_keys_only(self):
        triangle = {"edges": [[1, 2], [2, 3], [3, 1]]}
        cases = {
            "no expected values": ({}, "partial", "partial"),
            "a contradicted value": ({"bipartite": True}, "failed", "exhaustive"),
            "matching values": ({"n": 3, "connected": True}, "passed", "exhaustive"),
        }
        for label, (expected, result_status, coverage) in cases.items():
            with self.subTest(case=label):
                rc, out, err = self._verify({**triangle, "expected": expected})
                self.assertEqual(rc, 0, err)
                payload = json.loads(out)
                block = payload["computation_evidence"]
                self.assertEqual((block["result_status"], block["coverage_status"]), (result_status, coverage))
                self.assertEqual("exhaustiveness_argument" in block, coverage == "exhaustive")
                self.assertEqual(computation_evidence_errors(block), [])
                digest = hashlib.sha256((self.tmp / "input.json").read_bytes()).hexdigest()
                self.assertEqual(block["input_encoding_ref"], f"sha256:{digest}")
                self.assertEqual(block["evaluated_predicate"], json.dumps(expected, sort_keys=True))
                self.assertFalse(payload["claim_evaluated"])
                self.assertIn("the claim text is not evaluated", block["limitations"])

    def test_a_long_degree_sequence_reports_length_and_truncation(self):
        expected = {"degree_sequence": [2] * 58 + [1, 1]}
        rc, out, err = self._verify({"edges": path_edges(60), "expected": expected})
        self.assertEqual(rc, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["degree_sequence_length"], 60)
        self.assertTrue(payload["degree_sequence_truncated"])
        self.assertEqual(len(payload["degree_sequence"]), 50)
        # The comparison reads the whole sequence, not the printed prefix.
        self.assertTrue(payload["matches_expected"])
        self.assertEqual(payload["computation_evidence"]["result_status"], "passed")

    def test_a_null_expected_map_reads_as_no_expected_values(self):
        rc, out, err = self._verify({"edges": [[1, 2]], "expected": None})
        self.assertEqual(rc, 0, err)
        assert_no_traceback(self, err)
        payload = json.loads(out)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["computation_evidence"]["evaluated_predicate"], "{}")

    def test_girth_is_null_when_infinite_and_unchecked_when_it_raises(self):
        """Stub nx.girth, which networkx 2.8 lacks and newer releases give inf on acyclic graphs."""
        driver = (
            "import runpy, sys\n"
            "import networkx as nx\n"
            "def acyclic(graph):\n"
            "    return float('inf')\n"
            "def broken(graph):\n"
            "    raise RuntimeError('girth failed')\n"
            "nx.girth = acyclic if sys.argv[3] == 'inf' else broken\n"
            "script, source = sys.argv[1], sys.argv[2]\n"
            "sys.argv = [script, '--input', source]\n"
            "runpy.run_path(script, run_name='__main__')\n"
        )

        def strict(constant):
            raise ValueError(f"{constant} is not JSON")

        path = self.tmp / "input.json"
        path.write_text(json.dumps({"edges": path_edges(4), "expected": {"girth": None}}), encoding="utf-8")
        cases = {"inf": ("checked", True), "raises": ("error", False)}
        for mode, (status, matches) in cases.items():
            with self.subTest(girth=mode):
                rc, out, err = run(["-c", driver, self.SCRIPT, path, mode])
                self.assertEqual(rc, 0, err)
                payload = json.loads(out, parse_constant=strict)
                self.assertIsNone(payload["girth"])
                self.assertNotIn("girth", payload.get("mismatches", {}))
                self.assertEqual(payload["matches_expected"], matches)
                self.assertEqual(payload["property_status"]["girth"], status)
                self.assertEqual("girth" in payload["unchecked_expected_keys"], not matches)


class OpenGaussRunStateTests(unittest.TestCase):
    """status.json is rewritten by kill, so an interrupt can leave it torn."""

    SCRIPT = SKILLS / "opengauss" / "opengauss.py"
    COMMANDS = ("status", "harvest", "kill")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.work = self.tmp / "work"
        self.run_dir = self.work / "gauss_runs" / "run-abc"
        self.run_dir.mkdir(parents=True)
        self.status = self.run_dir / "status.json"

    def _run(self, command, run_id="run-abc"):
        rc, out, err = run([self.SCRIPT, command, "--run-id", run_id, "--work-dir", self.work],
                           env={"PYTHONPATH": str(WORKSPACE)})
        return rc, out, err

    def _healthy(self):
        self.status.write_text(
            json.dumps({"run_id": "run-abc", "workflow": "prove", "status": "running"}) + "\n",
            encoding="utf-8")

    def test_an_absent_run_is_still_reported_as_unknown_run(self):
        for command in self.COMMANDS:
            with self.subTest(command=command):
                rc, out, err = self._run(command, run_id="run-missing")
                self.assertEqual(rc, 2, err)
                payload = json.loads(out)
                self.assertEqual(payload["error_code"], "unknown_run")

    def test_a_healthy_run_is_still_read(self):
        for command in self.COMMANDS:
            with self.subTest(command=command):
                self._healthy()
                rc, out, err = self._run(command)
                self.assertEqual(rc, 0, err)
                self.assertTrue(json.loads(out)["ok"])

    def test_a_torn_run_state_is_named_rather_than_crashing(self):
        for text in ("", MALFORMED):
            for command in self.COMMANDS:
                with self.subTest(command=command, state=repr(text)):
                    self.status.write_text(text, encoding="utf-8")
                    rc, out, err = self._run(command)
                    assert_no_traceback(self, err)
                    self.assertEqual(rc, 2)
                    payload = json.loads(out)
                    self.assertFalse(payload["ok"])
                    self.assertEqual(payload["error_code"], "unreadable_run_state")

    def test_kill_replaces_the_state_file_by_rename(self):
        """The write that produces the torn file above must not truncate first."""
        self._healthy()
        rc, out, err = self._run("kill")
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(self.status.read_text(encoding="utf-8"))["status"], "killed")
        self.assertEqual(sorted(p.name for p in self.run_dir.iterdir()), ["status.json"])


class FailoverSettingsTests(unittest.TestCase):
    """--from-json points at a hand-edited seed; the loop file is hand-edited too."""

    SCRIPT = SKILLS / "autonomous-research-loop-runtime" / "apply_failover_settings.py"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.loop = self.tmp / "loop"
        self.loop.mkdir()

    def _seed(self, text):
        path = self.tmp / "seed.json"
        path.write_text(text, encoding="utf-8")
        return path

    def test_a_well_formed_seed_is_still_applied(self):
        rc, out, err = run([self.SCRIPT, "--dir", self.loop,
                            "--from-json", self._seed('{"primary_order": ["grok"]}')])
        self.assertEqual(rc, 0, err)
        self.assertTrue(json.loads(out)["ok"])
        written = json.loads((self.loop / "failover.json").read_text(encoding="utf-8"))
        self.assertEqual(written["primary_order"], ["grok"])

    def test_a_malformed_seed_is_named_rather_than_crashing(self):
        rc, out, err = run([self.SCRIPT, "--dir", self.loop, "--from-json", self._seed(MALFORMED)])
        assert_no_traceback(self, err)
        self.assertEqual(rc, 1)
        payload = json.loads(out)
        self.assertFalse(payload["ok"])
        self.assertIn("--from-json seed is not readable JSON", payload["error"])

    def test_a_torn_loop_file_is_named_rather_than_crashing(self):
        (self.loop / "failover.json").write_text('{"primary_order": [', encoding="utf-8")
        rc, out, err = run([self.SCRIPT, "--dir", self.loop, "--research-title", "TS_k acyclicity"])
        assert_no_traceback(self, err)
        self.assertEqual(rc, 1)
        payload = json.loads(out)
        self.assertFalse(payload["ok"])
        self.assertIn("failover.json is not readable JSON", payload["error"])


class TikzDrawTests(unittest.TestCase):
    """Specs, designs, contracts and briefs are authored between commands."""

    SCRIPT = SKILLS / "tikz-draw" / "tikz_draw.py"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _verify_design(self, text):
        artifacts = self.tmp / "artifacts.json"
        artifacts.write_text(text, encoding="utf-8")
        return run([self.SCRIPT, "verify-design", "--artifacts", artifacts,
                    "--work-dir", self.tmp / "wd"])

    def test_a_well_formed_manifest_still_reports_its_missing_keys(self):
        rc, _out, err = self._verify_design('{"figure_id": "fig1"}')
        self.assertEqual(rc, 1)
        assert_no_traceback(self, err)
        self.assertIn("artifact manifest missing required keys", err)

    def test_a_malformed_manifest_is_reported_the_same_way(self):
        rc, _out, err = self._verify_design(MALFORMED)
        self.assertEqual(rc, 1)
        assert_no_traceback(self, err)
        self.assertIn("is not valid JSON", err)
        # The routed case above is a single line; so is this one now.
        self.assertEqual(len([ln for ln in err.splitlines() if ln.strip()]), 1)


class SlidesToVideoTests(unittest.TestCase):
    """The transcript is reviewed and edited by hand before the render gate."""

    SCRIPT = SKILLS / "slides-to-video" / "slides_to_video_runtime.py"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.work = self.tmp / "wd"
        self.work.mkdir()

    def test_an_empty_work_dir_still_reports_its_status(self):
        rc, out, err = run([self.SCRIPT, "status", "--work-dir", self.work])
        self.assertEqual(rc, 0, err)
        self.assertFalse(json.loads(out)["analyzed"])

    def test_a_malformed_work_dir_file_is_named_rather_than_crashing(self):
        (self.work / "config.json").write_text(MALFORMED, encoding="utf-8")
        rc, out, err = run([self.SCRIPT, "status", "--work-dir", self.work])
        assert_no_traceback(self, err)
        self.assertEqual(rc, 2)
        payload = json.loads(out)
        self.assertEqual(payload["error"], "invalid_input")
        self.assertIn("invalid JSON", payload["message"])


class SkillConfigTests(unittest.TestCase):
    """zotero and calibre both route a *missing* config and both crashed on a
    malformed one, from the read every command in each skill goes through."""

    DRIVER = (
        "import sys\n"
        "sys.path.insert(0, '.')\n"
        "from lib.config import load_config\n"
        "load_config()\n"
        "print('LOADED', flush=True)\n"
    )

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _load(self, skill, config_arg=""):
        """Call load_config from inside the skill dir, as every command does."""
        code = self.DRIVER.replace("load_config()", f"load_config({config_arg})")
        proc = subprocess.run([sys.executable, "-c", code], cwd=SKILLS / skill,
                              capture_output=True, text=True, encoding="utf-8", timeout=120,
                              env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        return proc.returncode, proc.stdout, proc.stderr

    def test_zotero_names_a_missing_config(self):
        rc, out, err = self._load("zotero", f"config_path={str(self.tmp / 'nope.json')!r}")
        self.assertEqual(rc, 1, err)
        payload = json.loads(out)
        self.assertEqual(payload["code"], "CONFIG_MISSING")

    def test_zotero_names_a_malformed_config_too(self):
        path = self.tmp / "config.json"
        path.write_text(MALFORMED, encoding="utf-8")
        rc, out, err = self._load("zotero", f"config_path={str(path)!r}")
        assert_no_traceback(self, err)
        self.assertEqual(rc, 1)
        payload = json.loads(out)
        self.assertEqual(payload["code"], "CONFIG_UNREADABLE")
        self.assertIn(str(path), payload["message"])

    def test_calibre_names_a_malformed_config(self):
        """calibre reads config.json from its own skill dir, so drive a copy."""
        skill = self.tmp / "calibre"
        shutil.copytree(SKILLS / "calibre", skill)
        (skill / "config.json").write_text(MALFORMED, encoding="utf-8")
        proc = subprocess.run([sys.executable, "-c", self.DRIVER], cwd=skill,
                              capture_output=True, text=True, encoding="utf-8", timeout=120,
                              env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        assert_no_traceback(self, proc.stderr)
        self.assertEqual(proc.returncode, 1)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["status"], "error")
        self.assertIn("could not be read", payload["message"])

    def test_calibre_still_loads_without_a_config_file(self):
        skill = self.tmp / "calibre"
        shutil.copytree(SKILLS / "calibre", skill)
        (skill / "config.json").unlink(missing_ok=True)
        proc = subprocess.run([sys.executable, "-c", self.DRIVER], cwd=skill,
                              capture_output=True, text=True, encoding="utf-8", timeout=120,
                              env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("LOADED", proc.stdout)


if __name__ == "__main__":
    unittest.main()
