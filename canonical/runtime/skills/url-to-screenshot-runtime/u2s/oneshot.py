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
    """Launch the one-shot capture into a fresh temp profile and reap on timeout.

    Imported lazily by the engine; never reached from the offline selftest. A
    fresh ``url2png_`` ``--user-data-dir`` is created and removed in ``finally``
    via ``procctl.cleanup_profile_dir``. On timeout the whole process tree is
    reaped via the per-OS kill strategy and ``BLOCKED_TIMEOUT`` is returned.
    """
    import os
    import subprocess
    import tempfile
    from pathlib import Path

    from . import procctl

    os_name = os_name or os.name
    profile_dir = Path(tempfile.mkdtemp(prefix="url2png_"))
    argv = build_oneshot_argv(spec)
    # The one-shot needs its own user-data-dir; insert it right after the binary.
    argv.insert(1, f"--user-data-dir={profile_dir}")
    strategy = procctl.select_kill_strategy(os_name)
    proc = subprocess.Popen(  # noqa: S603 - argv is built from validated inputs
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **procctl.popen_kwargs(os_name),
    )
    try:
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            strategy.kill(proc)
            return {"returncode": None, "reaped": True, "reason": "BLOCKED_TIMEOUT"}
        return {"returncode": proc.returncode, "reaped": False, "out_path": spec.out_path}
    finally:
        procctl.cleanup_profile_dir(profile_dir)
