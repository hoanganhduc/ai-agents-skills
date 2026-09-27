#!/usr/bin/env python3
"""Bind local workflow readiness; render paper metadata and safe CI summaries.

Controller attestations must come from actual independent review runs outside
the candidate. This checks their bindings, not the truth of human judgments or
cryptographic identities. No command builds, logs in, fetches or publishes.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import subprocess
import zipfile
from urllib.parse import urlsplit

from lax_formalization import digest, read_json, regular_bytes, tree_inventory
from lax_executor import executor_identity, git, source_inventory
from public_source import (disjoint, ordinary_root, private_path, public_origin,
                           read_private_json, relative_name, write_private_json)

POLICY = "lax-paper-workflow.v1"
HASH = re.compile(r"[a-f0-9]{64}")
COMMIT = re.compile(r"[a-f0-9]{40}")
OPERATIONS = {"formalize-new", "from-existing-lean", "update-lax-artifact", "update-paper-metadata"}
PAPER_MODES = {"link-only", "source-in-repo", "embedded-tex"}
JOB_KEYS = {"schema_version", "job_id", "operation", "execution", "project_root", "submission",
            "source_repo", "public_origin", "source_commit", "targets", "producer_run", "paper_mode", "paper",
            "verification", "correspondence", "privacy_review", "public_inventory", "paper_build"}


def bounded_string(value, limit=200) -> bool:
    return isinstance(value, str) and 0 < len(value) <= limit and not re.search(r"[\x00-\x1f]", value)


def job_record(path: Path) -> tuple[dict, Path, Path]:
    job = read_private_json(path)
    if set(job) - JOB_KEYS or job.get("schema_version") != "lax-workflow-job.v1":
        raise ValueError("invalid-job-contract")
    if job.get("operation") not in OPERATIONS or job.get("execution") not in {"audit-only", "prepare-local"}:
        raise ValueError("invalid-job-operation")
    if job.get("paper_mode") not in PAPER_MODES or not bounded_string(job.get("producer_run")):
        raise ValueError("invalid-job-review-profile")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", job.get("job_id", "")):
        raise ValueError("invalid-job-id")
    if not COMMIT.fullmatch(job.get("source_commit", "")): raise ValueError("invalid-source-commit")
    if not Path(job["project_root"]).is_absolute(): raise ValueError("absolute-project-root-required")
    if job["operation"] == "from-existing-lean" and not job.get("source_repo"):
        raise ValueError("original-source-root-required")
    project = ordinary_root(Path(job["project_root"])); controller = Path(path).resolve().parent
    private_path(path, excluded=(project,))
    if job.get("source_repo"):
        if not Path(job["source_repo"]).is_absolute(): raise ValueError("absolute-source-root-required")
        source = ordinary_root(Path(job["source_repo"]))
        if not disjoint(source, project): raise ValueError("source-and-public-repo-overlap")
        private_path(path, excluded=(source, project))
    public_origin(job["public_origin"])
    folder = job.get("submission", "submission")
    if folder != ".": relative_name(folder)
    targets = job.get("targets")
    if not isinstance(targets, list) or not 1 <= len(targets) <= 500 or any(
            not isinstance(t, str) or not re.fullmatch(r"[A-Za-z_][\w.']*", t) for t in targets) or len(set(targets)) != len(targets):
        raise ValueError("invalid-target-inventory")
    paper = job.get("paper", {})
    if not isinstance(paper, dict) or set(paper) != {"id", "version", "path", "sha256"} or not all(
            bounded_string(paper.get(k), 1024) for k in ["id", "version", "path"]) or not HASH.fullmatch(paper.get("sha256", "")):
        raise ValueError("invalid-paper-identity")
    return job, project, controller


def controller_file(controller: Path, name: str, project: Path) -> Path:
    if not isinstance(name, str): raise ValueError("invalid-controller-reference")
    relative_name(name)
    path = controller / name
    if not path.resolve().is_relative_to(controller) or path.is_symlink():
        raise ValueError("controller-reference-escape")
    return private_path(path, excluded=(project,))


def controller_json(controller: Path, name: str, project: Path) -> dict:
    return read_json(controller_file(controller, name, project))


def independent_review(record: dict, schema: str, producer: str) -> bool:
    return (isinstance(record, dict) and bounded_string(producer)
            and record.get("schema_version") == schema and record.get("policy_version") == POLICY
            and record.get("status") == "accepted" and record.get("producer_run") == producer
            and bounded_string(record.get("reviewer_run")) and record["reviewer_run"] != producer)


def check_readiness(path: Path) -> dict:
    result = {"schema_version": "lax-workflow-readiness.v1", "policy_version": POLICY,
              "status": "blocked", "publication_enabled": False, "issues": []}
    try:
        job, project, control = job_record(path)
        if job["execution"] != "prepare-local" or job["operation"] == "update-paper-metadata":
            raise ValueError("job-does-not-request-artifact-readiness")
        if git(project, "status", "--porcelain", "--ignore-submodules=all"):
            raise ValueError("public-source-not-clean")
        commit = git(project, "rev-parse", "HEAD")
        if commit != job["source_commit"] or git(project, "remote", "get-url", "origin") != job["public_origin"]:
            raise ValueError("public-source-identity-changed")
        inventory = source_inventory(project)
        approved = controller_json(control, job["public_inventory"], project)
        if approved.get("files") != inventory["files"] or approved.get("sha256") != inventory["sha256"]:
            raise ValueError("public-inventory-changed")
        paper = job["paper"]
        paper_raw = regular_bytes(controller_file(control, paper["path"], project))
        if hashlib.sha256(paper_raw).hexdigest() != paper["sha256"]:
            raise ValueError("paper-bytes-changed")
        report = controller_json(control, job["verification"], project)
        expected_source = {"repository": job["public_origin"], "commit": commit}
        if report.get("schema_version") != "lax-verification.v1" or report.get("backend") != "lax":
            raise ValueError("unsupported-verification-report")
        if any(report.get(k) != v for k, v in {"status": "passed", "machine_status": "passed",
                "closure_status": "closed", "semantic_status": "accepted"}.items()):
            raise ValueError("verification-or-semantic-review-not-accepted")
        if any(report.get("source", {}).get(k) != v for k, v in expected_source.items()):
            raise ValueError("verification-source-mismatch")
        concepts = project / job.get("submission", "submission") / "concepts"
        concept_hash = tree_inventory(concepts)["sha256"]
        bindings = {"source_commit": commit, "source_digest": inventory["sha256"],
                    "challenge_digest": concept_hash, "scope_digest": digest(job["targets"]),
                    "dependency_digests": report.get("dependency_digests")}
        if not isinstance(bindings["dependency_digests"], dict): raise ValueError("missing-dependency-bindings")
        for key in ["source_digest", "challenge_digest", "scope_digest"]:
            if report.get(key) != bindings[key]: raise ValueError("verification-binding-mismatch")
        if report.get("targets") != job["targets"] or report.get("tools", {}).get("executor_sha256") != executor_identity():
            raise ValueError("verification-target-or-executor-mismatch")
        review = controller_json(control, job["correspondence"], project)
        if not independent_review(review, "paper-correspondence.v1", job["producer_run"]):
            raise ValueError("correspondence-review-not-independent")
        expected = {**bindings, "paper_id": paper["id"], "paper_version": paper["version"], "paper_sha256": paper["sha256"]}
        if any(review.get(k) != v for k, v in expected.items()): raise ValueError("correspondence-binding-mismatch")
        privacy = controller_json(control, job["privacy_review"], project)
        if not independent_review(privacy, "public-review.v1", job["producer_run"]):
            raise ValueError("privacy-review-not-independent")
        if any(privacy.get(k) != v for k, v in {"source_commit": commit, "history_tip": commit,
                "source_digest": inventory["sha256"], "public_origin": job["public_origin"]}.items()):
            raise ValueError("privacy-review-stale")
        paper_build_status = "not_requested"
        if job["paper_mode"] != "link-only":
            build = controller_json(control, job["paper_build"], project)
            if build.get("schema_version") != "paper-build-review.v1" or build.get("status") != "passed" or any(
                    build.get(k) != v for k, v in {"source_commit": commit, "source_digest": inventory["sha256"],
                    "paper_sha256": paper["sha256"], "mode": job["paper_mode"]}.items()):
                raise ValueError("paper-build-not-bound")
            if not all(build.get(k) is True for k in ["isolated", "no_private_mounts", "compiled", "render_reviewed"]):
                raise ValueError("paper-build-incomplete")
            for key in ["toolchain_digest", "output_digest"]:
                if not HASH.fullmatch(build.get(key, "")): raise ValueError("paper-build-missing-digest")
            paper_build_status = "passed"
        result.update(status="local_ready", job_id=job["job_id"], source_commit=commit,
                      source_digest=inventory["sha256"], paper_id=paper["id"], paper_sha256=paper["sha256"],
                      machine_status="passed", closure_status="closed", correspondence_status="accepted",
                      privacy_status="accepted", paper_build_status=paper_build_status,
                      zenodo_status="not_requested", job_digest=digest(job),
                      limitations=["controller attestation provenance is trusted, not cryptographic authentication",
                                   "first-submit binding or renumbering requires new exact-source verification",
                                   "no remote publication or hosted paper workflow is certified"])
    except (OSError, ValueError, KeyError, TypeError, UnicodeError, subprocess.SubprocessError):
        # Paths, repository-provided exception text and private notes are not public diagnostics.
        result["issues"] = ["workflow-binding-or-required-check-refused"]
    return result


def validate_registry(registry: dict) -> None:
    if not isinstance(registry, dict) or set(registry) != {"schema_version", "papers"} or registry["schema_version"] != "paper-versions.v1":
        raise ValueError("invalid-paper-registry")
    if not isinstance(registry["papers"], list) or len(registry["papers"]) > 100: raise ValueError("invalid-paper-list")
    ids = set()
    for paper in registry["papers"]:
        if not isinstance(paper, dict) or set(paper) - {"id", "kind", "title", "version", "url", "doi", "venue", "year", "sha256", "relation", "related_to"}:
            raise ValueError("invalid-paper-fields")
        if not all(bounded_string(paper.get(k), 1024) for k in ["id", "title", "version", "url"]):
            raise ValueError("incomplete-paper-entry")
        if paper["id"] in ids or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", paper["id"]): raise ValueError("invalid-paper-id")
        ids.add(paper["id"])
        if paper.get("kind") not in {"arxiv", "conference", "journal", "correction", "manuscript"}:
            raise ValueError("invalid-paper-kind")
        url = urlsplit(paper["url"])
        if url.scheme != "https" or not url.hostname or url.username or url.password or any(x in paper["url"] for x in "<>\n\r "):
            raise ValueError("invalid-paper-url")
        if paper["kind"] == "arxiv" and (url.hostname not in {"arxiv.org", "www.arxiv.org"}
                or not re.fullmatch(r"/abs/(?:\d{4}\.\d{4,5}|[A-Za-z.-]+/\d{7})v[1-9][0-9]*", url.path)
                or not url.path.endswith(paper["version"])):
            raise ValueError("arxiv-version-required")
        if "sha256" in paper and not HASH.fullmatch(paper["sha256"]): raise ValueError("invalid-paper-digest")
        if "doi" in paper and not re.fullmatch(r"10\.[0-9]{4,9}/\S+", paper["doi"]): raise ValueError("invalid-paper-doi")
        if "relation" in paper and paper["relation"] not in {"extends", "corrects", "version-of"}:
            raise ValueError("invalid-paper-relation")
        if ("relation" in paper) != ("related_to" in paper): raise ValueError("incomplete-paper-relation")
    if any(p.get("related_to") not in ids or p.get("related_to") == p["id"] for p in registry["papers"] if "related_to" in p):
        raise ValueError("unknown-related-paper")


def md(value: str) -> str:
    return re.sub(r"([\\`*_|\[\]()<>])", r"\\\1", value).replace("\n", " ").replace("\r", " ")


def render_papers(registry: dict, reviews: list[dict] | None = None) -> str:
    validate_registry(registry)
    rows = ["<!-- lax-paper-versions:start -->", "## Paper versions and publications", "",
            "Bibliographic entries do not by themselves establish formalization coverage.", "",
            "| Paper | Kind / version | Reference | Correspondence |", "|---|---|---|---|"]
    for paper in registry["papers"]:
        status = "not-reviewed"
        for review in reviews or []:
            if (independent_review(review, "paper-correspondence.v1", review.get("producer_run"))
                    and HASH.fullmatch(paper.get("sha256", "")) and HASH.fullmatch(review.get("paper_sha256", ""))
                    and review.get("paper_id") == paper["id"] and review.get("paper_version") == paper["version"]
                    and review.get("paper_sha256") == paper.get("sha256")
                    and COMMIT.fullmatch(review.get("source_commit", "")) and HASH.fullmatch(review.get("scope_digest", ""))):
                status = "accepted for commit `" + review["source_commit"] + "`, scope `" + review["scope_digest"] + "`"
        url = paper["url"].replace("(", "%28").replace(")", "%29")
        rows.append(f"| {md(paper['title'])} | {md(paper['kind'])} / {md(paper['version'])} | [paper]({url}) | {status} |")
    return "\n".join([*rows, "", "Pinned formal artifacts retain their original commit even when this table changes.",
                      "<!-- lax-paper-versions:end -->", ""])


def public_summary(report: dict) -> dict:
    if not isinstance(report, dict) or not isinstance(report.get("source"), dict):
        raise ValueError("invalid-report-shape")
    if report.get("schema_version") != "lax-verification.v1" or any(report.get(k) != v for k, v in {
            "status": "passed", "machine_status": "passed", "closure_status": "closed"}.items()):
        raise ValueError("no-passing-machine-report")
    source = report.get("source", {})
    public_origin(source.get("repository"))
    if not COMMIT.fullmatch(source.get("commit", "")): raise ValueError("invalid-source-commit")
    if not re.fullmatch(r"lax-[1-9][0-9]*", report.get("submission_id", "")): raise ValueError("invalid-submission-id")
    if not HASH.fullmatch(report.get("source_digest", "")) or not HASH.fullmatch(report.get("scope_digest", "")):
        raise ValueError("invalid-report-binding")
    return {"schema_version": "lax-public-ci-summary.v1", "machine_status": "passed", "closure_status": "closed",
            "semantic_status": "pending", "privacy_status": "not-certified-by-ci", "publication_enabled": False,
            "submission_id": report["submission_id"], "source": {"repository": source["repository"], "commit": source["commit"]},
            "source_digest": report["source_digest"], "scope_digest": report["scope_digest"]}


def public_bundle_check(bundle: Path) -> dict:
    """Check an already-public CI source bundle without rewriting its evidence.

    This is an outbound diagnostics check, NOT privacy approval of source. The
    source must pass the local privacy gate before the push that started CI.
    """
    expected_files = {"source.zip", "metadata.json", "provenance.json", "verification-summary.json",
                      "dependency-inventory.json", "REPRODUCE.md", "SHA256SUMS"}
    if set(p.name for p in bundle.iterdir()) != expected_files: raise ValueError("unreviewed-bundle-file")
    module_path = Path(__file__).resolve().parent.parent / "zenodo-artifact/zenodo_artifact.py"
    spec = importlib.util.spec_from_file_location("_workflow_zenodo", module_path)
    if spec is None or spec.loader is None: raise ValueError("zenodo-runtime-unavailable")
    zenodo = importlib.util.module_from_spec(spec); spec.loader.exec_module(zenodo)
    checked = zenodo.validate_bundle(bundle)
    if not checked.get("ok"): raise ValueError("invalid-bundle")
    receipt = read_json(bundle / "verification-summary.json")
    public_summary(receipt)
    allowed = {"schema_version", "backend", "status", "machine_status", "closure_status", "closure",
               "semantic_status", "publication_status", "publication_enabled", "submission_id", "submission_folder",
               "source", "source_digest", "scope_digest", "targets", "challenge_digest", "request_digest",
               "database_commit", "database_digest", "tools", "capture", "phases", "coverage", "verified_proofs",
               "dependency_digests", "limitations"}
    if set(receipt) != allowed: raise ValueError("unreviewed-receipt-fields")
    def exact_keys(value, keys):
        if not isinstance(value, dict) or set(value) != set(keys): raise ValueError("unreviewed-nested-receipt-fields")
    def names(value):
        if not isinstance(value, list) or any(not isinstance(n, str) or not re.fullmatch(r"[A-Za-z_][\w.']*", n) for n in value):
            raise ValueError("invalid-public-name-list")
    def hash_value(value, pattern=HASH):
        if not isinstance(value, str) or not pattern.fullmatch(value): raise ValueError("invalid-public-digest")
    def size(value):
        if type(value) is not int or not 0 <= value <= 2 * 1024**3: raise ValueError("invalid-public-size")
    exact_keys(receipt["source"], ["repository", "commit", "tree"])
    hash_value(receipt["source"]["tree"], COMMIT)
    hash_value(receipt["database_commit"], COMMIT)
    for field in ["challenge_digest", "request_digest", "database_digest"]: hash_value(receipt[field])
    names(receipt["targets"])
    if (receipt["backend"] != "lax" or receipt["publication_enabled"] is not False
            or receipt["publication_status"] not in {"local", "registered"}
            or receipt["semantic_status"] not in {"accepted", "pending"}):
        raise ValueError("invalid-public-status")
    if receipt["submission_folder"] != ".": relative_name(receipt["submission_folder"])
    if not isinstance(receipt["dependency_digests"], dict): raise ValueError("invalid-public-dependencies")
    for name, value in receipt["dependency_digests"].items():
        if not re.fullmatch(r"lax-[1-9][0-9]*", name): raise ValueError("invalid-public-dependency-id")
        hash_value(value)
    exact_keys(receipt["tools"], ["lax", "spec_sha256", "package_sha256", "executor_sha256", "trusted_inputs", "environment", "mathlib", "image"])
    exact_keys(receipt["tools"]["trusted_inputs"], ["elan_launchers", "inspector", "toolchain", "warm"])
    for key in ["spec_sha256", "package_sha256", "executor_sha256"]:
        if not isinstance(receipt["tools"][key], str) or not HASH.fullmatch(receipt["tools"][key]):
            raise ValueError("invalid-tool-digest")
    if any(not isinstance(v, str) or not HASH.fullmatch(v) for v in receipt["tools"]["trusted_inputs"].values()):
        raise ValueError("invalid-trusted-input-digest")
    from lax_formalization import ENVIRONMENT, MATHLIB_SHA, VERSION
    if (receipt["tools"]["lax"] != VERSION or receipt["tools"]["environment"] != ENVIRONMENT
            or receipt["tools"]["mathlib"] != MATHLIB_SHA or not isinstance(receipt["tools"]["image"], str)
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", receipt["tools"]["image"])):
        raise ValueError("unsupported-public-tool-profile")
    exact_keys(receipt["capture"], ["sha256", "files", "bytes"])
    hash_value(receipt["capture"]["sha256"]); size(receipt["capture"]["bytes"])
    if not isinstance(receipt["capture"]["files"], list): raise ValueError("invalid-capture-files")
    for entry in receipt["capture"]["files"]:
        exact_keys(entry, ["path", "bytes", "sha256", "executable"]); relative_name(entry["path"])
        hash_value(entry["sha256"]); size(entry["bytes"])
        if type(entry["executable"]) is not bool: raise ValueError("invalid-public-file-mode")
    exact_keys(receipt["coverage"], ["concepts", "proofs"])
    names(receipt["coverage"]["concepts"]); names(receipt["coverage"]["proofs"])
    exact_keys(receipt["closure"], ["closed", "open", "witnesses"])
    if receipt["closure"]["closed"] is not True or receipt["closure"]["open"] != []:
        raise ValueError("open-public-proof-closure")
    names(receipt["closure"]["open"]); names(receipt["closure"]["witnesses"])
    if not isinstance(receipt["verified_proofs"], list): raise ValueError("invalid-public-proofs")
    for proof in receipt["verified_proofs"]:
        exact_keys(proof, ["id", "path", "conclusion", "assumptions", "description", "state", "independently_verified"])
        relative_name(proof["path"])
        names([proof["id"], proof["conclusion"]]); names(proof["assumptions"])
        if (proof["state"] not in {"local", "registered"} or proof["independently_verified"] is not True
                or not isinstance(proof["description"], str) or len(proof["description"]) > 1024**2):
            raise ValueError("invalid-public-proof-fields")
    if receipt["limitations"] != ["kernel/toolchain and pinned mathlib are declared background trust",
            "no external comparator was run", "no remote publication was tested"]:
        raise ValueError("unreviewed-limitations")
    provenance = read_json(bundle / "provenance.json")
    exact_keys(provenance, ["source_commit", "verification_sha256", "bundle_class", "offline_self_contained", "semantic_status"])
    if provenance["bundle_class"] != "source-and-evidence" or provenance["offline_self_contained"] is not False:
        raise ValueError("invalid-bundle-provenance")
    with zipfile.ZipFile(bundle / "source.zip") as source_zip:
        archived_metadata = json.loads(source_zip.read(".zenodo.json"))
    if archived_metadata != read_json(bundle / "metadata.json"):
        raise ValueError("ci-metadata-must-be-in-public-source")
    if not isinstance(receipt["phases"], list): raise ValueError("invalid-public-phases")
    for phase in receipt["phases"]:
        if set(phase) != {"mode", "exit", "seconds", "teardown_confirmed", "transcript_sha256"}:
            raise ValueError("raw-phase-diagnostics-not-public")
        if phase["exit"] != 0 or phase["teardown_confirmed"] is not True or not HASH.fullmatch(phase["transcript_sha256"]):
            raise ValueError("incomplete-phase")
        if (phase["mode"] not in {"static", "concepts", "compile", "check"}
                or type(phase["seconds"]) not in {int, float} or not math.isfinite(phase["seconds"])
                or phase["seconds"] < 0):
            raise ValueError("invalid-public-phase-fields")
    # Unknown nested debug fields and host paths cannot ride along as evidence.
    forbidden_keys = {"log", "logs", "stdout", "stderr", "transcript", "request", "project_root", "challenge_root"}
    def inspect(value):
        if isinstance(value, dict):
            if forbidden_keys.intersection(value): raise ValueError("private-diagnostic-field")
            for child in value.values(): inspect(child)
        elif isinstance(value, list):
            for child in value: inspect(child)
        elif isinstance(value, str) and re.search(r"(?:[/](?:home|Users|tmp|private)/|[A-Za-z]:\\|gh[pousr]_[A-Za-z0-9]{20,}|-----BEGIN .*PRIVATE KEY-----)", value):
            raise ValueError("private-diagnostic-value")
    inspect(receipt); inspect(provenance)
    return {"status": "public-bundle-structure-checked", "publication_enabled": False,
            "source_privacy": "requires-pre-push-review", "source_archive_sha256": checked["source_archive_sha256"]}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__); sub = p.add_subparsers(dest="command", required=True)
    check = sub.add_parser("readiness"); check.add_argument("--job", type=Path, required=True); check.add_argument("--out", type=Path, required=True)
    snapshot = sub.add_parser("snapshot"); snapshot.add_argument("--project", type=Path, required=True); snapshot.add_argument("--out", type=Path, required=True)
    papers = sub.add_parser("render-papers"); papers.add_argument("--registry", type=Path, required=True); papers.add_argument("--out", type=Path, required=True)
    papers.add_argument("--reviews", type=Path, help="optional controller-owned JSON object with a reviews list")
    summary = sub.add_parser("public-summary"); summary.add_argument("--evidence", type=Path, required=True); summary.add_argument("--out", type=Path, required=True)
    bundle = sub.add_parser("public-bundle"); bundle.add_argument("--bundle", type=Path, required=True)
    args = p.parse_args(argv)
    try:
        if hasattr(args, "out") and (args.out.exists() or args.out.is_symlink()):
            raise ValueError("output-must-be-new")
        if args.command == "snapshot":
            project = ordinary_root(args.project)
            private_path(args.out, excluded=(project,))
            if git(project, "status", "--porcelain", "--ignore-submodules=all"): raise ValueError("public-source-not-clean")
            public_origin(git(project, "remote", "get-url", "origin"))
            inventory = source_inventory(project); write_private_json(args.out, inventory)
            result = {"status": "snapshotted", "publication_enabled": False}
        elif args.command == "readiness":
            job, project, control = job_record(args.job)
            excluded = (project, ordinary_root(Path(job["source_repo"]))) if job.get("source_repo") else (project,)
            out = private_path(args.out, excluded=excluded)
            inputs = [job[k] for k in ["verification", "correspondence", "privacy_review", "public_inventory"]]
            inputs.append(job["paper"]["path"])
            if "paper_build" in job: inputs.append(job["paper_build"])
            if out in {args.job.resolve(), *(controller_file(control, name, project) for name in inputs)}:
                raise ValueError("output-overlaps-input")
            result = check_readiness(args.job); write_private_json(out, result)
        elif args.command == "render-papers":
            reviews = read_private_json(args.reviews).get("reviews", []) if args.reviews else []
            result = {"status": "rendered", "publication_enabled": False}
            text = render_papers(read_json(args.registry), reviews)
            # Always a new file: the caller reviews/integrates the section, never
            # overwrites an existing README or the registry through this helper.
            with args.out.open("x", encoding="utf-8") as stream: stream.write(text)
        elif args.command == "public-bundle":
            result = public_bundle_check(args.bundle)
        else:
            result = public_summary(read_json(args.evidence))
            with args.out.open("x", encoding="utf-8") as stream: json.dump(result, stream, sort_keys=True, indent=2); stream.write("\n")
        print(json.dumps({k: result[k] for k in ["status", "machine_status", "publication_enabled"] if k in result}))
        return 1 if result.get("status") == "blocked" else 0
    except (OSError, ValueError, KeyError, TypeError, UnicodeError, subprocess.SubprocessError, zipfile.BadZipFile):
        print(json.dumps({"status": "blocked", "reason": "workflow-check-refused", "publication_enabled": False})); return 1


if __name__ == "__main__": raise SystemExit(main())
