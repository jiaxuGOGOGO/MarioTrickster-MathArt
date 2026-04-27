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
    "fluid_fitness_weight": 0.35,
    "cloth_fitness_weight": 0.30,
    "line_width": 1.0,
    "shadow_hardness": 0.75,
    "palette_color_count": 16,
    "fluid_metaball_resolution": 0.18,
    "fluid_render_resolution": 0.08,
    "fluid_glow_intensity": 1.8,
    "fluid_particle_radius": 0.055,
    "fluid_splash_spread_target": 0.42,
    "fluid_particle_count_target": 28,
    "fluid_emit_rate": 12,
    "fluid_grid_size": 36,
    "fluid_density_gain": 1.15,
    "fluid_velocity_scale": 22.0,
    "fluid_enabled": True,
    "cloth_damping": 0.72,
    "cloth_weight": 0.15,
    "cloth_bend_stiffness": 0.0005,
    "cloth_flutter_target": 0.18,
    "cloth_segment_count": 6,
    "cloth_segment_length": 0.126,
    "cloth_enabled": True,
    "wfc_platform_spacing": 4.5,
    "vertical_bias": 0.8,
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
    fluid_fitness_weight: float = 0.35
    cloth_fitness_weight: float = 0.30

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FluidParams:
    """Book-distilled controls for V6 metaball fluid simulation and rendering."""

    enabled: bool = True
    metaball_resolution: float = 0.18
    render_resolution: float = 0.08
    glow_intensity: float = 1.8
    particle_radius: float = 0.055
    splash_spread_target: float = 0.42
    particle_count_target: int = 28
    emit_rate: int = 12
    grid_size: int = 36
    density_gain: float = 1.15
    velocity_scale: float = 22.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ClothParams:
    """Book-distilled controls for V6 XPBD cloth/cape simulation and rendering."""

    enabled: bool = True
    damping: float = 0.72
    weight: float = 0.15
    bend_stiffness: float = 0.0005
    flutter_target: float = 0.18
    segment_count: int = 6
    segment_length: float = 0.126

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EnvironmentParams:
    """Book-distilled controls for WFC level spacing and verticality bias."""

    wfc_platform_spacing: float = 4.5
    vertical_bias: float = 0.8

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EffectActivationParams:
    """Semantic bus switches inferred from vibe and distilled book theory."""

    fluid_vfx: bool = True
    cloth_xpbd: bool = True
    terrain_ik: bool = True
    active_vfx_plugins: tuple[str, ...] = ()
    semantic_reason: str = "knowledge_default"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["active_vfx_plugins"] = list(self.active_vfx_plugins)
        return data


@dataclass(frozen=True)
class StyleParams:
    """Low-level Blender/compositor/toon controls distilled from art books."""

    toon_bands: int = 3
    line_width: float = 1.0
    shadow_hardness: float = 0.75
    palette_color_count: int = 16
    color_quantization_enabled: bool = True
    oklab_color_palette: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["oklab_color_palette"] = list(self.oklab_color_palette or [])
        return data


@dataclass(frozen=True)
class InterpretedKnowledge:
    """Structured output of the knowledge translation center."""

    timing: TimingParams
    physics: PhysicsParams
    style: StyleParams
    fluid: FluidParams
    cloth: ClothParams
    environment: EnvironmentParams
    effects: EffectActivationParams
    source_path: str = "mock_default"
    raw: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "TimingParams": self.timing.to_dict(),
            "PhysicsParams": self.physics.to_dict(),
            "StyleParams": self.style.to_dict(),
            "FluidParams": self.fluid.to_dict(),
            "ClothParams": self.cloth.to_dict(),
            "EnvironmentParams": self.environment.to_dict(),
            "EffectActivationParams": self.effects.to_dict(),
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


def _coerce_hex_palette(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    palette: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        text = item.strip().upper()
        if len(text) == 7 and text.startswith("#") and all(ch in "0123456789ABCDEF" for ch in text[1:]):
            palette.append(text)
    return palette


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
        timing_raw = raw.get("TimingParams") if isinstance(raw.get("TimingParams"), Mapping) else {}
        physics_raw = raw.get("PhysicsParams") if isinstance(raw.get("PhysicsParams"), Mapping) else {}
        style_raw = raw.get("StyleParams") if isinstance(raw.get("StyleParams"), Mapping) else {}
        fluid_raw = raw.get("FluidParams") if isinstance(raw.get("FluidParams"), Mapping) else raw.get("fluid") if isinstance(raw.get("fluid"), Mapping) else {}
        cloth_raw = raw.get("ClothParams") if isinstance(raw.get("ClothParams"), Mapping) else raw.get("cloth") if isinstance(raw.get("cloth"), Mapping) else {}
        environment_raw = raw.get("EnvironmentParams") if isinstance(raw.get("EnvironmentParams"), Mapping) else raw.get("environment") if isinstance(raw.get("environment"), Mapping) else {}
        effects_raw = raw.get("effects") if isinstance(raw.get("effects"), Mapping) else {}

        def pick(name: str, nested: Mapping[str, Any], nested_name: str | None = None) -> Any:
            key = nested_name or name
            return nested.get(key, raw.get(name))

        def pick_from(section: Mapping[str, Any], name: str, default: Any) -> Any:
            return section.get(name, raw.get(name, default))

        timing = TimingParams(
            hit_stop_frames=_coerce_int(pick_from(timing_raw, "hit_stop_frames", DEFAULT_KNOWLEDGE["hit_stop_frames"]), DEFAULT_KNOWLEDGE["hit_stop_frames"], minimum=0, maximum=24),
            step_rate=_coerce_int(pick_from(timing_raw, "step_rate", DEFAULT_KNOWLEDGE["step_rate"]), DEFAULT_KNOWLEDGE["step_rate"], minimum=1, maximum=6),
            hit_stop_acceleration_drop=_coerce_float(raw.get("hit_stop_acceleration_drop"), DEFAULT_KNOWLEDGE["hit_stop_acceleration_drop"], minimum=0.0),
            hit_stop_min_previous_acceleration=_coerce_float(raw.get("hit_stop_min_previous_acceleration"), DEFAULT_KNOWLEDGE["hit_stop_min_previous_acceleration"], minimum=0.0),
            smooth_motion_velocity_threshold=_coerce_float(raw.get("smooth_motion_velocity_threshold"), DEFAULT_KNOWLEDGE["smooth_motion_velocity_threshold"], minimum=0.0),
            enabled=_coerce_bool(raw.get("timing_enabled", True), True),
        )
        physics = PhysicsParams(
            squash_velocity_threshold=_coerce_float(raw.get("squash_velocity_threshold"), DEFAULT_KNOWLEDGE["squash_velocity_threshold"], minimum=0.0),
            squash_acceleration_threshold=_coerce_float(raw.get("squash_acceleration_threshold"), 0.0, minimum=0.0),
            squash_max_stretch=_coerce_float(pick_from(physics_raw, "squash_max_stretch", DEFAULT_KNOWLEDGE["squash_max_stretch"]), DEFAULT_KNOWLEDGE["squash_max_stretch"], minimum=1.0, maximum=3.0),
            squash_velocity_to_stretch=_coerce_float(raw.get("squash_velocity_to_stretch"), DEFAULT_KNOWLEDGE["squash_velocity_to_stretch"], minimum=0.0),
            squash_acceleration_to_stretch=_coerce_float(raw.get("squash_acceleration_to_stretch"), DEFAULT_KNOWLEDGE["squash_acceleration_to_stretch"], minimum=0.0),
            anticipation_weight=_coerce_float(pick_from(physics_raw, "anticipation_weight", DEFAULT_KNOWLEDGE["anticipation_weight"]), DEFAULT_KNOWLEDGE["anticipation_weight"], minimum=0.0),
            impact_reward_weight=_coerce_float(pick_from(physics_raw, "impact_reward_weight", DEFAULT_KNOWLEDGE["impact_reward_weight"]), DEFAULT_KNOWLEDGE["impact_reward_weight"], minimum=0.0),
            fluid_fitness_weight=_coerce_float(raw.get("fluid_fitness_weight"), DEFAULT_KNOWLEDGE["fluid_fitness_weight"], minimum=0.0),
            cloth_fitness_weight=_coerce_float(raw.get("cloth_fitness_weight"), DEFAULT_KNOWLEDGE["cloth_fitness_weight"], minimum=0.0),
        )
        style = StyleParams(
            toon_bands=_coerce_int(pick_from(style_raw, "toon_bands", DEFAULT_KNOWLEDGE["toon_bands"]), DEFAULT_KNOWLEDGE["toon_bands"], minimum=1, maximum=12),
            line_width=_coerce_float(raw.get("line_width"), DEFAULT_KNOWLEDGE["line_width"], minimum=0.0),
            shadow_hardness=_coerce_float(pick_from(style_raw, "shadow_hardness", DEFAULT_KNOWLEDGE["shadow_hardness"]), DEFAULT_KNOWLEDGE["shadow_hardness"], minimum=0.0, maximum=1.0),
            palette_color_count=_coerce_int(raw.get("palette_color_count"), DEFAULT_KNOWLEDGE["palette_color_count"], minimum=2, maximum=256),
            color_quantization_enabled=_coerce_bool(raw.get("color_quantization_enabled", True), True),
            oklab_color_palette=_coerce_hex_palette(style_raw.get("oklab_color_palette", style_raw.get("oklab_palette", raw.get("oklab_color_palette", raw.get("oklab_palette", []))))),
        )
        fluid = FluidParams(
            enabled=_coerce_bool(pick("fluid_enabled", fluid_raw, "enabled"), DEFAULT_KNOWLEDGE["fluid_enabled"]),
            metaball_resolution=_coerce_float(fluid_raw.get("fluid_resolution", pick("fluid_metaball_resolution", fluid_raw, "resolution")), DEFAULT_KNOWLEDGE["fluid_metaball_resolution"], minimum=0.01, maximum=2.0),
            render_resolution=_coerce_float(pick("fluid_render_resolution", fluid_raw, "render_resolution"), DEFAULT_KNOWLEDGE["fluid_render_resolution"], minimum=0.01, maximum=2.0),
            glow_intensity=_coerce_float(fluid_raw.get("emission_strength", pick("fluid_glow_intensity", fluid_raw, "glow_intensity")), DEFAULT_KNOWLEDGE["fluid_glow_intensity"], minimum=0.0, maximum=20.0),
            particle_radius=_coerce_float(pick("fluid_particle_radius", fluid_raw, "particle_radius"), DEFAULT_KNOWLEDGE["fluid_particle_radius"], minimum=0.005, maximum=1.0),
            splash_spread_target=_coerce_float(pick("fluid_splash_spread_target", fluid_raw, "splash_spread_target"), DEFAULT_KNOWLEDGE["fluid_splash_spread_target"], minimum=0.0, maximum=2.0),
            particle_count_target=_coerce_int(pick("fluid_particle_count_target", fluid_raw, "particle_count_target"), DEFAULT_KNOWLEDGE["fluid_particle_count_target"], minimum=1, maximum=512),
            emit_rate=_coerce_int(pick("fluid_emit_rate", fluid_raw, "emit_rate"), DEFAULT_KNOWLEDGE["fluid_emit_rate"], minimum=1, maximum=512),
            grid_size=_coerce_int(pick("fluid_grid_size", fluid_raw, "grid_size"), DEFAULT_KNOWLEDGE["fluid_grid_size"], minimum=8, maximum=256),
            density_gain=_coerce_float(pick("fluid_density_gain", fluid_raw, "density_gain"), DEFAULT_KNOWLEDGE["fluid_density_gain"], minimum=0.0, maximum=10.0),
            velocity_scale=_coerce_float(pick("fluid_velocity_scale", fluid_raw, "velocity_scale"), DEFAULT_KNOWLEDGE["fluid_velocity_scale"], minimum=0.0, maximum=100.0),
        )
        cloth = ClothParams(
            enabled=_coerce_bool(pick("cloth_enabled", cloth_raw, "enabled"), DEFAULT_KNOWLEDGE["cloth_enabled"]),
            damping=_coerce_float(cloth_raw.get("cloth_damping", pick("cloth_damping", cloth_raw, "damping")), DEFAULT_KNOWLEDGE["cloth_damping"], minimum=0.0, maximum=1.0),
            weight=_coerce_float(pick("cloth_weight", cloth_raw, "weight"), DEFAULT_KNOWLEDGE["cloth_weight"], minimum=0.001, maximum=10.0),
            bend_stiffness=_coerce_float(cloth_raw.get("cloth_stiffness", pick("cloth_bend_stiffness", cloth_raw, "stiffness")), DEFAULT_KNOWLEDGE["cloth_bend_stiffness"], minimum=0.0, maximum=1.0),
            flutter_target=_coerce_float(pick("cloth_flutter_target", cloth_raw, "flutter_target"), DEFAULT_KNOWLEDGE["cloth_flutter_target"], minimum=0.0, maximum=2.0),
            segment_count=_coerce_int(pick("cloth_segment_count", cloth_raw, "segment_count"), DEFAULT_KNOWLEDGE["cloth_segment_count"], minimum=3, maximum=32),
            segment_length=_coerce_float(pick("cloth_segment_length", cloth_raw, "segment_length"), DEFAULT_KNOWLEDGE["cloth_segment_length"], minimum=0.01, maximum=2.0),
        )
        environment = EnvironmentParams(
            wfc_platform_spacing=_coerce_float(environment_raw.get("wfc_platform_spacing", raw.get("wfc_platform_spacing", DEFAULT_KNOWLEDGE["wfc_platform_spacing"])), DEFAULT_KNOWLEDGE["wfc_platform_spacing"], minimum=0.0, maximum=64.0),
            vertical_bias=_coerce_float(environment_raw.get("vertical_bias", raw.get("vertical_bias", DEFAULT_KNOWLEDGE["vertical_bias"])), DEFAULT_KNOWLEDGE["vertical_bias"], minimum=0.0, maximum=1.0),
        )
        plugins = effects_raw.get("active_vfx_plugins", raw.get("active_vfx_plugins", ()))
        if not isinstance(plugins, (list, tuple)):
            plugins = ()
        effects = EffectActivationParams(
            fluid_vfx=_coerce_bool(effects_raw.get("fluid_vfx", raw.get("activate_fluid_vfx", fluid.enabled)), fluid.enabled),
            cloth_xpbd=_coerce_bool(effects_raw.get("cloth_xpbd", raw.get("activate_cloth_xpbd", cloth.enabled)), cloth.enabled),
            terrain_ik=_coerce_bool(effects_raw.get("terrain_ik", raw.get("activate_terrain_ik", True)), True),
            active_vfx_plugins=tuple(str(p) for p in plugins),
            semantic_reason=str(effects_raw.get("semantic_reason", raw.get("semantic_reason", "knowledge_default"))),
        )
        return InterpretedKnowledge(timing=timing, physics=physics, style=style, fluid=fluid, cloth=cloth, environment=environment, effects=effects, source_path=source, raw=raw)


def interpret_knowledge(knowledge_path: str | Path | None = None) -> InterpretedKnowledge:
    """Convenience entry point for export-layer modifiers."""

    return KnowledgeInterpreter(knowledge_path).interpret()


__all__ = [
    "DEFAULT_KNOWLEDGE",
    "TimingParams",
    "PhysicsParams",
    "FluidParams",
    "ClothParams",
    "EnvironmentParams",
    "EffectActivationParams",
    "StyleParams",
    "InterpretedKnowledge",
    "KnowledgeInterpreter",
    "interpret_knowledge",
]
