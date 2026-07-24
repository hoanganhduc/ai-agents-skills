#!/usr/bin/env python3
"""Parent-owned multi-agent panel dispatcher for ARL (hybrid model).

Architecture
------------
The ARL **driver** (or an interactive top parent) owns multi-agent
target-advice and result-review. The drive **primary** does single-path
work only and must not nest panel CLIs under its sandbox.

This module runs **outside** the primary agent process: correct CLI argv, real
auth homes, parallel dispatch, adaptive timeouts, and standard panel artifacts.

See autonomous-research-loop skill docs: hybrid parent-owned panel model.
"""

from __future__ import annotations

import concurrent.futures
import json
import math
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

DEFAULT_PROVIDERS = ("claude", "codex", "codewhale", "kimi")

DEFAULT_TIMEOUT_S = {
    "target_advice": 600,
    "result_review": 900,
    "smoke": 120,
}

# Adaptive timeout defaults (see compute_provider_timeouts).
DEFAULT_PROVIDER_MULT: dict[str, float] = {
    "kimi": 1.5,
    "claude": 1.15,
    "codex": 1.1,
    "codewhale": 1.0,
}
DEFAULT_TIMEOUT_CALC: dict[str, Any] = {
    "min_s": 120,
    "max_s": 2400,
    "max_s_smoke": 180,
    "size_free": 4000,
    "size_chars_per_second": 80,
    "hist_margin": 1.25,
    "history_n": 5,
}

MIN_USABLE_CHARS = 8

# Optional injectable runner for unit tests: (cmd, env, cwd, timeout_s) -> (rc, stdout, stderr)
Runner = Callable[[list[str], dict[str, str], str, int], tuple[int, str, str]]


def which(name: str) -> str | None:
    return shutil.which(name)


def prepare_writable_home_overlay(name: str, real_home: Path, work: Path) -> Path:
    """If real home is writable, use it. Else clone config into work overlay."""
    if real_home.is_dir() and os.access(real_home, os.W_OK):
        return real_home
    overlay = work / f"home_{name}"
    overlay.mkdir(parents=True, exist_ok=True)
    if real_home.is_dir():
        for item in ("config.toml", "auth.json", "credentials", "settings.toml", "secrets"):
            src = real_home / item
            dst = overlay / item
            if not src.exists() or dst.exists():
                continue
            try:
                if src.is_dir():
                    shutil.copytree(src, dst, dirs_exist_ok=True)
                else:
                    shutil.copy2(src, dst)
            except OSError:
                pass
    return overlay


def build_cmd(
    provider: str, prompt: str, root: Path, work: Path
) -> tuple[list[str], dict[str, str]]:
    env = os.environ.copy()
    extra_path = [
        str(Path.home() / ".local/bin"),
        str(Path.home() / ".npm-global/bin"),
        str(Path.home() / ".kimi-code/bin"),
    ]
    env["PATH"] = ":".join(extra_path + [env.get("PATH", "")])
    env.setdefault("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    env["HOME"] = str(Path.home())

    if provider == "claude":
        bin_ = which("claude") or "claude"
        return [bin_, "-p", prompt, "--output-format", "text"], env

    if provider == "codex":
        bin_ = which("codex") or "codex"
        cmd = [
            bin_,
            "exec",
            "--ephemeral",
            "--skip-git-repo-check",
            "--dangerously-bypass-approvals-and-sandbox",
            "-C",
            str(root),
            prompt,
        ]
        return cmd, env

    if provider == "codewhale":
        bin_ = which("codewhale") or "codewhale"
        return [bin_, "exec", prompt], env

    if provider == "kimi":
        bin_ = which("kimi") or "kimi"
        real = Path.home() / ".kimi-code"
        kimi_home = prepare_writable_home_overlay("kimi", real, work)
        env["KIMI_CODE_HOME"] = str(kimi_home)
        return [bin_, "-p", prompt], env

    raise ValueError(f"unknown provider {provider}")


def classify_error(stderr: str, exit_code: int) -> str:
    s = (stderr or "").lower()
    if exit_code == 124 or "timeout" in s:
        return "timeout"
    if "read-only file system" in s or "erofs" in s or "os error 30" in s:
        return "read_only_filesystem"
    if "quota" in s or "credit" in s or "rate limit" in s or "429" in s:
        return "quota_or_credit"
    if "enotimp" in s:
        return "network_enotimp"
    if "operation not permitted" in s or "eperm" in s:
        return "network_or_perm_denied"
    if "connection" in s or "network error" in s or "provider.connection" in s:
        return "network_connection_failure"
    if "cannot combine --prompt with --yolo" in s or "kimi_flag" in s:
        return "kimi_flag_conflict"
    if exit_code != 0:
        return "nonzero_exit"
    return "empty_or_short_stdout"


def usable_stdout(stdout: str) -> bool:
    text = (stdout or "").strip()
    if not text:
        return False
    noise_substrings = (
        "to resume this session:",
        "tokens used",
        "openai codex v",
        "hook: sessionstart",
        "reading additional input from stdin",
        "debug deepseek_base_url",
    )
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    substantive: list[str] = []
    for ln in lines:
        low = ln.lower()
        if any(n in low for n in noise_substrings):
            continue
        substantive.append(ln)
    body = "\n".join(substantive).strip().lstrip("•-* \t")
    return len(body) >= MIN_USABLE_CHARS


def _default_runner(
    cmd: list[str], env: dict[str, str], cwd: str, timeout_s: int
) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env=env,
            cwd=cwd,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except subprocess.TimeoutExpired as exc:
        stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else (
            exc.stdout.decode("utf-8", "replace") if exc.stdout else ""
        )
        stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else (
            exc.stderr.decode("utf-8", "replace") if exc.stderr else ""
        )
        return 124, stdout, (stderr + "\n[panel_parent] hard timeout\n").strip()
    except FileNotFoundError as exc:
        return 127, "", f"binary not found: {exc}"
    except OSError as exc:
        return 1, "", f"os error: {exc}"


def run_one(
    provider: str,
    prompt: str,
    root: Path,
    raw_dir: Path,
    phase: str,
    timeout_s: int,
    *,
    runner: Runner | None = None,
) -> dict[str, Any]:
    work = raw_dir / f"_work_{provider}_{phase}"
    work.mkdir(parents=True, exist_ok=True)
    cmd, env = build_cmd(provider, prompt, root, work)
    t0 = time.time()
    stdout_path = raw_dir / f"{provider}_{phase}_stdout.txt"
    stderr_path = raw_dir / f"{provider}_{phase}_stderr.txt"
    exit_path = raw_dir / f"{provider}_{phase}_exit_code"
    run = runner or _default_runner
    rc, stdout, stderr = run(cmd, env, str(root), timeout_s)
    stdout_path.write_text(stdout, encoding="utf-8", errors="replace")
    stderr_path.write_text(stderr, encoding="utf-8", errors="replace")
    exit_path.write_text(str(rc) + "\n", encoding="utf-8")
    ok = rc == 0 and usable_stdout(stdout)
    err_class = None if ok else classify_error(stderr, rc)
    return {
        "provider": provider,
        "phase": phase,
        "cmd_bin": cmd[0],
        "timeout_s": timeout_s,
        "started_unix": t0,
        "exit_code": rc,
        "elapsed_s": round(time.time() - t0, 2),
        "stdout_chars": len(stdout),
        "stderr_chars": len(stderr),
        "usable": ok,
        "status": "ok" if ok else "unavailable",
        "error_class": err_class,
        "credit_or_quota_error": (not ok) and err_class == "quota_or_credit",
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
    }


def phase_dirs(iter_dir: Path, phase: str) -> tuple[Path, Path]:
    panel = iter_dir / "panel"
    raw = iter_dir / "raw"
    panel.mkdir(parents=True, exist_ok=True)
    raw.mkdir(parents=True, exist_ok=True)
    if phase == "target_advice":
        out = panel / "01_target_advice"
    elif phase == "result_review":
        out = panel / "03_result_review"
    else:
        out = panel / phase
    out.mkdir(parents=True, exist_ok=True)
    return out, raw


def _timeout_calc_constants(cfg: dict[str, Any] | None) -> dict[str, Any]:
    out = dict(DEFAULT_TIMEOUT_CALC)
    if not cfg:
        return out
    raw = cfg.get("timeout_calc")
    if isinstance(raw, dict):
        for key, value in raw.items():
            if value is not None:
                out[key] = value
    return out


def _provider_mult(provider: str, cfg: dict[str, Any] | None) -> float:
    defaults = dict(DEFAULT_PROVIDER_MULT)
    if cfg and isinstance(cfg.get("timeouts_by_provider"), dict):
        entry = cfg["timeouts_by_provider"].get(provider)
        if isinstance(entry, (int, float)) and not isinstance(entry, bool):
            return float(entry)
        if isinstance(entry, dict):
            mult = entry.get("mult", entry.get("multiplier"))
            if isinstance(mult, (int, float)) and not isinstance(mult, bool):
                return float(mult)
    return float(defaults.get(provider, 1.0))


def _history_elapsed(
    run_dir: Path | None,
    provider: str,
    phase: str,
    history_n: int,
) -> float:
    """Max successful elapsed_s for provider+phase from recent dispatch summaries."""
    if run_dir is None or not Path(run_dir).is_dir() or history_n < 1:
        return 0.0
    root = Path(run_dir)
    candidates: list[Path] = []
    # Prefer iteration data panel_dispatch_*.json under this loop
    for path in sorted(root.glob("iterations/**/data/panel_dispatch_*.json")):
        candidates.append(path)
    for path in sorted(root.glob("iterations/**/panel/**/dispatch_summary.json")):
        candidates.append(path)
    # Also accept summaries placed directly under run_dir (tests / ad-hoc)
    for path in sorted(root.glob("**/panel_dispatch_*.json")):
        if path not in candidates:
            candidates.append(path)
    # Newest last; walk reverse
    best = 0.0
    seen = 0
    for path in reversed(candidates):
        if seen >= history_n:
            break
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        if data.get("phase") and data.get("phase") != phase and phase != "smoke":
            # allow unmatched only when file name encodes phase
            if phase not in path.name:
                continue
        results = data.get("results") or {}
        meta = results.get(provider) if isinstance(results, dict) else None
        if not isinstance(meta, dict):
            continue
        seen += 1
        if not meta.get("usable"):
            continue
        try:
            elapsed = float(meta.get("elapsed_s") or 0)
        except (TypeError, ValueError):
            elapsed = 0.0
        if elapsed > best:
            best = elapsed
    return best


def compute_provider_timeouts(
    phase: str,
    prompt: str,
    providers: list[str],
    cfg: dict[str, Any] | None = None,
    *,
    run_dir: Path | None = None,
    explicit_timeout_s: int | None = None,
) -> dict[str, dict[str, Any]]:
    """Per-provider timeout budgets (adaptive or fixed).

    Returns map provider -> {timeout_s, timeout_mode, timeout_inputs}.
    """
    cfg = cfg or {}
    mode = str(cfg.get("timeout_mode") or "adaptive").strip().lower()
    if mode not in {"adaptive", "fixed"}:
        mode = "adaptive"
    timeouts = cfg.get("timeouts") if isinstance(cfg.get("timeouts"), dict) else {}
    base = int(timeouts.get(phase, DEFAULT_TIMEOUT_S.get(phase, 600)))
    if explicit_timeout_s is not None and int(explicit_timeout_s) > 0:
        if mode == "fixed":
            base = int(explicit_timeout_s)
        else:
            base = max(base, int(explicit_timeout_s))
    calc = _timeout_calc_constants(cfg)
    try:
        min_s = int(calc.get("min_s", 120))
    except (TypeError, ValueError):
        min_s = 120
    try:
        max_s = int(calc.get("max_s", 2400))
    except (TypeError, ValueError):
        max_s = 2400
    if phase == "smoke":
        try:
            max_s = min(max_s, int(calc.get("max_s_smoke", 180)))
        except (TypeError, ValueError):
            max_s = min(max_s, 180)
    prompt_chars = len(prompt or "")
    try:
        size_free = int(calc.get("size_free", 4000))
    except (TypeError, ValueError):
        size_free = 4000
    try:
        cps = float(calc.get("size_chars_per_second", 80)) or 80.0
    except (TypeError, ValueError):
        cps = 80.0
    size_extra = int(math.ceil(max(0, prompt_chars - size_free) / cps))
    try:
        hist_margin = float(calc.get("hist_margin", 1.25)) or 1.25
    except (TypeError, ValueError):
        hist_margin = 1.25
    try:
        history_n = int(calc.get("history_n", 5))
    except (TypeError, ValueError):
        history_n = 5

    out: dict[str, dict[str, Any]] = {}
    for provider in providers:
        if mode == "fixed":
            t = max(min_s, min(max_s, base))
            out[provider] = {
                "timeout_s": t,
                "timeout_mode": "fixed",
                "timeout_inputs": {
                    "base": base,
                    "size_extra": 0,
                    "provider_mult": 1.0,
                    "hist_pad": 0,
                    "prompt_chars": prompt_chars,
                    "clamped": t != base,
                    "min_s": min_s,
                    "max_s": max_s,
                },
            }
            continue
        mult = _provider_mult(provider, cfg)
        hist = _history_elapsed(run_dir, provider, phase, history_n)
        hist_pad = int(math.ceil(hist * hist_margin)) if hist > 0 else 0
        raw = max(base + size_extra, hist_pad) * mult
        t = int(round(raw))
        clamped = max(min_s, min(max_s, t))
        out[provider] = {
            "timeout_s": clamped,
            "timeout_mode": "adaptive",
            "timeout_inputs": {
                "base": base,
                "size_extra": size_extra,
                "provider_mult": mult,
                "hist_pad": hist_pad,
                "hist_elapsed": hist,
                "prompt_chars": prompt_chars,
                "raw": raw,
                "clamped": clamped != t,
                "min_s": min_s,
                "max_s": max_s,
            },
        }
    return out


def dispatch_phase(
    iter_dir: Path,
    phase: str,
    prompt: str,
    providers: list[str],
    timeout_s: int,
    root: Path,
    *,
    runner: Runner | None = None,
    panel_cfg: dict[str, Any] | None = None,
    run_dir: Path | None = None,
) -> dict[str, Any]:
    out_dir, raw_dir = phase_dirs(iter_dir, phase)
    (out_dir / "prompt.md").write_text(
        prompt if prompt.endswith("\n") else prompt + "\n", encoding="utf-8"
    )

    cfg = panel_cfg if panel_cfg is not None else {}
    history_root = run_dir
    if history_root is None:
        # iter_dir is often <loop>/iterations/iterNNN
        try:
            if iter_dir.parent.name == "iterations":
                history_root = iter_dir.parent.parent
        except Exception:  # noqa: BLE001
            history_root = None
    budgets = compute_provider_timeouts(
        phase,
        prompt,
        list(providers),
        cfg,
        run_dir=history_root,
        explicit_timeout_s=timeout_s if timeout_s and timeout_s > 0 else None,
    )

    results: dict[str, Any] = {}
    workers = max(1, len(providers))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {
            pool.submit(
                run_one,
                p,
                prompt,
                root,
                raw_dir,
                phase,
                int(budgets[p]["timeout_s"]),
                runner=runner,
            ): p
            for p in providers
        }
        for fut in concurrent.futures.as_completed(futs):
            p = futs[fut]
            try:
                results[p] = fut.result()
            except Exception as exc:  # noqa: BLE001
                results[p] = {
                    "provider": p,
                    "status": "unavailable",
                    "usable": False,
                    "error_class": f"dispatcher_exception:{type(exc).__name__}",
                    "exit_code": 1,
                    "credit_or_quota_error": False,
                    "stderr": str(exc),
                }
            # Attach timeout telemetry even on exception path
            meta = budgets.get(p) or {}
            if isinstance(results.get(p), dict):
                results[p]["timeout_s"] = meta.get("timeout_s", results[p].get("timeout_s"))
                results[p]["timeout_mode"] = meta.get("timeout_mode")
                results[p]["timeout_inputs"] = meta.get("timeout_inputs")

    for p, meta in results.items():
        md_path = out_dir / f"{p}.md"
        if meta.get("usable") and meta.get("stdout_path"):
            body = Path(meta["stdout_path"]).read_text(encoding="utf-8", errors="replace")
            md_path.write_text(
                f"# {p} — {phase}\n\nStatus: ok\n\n{body.strip()}\n",
                encoding="utf-8",
            )
        else:
            md_path.write_text(
                f"# {p} — {phase}\n\n"
                f"Status: unavailable (`{meta.get('error_class')}`).\n\n"
                f"exit_code: {meta.get('exit_code')}\n"
                f"stderr: see `raw/{p}_{phase}_stderr.txt`\n",
                encoding="utf-8",
            )

    usable = [p for p, m in results.items() if m.get("usable")]
    primary_family = os.environ.get("AAS_AUTOLOOP_PRIMARY_PROVIDER", "codex")
    different_family = any(p in usable for p in usable if p != primary_family)
    summary = {
        "schema_version": "panel_parent.v1",
        "phase": phase,
        "iter_dir": str(iter_dir),
        "providers_invited": providers,
        "usable_providers": usable,
        "panel_content_pass": len(usable) >= 1,
        "all_invited_usable": set(usable) >= set(providers),
        "different_family_logic_available": different_family
        or any(p in usable for p in ("claude", "kimi", "codewhale")),
        "timeout_mode": (next(iter(budgets.values()), {}) or {}).get("timeout_mode"),
        "provider_timeouts": {p: budgets[p]["timeout_s"] for p in providers if p in budgets},
        "results": results,
        "generated_unix": time.time(),
    }
    (out_dir / "dispatch_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    data = iter_dir / "data"
    data.mkdir(parents=True, exist_ok=True)
    (data / f"panel_dispatch_{phase}.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def _normalize_name_list(raw: object) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [p.strip().lower() for p in raw.split(",") if p.strip()]
    if isinstance(raw, (list, tuple)):
        out: list[str] = []
        for item in raw:
            s = str(item).strip().lower()
            if s:
                out.append(s)
        return out
    return []


def filter_panel_providers(cfg: dict[str, Any]) -> list[str]:
    """Return invite list after exclude_until_credit / exclude_providers.

    Env AAS_AUTOLOOP_PANEL_PROVIDERS already overrides providers before this
    runs (see load_panel_config). Exclusions still apply unless the env list
    was the only source and the operator intentionally re-listed someone.
    """
    providers = [str(p).strip() for p in (cfg.get("providers") or DEFAULT_PROVIDERS) if str(p).strip()]
    excluded = set(_normalize_name_list(cfg.get("exclude_until_credit")))
    excluded |= set(_normalize_name_list(cfg.get("exclude_providers")))
    if not excluded:
        return providers
    return [p for p in providers if p.strip().lower() not in excluded]


def load_panel_config(run_dir: Path) -> dict[str, Any]:
    """Load panel config from panel.json and/or loop_state standing_orders.panel."""
    cfg: dict[str, Any] = {
        "enabled": False,
        "providers": list(DEFAULT_PROVIDERS),
        "exclude_until_credit": [],
        "timeouts": dict(DEFAULT_TIMEOUT_S),
        "timeout_mode": "adaptive",
        "require_different_family": True,
        "anti_deadlock_math_without_panel": True,
    }
    panel_json = run_dir / "panel.json"
    if panel_json.is_file():
        try:
            data = json.loads(panel_json.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                cfg.update({k: v for k, v in data.items() if v is not None})
        except (OSError, json.JSONDecodeError):
            pass
    state_path = run_dir / "loop_state.json"
    if state_path.is_file():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            so = state.get("standing_orders") if isinstance(state, dict) else None
            panel = so.get("panel") if isinstance(so, dict) else None
            if isinstance(panel, dict):
                cfg.update({k: v for k, v in panel.items() if v is not None})
            elif so and so.get("multi_agent_panel"):
                # clawfree legacy key
                cfg["enabled"] = True
        except (OSError, json.JSONDecodeError):
            pass
    env_flag = os.environ.get("AAS_AUTOLOOP_PANEL", "").strip().lower()
    if env_flag in ("1", "on", "true", "yes"):
        cfg["enabled"] = True
    elif env_flag in ("0", "off", "false", "no"):
        cfg["enabled"] = False
    env_prov = os.environ.get("AAS_AUTOLOOP_PANEL_PROVIDERS", "").strip()
    if env_prov:
        cfg["providers"] = [p.strip() for p in env_prov.split(",") if p.strip()]
    # Normalize invite list after merges so dispatch never sees excluded names.
    cfg["providers"] = filter_panel_providers(cfg)
    cfg["exclude_until_credit"] = _normalize_name_list(cfg.get("exclude_until_credit"))
    return cfg


def resolve_panel_mode(explicit: str | None, run_dir: Path) -> bool:
    """Return True if host panel phases should run.

    explicit: on|off|auto|None  (None treated as auto)
    """
    mode = (explicit or "auto").strip().lower()
    if mode == "on":
        return True
    if mode == "off":
        return False
    # auto
    return bool(load_panel_config(run_dir).get("enabled"))


def next_iteration_number(run_dir: Path) -> int:
    state_path = run_dir / "loop_state.json"
    last = 0
    if state_path.is_file():
        try:
            last = int(json.loads(state_path.read_text(encoding="utf-8")).get("last_iteration") or 0)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            last = 0
    return last + 1


def ensure_iter_dir(run_dir: Path, iteration: int | None = None) -> Path:
    n = iteration if iteration is not None else next_iteration_number(run_dir)
    path = run_dir / "iterations" / f"iter{n:03d}"
    # also accept unpadded if already used
    alt = run_dir / "iterations" / f"iter{n}"
    if alt.is_dir() and not path.is_dir():
        return alt
    path.mkdir(parents=True, exist_ok=True)
    return path


def build_target_brief(run_dir: Path, *, max_chars: int = 12000) -> str:
    """Compact brief: goal/replan, then next path, then truncated recovery."""
    parts: list[str] = [
        "# Panel target-advice brief (host parent)",
        "",
        "You are a panelist. Advise on the **single next path** only.",
        "Do not claim results are banked. Do not start formal-lane work unless recovery requires it.",
        "Label encoding-scoped vs manuscript claims carefully.",
        "",
    ]
    # (1) Goal / replan block first so truncation cannot drop it
    try:
        from goal_priority import goal_priority_brief_block  # type: ignore

        gp_block = goal_priority_brief_block(run_dir)
        if gp_block.strip():
            parts.append(gp_block.rstrip())
            parts.append("")
    except Exception:  # noqa: BLE001 — panel works without goal_priority module
        pass

    # (2) next_preferred_path / committed path
    state_path = run_dir / "loop_state.json"
    if state_path.is_file():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            npp = state.get("next_preferred_path") or ""
            parts.append("## next_preferred_path")
            parts.append("")
            parts.append(str(npp)[:2000] if npp else "(unset)")
            parts.append("")
            parts.append(f"last_iteration: {state.get('last_iteration')}")
            parts.append("")
        except (OSError, json.JSONDecodeError):
            pass

    # (3) recovery excerpt (may be truncated by max_chars)
    recovery = run_dir / "recovery.md"
    if recovery.is_file():
        text = recovery.read_text(encoding="utf-8", errors="replace")
        parts.append("## recovery.md (excerpt)")
        parts.append("")
        parts.append(text[:8000])
        parts.append("")
    parts.append("## Required output")
    parts.append("")
    parts.append(
        "1) Rank 1–3 next targets under single-path policy.\n"
        "2) Prefer the committed next path unless you have a host-verifiable blocker.\n"
        "3) Name what would falsify the preferred target.\n"
        "4) Flag encoding vs manuscript scope.\n"
        "Keep the reply under ~1500 words."
    )
    brief = "\n".join(parts)
    if len(brief) > max_chars:
        brief = brief[: max_chars - 20] + "\n\n…[truncated]…\n"
    return brief


def build_review_brief(run_dir: Path, iter_dir: Path, *, max_chars: int = 12000) -> str:
    parts = [
        "# Panel result-review brief (host parent)",
        "",
        f"Iteration directory: {iter_dir}",
        "Review the obtained results. Active break-attempt checklist:",
        "1) no circular reasoning",
        "2) assumptions stated",
        "3) edge cases",
        "4) every claim has a concrete artifact",
        "5) numeric results must be independently reproducible",
        "6) off-by-one / scope errors",
        "",
        "List inspected paths, uninspected paths, pass/fail/partial, and what would invalidate claims.",
        "Do not bank uncertified numeric tallies or manuscript theorems without independent checks.",
        "",
    ]
    try:
        from goal_priority import campaign_match_line  # type: ignore

        match = campaign_match_line(run_dir)
        if match.strip():
            parts.append("## Goal / campaign")
            parts.append("")
            parts.append(match.rstrip())
            parts.append("")
    except Exception:  # noqa: BLE001
        pass
    # List iteration files (names only)
    try:
        names = sorted(p.name for p in iter_dir.iterdir() if p.is_file())[:40]
        parts.append("## Files in iteration dir")
        parts.append("")
        for n in names:
            parts.append(f"- {n}")
        parts.append("")
    except OSError:
        pass
    for md in sorted(iter_dir.glob("0*.md"))[:6]:
        try:
            body = md.read_text(encoding="utf-8", errors="replace")[:2500]
            parts.append(f"## {md.name}")
            parts.append("")
            parts.append(body)
            parts.append("")
        except OSError:
            pass
    brief = "\n".join(parts)
    if len(brief) > max_chars:
        brief = brief[: max_chars - 20] + "\n\n…[truncated]…\n"
    return brief


def write_host_synthesis(
    iter_dir: Path,
    phase: str,
    summary: dict[str, Any],
    *,
    next_path: str = "",
) -> Path:
    """Deterministic synthesis: keep recovery path; record panel dissent/usability."""
    out_dir = phase_dirs(iter_dir, phase)[0]
    path = out_dir / "host_synthesis.md"
    usable = summary.get("usable_providers") or []
    lines = [
        f"# Host synthesis — {phase}",
        "",
        "Parent-owned hybrid model: panel advises; single path remains host/recovery-owned.",
        "Panel consensus is **not** evidence for banking.",
        "",
        f"- usable_providers: {', '.join(usable) if usable else '(none)'}",
        f"- panel_content_pass: {summary.get('panel_content_pass')}",
        f"- different_family_logic_available: {summary.get('different_family_logic_available')}",
        "",
    ]
    if next_path:
        lines.append("## Committed single path (from recovery / loop_state)")
        lines.append("")
        lines.append(next_path.strip())
        lines.append("")
    results = summary.get("results") or {}
    lines.append("## Per-provider status")
    lines.append("")
    for p, meta in sorted(results.items()):
        if not isinstance(meta, dict):
            continue
        lines.append(
            f"- **{p}**: {meta.get('status')} "
            f"(`{meta.get('error_class') or 'ok'}`, exit={meta.get('exit_code')})"
        )
    lines.append("")
    if not usable:
        lines.append(
            "## Note\n\nNo usable panel content. Anti-deadlock may allow single-path "
            "work if dual engines are ready; do not bank logic/scope without "
            "different-family review.\n"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def panel_prompt_addon(run_dir: Path, iter_dir: Path | None) -> str:
    """Text appended to the primary iteration prompt when host panel is enabled."""
    panel_root = (iter_dir / "panel") if iter_dir else (run_dir / "iterations")
    return (
        "\n\n## Host-owned multi-agent panel (hybrid model — mandatory when enabled)\n"
        "The headless **driver** (not you) owns multi-agent target advice and result review.\n"
        f"- Read panel artifacts under: `{panel_root}` (especially "
        "`01_target_advice/host_synthesis.md` and agent `*.md` files).\n"
        "- Execute the **single path** from recovery.md / loop_state next_preferred_path "
        "(unless a host-verifiable blocker is documented in panel synthesis).\n"
        "- **Do NOT** nest multi-agent panel CLI calls "
        "(`claude -p`, `codewhale exec`, `kimi -p`, nested `codex exec`) for panel purposes.\n"
        "- You may still run local scripts/tests for machine independence.\n"
        "- Independently host-verify any agent claims before banking; panel consensus ≠ evidence.\n"
        "- Append iteration ledger as usual; leave formal-lane rules from standing orders intact.\n"
    )


def smoke(
    root: Path,
    providers: list[str] | None = None,
    timeout_s: int = 120,
    *,
    runner: Runner | None = None,
) -> dict[str, Any]:
    providers = providers or list(DEFAULT_PROVIDERS)
    prompt = (
        "Reply with exactly one line: PANEL_SMOKE_OK. "
        "Do not use tools. Do not read files."
    )
    tmp = Path(tempfile.mkdtemp(prefix="panel_parent_smoke_"))
    try:
        summary = dispatch_phase(
            iter_dir=tmp,
            phase="smoke",
            prompt=prompt,
            providers=providers,
            timeout_s=timeout_s,
            root=root,
            runner=runner,
            panel_cfg={"timeout_mode": "fixed", "timeouts": dict(DEFAULT_TIMEOUT_S)},
        )
        return summary
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run_panel_phase_for_drive(
    run_dir: Path,
    root: Path,
    phase: str,
    *,
    iter_dir: Path | None = None,
    prompt: str | None = None,
    providers: list[str] | None = None,
    timeout_s: int | None = None,
    runner: Runner | None = None,
) -> dict[str, Any]:
    """High-level entry used by drive_command."""
    cfg = load_panel_config(run_dir)
    prov = providers or list(cfg.get("providers") or DEFAULT_PROVIDERS)
    timeouts = cfg.get("timeouts") or DEFAULT_TIMEOUT_S
    t_default = int(timeouts.get(phase, DEFAULT_TIMEOUT_S.get(phase, 600)))
    # 0 / None → let adaptive formula use phase base only
    t_s = int(timeout_s) if timeout_s is not None and int(timeout_s) > 0 else t_default
    idir = iter_dir or ensure_iter_dir(run_dir)
    if prompt is None:
        if phase == "target_advice":
            prompt = build_target_brief(run_dir)
        elif phase == "result_review":
            prompt = build_review_brief(run_dir, idir)
        else:
            prompt = "Reply briefly with status."
    summary = dispatch_phase(
        iter_dir=idir,
        phase=phase,
        prompt=prompt,
        providers=list(prov),
        timeout_s=t_s,
        root=root,
        runner=runner,
        panel_cfg=cfg,
        run_dir=run_dir,
    )
    next_path = ""
    state_path = run_dir / "loop_state.json"
    if state_path.is_file():
        try:
            next_path = str(
                json.loads(state_path.read_text(encoding="utf-8")).get("next_preferred_path")
                or ""
            )
        except (OSError, json.JSONDecodeError):
            next_path = ""
    write_host_synthesis(idir, phase, summary, next_path=next_path)
    summary["iter_dir"] = str(idir)
    return summary
