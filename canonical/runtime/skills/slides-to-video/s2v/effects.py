"""Tier-1 visual effects -> ffmpeg ``-vf`` filtergraph fragments.

Every effect is expressible as a single video-filter chain applied to one
looping still image, so a slide clip is one ffmpeg invocation and the lossless
``-c copy`` concat is preserved. Effects operate on the rendered slide pixels,
so they work on arbitrary pre-made PNG/PDF/PPTX slides (no Manim authoring).

Supported: ken_burns (zoompan), highlight (drawbox), spotlight (4 dark bands
around a region), laser (a small marker moving between two points), reveal
(masks that lift at timestamps).

Pure standard library only; this module builds strings, it does not run ffmpeg.
"""

from __future__ import annotations

from .model import EffectSpec


def base_chain(width: int, height: int, fps: int) -> str:
    """Normalize any source image to the canonical frame before effects."""
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"setsar=1,fps={fps}"
    )


def _enable(start: float, duration, total: float) -> str:
    if start <= 0 and duration is None:
        return ""
    end = total if duration is None else min(total, start + float(duration))
    return f":enable='between(t,{start:.3f},{end:.3f})'"


def _ken_burns(eff: EffectSpec, width: int, height: int, fps: int, total: float) -> str:
    p = eff.params
    zoom = float(p.get("zoom", 1.15))
    frames = max(1, int(round(total * fps)))
    step = max(0.0001, (zoom - 1.0) / frames)
    return (
        f"zoompan=z='min(zoom+{step:.6f},{zoom})':d={frames}"
        f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={width}x{height}:fps={fps}"
    )


def _highlight(eff: EffectSpec, total: float) -> str:
    p = eff.params
    color = p.get("color", "yellow")
    opacity = float(p.get("opacity", 0.35))
    x, y = int(p.get("x", 0)), int(p.get("y", 0))
    w, h = int(p.get("w", 100)), int(p.get("h", 100))
    return f"drawbox=x={x}:y={y}:w={w}:h={h}:color={color}@{opacity}:t=fill{_enable(eff.start, eff.duration, total)}"


def _spotlight(eff: EffectSpec, width: int, height: int, total: float) -> str:
    p = eff.params
    dim = float(p.get("dim", 0.55))
    x, y = int(p.get("x", 0)), int(p.get("y", 0))
    w, h = int(p.get("w", width)), int(p.get("h", height))
    en = _enable(eff.start, eff.duration, total)
    bands = [
        (0, 0, width, y),                      # top
        (0, y + h, width, height - (y + h)),   # bottom
        (0, y, x, h),                          # left
        (x + w, y, width - (x + w), h),        # right
    ]
    parts = []
    for bx, by, bw, bh in bands:
        if bw > 0 and bh > 0:
            parts.append(f"drawbox=x={bx}:y={by}:w={bw}:h={bh}:color=black@{dim}:t=fill{en}")
    return ",".join(parts)


def _laser(eff: EffectSpec, total: float) -> str:
    p = eff.params
    radius = int(p.get("radius", 14))
    color = p.get("color", "red")
    x0, y0 = p.get("from", [0, 0])
    x1, y1 = p.get("to", [x0, y0])
    start = float(eff.start)
    dur = float(eff.duration) if eff.duration else max(0.001, total - start)
    side = radius * 2
    xexpr = f"{x0}+({x1}-{x0})*max(0\\,min(1\\,(t-{start:.3f})/{dur:.3f}))-{radius}"
    yexpr = f"{y0}+({y1}-{y0})*max(0\\,min(1\\,(t-{start:.3f})/{dur:.3f}))-{radius}"
    return (
        f"drawbox=x='{xexpr}':y='{yexpr}':w={side}:h={side}:"
        f"color={color}@0.9:t=fill{_enable(eff.start, eff.duration, total)}"
    )


def _reveal(eff: EffectSpec, total: float) -> str:
    """Cover regions with opaque masks that disappear at their reveal time."""
    covers = eff.params.get("covers", [])
    parts = []
    for cover in covers:
        x, y, w, h = int(cover["x"]), int(cover["y"]), int(cover["w"]), int(cover["h"])
        reveal = float(cover.get("at", 0.0))
        parts.append(
            f"drawbox=x={x}:y={y}:w={w}:h={h}:color=black@1.0:t=fill"
            f":enable='lt(t,{reveal:.3f})'"
        )
    return ",".join(parts)


def build_filtergraph(width: int, height: int, fps: int, total: float, effects: list[EffectSpec]) -> str:
    """Compose the full ``-vf`` chain: base normalize -> effects -> output format."""
    chain = [base_chain(width, height, fps)]
    for eff in effects:
        eff.validate()
        if eff.type == "ken_burns":
            chain.append(_ken_burns(eff, width, height, fps, total))
        elif eff.type == "highlight":
            chain.append(_highlight(eff, total))
        elif eff.type == "spotlight":
            frag = _spotlight(eff, width, height, total)
            if frag:
                chain.append(frag)
        elif eff.type == "laser":
            chain.append(_laser(eff, total))
        elif eff.type == "reveal":
            frag = _reveal(eff, total)
            if frag:
                chain.append(frag)
    chain.append("format=yuv420p")
    return ",".join(c for c in chain if c)
