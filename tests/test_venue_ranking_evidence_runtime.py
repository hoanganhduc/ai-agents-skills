from __future__ import annotations

import json
import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = REPO_ROOT / "canonical" / "runtime" / "skills" / "venue-ranking-evidence"
SCRIPT = RUNTIME_DIR / "venue_ranking_evidence.py"


def run_runtime(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    if check and completed.returncode != 0:
        raise AssertionError(
            f"venue runtime failed\nstdout={completed.stdout}\nstderr={completed.stderr}"
        )
    return completed


def payload(stdout: str) -> dict[str, object]:
    value = json.loads(stdout)
    assert isinstance(value, dict)
    return value


def call_runtime_function(function: str, **kwargs: object) -> object:
    code = (
        "import importlib.util,json,sys;"
        "spec=importlib.util.spec_from_file_location('venue_runtime',sys.argv[1]);"
        "module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);"
        "request=json.load(sys.stdin);"
        "print(json.dumps(getattr(module,request['function'])(**request['kwargs'])))"
    )
    completed = subprocess.run(
        [sys.executable, "-B", "-c", code, str(SCRIPT)],
        cwd=REPO_ROOT,
        input=json.dumps({"function": function, "kwargs": kwargs}),
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr)
    return json.loads(completed.stdout)


def load_runtime_module() -> object:
    spec = importlib.util.spec_from_file_location("venue_runtime_for_tests", SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load venue runtime")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_records(path: Path) -> Path:
    records = {
        "schema_version": "venue-ranking-records.v1",
        "synthetic": True,
        "venues": [
            {
                "venue_id": "venue-tcs-journal",
                "canonical_title": "Theoretical Computer Science",
                "venue_type": "journal",
                "aliases": ["TCS"],
                "identifiers": {"issn": ["0304-3975"], "source_id": ["20571"]},
            },
            {
                "venue_id": "venue-tocs-journal",
                "canonical_title": "Theory of Computing Systems",
                "venue_type": "journal",
                "aliases": ["TCS", "TOCS"],
                "identifiers": {"issn": ["1432-4350"]},
            },
        ],
        "observations": [
            {
                "observation_id": "obs-tcs-sjr-cs",
                "venue_id": "venue-tcs-journal",
                "source_id": "scimago",
                "assertion_kind": "quartile",
                "scheme": "sjr-quartile",
                "category": "Computer Science (miscellaneous)",
                "value": "Q2",
                "metric_year": "2024",
                "freshness_status": "verified-historical",
                "official_url": "https://www.scimagojr.com/journalsearch.php?q=20571&tip=sid",
            },
            {
                "observation_id": "obs-tcs-sjr-theory",
                "venue_id": "venue-tcs-journal",
                "source_id": "scimago",
                "assertion_kind": "quartile",
                "scheme": "sjr-quartile",
                "category": "Theoretical Computer Science",
                "value": "Q3",
                "metric_year": "2024",
                "freshness_status": "verified-historical",
                "official_url": "https://www.scimagojr.com/journalsearch.php?q=20571&tip=sid",
            },
            {
                "observation_id": "obs-tcs-scopus-member",
                "venue_id": "venue-tcs-journal",
                "source_id": "scopus",
                "assertion_kind": "index-membership",
                "scheme": "scopus-source-coverage",
                "value": "included",
                "metric_year": "2024",
                "freshness_status": "verified-historical",
                "official_url": "https://www.scopus.com/sourceid/20571",
            },
        ],
    }
    path.write_text(json.dumps(records), encoding="utf-8")
    return path


def write_declarative_descriptor(
    path: Path,
    *,
    source_id: str,
    assertion_kinds: list[str],
    adapter: str = "csv",
) -> Path:
    descriptor = {
        "schema_version": "venue-ranking-source.v1",
        "source_id": source_id,
        "display_name": f"{source_id} rankings",
        "authority": f"{source_id} scholarly society",
        "provenance_class": "user-added-official",
        "official_domains": [f"{source_id}.example.org"],
        "venue_types": ["journal"],
        "assertion_kinds": assertion_kinds,
        "access_class": "user-export",
        "may_claim_latest": False,
        "lookup": {
            "adapter": adapter,
            "field_mapping": {
                "venue_id": "venue_id",
                "canonical_title": "title",
                "venue_type": "type",
                "aliases": "aliases",
                "issn": "issn",
                "eissn": "eissn",
                "provider_id": "provider_id",
                "assertion_kind": "kind",
                "scheme": "scheme",
                "category": "category",
                "value": "value",
                "metric_year": "year",
                "official_url": "official_url",
            },
        },
        "proof": {"strategy": "official-detail-page"},
        "freshness": {"mode": "user-declared-edition"},
    }
    path.write_text(json.dumps(descriptor), encoding="utf-8")
    return path


def add_declarative_source(registry_dir: Path, descriptor: Path) -> None:
    run_runtime(
        "sources",
        "add",
        "--descriptor",
        str(descriptor),
        "--registry-dir",
        str(registry_dir),
    )


def rewrite_recorded_hash(run_dir: Path, artifact_name: str) -> None:
    state_path = run_dir / "run_status.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["artifact_hashes"][artifact_name] = hashlib.sha256(
        (run_dir / artifact_name).read_bytes()
    ).hexdigest()
    state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")


def create_fixture_independent_verified_run(
    root: Path,
) -> tuple[object, Path, str, dict[str, object], dict[str, object]]:
    """Build a self-contained proof bundle; browser/PDF checks are mocked by tests."""

    module = load_runtime_module()
    requested_url = "https://portal.core.edu.au/conf-ranks/11/"
    records = root / "verified-records.json"
    records.write_text(
        json.dumps(
            {
                "schema_version": "venue-ranking-records.v1",
                "synthetic": True,
                "venues": [
                    {
                        "venue_id": "icore-11",
                        "canonical_title": "Target Conference",
                        "venue_type": "conference",
                        "aliases": ["TARGET"],
                        "identifiers": {"icore_id": ["11"]},
                        "official_url": requested_url,
                    }
                ],
                "observations": [
                    {
                        "observation_id": "obs-target-icore2026",
                        "venue_id": "icore-11",
                        "source_id": "icore",
                        "assertion_kind": "classification-level",
                        "scheme": "ICORE conference rank",
                        "category": None,
                        "collection": None,
                        "value": "A*",
                        "edition": "ICORE2026",
                        "metric_year": "2026",
                        "official_url": requested_url,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    run_dir = root / "verified-run"
    run_runtime(
        "lookup",
        "--dir",
        str(run_dir),
        "--query",
        "Target Conference",
        "--records-file",
        str(records),
        "--offline",
    )
    venue = json.loads((run_dir / "venues.jsonl").read_text(encoding="utf-8"))
    observation = json.loads(
        (run_dir / "observations.jsonl").read_text(encoding="utf-8")
    )
    source = module.load_registry(None)["icore"]
    proof_root = run_dir / "proofs" / observation["observation_id"]
    proof_root.mkdir(parents=True)
    pdf_path = proof_root / "official-page.pdf"
    png_path = proof_root / "official-page.png"
    pdf_path.write_bytes(b"%PDF-1.7\nfixture-independent-proof\n%%EOF\n")
    png_path.write_bytes(b"fixture-independent-png-evidence")
    pdf_relative = str(pdf_path.relative_to(run_dir))
    png_relative = str(png_path.relative_to(run_dir))
    pdf_sha256 = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    png_sha256 = hashlib.sha256(png_path.read_bytes()).hexdigest()
    common_runtime = {
        "runtime_version": "0.2.0",
        "browser": {"version": "Chromium 123.0.0"},
        "navigation_complete": True,
        "same_origin_only": True,
        "origin_policy": "scheme-host-port",
        "sandbox": "enabled",
        "private_targets_allowed": False,
        "initial_url": requested_url,
        "final_url": requested_url,
        "resolver_pin": ["portal.core.edu.au", "8.8.8.8"],
    }
    pdf_runtime = {
        **common_runtime,
        "status": "PDF_PRINTED",
        "out_path": pdf_relative,
        "bytes": pdf_path.stat().st_size,
        "sha256": pdf_sha256,
    }
    png_runtime = {
        **common_runtime,
        "status": "CAPTURED",
        "tier": "cdp",
        "out_path": png_relative,
        "bytes": png_path.stat().st_size,
        "sha256": png_sha256,
        "width": 1200,
        "height": 2400,
        "document_width": 1200,
        "document_height": 2400,
        "document_ready_state": "complete",
        "full_page": True,
        "full_page_complete": True,
    }
    pdf_check = {
        "status": "STRUCTURALLY_VALID",
        "structurally_valid": True,
        "final_verdict": "UNVERIFIED",
        "bytes": pdf_path.stat().st_size,
        "sha256": pdf_sha256,
        "page_count": 1,
    }
    png_check = {"final_verdict": "VERIFIED"}
    pdf_sidecar = proof_root / "official-page.pdf.result.json"
    png_sidecar = proof_root / "official-page.png.result.json"
    pdf_sidecar.write_text(json.dumps(pdf_runtime, sort_keys=True) + "\n", encoding="utf-8")
    png_sidecar.write_text(json.dumps(png_runtime, sort_keys=True) + "\n", encoding="utf-8")
    pdf_text = (
        "Target Conference\n"
        "Acronym: TARGET\n"
        "DBLP Source: https://dblp.org/db/conf/target\n"
        "Source: ICORE2026\n"
        "Rank: A*\n"
    )
    proof = {
        "schema_version": "venue-ranking-proof.v1",
        "proof_id": "proof-obs-target-icore2026",
        "observation_id": observation["observation_id"],
        "venue_id": venue["venue_id"],
        "source_id": "icore",
        "requested_url": requested_url,
        "captured_at": module.utc_now(),
        "pdf_path": pdf_relative,
        "png_path": png_relative,
        "pdf_sidecar_path": str(pdf_sidecar.relative_to(run_dir)),
        "png_sidecar_path": str(png_sidecar.relative_to(run_dir)),
        "pdf_sha256": pdf_sha256,
        "png_sha256": png_sha256,
        "pdf_sidecar_sha256": hashlib.sha256(pdf_sidecar.read_bytes()).hexdigest(),
        "png_sidecar_sha256": hashlib.sha256(png_sidecar.read_bytes()).hexdigest(),
        "pdf_bytes": pdf_path.stat().st_size,
        "png_bytes": png_path.stat().st_size,
        "media": "print",
        "print_background": True,
        "prefer_css_page_size": True,
        "expected_markers": module.expected_evidence_markers(observation, venue, source),
        "missing_markers": [],
        "blocked_markers": [],
        "claim_association": True,
        "record_url_binding": True,
        "association_adapter": "icore-detail-text-v1",
        "warnings": [],
        "pdf_runtime": pdf_runtime,
        "pdf_verification": pdf_check,
        "png_runtime": png_runtime,
        "png_verification": png_check,
        "capture_status": "captured",
        "verification_verdict": "UNVERIFIED",
        "verification_required": True,
    }
    (run_dir / "proofs.jsonl").write_text(
        json.dumps(proof, sort_keys=True) + "\n", encoding="utf-8"
    )
    (run_dir / "report.md").write_text(module.render_report(run_dir), encoding="utf-8")
    state_path = run_dir / "run_status.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["artifact_hashes"] = module.artifact_hashes(run_dir)
    state_path.write_text(json.dumps(state, sort_keys=True) + "\n", encoding="utf-8")
    return module, run_dir, pdf_text, pdf_check, png_check


class VenueRankingEvidenceRuntimeTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "POSIX shell wrapper test")
    def test_posix_wrapper_uses_aas_runtime_python(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp) / "venue-ranking-evidence"
            runtime_dir.mkdir()
            shutil.copy2(
                RUNTIME_DIR / "run_venue_ranking_evidence.sh",
                runtime_dir / "run_venue_ranking_evidence.sh",
            )
            (runtime_dir / "venue_ranking_evidence.py").write_text(
                "import json, sys\n"
                "print(json.dumps({'executable': sys.executable, 'args': sys.argv[1:]}))\n",
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["AAS_RUNTIME_PYTHON"] = sys.executable
            env.pop("VENUE_RANKING_EVIDENCE_PYTHON", None)
            completed = subprocess.run(
                [
                    "bash",
                    str(runtime_dir / "run_venue_ranking_evidence.sh"),
                    "arg1",
                    "arg2",
                ],
                check=False,
                text=True,
                capture_output=True,
                env=env,
                timeout=30,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = payload(completed.stdout)
        self.assertEqual(Path(str(result["executable"])).resolve(), Path(sys.executable).resolve())
        self.assertEqual(result["args"], ["arg1", "arg2"])

    def test_smoke_is_offline_and_nonmutating(self) -> None:
        result = payload(run_runtime("smoke").stdout)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["smoke_mode"], "offline")
        self.assertFalse(result["network_required"])
        self.assertFalse(result["live_api_attempted"])
        self.assertFalse(result["config_written"])
        self.assertFalse(result["real_secrets_read"])

    def test_sources_list_classifies_required_sources(self) -> None:
        result = payload(run_runtime("sources", "list").stdout)
        sources = {row["source_id"]: row for row in result["sources"]}
        for source_id in (
            "icore",
            "ccf",
            "scimago",
            "scopus",
            "wos-mjl",
            "clarivate-jcr",
            "jufo",
            "norwegian-register",
            "doaj",
            "conference-ranks",
        ):
            self.assertIn(source_id, sources)
        self.assertEqual(sources["conference-ranks"]["provenance_class"], "secondary-legacy")
        self.assertFalse(sources["conference-ranks"]["may_claim_latest"])
        self.assertTrue(sources["icore"]["live_lookup_supported"])
        self.assertTrue(sources["icore"]["proof_supported"])
        self.assertFalse(sources["scimago"]["live_lookup_supported"])
        self.assertFalse(sources["scimago"]["proof_supported"])

    def test_legacy_live_lookup_warns_and_records_blocked_freshness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            result = payload(
                run_runtime(
                    "lookup",
                    "--dir",
                    str(run_dir),
                    "--query",
                    "SIGCOMM",
                    "--source",
                    "conference-ranks",
                    "--allow-network",
                    "--allow-source",
                    "conference-ranks",
                ).stdout
            )
            source_access = json.loads(
                (run_dir / "sources.jsonl").read_text(encoding="utf-8")
            )
            report = (run_dir / "report.md").read_text(encoding="utf-8")
            self.assertEqual(source_access["freshness_status"], "blocked")
            self.assertTrue(
                any("cannot establish current official" in warning for warning in result["warnings"])
            )
            self.assertIn("cannot establish current official ranking status", report)

    def test_lookup_returns_all_acronym_matches_and_separate_observations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            records = write_records(root / "records.json")
            run_dir = root / "run"
            result = payload(
                run_runtime(
                    "lookup",
                    "--dir",
                    str(run_dir),
                    "--query",
                    "TCS",
                    "--records-file",
                    str(records),
                    "--offline",
                ).stdout
            )
            self.assertEqual(result["match_count"], 2)
            matches = [json.loads(line) for line in (run_dir / "matches.jsonl").read_text().splitlines()]
            self.assertEqual({row["venue_id"] for row in matches}, {"venue-tcs-journal", "venue-tocs-journal"})
            self.assertTrue(all(row["match_method"] == "exact-alias" for row in matches))
            observations = [
                json.loads(line) for line in (run_dir / "observations.jsonl").read_text().splitlines()
            ]
            tcs_quartiles = [
                row
                for row in observations
                if row["venue_id"] == "venue-tcs-journal" and row["assertion_kind"] == "quartile"
            ]
            self.assertEqual(len(tcs_quartiles), 2)
            self.assertEqual({row["category"] for row in tcs_quartiles}, {"Computer Science (miscellaneous)", "Theoretical Computer Science"})

    def test_identifier_match_precedes_text_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            records = write_records(root / "records.json")
            run_dir = root / "run"
            run_runtime(
                "lookup",
                "--dir",
                str(run_dir),
                "--query",
                "0304-3975",
                "--records-file",
                str(records),
                "--offline",
            )
            matches = [json.loads(line) for line in (run_dir / "matches.jsonl").read_text().splitlines()]
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0]["venue_id"], "venue-tcs-journal")
            self.assertEqual(matches[0]["match_method"], "exact-identifier")

    def test_live_lookup_requires_network_and_source_gates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            result = run_runtime(
                "lookup",
                "--dir",
                str(run_dir),
                "--query",
                "SIGCOMM",
                "--source",
                "icore",
                "--allow-network",
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("--allow-source icore", result.stderr)

    def test_declarative_source_rejects_executable_or_insecure_descriptor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry_dir = root / "registry"
            descriptor = root / "bad.json"
            descriptor.write_text(
                json.dumps(
                    {
                        "schema_version": "venue-ranking-source.v1",
                        "source_id": "unsafe",
                        "display_name": "Unsafe",
                        "official_domains": ["example.org"],
                        "lookup": {"format": "csv", "url": "http://example.org/data.csv"},
                        "python_import": "evil.module",
                    }
                ),
                encoding="utf-8",
            )
            result = run_runtime(
                "sources",
                "add",
                "--descriptor",
                str(descriptor),
                "--registry-dir",
                str(registry_dir),
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(registry_dir.exists())
            self.assertIn("unsupported descriptor field", result.stderr)

    def test_declarative_csv_source_can_be_added_and_queried_offline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry_dir = root / "registry"
            descriptor = root / "community-list.json"
            descriptor.write_text(
                json.dumps(
                    {
                        "schema_version": "venue-ranking-source.v1",
                        "source_id": "community-list",
                        "display_name": "Community Venue List",
                        "authority": "Example Scholarly Society",
                        "provenance_class": "user-added-official",
                        "official_domains": ["rankings.example.org"],
                        "venue_types": ["journal"],
                        "assertion_kinds": ["classification-level"],
                        "access_class": "user-export",
                        "may_claim_latest": False,
                        "lookup": {
                            "adapter": "csv",
                            "field_mapping": {
                                "canonical_title": "title",
                                "aliases": "short_name",
                                "issn": "issn",
                                "assertion_kind": "kind",
                                "scheme": "scheme",
                                "value": "level",
                                "metric_year": "year",
                                "official_url": "official_url",
                            },
                        },
                        "proof": {"strategy": "official-detail-page"},
                        "freshness": {"mode": "user-declared-edition"},
                    }
                ),
                encoding="utf-8",
            )
            run_runtime(
                "sources",
                "add",
                "--descriptor",
                str(descriptor),
                "--registry-dir",
                str(registry_dir),
            )
            export = root / "community.csv"
            export.write_text(
                "title,short_name,issn,kind,scheme,level,year,official_url\n"
                "Journal of Synthetic Evidence,JSE,1234-5678,classification-level,Society list,A,2026,https://rankings.example.org/venues/jse\n",
                encoding="utf-8",
            )
            run_dir = root / "run"
            result = payload(
                run_runtime(
                    "lookup",
                    "--dir",
                    str(run_dir),
                    "--query",
                    "JSE",
                    "--registry-dir",
                    str(registry_dir),
                    "--data-file",
                    f"community-list={export}",
                    "--offline",
                ).stdout
            )
            self.assertEqual(result["match_count"], 1)
            observation = json.loads((run_dir / "observations.jsonl").read_text())
            self.assertEqual(observation["value"], "A")
            self.assertEqual(observation["freshness_status"], "currentness-unconfirmed")
            self.assertEqual(observation["source_id"], "community-list")

    def test_nonempty_unmarked_run_and_cache_directories_cannot_be_claimed_or_purged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            records = write_records(root / "records.json")
            run_dir = root / "existing-run"
            run_dir.mkdir()
            run_sentinel = run_dir / "keep.txt"
            run_sentinel.write_text("user-owned", encoding="utf-8")

            claim_run = run_runtime(
                "lookup",
                "--dir",
                str(run_dir),
                "--query",
                "TCS",
                "--records-file",
                str(records),
                "--offline",
                check=False,
            )
            purge_run = run_runtime("purge", "--dir", str(run_dir), check=False)
            self.assertNotEqual(claim_run.returncode, 0)
            self.assertNotEqual(purge_run.returncode, 0)
            self.assertTrue(run_sentinel.is_file())
            self.assertFalse((run_dir / ".venue-ranking-evidence-run").exists())

            cache_dir = root / "existing-cache"
            cache_dir.mkdir()
            cache_sentinel = cache_dir / "keep.txt"
            cache_sentinel.write_text("user-owned", encoding="utf-8")
            claim_cache = run_runtime(
                "cache",
                "refresh",
                "--cache-dir",
                str(cache_dir),
                "--source",
                "icore",
                "--allow-network",
                "--allow-source",
                "icore",
                check=False,
            )
            purge_cache = run_runtime(
                "cache", "purge", "--cache-dir", str(cache_dir), check=False
            )
            self.assertNotEqual(claim_cache.returncode, 0)
            self.assertNotEqual(purge_cache.returncode, 0)
            self.assertTrue(cache_sentinel.is_file())
            self.assertFalse((cache_dir / ".venue-ranking-evidence-cache").exists())

    def test_offline_icore_cache_checks_hash_and_assigns_current_or_stale_freshness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache_dir = root / "cache"
            cache_dir.mkdir()
            (cache_dir / ".venue-ranking-evidence-cache").write_text(
                "venue-ranking-evidence-cache.v1\n", encoding="utf-8"
            )
            csv_payload = (
                "id,title,acronym,source,rank,rank_source,for_code\n"
                "11,ACM SIGCOMM Conference,SIGCOMM,ICORE2026,A*,ICORE2026,4606\n"
            ).encode("utf-8")
            csv_path = cache_dir / "ICORE2026.csv"
            csv_path.write_bytes(csv_payload)
            metadata_path = cache_dir / "ICORE2026.json"

            def write_metadata(cached_at: str) -> None:
                response_hash = hashlib.sha256(csv_path.read_bytes()).hexdigest()
                access = {
                    "schema_version": "venue-ranking-source-access.v1",
                    "source_id": "icore",
                    "endpoint_class": "official-csv-export",
                    "requested_domain": "portal.core.edu.au",
                    "final_domain": "portal.core.edu.au",
                    "discovery_final_domain": "portal.core.edu.au",
                    "final_url": "https://portal.core.edu.au/conf-ranks/?source=ICORE2026&do=Export",
                    "discovery_final_url": "https://portal.core.edu.au/",
                    "discovery_response_sha256": "a" * 64,
                    "discovery_response_bytes": 4096,
                    "discovery_edition_signal": "ICORE2026",
                    "retrieved_at": cached_at,
                    "response_sha256": response_hash,
                    "response_bytes": len(csv_path.read_bytes()),
                    "edition": "ICORE2026",
                    "freshness_status": "verified-current",
                    "cache_status": "live",
                }
                metadata_path.write_text(
                    json.dumps(
                        {
                            "schema_version": "venue-ranking-evidence-cache.v1",
                            "edition": "ICORE2026",
                            "final_url": "https://portal.core.edu.au/conf-ranks/?source=ICORE2026&do=Export",
                            "cached_at": cached_at,
                            "csv_sha256": response_hash,
                            "access": access,
                        }
                    ),
                    encoding="utf-8",
                )

            write_metadata(datetime.now(timezone.utc).isoformat())
            current_run = root / "current-run"
            current = payload(
                run_runtime(
                    "lookup",
                    "--dir",
                    str(current_run),
                    "--query",
                    "SIGCOMM",
                    "--source",
                    "icore",
                    "--cache-dir",
                    str(cache_dir),
                    "--offline",
                ).stdout
            )
            self.assertEqual(current["match_count"], 1)
            current_observation = json.loads(
                (current_run / "observations.jsonl").read_text(encoding="utf-8")
            )
            current_access = json.loads(
                (current_run / "sources.jsonl").read_text(encoding="utf-8")
            )
            self.assertEqual(
                current_observation["freshness_status"], "currentness-unconfirmed"
            )
            self.assertEqual(current_observation["cache_status"], "cached")
            self.assertEqual(
                current_access["freshness_status"], "currentness-unconfirmed"
            )
            self.assertEqual(current_access["response_sha256"], hashlib.sha256(csv_payload).hexdigest())

            write_metadata("2000-01-01T00:00:00Z")
            stale_run = root / "stale-run"
            run_runtime(
                "lookup",
                "--dir",
                str(stale_run),
                "--query",
                "SIGCOMM",
                "--source",
                "icore",
                "--cache-dir",
                str(cache_dir),
                "--offline",
            )
            stale_observation = json.loads(
                (stale_run / "observations.jsonl").read_text(encoding="utf-8")
            )
            self.assertEqual(stale_observation["freshness_status"], "stale")

            csv_path.write_bytes(csv_payload + b"tampered")
            tampered_run = root / "tampered-run"
            tampered = payload(
                run_runtime(
                    "lookup",
                    "--dir",
                    str(tampered_run),
                    "--query",
                    "SIGCOMM",
                    "--source",
                    "icore",
                    "--cache-dir",
                    str(cache_dir),
                    "--offline",
                ).stdout
            )
            self.assertEqual(tampered["match_count"], 0)
            self.assertTrue(any("hash check" in warning for warning in tampered["warnings"]))

    def test_icore_cache_rejects_future_attestation_and_preserves_historical_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache_dir = root / "cache"
            cache_dir.mkdir()
            (cache_dir / ".venue-ranking-evidence-cache").write_text(
                "venue-ranking-evidence-cache.v1\n", encoding="utf-8"
            )
            csv_path = cache_dir / "ICORE2026.csv"
            metadata_path = cache_dir / "ICORE2026.json"

            def write_cache(row_edition: str, cached_at: str) -> None:
                csv_bytes = (
                    "id,title,acronym,source,rank,rank_source,for_code\n"
                    f"11,ACM SIGCOMM Conference,SIGCOMM,{row_edition},A*,{row_edition},4606\n"
                ).encode("utf-8")
                csv_path.write_bytes(csv_bytes)
                digest = hashlib.sha256(csv_bytes).hexdigest()
                final_url = "https://portal.core.edu.au/conf-ranks/?source=ICORE2026&do=Export"
                access = {
                    "schema_version": "venue-ranking-source-access.v1",
                    "source_id": "icore",
                    "endpoint_class": "official-csv-export",
                    "requested_domain": "portal.core.edu.au",
                    "final_domain": "portal.core.edu.au",
                    "discovery_final_domain": "portal.core.edu.au",
                    "final_url": final_url,
                    "discovery_final_url": "https://portal.core.edu.au/",
                    "discovery_response_sha256": "b" * 64,
                    "discovery_response_bytes": 2048,
                    "discovery_edition_signal": "ICORE2026",
                    "retrieved_at": cached_at,
                    "response_sha256": digest,
                    "response_bytes": len(csv_bytes),
                    "edition": "ICORE2026",
                    "freshness_status": "verified-current",
                    "cache_status": "live",
                }
                metadata_path.write_text(
                    json.dumps(
                        {
                            "schema_version": "venue-ranking-evidence-cache.v1",
                            "edition": "ICORE2026",
                            "final_url": final_url,
                            "cached_at": cached_at,
                            "csv_sha256": digest,
                            "access": access,
                        }
                    ),
                    encoding="utf-8",
                )

            future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
            write_cache("ICORE2026", future)
            future_result = payload(
                run_runtime(
                    "lookup",
                    "--dir",
                    str(root / "future-run"),
                    "--query",
                    "SIGCOMM",
                    "--source",
                    "icore",
                    "--cache-dir",
                    str(cache_dir),
                    "--offline",
                ).stdout
            )
            cache_status = payload(
                run_runtime(
                    "cache", "status", "--cache-dir", str(cache_dir), check=False
                ).stdout
            )
            self.assertEqual(future_result["match_count"], 0)
            self.assertTrue(any("future" in warning for warning in future_result["warnings"]))
            self.assertFalse(cache_status["valid"])

            write_cache("ICORE2025", datetime.now(timezone.utc).isoformat())
            historical_run = root / "historical-run"
            historical_result = payload(
                run_runtime(
                    "lookup",
                    "--dir",
                    str(historical_run),
                    "--query",
                    "SIGCOMM",
                    "--source",
                    "icore",
                    "--cache-dir",
                    str(cache_dir),
                    "--offline",
                ).stdout
            )
            observation = json.loads(
                (historical_run / "observations.jsonl").read_text(encoding="utf-8")
            )
            self.assertEqual(historical_result["match_count"], 1)
            self.assertEqual(observation["edition"], "ICORE2025")
            self.assertEqual(observation["freshness_status"], "verified-historical")

    def test_cross_source_csv_coalesces_normalized_title_and_issn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry_dir = root / "registry"
            source_a = write_declarative_descriptor(
                root / "source-a.json",
                source_id="source-a",
                assertion_kinds=["quartile"],
            )
            source_b = write_declarative_descriptor(
                root / "source-b.json",
                source_id="source-b",
                assertion_kinds=["index-membership"],
            )
            add_declarative_source(registry_dir, source_a)
            add_declarative_source(registry_dir, source_b)
            export_a = root / "a.csv"
            export_a.write_text(
                "title,type,aliases,issn,kind,scheme,category,value,year,official_url\n"
                "Journal of Logic & Evidence,journal,JLE,1234-5678,quartile,SJR,Logic,Q1,2026,https://source-a.example.org/jle\n",
                encoding="utf-8",
            )
            export_b = root / "b.csv"
            export_b.write_text(
                "title,type,aliases,issn,kind,scheme,category,value,year,official_url\n"
                "Journal of Logic and Evidence,journal,JLE,12345678,index-membership,Index,,included,2026,https://source-b.example.org/jle\n",
                encoding="utf-8",
            )
            run_dir = root / "run"
            result = payload(
                run_runtime(
                    "lookup",
                    "--dir",
                    str(run_dir),
                    "--query",
                    "Journal of Logic and Evidence",
                    "--registry-dir",
                    str(registry_dir),
                    "--data-file",
                    f"source-a={export_a}",
                    "--data-file",
                    f"source-b={export_b}",
                    "--offline",
                ).stdout
            )
            venues = [json.loads(line) for line in (run_dir / "venues.jsonl").read_text().splitlines()]
            observations = [
                json.loads(line)
                for line in (run_dir / "observations.jsonl").read_text().splitlines()
            ]
            self.assertEqual(result["match_count"], 1)
            self.assertEqual(len(venues), 1)
            self.assertEqual({row["source_id"] for row in observations}, {"source-a", "source-b"})
            self.assertEqual({row["venue_id"] for row in observations}, {venues[0]["venue_id"]})

    def test_same_title_with_disjoint_issns_stays_separate_and_warns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry_dir = root / "registry"
            for source_id in ("source-a", "source-b"):
                add_declarative_source(
                    registry_dir,
                    write_declarative_descriptor(
                        root / f"{source_id}.json",
                        source_id=source_id,
                        assertion_kinds=["quartile"],
                    ),
                )
            for source_id, issn in (("source-a", "1111-1111"), ("source-b", "2222-2222")):
                (root / f"{source_id}.csv").write_text(
                    "title,type,aliases,issn,kind,scheme,category,value,year,official_url\n"
                    f"Journal of Shared Names,journal,JSN,{issn},quartile,SJR,Logic,Q1,2026,https://{source_id}.example.org/jsn\n",
                    encoding="utf-8",
                )
            run_dir = root / "run"
            result = payload(
                run_runtime(
                    "lookup",
                    "--dir",
                    str(run_dir),
                    "--query",
                    "Journal of Shared Names",
                    "--registry-dir",
                    str(registry_dir),
                    "--data-file",
                    f"source-a={root / 'source-a.csv'}",
                    "--data-file",
                    f"source-b={root / 'source-b.csv'}",
                    "--offline",
                ).stdout
            )
            venues = [
                json.loads(line)
                for line in (run_dir / "venues.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            delivery = json.loads((run_dir / "delivery.json").read_text(encoding="utf-8"))
            self.assertEqual(result["match_count"], 2)
            self.assertEqual(len(venues), 2)
            self.assertTrue(any("identity conflict" in warning for warning in delivery["warnings"]))

    def test_declarative_ids_are_stable_unicode_capable_and_identity_aware(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry_dir = root / "registry"
            descriptor = write_declarative_descriptor(
                root / "identity-source.json",
                source_id="identity-source",
                assertion_kinds=["quartile"],
            )
            add_declarative_source(registry_dir, descriptor)
            header = (
                "venue_id,title,type,aliases,issn,eissn,provider_id,kind,scheme,"
                "category,value,year,official_url\n"
            )
            rows = [
                "会议一,Journal of Shared Identity,journal,JSI-A,1111-1111,,,quartile,SJR,Logic,Q1,2026,https://identity-source.example.org/a\n",
                "会议一,Journal of Shared Identity,journal,JSI-A2,1111-1111,,,quartile,SJR,Logic,Q2,2026,https://identity-source.example.org/a\n",
                "会议二,Journal of Shared Identity,journal,JSI-B,2222-2222,,,quartile,SJR,Logic,Q3,2026,https://identity-source.example.org/b\n",
            ]
            export = root / "identity.csv"
            export.write_text(header + "".join(rows), encoding="utf-8")

            def lookup(name: str, csv_path: Path) -> tuple[dict[str, object], list[dict[str, object]]]:
                run_dir = root / name
                result = payload(
                    run_runtime(
                        "lookup",
                        "--dir",
                        str(run_dir),
                        "--query",
                        "Journal of Shared Identity",
                        "--registry-dir",
                        str(registry_dir),
                        "--data-file",
                        f"identity-source={csv_path}",
                        "--offline",
                    ).stdout
                )
                venues = [
                    json.loads(line)
                    for line in (run_dir / "venues.jsonl").read_text(encoding="utf-8").splitlines()
                ]
                return result, venues

            first_result, first_venues = lookup("first-run", export)
            reversed_export = root / "identity-reversed.csv"
            reversed_export.write_text(header + "".join(reversed(rows)), encoding="utf-8")
            second_result, second_venues = lookup("second-run", reversed_export)

            self.assertEqual(first_result["match_count"], 2)
            self.assertEqual(second_result["match_count"], 2)
            self.assertEqual(len(first_venues), 2)
            self.assertEqual(
                {
                    tuple(venue["identifiers"]["issn"]): venue["venue_id"]
                    for venue in first_venues
                },
                {
                    tuple(venue["identifiers"]["issn"]): venue["venue_id"]
                    for venue in second_venues
                },
            )
            self.assertEqual(len({venue["venue_id"] for venue in first_venues}), 2)
            first_identity = next(
                venue for venue in first_venues if venue["identifiers"]["issn"] == ["1111-1111"]
            )
            self.assertEqual(set(first_identity["aliases"]), {"JSI-A", "JSI-A2"})

            collision = root / "collision.csv"
            collision.write_text(
                header
                + "same-id,First Journal,journal,,3333-3333,,,quartile,SJR,Logic,Q1,2026,https://identity-source.example.org/first\n"
                + "same-id,Second Journal,journal,,3333-3333,,,quartile,SJR,Logic,Q2,2026,https://identity-source.example.org/second\n",
                encoding="utf-8",
            )
            failed = run_runtime(
                "lookup",
                "--dir",
                str(root / "collision-run"),
                "--query",
                "Journal",
                "--registry-dir",
                str(registry_dir),
                "--data-file",
                f"identity-source={collision}",
                "--offline",
                check=False,
            )
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("reuses venue ID", failed.stderr)

    def test_declarative_same_title_disjoint_ids_do_not_collapse_without_explicit_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry_dir = root / "registry"
            add_declarative_source(
                registry_dir,
                write_declarative_descriptor(
                    root / "same-source.json",
                    source_id="same-source",
                    assertion_kinds=["quartile"],
                ),
            )
            export = root / "same-source.csv"
            export.write_text(
                "title,type,aliases,issn,kind,scheme,category,value,year,official_url\n"
                "Journal of Duplicate Names,journal,JDN-A,1111-1111,quartile,SJR,Logic,Q1,2026,https://same-source.example.org/a\n"
                "Journal of Duplicate Names,journal,JDN-A2,1111-1111,quartile,SJR,Logic,Q2,2026,https://same-source.example.org/a\n"
                "Journal of Duplicate Names,journal,JDN-B,2222-2222,quartile,SJR,Logic,Q3,2026,https://same-source.example.org/b\n",
                encoding="utf-8",
            )
            run_dir = root / "run"
            result = payload(
                run_runtime(
                    "lookup",
                    "--dir",
                    str(run_dir),
                    "--query",
                    "Journal of Duplicate Names",
                    "--registry-dir",
                    str(registry_dir),
                    "--data-file",
                    f"same-source={export}",
                    "--offline",
                ).stdout
            )
            venues = [
                json.loads(line)
                for line in (run_dir / "venues.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(result["match_count"], 2)
            self.assertEqual(
                {tuple(venue["identifiers"]["issn"]) for venue in venues},
                {("1111-1111",), ("2222-2222",)},
            )
            repeated = next(
                venue for venue in venues if venue["identifiers"]["issn"] == ["1111-1111"]
            )
            self.assertEqual(set(repeated["aliases"]), {"JDN-A", "JDN-A2"})

    def test_token_prefix_matching_starts_at_token_boundaries(self) -> None:
        venues = [
            {
                "venue_id": "score-systems",
                "canonical_title": "Score Systems Conference",
                "venue_type": "conference",
                "aliases": [],
                "identifiers": {},
            }
        ]
        valid = call_runtime_function("match_venues", query="sco sys", venues=venues)
        mid_token = call_runtime_function("match_venues", query="ore sys", venues=venues)
        self.assertEqual(valid[0]["match_method"], "token-prefix")
        self.assertEqual(mid_token, [])

    def test_unicode_titles_match_without_latin_transliteration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            records = root / "records.json"
            records.write_text(
                json.dumps(
                    {
                        "schema_version": "venue-ranking-records.v1",
                        "synthetic": True,
                        "venues": [
                            {
                                "venue_id": "zh-journal",
                                "canonical_title": "中国科学",
                                "venue_type": "journal",
                                "aliases": [],
                                "identifiers": {},
                            },
                            {
                                "venue_id": "ru-journal",
                                "canonical_title": "Журнал вычислительной математики",
                                "venue_type": "journal",
                                "aliases": [],
                                "identifiers": {},
                            },
                        ],
                        "observations": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            for index, (query, expected_id) in enumerate(
                (
                    ("中国科学", "zh-journal"),
                    ("Журнал вычислительной математики", "ru-journal"),
                )
            ):
                run_dir = root / f"run-{index}"
                result = payload(
                    run_runtime(
                        "lookup",
                        "--dir",
                        str(run_dir),
                        "--query",
                        query,
                        "--records-file",
                        str(records),
                        "--offline",
                    ).stdout
                )
                match = json.loads((run_dir / "matches.jsonl").read_text(encoding="utf-8"))
                self.assertEqual(result["match_count"], 1)
                self.assertEqual(match["venue_id"], expected_id)

    def test_icore_association_requires_detail_identity_and_adjacent_source_rank_block(self) -> None:
        source = {
            "source_id": "icore",
            "proof": {"association_adapter": "icore-detail-text-v1"},
        }
        venue = {
            "canonical_title": "Target Conference",
            "identifiers": {"icore_id": ["11"]},
        }
        observation = {"edition": "ICORE2026", "value": "A*"}
        valid = call_runtime_function(
            "source_claim_association",
            document_text=(
                "Target Conference\nAcronym: TARGET\nSource: ICORE2026\nRank: A*\n"
            ),
            observation=observation,
            venue=venue,
            source=source,
            evidence_url="https://portal.core.edu.au/conf-ranks/11/",
        )
        adversarial = call_runtime_function(
            "source_claim_association",
            document_text=(
                "Target Conference " + "filler " * 1200
                + "Different Conference\nSource: ICORE2026\nRank: A*\n"
            ),
            observation=observation,
            venue=venue,
            source=source,
            evidence_url="https://portal.core.edu.au/conf-ranks/11/",
        )
        suffix_collision = call_runtime_function(
            "source_claim_association",
            document_text=(
                "Different Target Conference\nSource: ICORE2026\nRank: A*\n"
            ),
            observation=observation,
            venue=venue,
            source=source,
            evidence_url="https://portal.core.edu.au/conf-ranks/11/",
        )
        wrong_identity = call_runtime_function(
            "source_claim_association",
            document_text="Target Conference\nSource: ICORE2026\nRank: A*\n",
            observation=observation,
            venue=venue,
            source=source,
            evidence_url="https://portal.core.edu.au/conf-ranks/22/",
        )
        wrong_value = call_runtime_function(
            "source_claim_association",
            document_text="Target Conference\nSource: ICORE2026\nRank: A\n",
            observation=observation,
            venue=venue,
            source=source,
            evidence_url="https://portal.core.edu.au/conf-ranks/11/",
        )
        self.assertTrue(valid)
        self.assertFalse(adversarial)
        self.assertFalse(suffix_collision)
        self.assertFalse(wrong_identity)
        self.assertFalse(wrong_value)

    def test_exact_title_and_exact_alias_candidates_are_both_retained(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            records = root / "records.json"
            records.write_text(
                json.dumps(
                    {
                        "schema_version": "venue-ranking-records.v1",
                        "synthetic": True,
                        "venues": [
                            {
                                "venue_id": "exact-title",
                                "canonical_title": "Theoretical Computer Science",
                                "venue_type": "journal",
                                "aliases": ["TCS"],
                                "identifiers": {},
                            },
                            {
                                "venue_id": "exact-alias",
                                "canonical_title": "Transactions on Computational Semantics",
                                "venue_type": "journal",
                                "aliases": ["Theoretical Computer Science"],
                                "identifiers": {},
                            },
                        ],
                        "observations": [],
                    }
                ),
                encoding="utf-8",
            )
            run_dir = root / "run"
            run_runtime(
                "lookup",
                "--dir",
                str(run_dir),
                "--query",
                "Theoretical Computer Science",
                "--records-file",
                str(records),
                "--offline",
            )
            matches = [json.loads(line) for line in (run_dir / "matches.jsonl").read_text().splitlines()]
            self.assertEqual(len(matches), 2)
            self.assertEqual(
                {(row["venue_id"], row["match_method"]) for row in matches},
                {("exact-title", "exact-title"), ("exact-alias", "exact-alias")},
            )

    def test_malicious_export_text_is_inert_in_markdown_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry_dir = root / "registry"
            descriptor = write_declarative_descriptor(
                root / "markdown-source.json",
                source_id="markdown-source",
                assertion_kinds=["quartile"],
                adapter="json",
            )
            add_declarative_source(registry_dir, descriptor)
            malicious_title = (
                "Journal \u202ePDF <script>alert(1)</script>\n"
                "# injected ![proof](https://evil.example/pixel)"
            )
            export = root / "malicious.json"
            export.write_text(
                json.dumps(
                    [
                        {
                            "title": malicious_title,
                            "type": "journal",
                            "aliases": "SAFE",
                            "issn": "1234-5678",
                            "kind": "quartile",
                            "scheme": "S | X\n## forged heading",
                            "category": "`code` | [link](https://evil.example)",
                            "value": "Q1\n| forged | row |",
                            "year": "2026",
                            "official_url": "https://markdown-source.example.org/venue",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            run_dir = root / "run"
            run_runtime(
                "lookup",
                "--dir",
                str(run_dir),
                "--query",
                "SAFE",
                "--registry-dir",
                str(registry_dir),
                "--data-file",
                f"markdown-source={export}",
                "--offline",
            )
            report = (run_dir / "report.md").read_text(encoding="utf-8")
            self.assertNotIn("<script>", report)
            self.assertNotIn("\u202e", report)
            self.assertNotIn("![proof]", report)
            self.assertNotIn("\n# injected", report)
            self.assertNotIn("\n## forged heading", report)
            self.assertNotIn("| forged | row |", report)
            self.assertIn("&lt;script&gt;", report)
            self.assertIn(r"\| forged \| row \|", report)
            self.assertIn(r"https://markdown\-source\.example\.org/venue", report)

    def test_declarative_assertion_and_scalar_validation_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry_dir = root / "registry"
            descriptor = write_declarative_descriptor(
                root / "typed-source.json",
                source_id="typed-source",
                assertion_kinds=["quartile"],
                adapter="json",
            )
            add_declarative_source(registry_dir, descriptor)

            mismatch = root / "mismatch.json"
            mismatch.write_text(
                json.dumps(
                    [
                        {
                            "title": "Journal of Typed Evidence",
                            "kind": "rank",
                            "value": "A",
                            "official_url": "https://typed-source.example.org/venue",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            mismatch_result = run_runtime(
                "lookup",
                "--dir",
                str(root / "mismatch-run"),
                "--query",
                "Journal of Typed Evidence",
                "--registry-dir",
                str(registry_dir),
                "--data-file",
                f"typed-source={mismatch}",
                "--offline",
                check=False,
            )
            self.assertNotEqual(mismatch_result.returncode, 0)
            self.assertIn("not declared by source", mismatch_result.stderr)

            nullable = root / "nullable.json"
            nullable.write_text(
                json.dumps(
                    [
                        {
                            "title": "Journal of Nullable Evidence",
                            "type": None,
                            "aliases": None,
                            "kind": "quartile",
                            "category": None,
                            "value": "Q2",
                            "year": None,
                            "official_url": "https://typed-source.example.org/nullable",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            nullable_run = root / "nullable-run"
            run_runtime(
                "lookup",
                "--dir",
                str(nullable_run),
                "--query",
                "Journal of Nullable Evidence",
                "--registry-dir",
                str(registry_dir),
                "--data-file",
                f"typed-source={nullable}",
                "--offline",
            )
            nullable_observation = json.loads(
                (nullable_run / "observations.jsonl").read_text(encoding="utf-8")
            )
            self.assertIsNone(nullable_observation["category"])
            self.assertIsNone(nullable_observation["metric_year"])
            self.assertNotIn("None", (nullable_run / "report.md").read_text(encoding="utf-8"))

            nonscalar = root / "nonscalar.json"
            nonscalar.write_text(
                json.dumps(
                    [
                        {
                            "title": "Journal of Structured Evidence",
                            "kind": "quartile",
                            "value": ["Q1"],
                            "official_url": "https://typed-source.example.org/structured",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            nonscalar_result = run_runtime(
                "lookup",
                "--dir",
                str(root / "nonscalar-run"),
                "--query",
                "Journal of Structured Evidence",
                "--registry-dir",
                str(registry_dir),
                "--data-file",
                f"typed-source={nonscalar}",
                "--offline",
                check=False,
            )
            self.assertNotEqual(nonscalar_result.returncode, 0)
            self.assertIn("must contain scalar values", nonscalar_result.stderr)

    def test_credential_urls_are_rejected_before_observation_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry_dir = root / "registry"
            descriptor = write_declarative_descriptor(
                root / "credential-source.json",
                source_id="credential-source",
                assertion_kinds=["quartile"],
                adapter="json",
            )
            add_declarative_source(registry_dir, descriptor)
            for index, suffix in enumerate(
                (
                    "?sessionid=secret",
                    "?jsessionid=secret",
                    "?code=secret",
                    ";jsessionid=secret",
                    "?%2573essionid=secret",
                    "/%253Bjsessionid=secret",
                    "/access_token=secret",
                    "/token/value",
                    "/%25252Ftoken%25252Fvalue",
                    "/%252561ccess_token%253Dsecret",
                )
            ):
                export = root / f"credential-{index}.json"
                export.write_text(
                    json.dumps(
                        [
                            {
                                "title": "Journal of Credential Boundaries",
                                "kind": "quartile",
                                "value": "Q1",
                                "official_url": (
                                    "https://credential-source.example.org/venue" + suffix
                                ),
                            }
                        ]
                    ),
                    encoding="utf-8",
                )
                run_dir = root / f"run-{index}"
                result = run_runtime(
                    "lookup",
                    "--dir",
                    str(run_dir),
                    "--query",
                    "Journal of Credential Boundaries",
                    "--registry-dir",
                    str(registry_dir),
                    "--data-file",
                    f"credential-source={export}",
                    "--offline",
                    check=False,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("credential", result.stderr.casefold())
                observations = run_dir / "observations.jsonl"
                self.assertFalse(observations.exists())

    def test_proof_policy_blocks_user_export_and_unreviewed_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry_dir = root / "registry"
            descriptor = write_declarative_descriptor(
                root / "policy-source.json",
                source_id="policy-source",
                assertion_kinds=["quartile"],
                adapter="json",
            )
            add_declarative_source(registry_dir, descriptor)
            export = root / "policy.json"
            export.write_text(
                json.dumps(
                    [
                        {
                            "title": "Journal of Access Policy",
                            "kind": "quartile",
                            "value": "Q1",
                            "official_url": "https://policy-source.example.org/venue",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            run_dir = root / "run"
            run_runtime(
                "lookup",
                "--dir",
                str(run_dir),
                "--query",
                "Journal of Access Policy",
                "--registry-dir",
                str(registry_dir),
                "--data-file",
                f"policy-source={export}",
                "--offline",
            )
            observation = json.loads(
                (run_dir / "observations.jsonl").read_text(encoding="utf-8")
            )
            result = run_runtime(
                "proof",
                "--dir",
                str(run_dir),
                "--observation-id",
                observation["observation_id"],
                "--allow-network",
                "--allow-source",
                "policy-source",
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("blocked by source access policy", result.stderr)
            self.assertFalse((run_dir / "proofs" / observation["observation_id"]).exists())

    def test_verify_without_a_proof_bundle_is_unverified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            records = write_records(root / "records.json")
            run_dir = root / "run"
            run_runtime(
                "lookup",
                "--dir",
                str(run_dir),
                "--query",
                "0304-3975",
                "--records-file",
                str(records),
                "--offline",
            )
            verified = payload(run_runtime("verify", "--dir", str(run_dir), check=False).stdout)
            self.assertEqual(verified["status"], "not-ready")
            self.assertEqual(verified["verdict"], "UNVERIFIED")
            self.assertIn("no proof bundle has been captured", verified["findings"])
            self.assertEqual(verified["verified_proof_ids"], [])
            self.assertEqual(verified["verified_observation_ids"], [])

    def test_verify_binds_record_urls_and_rechecks_copied_proof_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            module, run_dir, pdf_text, pdf_check, png_check = (
                create_fixture_independent_verified_run(root)
            )

            def fresh_check(command: list[str], **_: object) -> dict[str, object]:
                return dict(pdf_check if "verify-pdf" in command else png_check)

            with (
                mock.patch.object(module, "extract_pdf_text", return_value=pdf_text),
                mock.patch.object(module, "locate_browser_runtime", return_value=Path("/fake/runtime.py")),
                mock.patch.object(module, "run_json_command", side_effect=fresh_check),
            ):
                verified = module.verify_run(run_dir)
            self.assertEqual(verified["verdict"], "VERIFIED", verified["findings"])
            self.assertEqual(
                verified["verified_proof_ids"], ["proof-obs-target-icore2026"]
            )
            self.assertEqual(
                verified["verified_observation_ids"], ["obs-target-icore2026"]
            )

            tampered_run = root / "tampered-run"
            shutil.copytree(run_dir, tampered_run)
            proof_path = tampered_run / "proofs.jsonl"
            proof = json.loads(proof_path.read_text(encoding="utf-8"))
            proof["pdf_runtime"].update(
                {
                    "final_url": "https://portal.core.edu.au/conf-ranks/22/",
                    "origin_policy": "host-only",
                    "bytes": 1,
                    "sha256": "0" * 64,
                }
            )
            proof["png_runtime"].update(
                {
                    "final_url": "https://portal.core.edu.au/conf-ranks/33/",
                    "full_page_complete": False,
                    "bytes": 1,
                    "sha256": "0" * 64,
                    "document_height": 9999,
                }
            )
            proof["pdf_runtime"].pop("navigation_complete")
            proof["png_runtime"].pop("navigation_complete")
            proof["png_runtime"].pop("document_ready_state")
            proof["pdf_verification"].update(
                {"bytes": 1, "sha256": "0" * 64, "page_count": 99}
            )
            proof["record_url_binding"] = False
            proof["claim_association"] = False
            pdf_sidecar = tampered_run / proof["pdf_sidecar_path"]
            png_sidecar = tampered_run / proof["png_sidecar_path"]
            pdf_sidecar.write_text(
                json.dumps(proof["pdf_runtime"], sort_keys=True) + "\n", encoding="utf-8"
            )
            png_sidecar.write_text(
                json.dumps(proof["png_runtime"], sort_keys=True) + "\n", encoding="utf-8"
            )
            proof["pdf_sidecar_sha256"] = hashlib.sha256(pdf_sidecar.read_bytes()).hexdigest()
            proof["png_sidecar_sha256"] = hashlib.sha256(png_sidecar.read_bytes()).hexdigest()
            proof_path.write_text(json.dumps(proof, sort_keys=True) + "\n", encoding="utf-8")
            state_path = tampered_run / "run_status.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["artifact_hashes"] = module.artifact_hashes(tampered_run)
            state_path.write_text(json.dumps(state, sort_keys=True) + "\n", encoding="utf-8")

            with (
                mock.patch.object(module, "extract_pdf_text", return_value=pdf_text),
                mock.patch.object(module, "locate_browser_runtime", return_value=Path("/fake/runtime.py")),
                mock.patch.object(module, "run_json_command", side_effect=fresh_check),
            ):
                rejected = module.verify_run(tampered_run)
            findings = "\n".join(rejected["findings"])
            self.assertEqual(rejected["verdict"], "UNVERIFIED")
            self.assertIn("do not bind to one source record", findings)
            self.assertIn("claim markers are not associated", findings)
            self.assertIn("PDF metadata mismatch", findings)
            self.assertIn("scheme-host-port origin policy", findings)
            self.assertIn("complete full-page", findings)
            self.assertIn("completed navigation", findings)
            self.assertIn("complete document readiness", findings)
            self.assertIn("PNG runtime byte count", findings)
            self.assertIn("PNG runtime SHA-256", findings)
            self.assertIn("PNG measured document dimensions", findings)
            self.assertEqual(rejected["verified_proof_ids"], [])
            self.assertEqual(rejected["verified_observation_ids"], [])

    def test_proof_rejects_fragment_url_and_symlinked_output_path_before_browser_use(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry_dir = root / "registry"
            descriptor = write_declarative_descriptor(
                root / "proof-source.json",
                source_id="proof-source",
                assertion_kinds=["quartile"],
                adapter="json",
            )
            add_declarative_source(registry_dir, descriptor)
            export = root / "proof.json"
            export.write_text(
                json.dumps(
                    [
                        {
                            "title": "Journal of Proof Boundaries",
                            "kind": "quartile",
                            "value": "Q1",
                            "official_url": "https://proof-source.example.org/venue",
                        }
                    ]
                ),
                encoding="utf-8",
            )

            def create_run(name: str) -> tuple[Path, str]:
                run_dir = root / name
                run_runtime(
                    "lookup",
                    "--dir",
                    str(run_dir),
                    "--query",
                    "Journal of Proof Boundaries",
                    "--registry-dir",
                    str(registry_dir),
                    "--data-file",
                    f"proof-source={export}",
                    "--offline",
                )
                observation = json.loads(
                    (run_dir / "observations.jsonl").read_text(encoding="utf-8")
                )
                return run_dir, observation["observation_id"]

            fragment_run, fragment_observation_id = create_run("fragment-run")
            fragment_observation = json.loads(
                (fragment_run / "observations.jsonl").read_text(encoding="utf-8")
            )
            fragment_observation["official_url"] += "#access_token=secret"
            (fragment_run / "observations.jsonl").write_text(
                json.dumps(fragment_observation, sort_keys=True) + "\n", encoding="utf-8"
            )
            rewrite_recorded_hash(fragment_run, "observations.jsonl")
            fragment_result = run_runtime(
                "proof",
                "--dir",
                str(fragment_run),
                "--observation-id",
                fragment_observation_id,
                "--allow-network",
                "--allow-source",
                "proof-source",
                check=False,
            )
            self.assertNotEqual(fragment_result.returncode, 0)
            self.assertIn("fragments are not permitted", fragment_result.stderr)

            symlink_run, symlink_observation_id = create_run("symlink-run")
            outside = root / "outside"
            outside.mkdir()
            (symlink_run / "proofs").symlink_to(outside, target_is_directory=True)
            symlink_result = run_runtime(
                "proof",
                "--dir",
                str(symlink_run),
                "--observation-id",
                symlink_observation_id,
                "--allow-network",
                "--allow-source",
                "proof-source",
                check=False,
            )
            self.assertNotEqual(symlink_result.returncode, 0)
            self.assertIn("symlink", symlink_result.stderr.lower())
            self.assertEqual(list(outside.iterdir()), [])

    def test_verify_detects_tampered_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            records = write_records(root / "records.json")
            run_dir = root / "run"
            run_runtime(
                "lookup",
                "--dir",
                str(run_dir),
                "--query",
                "TCS",
                "--records-file",
                str(records),
                "--offline",
            )
            with (run_dir / "venues.jsonl").open("a", encoding="utf-8") as handle:
                handle.write("{}\n")
            result = payload(run_runtime("verify", "--dir", str(run_dir), check=False).stdout)
            self.assertEqual(result["status"], "not-ready")
            self.assertTrue(any("hash" in finding.lower() for finding in result["findings"]))


if __name__ == "__main__":
    unittest.main()
