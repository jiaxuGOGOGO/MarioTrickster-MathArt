"""
SESSION-061: Unified Motion 2D Pipeline — NSM → Projection → Terrain IK → Spine Export

This module is the integration hub that connects all Phase 3 subsystems into
a single end-to-end pipeline:

    NSM Gait Planner → Orthographic Projector → Terrain-Adaptive 2D IK → Spine JSON Export

Research foundations:
  1. **Sebastian Starke — MANN (SIGGRAPH 2018)**: Quadruped gating network
     for asymmetric gaits feeds per-limb contact labels into the pipeline.
  2. **Sebastian Starke — NSM (SIGGRAPH Asia 2019)**: Goal-driven scene
     interactions inform the terrain adaptation strategy.
  3. **Sebastian Starke — DeepPhase (SIGGRAPH 2022)**: Multi-dimensional
     phase space decomposition drives the per-limb phase channels.
  4. **Daniel Holden — PFNN (SIGGRAPH 2017)**: Terrain heightmap as
     first-class input to the motion controller.

Architecture:
    ┌──────────────────────────────────────────────────────────────────────┐
    │                     Motion2DPipeline                                │
    │                                                                     │
    │  ┌─────────────┐   ┌──────────────────┐   ┌───────────────────┐    │
    │  │ NSM Gait    │──▶│ Orthographic     │──▶│ Terrain IK 2D    │    │
    │  │ Planner     │   │ Projector        │   │ (FABRIK)          │    │
    │  └─────────────┘   └──────────────────┘   └───────────────────┘    │
    │         │                   │                       │               │
    │         ▼                   ▼                       ▼               │
    │  contact_labels      Pose2D + sorting       adapted_pose           │
    │                                                     │               │
    │                                              ┌──────▼──────┐       │
    │                                              │ Spine JSON  │       │
    │                                              │ Exporter    │       │
    │                                              └─────────────┘       │
    │                                                                     │
    │  ┌─────────────────────────────────────────────────────────────┐    │
    │  │ Principles Quantifier — 12-principle scoring per clip       │    │
    │  └─────────────────────────────────────────────────────────────┘    │
    │                                                                     │
    │  ┌─────────────────────────────────────────────────────────────┐    │
    │  │ Pipeline Quality Audit — end-to-end metrics                 │    │
    │  └─────────────────────────────────────────────────────────────┘    │
    └──────────────────────────────────────────────────────────────────────┘

Usage::

    from mathart.animation.motion_2d_pipeline import (
        Motion2DPipeline, PipelineConfig, PipelineResult,
    )

    pipeline = Motion2DPipeline(PipelineConfig())
    result = pipeline.run_biped_walk(n_frames=30, terrain_sdf=terrain)
    result = pipeline.run_quadruped_trot(n_frames=30, terrain_sdf=terrain)
    pipeline.export_spine_json(result, "output/walk.json")
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

import numpy as np

from .nsm_gait import (
    DistilledNeuralStateMachine,
    AsymmetricGaitProfile,
    NSMGaitFrame,
    BIPED_LIMP_RIGHT_PROFILE,
    QUADRUPED_TROT_PROFILE,
    QUADRUPED_PACE_PROFILE,
)
from .orthographic_projector import (
    OrthographicProjector,
    SpineJSONExporter,
    ProjectionConfig,
    ProjectionQualityMetrics,
    Bone3D,
    Pose3D,
    Clip3D,
    Clip2D,
    create_biped_skeleton_3d,
    create_quadruped_skeleton_3d,
    create_sample_walk_clip_3d,
)
from .terrain_ik_2d import (
    TerrainProbe2D,
    FABRIK2DSolver,
    TerrainAdaptiveIKLoop,
    IKConfig,
    IKQualityMetrics,
    create_terrain_ik_loop,
)
from .principles_quantifier import (
    PrincipleScorer,
    PrincipleReport,
    AnimFrame,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Configuration & Results
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class PipelineConfig:
    """Configuration for the unified Motion 2D Pipeline."""

    projection: ProjectionConfig = field(default_factory=ProjectionConfig)
    ik: IKConfig = field(default_factory=IKConfig)
    fps: float = 30.0
    base_stride: float = 0.8
    base_step_height: float = 0.12
    score_principles: bool = True


@dataclass
class PipelineResult:
    """Result from a complete pipeline run."""

    clip_2d: Optional[Clip2D] = None
    projection_quality: Optional[ProjectionQualityMetrics] = None
    ik_quality: Optional[IKQualityMetrics] = None
    principles_report: Optional[PrincipleReport] = None
    nsm_frames: list[NSMGaitFrame] = field(default_factory=list)
    adapted_poses: list[dict[str, Any]] = field(default_factory=list)
    total_frames: int = 0
    pipeline_pass: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_frames": self.total_frames,
            "pipeline_pass": self.pipeline_pass,
            "projection_quality": self.projection_quality.to_dict() if self.projection_quality else None,
            "ik_quality": self.ik_quality.to_dict() if self.ik_quality else None,
            "principles_report": self.principles_report.to_dict() if self.principles_report else None,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# Motion 2D Pipeline
# ═══════════════════════════════════════════════════════════════════════════════

class Motion2DPipeline:
    """End-to-end pipeline: NSM → Projection → Terrain IK → Spine Export."""

    def __init__(self, config: Optional[PipelineConfig] = None) -> None:
        self.config = config or PipelineConfig()
        self.projector = OrthographicProjector(self.config.projection)
        self.nsm = DistilledNeuralStateMachine()
        self.scorer = PrincipleScorer()
        self.exporter = SpineJSONExporter()

    def _nsm_to_3d_pose(
        self,
        frame: NSMGaitFrame,
        skeleton_bones: list[Bone3D],
        frame_idx: int,
        n_frames: int,
    ) -> Pose3D:
        """Convert an NSM gait frame to a 3D pose."""
        t = frame_idx / max(n_frames - 1, 1)
        phase = frame.global_phase * 2.0 * math.pi

        pose = Pose3D(
            root_position=(t * 2.0, frame.root_bounce, 0.0),
            root_rotation=(0.0, 0.0, frame.torso_twist * 10.0),
        )

        for limb_name, state in frame.limb_states.items():
            ox, oy = state.target_offset
            rz = ox * 50.0  # Convert offset to approximate rotation

            # Map limb names to bone names
            bone_name = limb_name
            if limb_name == "l_foot":
                bone_name = "l_thigh"
            elif limb_name == "r_foot":
                bone_name = "r_thigh"

            depth = 0.01 if "left" in limb_name or limb_name.startswith("l_") else -0.01
            pose.bone_transforms[bone_name] = {
                "tx": ox * 0.1,
                "ty": oy * 0.1,
                "tz": depth,
                "rz": rz,
                "sx": 1.0,
                "sy": 1.0,
            }

        pose.contact_labels = dict(frame.contact_labels)
        pose.metadata = {
            "nsm_profile": frame.profile_name,
            "morphology": frame.morphology,
            "global_phase": frame.global_phase,
            "speed": frame.speed,
        }
        return pose

    def _pose2d_to_anim_frame(self, pose_2d: Any, time: float) -> AnimFrame:
        """Convert a projected 2D pose to an AnimFrame for principle scoring."""
        joint_positions: dict[str, tuple[float, float]] = {}
        joint_scales: dict[str, tuple[float, float]] = {}

        for bname, xform in pose_2d.bone_transforms.items():
            joint_positions[bname] = (
                float(xform.get("tx", 0.0)),
                float(xform.get("ty", 0.0)),
            )
            joint_scales[bname] = (
                float(xform.get("sx", 1.0)),
                float(xform.get("sy", 1.0)),
            )

        return AnimFrame(
            joint_positions=joint_positions,
            joint_scales=joint_scales,
            root_position=(pose_2d.root_x, pose_2d.root_y),
            time=time,
        )

    def run_biped_walk(
        self,
        n_frames: int = 30,
        profile: Optional[AsymmetricGaitProfile] = None,
        terrain_sdf: Any = None,
        speed: float = 1.0,
    ) -> PipelineResult:
        """Run the full pipeline for a biped walk cycle."""
        prof = profile or BIPED_LIMP_RIGHT_PROFILE
        bones_3d = create_biped_skeleton_3d()
        ik_loop = create_terrain_ik_loop(terrain_sdf, self.config.ik)

        # Stage 1: NSM gait planning
        nsm_frames: list[NSMGaitFrame] = []
        poses_3d: list[Pose3D] = []
        for i in range(n_frames):
            phase = i / max(n_frames - 1, 1)
            gait_frame = self.nsm.evaluate(
                prof, phase=phase, speed=speed,
                base_stride=self.config.base_stride,
                base_height=self.config.base_step_height,
            )
            nsm_frames.append(gait_frame)
            pose_3d = self._nsm_to_3d_pose(gait_frame, bones_3d, i, n_frames)
            poses_3d.append(pose_3d)

        clip_3d = Clip3D(
            name="biped_walk",
            fps=self.config.fps,
            frames=poses_3d,
            skeleton_bones=bones_3d,
        )

        # Stage 2: Orthographic projection
        clip_2d = self.projector.project_clip(clip_3d)
        proj_quality = self.projector.evaluate_quality(clip_3d, clip_2d)

        # Stage 3: Terrain-adaptive IK
        adapted_poses: list[dict[str, Any]] = []
        ik_metrics_list: list[IKQualityMetrics] = []
        for frame_2d, nsm_frame in zip(clip_2d.frames, nsm_frames):
            pose_data: dict[str, Any] = {}
            for bname, xform in frame_2d.bone_transforms.items():
                pose_data[bname] = (
                    float(xform.get("tx", 0.0)),
                    float(xform.get("ty", 0.0)),
                )
            adapted = ik_loop.adapt_pose(
                pose_data,
                nsm_frame.contact_labels,
                hip_position=(frame_2d.root_x, frame_2d.root_y + 0.8),
            )
            adapted_poses.append(adapted)
            ik_m = ik_loop.evaluate_ik_quality(
                pose_data, adapted, nsm_frame.contact_labels,
            )
            ik_metrics_list.append(ik_m)

        # Aggregate IK metrics
        avg_ik = IKQualityMetrics(
            foot_terrain_error=float(np.mean([m.foot_terrain_error for m in ik_metrics_list])) if ik_metrics_list else 0.0,
            hip_height_delta=float(np.mean([m.hip_height_delta for m in ik_metrics_list])) if ik_metrics_list else 0.0,
            convergence_iterations=float(np.mean([m.convergence_iterations for m in ik_metrics_list])) if ik_metrics_list else 0.0,
            contact_accuracy=float(np.mean([m.contact_accuracy for m in ik_metrics_list])) if ik_metrics_list else 0.0,
            total_chains_solved=sum(m.total_chains_solved for m in ik_metrics_list),
        )

        # Stage 4: Principles scoring
        principles_report = None
        if self.config.score_principles:
            dt = 1.0 / max(self.config.fps, 1.0)
            anim_frames = [
                self._pose2d_to_anim_frame(f, i * dt)
                for i, f in enumerate(clip_2d.frames)
            ]
            principles_report = self.scorer.score_clip(anim_frames)

        # Add IK constraints for legs
        clip_2d.ik_constraints = [
            {"name": "left_leg_ik", "order": 0, "bones": ["l_thigh", "l_shin"], "target": "l_foot", "mix": 1.0, "bendPositive": False},
            {"name": "right_leg_ik", "order": 1, "bones": ["r_thigh", "r_shin"], "target": "r_foot", "mix": 1.0, "bendPositive": False},
        ]

        pipeline_pass = (
            proj_quality.bone_length_preservation > 0.95
            and proj_quality.joint_angle_fidelity > 0.90
            and avg_ik.contact_accuracy >= 0.8
        )

        return PipelineResult(
            clip_2d=clip_2d,
            projection_quality=proj_quality,
            ik_quality=avg_ik,
            principles_report=principles_report,
            nsm_frames=nsm_frames,
            adapted_poses=adapted_poses,
            total_frames=n_frames,
            pipeline_pass=pipeline_pass,
        )

    def run_quadruped_trot(
        self,
        n_frames: int = 30,
        profile: Optional[AsymmetricGaitProfile] = None,
        terrain_sdf: Any = None,
        speed: float = 1.0,
    ) -> PipelineResult:
        """Run the full pipeline for a quadruped trot cycle."""
        prof = profile or QUADRUPED_TROT_PROFILE
        bones_3d = create_quadruped_skeleton_3d()
        ik_loop = create_terrain_ik_loop(terrain_sdf, self.config.ik)

        nsm_frames: list[NSMGaitFrame] = []
        poses_3d: list[Pose3D] = []
        for i in range(n_frames):
            phase = i / max(n_frames - 1, 1)
            gait_frame = self.nsm.evaluate(
                prof, phase=phase, speed=speed,
                base_stride=1.2 * 0.35,
                base_height=0.14,
            )
            nsm_frames.append(gait_frame)
            pose_3d = self._nsm_to_3d_pose(gait_frame, bones_3d, i, n_frames)
            poses_3d.append(pose_3d)

        clip_3d = Clip3D(
            name="quadruped_trot",
            fps=self.config.fps,
            frames=poses_3d,
            skeleton_bones=bones_3d,
        )

        clip_2d = self.projector.project_clip(clip_3d)
        proj_quality = self.projector.evaluate_quality(clip_3d, clip_2d)

        # Quadruped terrain IK
        adapted_poses: list[dict[str, Any]] = []
        for frame_2d, nsm_frame in zip(clip_2d.frames, nsm_frames):
            pose_data: dict[str, Any] = {}
            for bname, xform in frame_2d.bone_transforms.items():
                pose_data[bname] = (
                    float(xform.get("tx", 0.0)),
                    float(xform.get("ty", 0.0)),
                )
            adapted = ik_loop.adapt_quadruped_pose(
                pose_data,
                nsm_frame.contact_labels,
                spine_position=(frame_2d.root_x, frame_2d.root_y + 0.5),
            )
            adapted_poses.append(adapted)

        # Add quadruped IK constraints
        clip_2d.ik_constraints = [
            {"name": "fl_leg_ik", "order": 0, "bones": ["fl_upper", "fl_lower"], "target": "fl_paw", "mix": 1.0},
            {"name": "fr_leg_ik", "order": 1, "bones": ["fr_upper", "fr_lower"], "target": "fr_paw", "mix": 1.0},
            {"name": "hl_leg_ik", "order": 2, "bones": ["hl_upper", "hl_lower"], "target": "hl_paw", "mix": 1.0},
            {"name": "hr_leg_ik", "order": 3, "bones": ["hr_upper", "hr_lower"], "target": "hr_paw", "mix": 1.0},
        ]

        principles_report = None
        if self.config.score_principles:
            dt = 1.0 / max(self.config.fps, 1.0)
            anim_frames = [
                self._pose2d_to_anim_frame(f, i * dt)
                for i, f in enumerate(clip_2d.frames)
            ]
            principles_report = self.scorer.score_clip(anim_frames)

        pipeline_pass = (
            proj_quality.bone_length_preservation > 0.95
            and proj_quality.joint_angle_fidelity > 0.90
        )

        return PipelineResult(
            clip_2d=clip_2d,
            projection_quality=proj_quality,
            ik_quality=None,
            principles_report=principles_report,
            nsm_frames=nsm_frames,
            adapted_poses=adapted_poses,
            total_frames=n_frames,
            pipeline_pass=pipeline_pass,
        )

    def export_spine_json(
        self,
        result: PipelineResult,
        output_path: str | Path,
    ) -> Path:
        """Export a pipeline result to Spine JSON."""
        if result.clip_2d is None:
            raise ValueError("No clip_2d in pipeline result")
        return self.exporter.export(result.clip_2d, output_path)


__all__ = [
    "PipelineConfig",
    "PipelineResult",
    "Motion2DPipeline",
]
