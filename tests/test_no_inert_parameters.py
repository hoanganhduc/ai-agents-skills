from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Trees whose Python is shipped source. `tests/` is excluded: a test helper may
# legitimately declare a parameter it does not read (fakes, stub callbacks that
# stand in for a real signature), and pinning those adds churn without protecting
# any runtime behaviour.
SCAN_ROOTS = ("canonical", "installer")
SKIP_PREFIXES = ("installer/vendor/",)


# A parameter is exempt on its shape alone when one of these holds. Each rule
# describes a signature whose width is a contract rather than an oversight, so a
# new instance of the shape needs no allowlist entry.
#
#   dunder        -- `__exit__(self, exc_type, exc, tb)` and friends: the
#                    interpreter calls these positionally, so the arity is fixed
#                    by the protocol, not by what the body happens to use.
#   vararg/kwarg  -- `*args` / `**kwargs` absorb a caller's surplus by design.
#   leading `_`   -- the conventional "declared, deliberately discarded" marker.
#   explicit del  -- `del unused` in the body is the same marker, written out.
#   ImportError   -- a function defined inside `except ImportError:` is a fallback
#                    that has to accept the real dependency's call shape.
#   NotImplemented-- a body whose last statement is `raise NotImplementedError`
#                    cannot return normally; its parameters are the declared
#                    contract of the implementation that has not shipped yet.


class _Collector(ast.NodeVisitor):
    def __init__(self, rel: str) -> None:
        self.rel = rel
        self.stack: list[str] = []
        self.import_fallback_depth = 0
        self.hits: list[tuple[str, str, int, str]] = []

    # `except ImportError:` bodies define fallback stubs.
    def visit_Try(self, node: ast.Try) -> None:
        for child in node.body + node.orelse + node.finalbody:
            self.visit(child)
        for handler in node.handlers:
            fallback = _catches_import_error(handler)
            if fallback:
                self.import_fallback_depth += 1
            for child in handler.body:
                self.visit(child)
            if fallback:
                self.import_fallback_depth -= 1

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._function(node)

    def _function(self, node) -> None:
        self.stack.append(node.name)
        qualname = ".".join(self.stack)
        if not self._exempt_function(node):
            args = node.args
            declared = [
                a.arg for a in (args.posonlyargs + args.args + args.kwonlyargs)
            ]
            declared = [d for d in declared if d not in {"self", "cls"}]
            declared = [d for d in declared if not d.startswith("_")]
            if declared:
                # A name counts as read if it is loaded anywhere under the
                # function, nested scopes included -- an over-approximation, so
                # every surviving hit is genuinely never read.
                loaded = {
                    n.id
                    for n in ast.walk(node)
                    if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
                }
                deleted = {
                    n.id
                    for n in ast.walk(node)
                    if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Del)
                }
                for name in declared:
                    if name not in loaded and name not in deleted:
                        self.hits.append((self.rel, qualname, node.lineno, name))
        self.generic_visit(node)
        self.stack.pop()

    def _exempt_function(self, node) -> bool:
        if node.name.startswith("__") and node.name.endswith("__"):
            return True
        if self.import_fallback_depth:
            return True
        return _always_raises_not_implemented(node)


def _catches_import_error(handler: ast.ExceptHandler) -> bool:
    caught = handler.type
    names: list[ast.expr] = []
    if isinstance(caught, ast.Tuple):
        names = list(caught.elts)
    elif caught is not None:
        names = [caught]
    for item in names:
        if isinstance(item, ast.Name) and "ImportError" in item.id:
            return True
        if isinstance(item, ast.Attribute) and "ImportError" in item.attr:
            return True
    return False


def _always_raises_not_implemented(node) -> bool:
    """The body's last statement is `raise NotImplementedError[...]`."""
    if not node.body:
        return False
    last = node.body[-1]
    if not isinstance(last, ast.Raise) or last.exc is None:
        return False
    exc = last.exc
    if isinstance(exc, ast.Call):
        exc = exc.func
    if isinstance(exc, ast.Name):
        return exc.id == "NotImplementedError"
    if isinstance(exc, ast.Attribute):
        return exc.attr == "NotImplementedError"
    return False


def scan() -> list[tuple[str, str, int, str]]:
    hits: list[tuple[str, str, int, str]] = []
    for top in SCAN_ROOTS:
        for path in sorted((ROOT / top).rglob("*.py")):
            rel = path.relative_to(ROOT).as_posix()
            if rel.startswith(SKIP_PREFIXES):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
            collector = _Collector(rel)
            collector.visit(tree)
            hits.extend(collector.hits)
    return hits


# Every surviving inert parameter, with the reason it is not a defect. A hit that
# is not listed here is a parameter a reader will take as load-bearing and that
# the body never reads -- fix the body or drop the parameter.
ALLOWED: dict[tuple[str, str, str], str] = {}


def _allow(rel: str, qualname: str, params: str, reason: str) -> None:
    for param in params.split():
        ALLOWED[(rel, qualname, param)] = reason


_S = "canonical/runtime/skills/"
_W = "canonical/runtime/workspace/"

# --- argparse / dispatch-table conformance -------------------------------------
# The dispatcher calls every handler in the family with the same arguments, so a
# handler that needs none of them still declares them.
_allow(_S + "calibre/cal.py", "cmd_doctor", "args",
       "cal.py dispatches every cmd_* with (args); doctor takes no options")
_allow(_S + "calibre/cal.py", "cmd_clean", "args",
       "cal.py dispatches every cmd_* with (args); clean takes no options")
_allow(_S + "zotero/zot.py", "cmd_sync_cache", "args",
       "zot.py dispatches every cmd_* with (args); sync-cache takes no options")
_allow(_S + "zotero/zot.py", "cmd_clean_staging", "args",
       "zot.py dispatches every cmd_* with (args); clean-staging takes no options")
_allow(_S + "zotero/zot.py", "_cmd_add_batch", "config client",
       "the add dispatcher hands every mode handler (args, config, client)")
_allow(_S + "zotero/zot.py", "_cmd_add_from_manifest", "config client",
       "the add dispatcher hands every mode handler (args, config, client)")
_allow(_S + "getscipapers-requester/gsp_openclaw_helper.py", "cmd_extract", "settings",
       "COMMANDS table calls every cmd_* with (args, settings)")
_allow(_S + "getscipapers-requester/gsp_openclaw_helper.py", "cmd_resolve", "settings",
       "COMMANDS table calls every cmd_* with (args, settings)")
_allow(_S + "getscipapers-requester/gsp_openclaw_helper.py", "cmd_introspect", "args settings",
       "COMMANDS table calls every cmd_* with (args, settings)")
_allow(_S + "getscipapers-requester/gsp_openclaw_helper.py", "cmd_run", "settings",
       "COMMANDS table calls every cmd_* with (args, settings)")
_allow(_S + "getscipapers-requester/gsp_openclaw_helper.py", "cmd_list_watches", "args",
       "COMMANDS table calls every cmd_* with (args, settings)")
_allow(_S + "hetzner-research-compute/hetzner_driver.py", "fetch_availability", "config",
       "main() calls every subcommand function with (args, config)")
_allow(_S + "hetzner-research-compute/hetzner_driver.py", "render_cloud_init", "config",
       "main() calls every subcommand function with (args, config)")
_allow(_S + "kaggle-research-compute/kaggle_driver.py", "status", "config",
       "main() calls every subcommand function with (args, config)")
_allow(_S + "kaggle-research-compute/kaggle_driver.py", "wait", "config",
       "main() calls every subcommand function with (args, config)")
_allow(_S + "kaggle-research-compute/kaggle_driver.py", "fetch", "config",
       "main() calls every subcommand function with (args, config)")
_allow(_W + "research_compute/cli.py", "command_wait", "config",
       "cli.py calls every command_* with (args, config)")
_allow(_S + "lean-research-library/lean_research_library.py", "intake_payload", "cfg",
       "every *_payload builder takes (cfg, args); intake reads only args")
_allow(_S + "venue-ranking-evidence/venue_ranking_evidence.py", "parse_doaj", "final_url",
       "every parse_* source adapter takes (payload, final_url)")
_allow(_S + "venue-ranking-evidence/venue_ranking_evidence.py", "parse_icore", "final_url",
       "every parse_* source adapter takes (payload, final_url)")

# --- console-script entrypoints ------------------------------------------------
# `main(argv=None)` is the shape every entrypoint in the tree presents, and the
# ones below parse nothing because they take no options.
_allow(_S + "manim-math-animation/mma/doctor.py", "main", "argv",
       "doctor entrypoint takes no options; keeps the tree-wide main(argv) shape")
_allow(_S + "slides-to-video/s2v/doctor.py", "main", "argv",
       "doctor entrypoint takes no options; keeps the tree-wide main(argv) shape")
_allow(_S + "url-to-screenshot-runtime/u2s/doctor.py", "main", "argv",
       "doctor entrypoint takes no options; keeps the tree-wide main(argv) shape")

# --- library-imposed signatures ------------------------------------------------
_allow(_S + "remote-bridge/remote_bridge.py",
       "_RejectTransportRedirects.redirect_request", "msg newurl",
       "urllib HTTPRedirectHandler.redirect_request override signature")

# --- injectable default hooks --------------------------------------------------
# The module-level hook is invoked as HOOK(config); the default implementation
# reads its credentials from the environment, so it needs no config.
_allow(_W + "research_compute/hetzner_backend.py", "_default_account_liveness_probe", "config",
       "ACCOUNT_LIVENESS_PROBE hook shape; the token comes from HCLOUD_TOKEN")
_allow(_W + "research_compute/kaggle_backend.py", "_default_kagglehub_validate", "config",
       "KAGGLEHUB_VALIDATE hook shape; kagglehub reads the token itself")

# --- documented partial implementations ----------------------------------------
_allow(_S + "annotated-review/pdf_annotator.py", "produce_merged_pdf", "companion_html_path",
       "documented: copies the marked PDF; the HTML->PDF merge is not implemented")
_allow(_S + "calibre/lib/verifier.py", "_verify_mobi", "header",
       "documented: MOBI accepted on size; the deep header check is not implemented")
_allow(_S + "remote-bridge/remote_bridge.py", "remember_notification_delivery", "event_id",
       "dedupe is keyed by the notification body, not the event id")


class NoInertParametersTest(unittest.TestCase):
    """A parameter that is declared and never read is a false interface.

    Finding #34 of the full-tree audit: 21 functions across the runtime and the
    installer declared parameters their bodies never touched, and every call site
    dutifully computed and passed a value. Each one told a reader something the
    code did not do -- `markup_page` appeared to apply its own page mapping on top
    of the caller's, `resolve_voice` appeared to pick a voice per presenter role,
    `included_minutes` appeared to look up an owner-scoped plan, `build_burn_args`
    appeared to set an output frame rate. This test pins the class shut.
    """

    def test_no_parameter_is_declared_and_never_read(self) -> None:
        unexplained = [
            (rel, qualname, line, param)
            for rel, qualname, line, param in scan()
            if (rel, qualname, param) not in ALLOWED
        ]
        self.assertEqual(
            [],
            unexplained,
            "declared-and-never-read parameters:\n"
            + "\n".join(
                f"  {rel}:{line} {qualname}({param})"
                for rel, qualname, line, param in unexplained
            )
            + "\nEither read the parameter, drop it and fix the call sites, or -- if"
            " the signature is a contract -- add it to ALLOWED with the reason.",
        )

    def test_the_allowlist_has_no_stale_entries(self) -> None:
        """An allowlist that outlives its hit hides the next real one."""

        live = {(rel, qualname, param) for rel, qualname, _line, param in scan()}
        stale = sorted(key for key in ALLOWED if key not in live)
        self.assertEqual(
            [],
            stale,
            "ALLOWED entries that no longer match any inert parameter "
            "(the parameter was read, renamed, or removed -- drop the entry):\n"
            + "\n".join(f"  {rel} {qualname}({param})" for rel, qualname, param in stale),
        )

    def test_the_scan_actually_reaches_the_tree(self) -> None:
        """Guards the sweep against silently scanning nothing."""

        files = [
            p
            for top in SCAN_ROOTS
            for p in (ROOT / top).rglob("*.py")
            if not p.relative_to(ROOT).as_posix().startswith(SKIP_PREFIXES)
        ]
        self.assertGreater(len(files), 200, "the scan roots went missing")


class ExemptionShapeTest(unittest.TestCase):
    """The structural exemptions must be narrow enough to still catch a defect."""

    def _scan_source(self, source: str) -> list[str]:
        collector = _Collector("<memory>")
        collector.visit(ast.parse(source))
        return [f"{q}({p})" for _rel, q, _line, p in collector.hits]

    def test_a_plain_inert_parameter_is_caught(self) -> None:
        self.assertEqual(
            ["f(unused)"], self._scan_source("def f(used, unused):\n    return used\n")
        )

    def test_a_parameter_read_only_in_a_nested_scope_is_not_a_hit(self) -> None:
        self.assertEqual(
            [],
            self._scan_source(
                "def f(x):\n    def g():\n        return x\n    return g\n"
            ),
        )

    def test_dunder_methods_are_exempt_but_their_siblings_are_not(self) -> None:
        source = (
            "class C:\n"
            "    def __exit__(self, exc_type, exc, tb):\n"
            "        return False\n"
            "    def close(self, exc_type):\n"
            "        return False\n"
        )
        self.assertEqual(["C.close(exc_type)"], self._scan_source(source))

    def test_an_import_fallback_is_exempt_but_the_try_body_is_not(self) -> None:
        source = (
            "try:\n"
            "    def real(a):\n"
            "        return None\n"
            "except ImportError:\n"
            "    def real(a):\n"
            "        return None\n"
        )
        self.assertEqual(["real(a)"], self._scan_source(source))

    def test_a_not_implemented_stub_is_exempt_but_a_returning_one_is_not(self) -> None:
        source = (
            "def stub(a):\n"
            "    raise NotImplementedError('later')\n"
            "def guarded(a):\n"
            "    if True:\n"
            "        return None\n"
            "    raise NotImplementedError('later')\n"
            "def returns(a):\n"
            "    raise ValueError('now')\n"
        )
        self.assertEqual(["returns(a)"], self._scan_source(source))

    def test_an_explicit_del_is_exempt(self) -> None:
        self.assertEqual(
            [], self._scan_source("def f(a):\n    del a\n    return None\n")
        )


if __name__ == "__main__":
    unittest.main()
