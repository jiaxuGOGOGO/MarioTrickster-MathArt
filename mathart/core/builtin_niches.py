"""Built-in Evolution Niches — MAP-Elites Cells for Existing Pipelines.

SESSION-064: Bridge existing evolution bridges into the niche registry.

Each niche wraps an existing evolution bridge as a MAP-Elites cell with
isolated fitness evaluation. New niches can be added by simply creating
a new class with the ``@register_niche`` decorator — no trunk modification.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from mathart.core.niche_registry import (
    EvolutionNiche,
    NicheReport,
    register_niche,
)

logger = logging.getLogger(__name__)


def _safe_import_bridge(module_name: str, class_name: str):
    """Safely import a bridge class, returning None if unavailable."""
    try:
        module = __import__(module_name, fromlist=[class_name])
        return getattr(module, class_name)
    except (ImportError, AttributeError) as e:
        logger.debug("Bridge %s.%s not available: %s", module_name, class_name, e)
        return None


def _run_bridge_safe(bridge_cls, root: Path, verbose: bool = False, **kwargs) -> tuple:
    """Run a bridge safely, returning (metrics, knowledge_ref, bonus)."""
    try:
        try:
            bridge = bridge_cls(project_root=root, verbose=verbose)
        except TypeError:
            bridge = bridge_cls(project_root=root)
        return bridge.run_full_cycle(**kwargs)
    except Exception as e:
        logger.debug("Bridge execution failed: %s", e)
        return None, None, 0.0


# ---------------------------------------------------------------------------
# Smooth Morphology Niche
# ---------------------------------------------------------------------------

@register_niche(
    "smooth_morphology",
    display_name="Smooth Morphology SDF",
    lane="2d_contour",
    fitness_objectives=("contour_fidelity", "smoothness", "feature_preservation"),
    pass_gate_conditions=("contour_fidelity > 0.7",),
    behavioral_descriptors=("body_proportion", "limb_count"),
    session_origin="SESSION-064",
)
class SmoothMorphologyNiche(EvolutionNiche):
    """MAP-Elites niche for smooth morphology SDF evolution."""

    def __init__(self, project_root=None, **kwargs):
        super().__init__("smooth_morphology", project_root)

    def evaluate(self, **kwargs) -> NicheReport:
        bridge_cls = _safe_import_bridge(
            "mathart.evolution.smooth_morphology_bridge",
            "SmoothMorphologyEvolutionBridge",
        )
        if bridge_cls is None:
            return NicheReport(
                niche_name="smooth_morphology",
                fitness_scores={"contour_fidelity": 0.0},
                pass_gate=False,
                metadata={"status": "bridge_not_available"},
            )

        metrics, knowledge_ref, bonus = _run_bridge_safe(
            bridge_cls, self._root, resolution=kwargs.get("resolution", 48),
        )
        if metrics is None:
            return NicheReport(
                niche_name="smooth_morphology",
                fitness_scores={"contour_fidelity": 0.0},
                pass_gate=False,
                metadata={"status": "execution_failed"},
            )

        scores = {}
        passed = False
        try:
            if hasattr(metrics, "metrics") and isinstance(metrics.metrics, dict):
                scores = {k: float(v) for k, v in metrics.metrics.items()
                          if isinstance(v, (int, float))}
            passed = bool(getattr(metrics, "all_pass", False)
                         or getattr(metrics, "pass_gate", False))
        except Exception:
            pass

        report = NicheReport(
            niche_name="smooth_morphology",
            fitness_scores=scores or {"contour_fidelity": float(bonus)},
            pass_gate=passed,
            cycle_count=self._cycle_count + 1,
            trend=self._trend + [max(scores.values()) if scores else float(bonus)],
            metadata={"knowledge_ref": str(knowledge_ref) if knowledge_ref else None},
        )
        self._save_state(report)
        return report

    def distill(self) -> list[str]:
        return [
            "SDF smoothness correlates with contour fidelity",
            "Higher resolution improves feature preservation but costs compute",
        ]


# ---------------------------------------------------------------------------
# WFC Tilemap Niche
# ---------------------------------------------------------------------------

@register_niche(
    "constraint_wfc",
    display_name="Constraint WFC Tilemap",
    lane="level_design",
    fitness_objectives=("constraint_satisfaction", "tile_diversity", "playability"),
    pass_gate_conditions=("constraint_satisfaction > 0.8",),
    behavioral_descriptors=("level_complexity", "path_length"),
    session_origin="SESSION-064",
)
class ConstraintWFCNiche(EvolutionNiche):
    """MAP-Elites niche for WFC tilemap generation."""

    def __init__(self, project_root=None, **kwargs):
        super().__init__("constraint_wfc", project_root)

    def evaluate(self, **kwargs) -> NicheReport:
        bridge_cls = _safe_import_bridge(
            "mathart.evolution.constraint_wfc_bridge",
            "ConstraintWFCEvolutionBridge",
        )
        if bridge_cls is None:
            return NicheReport(
                niche_name="constraint_wfc",
                fitness_scores={"constraint_satisfaction": 0.0},
                pass_gate=False,
                metadata={"status": "bridge_not_available"},
            )

        metrics, knowledge_ref, bonus = _run_bridge_safe(
            bridge_cls, self._root,
            n_levels=kwargs.get("n_levels", 4),
            width=kwargs.get("width", 18),
            height=kwargs.get("height", 7),
            seed=kwargs.get("seed", 64),
        )
        if metrics is None:
            return NicheReport(
                niche_name="constraint_wfc",
                fitness_scores={"constraint_satisfaction": 0.0},
                pass_gate=False,
                metadata={"status": "execution_failed"},
            )

        scores = {}
        passed = False
        try:
            if hasattr(metrics, "metrics") and isinstance(metrics.metrics, dict):
                scores = {k: float(v) for k, v in metrics.metrics.items()
                          if isinstance(v, (int, float))}
            passed = bool(getattr(metrics, "all_pass", False)
                         or getattr(metrics, "pass_gate", False))
        except Exception:
            pass

        report = NicheReport(
            niche_name="constraint_wfc",
            fitness_scores=scores or {"constraint_satisfaction": float(bonus)},
            pass_gate=passed,
            cycle_count=self._cycle_count + 1,
            trend=self._trend + [max(scores.values()) if scores else float(bonus)],
            metadata={"knowledge_ref": str(knowledge_ref) if knowledge_ref else None},
        )
        self._save_state(report)
        return report

    def distill(self) -> list[str]:
        return [
            "WFC constraint propagation benefits from larger tile adjacency tables",
            "Playability correlates with path connectivity metrics",
        ]


# ---------------------------------------------------------------------------
# Motion 2D Pipeline Niche
# ---------------------------------------------------------------------------

@register_niche(
    "motion_2d_pipeline",
    display_name="Motion 2D Pipeline",
    lane="animation_2d",
    fitness_objectives=("motion_smoothness", "sprite_quality", "frame_consistency"),
    pass_gate_conditions=("motion_smoothness > 0.6",),
    behavioral_descriptors=("animation_style", "frame_count"),
    session_origin="SESSION-064",
)
class Motion2DPipelineNiche(EvolutionNiche):
    """MAP-Elites niche for 2D motion/animation pipeline."""

    def __init__(self, project_root=None, **kwargs):
        super().__init__("motion_2d_pipeline", project_root)

    def evaluate(self, **kwargs) -> NicheReport:
        bridge_cls = _safe_import_bridge(
            "mathart.evolution.motion_2d_pipeline_bridge",
            "Motion2DPipelineEvolutionBridge",
        )
        if bridge_cls is None:
            return NicheReport(
                niche_name="motion_2d_pipeline",
                fitness_scores={"motion_smoothness": 0.0},
                pass_gate=False,
                metadata={"status": "bridge_not_available"},
            )

        metrics, knowledge_ref, bonus = _run_bridge_safe(
            bridge_cls, self._root,
            n_frames=kwargs.get("n_frames", 30),
        )
        if metrics is None:
            return NicheReport(
                niche_name="motion_2d_pipeline",
                fitness_scores={"motion_smoothness": 0.0},
                pass_gate=False,
                metadata={"status": "execution_failed"},
            )

        scores = {}
        passed = False
        try:
            if hasattr(metrics, "metrics") and isinstance(metrics.metrics, dict):
                scores = {k: float(v) for k, v in metrics.metrics.items()
                          if isinstance(v, (int, float))}
            passed = bool(getattr(metrics, "all_pass", False)
                         or getattr(metrics, "pass_gate", False))
        except Exception:
            pass

        report = NicheReport(
            niche_name="motion_2d_pipeline",
            fitness_scores=scores or {"motion_smoothness": float(bonus)},
            pass_gate=passed,
            cycle_count=self._cycle_count + 1,
            trend=self._trend + [max(scores.values()) if scores else float(bonus)],
        )
        self._save_state(report)
        return report

    def distill(self) -> list[str]:
        return [
            "Frame interpolation quality depends on SDF temporal coherence",
            "Sprite sheet packing efficiency improves with power-of-2 frame counts",
        ]


# ---------------------------------------------------------------------------
# Dimension Uplift Niche
# ---------------------------------------------------------------------------

@register_niche(
    "dimension_uplift",
    display_name="2.5D/3D Dimension Uplift",
    lane="3d_mesh",
    fitness_objectives=("mesh_quality", "normal_continuity", "cache_accuracy"),
    pass_gate_conditions=("mesh_quality > 0.5",),
    behavioral_descriptors=("vertex_count", "topology_genus"),
    session_origin="SESSION-064",
)
class DimensionUpliftNiche(EvolutionNiche):
    """MAP-Elites niche for 2.5D/3D dimension uplift."""

    def __init__(self, project_root=None, **kwargs):
        super().__init__("dimension_uplift", project_root)

    def evaluate(self, **kwargs) -> NicheReport:
        bridge_cls = _safe_import_bridge(
            "mathart.evolution.dimension_uplift_bridge",
            "DimensionUpliftEvolutionBridge",
        )
        if bridge_cls is None:
            return NicheReport(
                niche_name="dimension_uplift",
                fitness_scores={"mesh_quality": 0.0},
                pass_gate=False,
                metadata={"status": "bridge_not_available"},
            )

        metrics, knowledge_ref, bonus = _run_bridge_safe(
            bridge_cls, self._root,
        )
        if metrics is None:
            return NicheReport(
                niche_name="dimension_uplift",
                fitness_scores={"mesh_quality": 0.0},
                pass_gate=False,
                metadata={"status": "execution_failed"},
            )

        scores = {}
        passed = False
        try:
            if hasattr(metrics, "metrics") and isinstance(metrics.metrics, dict):
                scores = {k: float(v) for k, v in metrics.metrics.items()
                          if isinstance(v, (int, float))}
            passed = bool(getattr(metrics, "all_pass", False)
                         or getattr(metrics, "pass_gate", False))
        except Exception:
            pass

        report = NicheReport(
            niche_name="dimension_uplift",
            fitness_scores=scores or {"mesh_quality": float(bonus)},
            pass_gate=passed,
            cycle_count=self._cycle_count + 1,
            trend=self._trend + [max(scores.values()) if scores else float(bonus)],
        )
        self._save_state(report)
        return report

    def distill(self) -> list[str]:
        return [
            "Normal continuity requires Laplacian smoothing post-marching-cubes",
            "Cache-friendly vertex ordering reduces GPU draw call overhead",
        ]


# ---------------------------------------------------------------------------
# Phase 3 Physics Niche
# ---------------------------------------------------------------------------

@register_niche(
    "phase3_physics",
    display_name="Phase 3 Physics Simulation",
    lane="physics_vfx",
    fitness_objectives=("physics_accuracy", "visual_fidelity", "performance"),
    pass_gate_conditions=("physics_accuracy > 0.5",),
    behavioral_descriptors=("particle_count", "simulation_type"),
    session_origin="SESSION-064",
)
class Phase3PhysicsNiche(EvolutionNiche):
    """MAP-Elites niche for physics simulation."""

    def __init__(self, project_root=None, **kwargs):
        super().__init__("phase3_physics", project_root)

    def evaluate(self, **kwargs) -> NicheReport:
        bridge_cls = _safe_import_bridge(
            "mathart.evolution.phase3_physics_bridge",
            "Phase3PhysicsEvolutionBridge",
        )
        if bridge_cls is None:
            return NicheReport(
                niche_name="phase3_physics",
                fitness_scores={"physics_accuracy": 0.0},
                pass_gate=False,
                metadata={"status": "bridge_not_available"},
            )

        metrics, knowledge_ref, bonus = _run_bridge_safe(
            bridge_cls, self._root,
        )
        if metrics is None:
            return NicheReport(
                niche_name="phase3_physics",
                fitness_scores={"physics_accuracy": 0.0},
                pass_gate=False,
                metadata={"status": "execution_failed"},
            )

        scores = {}
        passed = False
        try:
            if hasattr(metrics, "metrics") and isinstance(metrics.metrics, dict):
                scores = {k: float(v) for k, v in metrics.metrics.items()
                          if isinstance(v, (int, float))}
            passed = bool(getattr(metrics, "all_pass", False)
                         or getattr(metrics, "pass_gate", False))
        except Exception:
            pass

        report = NicheReport(
            niche_name="phase3_physics",
            fitness_scores=scores or {"physics_accuracy": float(bonus)},
            pass_gate=passed,
            cycle_count=self._cycle_count + 1,
            trend=self._trend + [max(scores.values()) if scores else float(bonus)],
        )
        self._save_state(report)
        return report

    def distill(self) -> list[str]:
        return [
            "Fluid simulation benefits from adaptive time-stepping",
            "VAT baking preserves physics fidelity at runtime",
        ]


# ---------------------------------------------------------------------------
# Unity URP 2D Niche
# ---------------------------------------------------------------------------

@register_niche(
    "unity_urp_2d",
    display_name="Unity URP 2D Export",
    lane="engine_export",
    fitness_objectives=("export_completeness", "shader_compatibility", "asset_integrity"),
    pass_gate_conditions=("export_completeness > 0.7",),
    behavioral_descriptors=("unity_version", "render_pipeline"),
    session_origin="SESSION-064",
)
class UnityURP2DNiche(EvolutionNiche):
    """MAP-Elites niche for Unity URP 2D export."""

    def __init__(self, project_root=None, **kwargs):
        super().__init__("unity_urp_2d", project_root)

    def evaluate(self, **kwargs) -> NicheReport:
        bridge_cls = _safe_import_bridge(
            "mathart.evolution.unity_urp_2d_bridge",
            "UnityURP2DEvolutionBridge",
        )
        if bridge_cls is None:
            return NicheReport(
                niche_name="unity_urp_2d",
                fitness_scores={"export_completeness": 0.0},
                pass_gate=False,
                metadata={"status": "bridge_not_available"},
            )

        metrics, knowledge_ref, bonus = _run_bridge_safe(
            bridge_cls, self._root,
        )
        if metrics is None:
            return NicheReport(
                niche_name="unity_urp_2d",
                fitness_scores={"export_completeness": 0.0},
                pass_gate=False,
                metadata={"status": "execution_failed"},
            )

        scores = {}
        passed = False
        try:
            if hasattr(metrics, "metrics") and isinstance(metrics.metrics, dict):
                scores = {k: float(v) for k, v in metrics.metrics.items()
                          if isinstance(v, (int, float))}
            passed = bool(getattr(metrics, "all_pass", False)
                         or getattr(metrics, "pass_gate", False))
        except Exception:
            pass

        report = NicheReport(
            niche_name="unity_urp_2d",
            fitness_scores=scores or {"export_completeness": float(bonus)},
            pass_gate=passed,
            cycle_count=self._cycle_count + 1,
            trend=self._trend + [max(scores.values()) if scores else float(bonus)],
        )
        self._save_state(report)
        return report

    def distill(self) -> list[str]:
        return [
            "URP 2D shader compatibility requires SRP Batcher support",
            "Asset bundle integrity depends on deterministic GUID assignment",
        ]


# ---------------------------------------------------------------------------
# Environment Closed Loop Niche
# ---------------------------------------------------------------------------

@register_niche(
    "env_closedloop",
    display_name="Environment Closed Loop",
    lane="integration",
    fitness_objectives=("integration_score", "loop_stability", "convergence_rate"),
    pass_gate_conditions=("integration_score > 0.5",),
    behavioral_descriptors=("loop_depth", "module_count"),
    session_origin="SESSION-064",
)
class EnvClosedLoopNiche(EvolutionNiche):
    """MAP-Elites niche for environment closed-loop integration."""

    def __init__(self, project_root=None, **kwargs):
        super().__init__("env_closedloop", project_root)

    def evaluate(self, **kwargs) -> NicheReport:
        bridge_cls = _safe_import_bridge(
            "mathart.evolution.env_closedloop_bridge",
            "EnvClosedLoopOrchestrator",
        )
        if bridge_cls is None:
            return NicheReport(
                niche_name="env_closedloop",
                fitness_scores={"integration_score": 0.0},
                pass_gate=False,
                metadata={"status": "bridge_not_available"},
            )

        metrics, knowledge_ref, bonus = _run_bridge_safe(
            bridge_cls, self._root,
        )
        if metrics is None:
            return NicheReport(
                niche_name="env_closedloop",
                fitness_scores={"integration_score": 0.0},
                pass_gate=False,
                metadata={"status": "execution_failed"},
            )

        scores = {}
        passed = False
        try:
            if hasattr(metrics, "metrics") and isinstance(metrics.metrics, dict):
                scores = {k: float(v) for k, v in metrics.metrics.items()
                          if isinstance(v, (int, float))}
            passed = bool(getattr(metrics, "all_pass", False)
                         or getattr(metrics, "pass_gate", False))
        except Exception:
            pass

        report = NicheReport(
            niche_name="env_closedloop",
            fitness_scores=scores or {"integration_score": float(bonus)},
            pass_gate=passed,
            cycle_count=self._cycle_count + 1,
            trend=self._trend + [max(scores.values()) if scores else float(bonus)],
        )
        self._save_state(report)
        return report

    def distill(self) -> list[str]:
        return [
            "Closed-loop stability requires monotonic convergence checks",
            "Module integration order affects final quality metrics",
        ]
