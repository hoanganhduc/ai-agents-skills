from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from installer.ai_agents_skills.cli import main
from installer.ai_agents_skills.openclaw_runtime_target_evidence import build_runtime_target_evidence
from installer.ai_agents_skills.openclaw_target_paths import validate_openclaw_target_home

# A runtime-backed skill that renders cleanly for openclaw and ships an executable
# (.py) -> shared-runtime-file requires helper-invocation evidence.
RUNTIME_SKILL = "graph-verifier"
SHARED_RUNTIME_EVIDENCE = (
    "native-loader", "quiescence-lock", "neutral-runtime-root",
    "runtime-pre-state", "compatibility-tuple-match", "helper-invocation",
)


def _mk_root(tmp: Path) -> Path:
    root = tmp / "home"
    (root / ".openclaw" / "skills").mkdir(parents=True)
    return root


def _write_evidence(out_dir: Path, root: Path, runtime_root: Path, types) -> list[str]:
    paths = validate_openclaw_target_home(root)
    rp = dict(
        target_realpath=paths["home_realpath"],
        managed_skills_realpath=paths["managed_skills_realpath"],
        runtime_realpath=str(runtime_root.resolve(strict=False)),
    )
    files = []
    for i, t in enumerate(types):
        ev = build_runtime_target_evidence(
            evidence_type=t, platform="linux", path_style="posix",
            observed_behavior=f"probed {t}", checks={"t": t}, **rp)
        p = out_dir / f"ev{i}.json"
        p.write_text(json.dumps(ev), encoding="utf-8")
        files.append(str(p))
    return files


def _run(argv: list[str]) -> dict:
    stream = io.StringIO()
    with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(io.StringIO()):
        code = main(argv)
    return {"code": code, "out": stream.getvalue()}


class RuntimeCliTest(unittest.TestCase):
    def test_dry_run_then_approve(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            root = _mk_root(tmp)
            rroot = tmp / "neutral-runtime"
            ev_paths = _write_evidence(tmp, root, rroot, SHARED_RUNTIME_EVIDENCE)

            argv = ["--json", "--root", str(root), "openclaw-runtime-dry-run-manifest",
                    "--skill", RUNTIME_SKILL, "--action-class", "shared-runtime-file",
                    "--runtime-root", str(rroot), "--source-commit", "abc123",
                    "--created-at", "2026-06-20T00:00:00Z"]
            for p in ev_paths:
                argv += ["--evidence", p]
            res = _run(argv)
            self.assertEqual(res["code"], 0, res["out"])
            manifest = json.loads(res["out"])
            self.assertEqual(manifest["manifest_schema_version"], "openclaw.target-manifest.v3")
            self.assertEqual(manifest["skill"], RUNTIME_SKILL)
            self.assertTrue(manifest["content_id"].startswith("content_"))
            self.assertEqual(manifest["approval"]["review_status"], "unreviewed")

            # approve
            mpath = tmp / "manifest.json"
            mpath.write_text(json.dumps(manifest), encoding="utf-8")
            res2 = _run(["--json", "openclaw-runtime-approve-manifest", "--manifest", str(mpath), "--reviewer", "me"])
            self.assertEqual(res2["code"], 0, res2["out"])
            approved = json.loads(res2["out"])
            self.assertEqual(approved["approval"]["review_status"], "approved")
            self.assertEqual(approved["approval"]["approval_hash"], approved["manifest_id"])

    def test_dry_run_without_evidence_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            root = _mk_root(tmp)
            rroot = tmp / "neutral-runtime"
            # only 2 of the required evidence types -> authorization fails -> nonzero exit
            ev_paths = _write_evidence(tmp, root, rroot, ("native-loader", "quiescence-lock"))
            argv = ["--json", "--root", str(root), "openclaw-runtime-dry-run-manifest",
                    "--skill", RUNTIME_SKILL, "--action-class", "shared-runtime-file",
                    "--runtime-root", str(rroot), "--source-commit", "abc"]
            for p in ev_paths:
                argv += ["--evidence", p]
            res = _run(argv)
            self.assertNotEqual(res["code"], 0)


if __name__ == "__main__":
    unittest.main()
