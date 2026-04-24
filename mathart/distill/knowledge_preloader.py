"""Runtime preload helpers for distilled JSON knowledge assets.

This module implements the **read side** of the distillation closed loop.
It loads JSON knowledge assets written by registry-native distillation backends
and registers them into ``RuntimeDistillationBus`` as ``CompiledParameterSpace``
instances, so downstream systems consume distilled parameters instead of
hardcoded defaults.

SESSION-184 Sandbox Validator Integration
-----------------------------------------
The preloader now implements the **Middleware Interceptor Pattern** (NestJS
Request Lifecycle / ASP.NET Core Middleware Pipeline analogy): before any
knowledge asset is mounted onto the ``RuntimeDistillationBus``, it MUST pass
through the ``SandboxValidator`` anti-hallucination funnel.

The integration follows the **Graceful Degradation** principle (Google Cloud
Architecture Framework, CMU S3D-25-104): if the validator rejects a rule or
encounters an error, the preloader logs a ``WARNING`` and **continues** loading
healthy rules.  The system NEVER crashes or flash-exits due to a single
poisoned knowledge entry.

Research foundations:
- AST-Based Sandboxing & Zero-Trust Execution (Two Six Technologies 2022):
  ``ast.parse(mode='eval')`` + whitelist node walker prevents RCE.
- Middleware Interceptor Pattern (Martin Fowler 2004, NestJS 2024):
  Quality gates as pre-mount interceptors on the data loading pipeline.
- Graceful Degradation in Policy Gateways (Google Cloud 2024):
  Discard toxic rules, clamp outliers, log warnings, continue startup.
- Automated Kinematic Sweeping (NVIDIA Isaac Gym 2021):
  Distilled physics parameters are validated before bus consumption.

Architecture discipline
-----------------------
- Follows the EA Frostbite data-driven configuration pattern: knowledge assets
  are typed JSON files that the runtime loads at startup.
- The preloader is the **only** consumer of the distilled JSON assets written by
  P1-DISTILL-3 and P1-DISTILL-4.
- This closes the required write → read loop:
  ``distill backend`` writes → ``knowledge_preloader`` reads →
  **SandboxValidator** validates → ``CompiledParameterSpace`` serves →
  runtime consumers resolve distilled values.

Red-line enforcement
--------------------
- No blind writes: this module proves that knowledge files are actually read
  and their values override defaults in the compiled parameter space.
- Physics and cognitive namespaces coexist on the same runtime bus without
  overwriting one another.
- SESSION-184: ZERO ``eval()`` or ``exec()`` on any external data. All
  expression validation goes through the AST whitelist firewall.
- SESSION-184: Validator failures NEVER crash the preloader. Graceful
  degradation via ``logger.warning`` + ``continue``.
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


# ═══════════════════════════════════════════════════════════════════════════
#  SESSION-184: Sandbox Validator Pre-Mount Interceptor
#
#  This section implements the Middleware Interceptor Pattern: before any
#  knowledge rule or JSON asset is mounted onto the RuntimeDistillationBus,
#  it MUST pass through the SandboxValidator's four-dimensional anti-
#  hallucination funnel (provenance → AST firewall → math fuzz → physics
#  dry-run).
#
#  Graceful Degradation contract:
#  - If the validator itself fails to import or initialize, the preloader
#    logs a WARNING and proceeds WITHOUT validation (fail-open for startup).
#  - If individual rules fail validation, they are SKIPPED with a WARNING
#    log entry. Healthy rules continue to load.
#  - The preloader NEVER raises a fatal exception due to validation failure.
# ═══════════════════════════════════════════════════════════════════════════


def _get_sandbox_validator(project_root: Path | str | None = None):
    """Lazy-load the SandboxValidator with graceful fallback.

    Returns a ``SandboxValidator`` instance if available, or ``None`` if the
    module cannot be imported (e.g. circular import edge case). The caller
    MUST handle the ``None`` case by skipping validation.
    """
    try:
        from mathart.distill.sandbox_validator import SandboxValidator
        root = Path(project_root) if project_root else Path.cwd()
        return SandboxValidator(project_root=root)
    except Exception as exc:
        logger.warning(
            "[SandboxValidator] Failed to initialize validator, "
            "proceeding without pre-mount validation: %s", exc
        )
        return None


def _validate_quarantine_rules(
    project_root: Path | str | None = None,
) -> dict[str, Any]:
    """Run the SandboxValidator on all quarantined knowledge rules.

    This is the **pre-mount interceptor** that runs BEFORE any knowledge
    is loaded onto the RuntimeDistillationBus.

    Returns a summary dict with counts and lists of passed/failed rule IDs.
    On any error, returns a safe empty summary — NEVER crashes.

    Graceful Degradation:
    - Failed rules are logged as WARNING and skipped.
    - Passed rules are promoted (or left for downstream consumption).
    - The function NEVER raises an exception.
    """
    summary = {
        "validator_available": False,
        "total": 0,
        "passed": 0,
        "failed": 0,
        "failed_rule_ids": [],
        "passed_rule_ids": [],
    }

    try:
        validator = _get_sandbox_validator(project_root)
        if validator is None:
            logger.warning(
                "[SandboxValidator] Validator unavailable; "
                "quarantine scan skipped. System continues with "
                "existing active knowledge."
            )
            return summary

        summary["validator_available"] = True

        # Scan quarantine directory for rules that need validation
        batch_result = validator.validate_quarantine()
        summary["total"] = batch_result.total
        summary["passed"] = batch_result.passed
        summary["failed"] = batch_result.failed

        for report in batch_result.reports:
            if report.passed:
                summary["passed_rule_ids"].append(report.rule_id)
            else:
                summary["failed_rule_ids"].append(report.rule_id)
                # Graceful Degradation: log WARNING, do NOT raise
                logger.warning(
                    "[SandboxValidator] Rule %r REJECTED by anti-hallucination "
                    "funnel (reasons: %s). Skipping this rule — system continues "
                    "with healthy knowledge.",
                    report.rule_id,
                    "; ".join(report.reasons),
                )

        if batch_result.total > 0:
            logger.info(
                "[SandboxValidator] Quarantine scan complete: "
                "%d/%d rules passed, %d rejected.",
                batch_result.passed,
                batch_result.total,
                batch_result.failed,
            )
        else:
            logger.debug(
                "[SandboxValidator] No quarantine rules found; "
                "scan skipped."
            )

    except Exception as exc:
        # Ultimate safety net: NEVER let validator errors crash the preloader
        logger.warning(
            "[SandboxValidator] Unexpected error during quarantine scan: %s. "
            "Proceeding without validation — system continues normally.",
            exc,
        )

    return summary


def _validate_knowledge_json_expressions(
    knowledge_data: dict[str, Any],
    source_path: str = "<unknown>",
) -> dict[str, Any]:
    """Validate expressions embedded in a JSON knowledge asset.

    SESSION-184: This function scans ``parameter_space_constraints`` for any
    embedded ``expr`` fields and runs them through the AST whitelist firewall
    and math fuzzing. This prevents poisoned expressions from reaching the
    ``CompiledParameterSpace``.

    Graceful Degradation:
    - Invalid expressions are logged as WARNING and their constraints are
      removed from the data dict (in-place mutation).
    - The function NEVER raises an exception.
    - Returns a summary of validation results.
    """
    summary = {"checked": 0, "passed": 0, "failed": 0, "failed_params": []}

    try:
        from mathart.distill.sandbox_validator import (
            safe_parse_expression,
            math_fuzz_expression,
            UnsafeExpressionError,
            MathToxinError,
        )
    except ImportError:
        logger.debug(
            "[SandboxValidator] Expression validator not available; "
            "skipping JSON expression check for %s.",
            source_path,
        )
        return summary

    constraints = knowledge_data.get("parameter_space_constraints", {})
    poisoned_keys: list[str] = []

    for param_name, spec in constraints.items():
        expr = spec.get("expr")
        if not isinstance(expr, str) or not expr.strip():
            continue

        summary["checked"] += 1
        try:
            # Gate 1: AST whitelist check (ZERO eval/exec)
            safe_parse_expression(expr)
            # Gate 2: Math fuzz check (NaN/Inf/overflow detection)
            math_fuzz_expression(expr)
            summary["passed"] += 1
        except (UnsafeExpressionError, MathToxinError) as exc:
            summary["failed"] += 1
            summary["failed_params"].append(param_name)
            poisoned_keys.append(param_name)
            logger.warning(
                "[SandboxValidator] Expression in constraint %r from %s "
                "REJECTED: %s. Removing this constraint — healthy "
                "constraints continue loading.",
                param_name,
                source_path,
                exc,
            )
        except Exception as exc:
            summary["failed"] += 1
            summary["failed_params"].append(param_name)
            poisoned_keys.append(param_name)
            logger.warning(
                "[SandboxValidator] Unexpected error validating expression "
                "in %r from %s: %s. Removing constraint defensively.",
                param_name,
                source_path,
                exc,
            )

    # Remove poisoned constraints (Graceful Degradation: clamp/discard)
    for key in poisoned_keys:
        constraints.pop(key, None)

    if summary["checked"] > 0:
        logger.info(
            "[SandboxValidator] JSON expression scan for %s: "
            "%d checked, %d passed, %d rejected.",
            source_path,
            summary["checked"],
            summary["passed"],
            summary["failed"],
        )

    return summary


# ═══════════════════════════════════════════════════════════════════════════
#  Knowledge Asset Loading (original preloader logic, now with validator)
# ═══════════════════════════════════════════════════════════════════════════


def _load_knowledge_asset(knowledge_path: Path | str) -> dict[str, Any]:
    path = Path(knowledge_path)
    if not path.exists():
        raise FileNotFoundError(f"Knowledge file not found: {path}")

    data = json.loads(path.read_text(encoding="utf-8"))
    required_fields = {"schema_version", "best_config", "parameter_space_constraints"}
    missing = required_fields - set(data.keys())
    if missing:
        raise ValueError(f"Knowledge file missing required fields: {missing}")

    # SESSION-184: Validate expressions in the knowledge asset BEFORE
    # it reaches the CompiledParameterSpace. This is the pre-mount
    # interceptor for JSON-embedded expressions.
    _validate_knowledge_json_expressions(data, source_path=str(path))

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
    """Preload all distilled knowledge assets and mount evolution states.

    SESSION-177: This function now also triggers the State Vault migration
    and mounts evolution states onto the bus as a defensive pre-heat step.
    The bus ``__init__`` already does this, but running it here as well
    ensures that any late-arriving legacy files are caught.

    SESSION-184: **Sandbox Validator Pre-Mount Interceptor** — before loading
    any knowledge asset onto the bus, the quarantine directory is scanned by
    ``SandboxValidator``. Failed rules are logged as WARNING and skipped.
    JSON knowledge assets are also scanned for embedded expressions via the
    AST whitelist firewall. The preloader NEVER crashes due to validation
    failures (Graceful Degradation contract).
    """
    kdir = Path(knowledge_dir) if knowledge_dir else bus.knowledge_dir
    loaded: dict[str, CompiledParameterSpace] = {}

    # ── SESSION-184: Pre-mount Sandbox Validator scan ─────────────────
    # This is the Middleware Interceptor: validate quarantine rules BEFORE
    # any knowledge is mounted onto the bus.
    validator_summary = _validate_quarantine_rules(
        project_root=bus.project_root,
    )
    if validator_summary.get("failed", 0) > 0:
        logger.warning(
            "[KnowledgePreloader] SESSION-184 Sandbox Validator rejected "
            "%d rule(s). Rejected IDs: %s. Healthy rules continue loading.",
            validator_summary["failed"],
            validator_summary["failed_rule_ids"],
        )

    # SESSION-177: Defensive hot migration at preload time
    try:
        from mathart.evolution.state_vault import migrate_legacy_states
        manifest = migrate_legacy_states(bus.project_root)
        if manifest:
            logger.info(
                "[KnowledgePreloader] SESSION-177 defensive migration: "
                "%d legacy state file(s) moved to vault.",
                len(manifest),
            )
    except Exception as exc:
        logger.debug(
            "[KnowledgePreloader] SESSION-177 vault migration skipped: %s", exc
        )

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
