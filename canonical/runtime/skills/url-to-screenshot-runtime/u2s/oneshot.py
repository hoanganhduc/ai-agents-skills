"""Tier-1 headless one-shot ``--screenshot`` argv builder + runner.

Tier-1 is the ``--consent off`` fallback path (not the stock default -- consent
dismissal is on by default and is a Tier-2 CDP DOM op, so ordinary captures enter
Tier-2). Its SSRF posture is weaker than Tier-2: it enforces ONLY the Python
pre-resolve admission gate plus a single ``--host-resolver-rules`` MAP pin of the
validated top-level host, so redirect/sub-resource SSRF is unguarded here.

The argv builder is a pure function so the offline selftest validates it without
launching a browser. The actual launch is LAZY (inside ``run_oneshot``) so the
selftest import graph never reaches a subprocess. Tier-1 cannot do full-page; a
full-page request is routed to Tier-2 by ``capture.py`` and never silently
degraded here.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class OneShotSpec:
    browser_path: str
    url: str
    out_path: str
    width: int = 1280
    height: int = 800
    device_scale: float = 1.0
    no_sandbox: bool = False
    wait_ms: int = 0
    resolver_pin: tuple[str, str] | None = None  # (validated host, validated ip)


def build_oneshot_argv(spec: OneShotSpec) -> list[str]:
    """Build the headless one-shot capture argv.

    Note: this path adds NO ``--remote-debugging-port`` and NO
    ``--remote-allow-origins`` flag (there is no CDP endpoint in Tier-1). When a
    settle wait is requested it is passed via ``--virtual-time-budget`` so the
    one-shot lets late paint complete deterministically.
    """
    argv = [
        spec.browser_path,
        "--headless=new",
        "--disable-gpu",
        "--hide-scrollbars",
        "--disable-extensions",
        f"--window-size={spec.width},{spec.height}",
        f"--force-device-scale-factor={spec.device_scale:g}",
    ]
    if spec.no_sandbox:
        argv.append("--no-sandbox")
    if spec.wait_ms and spec.wait_ms > 0:
        argv.append(f"--virtual-time-budget={int(spec.wait_ms)}")
    if spec.resolver_pin is not None:
        host, ip = spec.resolver_pin
        argv.append(f"--host-resolver-rules=MAP {host} {ip}")
    argv.append(f"--screenshot={spec.out_path}")
    argv.append(spec.url)
    return argv


def run_oneshot(spec: OneShotSpec, *, timeout: float, os_name: str | None = None) -> dict:
    """Launch into a fresh profile, then atomically publish a validated PNG.

    Imported lazily by the engine; never reached from the offline selftest. A
    fresh ``url2png_`` ``--user-data-dir`` is created and removed in ``finally``
    via ``procctl.cleanup_profile_dir``. On timeout the whole process tree is
    reaped via the per-OS kill strategy and ``BLOCKED_TIMEOUT`` is returned. The
    browser writes only to an adjacent unpredictable temporary path; the final
    destination is replaced atomically, so a planted output symlink is never
    followed.
    """
    import os
    import subprocess
    import tempfile
    from dataclasses import replace
    from pathlib import Path

    from . import cdp, procctl

    os_name = os_name or os.name
    profile_dir = Path(tempfile.mkdtemp(prefix="url2png_"))
    destination = Path(spec.out_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, staged_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp.png", dir=str(destination.parent)
    )
    os.close(descriptor)
    os.unlink(staged_name)
    staged = Path(staged_name)
    argv = build_oneshot_argv(replace(spec, out_path=staged_name))
    # The one-shot needs its own user-data-dir; insert it right after the binary.
    argv.insert(1, f"--user-data-dir={profile_dir}")
    strategy = procctl.select_kill_strategy(os_name)
    proc = None
    try:
        proc = subprocess.Popen(  # noqa: S603 - argv is built from validated inputs
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **procctl.popen_kwargs(os_name),
        )
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            strategy.kill(proc)
            return {"returncode": None, "reaped": True, "reason": "BLOCKED_TIMEOUT"}
        if proc.returncode != 0 or not staged.is_file():
            return {
                "returncode": proc.returncode,
                "reaped": False,
                "status": "ONESHOT_FAILED",
                "reason": "browser did not produce a PNG",
            }
        size = staged.stat().st_size
        if size <= 0 or size > cdp.MAX_PNG_BYTES:
            return {
                "returncode": proc.returncode,
                "reaped": False,
                "status": "BLOCKED_OUTPUT",
                "reason": f"PNG size {size} exceeds byte limit {cdp.MAX_PNG_BYTES}",
            }
        png_bytes = staged.read_bytes()
        width, height, digest = cdp._atomic_write_png(spec.out_path, png_bytes)
        return {
            "returncode": proc.returncode,
            "reaped": False,
            "out_path": spec.out_path,
            "width": width,
            "height": height,
            "bytes": len(png_bytes),
            "sha256": digest,
        }
    except cdp._OutputBlocked as exc:
        return {
            "returncode": proc.returncode if proc is not None else None,
            "reaped": False,
            "status": exc.status,
            "reason": exc.detail,
        }
    finally:
        try:
            staged.unlink()
        except OSError:
            pass
        procctl.cleanup_profile_dir(profile_dir)
