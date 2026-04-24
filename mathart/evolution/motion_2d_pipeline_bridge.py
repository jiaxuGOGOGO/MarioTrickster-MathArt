"""
SESSION-061: Three-Layer Evolution Bridge — Motion 2D Pipeline

This bridge integrates the Phase 3 Motion Cognitive Dimensionality Reduction
and 2D IK Closed Loop into the project's three-layer evolution architecture:

    Layer 1 (Internal Evolution): Evaluate the full NSM → Projection → Terrain
        IK → Spine Export pipeline, measuring projection quality, IK convergence,
        and animation 12-principle scores.

    Layer 2 (External Knowledge Distillation): Distill research findings from
        Sebastian Starke (MANN, NSM, DeepPhase), Daniel Holden (PFNN), and
        animation principles into actionable rules persisted to Markdown.

    Layer 3 (Self-Iterating Test): Persist state, compute fitness bonus, and
        trigger re-evolution when metrics degrade.

Architecture follows the repository's standard bridge pattern:
    ``run_full_cycle(**kwargs) → (metrics, knowledge_path, bonus)``

Research foundations:
  - Sebastian Starke — MANN (SIGGRAPH 2018), NSM (SIGGRAPH Asia 2019),
    DeepPhase (SIGGRAPH 2022)
  - Daniel Holden — PFNN (SIGGRAPH 2017)
  - Thomas & Johnston — The Illusion of Life (1981)
  - Aristidou & Lasenby — FABRIK (2011)
"""
from __future__ import annotations
from .state_vault import resolve_state_path

import json
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


# ═══════════════════════════════════════════════════════════════════════════════
# Metrics
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Motion2DPipelineMetrics:
    """Metrics from a single evaluation cycle of the Motion 2D Pipeline."""

    # Projection quality
    bone_length_preservation: float = 0.0
    joint_angle_fidelity: float = 0.0
    sorting_order_stability: float = 0.0

    # IK quality
    foot_terrain_error: float = 0.0
    ik_contact_accuracy: float = 0.0
    ik_convergence_iterations: float = 0.0

    # Animation principles
    principles_aggregate: float = 0.0
    principles_squash_stretch: float = 0.0
    principles_arcs: float = 0.0
    principles_timing: float = 0.0
    principles_solid_drawing: float = 0.0
    principles_recommendations_count: int = 0

    # Pipeline
    biped_pipeline_pass: bool = False
    quadruped_pipeline_pass: bool = False
    spine_export_success: bool = False
    total_frames_processed: int = 0

    # Gate
    all_pass: bool = False
    pass_gate: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ═══════════════════════════════════════════════════════════════════════════════
# State
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Motion2DPipelineState:
    """Persistent state for the Motion 2D Pipeline bridge."""

    total_cycles: int = 0
    total_passes: int = 0
    total_failures: int = 0
    best_projection_quality: float = 0.0
    best_ik_accuracy: float = 0.0
    best_principles_score: float = 0.0
    quality_trend: list[float] = field(default_factory=list)
    cycle_history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_cycles": self.total_cycles,
            "total_passes": self.total_passes,
            "total_failures": self.total_failures,
            "best_projection_quality": round(self.best_projection_quality, 4),
            "best_ik_accuracy": round(self.best_ik_accuracy, 4),
            "best_principles_score": round(self.best_principles_score, 4),
            "quality_trend": [round(q, 4) for q in self.quality_trend[-50:]],
            "cycle_history": self.cycle_history[-20:],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Motion2DPipelineState":
        return cls(
            total_cycles=int(data.get("total_cycles", 0)),
            total_passes=int(data.get("total_passes", 0)),
            total_failures=int(data.get("total_failures", 0)),
            best_projection_quality=float(data.get("best_projection_quality", 0.0)),
            best_ik_accuracy=float(data.get("best_ik_accuracy", 0.0)),
            best_principles_score=float(data.get("best_principles_score", 0.0)),
            quality_trend=list(data.get("quality_trend", [])),
            cycle_history=list(data.get("cycle_history", [])),
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Status
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Motion2DPipelineStatus:
    """Status snapshot for the Motion 2D Pipeline bridge."""

    orthographic_projector_available: bool = False
    terrain_ik_2d_available: bool = False
    principles_quantifier_available: bool = False
    motion_2d_pipeline_available: bool = False
    spine_exporter_available: bool = False
    nsm_gait_available: bool = False
    state_file_exists: bool = False
    knowledge_file_exists: bool = False
    total_cycles: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def collect_motion_2d_pipeline_status(
    project_root: Optional[str | Path] = None,
) -> Motion2DPipelineStatus:
    """Collect status of all Motion 2D Pipeline subsystems."""
    root = Path(project_root) if project_root else Path.cwd()
    status = Motion2DPipelineStatus()

    try:
        from mathart.animation.orthographic_projector import OrthographicProjector
        status.orthographic_projector_available = True
    except Exception:
        pass

    try:
        from mathart.animation.terrain_ik_2d import TerrainAdaptiveIKLoop
        status.terrain_ik_2d_available = True
    except Exception:
        pass

    try:
        from mathart.animation.principles_quantifier import PrincipleScorer
        status.principles_quantifier_available = True
    except Exception:
        pass

    try:
        from mathart.animation.motion_2d_pipeline import Motion2DPipeline
        status.motion_2d_pipeline_available = True
    except Exception:
        pass

    try:
        from mathart.animation.orthographic_projector import SpineJSONExporter
        status.spine_exporter_available = True
    except Exception:
        pass

    try:
        from mathart.animation.nsm_gait import DistilledNeuralStateMachine
        status.nsm_gait_available = True
    except Exception:
        pass

    state_path = resolve_state_path(root, ".motion_2d_pipeline_state.json")
    status.state_file_exists = state_path.exists()

    knowledge_path = root / "knowledge" / "motion_2d_pipeline_rules.md"
    status.knowledge_file_exists = knowledge_path.exists()

    if state_path.exists():
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
            status.total_cycles = int(data.get("total_cycles", 0))
        except Exception:
            pass

    return status


# ═══════════════════════════════════════════════════════════════════════════════
# Distilled Knowledge
# ═══════════════════════════════════════════════════════════════════════════════

DISTILLED_KNOWLEDGE: list[dict[str, str]] = [
    {
        "source": "Sebastian Starke — MANN (SIGGRAPH 2018)",
        "rule": "Quadruped gating networks must handle asymmetric phase offsets "
                "between diagonal and lateral limb pairs. The duty factor per limb "
                "controls stance/swing ratio and must be independently configurable.",
    },
    {
        "source": "Sebastian Starke — NSM (SIGGRAPH Asia 2019)",
        "rule": "Neural State Machine goal-driven scene interactions require "
                "terrain geometry as a first-class input. The 2D projection must "
                "preserve contact labels and target offsets from the NSM output.",
    },
    {
        "source": "Sebastian Starke — DeepPhase (SIGGRAPH 2022)",
        "rule": "Multi-dimensional phase space decomposition enables per-limb "
                "independent phase channels. Each limb's local phase drives its "
                "own contact probability and swing trajectory.",
    },
    {
        "source": "Daniel Holden — PFNN (SIGGRAPH 2017)",
        "rule": "Terrain heightmap input to the motion controller must be sampled "
                "at the foot's horizontal position. The IK solver adjusts ankle "
                "targets to match the queried ground height.",
    },
    {
        "source": "Aristidou & Lasenby — FABRIK (2011)",
        "rule": "FABRIK 2D solver converges in O(n) per iteration for chain IK. "
                "Angular constraints should be applied as a post-processing step "
                "after the forward-backward pass to maintain convergence speed.",
    },
    {
        "source": "Thomas & Johnston — The Illusion of Life (1981)",
        "rule": "Animation quality must be quantified against all 12 principles. "
                "Squash & stretch requires volume preservation (sx*sy ≈ 1.0). "
                "Arcs require smooth curvature variance across joint trajectories.",
    },
    {
        "source": "Orthographic Projection Pipeline Design",
        "rule": "3D→2D projection must preserve X/Y displacement and Z-axis "
                "rotation while converting Z-depth to integer sorting orders. "
                "Bone length preservation ratio must exceed 0.95.",
    },
    {
        "source": "Spine JSON Export Format (Esoteric Software)",
        "rule": "Spine JSON export must include skeleton metadata, bone hierarchy, "
                "slot definitions with draw order, IK constraint definitions, and "
                "animation timelines (rotate, translate, scale).",
    },
]


# ═══════════════════════════════════════════════════════════════════════════════
# Bridge
# ═══════════════════════════════════════════════════════════════════════════════

class Motion2DPipelineEvolutionBridge:
    """Three-layer evolution bridge for the Motion 2D Pipeline.

    Follows the repository's standard bridge pattern:
        ``run_full_cycle(**kwargs) → (metrics, knowledge_path, bonus)``
    """

    STATE_FILE = "motion_2d_pipeline_state.json"
    KNOWLEDGE_FILE = "knowledge/motion_2d_pipeline_rules.md"

    def __init__(
        self,
        project_root: Optional[str | Path] = None,
        verbose: bool = False,
    ) -> None:
        self.root = Path(project_root) if project_root else Path.cwd()
        self.verbose = verbose
        self.state_path = resolve_state_path(self.root, self.STATE_FILE)
        self.knowledge_path = self.root / self.KNOWLEDGE_FILE
        self.state = self._load_state()

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(f"[motion_2d_bridge] {msg}")

    def _load_state(self) -> Motion2DPipelineState:
        if not self.state_path.exists():
            return Motion2DPipelineState()
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            return Motion2DPipelineState.from_dict(data)
        except (json.JSONDecodeError, OSError):
            return Motion2DPipelineState()

    def _save_state(self) -> None:
        self.state_path.write_text(
            json.dumps(self.state.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    # ── Layer 1: Evaluate ───────────────────────────────────────────────

    def evaluate(
        self,
        n_frames: int = 30,
        speed: float = 1.0,
    ) -> Motion2DPipelineMetrics:
        """Layer 1: Evaluate the full Motion 2D Pipeline."""
        metrics = Motion2DPipelineMetrics()

        try:
            from mathart.animation.motion_2d_pipeline import (
                Motion2DPipeline, PipelineConfig,
            )

            pipeline = Motion2DPipeline(PipelineConfig())

            # Biped walk
            self._log("Evaluating biped walk pipeline...")
            biped_result = pipeline.run_biped_walk(n_frames=n_frames, speed=speed)
            metrics.biped_pipeline_pass = biped_result.pipeline_pass
            metrics.total_frames_processed += biped_result.total_frames

            if biped_result.projection_quality:
                pq = biped_result.projection_quality
                metrics.bone_length_preservation = pq.bone_length_preservation
                metrics.joint_angle_fidelity = pq.joint_angle_fidelity
                metrics.sorting_order_stability = pq.sorting_order_stability

            if biped_result.ik_quality:
                iq = biped_result.ik_quality
                metrics.foot_terrain_error = iq.foot_terrain_error
                metrics.ik_contact_accuracy = iq.contact_accuracy
                metrics.ik_convergence_iterations = iq.convergence_iterations

            if biped_result.principles_report:
                pr = biped_result.principles_report
                metrics.principles_aggregate = pr.aggregate_score
                metrics.principles_squash_stretch = pr.squash_stretch
                metrics.principles_arcs = pr.arcs
                metrics.principles_timing = pr.timing
                metrics.principles_solid_drawing = pr.solid_drawing
                metrics.principles_recommendations_count = len(pr.recommendations)

            # Quadruped trot
            self._log("Evaluating quadruped trot pipeline...")
            quad_result = pipeline.run_quadruped_trot(n_frames=n_frames, speed=speed)
            metrics.quadruped_pipeline_pass = quad_result.pipeline_pass
            metrics.total_frames_processed += quad_result.total_frames

            # Spine export test
            self._log("Testing Spine JSON export...")
            import tempfile
            with tempfile.TemporaryDirectory() as tmpdir:
                path = pipeline.export_spine_json(
                    biped_result, Path(tmpdir) / "test_export.json",
                )
                metrics.spine_export_success = path.exists()

            # Gate
            metrics.pass_gate = (
                metrics.bone_length_preservation >= 0.95
                and metrics.joint_angle_fidelity >= 0.90
                and metrics.ik_contact_accuracy >= 0.80
                and metrics.biped_pipeline_pass
                and metrics.quadruped_pipeline_pass
                and metrics.spine_export_success
            )
            metrics.all_pass = metrics.pass_gate

            self._log(
                f"Evaluation complete: pass={metrics.pass_gate}, "
                f"proj={metrics.bone_length_preservation:.4f}, "
                f"ik_acc={metrics.ik_contact_accuracy:.4f}, "
                f"principles={metrics.principles_aggregate:.4f}"
            )

        except Exception as e:
            self._log(f"Evaluation error: {e}")
            import traceback
            traceback.print_exc()

        return metrics

    # ── Layer 2: Distill Knowledge ──────────────────────────────────────

    def distill_knowledge(
        self,
        metrics: Motion2DPipelineMetrics,
    ) -> Path:
        """Layer 2: Distill research knowledge into persistent rules."""
        self.knowledge_path.parent.mkdir(parents=True, exist_ok=True)

        lines: list[str] = [
            "# Motion 2D Pipeline — Distilled Knowledge Rules",
            "",
            f"*Auto-generated by SESSION-061 bridge on "
            f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}*",
            "",
            "## Static Research Rules",
            "",
        ]

        for i, rule in enumerate(DISTILLED_KNOWLEDGE, 1):
            lines.append(f"### Rule {i}: {rule['source']}")
            lines.append("")
            lines.append(f"> {rule['rule']}")
            lines.append("")

        # Dynamic rules based on metrics
        lines.append("## Dynamic Rules (from latest evaluation)")
        lines.append("")

        if metrics.bone_length_preservation < 0.98:
            lines.append(
                "- **Projection Warning**: Bone length preservation "
                f"({metrics.bone_length_preservation:.4f}) is below 0.98. "
                "Consider adjusting projection scale or verifying bone hierarchy."
            )

        if metrics.ik_contact_accuracy < 0.95:
            lines.append(
                "- **IK Warning**: Contact accuracy "
                f"({metrics.ik_contact_accuracy:.4f}) is below 0.95. "
                "Increase FABRIK max_iterations or reduce tolerance."
            )

        if metrics.principles_aggregate < 0.5:
            lines.append(
                "- **Principles Warning**: Aggregate score "
                f"({metrics.principles_aggregate:.4f}) is below 0.5. "
                "Review anticipation, follow-through, and timing curves."
            )

        if metrics.foot_terrain_error > 0.01:
            lines.append(
                "- **Terrain Error**: Foot-terrain error "
                f"({metrics.foot_terrain_error:.6f}) exceeds 0.01. "
                "Verify TerrainProbe2D accuracy and IK target computation."
            )

        lines.append("")
        lines.append("## Metrics Snapshot")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        lines.append(f"| Bone Length Preservation | {metrics.bone_length_preservation:.4f} |")
        lines.append(f"| Joint Angle Fidelity | {metrics.joint_angle_fidelity:.4f} |")
        lines.append(f"| Sorting Order Stability | {metrics.sorting_order_stability:.4f} |")
        lines.append(f"| IK Contact Accuracy | {metrics.ik_contact_accuracy:.4f} |")
        lines.append(f"| Foot Terrain Error | {metrics.foot_terrain_error:.6f} |")
        lines.append(f"| Principles Aggregate | {metrics.principles_aggregate:.4f} |")
        lines.append(f"| Biped Pipeline Pass | {metrics.biped_pipeline_pass} |")
        lines.append(f"| Quadruped Pipeline Pass | {metrics.quadruped_pipeline_pass} |")
        lines.append(f"| Spine Export Success | {metrics.spine_export_success} |")
        lines.append(f"| Total Frames Processed | {metrics.total_frames_processed} |")
        lines.append("")

        self.knowledge_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self._log(f"Knowledge distilled to {self.knowledge_path}")
        return self.knowledge_path

    # ── Layer 3: Persist & Evolve ───────────────────────────────────────

    def persist_and_evolve(
        self,
        metrics: Motion2DPipelineMetrics,
    ) -> float:
        """Layer 3: Persist state and compute fitness bonus."""
        self.state.total_cycles += 1

        if metrics.pass_gate:
            self.state.total_passes += 1
        else:
            self.state.total_failures += 1

        # Track best scores
        self.state.best_projection_quality = max(
            self.state.best_projection_quality,
            metrics.bone_length_preservation,
        )
        self.state.best_ik_accuracy = max(
            self.state.best_ik_accuracy,
            metrics.ik_contact_accuracy,
        )
        self.state.best_principles_score = max(
            self.state.best_principles_score,
            metrics.principles_aggregate,
        )

        # Compute fitness bonus
        bonus = 0.0
        if metrics.pass_gate:
            bonus += 0.10
        bonus += metrics.bone_length_preservation * 0.15
        bonus += metrics.ik_contact_accuracy * 0.15
        bonus += metrics.principles_aggregate * 0.10
        if metrics.spine_export_success:
            bonus += 0.05
        if metrics.quadruped_pipeline_pass:
            bonus += 0.05
        bonus = min(bonus, 0.60)

        # Quality trend
        quality = (
            metrics.bone_length_preservation * 0.3
            + metrics.ik_contact_accuracy * 0.3
            + metrics.principles_aggregate * 0.2
            + (0.1 if metrics.biped_pipeline_pass else 0.0)
            + (0.1 if metrics.quadruped_pipeline_pass else 0.0)
        )
        self.state.quality_trend.append(quality)

        # Cycle history
        self.state.cycle_history.append({
            "cycle": self.state.total_cycles,
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "pass": metrics.pass_gate,
            "bonus": round(bonus, 4),
            "quality": round(quality, 4),
        })

        self._save_state()
        self._log(
            f"State persisted: cycle={self.state.total_cycles}, "
            f"bonus={bonus:.4f}, quality={quality:.4f}"
        )
        return bonus

    # ── Full Cycle ──────────────────────────────────────────────────────

    def run_full_cycle(
        self,
        n_frames: int = 30,
        speed: float = 1.0,
        **kwargs: Any,
    ) -> tuple[Motion2DPipelineMetrics, Path, float]:
        """Run a complete three-layer evolution cycle.

        Returns
        -------
        tuple
            (metrics, knowledge_path, fitness_bonus)
        """
        self._log("Starting Motion 2D Pipeline evolution cycle...")

        # Layer 1: Evaluate
        metrics = self.evaluate(n_frames=n_frames, speed=speed)

        # Layer 2: Distill
        knowledge_path = self.distill_knowledge(metrics)

        # Layer 3: Persist & Evolve
        bonus = self.persist_and_evolve(metrics)

        self._log(
            f"Cycle complete: pass={metrics.pass_gate}, bonus={bonus:.4f}"
        )
        return metrics, knowledge_path, bonus


__all__ = [
    "Motion2DPipelineMetrics",
    "Motion2DPipelineState",
    "Motion2DPipelineStatus",
    "collect_motion_2d_pipeline_status",
    "DISTILLED_KNOWLEDGE",
    "Motion2DPipelineEvolutionBridge",
]
