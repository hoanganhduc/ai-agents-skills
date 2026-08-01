#!/usr/bin/env python3
"""Personal Lean research-library steward: reuse-first search, user-gated
intake/staging, upstream preparation, maintenance, and paper-artifact
scaffolding. Propose-only by design: never commits, pushes, or publishes."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

SCHEMA = "lean-research-library.v1"
TOOLS = ("lean", "lake", "elan", "git")
TOOL_ENV = {"lean": "AAS_LEAN", "lake": "AAS_LAKE"}
ALLOWED_AXIOMS = ("propext", "Classical.choice", "Quot.sound")

# Endpoint defaults, every one overridable by env (mirrors LEANSEARCHCLIENT_*).
LOOGLE_URL = os.environ.get("AAS_LOOGLE_URL", "https://loogle.lean-lang.org/json")
STATESEARCH_URL = os.environ.get("AAS_LEANSTATESEARCH_URL", "https://premise-search.com/api/search")
LEANSEARCH_URL = os.environ.get("AAS_LEANSEARCH_URL", "https://leansearch.net/search")
MATHLIB_RELEASES_URL = "https://api.github.com/repos/leanprover-community/mathlib4/releases?per_page=30"
ZENODO_SANDBOX_API = "https://sandbox.zenodo.org/api"
ZENODO_PRODUCTION_API = "https://zenodo.org/api"

# Packages whose search hits are statements-only collections, never reusable proofs.
STATEMENT_ONLY_MODULES = ("FormalConjectures",)

def safe_read(path: Path) -> str | None:
    """Read text or return None; non-UTF8/unreadable files are skipped, not crashes."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


DECL_RE = re.compile(
    r"^\s*(?:@\[[^\]]*\]\s*)*(?:private\s+|protected\s+|noncomputable\s+|unsafe\s+)*"
    r"(?:theorem|lemma|def|abbrev|structure|inductive|class|instance)\s+([A-Za-z_][\w.']*)",
    re.MULTILINE,
)
IMPORT_RE = re.compile(r"^(?:public |private )?(?:meta )?import (?:all )?(\S+)", re.MULTILINE)


# ---------------------------------------------------------------- config ----

def config_path() -> Path:
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "lean-research-library" / "config.json"
    xdg = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(xdg) / "lean-research-library" / "config.json"


def load_config() -> dict[str, Any]:
    cfg: dict[str, Any] = {}
    path = config_path()
    if path.is_file():
        try:
            cfg = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            cfg = {"config_error": f"unreadable config at {path}"}
    env_root = os.environ.get("AAS_LEAN_LIBRARY_ROOT", "").strip()
    if env_root:
        cfg["library_root"] = env_root
    return cfg


def library_root(cfg: dict[str, Any]) -> Path | None:
    root = str(cfg.get("library_root", "")).strip()
    if not root:
        return None
    path = Path(root).expanduser()
    return path if path.is_dir() else None


def library_module(cfg: dict[str, Any]) -> str:
    return str(cfg.get("library_module", "HoangMathLib"))


def first_run_guidance() -> dict[str, Any]:
    return {
        "library_configured": False,
        "guidance": [
            "clone the library: git clone https://github.com/hoanganhduc/HoangMathLib.git",
            f"then either set AAS_LEAN_LIBRARY_ROOT to the checkout, or write {config_path()}"
            ' with {"library_root": "<checkout>", "library_module": "HoangMathLib"}',
            "optional keys: template_root (clone of lean-paper-artifact-template),"
            " peer_satellites (list of checkout paths), closed_deps (bool)",
        ],
    }


# ---------------------------------------------------------------- doctor ----

def tool_status() -> dict[str, dict[str, Any]]:
    status: dict[str, dict[str, Any]] = {}
    for tool in TOOLS:
        env_var = TOOL_ENV.get(tool)
        if env_var and os.environ.get(env_var, "").strip():
            candidate = os.environ[env_var].strip()
            resolved = shutil.which(candidate) if "/" not in candidate and "\\" not in candidate else (
                candidate if Path(candidate).expanduser().is_file() else ""
            )
            status[tool] = {
                "status": "available" if resolved else "tool_unavailable",
                "path": resolved or candidate,
                "source": "env",
            }
            continue
        path = shutil.which(tool)
        if not path:
            elan_bin = Path.home() / ".elan" / "bin" / (f"{tool}.exe" if os.name == "nt" else tool)
            path = str(elan_bin) if elan_bin.is_file() else ""
        status[tool] = {
            "status": "available" if path else "tool_unavailable",
            "path": path,
            "source": "path" if path else "not-found",
        }
    return status


def doctor_payload(cfg: dict[str, Any], *, ecosystem: bool) -> dict[str, Any]:
    root = library_root(cfg)
    payload: dict[str, Any] = {
        "schema_version": SCHEMA,
        "status": "ok",
        "tool_status": tool_status(),
        "no_auto_install": True,
        "installs_attempted": False,
        "network_required": False,
        "library_configured": root is not None,
        "config_path": str(config_path()),
    }
    if "config_error" in cfg:
        payload["config_error"] = cfg["config_error"]
    if root is None:
        payload.update(first_run_guidance())
    else:
        payload["library_root"] = str(root)
        payload["library_module"] = library_module(cfg)
        payload["pinned_mathlib"] = pinned_mathlib_rev(root)
    if ecosystem:
        payload["network_required"] = True
        payload["ecosystem"] = ecosystem_drift(root)
    return payload


def pinned_mathlib_rev(root: Path) -> str:
    lakefile = root / "lakefile.toml"
    if lakefile.is_file():
        match = re.search(r'^rev\s*=\s*"([^"]+)"', lakefile.read_text(encoding="utf-8"), re.MULTILINE)
        if match:
            return match.group(1)
    return "unknown"


def ecosystem_drift(root: Path | None) -> dict[str, Any]:
    releases = http_json("GET", MATHLIB_RELEASES_URL)
    latest_stable = ""
    if isinstance(releases, list):
        for release in releases:
            tag = str(release.get("tag_name", ""))
            if tag and "rc" not in tag and "nightly" not in tag:
                latest_stable = tag
                break
    endpoints = {
        "loogle": endpoint_alive(f"{LOOGLE_URL}?q=Nat"),
        "leanstatesearch": endpoint_alive(f"{STATESEARCH_URL}?query=Nat&results=1"),
    }
    action_inputs = {
        "leanprover/lean-action@38fbc41a8c28c4cbaec22d7f7de508ec2e7c0dd9":
            ("build-args", "lint", "mk_all-check", "leanchecker"),
        "leanprover-community/mathlib-update-action@30121004826adb85f006e31ce5d25a33ce79c7a6":
            ("intermediate_releases", "on_update_succeeds", "on_update_fails"),
        "leanprover-community/upstreaming-dashboard-action@e3ee7dc54fd376f093ef62973d7b04cf7beabad0":
            ("website-directory", "project-name", "branch-name", "relevant-labels"),
    }
    action_drift: dict[str, Any] = {}
    for pinned, expected in action_inputs.items():
        repo_ref, _, sha = pinned.partition("@")
        raw = f"https://raw.githubusercontent.com/{repo_ref}/{sha}/action.yml"
        request = urllib.request.Request(raw, headers={"User-Agent": "lean-research-library/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                text = response.read().decode("utf-8", errors="replace")
            missing = [name for name in expected if f"{name}:" not in text]
            action_drift[repo_ref] = {"missing_inputs": missing} if missing else "ok"
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            action_drift[repo_ref] = f"unreachable: {exc}"
    drift: dict[str, Any] = {
        "latest_stable_mathlib": latest_stable or "unreachable",
        "endpoints": endpoints,
        "pinned_action_inputs": action_drift,
        "facts_baseline": "plan verified 2026-08-01; re-verify action inputs when bumping action SHAs",
    }
    if root is not None:
        pin = pinned_mathlib_rev(root)
        drift["pinned"] = pin
        drift["pin_behind_stable"] = bool(latest_stable) and pin not in ("unknown", latest_stable)
    return drift


# ---------------------------------------------------------------- search ----

def http_json(method: str, url: str, body: dict[str, Any] | None = None, timeout: int = 10) -> Any:
    request = urllib.request.Request(url, method=method)
    request.add_header("User-Agent", "lean-research-library/1.0")
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, data=data, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        return {"error": str(exc)}


def endpoint_alive(url: str) -> str:
    result = http_json("GET", url, timeout=6)
    return "unreachable" if isinstance(result, dict) and "error" in result else "ok"


def grep_tree(root: Path, module_prefix: str, query: str) -> list[dict[str, str]]:
    hits: list[dict[str, str]] = []
    needle = query.lower()
    for lean_file in sorted(root.rglob("*.lean")):
        if ".lake" in lean_file.parts:
            continue
        try:
            text = lean_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        lowered = text.lower()
        decl_hit = False
        for match in DECL_RE.finditer(text):
            name = match.group(1)
            if needle in name.lower() or needle in lowered[max(0, match.start() - 600):match.end() + 600]:
                decl_hit = True
                hits.append({
                    "name": name,
                    "file": str(lean_file.relative_to(root)),
                    "source": module_prefix,
                })
                if len(hits) >= 25:
                    return hits
        if not decl_hit and needle in lowered:
            # phrase lives elsewhere in the file (module docstring, comments):
            # surface every declaration of the file as a file-level match
            for match in DECL_RE.finditer(text):
                hits.append({
                    "name": match.group(1),
                    "file": str(lean_file.relative_to(root)),
                    "source": module_prefix,
                    "match": "file-level",
                })
                if len(hits) >= 25:
                    return hits
    return hits


def search_payload(cfg: dict[str, Any], query: str, *, offline: bool, with_leansearch: bool) -> dict[str, Any]:
    closed = bool(cfg.get("closed_deps", False))
    buckets: dict[str, list[dict[str, Any]]] = {
        "mathlib": [], "library": [], "peer_satellite": [], "elsewhere": [],
    }
    notes: list[str] = []

    root = library_root(cfg)
    if root is not None:
        module = library_module(cfg)
        buckets["library"] = grep_tree(root / module, module, query) + [
            hit for hit in decls_index_hits(root, query)
        ]
    else:
        notes.append("library not configured; run doctor for first-run guidance")

    if not closed:
        for peer in cfg.get("peer_satellites", []):
            peer_path = Path(str(peer)).expanduser()
            if peer_path.is_dir():
                buckets["peer_satellite"].extend(grep_tree(peer_path, peer_path.name, query)[:10])
    else:
        notes.append("closed-deps mode: peer-satellite tier disabled (core+mathlib+library only)")

    if not offline:
        loogle = http_json("GET", f"{LOOGLE_URL}?{urllib.parse.urlencode({'q': query})}")
        if isinstance(loogle, dict) and loogle.get("error") and loogle.get("suggestions"):
            # partial identifiers are rejected with suggestions; the quoted form
            # does substring search over declaration names — retry once with it
            loogle = http_json("GET", f"{LOOGLE_URL}?{urllib.parse.urlencode({'q': chr(34) + query + chr(34)})}")
        if isinstance(loogle, dict) and not loogle.get("error"):
            for hit in (loogle.get("hits") or [])[:15]:
                module_name = str(hit.get("module", ""))
                entry = {"name": hit.get("name"), "module": module_name, "type": str(hit.get("type", ""))[:200]}
                if module_name.startswith("Mathlib"):
                    buckets["mathlib"].append(entry)
                else:
                    entry["statement_only"] = any(module_name.startswith(p) for p in STATEMENT_ONLY_MODULES)
                    buckets["elsewhere"].append(entry)
        else:
            notes.append("loogle unreachable")
        state = http_json("GET", f"{STATESEARCH_URL}?{urllib.parse.urlencode({'query': query, 'results': 8})}")
        if isinstance(state, list):
            for hit in state[:8]:
                name = str(hit.get("name", "")) if isinstance(hit, dict) else ""
                if name:
                    buckets["mathlib"].append({"name": name, "module": str(hit.get("module", "")), "via": "leanstatesearch"})
        if with_leansearch:
            lean_search = http_json("POST", LEANSEARCH_URL, {"query": query, "num_results": 8})
            if isinstance(lean_search, list):
                for hit in lean_search[:8]:
                    if isinstance(hit, dict) and hit.get("formal_name"):
                        buckets["mathlib"].append({"name": hit["formal_name"], "via": "leansearch"})
            else:
                notes.append("leansearch unreachable (optional backend)")
    else:
        notes.append("offline search: network backends skipped")

    if buckets["mathlib"]:
        recommendation = "use-mathlib"
    elif buckets["library"]:
        recommendation = "use-library"
    elif buckets["peer_satellite"]:
        recommendation = "peer-satellite-hit: cite it; consider depending or re-proving"
    else:
        recommendation = "formalize-new"
    return {
        "schema_version": SCHEMA,
        "query": query,
        "buckets": buckets,
        "precedence_rule": "mathlib > library > peer-satellite > formalize new",
        "recommendation": recommendation,
        "closed_deps": closed,
        "notes": notes,
        "limitations": [
            "a FormalConjectures or statement_only hit is a sorry'd statement, never a reusable proof",
            "grep tiers match names and nearby text, not statement semantics",
        ],
    }


def decls_index_hits(root: Path, query: str) -> list[dict[str, Any]]:
    index = root / "decls-index.jsonl"
    hits: list[dict[str, Any]] = []
    if not index.is_file():
        return hits
    needle = query.lower()
    for line in index.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        haystack = " ".join(str(entry.get(k, "")) for k in ("name", "type", "doc")).lower()
        if needle in haystack:
            entry["source"] = "decls-index"
            hits.append(entry)
            if len(hits) >= 15:
                break
    return hits


# ---------------------------------------------------------------- status ----

def status_payload(cfg: dict[str, Any]) -> dict[str, Any]:
    root = library_root(cfg)
    if root is None:
        return {"schema_version": SCHEMA, "status": "not-configured", **first_run_guidance()}
    module = library_module(cfg)
    staging = root / module / "Mathlib"
    staged: list[dict[str, Any]] = []
    if staging.is_dir():
        for lean_file in sorted(staging.rglob("*.lean")):
            text = safe_read(lean_file)
            if text is None:
                staged.append({"file": str(lean_file.relative_to(root)), "sorry_free": False,
                               "import_discipline_ok": False, "bad_imports": ["<unreadable or non-UTF8>"]})
                continue
            imports = IMPORT_RE.findall(text)
            bad_imports = [i for i in imports if not (i.startswith("Mathlib.") or i.startswith(f"{module}.Mathlib."))]
            staged.append({
                "file": str(lean_file.relative_to(root)),
                "sorry_free": not re.search(r"\bsorry\b", text),
                "import_discipline_ok": not bad_imports,
                "bad_imports": bad_imports,
            })
    research_sorries = 0
    for lean_file in (root / module).rglob("*.lean"):
        if staging in lean_file.parents or ".lake" in lean_file.parts:
            continue
        research_text = safe_read(lean_file)
        if research_text is not None:
            research_sorries += len(re.findall(r"\bsorry\b", research_text))
    return {
        "schema_version": SCHEMA,
        "status": "ok",
        "library_root": str(root),
        "pinned_mathlib": pinned_mathlib_rev(root),
        "staged_files": staged,
        "ready_to_upstream": [f["file"] for f in staged if f["sorry_free"] and f["import_discipline_ok"]],
        "research_sorry_count": research_sorries,
    }


# ---------------------------------------------------------------- intake ----

def intake_payload(cfg: dict[str, Any], file: Path, task_id: str) -> dict[str, Any]:
    text = safe_read(file) if file.is_file() else None
    if text is None:
        return {"schema_version": SCHEMA, "status": "error", "error": f"missing or unreadable file: {file}"}
    decls = [m.group(1) for m in DECL_RE.finditer(text)]
    has_sorry = bool(re.search(r"\bsorry\b", text))
    candidates = []
    for name in decls:
        candidates.append({
            "declaration": name,
            "proposed_classification": "library-staging" if not has_sorry else "library-research",
            "next_check": f"search --query {name.split('.')[-1]} (mathlib-presence, MUST run before staging)",
        })
    return {
        "schema_version": SCHEMA,
        "status": "proposals-ready",
        "task_id": task_id,
        "source_file": str(file),
        "candidates": candidates,
        "user_gate": "REQUIRED: present these to the user and obtain approval before any stage call",
        "verification_gate_packet": {
            "gate": "lean-strict-verification-gate",
            "typecheck_status": "unknown-until-gate-runs",
            "sorry_present": has_sorry,
            "claim_support": "typechecking alone does not establish the paper claim",
        },
        "writes_performed": [],
    }


# ----------------------------------------------------------------- stage ----

MATHLIB_HEADER = """/-
Copyright (c) {year} Duc A. Hoang. All rights reserved.
Released under Apache 2.0 license as described in the file LICENSE.
Authors: Duc A. Hoang
-/
module

public import Mathlib.<your-dependencies-here>

/-!
# {title}

(Module docstring: state what this file provides and why; mathlib requires it.)
-/

@[expose] public section

"""


def stage_payload(cfg: dict[str, Any], file: Path, target: str, *, apply: bool) -> dict[str, Any]:
    root = library_root(cfg)
    if root is None:
        return {"schema_version": SCHEMA, "status": "not-configured", **first_run_guidance()}
    module = library_module(cfg)
    text = safe_read(file) if file.is_file() else None
    if text is None:
        return {"schema_version": SCHEMA, "status": "error", "error": f"missing or unreadable file: {file}"}
    # containment: normalize separators, refuse absolute targets and traversal
    target = target.replace("\\", "/")
    target_path = Path(target)
    if target_path.is_absolute() or ".." in target_path.parts or not target.endswith(".lean"):
        return {"schema_version": SCHEMA, "status": "error",
                "error": "target must be a relative .lean path inside the library module (no .. or absolute paths)"}
    is_staging = target.startswith("Mathlib/")
    destination = (root / module / target).resolve()
    module_root = (root / module).resolve()
    if not str(destination).startswith(str(module_root) + os.sep):
        return {"schema_version": SCHEMA, "status": "error", "error": "target escapes the library module"}
    if destination.exists():
        return {"schema_version": SCHEMA, "status": "error",
                "error": f"destination exists: {destination}; refusing silent overwrite"}
    problems: list[str] = []
    if is_staging:
        if re.search(r"\bsorry\b", text):
            problems.append("staging tree must be sorry-free")
        for imported in IMPORT_RE.findall(text):
            if not (imported.startswith("Mathlib.") or imported.startswith(f"{module}.Mathlib.")):
                problems.append(f"staged files may only import Mathlib.* or staged siblings: {imported}")
        if "module" not in text.split("\n\n")[0] and "/-" in text[:10]:
            problems.append("staged file should use the pinned mathlib file form (module + public import)")
    payload: dict[str, Any] = {
        "schema_version": SCHEMA,
        "status": "blocked" if problems else ("applied" if apply else "dry-run"),
        "destination": str(destination),
        "tree": "staging-mirror" if is_staging else "research",
        "problems": problems,
        "reminders": [
            "add the new module to the root import file",
            "commits are yours: git add/commit/push are never run by this skill",
        ],
    }
    if is_staging and not text.lstrip().startswith("/-"):
        payload["header_scaffold"] = MATHLIB_HEADER.format(year="2026", title=destination.stem)
    if apply and not problems:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")
        payload["writes_performed"] = [str(destination)]
    else:
        payload["writes_performed"] = []
    return payload


# ------------------------------------------------------- prepare-upstream ----

def prepare_upstream_payload(cfg: dict[str, Any], staged: Path) -> dict[str, Any]:
    root = library_root(cfg)
    if root is None or not staged.is_file():
        return {"schema_version": SCHEMA, "status": "error", "error": "library or staged file missing"}
    module = library_module(cfg)
    text = safe_read(staged)
    if text is None:
        return {"schema_version": SCHEMA, "status": "error", "error": f"unreadable staged file: {staged}"}
    ported = text.replace(f"import {module}.Mathlib.", "import Mathlib.")
    try:
        relative = staged.relative_to(root / module)
    except ValueError:
        return {"schema_version": SCHEMA, "status": "error", "error": "file is not inside the library module"}
    return {
        "schema_version": SCHEMA,
        "status": "port-prepared",
        "mathlib_target_path": str(relative),
        "ported_content": ported,
        "checklist": [
            "create a branch of your mathlib fork against CURRENT MASTER",
            f"place the ported content at {relative}",
            "adjust module/public-import header form to master's if it changed",
            "run scripts/fix_deprecations.py; lake build the touched module",
            "PR title: feat(Combinatorics/...): <imperative, lowercase, no trailing dot>",
            "disclose AI assistance in the PR description per the mathlib policy",
            "after merge + library bump: DELETE the staged copy (audit will flag it)",
        ],
        "writes_performed": [],
    }


# ------------------------------------------------------------ bump/audit ----

def bump_payload(cfg: dict[str, Any], to_tag: str, *, apply: bool) -> dict[str, Any]:
    root = library_root(cfg)
    if root is None:
        return {"schema_version": SCHEMA, "status": "not-configured", **first_run_guidance()}
    releases = http_json("GET", MATHLIB_RELEASES_URL)
    stable = [str(r.get("tag_name")) for r in releases if isinstance(r, dict)
              and "rc" not in str(r.get("tag_name", ""))] if isinstance(releases, list) else []
    pin = pinned_mathlib_rev(root)
    target = to_tag or (stable[0] if stable else "")
    payload: dict[str, Any] = {
        "schema_version": SCHEMA,
        "current_pin": pin,
        "target": target or "unreachable",
        "stable_ladder": stable[:6],
        "status": "up-to-date" if target == pin else ("applied" if apply else "dry-run"),
        "checklist": [
            f"lean-toolchain := leanprover/lean4:{target}" if target else "",
            f'lakefile.toml rev := "{target}"' if target else "",
            "lake update mathlib   # post-update hook fetches the cache",
            "lake build            # deprecation warnings only surface at build",
            "commit lake-manifest.json TOGETHER with the pin files",
            "then: audit --check-landed (delete staged copies that landed)",
        ],
        "writes_performed": [],
    }
    if apply and target and target != pin:
        toolchain = root / "lean-toolchain"
        lakefile = root / "lakefile.toml"
        toolchain.write_text(f"leanprover/lean4:{target}\n", encoding="utf-8")
        lakefile.write_text(
            re.sub(r'^rev\s*=\s*".*"$', f'rev = "{target}"', lakefile.read_text(encoding="utf-8"),
                   count=1, flags=re.MULTILINE),
            encoding="utf-8",
        )
        payload["writes_performed"] = [str(toolchain), str(lakefile)]
        payload["next_steps"] = ["run the lake commands from the checklist yourself (they need the toolchain)"]
    return payload


def run_landed_gate(root: Path, module: str) -> dict[str, Any]:
    """Best-effort execution of the deterministic landed gate; falls back to commands."""
    lake = shutil.which("lake") or str(Path.home() / ".elan" / "bin" / ("lake.exe" if os.name == "nt" else "lake"))
    if not Path(lake).is_file() and not shutil.which("lake"):
        return {"executed": False, "reason": "lake unavailable; run the commands manually"}
    if not (root / "scripts" / "check_landed.lean").is_file():
        return {"executed": False, "reason": "scripts/check_landed.lean missing in the library"}
    try:
        listed = subprocess.run(
            [lake, "env", "lean", "--run", "scripts/list_decls.lean", f"{module}.Mathlib"],
            cwd=root, capture_output=True, text=True, timeout=600, check=False,
        )
        if listed.returncode != 0:
            return {"executed": False, "reason": f"list_decls failed (library built?): {listed.stderr[-400:]}"}
        (root / "staged_decls.txt").write_text(listed.stdout, encoding="utf-8")
        gate = subprocess.run(
            [lake, "env", "lean", "scripts/check_landed.lean"],
            cwd=root, capture_output=True, text=True, timeout=1800, check=False,
        )
        return {
            "executed": True,
            "landed_or_collision": gate.returncode != 0,
            "output": (gate.stdout + gate.stderr)[-1500:],
        }
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {"executed": False, "reason": str(exc)}


def audit_payload(cfg: dict[str, Any], *, run_gate: bool = False) -> dict[str, Any]:
    root = library_root(cfg)
    if root is None:
        return {"schema_version": SCHEMA, "status": "not-configured", **first_run_guidance()}
    module = library_module(cfg)
    status = status_payload(cfg)
    payload: dict[str, Any] = {
        "schema_version": SCHEMA,
        "status": "ok",
        "staging_summary": {
            "ready_to_upstream": status.get("ready_to_upstream", []),
            "violations": [f for f in status.get("staged_files", [])
                           if not (f["sorry_free"] and f["import_discipline_ok"])],
        },
        "landed_gate_commands": [
            f"cd {root}",
            "lake build",
            f"lake env lean --run scripts/list_decls.lean {module}.Mathlib > staged_decls.txt",
            "lake env lean scripts/check_landed.lean   # nonzero exit = landed/collision, delete or rename",
        ],
        "landed_gate_note": "deterministic gate needs the Lean toolchain + mathlib cache; commands above run it",
        "allowed_axioms": list(ALLOWED_AXIOMS),
    }
    if run_gate:
        payload["landed_gate"] = run_landed_gate(root, module)
    return payload


# --------------------------------------------------------------- artifact ----

EMBEDDED_ARTIFACT_FILES: dict[str, str] = {
    "lakefile.toml": (
        'name = "artifact"\ndefaultTargets = ["Artifact"]\n\n[[require]]\nname = "mathlib"\n'
        'git = "https://github.com/leanprover-community/mathlib4.git"\nrev = "v4.32.2"\n\n'
        '[[lean_lib]]\nname = "Artifact"\n'
    ),
    "lean-toolchain": "leanprover/lean4:v4.32.2\n",
    "Artifact.lean": "import Artifact.Results\n",
    "Artifact/Results.lean": (
        "/- Statement index: one declaration per paper theorem. -/\n"
        "import Mathlib.Logic.Basic\n\ntheorem paper_thm_placeholder : True := trivial\n"
    ),
    "decls.txt": "paper_thm_placeholder\n",
    ".gitignore": "/.lake\nartifact-bundle.tar.gz\n",
}


def artifact_new_payload(cfg: dict[str, Any], paper: str, directory: Path, library_rev: str) -> dict[str, Any]:
    template_root = Path(str(cfg.get("template_root", ""))).expanduser()
    if directory.exists() and any(directory.iterdir()):
        return {"schema_version": SCHEMA, "status": "error", "error": f"{directory} exists and is not empty"}
    used = "template_root"
    if template_root.is_dir():
        shutil.copytree(template_root, directory, ignore=shutil.ignore_patterns(".git", ".lake"))
    else:
        used = "embedded-minimal"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "Artifact").mkdir(exist_ok=True)
        for name, content in EMBEDDED_ARTIFACT_FILES.items():
            (directory / name).write_text(content, encoding="utf-8")
    proposals = [
        f"gh repo create <owner>/paper-{paper}-lean --public --source {directory} --push",
        f"per-paper library tag proposal: git -C <library> tag paper-{paper}-v1 && git push origin paper-{paper}-v1",
    ]
    if library_rev:
        proposals.insert(0, f"pin hoangmathlib rev = \"{library_rev}\" in {directory}/lakefile.toml")
    return {
        "schema_version": SCHEMA,
        "status": "scaffolded",
        "source": used,
        "directory": str(directory),
        "note": "" if used == "template_root" else (
            "embedded-minimal scaffold builds with lake but lacks the CI verification ladder,"
            " README, and metadata files; clone"
            " https://github.com/hoanganhduc/lean-paper-artifact-template and set template_root"
            " in the config for the full verification ladder"
        ),
        "user_gate": "REQUIRED: repo creation and pushes are proposals; the user runs or approves them",
        "proposed_commands": proposals,
        "writes_performed": [str(directory)],
    }


def zenodo_precheck(directory: Path) -> list[str]:
    problems: list[str] = []
    zenodo_json = directory / ".zenodo.json"
    if not zenodo_json.is_file():
        problems.append(".zenodo.json missing")
    else:
        raw = zenodo_json.read_text(encoding="utf-8")
        try:
            json.loads(raw)
        except json.JSONDecodeError as exc:
            problems.append(f".zenodo.json invalid JSON: {exc}")
        for placeholder in re.findall(r"<[A-Z][A-Z-]+>", raw):
            problems.append(f"placeholder left in .zenodo.json: {placeholder}")
    cff = directory / "CITATION.cff"
    if cff.is_file():
        for placeholder in set(re.findall(r"<[A-Z][A-Z-]+>", cff.read_text(encoding="utf-8"))):
            problems.append(f"placeholder left in CITATION.cff: {placeholder}")
    return problems


def artifact_publish_payload(directory: Path, *, mode: str, production: bool,
                             confirm_production: bool, dry_run: bool) -> dict[str, Any]:
    problems = zenodo_precheck(directory)
    base: dict[str, Any] = {
        "schema_version": SCHEMA,
        "mode": mode,
        "zenodo_environment": "production" if production else "sandbox",
        "prechecks": problems or ["ok"],
    }
    if problems:
        return {**base, "status": "blocked", "reason": "fix prechecks before publishing"}
    if mode == "github-sync":
        return {
            **base,
            "status": "checklist",
            "steps": [
                "zenodo.org (or sandbox.zenodo.org for the dry-run): link GitHub, Sync now, toggle this repo ON",
                "git tag v1.0.0 && git push origin v1.0.0",
                "create a GitHub RELEASE from the tag (Zenodo ingests Releases, not tags)",
                "Zenodo mints version DOI + concept DOI; cite version DOI + repo URL + tag + commit SHA",
                "the DOI record archives the SOURCE SNAPSHOT ONLY; upload the bundle to the deposit manually",
            ],
        }
    # api mode
    if production and not confirm_production:
        return {
            **base,
            "status": "refused",
            "reason": "production publish mints an UNDELETABLE DOI;"
                      " pass --confirm-production after explicit user approval",
        }
    token = os.environ.get("ZENODO_TOKEN", "").strip()
    if not token:
        return {**base, "status": "blocked",
                "reason": "set ZENODO_TOKEN (personal access token; sandbox and production use different tokens)"}
    api = ZENODO_PRODUCTION_API if production else ZENODO_SANDBOX_API
    if dry_run:
        return {**base, "status": "dry-run",
                "would_do": [f"POST {api}/deposit/depositions (create draft)",
                             "PUT metadata from .zenodo.json",
                             "upload source archive (+ bundle when present)",
                             "publish only on explicit approval"]}
    created = http_json("POST", f"{api}/deposit/depositions?access_token={urllib.parse.quote(token)}", {})
    if not isinstance(created, dict) or "error" in created or "id" not in created:
        return {**base, "status": "error", "error": str(created)[:400]}
    return {
        **base,
        "status": "draft-created",
        "deposition_id": created.get("id"),
        "draft_url": str(created.get("links", {}).get("html", "")),
        "next": "review the draft in the browser; metadata/file upload and PUBLISH remain manual/gated",
    }


# ------------------------------------------------------------------ main ----

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lean-research-library")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor")
    doctor.add_argument("--ecosystem", action="store_true", help="network drift checks")

    search = sub.add_parser("search")
    search.add_argument("--query", required=True)
    search.add_argument("--offline", action="store_true")
    search.add_argument("--with-leansearch", action="store_true")

    sub.add_parser("status")

    intake = sub.add_parser("intake")
    intake.add_argument("--file", required=True, type=Path)
    intake.add_argument("--task-id", default="")

    stage = sub.add_parser("stage")
    stage.add_argument("--file", required=True, type=Path)
    stage.add_argument("--target", required=True, help="Mathlib/A/B/C.lean or Reconfig/X.lean")
    stage.add_argument("--apply", action="store_true")

    prepare = sub.add_parser("prepare-upstream")
    prepare.add_argument("--file", required=True, type=Path)

    bump = sub.add_parser("bump")
    bump.add_argument("--to", default="")
    bump.add_argument("--apply", action="store_true")

    audit = sub.add_parser("audit")
    audit.add_argument("--run-gate", action="store_true",
                       help="execute the landed gate (needs lake + built library); default prints commands")

    artifact = sub.add_parser("artifact")
    artifact_sub = artifact.add_subparsers(dest="artifact_command", required=True)
    art_new = artifact_sub.add_parser("new")
    art_new.add_argument("--paper", required=True)
    art_new.add_argument("--dir", required=True, type=Path)
    art_new.add_argument("--library-rev", default="")
    art_pub = artifact_sub.add_parser("publish")
    art_pub.add_argument("--dir", required=True, type=Path)
    art_pub.add_argument("--mode", choices=("github-sync", "api"), default="github-sync")
    art_pub.add_argument("--production", action="store_true")
    art_pub.add_argument("--confirm-production", action="store_true")
    art_pub.add_argument("--dry-run", action="store_true")

    args = parser.parse_args(argv)
    cfg = load_config()

    if args.command == "doctor":
        emit(doctor_payload(cfg, ecosystem=args.ecosystem))
        return 0  # doctor reports states, never fails (smoke contract)
    if args.command == "search":
        return emit(search_payload(cfg, args.query, offline=args.offline, with_leansearch=args.with_leansearch))
    if args.command == "status":
        return emit(status_payload(cfg))
    if args.command == "intake":
        return emit(intake_payload(cfg, args.file, args.task_id))
    if args.command == "stage":
        return emit(stage_payload(cfg, args.file, args.target, apply=args.apply))
    if args.command == "prepare-upstream":
        return emit(prepare_upstream_payload(cfg, args.file))
    if args.command == "bump":
        return emit(bump_payload(cfg, args.to, apply=args.apply))
    if args.command == "audit":
        return emit(audit_payload(cfg, run_gate=args.run_gate))
    if args.command == "artifact" and args.artifact_command == "new":
        return emit(artifact_new_payload(cfg, args.paper, args.dir, args.library_rev))
    if args.command == "artifact" and args.artifact_command == "publish":
        return emit(artifact_publish_payload(args.dir, mode=args.mode, production=args.production,
                                             confirm_production=args.confirm_production, dry_run=args.dry_run))
    raise AssertionError(args.command)


def emit(payload: dict[str, Any]) -> int:
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 1 if payload.get("status") in {"error", "blocked", "refused"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
