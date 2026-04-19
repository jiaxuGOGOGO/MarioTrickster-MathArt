"""Runtime preload helpers for distilled JSON knowledge assets.

This module implements the **read side** of the distillation closed loop.
It loads JSON knowledge assets written by registry-native distillation backends
and registers them into ``RuntimeDistillationBus`` as ``CompiledParameterSpace``
instances, so downstream systems consume distilled parameters instead of
hardcoded defaults.

Architecture discipline
-----------------------
- Follows the EA Frostbite data-driven configuration pattern: knowledge assets
  are typed JSON files that the runtime loads at startup.
- The preloader is the **only** consumer of the distilled JSON assets written by
  P1-DISTILL-3 and P1-DISTILL-4.
- This closes the required write → read loop:
  ``distill backend`` writes → ``knowledge_preloader`` reads →
  ``CompiledParameterSpace`` serves → runtime consumers resolve distilled values.

Red-line enforcement
--------------------
- No blind writes: this module proves that knowledge files are actually read
  and their values override defaults in the compiled parameter space.
- Physics and cognitive namespaces coexist on the same runtime bus without
  overwriting one another.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from mathart.distill.compiler import Constraint, ParameterSpace
from mathart.distill.runtime_bus import CompiledParameterSpace, RuntimeDistillationBus

logger = logging.getLogger(__name__)

PHYSICS_GAIT_MODULE = "physics_gait"
COGNITIVE_MOTION_MODULE = "cognitive_motion"
TRANSIENT_MOTION_MODULE = "transient_motion"

_PHYSICS_GAIT_SYNONYMS: dict[str, tuple[str, ...]] = {
    "physics_gait.compliance_distance": (
        "compliance_distance",
        "xpbd_compliance_distance",
        "distilled_compliance_distance",
    ),
    "physics_gait.compliance_bending": (
        "compliance_bending",
        "xpbd_compliance_bending",
        "distilled_compliance_bending",
    ),
    "physics_gait.damping": (
        "damping",
        "xpbd_damping",
        "velocity_damping",
        "distilled_damping",
    ),
    "physics_gait.sub_steps": (
        "sub_steps",
        "xpbd_sub_steps",
        "substeps",
        "distilled_sub_steps",
    ),
    "physics_gait.blend_time": (
        "blend_time",
        "gait_blend_time",
        "transition_blend_time",
        "distilled_blend_time",
    ),
    "physics_gait.phase_weight": (
        "phase_weight",
        "gait_phase_weight",
        "phase_alignment_weight",
        "distilled_phase_weight",
    ),
}

_COGNITIVE_MOTION_SYNONYMS: dict[str, tuple[str, ...]] = {
    "cognitive_motion.anticipation_bias": (
        "anticipation_bias",
        "cognitive_anticipation_bias",
        "distilled_anticipation_bias",
    ),
    "cognitive_motion.phase_salience": (
        "phase_salience",
        "deepphase_phase_salience",
        "distilled_phase_salience",
    ),
    "cognitive_motion.jerk_tolerance": (
        "jerk_tolerance",
        "biomotion_jerk_tolerance",
        "distilled_jerk_tolerance",
    ),
    "cognitive_motion.contact_expectation_weight": (
        "contact_expectation_weight",
        "cognitive_contact_expectation_weight",
        "distilled_contact_expectation_weight",
    ),
}

_TRANSIENT_MOTION_SYNONYMS: dict[str, tuple[str, ...]] = {
    "transient_motion.recovery_half_life": (
        "recovery_half_life",
        "transition_recovery_half_life",
        "hit_recovery_half_life",
        "distilled_recovery_half_life",
    ),
    "transient_motion.impact_damping_weight": (
        "impact_damping_weight",
        "landing_impact_damping_weight",
        "transition_impact_damping_weight",
        "distilled_impact_damping_weight",
    ),
    "transient_motion.landing_anticipation_window": (
        "landing_anticipation_window",
        "anticipation_window",
        "landing_buffer_window",
        "distilled_landing_anticipation_window",
    ),
    "transient_motion.peak_residual_threshold": (
        "peak_residual_threshold",
        "transition_peak_residual_threshold",
        "distilled_peak_residual_threshold",
    ),
    "transient_motion.frames_to_stability_threshold": (
        "frames_to_stability_threshold",
        "transition_frames_to_stability_threshold",
        "distilled_frames_to_stability_threshold",
    ),
    "transient_motion.peak_jerk_threshold": (
        "peak_jerk_threshold",
        "transition_peak_jerk_threshold",
        "distilled_peak_jerk_threshold",
    ),
    "transient_motion.peak_root_velocity_delta_threshold": (
        "peak_root_velocity_delta_threshold",
        "transition_peak_root_velocity_delta_threshold",
        "distilled_peak_root_velocity_delta_threshold",
    ),
    "transient_motion.peak_pose_gap_threshold": (
        "peak_pose_gap_threshold",
        "transition_peak_pose_gap_threshold",
        "distilled_peak_pose_gap_threshold",
    ),
}


def _load_knowledge_asset(knowledge_path: Path | str) -> dict[str, Any]:
    path = Path(knowledge_path)
    if not path.exists():
        raise FileNotFoundError(f"Knowledge file not found: {path}")

    data = json.loads(path.read_text(encoding="utf-8"))
    required_fields = {"schema_version", "best_config", "parameter_space_constraints"}
    missing = required_fields - set(data.keys())
    if missing:
        raise ValueError(f"Knowledge file missing required fields: {missing}")
    return data


def _build_parameter_space_from_knowledge(
    knowledge: dict[str, Any],
    *,
    module_name: str,
) -> ParameterSpace:
    constraints_data = knowledge.get("parameter_space_constraints", {})
    best_config = knowledge.get("best_config", {})

    constraints: dict[str, Constraint] = {}
    for param_name, spec in constraints_data.items():
        constraints[param_name] = Constraint(
            param_name=param_name,
            min_value=spec.get("min_value"),
            max_value=spec.get("max_value"),
            default_value=spec.get("default_value"),
            is_hard=spec.get("is_hard", False),
            source_rule_id=spec.get("source_rule_id", f"distill_{module_name}"),
        )

    if not constraints and best_config:
        for key, value in best_config.items():
            param_name = f"{module_name}.{key}"
            constraints[param_name] = Constraint(
                param_name=param_name,
                default_value=float(value),
                source_rule_id=f"distill_{module_name}_best",
            )

    return ParameterSpace(name=module_name, constraints=constraints)


def load_physics_gait_knowledge(knowledge_path: Path | str) -> dict[str, Any]:
    return _load_knowledge_asset(knowledge_path)


def load_cognitive_science_knowledge(knowledge_path: Path | str) -> dict[str, Any]:
    return _load_knowledge_asset(knowledge_path)


def build_parameter_space_from_knowledge(knowledge: dict[str, Any]) -> ParameterSpace:
    return _build_parameter_space_from_knowledge(knowledge, module_name=PHYSICS_GAIT_MODULE)


def build_cognitive_parameter_space_from_knowledge(knowledge: dict[str, Any]) -> ParameterSpace:
    return _build_parameter_space_from_knowledge(knowledge, module_name=COGNITIVE_MOTION_MODULE)


def build_transient_parameter_space_from_knowledge(knowledge: dict[str, Any]) -> ParameterSpace:
    return _build_parameter_space_from_knowledge(knowledge, module_name=TRANSIENT_MOTION_MODULE)


def register_physics_gait_knowledge(
    bus: RuntimeDistillationBus,
    knowledge_path: Path | str,
) -> CompiledParameterSpace:
    knowledge = load_physics_gait_knowledge(knowledge_path)
    space = build_parameter_space_from_knowledge(knowledge)
    compiled = bus.register_space(PHYSICS_GAIT_MODULE, space)
    logger.info(
        "Registered physics-gait knowledge: %d parameters",
        compiled.dimensions,
    )
    return compiled


def register_cognitive_science_knowledge(
    bus: RuntimeDistillationBus,
    knowledge_path: Path | str,
) -> CompiledParameterSpace:
    knowledge = load_cognitive_science_knowledge(knowledge_path)
    space = build_cognitive_parameter_space_from_knowledge(knowledge)
    compiled = bus.register_space(COGNITIVE_MOTION_MODULE, space)
    logger.info(
        "Registered cognitive-motion knowledge: %d parameters",
        compiled.dimensions,
    )
    return compiled


def load_transient_motion_knowledge(knowledge_path: Path | str) -> dict[str, Any]:
    return _load_knowledge_asset(knowledge_path)


def register_transient_motion_knowledge(
    bus: RuntimeDistillationBus,
    knowledge_path: Path | str,
) -> CompiledParameterSpace:
    knowledge = load_transient_motion_knowledge(knowledge_path)
    space = build_transient_parameter_space_from_knowledge(knowledge)
    compiled = bus.register_space(TRANSIENT_MOTION_MODULE, space)
    logger.info(
        "Registered transient-motion knowledge: %d parameters",
        compiled.dimensions,
    )
    return compiled


def preload_all_distilled_knowledge(
    bus: RuntimeDistillationBus,
    knowledge_dir: Path | str | None = None,
) -> dict[str, CompiledParameterSpace]:
    kdir = Path(knowledge_dir) if knowledge_dir else bus.knowledge_dir
    loaded: dict[str, CompiledParameterSpace] = {}

    physics_gait_path = kdir / "physics_gait_rules.json"
    if physics_gait_path.exists():
        try:
            loaded[PHYSICS_GAIT_MODULE] = register_physics_gait_knowledge(bus, physics_gait_path)
        except Exception as exc:
            logger.warning("Failed to preload physics-gait knowledge: %s", exc)

    cognitive_path = kdir / "cognitive_science_rules.json"
    if cognitive_path.exists():
        try:
            loaded[COGNITIVE_MOTION_MODULE] = register_cognitive_science_knowledge(bus, cognitive_path)
        except Exception as exc:
            logger.warning("Failed to preload cognitive-motion knowledge: %s", exc)

    transient_path = kdir / "transient_motion_rules.json"
    if transient_path.exists():
        try:
            loaded[TRANSIENT_MOTION_MODULE] = register_transient_motion_knowledge(bus, transient_path)
        except Exception as exc:
            logger.warning("Failed to preload transient-motion knowledge: %s", exc)

    return loaded


def inject_physics_gait_synonyms() -> None:
    from mathart.distill.runtime_bus import _RUNTIME_PARAM_SYNONYMS

    for canonical, aliases in _PHYSICS_GAIT_SYNONYMS.items():
        if canonical not in _RUNTIME_PARAM_SYNONYMS:
            _RUNTIME_PARAM_SYNONYMS[canonical] = aliases


def inject_cognitive_motion_synonyms() -> None:
    from mathart.distill.runtime_bus import _RUNTIME_PARAM_SYNONYMS

    for canonical, aliases in _COGNITIVE_MOTION_SYNONYMS.items():
        if canonical not in _RUNTIME_PARAM_SYNONYMS:
            _RUNTIME_PARAM_SYNONYMS[canonical] = aliases


def inject_transient_motion_synonyms() -> None:
    from mathart.distill.runtime_bus import _RUNTIME_PARAM_SYNONYMS

    for canonical, aliases in _TRANSIENT_MOTION_SYNONYMS.items():
        if canonical not in _RUNTIME_PARAM_SYNONYMS:
            _RUNTIME_PARAM_SYNONYMS[canonical] = aliases


inject_physics_gait_synonyms()
inject_cognitive_motion_synonyms()
inject_transient_motion_synonyms()


__all__ = [
    "PHYSICS_GAIT_MODULE",
    "COGNITIVE_MOTION_MODULE",
    "TRANSIENT_MOTION_MODULE",
    "build_parameter_space_from_knowledge",
    "build_cognitive_parameter_space_from_knowledge",
    "build_transient_parameter_space_from_knowledge",
    "inject_physics_gait_synonyms",
    "inject_cognitive_motion_synonyms",
    "inject_transient_motion_synonyms",
    "load_physics_gait_knowledge",
    "load_cognitive_science_knowledge",
    "load_transient_motion_knowledge",
    "preload_all_distilled_knowledge",
    "register_physics_gait_knowledge",
    "register_cognitive_science_knowledge",
    "register_transient_motion_knowledge",
]
