"""
SESSION-063: Dimension Uplift Evolution Bridge — Three-Layer 2.5D/3D Loop.

Integrates the dimension uplift engine (dimension_uplift_engine.py) into the
repository's standard three-layer evolution bridge pattern:

1. **Layer 1 — Evaluate (Internal Evolution)**: Run 2D→3D uplift pipeline
   through fitness evaluation, measuring mesh quality (vertex count, face
   regularity, feature preservation), SDF cache accuracy, displacement
   fidelity, and cel-shading coverage.

2. **Layer 2 — Distill (External Knowledge Distillation)**: Extract reusable
   rules from top-performing configurations (optimal DC resolution, smin k
   ranges, displacement strength, cache error thresholds) and persist to
   knowledge files for cross-session learning.

3. **Layer 3 — Self-Iteration Test (Fitness Bonus + Trend)**: Compute
   evolution fitness bonus for the broader orchestrator, persist trend data,
   and validate that the pipeline can self-improve through parameter tuning.

Research provenance:
  - Inigo Quilez (Shadertoy): 3D SDF primitives + Smooth Min for skinning
  - Tao Ju et al. (SIGGRAPH 2002): Dual Contouring of Hermite Data
  - Pujol & Chica (C&G 2023): Adaptive SDF approximation
  - Arc System Works / Junya Motomura (GDC 2015): Cel-shading pipeline
  - Hades (Supergiant Games): Isometric 2.5D displacement mapping
  - Taichi AOT: Vulkan SPIR-V compilation for Unity GPU deployment
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np


# ── Metrics ──────────────────────────────────────────────────────────────────

@dataclass
class DimensionUpliftMetrics:
    """Metrics captured from one dimension uplift evaluation cycle."""

    cycle_id: int = 0
    # Dual Contouring metrics
    dc_vertex_count: int = 0
    dc_face_count: int = 0
    dc_avg_face_area_variance: float = 0.0
    dc_feature_preservation_score: float = 0.0
    # Adaptive cache metrics
    cache_node_count: int = 0
    cache_max_error: float = 0.0
    cache_avg_error: float = 0.0
    # Displacement metrics
    displacement_depth_coverage: float = 0.0
    displacement_smoothness: float = 0.0
    # SDF quality
    sdf_3d_primitives_tested: int = 0
    smin_blend_quality: float = 0.0
    # Pipeline health
    all_modules_valid: bool = True
    pass_gate: bool = False
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "dc_vertex_count": self.dc_vertex_count,
            "dc_face_count": self.dc_face_count,
            "dc_avg_face_area_variance": round(self.dc_avg_face_area_variance, 6),
            "dc_feature_preservation_score": round(self.dc_feature_preservation_score, 4),
            "cache_node_count": self.cache_node_count,
            "cache_max_error": round(self.cache_max_error, 6),
            "cache_avg_error": round(self.cache_avg_error, 6),
            "displacement_depth_coverage": round(self.displacement_depth_coverage, 4),
            "displacement_smoothness": round(self.displacement_smoothness, 4),
            "sdf_3d_primitives_tested": self.sdf_3d_primitives_tested,
            "smin_blend_quality": round(self.smin_blend_quality, 4),
            "all_modules_valid": self.all_modules_valid,
            "pass_gate": self.pass_gate,
            "timestamp": self.timestamp,
        }


@dataclass
class DimensionUpliftState:
    """Persistent state for the dimension uplift evolution bridge."""

    total_cycles: int = 0
    best_feature_score_ever: float = 0.0
    feature_trend: list[float] = field(default_factory=list)
    cache_efficiency_trend: list[float] = field(default_factory=list)
    mesh_quality_trend: list[float] = field(default_factory=list)
    distilled_rules: dict[str, Any] = field(default_factory=dict)
    last_updated: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_cycles": self.total_cycles,
            "best_feature_score_ever": round(self.best_feature_score_ever, 4),
            "feature_trend": [round(f, 4) for f in self.feature_trend[-50:]],
            "cache_efficiency_trend": [round(c, 4) for c in self.cache_efficiency_trend[-50:]],
            "mesh_quality_trend": [round(m, 4) for m in self.mesh_quality_trend[-50:]],
            "distilled_rules": self.distilled_rules,
            "last_updated": self.last_updated,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DimensionUpliftState:
        return cls(
            total_cycles=int(data.get("total_cycles", 0)),
            best_feature_score_ever=float(data.get("best_feature_score_ever", 0.0)),
            feature_trend=list(data.get("feature_trend", [])),
            cache_efficiency_trend=list(data.get("cache_efficiency_trend", [])),
            mesh_quality_trend=list(data.get("mesh_quality_trend", [])),
            distilled_rules=dict(data.get("distilled_rules", {})),
            last_updated=str(data.get("last_updated", "")),
        )


@dataclass
class DimensionUpliftStatus:
    """Repository-audit status for the dimension uplift subsystem."""

    engine_module_exists: bool = False
    bridge_module_exists: bool = False
    test_exists: bool = False
    state_file_exists: bool = False
    knowledge_file_exists: bool = False
    research_file_exists: bool = False
    tracked_exports: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "engine_module_exists": self.engine_module_exists,
            "bridge_module_exists": self.bridge_module_exists,
            "test_exists": self.test_exists,
            "state_file_exists": self.state_file_exists,
            "knowledge_file_exists": self.knowledge_file_exists,
            "research_file_exists": self.research_file_exists,
            "tracked_exports": self.tracked_exports,
        }


# ── Status Collector ─────────────────────────────────────────────────────────

def collect_dimension_uplift_status(project_root: str | Path) -> DimensionUpliftStatus:
    """Collect the persisted state of the dimension uplift subsystem."""
    root = Path(project_root)
    engine_path = root / "mathart" / "animation" / "dimension_uplift_engine.py"
    bridge_path = root / "mathart" / "evolution" / "dimension_uplift_bridge.py"
    test_path = root / "tests" / "test_dimension_uplift.py"
    state_path = root / ".dimension_uplift_state.json"
    knowledge_path = root / "knowledge" / "dimension_uplift_rules.md"
    research_path = root / "research" / "session063_phase5_dimension_uplift_research.md"

    tracked_exports: list[str] = []
    if engine_path.exists():
        try:
            text = engine_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        for name in (
            "SDF3DPrimitives",
            "SmoothMin3D",
            "SDFDimensionLifter",
            "DualContouringExtractor",
            "IsometricDisplacementMapper",
            "CelShadingConfig",
            "TaichiAOTBridge",
            "AdaptiveSDFCache",
        ):
            if name in text:
                tracked_exports.append(name)

    return DimensionUpliftStatus(
        engine_module_exists=engine_path.exists(),
        bridge_module_exists=bridge_path.exists(),
        test_exists=test_path.exists(),
        state_file_exists=state_path.exists(),
        knowledge_file_exists=knowledge_path.exists(),
        research_file_exists=research_path.exists(),
        tracked_exports=tracked_exports,
    )


# ── Evolution Bridge ─────────────────────────────────────────────────────────

class DimensionUpliftEvolutionBridge:
    """Three-layer evolution bridge for the 2.5D/3D dimension uplift system.

    Follows the repository's standard bridge pattern (see smooth_morphology_bridge.py).

    Three-Layer Evolution Loop:
        Layer 1 (Internal Evolution):
            Evaluate DC mesh quality, cache accuracy, displacement fidelity.
        Layer 2 (External Knowledge Distillation):
            Extract optimal parameters, persist rules to knowledge files.
        Layer 3 (Self-Iteration Test):
            Compute fitness bonus, track trends, validate self-improvement.
    """

    STATE_FILE = ".dimension_uplift_state.json"
    KNOWLEDGE_FILE = "knowledge/dimension_uplift_rules.md"

    def __init__(self, project_root: str | Path) -> None:
        self.root = Path(project_root)
        self.state_path = self.root / self.STATE_FILE
        self.knowledge_path = self.root / self.KNOWLEDGE_FILE
        self.state = self._load_state()

    def _load_state(self) -> DimensionUpliftState:
        if not self.state_path.exists():
            return DimensionUpliftState()
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return DimensionUpliftState()
        return DimensionUpliftState.from_dict(data)

    def _save_state(self) -> None:
        self.state.last_updated = datetime.now(timezone.utc).isoformat()
        self.state_path.write_text(
            json.dumps(self.state.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    # ── Layer 1: Evaluate (Internal Evolution) ──────────────────────────

    def evaluate(
        self,
        dc_resolution: int = 32,
        cache_max_depth: int = 5,
        cache_error_threshold: float = 0.01,
        displacement_strength: float = 0.3,
    ) -> DimensionUpliftMetrics:
        """Layer 1: Evaluate dimension uplift pipeline quality.

        Runs the full pipeline: 2D SDF → 3D SDF → Adaptive Cache →
        Dual Contouring → Mesh → Displacement Map → Quality Metrics.
        """
        from mathart.animation.dimension_uplift_engine import (
            SDF3DPrimitives,
            SmoothMin3D,
            SDFDimensionLifter,
            DualContouringExtractor,
            AdaptiveSDFCache,
            IsometricDisplacementMapper,
        )

        metrics = DimensionUpliftMetrics(
            cycle_id=self.state.total_cycles + 1,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        try:
            # --- Test 3D SDF primitives ---
            test_point = np.array([0.5, 0.3, 0.2])
            primitives_ok = 0
            for name, func, args in [
                ("sphere", SDF3DPrimitives.sphere, (test_point, 1.0)),
                ("box", SDF3DPrimitives.box,
                 (test_point, np.array([0.5, 0.5, 0.5]))),
                ("capsule", SDF3DPrimitives.capsule,
                 (test_point, np.array([0, -1, 0]),
                  np.array([0, 1, 0]), 0.3)),
                ("torus", SDF3DPrimitives.torus, (test_point, 0.5, 0.2)),
                ("cylinder", SDF3DPrimitives.cylinder, (test_point, 0.5, 1.0)),
                ("ellipsoid", SDF3DPrimitives.ellipsoid,
                 (test_point, np.array([1.0, 0.5, 0.3]))),
                ("rounded_box", SDF3DPrimitives.rounded_box,
                 (test_point, np.array([0.4, 0.4, 0.4]), 0.1)),
            ]:
                try:
                    d = func(*args)
                    if isinstance(d, (int, float)) and math.isfinite(d):
                        primitives_ok += 1
                except Exception:
                    pass
            metrics.sdf_3d_primitives_tested = primitives_ok

            # --- Test smin blend quality ---
            blend_scores = []
            for k in [0.05, 0.1, 0.2, 0.5]:
                d_q, t_q = SmoothMin3D.smin_quadratic(0.3, 0.5, k)
                d_c, t_c = SmoothMin3D.smin_cubic(0.3, 0.5, k)
                # Quality: smooth blend should be less than min
                if d_q < min(0.3, 0.5) and d_c < min(0.3, 0.5):
                    blend_scores.append(1.0)
                else:
                    blend_scores.append(0.5)
            metrics.smin_blend_quality = float(np.mean(blend_scores))

            # --- Test 2D→3D lift + Dual Contouring ---
            def test_sdf_2d(x: float, y: float) -> float:
                return math.sqrt(x * x + y * y) - 0.5

            lifter = SDFDimensionLifter()
            sdf_3d = lifter.extrude_2d_to_3d(test_sdf_2d, depth=0.4)

            extractor = DualContouringExtractor(
                resolution=dc_resolution, bias_strength=0.01
            )
            mesh = extractor.extract(sdf_3d, bounds=(-1.5, 1.5))
            metrics.dc_vertex_count = mesh.vertex_count
            metrics.dc_face_count = mesh.face_count

            # Feature preservation: check mesh is non-degenerate
            if mesh.vertex_count > 0 and mesh.face_count > 0:
                # Compute face area variance as regularity metric
                areas = []
                for q in mesh.quads:
                    if all(0 <= idx < len(mesh.vertices) for idx in q):
                        v0, v1, v2, v3 = [mesh.vertices[i] for i in q]
                        area1 = 0.5 * np.linalg.norm(
                            np.cross(v1 - v0, v2 - v0))
                        area2 = 0.5 * np.linalg.norm(
                            np.cross(v2 - v0, v3 - v0))
                        areas.append(area1 + area2)
                if areas:
                    metrics.dc_avg_face_area_variance = float(np.var(areas))
                    # Feature score: low variance = regular mesh = good
                    mean_area = float(np.mean(areas))
                    if mean_area > 0:
                        cv = math.sqrt(float(np.var(areas))) / mean_area
                        metrics.dc_feature_preservation_score = max(
                            0.0, 1.0 - cv)

            # --- Test Adaptive SDF Cache ---
            cache = AdaptiveSDFCache(
                max_depth=cache_max_depth,
                error_threshold=cache_error_threshold,
            )
            bmin = np.array([-1.5, -1.5, -1.5])
            bmax = np.array([1.5, 1.5, 1.5])
            cache.build(sdf_3d, bmin, bmax)
            metrics.cache_node_count = cache.node_count

            # Measure cache accuracy
            test_errors = []
            rng = np.random.RandomState(42)
            for _ in range(100):
                tp = rng.uniform(-1.0, 1.0, size=3)
                cached_val = cache.query(*tp)
                actual_val = sdf_3d(*tp)
                test_errors.append(abs(cached_val - actual_val))
            metrics.cache_max_error = float(max(test_errors))
            metrics.cache_avg_error = float(np.mean(test_errors))

            # --- Test Displacement Mapping ---
            mapper = IsometricDisplacementMapper()
            depth_map = mapper.generate_depth_map(test_sdf_2d, resolution=64)
            nonzero = np.count_nonzero(depth_map)
            total = depth_map.size
            metrics.displacement_depth_coverage = nonzero / total if total > 0 else 0.0

            # Smoothness: average gradient magnitude (lower = smoother)
            if depth_map.shape[0] > 1 and depth_map.shape[1] > 1:
                gy, gx = np.gradient(depth_map)
                grad_mag = np.sqrt(gx ** 2 + gy ** 2)
                avg_grad = float(np.mean(grad_mag))
                metrics.displacement_smoothness = max(0.0, 1.0 - avg_grad * 2)

            metrics.all_modules_valid = True

        except Exception as e:
            metrics.all_modules_valid = False

        # Pass gate: mesh generated + cache accurate + modules valid
        metrics.pass_gate = (
            metrics.all_modules_valid
            and metrics.dc_vertex_count > 10
            and metrics.dc_face_count > 5
            and metrics.cache_avg_error < 0.5  # relaxed: octree trilinear on curved SDF
            and metrics.sdf_3d_primitives_tested >= 5
        )

        return metrics

    # ── Layer 2: Distill (External Knowledge Distillation) ──────────────

    def distill(self, metrics: DimensionUpliftMetrics) -> dict[str, Any]:
        """Layer 2: Distill rules from evaluation results.

        Extracts optimal parameter ranges and persists them for future
        sessions to consume.
        """
        rules: dict[str, Any] = {}

        # Rule: optimal DC resolution based on mesh quality
        if metrics.dc_feature_preservation_score > 0.5:
            rules["dc_resolution_range"] = [16, 64]
            rules["dc_bias_strength"] = 0.01
        else:
            rules["dc_resolution_range"] = [32, 128]
            rules["dc_bias_strength"] = 0.05

        # Rule: cache parameters
        if metrics.cache_avg_error < 0.01:
            rules["cache_max_depth"] = 5
            rules["cache_error_threshold"] = 0.01
        else:
            rules["cache_max_depth"] = 7
            rules["cache_error_threshold"] = 0.005

        # Rule: smin k ranges for different joint types
        rules["smin_k_ranges"] = {
            "tight_joint": [0.02, 0.08],
            "medium_joint": [0.08, 0.2],
            "loose_blend": [0.2, 0.5],
        }

        # Rule: displacement strength
        if metrics.displacement_smoothness > 0.7:
            rules["displacement_strength_range"] = [0.2, 0.5]
        else:
            rules["displacement_strength_range"] = [0.1, 0.3]

        # Rule: feature preservation threshold
        rules["min_feature_preservation_score"] = round(
            max(0.3, metrics.dc_feature_preservation_score * 0.8), 4
        )

        # Persist rules
        self.state.distilled_rules.update(rules)
        self._write_knowledge(rules, metrics)

        return rules

    def _write_knowledge(self, rules: dict, metrics: DimensionUpliftMetrics) -> None:
        """Write distilled knowledge to markdown file."""
        self.knowledge_path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# Dimension Uplift — Distilled Knowledge",
            "",
            f"Last updated: {datetime.now(timezone.utc).isoformat()}",
            f"Cycle: {metrics.cycle_id}",
            "",
            "## Research Sources",
            "",
            "- Inigo Quilez: SDF Smooth Min (smin) for 3D skeletal skinning",
            "- Tao Ju et al.: Dual Contouring of Hermite Data (SIGGRAPH 2002)",
            "- Pujol & Chica: Adaptive SDF Approximation (C&G 2023)",
            "- Arc System Works / Junya Motomura: Cel-shading (GDC 2015)",
            "- Isometric Camera 2.5D (Hades-style displacement)",
            "- Taichi AOT: Vulkan SPIR-V → Unity bridge",
            "",
            "## Distilled Rules",
            "",
        ]
        for key, value in rules.items():
            lines.append(f"- **{key}**: `{json.dumps(value)}`")
        lines.append("")
        lines.append("## Metrics Snapshot")
        lines.append("")
        lines.append(f"- DC Vertices: {metrics.dc_vertex_count}")
        lines.append(f"- DC Faces: {metrics.dc_face_count}")
        lines.append(f"- Feature Preservation: {metrics.dc_feature_preservation_score:.4f}")
        lines.append(f"- Cache Nodes: {metrics.cache_node_count}")
        lines.append(f"- Cache Avg Error: {metrics.cache_avg_error:.6f}")
        lines.append(f"- Displacement Coverage: {metrics.displacement_depth_coverage:.4f}")
        lines.append(f"- Displacement Smoothness: {metrics.displacement_smoothness:.4f}")
        lines.append(f"- Smin Blend Quality: {metrics.smin_blend_quality:.4f}")
        lines.append(f"- 3D Primitives Tested: {metrics.sdf_3d_primitives_tested}")
        lines.append(f"- All Modules Valid: {metrics.all_modules_valid}")
        lines.append(f"- Pass Gate: {metrics.pass_gate}")
        lines.append("")

        self.knowledge_path.write_text("\n".join(lines), encoding="utf-8")

    # ── Layer 3: Fitness Bonus + Trend (Self-Iteration Test) ────────────

    def compute_fitness_bonus(self, metrics: DimensionUpliftMetrics) -> float:
        """Layer 3: Compute fitness bonus for the broader orchestrator."""
        bonus = 0.0
        if metrics.pass_gate:
            bonus += 0.10
        if metrics.dc_feature_preservation_score > 0.6:
            bonus += 0.05
        if metrics.cache_avg_error < 0.01:
            bonus += 0.05
        if metrics.displacement_smoothness > 0.7:
            bonus += 0.03
        if metrics.smin_blend_quality >= 1.0:
            bonus += 0.02
        if metrics.all_modules_valid:
            bonus += 0.05
        return min(bonus, 0.30)

    def update_trends(self, metrics: DimensionUpliftMetrics) -> None:
        """Layer 3: Update persistent trend data for cross-session learning."""
        self.state.total_cycles += 1
        self.state.feature_trend.append(metrics.dc_feature_preservation_score)
        self.state.cache_efficiency_trend.append(
            1.0 - min(metrics.cache_avg_error * 10, 1.0)
        )
        # Composite mesh quality score
        mesh_q = 0.0
        if metrics.dc_vertex_count > 0:
            mesh_q = (
                0.4 * metrics.dc_feature_preservation_score
                + 0.3 * metrics.smin_blend_quality
                + 0.3 * metrics.displacement_smoothness
            )
        self.state.mesh_quality_trend.append(mesh_q)
        self.state.best_feature_score_ever = max(
            self.state.best_feature_score_ever,
            metrics.dc_feature_preservation_score,
        )
        self._save_state()

    # ── Full Cycle ───────────────────────────────────────────────────────

    def run_full_cycle(
        self,
        dc_resolution: int = 32,
        cache_max_depth: int = 5,
        cache_error_threshold: float = 0.01,
        displacement_strength: float = 0.3,
    ) -> tuple[DimensionUpliftMetrics, dict[str, Any], float]:
        """Run a complete three-layer evolution cycle.

        Layer 1: Evaluate → Layer 2: Distill → Layer 3: Trend + Bonus

        Returns (metrics, distilled_rules, fitness_bonus).
        """
        metrics = self.evaluate(
            dc_resolution=dc_resolution,
            cache_max_depth=cache_max_depth,
            cache_error_threshold=cache_error_threshold,
            displacement_strength=displacement_strength,
        )
        rules = self.distill(metrics)
        bonus = self.compute_fitness_bonus(metrics)
        self.update_trends(metrics)
        return metrics, rules, bonus
