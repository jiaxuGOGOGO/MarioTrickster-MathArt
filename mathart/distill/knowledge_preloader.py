"""Knowledge Preloader — SESSION-076 (P1-DISTILL-3) Closed-Loop Asset Loader.

This module implements the **read side** of the distillation closed loop.
It loads the JSON knowledge assets produced by ``PhysicsGaitDistillationBackend``
and registers them into ``RuntimeDistillationBus`` as ``CompiledParameterSpace``
instances, so that downstream physics and gait systems consume distilled
parameters instead of hardcoded defaults.

Architecture discipline
-----------------------
- Follows the EA Frostbite Data-Driven Configuration pattern: knowledge
  assets are typed JSON files that the runtime loads at startup.
- The preloader is the **only** consumer of ``physics_gait_rules.json``.
  It translates the distilled Pareto-optimal configs into ``Constraint``
  objects and registers them as a ``ParameterSpace`` on the bus.
- This closes the "write → read" loop required by the task brief:
  ``PhysicsGaitDistillationBackend`` writes → ``knowledge_preloader`` reads
  → ``CompiledParameterSpace`` serves → physics/gait systems consume.

Red-line enforcement
--------------------
- NO blind writes: this module proves the knowledge file is actually read
  and its values override defaults in the compiled parameter space.
- The preloader is tested end-to-end in ``test_p1_distill_3.py``.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional, Sequence

from mathart.distill.compiler import Constraint, ParameterSpace
from mathart.distill.runtime_bus import (
    CompiledParameterSpace,
    RuntimeDistillationBus,
)

logger = logging.getLogger(__name__)

# Module name under which physics-gait distilled parameters are registered
PHYSICS_GAIT_MODULE = "physics_gait"

# Synonym mapping so downstream consumers can resolve distilled parameters
# by short alias names (e.g., "compliance_distance" instead of
# "physics_gait.compliance_distance").
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


def load_physics_gait_knowledge(
    knowledge_path: Path | str,
) -> dict[str, Any]:
    """Load and validate a physics-gait knowledge JSON asset.

    Parameters
    ----------
    knowledge_path : Path or str
        Path to ``physics_gait_rules.json``.

    Returns
    -------
    dict : The parsed knowledge asset.

    Raises
    ------
    FileNotFoundError
        If the knowledge file does not exist.
    ValueError
        If the knowledge file is malformed.
    """
    path = Path(knowledge_path)
    if not path.exists():
        raise FileNotFoundError(f"Knowledge file not found: {path}")

    data = json.loads(path.read_text(encoding="utf-8"))

    # Validate required fields
    required_fields = {"schema_version", "best_config", "parameter_space_constraints"}
    missing = required_fields - set(data.keys())
    if missing:
        raise ValueError(f"Knowledge file missing required fields: {missing}")

    return data


def build_parameter_space_from_knowledge(
    knowledge: dict[str, Any],
) -> ParameterSpace:
    """Build a ``ParameterSpace`` from a distilled knowledge asset.

    This converts the ``parameter_space_constraints`` section of the
    knowledge JSON into a proper ``ParameterSpace`` with ``Constraint``
    objects that ``CompiledParameterSpace`` can compile.

    Parameters
    ----------
    knowledge : dict
        Parsed knowledge asset from ``load_physics_gait_knowledge()``.

    Returns
    -------
    ParameterSpace
        Ready for registration on ``RuntimeDistillationBus``.
    """
    constraints_data = knowledge.get("parameter_space_constraints", {})
    best_config = knowledge.get("best_config", {})

    constraints: dict[str, Constraint] = {}
    for param_name, spec in constraints_data.items():
        constraint = Constraint(
            param_name=param_name,
            min_value=spec.get("min_value"),
            max_value=spec.get("max_value"),
            default_value=spec.get("default_value"),
            is_hard=spec.get("is_hard", False),
            source_rule_id=spec.get("source_rule_id", "distill_SESSION-076"),
        )
        constraints[param_name] = constraint

    # If constraints are empty but best_config exists, create point constraints
    if not constraints and best_config:
        for key, value in best_config.items():
            param_name = f"physics_gait.{key}"
            constraints[param_name] = Constraint(
                param_name=param_name,
                default_value=float(value),
                source_rule_id="distill_SESSION-076_best",
            )

    return ParameterSpace(
        name=PHYSICS_GAIT_MODULE,
        constraints=constraints,
    )


def register_physics_gait_knowledge(
    bus: RuntimeDistillationBus,
    knowledge_path: Path | str,
) -> CompiledParameterSpace:
    """Load knowledge and register it on the RuntimeDistillationBus.

    This is the primary entry point for the closed-loop preload.
    After calling this function, downstream consumers can resolve
    distilled parameters via:

        bus.resolve_scalar(["physics_gait.compliance_distance", "compliance_distance"], default=0.001)

    Parameters
    ----------
    bus : RuntimeDistillationBus
        The runtime bus to register the knowledge on.
    knowledge_path : Path or str
        Path to ``physics_gait_rules.json``.

    Returns
    -------
    CompiledParameterSpace
        The compiled parameter space registered on the bus.
    """
    knowledge = load_physics_gait_knowledge(knowledge_path)
    space = build_parameter_space_from_knowledge(knowledge)
    compiled = bus.register_space(PHYSICS_GAIT_MODULE, space)

    logger.info(
        "Registered physics-gait knowledge: %d parameters, best_fitness=%.4f",
        compiled.dimensions,
        knowledge.get("pareto_frontier", [{}])[0].get("fitness", {}).get("combined", -1.0)
        if knowledge.get("pareto_frontier") else -1.0,
    )
    return compiled


def preload_all_distilled_knowledge(
    bus: RuntimeDistillationBus,
    knowledge_dir: Path | str | None = None,
) -> dict[str, CompiledParameterSpace]:
    """Preload all distilled knowledge assets from the knowledge directory.

    This function scans for known distilled JSON assets and registers
    them on the bus.  It is designed to be called at application startup
    after ``bus.refresh_from_knowledge()`` to layer distilled overrides
    on top of the base knowledge rules.

    Parameters
    ----------
    bus : RuntimeDistillationBus
        The runtime bus.
    knowledge_dir : Path or str or None
        Override knowledge directory.  Defaults to ``bus.knowledge_dir``.

    Returns
    -------
    dict : Mapping from module name to compiled parameter space.
    """
    kdir = Path(knowledge_dir) if knowledge_dir else bus.knowledge_dir
    loaded: dict[str, CompiledParameterSpace] = {}

    # Physics-gait distillation asset
    physics_gait_path = kdir / "physics_gait_rules.json"
    if physics_gait_path.exists():
        try:
            compiled = register_physics_gait_knowledge(bus, physics_gait_path)
            loaded[PHYSICS_GAIT_MODULE] = compiled
        except Exception as exc:
            logger.warning("Failed to preload physics-gait knowledge: %s", exc)

    # Future: add more distilled knowledge loaders here
    # e.g., cognitive_science_rules.json for P1-DISTILL-4

    return loaded


# ── Synonym registration hook ────────────────────────────────────────────

def inject_physics_gait_synonyms() -> None:
    """Inject physics-gait parameter synonyms into the runtime bus synonym table.

    This must be called before any ``CompiledParameterSpace`` is built
    for the ``physics_gait`` module, so that alias resolution works.
    """
    from mathart.distill.runtime_bus import _RUNTIME_PARAM_SYNONYMS
    for canonical, aliases in _PHYSICS_GAIT_SYNONYMS.items():
        if canonical not in _RUNTIME_PARAM_SYNONYMS:
            _RUNTIME_PARAM_SYNONYMS[canonical] = aliases


# Auto-inject synonyms on import
inject_physics_gait_synonyms()


__all__ = [
    "PHYSICS_GAIT_MODULE",
    "build_parameter_space_from_knowledge",
    "inject_physics_gait_synonyms",
    "load_physics_gait_knowledge",
    "preload_all_distilled_knowledge",
    "register_physics_gait_knowledge",
]
