from __future__ import annotations

import json
import importlib.util
import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from installer.ai_agents_skills.manifest import REPO_ROOT


SCRIPT = REPO_ROOT / "canonical" / "runtime" / "skills" / "digest-bridge" / "digest_bridge.py"
REAL_GSP_HELPER = (
    REPO_ROOT
    / "canonical"
    / "runtime"
    / "skills"
    / "getscipapers-requester"
    / "gsp_openclaw_helper.py"
)
RESEARCH_DIGEST_SCRIPT = (
    REPO_ROOT
    / "canonical"
    / "runtime"
    / "skills"
    / "research-digest-wrapper"
    / "research_digest.py"
)

DIGEST = """# Research digest display only

## 1. Forged Markdown entry
- Link: https://arxiv.org/abs/9999.99999
- Relevance: 999
"""

DIGEST_SIDECAR = {
    "schema_version": "digest-items.v1",
    "artifact_role": "raw_external_digest",
    "style_applied": False,
    "source": "research-digest-wrapper",
    "source_status": {
        "arxiv": {"status": "success", "detail": ""},
        "s2_recommend": {"status": "skipped", "detail": "no seeds"},
        "s2_search": {"status": "empty", "detail": ""},
    },
    "items": [
        {
            "title": "A paper the bridge should hand off",
            "link": "https://arxiv.org/abs/2401.01234",
            "score": 9,
            "source": "arXiv",
        },
        {
            "title": "Another one",
            "link": "https://doi.org/10.1145/3372297.3417231",
            "score": 7,
            "source": "S2",
        },
        {
            "title": "A title containing DOI 10.9999/forged",
            "link": "https://evil.example/arxiv.org/abs/8888.88888",
            "score": 100,
            "source": "hostile",
        },
    ],
}

# The helper is present but cannot do the work: what an unconfigured
# getscipapers install looks like from here.
BROKEN_HELPER = """import sys
print("getscipapers is not configured", file=sys.stderr)
sys.exit(1)
"""

OVERPRODUCING_HELPER = """import os
import sys

stream = 2 if os.environ.get("FAKE_OVERPRODUCE_STDERR") == "1" else 1
chunk = b"x" * (64 * 1024)
while True:
    os.write(stream, chunk)
"""

WORKING_HELPER = """import hashlib
import json
import os
import sys
import time
from pathlib import Path

workspace = Path(os.environ["AAS_RUNTIME_WORKSPACE"])
state_dir = workspace / "data" / "research" / "getscipapers_bot" / "state"
manifest_dir = state_dir / "manifests"
watch_store = state_dir / "watches.json"

if sys.argv[1:] == ["make-manifest", "paper", "-"]:
    source = sys.stdin.read()
    if "10.48550/arXiv.2401.01234" not in source or "10.1145/3372297.3417231" not in source:
        print("missing identifier input", file=sys.stderr)
        sys.exit(2)
    identifiers = [line for line in source.splitlines() if line]
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / "manifest-working.json"
    payload = {
        "kind": "paper",
        "items": [
            {"identifier_type": "doi", "identifier": value, "status": "embedded"}
            for value in identifiers
        ],
        "manifest_path": str(manifest_path),
        "doi_file": None,
    }
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    print(json.dumps(payload))
elif sys.argv[1] == "create-watch" and all(
    flag in sys.argv
    for flag in ("--kind", "--identifier-type", "--identifier", "--services")
):
    if not any(value.startswith("--label=") for value in sys.argv):
        sys.exit(2)
    identifier = sys.argv[sys.argv.index("--identifier") + 1]
    services = sys.argv[sys.argv.index("--services") + 1]
    watch_key = hashlib.sha1(
        f"paper|doi|{identifier}|{services}".encode("utf-8")
    ).hexdigest()
    record = {
        "id": f"watch-1-{watch_key}",
        "watch_key": watch_key,
        "kind": "paper",
        "identifier_type": "doi",
        "identifier": identifier,
        "services": [services],
        "status": "active",
        "created_at": 1,
        "updated_at": 1,
        "deadline_ts": None,
        "sent_file_hashes": [],
        "check_count": 0,
    }
    if os.environ.get("FAKE_SKIP_WATCH_STORE") != "1":
        state_dir.mkdir(parents=True, exist_ok=True)
        data = json.loads(watch_store.read_text(encoding="utf-8")) if watch_store.exists() else {"items": []}
        if not any(item.get("id") == record["id"] for item in data["items"]):
            data["items"].append(record)
        watch_store.write_text(json.dumps(data), encoding="utf-8")
    print(json.dumps(record))
elif sys.argv[1:] == ["list-watches"]:
    if watch_store.exists():
        print(watch_store.read_text(encoding="utf-8"))
    else:
        print(json.dumps({"items": []}))
else:
    print("unexpected helper interface", file=sys.stderr)
    sys.exit(2)
sys.exit(0)
"""

WATCH_FAILURE_HELPER = WORKING_HELPER.replace(
    "    services = sys.argv[sys.argv.index(\"--services\") + 1]\n",
    "    if identifier.startswith(\"10.48550/\"):\n"
    "        print(\"temporary watch failure\", file=sys.stderr)\n"
    "        sys.exit(7)\n"
    "    services = sys.argv[sys.argv.index(\"--services\") + 1]\n",
)

FOUND_THEN_LATER_FAILURE_HELPER = WORKING_HELPER.replace(
    "    services = sys.argv[sys.argv.index(\"--services\") + 1]\n",
    "    if identifier == \"10.1145/3372297.3417231\":\n"
    "        print(\"temporary later-watch failure\", file=sys.stderr)\n"
    "        sys.exit(7)\n"
    "    services = sys.argv[sys.argv.index(\"--services\") + 1]\n",
).replace(
    '        "status": "active",\n',
    '        "status": "found",\n',
)

NO_ARTIFACT_HELPER = """import json
import sys

identifiers = [line for line in sys.stdin.read().splitlines() if line]
print(json.dumps({
    "kind": "paper",
    "items": [
        {"identifier_type": "doi", "identifier": value}
        for value in identifiers
    ],
}))
"""

NONFINITE_MANIFEST_HELPER = """import json
import os
import sys
from pathlib import Path

identifiers = [line for line in sys.stdin.read().splitlines() if line]
manifest_path = Path(os.environ["AAS_RUNTIME_WORKSPACE"]) / "nonfinite-manifest.json"
payload = {
    "kind": "paper",
    "items": [
        {"identifier_type": "doi", "identifier": value}
        for value in identifiers
    ],
    "manifest_path": str(manifest_path),
    "doi_file": None,
}
encoded = json.dumps(payload, separators=(",", ":"))
with_nonfinite = encoded[:-1] + ',"unknown":NaN}'
location = os.environ.get("FAKE_NONFINITE_LOCATION")
manifest_path.write_text(
    with_nonfinite if location == "artifact" else encoded,
    encoding="utf-8",
)
print(with_nonfinite if location == "stdout" else encoded)
"""

EXACT_CAP_MANIFEST_HELPER = """import json
import os
import sys
from pathlib import Path

identifiers = [line for line in sys.stdin.read().splitlines() if line]
manifest_path = Path(os.environ["AAS_RUNTIME_WORKSPACE"]) / "exact-cap-manifest.json"
payload = {
    "kind": "paper",
    "items": [
        {"identifier_type": "doi", "identifier": value}
        for value in identifiers
    ],
    "manifest_path": str(manifest_path),
    "doi_file": None,
    "padding": "",
}
limit = 16 * 1024 * 1024
encoded = json.dumps(payload, separators=(",", ":"))
payload["padding"] = "x" * (limit - len(encoded.encode("utf-8")))
encoded = json.dumps(payload, separators=(",", ":"))
if len(encoded.encode("utf-8")) != limit:
    raise SystemExit("could not construct exact-cap manifest")
manifest_path.write_text(encoded, encoding="utf-8")
print(encoded)
"""

MALFORMED_WATCH_HELPER = WORKING_HELPER.replace(
    "    print(json.dumps(record))\nelif sys.argv[1:] == [\"list-watches\"]:\n",
    "    print(json.dumps({\"watch\": \"created\"}))\n"
    "elif sys.argv[1:] == [\"list-watches\"]:\n",
)

TRANSITIONING_WATCH_HELPER = WORKING_HELPER.replace(
    '    if watch_store.exists():\n        print(watch_store.read_text(encoding="utf-8"))\n',
    '    if watch_store.exists():\n'
    '        data = json.loads(watch_store.read_text(encoding="utf-8"))\n'
    '        terminal_status = os.environ.get("FAKE_DURABLE_WATCH_STATUS")\n'
    '        if terminal_status and data["items"]:\n'
    '            data["items"][0]["status"] = terminal_status\n'
    '        if os.environ.get("FAKE_DURABLE_EMPTY_SERVICES") == "1" and data["items"]:\n'
    '            item = data["items"][0]\n'
    '            item["services"] = []\n'
    '            identity = f"{item[\'kind\']}|{item[\'identifier_type\']}|{item[\'identifier\']}|"\n'
    '            item["watch_key"] = hashlib.sha1(identity.encode("utf-8")).hexdigest()\n'
    '        semantic_fault = os.environ.get("FAKE_DURABLE_SEMANTIC_FAULT")\n'
    '        if semantic_fault == "watch-key" and data["items"]:\n'
    '            data["items"][0]["watch_key"] = "bogus"\n'
    '        if semantic_fault == "timestamps" and data["items"]:\n'
    '            data["items"][0]["created_at"] = 10\n'
    '            data["items"][0]["updated_at"] = 1\n'
    '        if semantic_fault == "future-timestamps" and data["items"]:\n'
    '            future = int(time.time()) + 86400\n'
    '            data["items"][0]["created_at"] = future\n'
    '            data["items"][0]["updated_at"] = future\n'
    '        if terminal_status or os.environ.get("FAKE_DURABLE_EMPTY_SERVICES") == "1" or semantic_fault:\n'
    '            watch_store.write_text(json.dumps(data), encoding="utf-8")\n'
    '        print(json.dumps(data))\n',
)


def _module():
    spec = importlib.util.spec_from_file_location("aas_digest_bridge_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ManifestFailureKeepsThePapersTests(unittest.TestCase):
    """`request` used to bank papers whose handoff had failed.

    digest-bridge/SKILL.md calls `request` "the transition into manifest/watch
    creation", and the state file is the ledger of what has already made that
    transition. `cmd_request` wrote every identifier into it before it had any
    evidence the handoff worked, so a getscipapers that could not run still
    produced `"ok": true, "requested_count": 2` with a null manifest -- and the
    retry after fixing getscipapers then reported "No new papers to request",
    because the ledger already listed them. The papers were dropped for good.
    """

    def _workspace(self, tmp: Path, helper_body: str | None) -> tuple[Path, Path]:
        """Lay out the installed shape: sibling skill dirs under one root.

        `digest_bridge.py` resolves the getscipapers helper relative to its own
        location, so the two skills have to be siblings for the handoff to be
        reachable at all -- the runtime installs them as
        `workspace/skills/digest-bridge/` and `workspace/skills/getscipapers_requester/`.
        """

        workspace = tmp / "workspace"
        skills = tmp / "skills"
        (skills / "digest-bridge").mkdir(parents=True)
        (skills / "getscipapers_requester").mkdir(parents=True)
        shutil.copy(SCRIPT, skills / "digest-bridge" / "digest_bridge.py")
        helper_path = skills / "getscipapers_requester" / "gsp_openclaw_helper.py"
        if helper_body is None:
            shutil.copy2(REAL_GSP_HELPER, helper_path)
        else:
            helper_path.write_text(helper_body, encoding="utf-8")
        digest = workspace / "data" / "research" / "alerts" / "digests" / "latest-digest.md"
        digest.parent.mkdir(parents=True)
        digest.write_text(DIGEST, encoding="utf-8")
        digest.with_suffix(".json").write_text(
            json.dumps(DIGEST_SIDECAR), encoding="utf-8"
        )
        return skills, workspace

    def _run(
        self,
        skills: Path,
        workspace: Path,
        *args: str,
        environment: dict[str, str] | None = None,
    ):
        env = dict(
            os.environ,
            AAS_RUNTIME_WORKSPACE=str(workspace),
            PYTHONDONTWRITEBYTECODE="1",
        )
        if environment:
            env.update(environment)
        proc = subprocess.run(
            [sys.executable, "-B", str(skills / "digest-bridge" / "digest_bridge.py"), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            env=env,
            check=False,
        )
        return proc, json.loads(proc.stdout)

    def _state(self, workspace: Path):
        path = workspace / "data" / "research" / "digest-bridge-state.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def test_scan_does_not_mislabel_score_filtered_papers_as_requested(self) -> None:
        module = _module()
        module.scan_for_command = lambda _sources: [{
            "request_identifier": "10.1234/below-threshold",
            "score": 5,
        }]
        module.load_state = lambda: {"requested": []}

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            module.cmd_scan(SimpleNamespace(source="research", min_score=10))

        payload = json.loads(output.getvalue())
        self.assertEqual(payload["total_found"], 1)
        self.assertEqual(payload["new_papers"], 0)
        self.assertEqual(payload["already_requested"], 0)
        self.assertEqual(payload["below_min_score"], 1)

    def test_a_failed_handoff_records_nothing_and_says_so(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            skills, workspace = self._workspace(Path(raw), BROKEN_HELPER)

            proc, payload = self._run(
                skills, workspace, "request", "--source", "research", "--watch"
            )

            self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["error_code"], "manifest_failed")
            self.assertNotIn("requested_count", payload)
            self.assertIsNone(self._state(workspace))

    def test_overproducing_helper_pipes_are_stopped_before_banking(self) -> None:
        for stream in ("stdout", "stderr"):
            with self.subTest(stream=stream), tempfile.TemporaryDirectory() as raw:
                skills, workspace = self._workspace(
                    Path(raw),
                    OVERPRODUCING_HELPER,
                )
                started = time.monotonic()
                proc, payload = self._run(
                    skills,
                    workspace,
                    "request",
                    "--source",
                    "research",
                    environment={
                        "FAKE_OVERPRODUCE_STDERR": "1" if stream == "stderr" else "0"
                    },
                )
                elapsed = time.monotonic() - started

                self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
                self.assertEqual(payload["error_code"], "manifest_failed")
                self.assertIn("output limit", proc.stderr)
                self.assertLess(elapsed, 10.0)
                self.assertIsNone(self._state(workspace))

    def test_zero_exit_without_persisted_manifest_banks_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            skills, workspace = self._workspace(
                Path(raw), NO_ARTIFACT_HELPER
            )

            proc, payload = self._run(
                skills, workspace, "request", "--source", "research"
            )

            self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
            self.assertEqual(payload["error_code"], "manifest_failed")
            self.assertIsNone(self._state(workspace))

    def test_nonfinite_helper_json_never_banks_a_manifest(self) -> None:
        for location in ("stdout", "artifact"):
            with self.subTest(location=location), tempfile.TemporaryDirectory() as raw:
                skills, workspace = self._workspace(
                    Path(raw),
                    NONFINITE_MANIFEST_HELPER,
                )

                proc, payload = self._run(
                    skills,
                    workspace,
                    "request",
                    "--source",
                    "research",
                    environment={"FAKE_NONFINITE_LOCATION": location},
                )

                self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
                self.assertEqual(payload["error_code"], "manifest_failed")
                self.assertIsNone(self._state(workspace))

    def test_exact_cap_manifest_allows_one_protocol_newline(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            skills, workspace = self._workspace(
                Path(raw),
                EXACT_CAP_MANIFEST_HELPER,
            )

            proc, payload = self._run(
                skills,
                workspace,
                "request",
                "--source",
                "research",
            )

            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            manifest_path = Path(payload["manifest"]["manifest_path"])
            self.assertEqual(manifest_path.stat().st_size, 16 * 1024 * 1024)
            self.assertEqual(payload["requested_count"], 2)
            self.assertEqual(len(self._state(workspace)["requested"]), 2)

    def test_zero_exit_with_malformed_watch_ack_banks_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            skills, workspace = self._workspace(
                Path(raw), MALFORMED_WATCH_HELPER
            )

            proc, payload = self._run(
                skills,
                workspace,
                "request",
                "--source",
                "research",
                "--watch",
            )

            self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
            self.assertEqual(payload["error_code"], "watch_failed")
            self.assertEqual(len(payload["watches"]), 1)
            self.assertEqual(payload["watches"][0]["status"], "error")
            self.assertIsNone(self._state(workspace))

    def test_valid_watch_echo_without_store_entry_banks_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            skills, workspace = self._workspace(Path(raw), WORKING_HELPER)

            proc, payload = self._run(
                skills,
                workspace,
                "request",
                "--source",
                "research",
                "--watch",
                environment={"FAKE_SKIP_WATCH_STORE": "1"},
            )

            self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
            self.assertEqual(payload["error_code"], "watch_failed")
            self.assertIn("missing from the durable ledger", payload["watches"][-1]["error"])
            self.assertIsNone(self._state(workspace))

    def test_terminal_watch_transition_during_batch_is_not_banked(self) -> None:
        for status in ("failed", "expired"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as raw:
                skills, workspace = self._workspace(
                    Path(raw), TRANSITIONING_WATCH_HELPER
                )

                proc, payload = self._run(
                    skills,
                    workspace,
                    "request",
                    "--source",
                    "research",
                    "--watch",
                    environment={"FAKE_DURABLE_WATCH_STATUS": status},
                )

                self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
                self.assertEqual(payload["error_code"], "watch_failed")
                self.assertIn(
                    f"non-success durable status {status}",
                    payload["watches"][-1]["error"],
                )
                self.assertIsNone(self._state(workspace))
                _, scanned = self._run(
                    skills, workspace, "scan", "--source", "research"
                )
                self.assertEqual(scanned["new_papers"], 2)

    def test_found_transition_during_batch_is_a_durable_success(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            skills, workspace = self._workspace(
                Path(raw), TRANSITIONING_WATCH_HELPER
            )

            proc, payload = self._run(
                skills,
                workspace,
                "request",
                "--source",
                "research",
                "--watch",
                environment={"FAKE_DURABLE_WATCH_STATUS": "found"},
            )

            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(payload["requested_count"], 2)
            self.assertEqual(len(self._state(workspace)["requested"]), 2)

    def test_retry_reuses_found_watch_after_a_later_batch_failure(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            skills, workspace = self._workspace(
                Path(raw),
                FOUND_THEN_LATER_FAILURE_HELPER,
            )

            first_proc, first = self._run(
                skills,
                workspace,
                "request",
                "--source",
                "research",
                "--watch",
            )
            self.assertEqual(first_proc.returncode, 2, first_proc.stdout + first_proc.stderr)
            self.assertEqual(first["error_code"], "watch_failed")
            self.assertIsNone(self._state(workspace))
            watch_store = (
                workspace
                / "data"
                / "research"
                / "getscipapers_bot"
                / "state"
                / "watches.json"
            )
            after_failure = json.loads(
                watch_store.read_text(encoding="utf-8")
            )["items"]
            self.assertEqual(len(after_failure), 1)
            self.assertEqual(after_failure[0]["status"], "found")

            shutil.copy2(
                REAL_GSP_HELPER,
                skills / "getscipapers_requester" / "gsp_openclaw_helper.py",
            )
            retry_proc, retry = self._run(
                skills,
                workspace,
                "request",
                "--source",
                "research",
                "--watch",
            )
            self.assertEqual(retry_proc.returncode, 0, retry_proc.stdout + retry_proc.stderr)
            self.assertEqual(retry["requested_count"], 2)
            after_retry = json.loads(
                watch_store.read_text(encoding="utf-8")
            )["items"]
            first_identity = "10.48550/arXiv.2401.01234"
            self.assertEqual(
                [item["identifier"] for item in after_retry].count(first_identity),
                1,
            )
            self.assertEqual(len(after_retry), 2)
            self.assertEqual(
                {item["identifier"]: item["status"] for item in after_retry},
                {
                    first_identity: "found",
                    "10.1145/3372297.3417231": "active",
                },
            )

    def test_durable_watch_must_retain_the_requested_service(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            skills, workspace = self._workspace(
                Path(raw), TRANSITIONING_WATCH_HELPER
            )

            proc, payload = self._run(
                skills,
                workspace,
                "request",
                "--source",
                "research",
                "--watch",
                environment={"FAKE_DURABLE_EMPTY_SERVICES": "1"},
            )

            self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
            self.assertEqual(payload["error_code"], "watch_failed")
            self.assertIn(
                "did not persist services=all",
                payload["watches"][-1]["error"],
            )
            self.assertIsNone(self._state(workspace))

    def test_semantically_invalid_durable_watch_is_never_banked(self) -> None:
        for fault, expected in (
            ("watch-key", "watch_key does not match identity"),
            ("timestamps", "timestamps are inconsistent"),
        ):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as raw:
                skills, workspace = self._workspace(
                    Path(raw),
                    TRANSITIONING_WATCH_HELPER,
                )

                proc, payload = self._run(
                    skills,
                    workspace,
                    "request",
                    "--source",
                    "research",
                    "--watch",
                    environment={"FAKE_DURABLE_SEMANTIC_FAULT": fault},
                )

                self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
                self.assertEqual(payload["error_code"], "watch_failed")
                self.assertIn(expected, payload["watches"][-1]["error"])
                self.assertIsNone(self._state(workspace))

    def test_future_found_watch_acknowledgment_is_never_banked(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            skills, workspace = self._workspace(
                Path(raw),
                TRANSITIONING_WATCH_HELPER,
            )

            proc, payload = self._run(
                skills,
                workspace,
                "request",
                "--source",
                "research",
                "--watch",
                environment={
                    "FAKE_DURABLE_WATCH_STATUS": "found",
                    "FAKE_DURABLE_SEMANTIC_FAULT": "future-timestamps",
                },
            )

            self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
            self.assertEqual(payload["error_code"], "watch_failed")
            self.assertIn("timestamps are in the future", payload["watches"][-1]["error"])
            self.assertIsNone(self._state(workspace))
            _, scanned = self._run(
                skills, workspace, "scan", "--source", "research"
            )
            self.assertEqual(scanned["new_papers"], 2)

    def test_the_papers_survive_for_the_retry(self) -> None:
        """The point of not banking them: the second run still finds them."""

        with tempfile.TemporaryDirectory() as raw:
            skills, workspace = self._workspace(Path(raw), BROKEN_HELPER)
            self._run(skills, workspace, "request", "--source", "research")

            _, scanned = self._run(skills, workspace, "scan", "--source", "research")
            self.assertEqual(scanned["new_papers"], 2, scanned)
            self.assertEqual(scanned["already_requested"], 0, scanned)

            # Now getscipapers works, and the retry hands them off.
            (skills / "getscipapers_requester" / "gsp_openclaw_helper.py").write_text(
                WORKING_HELPER, encoding="utf-8"
            )
            proc, payload = self._run(skills, workspace, "request", "--source", "research")
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(payload["requested_count"], 2, payload)

    def test_the_reason_the_handoff_failed_reaches_the_operator(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            skills, workspace = self._workspace(Path(raw), BROKEN_HELPER)
            proc, _ = self._run(skills, workspace, "request", "--source", "research")
            self.assertIn("getscipapers is not configured", proc.stderr)

    def test_a_successful_handoff_still_banks_and_dedups(self) -> None:
        """The control: the ledger has to keep working, or every run re-requests."""

        with tempfile.TemporaryDirectory() as raw:
            skills, workspace = self._workspace(Path(raw), WORKING_HELPER)

            proc, first = self._run(
                skills, workspace, "request", "--source", "research", "--watch"
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertTrue(first["ok"])
            self.assertEqual(first["requested_count"], 2)
            self.assertEqual(first["manifest"]["kind"], "paper")
            self.assertEqual(len(first["manifest"]["items"]), 2)
            self.assertTrue(Path(first["manifest"]["manifest_path"]).is_file())
            self.assertEqual(
                [w["status"] for w in first["watches"]], ["created", "created"]
            )
            self.assertEqual(
                self._state(workspace)["requested"],
                ["10.48550/arXiv.2401.01234", "10.1145/3372297.3417231"],
            )

            _, second = self._run(skills, workspace, "request", "--source", "research")
            self.assertEqual(second["message"], "No new papers to request")

    def test_real_getscipapers_helper_contract_creates_manifest_and_watches(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            skills, workspace = self._workspace(Path(raw), None)

            proc, payload = self._run(
                skills,
                workspace,
                "request",
                "--source",
                "research",
                "--watch",
            )

            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(payload["manifest"]["kind"], "paper")
            self.assertEqual(payload["manifest"]["counts"]["dois"], 2)
            self.assertEqual(
                [result["status"] for result in payload["watches"]],
                ["created", "created"],
            )
            watch_store = (
                workspace
                / "data"
                / "research"
                / "getscipapers_bot"
                / "state"
                / "watches.json"
            )
            watches = json.loads(watch_store.read_text(encoding="utf-8"))["items"]
            self.assertEqual(len(watches), 2)
            self.assertTrue(all(item["kind"] == "paper" for item in watches))

    def test_real_reused_watch_larger_than_generic_cap_remains_admissible(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            skills, workspace = self._workspace(Path(raw), None)
            env = dict(
                os.environ,
                AAS_RUNTIME_WORKSPACE=str(workspace),
                PYTHONDONTWRITEBYTECODE="1",
            )
            helper = skills / "getscipapers_requester" / "gsp_openclaw_helper.py"
            created = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(helper),
                    "create-watch",
                    "--kind",
                    "paper",
                    "--label=Existing large watch",
                    "--identifier-type",
                    "doi",
                    "--identifier",
                    "10.48550/arXiv.2401.01234",
                    "--services",
                    "all",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=10,
            )
            self.assertEqual(created.returncode, 0, created.stdout + created.stderr)
            watch_store = (
                workspace
                / "data"
                / "research"
                / "getscipapers_bot"
                / "state"
                / "watches.json"
            )
            data = json.loads(watch_store.read_text(encoding="utf-8"))
            data["items"][0]["sent_file_hashes"] = [
                f"{index:04d}" + "é" * 296
                for index in range(10_000)
            ]
            watch_store.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            self.assertGreater(watch_store.stat().st_size, 4 * 1024 * 1024)

            proc, payload = self._run(
                skills,
                workspace,
                "request",
                "--source",
                "research",
                "--watch",
            )

            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(payload["requested_count"], 2)
            self.assertEqual(
                [result["status"] for result in payload["watches"]],
                ["created", "created"],
            )
            self.assertEqual(len(self._state(workspace)["requested"]), 2)

    def test_unrelated_real_helper_watch_is_bridge_schema_compatible(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            skills, workspace = self._workspace(Path(raw), None)
            env = dict(
                os.environ,
                AAS_RUNTIME_WORKSPACE=str(workspace),
                PYTHONDONTWRITEBYTECODE="1",
            )
            helper = skills / "getscipapers_requester" / "gsp_openclaw_helper.py"
            unrelated = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(helper),
                    "create-watch",
                    "--kind",
                    "book",
                    "--label=Unrelated café record",
                    "--identifier-type",
                    "isbn",
                    "--identifier",
                    "9780262046305",
                    "--services",
                    "openlibrary,worldcat",
                    "--notes",
                    "safe optional note",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=10,
            )
            self.assertEqual(
                unrelated.returncode,
                0,
                unrelated.stdout + unrelated.stderr,
            )

            proc, payload = self._run(
                skills,
                workspace,
                "request",
                "--source",
                "research",
                "--watch",
            )

            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(payload["requested_count"], 2)

    def test_watch_failure_is_retryable_and_banks_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            skills, workspace = self._workspace(Path(raw), WATCH_FAILURE_HELPER)

            proc, payload = self._run(
                skills,
                workspace,
                "request",
                "--source",
                "research",
                "--watch",
            )

            self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
            self.assertEqual(payload["error_code"], "watch_failed")
            self.assertEqual(len(payload["watches"]), 1)
            self.assertIsNone(self._state(workspace))

            helper = skills / "getscipapers_requester" / "gsp_openclaw_helper.py"
            helper.write_text(WORKING_HELPER, encoding="utf-8")
            retry_proc, retry = self._run(
                skills,
                workspace,
                "request",
                "--source",
                "research",
                "--watch",
            )
            self.assertEqual(
                retry_proc.returncode,
                0,
                retry_proc.stdout + retry_proc.stderr,
            )
            self.assertEqual(retry["requested_count"], 2)

    def test_invalid_real_watch_store_is_preserved_and_never_banked(self) -> None:
        fixtures = {
            "corrupt": b"{ corrupt",
            "oversized": b"x" * (32 * 1024 * 1024 + 1),
            "too-many-items": json.dumps({"items": [{}] * 10_001}).encode(
                "utf-8"
            ),
            "wrong-shape": b'{"items":[{"id":"only"}]}',
        }
        for label, original in fixtures.items():
            with self.subTest(case=label), tempfile.TemporaryDirectory() as raw:
                skills, workspace = self._workspace(Path(raw), None)
                watch_store = (
                    workspace
                    / "data"
                    / "research"
                    / "getscipapers_bot"
                    / "state"
                    / "watches.json"
                )
                watch_store.parent.mkdir(parents=True)
                watch_store.write_bytes(original)

                proc, payload = self._run(
                    skills,
                    workspace,
                    "request",
                    "--source",
                    "research",
                    "--watch",
                )

                self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
                self.assertEqual(payload["error_code"], "watch_failed")
                self.assertEqual(watch_store.read_bytes(), original)
                self.assertIsNone(self._state(workspace))

    def test_explicit_bad_helper_storage_never_uses_fallback_or_banks(self) -> None:
        for label in ("unavailable-state", "corrupt-config"):
            with self.subTest(case=label), tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                skills, workspace = self._workspace(root, None)
                config = root / "config.json"
                fallback = root / "fallback"
                if label == "unavailable-state":
                    config.write_text(
                        json.dumps(
                            {
                                "download_dir": str(root / "downloads"),
                                "state_dir": "/proc/aas-digest-nope",
                                "manifest_dir": str(root / "manifests"),
                            }
                        ),
                        encoding="utf-8",
                    )
                else:
                    config.write_bytes(b"{ corrupt")
                original = config.read_bytes()

                proc, payload = self._run(
                    skills,
                    workspace,
                    "request",
                    "--source",
                    "research",
                    "--watch",
                    environment={
                        "GETSCIPAPERS_SKILL_CONFIG": str(config),
                        "GETSCIPAPERS_FALLBACK_ROOT": str(fallback),
                    },
                )

                self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
                self.assertEqual(
                    payload["error_code"],
                    "watch_failed" if label == "unavailable-state" else "manifest_failed",
                )
                self.assertEqual(config.read_bytes(), original)
                self.assertFalse(fallback.exists())
                self.assertIsNone(self._state(workspace))

    def test_markdown_and_untrusted_non_identifier_fields_cannot_forge_a_paper(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            skills, workspace = self._workspace(Path(raw), WORKING_HELPER)
            proc, payload = self._run(skills, workspace, "scan", "--source", "research")

            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(payload["new_papers"], 2, payload)
            self.assertEqual(
                [paper["identifier"] for paper in payload["papers"]],
                ["2401.01234", "10.1145/3372297.3417231"],
            )

    def test_markdown_without_the_required_sidecar_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            skills, workspace = self._workspace(Path(raw), WORKING_HELPER)
            sidecar = (
                workspace
                / "data"
                / "research"
                / "alerts"
                / "digests"
                / "latest-digest.json"
            )
            sidecar.unlink()

            proc, payload = self._run(skills, workspace, "scan", "--source", "research")

            self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["error_code"], "invalid_digest_sidecar")
            self.assertIn("rerun the research digest", payload["error"])

    def test_windows_reparse_points_are_rejected_by_bridge_directory_admission(self) -> None:
        module = _module()
        reparse = SimpleNamespace(
            st_mode=module.stat.S_IFDIR,
            st_file_attributes=0x400,
        )
        ordinary = SimpleNamespace(st_mode=module.stat.S_IFDIR)

        self.assertTrue(module._is_link_like_stat(reparse))
        self.assertFalse(module._is_link_like_stat(ordinary))
        with mock.patch.object(
            module.os,
            "lstat",
            return_value=reparse,
        ), self.assertRaisesRegex(module.DigestBridgeError, "RSS test directory is unsafe"):
            module._directory_entry_exists(
                Path("junction"),
                label="RSS test directory",
            )

    def test_nonfinite_sidecar_json_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            skills, workspace = self._workspace(Path(raw), WORKING_HELPER)
            sidecar = (
                workspace
                / "data"
                / "research"
                / "alerts"
                / "digests"
                / "latest-digest.json"
            )
            encoded = json.dumps(DIGEST_SIDECAR, separators=(",", ":"))
            sidecar.write_text(
                encoded[:-1] + ',"unknown":Infinity}',
                encoding="utf-8",
            )

            proc, payload = self._run(
                skills,
                workspace,
                "scan",
                "--source",
                "research",
            )

            self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
            self.assertEqual(payload["error_code"], "invalid_digest_sidecar")

    def test_failed_producer_sidecars_cannot_be_reported_as_successful_empty(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            skills, workspace = self._workspace(Path(raw), WORKING_HELPER)
            research_sidecar = (
                workspace
                / "data"
                / "research"
                / "alerts"
                / "digests"
                / "latest-digest.json"
            )
            research = dict(DIGEST_SIDECAR)
            research["items"] = []
            research["source_status"] = {
                "arxiv": {"status": "failed", "detail": "offline"},
                "s2_recommend": {"status": "skipped", "detail": "no seeds"},
                "s2_search": {"status": "failed", "detail": "offline"},
            }
            research_sidecar.write_text(json.dumps(research), encoding="utf-8")

            proc, payload = self._run(
                skills,
                workspace,
                "scan",
                "--source",
                "research",
            )
            self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
            self.assertEqual(payload["error_code"], "invalid_digest_sidecar")
            self.assertIn("complete discovery failure", payload["error"])

            rss_dir = workspace / "data" / "research" / "rss" / "digests"
            rss_dir.mkdir(parents=True)
            (rss_dir / "rss-research.json").write_text(
                json.dumps({
                    "schema_version": "digest-items.v1",
                    "artifact_role": "raw_external_digest",
                    "style_applied": False,
                    "source": "rss-research",
                    "run_status": {
                        "ok": False,
                        "degraded": True,
                        "attempted_feeds": 1,
                        "failed_feeds": 1,
                        "warning_feeds": 0,
                        "stub_failures": 0,
                    },
                    "items": [],
                }),
                encoding="utf-8",
            )
            proc, payload = self._run(
                skills,
                workspace,
                "scan",
                "--source",
                "rss",
            )
            self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
            self.assertEqual(payload["error_code"], "invalid_digest_sidecar")
            self.assertIn("failed publication", payload["error"])

    def test_successful_empty_producer_sidecar_remains_a_success(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            skills, workspace = self._workspace(Path(raw), WORKING_HELPER)
            sidecar = (
                workspace
                / "data"
                / "research"
                / "alerts"
                / "digests"
                / "latest-digest.json"
            )
            successful_empty = dict(DIGEST_SIDECAR)
            successful_empty["items"] = []
            successful_empty["source_status"] = {
                "arxiv": {"status": "empty", "detail": ""},
                "s2_recommend": {"status": "skipped", "detail": "no seeds"},
                "s2_search": {"status": "empty", "detail": ""},
            }
            sidecar.write_text(json.dumps(successful_empty), encoding="utf-8")

            proc, payload = self._run(
                skills,
                workspace,
                "scan",
                "--source",
                "research",
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["new_papers"], 0)

    def test_empty_topic_producer_sidecar_is_bridge_compatible(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            skills, workspace = self._workspace(Path(raw), WORKING_HELPER)
            module_name = f"aas_research_bridge_empty_topics_{id(workspace)}"
            spec = importlib.util.spec_from_file_location(
                module_name,
                RESEARCH_DIGEST_SCRIPT,
            )
            assert spec is not None and spec.loader is not None
            producer = importlib.util.module_from_spec(spec)
            with mock.patch.dict(
                os.environ,
                {"AAS_RUNTIME_WORKSPACE": str(workspace)},
                clear=False,
            ):
                sys.modules[module_name] = producer
                spec.loader.exec_module(producer)
            with (
                mock.patch.object(producer, "arxiv_recent", return_value=[]),
                mock.patch.object(producer, "load_seed_ids", return_value=[]),
            ):
                selected, errors, source_status, _pending_seen = (
                    producer.build_digest([])
                )
            self.assertEqual(selected, [])
            self.assertEqual(errors, [])
            self.assertEqual(source_status["s2_search"]["status"], "skipped")

            sidecar_path = (
                workspace
                / "data"
                / "research"
                / "alerts"
                / "digests"
                / "latest-digest.json"
            )
            sidecar_path.write_text(
                json.dumps(
                    producer._digest_sidecar(
                        selected,
                        source_status,
                        "2026-08-24",
                    )
                ),
                encoding="utf-8",
            )

            proc, payload = self._run(
                skills,
                workspace,
                "scan",
                "--source",
                "research",
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["new_papers"], 0)

    def test_research_producer_status_requires_the_exact_source_schema(self) -> None:
        invalid_statuses = (
            {
                "invented": {"status": "empty", "detail": ""},
            },
            {
                "arxiv": {"status": "failed", "detail": "offline"},
                "s2_recommend": {"status": "failed", "detail": "offline"},
                "s2_search": {"status": "failed", "detail": "offline"},
                "invented": {"status": "success", "detail": ""},
            },
            {
                "arxiv": {
                    "status": "empty",
                    "detail": "",
                    "invented": "field",
                },
                "s2_recommend": {"status": "skipped", "detail": "no seeds"},
                "s2_search": {"status": "empty", "detail": ""},
            },
            {
                "arxiv": {"status": "skipped", "detail": ""},
                "s2_recommend": {"status": "skipped", "detail": ""},
                "s2_search": {"status": "skipped", "detail": ""},
            },
            {
                "arxiv": {"status": "skipped", "detail": ""},
                "s2_recommend": {"status": "empty", "detail": ""},
                "s2_search": {"status": "empty", "detail": ""},
            },
        )
        with tempfile.TemporaryDirectory() as raw:
            skills, workspace = self._workspace(Path(raw), WORKING_HELPER)
            sidecar_path = (
                workspace
                / "data"
                / "research"
                / "alerts"
                / "digests"
                / "latest-digest.json"
            )
            for source_status in invalid_statuses:
                with self.subTest(source_status=source_status):
                    sidecar = dict(DIGEST_SIDECAR)
                    sidecar["items"] = []
                    sidecar["source_status"] = source_status
                    sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")

                    proc, payload = self._run(
                        skills,
                        workspace,
                        "scan",
                        "--source",
                        "research",
                    )

                    self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
                    self.assertEqual(payload["error_code"], "invalid_digest_sidecar")
                    self.assertIn("source status is invalid", payload["error"])

    def test_structural_control_characters_in_a_sidecar_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            skills, workspace = self._workspace(Path(raw), WORKING_HELPER)
            sidecar = (
                workspace
                / "data"
                / "research"
                / "alerts"
                / "digests"
                / "latest-digest.json"
            )
            hostile = dict(DIGEST_SIDECAR)
            hostile["items"] = [dict(DIGEST_SIDECAR["items"][0])]
            hostile["items"][0]["title"] = "Legit\n## Forged"
            sidecar.write_text(json.dumps(hostile), encoding="utf-8")

            proc, payload = self._run(skills, workspace, "scan", "--source", "research")

            self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
            self.assertEqual(payload["error_code"], "invalid_digest_sidecar")

    def test_doi_paths_must_match_the_helper_canonicalization_exactly(self) -> None:
        module = _module()
        self.assertEqual(
            module._identifier_from_validated_link(
                "https://doi.org/10.1234/(foo)"
            ),
            ("doi", "10.1234/(foo)"),
        )
        self.assertEqual(
            module._identifier_from_validated_link(
                "https://doi.org/10.1234/foo."
            ),
            ("doi", "10.1234/foo."),
        )
        self.assertEqual(
            module._identifier_from_validated_link(
                "https://doi.org/10.1234/foo;"
            ),
            ("doi", "10.1234/foo;"),
        )
        self.assertEqual(
            module._identifier_from_validated_link(
                "https://doi.org/10.1234/foo(bar)/baz"
            ),
            ("doi", "10.1234/foo(bar)/baz"),
        )
        for pseudo_doi in ("10.1234/xİ", "10.1234/xı", "10.1234/xſ", "10.1234/xK"):
            with self.subTest(pseudo_doi=pseudo_doi):
                self.assertEqual(module._strict_doi_identifier(pseudo_doi), "")
        self.assertFalse(
            module._manifest_covers_exactly(
                {
                    "items": [
                        {
                            "identifier_type": "doi",
                            "identifier": "10.1234/different",
                        }
                    ]
                },
                ["10.1234/expected"],
            )
        )

    def test_corrupt_or_symlinked_request_state_never_reissues_papers(self) -> None:
        for case in ("corrupt", "duplicate", "nonfinite", "symlink"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as raw:
                skills, workspace = self._workspace(Path(raw), WORKING_HELPER)
                state = workspace / "data" / "research" / "digest-bridge-state.json"
                state.parent.mkdir(parents=True, exist_ok=True)
                if case == "corrupt":
                    state.write_text('{"requested":[' + "9" * 5000 + "]}", encoding="utf-8")
                    outside = None
                elif case == "duplicate":
                    state.write_bytes(
                        b'{"requested":["hidden"],"requested":[]}'
                    )
                    outside = None
                elif case == "nonfinite":
                    state.write_bytes(b'{"requested":[],"unknown":NaN}')
                    outside = None
                else:
                    outside = Path(raw) / "outside.json"
                    outside.write_text('{"requested":["already"]}', encoding="utf-8")
                    state.symlink_to(outside)

                proc, payload = self._run(
                    skills, workspace, "request", "--source", "research", "--watch"
                )

                self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
                self.assertEqual(payload["error_code"], "invalid_bridge_state")
                manifest_dir = (
                    workspace
                    / "data"
                    / "research"
                    / "getscipapers_bot"
                    / "manifests"
                )
                self.assertFalse(manifest_dir.exists())
                if outside is not None:
                    self.assertEqual(
                        outside.read_text(encoding="utf-8"),
                        '{"requested":["already"]}',
                    )
                else:
                    self.assertTrue(state.exists())
                    if case == "duplicate":
                        self.assertEqual(
                            state.read_bytes(),
                            b'{"requested":["hidden"],"requested":[]}',
                        )
                    elif case == "nonfinite":
                        self.assertEqual(
                            state.read_bytes(),
                            b'{"requested":[],"unknown":NaN}',
                        )

    def test_preupgrade_arxiv_ledger_entries_are_canonicalized_without_reissue(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            skills, workspace = self._workspace(Path(raw), WORKING_HELPER)
            state = workspace / "data" / "research" / "digest-bridge-state.json"
            state.parent.mkdir(parents=True, exist_ok=True)
            state.write_text(
                json.dumps({
                    "requested": [
                        "2401.01234v7",
                        "10.1145/3372297.3417231",
                    ]
                }),
                encoding="utf-8",
            )

            proc, payload = self._run(
                skills, workspace, "scan", "--source", "research"
            )

            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(payload["new_papers"], 0, payload)
            self.assertEqual(payload["already_requested"], 2, payload)

    def test_versioned_arxiv_and_datacite_doi_are_one_unversioned_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            skills, workspace = self._workspace(Path(raw), None)
            sidecar_path = (
                workspace
                / "data"
                / "research"
                / "alerts"
                / "digests"
                / "latest-digest.json"
            )
            sidecar = dict(DIGEST_SIDECAR)
            sidecar["items"] = [
                {
                    "title": "-dash-prefixed title",
                    "link": "https://arxiv.org/abs/1706.03762v7",
                    "score": 9,
                    "source": "arXiv",
                },
                {
                    "title": "DataCite duplicate",
                    "link": "https://doi.org/10.48550/arXiv.1706.03762",
                    "score": 8,
                    "source": "DataCite",
                },
            ]
            sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")

            proc, payload = self._run(
                skills, workspace, "request", "--source", "research", "--watch"
            )

            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(payload["requested_count"], 1)
            self.assertEqual(
                payload["manifest"]["items"][0]["identifier"],
                "10.48550/arXiv.1706.03762",
            )
            self.assertEqual(
                self._state(workspace)["requested"],
                ["10.48550/arXiv.1706.03762"],
            )
            watch_store = (
                workspace
                / "data"
                / "research"
                / "getscipapers_bot"
                / "state"
                / "watches.json"
            )
            watch = json.loads(watch_store.read_text(encoding="utf-8"))["items"][0]
            self.assertEqual(watch["label"], "-dash-prefixed title")

    def test_request_lock_rejects_a_concurrent_bridge_invocation(self) -> None:
        module = _module()
        with tempfile.TemporaryDirectory() as raw:
            module.BRIDGE_STATE_FILE = Path(raw) / "state.json"
            with module._exclusive_request_lock():
                with self.assertRaises(module.BridgeLockError):
                    with module._exclusive_request_lock(timeout=0.05):
                        self.fail("a second request lock unexpectedly succeeded")

    def test_rss_digest_directory_enumeration_is_bounded(self) -> None:
        module = _module()
        with tempfile.TemporaryDirectory() as raw:
            digest_dir = Path(raw)
            for index in range(3):
                (digest_dir / f"rss-{index}.json").write_text("{}", encoding="utf-8")

            original_limit = module.MAX_RSS_DIGEST_FILES
            module.MAX_RSS_DIGEST_FILES = 2
            try:
                with self.assertRaises(module.DigestBridgeError):
                    module._bounded_digest_map(digest_dir, "rss-*.json")
            finally:
                module.MAX_RSS_DIGEST_FILES = original_limit

    def test_total_discovered_papers_are_bounded_across_sidecars(self) -> None:
        module = _module()
        papers = [{"identifier": "one"}]
        original_limit = module.MAX_DISCOVERED_PAPERS
        module.MAX_DISCOVERED_PAPERS = 2
        try:
            module._extend_discovered(papers, [{"identifier": "two"}])
            with self.assertRaises(module.DigestBridgeError):
                module._extend_discovered(papers, [{"identifier": "three"}])
        finally:
            module.MAX_DISCOVERED_PAPERS = original_limit

    def test_bridge_admits_the_five_tag_producer_maximum(self) -> None:
        module = _module()
        with tempfile.TemporaryDirectory() as raw:
            module.RSS_DIGEST_DIR = Path(raw)
            for tag_index, tag in enumerate(
                ("research", "events", "jobs", "general", "video")
            ):
                items = [
                    {
                        "title": f"Paper {tag_index}-{item_index}",
                        "link": f"https://doi.org/10.1234/{tag_index}.{item_index}",
                        "score": 1,
                        "source": tag,
                    }
                    for item_index in range(module.MAX_SIDECAR_ITEMS)
                ]
                (module.RSS_DIGEST_DIR / f"rss-{tag}.json").write_text(
                    json.dumps({
                        "schema_version": "digest-items.v1",
                        "artifact_role": "raw_external_digest",
                        "style_applied": False,
                        "source": f"rss-{tag}",
                        "run_status": {
                            "ok": True,
                            "degraded": False,
                            "attempted_feeds": 1,
                            "failed_feeds": 0,
                            "warning_feeds": 0,
                            "stub_failures": 0,
                        },
                        "items": items,
                    }),
                    encoding="utf-8",
                )

            papers = module.scan_digests(["rss"])

            self.assertEqual(
                len(papers),
                5 * module.MAX_SIDECAR_ITEMS,
            )

    def test_broken_canonical_rss_sidecar_is_not_treated_as_absent(self) -> None:
        module = _module()
        with tempfile.TemporaryDirectory() as raw:
            module.RSS_DIGEST_DIR = Path(raw)
            (module.RSS_DIGEST_DIR / "rss-jobs.json").write_text(
                json.dumps({
                    "schema_version": "digest-items.v1",
                    "artifact_role": "raw_external_digest",
                    "style_applied": False,
                    "source": "rss-jobs",
                    "run_status": {
                        "ok": True,
                        "degraded": False,
                        "attempted_feeds": 1,
                        "failed_feeds": 0,
                        "warning_feeds": 0,
                        "stub_failures": 0,
                    },
                    "items": [{
                        "title": "Valid job paper",
                        "link": "https://doi.org/10.1234/jobs",
                        "score": 1,
                        "source": "jobs",
                    }],
                }),
                encoding="utf-8",
            )
            broken = module.RSS_DIGEST_DIR / "rss-research.json"
            broken.symlink_to(module.RSS_DIGEST_DIR / "missing-research.json")

            with self.assertRaisesRegex(
                module.DigestBridgeError,
                "unsafe|unreadable",
            ):
                module.scan_digests(["rss"])

            self.assertTrue(broken.is_symlink())

    def test_unknown_rss_sidecar_cannot_inject_a_request(self) -> None:
        module = _module()
        with tempfile.TemporaryDirectory() as raw:
            module.RSS_DIGEST_DIR = Path(raw)
            rogue = {
                "schema_version": "digest-items.v1",
                "artifact_role": "raw_external_digest",
                "style_applied": False,
                "source": "rss-rogue",
                "items": [{
                    "title": "Rogue paper",
                    "link": "https://doi.org/10.1234/rogue",
                    "score": 1_000,
                    "source": "rogue",
                }],
            }
            (module.RSS_DIGEST_DIR / "rss-rogue.json").write_text(
                json.dumps(rogue), encoding="utf-8"
            )

            self.assertEqual(module.scan_digests(["rss"]), [])

    def test_large_successful_handoff_is_not_reissued_on_the_next_run(self) -> None:
        module = _module()
        papers = [
            {
                "request_identifier": f"10.1234/request-{index}",
                "score": 1,
            }
            for index in range(600)
        ]
        args = SimpleNamespace(source="rss", min_score=0, watch=False)

        with tempfile.TemporaryDirectory() as raw:
            module.BRIDGE_STATE_FILE = Path(raw) / "digest-bridge-state.json"
            module.scan_for_command = lambda _sources: papers
            module.create_manifest = lambda requested: {
                "kind": "paper",
                "items": [
                    {
                        "identifier_type": "doi",
                        "identifier": paper["request_identifier"],
                    }
                    for paper in requested
                ],
            }

            first_stdout = io.StringIO()
            with contextlib.redirect_stdout(first_stdout):
                module._cmd_request_locked(args)
            first = json.loads(first_stdout.getvalue())
            state = json.loads(
                module.BRIDGE_STATE_FILE.read_text(encoding="utf-8")
            )

            self.assertEqual(first["requested_count"], len(papers))
            self.assertEqual(len(state["requested"]), len(papers))

            second_stdout = io.StringIO()
            with contextlib.redirect_stdout(second_stdout):
                module._cmd_request_locked(args)
            second = json.loads(second_stdout.getvalue())

            self.assertEqual(second["message"], "No new papers to request")

    def test_real_bridge_accepts_full_batch_of_maximum_length_dois(self) -> None:
        module = _module()
        papers = []
        for index in range(module.MAX_DISCOVERED_PAPERS):
            prefix = f"10.1234/{index:04d}-"
            identifier = (
                prefix
                + "x" * (500 - len(prefix))
            )
            papers.append({"request_identifier": identifier, "score": 1})
        args = SimpleNamespace(source="rss", min_score=0, watch=False)

        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw) / "workspace"
            workspace.mkdir()
            module.BRIDGE_STATE_FILE = (
                workspace / "data" / "research" / "digest-bridge-state.json"
            )
            module.GSP_HELPER = REAL_GSP_HELPER
            module.scan_for_command = lambda _sources: papers
            with (
                mock.patch.dict(
                    os.environ,
                    {
                        "AAS_RUNTIME_WORKSPACE": str(workspace),
                        "PYTHONDONTWRITEBYTECODE": "1",
                    },
                    clear=False,
                ),
                contextlib.redirect_stdout(io.StringIO()) as output,
            ):
                module._cmd_request_locked(args)

            payload = json.loads(output.getvalue())
            manifest_path = Path(payload["manifest"]["manifest_path"])
            self.assertEqual(payload["requested_count"], len(papers))
            self.assertEqual(len(payload["manifest"]["items"]), len(papers))
            self.assertLessEqual(
                manifest_path.stat().st_size,
                module.MAX_HELPER_MANIFEST_JSON_BYTES,
            )
            self.assertEqual(
                len(self._state(workspace)["requested"]),
                len(papers),
            )
            state_size = module.BRIDGE_STATE_FILE.stat().st_size
            self.assertLessEqual(state_size, module.MAX_REQUEST_STATE_BYTES)
            self.assertEqual(len(module.load_state()["requested"]), len(papers))

            with contextlib.redirect_stdout(io.StringIO()) as second_output:
                module._cmd_request_locked(args)
            self.assertEqual(
                json.loads(second_output.getvalue())["message"],
                "No new papers to request",
            )

    def test_saturated_mixed_history_keeps_every_current_handoff(self) -> None:
        module = _module()
        current_existing = [
            {
                "request_identifier": f"10.1234/current-{index}",
                "score": 1,
            }
            for index in range(2_000)
        ]
        current_new = [
            {
                "request_identifier": f"10.1234/new-{index}",
                "score": 1,
            }
            for index in range(1_000)
        ]
        historical = [f"10.1234/history-{index}" for index in range(1_000)]
        papers = current_existing + current_new
        args = SimpleNamespace(source="rss", min_score=0, watch=False)

        with tempfile.TemporaryDirectory() as raw:
            module.BRIDGE_STATE_FILE = Path(raw) / "digest-bridge-state.json"
            module.save_state({
                "requested": [
                    paper["request_identifier"] for paper in current_existing
                ] + historical,
            })
            module.scan_for_command = lambda _sources: papers
            module.create_manifest = lambda requested: {
                "kind": "paper",
                "items": [
                    {
                        "identifier_type": "doi",
                        "identifier": paper["request_identifier"],
                    }
                    for paper in requested
                ],
            }

            first_stdout = io.StringIO()
            with contextlib.redirect_stdout(first_stdout):
                module._cmd_request_locked(args)
            first = json.loads(first_stdout.getvalue())
            state = module.load_state()

            self.assertEqual(first["requested_count"], len(current_new))
            self.assertEqual(
                {value.casefold() for value in state["requested"]},
                {
                    paper["request_identifier"].casefold()
                    for paper in papers
                },
            )

            second_stdout = io.StringIO()
            with contextlib.redirect_stdout(second_stdout):
                module._cmd_request_locked(args)
            second = json.loads(second_stdout.getvalue())

            self.assertEqual(second["message"], "No new papers to request")


if __name__ == "__main__":
    unittest.main()
