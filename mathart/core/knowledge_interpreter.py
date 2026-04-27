"""Knowledge Interpreter — V6 omnipresent knowledge translation center.

The interpreter converts externally distilled aesthetic knowledge into low-level
runtime parameter dictionaries. It accepts local JSON produced by book/PDF/paper
pipelines, but also provides a deterministic mock default so the animation stack
remains usable before external knowledge has been pushed.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping


DEFAULT_KNOWLEDGE: dict[str, Any] = {
    "hit_stop_frames": 4,
    "step_rate": 2,
    "toon_bands": 3,
    "hit_stop_acceleration_drop": 18.0,
    "hit_stop_min_previous_acceleration": 24.0,
    "smooth_motion_velocity_threshold": 0.08,
    "squash_velocity_threshold": 0.05,
    "squash_max_stretch": 1.35,
    "squash_velocity_to_stretch": 0.18,
    "squash_acceleration_to_stretch": 0.025,
    "anticipation_weight": 1.0,
    "impact_reward_weight": 1.0,
    "line_width": 1.0,
    "shadow_hardness": 0.75,
    "palette_color_count": 16,
}


@dataclass(frozen=True)
class TimingParams:
    """Low-level timing controls distilled from animation theory."""

    hit_stop_frames: int = 4
    step_rate: int = 2
    hit_stop_acceleration_drop: float = 18.0
    hit_stop_min_previous_acceleration: float = 24.0
    smooth_motion_velocity_threshold: float = 0.08
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PhysicsParams:
    """Low-level motion/impact controls distilled from physical animation knowledge."""

    squash_velocity_threshold: float = 0.05
    squash_acceleration_threshold: float = 0.0
    squash_max_stretch: float = 1.35
    squash_velocity_to_stretch: float = 0.18
    squash_acceleration_to_stretch: float = 0.025
    anticipation_weight: float = 1.0
    impact_reward_weight: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StyleParams:
    """Low-level Blender/compositor/toon controls distilled from art books."""

    toon_bands: int = 3
    line_width: float = 1.0
    shadow_hardness: float = 0.75
    palette_color_count: int = 16
    color_quantization_enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class InterpretedKnowledge:
    """Structured output of the knowledge translation center."""

    timing: TimingParams
    physics: PhysicsParams
    style: StyleParams
    source_path: str = "mock_default"
    raw: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "TimingParams": self.timing.to_dict(),
            "PhysicsParams": self.physics.to_dict(),
            "StyleParams": self.style.to_dict(),
            "source_path": self.source_path,
            "raw": dict(self.raw or {}),
        }


def _coerce_int(value: Any, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = int(default)
    if minimum is not None:
        result = max(minimum, result)
    if maximum is not None:
        result = min(maximum, result)
    return result


def _coerce_float(value: Any, default: float, *, minimum: float | None = None, maximum: float | None = None) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        result = float(default)
    if minimum is not None:
        result = max(minimum, result)
    if maximum is not None:
        result = min(maximum, result)
    return result


def _coerce_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    return bool(default)


class KnowledgeInterpreter:
    """Parse local distilled knowledge JSON into engine-level parameters."""

    ENV_PATH = "MATHART_KNOWLEDGE_JSON"

    def __init__(self, knowledge_path: str | Path | None = None, *, defaults: Mapping[str, Any] | None = None) -> None:
        self.knowledge_path = Path(knowledge_path) if knowledge_path else None
        self.defaults = dict(defaults or DEFAULT_KNOWLEDGE)

    def load_raw(self) -> tuple[dict[str, Any], str]:
        path = self.knowledge_path
        if path is None:
            env_path = os.environ.get(self.ENV_PATH)
            path = Path(env_path) if env_path else None

        merged = dict(self.defaults)
        if path and path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    merged.update(loaded)
                    return merged, str(path)
            except json.JSONDecodeError:
                return merged, f"mock_default_invalid_json:{path}"
        if path:
            return merged, f"mock_default_missing:{path}"
        return merged, "mock_default"

    def interpret(self) -> InterpretedKnowledge:
        raw, source = self.load_raw()
        timing = TimingParams(
            hit_stop_frames=_coerce_int(raw.get("hit_stop_frames"), DEFAULT_KNOWLEDGE["hit_stop_frames"], minimum=0, maximum=24),
            step_rate=_coerce_int(raw.get("step_rate"), DEFAULT_KNOWLEDGE["step_rate"], minimum=1, maximum=6),
            hit_stop_acceleration_drop=_coerce_float(raw.get("hit_stop_acceleration_drop"), DEFAULT_KNOWLEDGE["hit_stop_acceleration_drop"], minimum=0.0),
            hit_stop_min_previous_acceleration=_coerce_float(raw.get("hit_stop_min_previous_acceleration"), DEFAULT_KNOWLEDGE["hit_stop_min_previous_acceleration"], minimum=0.0),
            smooth_motion_velocity_threshold=_coerce_float(raw.get("smooth_motion_velocity_threshold"), DEFAULT_KNOWLEDGE["smooth_motion_velocity_threshold"], minimum=0.0),
            enabled=_coerce_bool(raw.get("timing_enabled", True), True),
        )
        physics = PhysicsParams(
            squash_velocity_threshold=_coerce_float(raw.get("squash_velocity_threshold"), DEFAULT_KNOWLEDGE["squash_velocity_threshold"], minimum=0.0),
            squash_acceleration_threshold=_coerce_float(raw.get("squash_acceleration_threshold"), 0.0, minimum=0.0),
            squash_max_stretch=_coerce_float(raw.get("squash_max_stretch"), DEFAULT_KNOWLEDGE["squash_max_stretch"], minimum=1.0, maximum=3.0),
            squash_velocity_to_stretch=_coerce_float(raw.get("squash_velocity_to_stretch"), DEFAULT_KNOWLEDGE["squash_velocity_to_stretch"], minimum=0.0),
            squash_acceleration_to_stretch=_coerce_float(raw.get("squash_acceleration_to_stretch"), DEFAULT_KNOWLEDGE["squash_acceleration_to_stretch"], minimum=0.0),
            anticipation_weight=_coerce_float(raw.get("anticipation_weight"), DEFAULT_KNOWLEDGE["anticipation_weight"], minimum=0.0),
            impact_reward_weight=_coerce_float(raw.get("impact_reward_weight"), DEFAULT_KNOWLEDGE["impact_reward_weight"], minimum=0.0),
        )
        style = StyleParams(
            toon_bands=_coerce_int(raw.get("toon_bands"), DEFAULT_KNOWLEDGE["toon_bands"], minimum=1, maximum=12),
            line_width=_coerce_float(raw.get("line_width"), DEFAULT_KNOWLEDGE["line_width"], minimum=0.0),
            shadow_hardness=_coerce_float(raw.get("shadow_hardness"), DEFAULT_KNOWLEDGE["shadow_hardness"], minimum=0.0, maximum=1.0),
            palette_color_count=_coerce_int(raw.get("palette_color_count"), DEFAULT_KNOWLEDGE["palette_color_count"], minimum=2, maximum=256),
            color_quantization_enabled=_coerce_bool(raw.get("color_quantization_enabled", True), True),
        )
        return InterpretedKnowledge(timing=timing, physics=physics, style=style, source_path=source, raw=raw)


def interpret_knowledge(knowledge_path: str | Path | None = None) -> InterpretedKnowledge:
    """Convenience entry point for export-layer modifiers."""

    return KnowledgeInterpreter(knowledge_path).interpret()


__all__ = [
    "DEFAULT_KNOWLEDGE",
    "TimingParams",
    "PhysicsParams",
    "StyleParams",
    "InterpretedKnowledge",
    "KnowledgeInterpreter",
    "interpret_knowledge",
]
