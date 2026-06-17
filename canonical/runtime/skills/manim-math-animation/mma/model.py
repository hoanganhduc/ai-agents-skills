"""Scene-spec data model. Pure standard library; JSON round-trips."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

EMPHASIS_TYPES = ("indicate", "circumscribe", "flash", "wiggle")


@dataclass
class Emphasis:
    """Emphasis applied after the equation at index ``at`` is shown."""

    at: int
    type: str = "indicate"

    def validate(self, n_equations: int) -> None:
        if self.type not in EMPHASIS_TYPES:
            raise ValueError(f"unknown emphasis type {self.type!r}; valid: {EMPHASIS_TYPES}")
        if not (0 <= self.at < n_equations):
            raise ValueError(f"emphasis.at {self.at} out of range for {n_equations} equations")

    def to_dict(self) -> dict[str, Any]:
        return {"at": self.at, "type": self.type}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Emphasis":
        return cls(at=int(d["at"]), type=d.get("type", "indicate"))


@dataclass
class SceneSpec:
    """A math-animation scene: a sequence of equations that are written then
    morphed into one another, with optional title and per-step emphasis."""

    equations: list[str]
    title: Optional[str] = None
    run_time: float = 1.5
    hold: float = 0.6
    width: int = 1920
    height: int = 1080
    fps: int = 30
    background: str = "#0f172a"
    font: str = "Noto Sans"
    scale: float = 1.6
    emphases: list[Emphasis] = field(default_factory=list)

    def validate(self) -> None:
        if not self.equations:
            raise ValueError("scene spec needs at least one equation")
        if self.fps <= 0 or self.width <= 0 or self.height <= 0:
            raise ValueError("width/height/fps must be positive")
        for emphasis in self.emphases:
            emphasis.validate(len(self.equations))

    def to_dict(self) -> dict[str, Any]:
        return {
            "equations": list(self.equations),
            "title": self.title,
            "run_time": self.run_time,
            "hold": self.hold,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "background": self.background,
            "font": self.font,
            "scale": self.scale,
            "emphases": [e.to_dict() for e in self.emphases],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SceneSpec":
        return cls(
            equations=list(d["equations"]),
            title=d.get("title"),
            run_time=float(d.get("run_time", 1.5)),
            hold=float(d.get("hold", 0.6)),
            width=int(d.get("width", 1920)),
            height=int(d.get("height", 1080)),
            fps=int(d.get("fps", 30)),
            background=d.get("background", "#0f172a"),
            font=d.get("font", "Noto Sans"),
            scale=float(d.get("scale", 1.6)),
            emphases=[Emphasis.from_dict(e) for e in d.get("emphases", [])],
        )
