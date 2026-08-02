#!/usr/bin/env python3
"""Force-loop CLI: bootstrap, apply-defaults, start, stop, replace, status, drain, smoke.

Cross-platform default path for scripted unattended ARL drive.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

PACK_DIR = Path(__file__).resolve().parent
RUNTIME_PARENT = PACK_DIR.parent  # autonomous-research-loop-runtime/

if str(PACK_DIR) not in sys.path:
    sys.path.insert(0, str(PACK_DIR))

# Local imports (same directory)
from apply_force_loop_defaults import (  # noqa: E402
    apply_defaults,
    verify_effective,
)
from force_loop_process import (  # noqa: E402
    build_drive_command,
    build_supervisor_command,
    run_foreground,
    run_posix_detach,
    select_backend,
    status_snapshot,
    stop_loop_processes,
    systemd_user_available,
)
from load_loop_env import EnvLoadError, apply_to_environ, load_env_file, merge_env_files  # noqa: E402


def _python() -> str:
    return os.environ.get("AAS_RUNTIME_PYTHON") or sys.executable


def _runtime_py() -> Path:
    return RUNTIME_PARENT / "autonomous_research_loop_runtime.py"


def _run_runtime(args: list[str], *, env: dict[str, str] | None = None) -> int:
    cmd = [_python(), str(_runtime_py()), *args]
    proc = subprocess.run(cmd, env=env or os.environ.copy(), check=False)
    return int(proc.returncode)


def cmd_apply_defaults(args: argparse.Namespace) -> int:
    result = apply_defaults(
        Path(args.loop),
        profile=args.profile,
        research_title=args.research_title,
        backup=not args.no_backup,
    )
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


def cmd_bootstrap(args: argparse.Namespace) -> int:
    loop = Path(args.loop).expanduser().resolve()
    root = Path(args.root).expanduser().resolve() if args.root else loop.parent
    loop.mkdir(parents=True, exist_ok=True)

    need_init = not (loop / "loop_state.json").is_file()
    if need_init:
        if not args.goal:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": "loop missing; pass --goal (and optional --success-criteria) for init",
                    },
                    indent=2,
                )
            )
            return 2
        init_args = [
            "init",
            "--dir",
            str(loop),
            "--goal",
            args.goal,
            "--goal-focus-mode",
            "enforce",
        ]
        if args.success_criteria:
            init_args.extend(["--success-criteria", args.success_criteria])
        if args.profile == "formal":
            init_args.extend(["--formal-policy", "on"])
            if args.formal_project:
                init_args.extend(["--formal-project", args.formal_project])
        rc = _run_runtime(init_args)
        if rc != 0:
            print(json.dumps({"ok": False, "error": "init failed", "rc": rc}, indent=2))
            return rc

    result = apply_defaults(
        loop,
        profile=args.profile,
        research_title=args.research_title,
        backup=not args.no_backup,
    )
    smoke = _smoke_checks(loop, args.profile, root=root, live=False)
    out = {
        "ok": bool(result.get("ok") and smoke.get("ok")),
        "bootstrap": result,
        "smoke": smoke,
        "next": [
            f"force-loop start --loop {loop} --root {root}",
        ],
    }
    print(json.dumps(out, indent=2))
    return 0 if out["ok"] else 1


def _load_start_env(loop: Path) -> dict[str, str]:
    env = os.environ.copy()
    pack_defaults = PACK_DIR / "defaults" / "env.defaults"
    host_env = loop / "driver" / "force_loop.env"
    try:
        merged = merge_env_files([pack_defaults, host_env])
    except EnvLoadError as exc:
        raise SystemExit(f"env load failed: {exc}") from exc
    apply_to_environ(merged, env, override=True)
    # Secrets file path only — never parse secrets into logs.
    secrets = env.get("AAS_COMPUTE_SECRETS_FILE", "").strip()
    if secrets:
        # Operator may source secrets outside this loader; we only ensure path is set.
        env["AAS_COMPUTE_SECRETS_FILE"] = secrets
    env["LOOP_DIR"] = str(loop)
    return env


def cmd_start(args: argparse.Namespace) -> int:
    loop = Path(args.loop).expanduser().resolve()
    root = Path(args.root).expanduser().resolve() if args.root else loop.parent
    if not loop.is_dir():
        print(json.dumps({"ok": False, "error": f"loop not found: {loop}"}, indent=2))
        return 2

    # Ensure pins present before start.
    errors = verify_effective(loop, args.profile)
    if errors and not args.skip_defaults_check:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "defaults missing; run bootstrap or apply-defaults",
                    "errors": errors,
                },
                indent=2,
            )
        )
        return 1

    try:
        backend = select_backend(args.backend, detach=bool(args.detach))
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 2

    env = _load_start_env(loop)
    env["PROJECT_ROOT"] = str(root)
    if not env.get("AAS_RUNTIME_ROOT"):
        # Best-effort: installed layout is runtime/workspace/skills/<skill>
        # When running from the git tree, leave unset (drive still works).
        candidate = RUNTIME_PARENT.parent.parent.parent
        if (candidate / "workspace").is_dir() or (candidate / "run_skill.sh").is_file():
            env["AAS_RUNTIME_ROOT"] = str(candidate)

    extra: list[str] = []
    if args.provider:
        extra.extend(["--provider", args.provider])
    if args.panel:
        extra.extend(["--panel", args.panel])
    if args.profile == "formal":
        extra.extend(["--formal-policy", "on"])
        if args.formal_typecheck:
            extra.append("--formal-typecheck")
    if args.drive_extra:
        extra.extend(args.drive_extra)

    supervisor = build_supervisor_command(
        pack_parent=RUNTIME_PARENT,
        loop_dir=loop,
        project_root=root,
    )
    if supervisor and not args.drive_only:
        argv = supervisor
        env["LOOP_DIR"] = str(loop)
        env["PROJECT_ROOT"] = str(root)
        if extra:
            # Supervisor reads DRIVE_EXTRA_ARGS as shell-ish; pass via env space-joined
            # only safe flags we control (no secrets).
            env["DRIVE_EXTRA_ARGS"] = " ".join(extra)
    else:
        argv = build_drive_command(
            runtime_py=_runtime_py(),
            loop_dir=loop,
            project_root=root,
            extra_args=extra,
        )

    print(
        json.dumps(
            {
                "ok": True,
                "backend": backend,
                "argv_preview": argv[:6] + (["…"] if len(argv) > 6 else []),
                "loop": str(loop),
            },
            indent=2,
        )
    )

    if backend == "foreground":
        return run_foreground(argv, loop_dir=loop, env=env)
    if backend == "posix_detach":
        return run_posix_detach(argv, loop_dir=loop, env=env)
    if backend == "systemd_user":
        return _start_systemd_user(argv, loop=loop, root=root, env=env)
    print(json.dumps({"ok": False, "error": f"backend not implemented: {backend}"}, indent=2))
    return 2


def _start_systemd_user(
    argv: list[str],
    *,
    loop: Path,
    root: Path,
    env: dict[str, str],
) -> int:
    unit = f"aas-force-loop-{loop.name}".replace("/", "-")[:100]
    # Transient unit: paths only in command; no secrets via --setenv.
    cmd = [
        "systemd-run",
        "--user",
        "--quiet",
        "--collect",
        "--no-ask-password",
        "--expand-environment=no",
        f"--unit={unit}",
        "--service-type=exec",
        f"--working-directory={root}",
        "--property=KillMode=control-group",
        "--property=TimeoutStopSec=20s",
        *argv,
    ]
    # Pass non-secret env that supervisor needs via file already written.
    proc = subprocess.run(cmd, env=env, check=False)
    print(json.dumps({"ok": proc.returncode == 0, "unit": unit, "rc": proc.returncode}, indent=2))
    return int(proc.returncode)


def cmd_stop(args: argparse.Namespace) -> int:
    loop = Path(args.loop).expanduser().resolve()
    stopped = stop_loop_processes(loop)
    print(json.dumps({"ok": True, "stopped": stopped, "loop": str(loop)}, indent=2))
    return 0


def cmd_replace(args: argparse.Namespace) -> int:
    stop_rc = cmd_stop(args)
    if stop_rc != 0:
        return stop_rc
    return cmd_start(args)


def cmd_status(args: argparse.Namespace) -> int:
    loop = Path(args.loop).expanduser().resolve()
    snap = status_snapshot(loop)
    # Best-effort goal-focus status
    gf: dict[str, Any] = {}
    if _runtime_py().is_file():
        try:
            proc = subprocess.run(
                [_python(), str(_runtime_py()), "goal-focus", "status", "--dir", str(loop)],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if proc.stdout.strip():
                try:
                    gf = json.loads(proc.stdout)
                except json.JSONDecodeError:
                    gf = {"raw_preview": proc.stdout[:500]}
        except (subprocess.TimeoutExpired, OSError) as exc:
            gf = {"error": str(exc)}
    errors = verify_effective(loop, args.profile) if (loop / "driver" / "force_loop.env").is_file() else [
        "force-loop defaults not applied"
    ]
    out = {
        "process": snap,
        "defaults_ok": not errors,
        "defaults_errors": errors,
        "goal_focus": gf,
        "systemd_user_available": systemd_user_available(),
    }
    print(json.dumps(out, indent=2, default=str))
    return 0


def cmd_drain(args: argparse.Namespace) -> int:
    """Thin wrappers around goal-focus recover/status — no reclaim reimplementation."""
    loop = Path(args.loop).expanduser().resolve()
    actions: list[dict[str, Any]] = []

    def _gf(extra: list[str]) -> dict[str, Any]:
        proc = subprocess.run(
            [_python(), str(_runtime_py()), "goal-focus", *extra, "--dir", str(loop)],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        body: Any
        try:
            body = json.loads(proc.stdout) if proc.stdout.strip() else {"stdout": proc.stdout}
        except json.JSONDecodeError:
            body = {"stdout": proc.stdout, "stderr": proc.stderr}
        return {"argv": extra, "rc": proc.returncode, "result": body}

    actions.append(_gf(["status"]))

    if args.cancel_dispatch_id:
        actions.append(
            _gf(
                [
                    "recover-dispatch",
                    "--cancel",
                    "--dispatch-id",
                    args.cancel_dispatch_id,
                ]
            )
        )
    elif args.recover_dispatch:
        actions.append(_gf(["recover-dispatch"]))

    if args.recover_quarantine:
        q = ["recover-quarantine"]
        if args.release_fingerprint:
            q.extend(["--release", "--candidate-fingerprint", args.release_fingerprint])
        actions.append(_gf(q))

    snap = status_snapshot(loop)
    if args.cancel_dead_pids and snap.get("matched_pids"):
        # Only cancel if pidfile claims dead owner — still via recover-dispatch when id known.
        actions.append(
            {
                "note": "matched pids present; use --cancel-dispatch-id with exact id from status",
                "matched_pids": snap.get("matched_pids"),
                "pidfile_alive": snap.get("pidfile_alive"),
            }
        )

    print(json.dumps({"ok": True, "actions": actions, "process": snap}, indent=2, default=str))
    return 0


def _smoke_checks(
    loop: Path,
    profile: str,
    *,
    root: Path | None = None,
    live: bool = False,
) -> dict[str, Any]:
    errors: list[str] = []
    if not _runtime_py().is_file():
        errors.append(f"runtime missing: {_runtime_py()}")
    defaults_errors = verify_effective(loop, profile) if loop.is_dir() else ["loop missing"]
    errors.extend(defaults_errors)

    # Env loader self-check
    try:
        from load_loop_env import parse_env_text

        parse_env_text("FOO=bar\n")
        try:
            parse_env_text("BAD=$(whoami)\n")
            errors.append("env loader accepted unsafe value")
        except EnvLoadError:
            pass
    except Exception as exc:  # pragma: no cover
        errors.append(f"env loader import failed: {exc}")

    backend = select_backend(None)
    out: dict[str, Any] = {
        "ok": not errors,
        "errors": errors,
        "default_backend": backend,
        "systemd_user_available": systemd_user_available(),
        "platform": sys.platform,
        "profile": profile,
        "loop": str(loop),
    }
    if live and root is not None and not errors:
        # Optional: runtime selftest
        rc = _run_runtime(["selftest"])
        out["runtime_selftest_rc"] = rc
        if rc != 0:
            out["ok"] = False
            out["errors"] = list(out["errors"]) + ["runtime selftest failed"]
    return out


def cmd_smoke(args: argparse.Namespace) -> int:
    loop = Path(args.loop).expanduser().resolve() if args.loop else Path.cwd()
    root = Path(args.root).expanduser().resolve() if args.root else loop.parent
    out = _smoke_checks(loop, args.profile, root=root, live=bool(args.live))
    print(json.dumps(out, indent=2))
    return 0 if out.get("ok") else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="force-loop",
        description="Default scripted force-loop kit (cross-platform)",
    )
    sub = p.add_subparsers(dest="command", required=True)

    def add_loop(sp: argparse.ArgumentParser, *, required: bool = True) -> None:
        sp.add_argument("--loop", required=required, help="loop directory")
        sp.add_argument("--root", default=None, help="project root (default: parent of loop)")
        sp.add_argument(
            "--profile",
            default="formal",
            choices=["formal", "general"],
        )

    b = sub.add_parser("bootstrap", help="init if needed + apply defaults + smoke")
    add_loop(b)
    b.add_argument("--goal", default=None)
    b.add_argument("--success-criteria", default=None)
    b.add_argument("--research-title", default=None)
    b.add_argument("--formal-project", default=None)
    b.add_argument("--no-backup", action="store_true")
    b.set_defaults(func=cmd_bootstrap)

    a = sub.add_parser("apply-defaults", help="write enforce/hard/notify/compute/formal pins")
    add_loop(a)
    a.add_argument("--research-title", default=None)
    a.add_argument("--no-backup", action="store_true")
    a.set_defaults(func=cmd_apply_defaults)

    s = sub.add_parser("start", help="start supervisor/drive (foreground default)")
    add_loop(s)
    s.add_argument("--backend", default=None, help="foreground|posix_detach|systemd_user|auto")
    s.add_argument("--detach", action="store_true", help="use posix_detach when backend=auto")
    s.add_argument("--provider", default=None)
    s.add_argument("--panel", default=None, help="on|off|auto")
    s.add_argument("--drive-only", action="store_true", help="skip bash supervisor even on POSIX")
    s.add_argument(
        "--formal-typecheck",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="pass --formal-typecheck when profile=formal (default: on)",
    )
    s.add_argument("--skip-defaults-check", action="store_true")
    s.add_argument("drive_extra", nargs="*", help="extra args after -- passed to drive")
    s.set_defaults(func=cmd_start)

    st = sub.add_parser("stop", help="stop matching supervisor/drive processes")
    add_loop(st)
    st.set_defaults(func=cmd_stop)

    r = sub.add_parser("replace", help="stop then start")
    add_loop(r)
    r.add_argument("--backend", default=None)
    r.add_argument("--detach", action="store_true")
    r.add_argument("--provider", default=None)
    r.add_argument("--panel", default=None)
    r.add_argument("--drive-only", action="store_true")
    r.add_argument(
        "--formal-typecheck",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    r.add_argument("--skip-defaults-check", action="store_true")
    r.add_argument("drive_extra", nargs="*")
    r.set_defaults(func=cmd_replace)

    u = sub.add_parser("status", help="process + defaults + goal-focus status")
    add_loop(u)
    u.set_defaults(func=cmd_status)

    d = sub.add_parser("drain", help="wrap goal-focus status/recover (no reclaim reimplementation)")
    add_loop(d)
    d.add_argument("--recover-dispatch", action="store_true")
    d.add_argument("--cancel-dispatch-id", default=None)
    d.add_argument("--recover-quarantine", action="store_true")
    d.add_argument("--release-fingerprint", default=None)
    d.add_argument("--cancel-dead-pids", action="store_true")
    d.set_defaults(func=cmd_drain)

    sm = sub.add_parser("smoke", help="offline default + env checks")
    add_loop(sm, required=False)
    sm.add_argument("--live", action="store_true", help="also run runtime selftest")
    sm.set_defaults(func=cmd_smoke)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
