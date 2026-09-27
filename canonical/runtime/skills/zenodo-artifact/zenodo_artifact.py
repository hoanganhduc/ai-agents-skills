#!/usr/bin/env python3
"""Prepare and validate explicit software archives offline. Never publish."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tempfile
import zipfile
import unicodedata
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
from git_safety import admit_git_config

MAX_FILE = 512 * 1024**2


def read_regular(path: Path) -> bytes:
    s = path.lstat()
    if not stat.S_ISREG(s.st_mode) or s.st_size > MAX_FILE: raise ValueError("not a bounded regular file")
    with os.fdopen(os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)), "rb") as f:
        b = f.read(MAX_FILE + 1)
    if len(b) > MAX_FILE or path.lstat().st_mtime_ns != s.st_mtime_ns: raise ValueError("file changed while reading")
    return b


def validate_metadata(metadata: dict[str, Any]) -> list[str]:
    problems = []
    if not isinstance(metadata, dict): return ["metadata must be an object"]
    for field in ["title", "description", "version", "license"]:
        if not isinstance(metadata.get(field), str) or not metadata[field].strip(): problems.append(f"missing {field}")
    if metadata.get("upload_type") != "software": problems.append("resource must be software")
    creators = metadata.get("creators")
    if not isinstance(creators, list) or not creators or any(not isinstance(c, dict) or not isinstance(c.get("name"), str) or not c["name"].strip() for c in creators):
        problems.append("nonempty named creators required")
    if re.search(r"<[A-Z][A-Z0-9_-]+>", json.dumps(metadata)): problems.append("unresolved metadata placeholder")
    return problems


def check_citation(path: Path, metadata: dict[str, Any]) -> list[str]:
    """Check shared scalar metadata; full CFF-schema validation is separate."""
    return check_citation_text(read_regular(path).decode(), metadata)


def check_citation_text(text: str, metadata: dict[str, Any]) -> list[str]:
    fields = {}
    if text.lstrip().startswith("{"):
        fields = json.loads(text)
        if fields.get("cff-version") != "1.2.0" or fields.get("type") != "software" or not fields.get("message"):
            return ["CFF must declare version 1.2.0, software type and citation message"]
        authors = fields.get("authors", [])
        names = [a.get("name") or ", ".join(filter(None, [a.get("family-names"), a.get("given-names")])) for a in authors if isinstance(a, dict)]
        if not names or names != [c.get("name") for c in metadata.get("creators", [])]:
            return ["CFF authors and Zenodo creators disagree"]
    else:
        return ["use the JSON-compatible YAML CFF template so creator consistency can be checked offline"]
    return [f"CFF and Zenodo {k} disagree" for k in ["title", "version", "license"] if fields.get(k) != metadata.get(k)]


def write_checksums(root: Path) -> None:
    lines = []
    for p in sorted(root.iterdir()):
        if p.name == "SHA256SUMS": continue
        if not p.is_file() or p.is_symlink(): raise ValueError("bundle contains non-regular entry")
        lines.append(f"{hashlib.sha256(read_regular(p)).hexdigest()}  {p.name}")
    (root / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")


def check_zip(path: Path) -> list[str]:
    issues = []; seen = set(); file_paths = set(); total = 0
    with zipfile.ZipFile(path) as z:
        if len(z.infolist()) > 100000: return ["source archive entry limit exceeded"]
        for entry in z.infolist():
            name = entry.filename.rstrip("/")
            parts = name.split("/"); key = unicodedata.normalize("NFC", name).casefold()
            mode = entry.external_attr >> 16
            if (not name or name.startswith("/") or "\\" in name or ":" in name or len(parts) > 40
                    or any(p in {"", ".", ".."} or p.casefold() == ".git" or p != p.rstrip(" .")
                           or re.search(r'[<>:"|?*\x00-\x1f]',p) or re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?',p) for p in parts)
                    or key in seen or (stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR})
                    or any("/".join(parts[:i]).casefold() in file_paths for i in range(1,len(parts)))):
                issues.append("unsafe/duplicate archive entry")
            if not entry.is_dir(): file_paths.add(key)
            seen.add(key); total += entry.file_size
            if entry.file_size > MAX_FILE or total > 2 * 1024**3: issues.append("source archive expansion limit")
        if any(any("/".join(key.split("/")[:i]) in file_paths for i in range(1,len(key.split("/")))) for key in seen):
            issues.append("file/directory archive collision")
        if not issues and z.testzip() is not None: issues.append("source archive CRC failure")
    return issues


def source_digest(path: Path) -> str:
    files = []
    with zipfile.ZipFile(path) as z:
        for entry in z.infolist():
            if entry.is_dir(): continue
            raw = z.read(entry)
            files.append({"path": entry.filename, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
                          "executable": bool((entry.external_attr >> 16) & 0o111)})
    files.sort(key=lambda f: f["path"])
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def validate_bundle(root: Path) -> dict[str, Any]:
    issues = []
    archive_sha = None
    try:
        expected = {}
        for line in read_regular(root / "SHA256SUMS").decode().splitlines():
            match = re.fullmatch(r"([a-f0-9]{64})  ([A-Za-z0-9_.-]+)", line)
            if not match or match[2] in expected: raise ValueError("invalid checksum inventory")
            expected[match[2]] = match[1]
        actual = {p.name for p in root.iterdir() if p.name != "SHA256SUMS"}
        if actual != set(expected): issues.append("bundle file set differs from inventory")
        if len(expected) > 32: raise ValueError("bundle file-count limit")
        snapshots = {name: read_regular(root / name) for name in expected}
        if sum(map(len, snapshots.values())) > 600 * 1024**2: raise ValueError("bundle byte limit")
        for name, digest in expected.items():
            if hashlib.sha256(snapshots[name]).hexdigest() != digest: issues.append(f"checksum mismatch: {name}")
        for name in ["source.zip", "metadata.json", "provenance.json", "verification-summary.json", "dependency-inventory.json", "REPRODUCE.md"]:
            if name not in expected: issues.append(f"missing {name}")
        if "source.zip" in snapshots: issues.extend(check_zip(io.BytesIO(snapshots["source.zip"])))
        if "metadata.json" in snapshots: issues.extend(validate_metadata(json.loads(snapshots["metadata.json"])))
        if not issues:
            certificate = json.loads(snapshots["verification-summary.json"])
            provenance = json.loads(snapshots["provenance.json"])
            archive_bytes = snapshots["source.zip"]
            archive_sha = hashlib.sha256(archive_bytes).hexdigest()
            if source_digest(io.BytesIO(archive_bytes)) != certificate.get("source_digest"): issues.append("source archive differs from verified source bytes")
            if provenance.get("source_commit") != certificate.get("source", {}).get("commit"): issues.append("source revision mismatch")
            if provenance.get("semantic_status", "pending") != certificate.get("semantic_status", "pending"): issues.append("semantic status contradicts verification report")
            if provenance.get("verification_sha256") != hashlib.sha256(snapshots["verification-summary.json"]).hexdigest(): issues.append("verification provenance mismatch")
            if certificate.get("machine_status") != "passed" or certificate.get("closure_status") != "closed": issues.append("certificate is not machine-verified and closed")
            if not re.fullmatch(r"[a-f0-9]{40}", certificate.get("source", {}).get("commit", "")): issues.append("missing exact source commit")
            if json.loads(snapshots["dependency-inventory.json"]) != certificate.get("dependency_digests", {}): issues.append("dependency inventory differs from certificate")
            with zipfile.ZipFile(io.BytesIO(archive_bytes)) as z:
                if "CITATION.cff" in z.namelist(): issues.extend(check_citation_text(z.read("CITATION.cff").decode(), json.loads(snapshots["metadata.json"])))
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        issues.append(str(exc))
    return {"schema_version": "zenodo-bundle-check.v1", "ok": not issues, "status": "passed" if not issues else "failed",
            "issues": issues, "source_archive_sha256": archive_sha, "publication_enabled": False, "proof_verification_performed": False}


def publication_plan(metadata: dict[str, Any]) -> dict[str, Any]:
    issues = validate_metadata(metadata)
    return {"schema_version": "zenodo-publication-plan.v1", "status": "blocked" if issues else "proposal",
        "issues": issues, "publication_enabled": False, "default_route": "manual-bundle-upload",
        "steps": ["review exact bundle and metadata", "obtain scoped publication authorization",
                  "upload explicit files and inspect the draft", "publish only in the authorized later operation"],
        "notes": ["GitHub integration may automatically archive a newly created Release",
                  "do not assume CI artifacts or release attachments are included", "a DOI does not verify Lean proofs"]}


def prepare(project: Path, evidence: Path, metadata_path: Path, out: Path) -> dict[str, Any]:
    project = project.resolve(strict=True)
    metadata = json.loads(read_regular(metadata_path)); issues = validate_metadata(metadata)
    citation = project / "CITATION.cff"
    if citation.exists(): issues += check_citation(citation, metadata)
    if issues: return {"status": "blocked", "issues": issues, "publication_enabled": False}
    certificate = json.loads(read_regular(evidence))
    if certificate.get("machine_status") != "passed" or certificate.get("closure_status") != "closed":
        raise ValueError("a closed machine verification report is required")
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": "/nonexistent", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
           "GIT_ALLOW_PROTOCOL": "", "GIT_NO_REPLACE_OBJECTS": "1", "GIT_OPTIONAL_LOCKS": "0"}
    prefix = ["git", "--no-pager", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", "-c", "core.attributesFile=/dev/null", "-C", str(project)]
    admit_git_config(project, env)
    def git(*args): return subprocess.check_output([*prefix, *args], env=env, timeout=60).decode().strip()
    if git("status", "--porcelain", "--ignore-submodules=all"): raise ValueError("source is not clean")
    sha = git("rev-parse", "HEAD")
    if sha != certificate.get("source", {}).get("commit"): raise ValueError("certificate does not describe current source revision")
    if out.exists() or project.resolve() in out.resolve().parents: raise ValueError("bundle directory must be new and outside source")
    out.mkdir(parents=True)
    subprocess.run([*prefix, "archive", "--format=zip", "--output", str((out / "source.zip").resolve()), sha], env=env, timeout=60, check=True)
    if problems := check_zip(out / "source.zip"): raise ValueError("; ".join(problems))
    if source_digest(out / "source.zip") != certificate.get("source_digest"): raise ValueError("archive bytes differ from verified source")
    with zipfile.ZipFile(out / "source.zip") as z:
        for entry in z.infolist():
            if not entry.is_dir() and read_regular(project / entry.filename) != z.read(entry):
                raise ValueError("worktree bytes differ from verified source")
    (out / "metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out / "verification-summary.json").write_text(json.dumps(certificate, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out / "dependency-inventory.json").write_text(json.dumps(certificate.get("dependency_digests", {}), indent=2) + "\n", encoding="utf-8")
    (out / "provenance.json").write_text(json.dumps({"source_commit": sha, "verification_sha256": hashlib.sha256(read_regular(out / "verification-summary.json")).hexdigest(),
        "bundle_class": "source-and-evidence", "offline_self_contained": False, "semantic_status": certificate.get("semantic_status", "pending")}, indent=2) + "\n", encoding="utf-8")
    (out / "REPRODUCE.md").write_text("# Reproduction\n\nVerify SHA256SUMS before safe extraction.\n"
        "Provision the exact toolchain, Lax version, mathlib and database identities in verification-summary.json.\n"
        "The ZIP omits Git history: initialize a new isolated Git workspace if required and record its synthetic commit separately.\n"
        "Restore pinned dependencies; run the independent verifier over the declared scope in an isolated executor.\n"
        "This archive may require network retrieval of dependencies. It has not been published.\n", encoding="utf-8")
    write_checksums(out)
    result = validate_bundle(out)
    result["metadata_validation"] = "Zenodo-required-fields and CFF shared scalars; full CFF schema validation is external"
    return result


def restore(bundle: Path, out: Path) -> dict[str, Any]:
    result = validate_bundle(bundle)
    if not result["ok"]: return result
    raw = read_regular(bundle / "source.zip")
    if hashlib.sha256(raw).hexdigest() != result["source_archive_sha256"]: raise ValueError("bundle changed before restoration")
    if issues := check_zip(io.BytesIO(raw)): raise ValueError("; ".join(issues))
    if out.exists(): raise ValueError("restore destination must be new")
    out.mkdir(parents=True)
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        for entry in z.infolist():
            target = out / entry.filename
            if entry.is_dir(): target.mkdir(parents=True, exist_ok=True); continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(entry))
            target.chmod(0o755 if (entry.external_attr >> 16) & 0o111 else 0o644)
    return {"status": "restored", "publication_enabled": False, "git_history_present": False,
            "original_source_digest": source_digest(io.BytesIO(raw)), "proof_verification_performed": False}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="zenodo-artifact"); sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor")
    prepare_cmd = sub.add_parser("prepare")
    for key in ["project", "evidence", "metadata", "out"]: prepare_cmd.add_argument("--" + key, type=Path, required=True)
    validate_cmd = sub.add_parser("validate"); validate_cmd.add_argument("--dir", type=Path, required=True)
    restore_cmd = sub.add_parser("restore"); restore_cmd.add_argument("--bundle", type=Path, required=True); restore_cmd.add_argument("--out", type=Path, required=True)
    plan_cmd = sub.add_parser("publication-plan"); plan_cmd.add_argument("--metadata", type=Path, required=True)
    try:
        args = p.parse_args(argv)
        if args.command == "doctor": result = {"status": "ok", "network_required": False, "publication_enabled": False, "credentials_required": False}
        elif args.command == "validate": result = validate_bundle(args.dir)
        elif args.command == "restore": result = restore(args.bundle, args.out)
        elif args.command == "publication-plan": result = publication_plan(json.loads(read_regular(args.metadata)))
        else: result = prepare(args.project, args.evidence, args.metadata, args.out)
        print(json.dumps(result, indent=2, sort_keys=True)); return 1 if result.get("status") in {"failed", "blocked", "error"} else 0
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "error", "reason": str(exc)[:500], "publication_enabled": False})); return 1


if __name__ == "__main__": raise SystemExit(main())
