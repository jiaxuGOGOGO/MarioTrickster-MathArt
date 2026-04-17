"""
SESSION-061: Tests for the Motion 2D Pipeline (Orthographic Projection,
Terrain IK 2D, Principles Quantifier, and end-to-end pipeline).
"""
import json
import math
import tempfile
from pathlib import Path

import pytest
import numpy as np

# ── Orthographic Projector ──────────────────────────────────────────────────

from mathart.animation.orthographic_projector import (
    OrthographicProjector,
    SpineJSONExporter,
    ProjectionConfig,
    ProjectionQualityMetrics,
    Bone3D,
    Bone2D,
    Pose3D,
    Pose2D,
    Clip3D,
    Clip2D,
    create_biped_skeleton_3d,
    create_quadruped_skeleton_3d,
    create_sample_walk_clip_3d,
)


class TestOrthographicProjector:
    def test_depth_to_sorting_order_range(self):
        proj = OrthographicProjector(ProjectionConfig(sorting_layers=16, depth_range=(-2.0, 2.0)))
        assert proj.depth_to_sorting_order(-2.0) == 15  # closest → highest order
        assert proj.depth_to_sorting_order(2.0) == 0   # farthest → lowest order
        mid = proj.depth_to_sorting_order(0.0)
        assert 6 <= mid <= 9

    def test_project_bone_preserves_xy(self):
        proj = OrthographicProjector(ProjectionConfig(scale=1.0))
        bone = Bone3D("test", None, (1.0, 2.0, 0.5), (10.0, 20.0, 30.0), 0.5)
        b2d = proj.project_bone(bone)
        assert b2d.name == "test"
        assert abs(b2d.x - 1.0) < 1e-6
        assert abs(b2d.y - 2.0) < 1e-6
        assert abs(b2d.rotation - 30.0) < 1e-6  # Z rotation preserved

    def test_project_skeleton_count(self):
        proj = OrthographicProjector()
        bones_3d = create_biped_skeleton_3d()
        bones_2d = proj.project_skeleton(bones_3d)
        assert len(bones_2d) == len(bones_3d)

    def test_project_clip_frame_count(self):
        proj = OrthographicProjector()
        clip_3d = create_sample_walk_clip_3d(n_frames=20)
        clip_2d = proj.project_clip(clip_3d)
        assert len(clip_2d.frames) == 20
        assert clip_2d.fps == clip_3d.fps

    def test_evaluate_quality_metrics(self):
        proj = OrthographicProjector()
        clip_3d = create_sample_walk_clip_3d(n_frames=10)
        clip_2d = proj.project_clip(clip_3d)
        metrics = proj.evaluate_quality(clip_3d, clip_2d)
        assert metrics.bone_length_preservation > 0.9
        assert metrics.joint_angle_fidelity > 0.9
        assert metrics.sorting_order_stability > 0.0
        assert metrics.total_frames == 10


class TestSpineJSONExporter:
    def test_export_creates_valid_json(self):
        proj = OrthographicProjector()
        clip_3d = create_sample_walk_clip_3d(n_frames=5)
        clip_2d = proj.project_clip(clip_3d)
        clip_2d.ik_constraints = [
            {"name": "left_leg", "order": 0, "bones": ["l_thigh", "l_shin"], "target": "l_foot", "mix": 1.0},
        ]

        exporter = SpineJSONExporter()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = exporter.export(clip_2d, Path(tmpdir) / "test.json")
            assert path.exists()
            data = json.loads(path.read_text())
            assert "skeleton" in data
            assert "bones" in data
            assert "slots" in data
            assert "ik" in data
            assert "animations" in data
            assert len(data["bones"]) == len(clip_2d.skeleton_bones)


class TestQuadrupedSkeleton:
    def test_quadruped_skeleton_has_four_legs(self):
        bones = create_quadruped_skeleton_3d()
        names = [b.name for b in bones]
        for prefix in ("fl_", "fr_", "hl_", "hr_"):
            assert any(n.startswith(prefix) for n in names), f"Missing {prefix} bones"


# ── Terrain IK 2D ──────────────────────────────────────────────────────────

from mathart.animation.terrain_ik_2d import (
    TerrainProbe2D,
    Joint2D,
    FABRIK2DSolver,
    TerrainAdaptiveIKLoop,
    IKConfig,
    IKQualityMetrics,
    create_terrain_ik_loop,
)


class TestTerrainProbe2D:
    def test_flat_ground_returns_zero(self):
        probe = TerrainProbe2D(None)
        assert abs(probe.probe_ground(0.0)) < 1e-6

    def test_probe_ahead_returns_samples(self):
        probe = TerrainProbe2D(None)
        samples = probe.probe_ahead(0.0, lookahead=1.0, n_samples=5)
        assert len(samples) == 5
        assert all(abs(y) < 1e-6 for _, y in samples)


class TestFABRIK2DSolver:
    def test_solve_reaches_target(self):
        solver = FABRIK2DSolver(IKConfig(max_iterations=20, tolerance=0.001))
        chain = [
            Joint2D(0.0, 0.0, "hip"),
            Joint2D(0.0, -0.3, "knee"),
            Joint2D(0.0, -0.6, "ankle"),
        ]
        target = Joint2D(0.1, -0.55, "target")
        solved, iters = solver.solve(chain, target)
        end = solved[-1]
        dist = math.sqrt((end.x - target.x) ** 2 + (end.y - target.y) ** 2)
        assert dist < 0.01, f"End effector too far from target: {dist}"
        assert iters > 0

    def test_unreachable_target_stretches(self):
        solver = FABRIK2DSolver(IKConfig(max_iterations=10))
        chain = [
            Joint2D(0.0, 0.0, "a"),
            Joint2D(0.0, -0.3, "b"),
            Joint2D(0.0, -0.6, "c"),
        ]
        target = Joint2D(5.0, 0.0, "far")
        solved, _ = solver.solve(chain, target)
        # Should stretch towards target
        assert solved[-1].x > 0.0

    def test_solve_with_constraints(self):
        solver = FABRIK2DSolver(IKConfig(max_iterations=15))
        chain = [
            Joint2D(0.0, 0.0, "hip"),
            Joint2D(0.0, -0.3, "knee"),
            Joint2D(0.0, -0.6, "ankle"),
        ]
        target = Joint2D(0.05, -0.55, "target")
        limits = [(-170.0, -10.0)]  # Knee bends backward
        solved, iters = solver.solve_with_constraints(chain, target, limits)
        assert len(solved) == 3


class TestTerrainAdaptiveIKLoop:
    def test_adapt_pose_flat_ground(self):
        loop = create_terrain_ik_loop(None)
        pose_data = {
            "l_hip": (0.0, 0.8),
            "l_knee": (0.0, 0.5),
            "l_ankle": (0.0, 0.2),
            "r_hip": (0.0, 0.8),
            "r_knee": (0.0, 0.5),
            "r_ankle": (0.0, 0.2),
        }
        contacts = {"l_foot": 1.0, "r_foot": 0.0}
        adapted = loop.adapt_pose(pose_data, contacts)
        assert "_contacts_solved" in adapted
        assert adapted["_contacts_solved"] == 1  # Only left foot in contact

    def test_adapt_quadruped_pose(self):
        loop = create_terrain_ik_loop(None)
        pose_data = {
            "fl_upper": (0.3, 0.45),
            "fl_lower": (0.3, 0.25),
            "fl_paw": (0.3, 0.05),
            "hr_upper": (-0.3, 0.45),
            "hr_lower": (-0.3, 0.25),
            "hr_paw": (-0.3, 0.05),
        }
        contacts = {"front_left": 1.0, "hind_right": 1.0, "front_right": 0.0, "hind_left": 0.0}
        adapted = loop.adapt_quadruped_pose(pose_data, contacts)
        assert adapted["_quadruped_contacts_solved"] == 2

    def test_evaluate_ik_quality(self):
        loop = create_terrain_ik_loop(None)
        original = {"l_ankle": (0.0, 0.2), "r_ankle": (0.0, 0.2)}
        adapted = {"l_ankle": (0.0, 0.0), "r_ankle": (0.0, 0.2), "_hip_adjustment": -0.1, "_ik_iterations": 5, "_contacts_solved": 1}
        contacts = {"l_foot": 1.0, "r_foot": 0.0}
        metrics = loop.evaluate_ik_quality(original, adapted, contacts)
        assert metrics.total_chains_solved == 1
        assert metrics.contact_accuracy == 1.0


# ── Principles Quantifier ──────────────────────────────────────────────────

from mathart.animation.principles_quantifier import (
    PrincipleScorer,
    PrincipleReport,
    AnimFrame,
)


class TestPrincipleScorer:
    def _make_walk_frames(self, n: int = 20) -> list[AnimFrame]:
        frames = []
        for i in range(n):
            t = i / max(n - 1, 1)
            phase = t * 2.0 * math.pi
            frames.append(AnimFrame(
                joint_positions={
                    "hip": (t * 2.0, 0.8 + 0.02 * math.sin(4 * phase)),
                    "l_foot": (t * 2.0 - 0.1, 0.0 + max(0, math.sin(phase)) * 0.1),
                    "r_foot": (t * 2.0 + 0.1, 0.0 + max(0, -math.sin(phase)) * 0.1),
                    "head": (t * 2.0, 1.2),
                    "l_arm": (t * 2.0 - 0.15, 0.9 - 0.05 * math.sin(phase)),
                    "r_arm": (t * 2.0 + 0.15, 0.9 + 0.05 * math.sin(phase)),
                },
                joint_scales={
                    "hip": (1.0, 1.0),
                    "l_foot": (1.0 + 0.05 * max(0, -math.sin(phase)), 1.0 - 0.05 * max(0, -math.sin(phase))),
                    "r_foot": (1.0 + 0.05 * max(0, math.sin(phase)), 1.0 - 0.05 * max(0, math.sin(phase))),
                },
                root_position=(t * 2.0, 0.8 + 0.02 * math.sin(4 * phase)),
                time=t,
            ))
        return frames

    def test_score_clip_returns_report(self):
        scorer = PrincipleScorer()
        frames = self._make_walk_frames(20)
        report = scorer.score_clip(frames)
        assert isinstance(report, PrincipleReport)
        assert report.frame_count == 20
        assert 0.0 <= report.aggregate_score <= 1.0

    def test_squash_stretch_volume_preservation(self):
        scorer = PrincipleScorer()
        frames = self._make_walk_frames(10)
        score = scorer.score_squash_stretch(frames)
        assert score > 0.8  # Near-perfect volume preservation

    def test_arcs_smooth_trajectories(self):
        scorer = PrincipleScorer()
        frames = self._make_walk_frames(20)
        score = scorer.score_arcs(frames)
        assert score > 0.3

    def test_solid_drawing_bone_stability(self):
        scorer = PrincipleScorer()
        frames = self._make_walk_frames(20)
        score = scorer.score_solid_drawing(frames)
        assert score > 0.0

    def test_report_to_dict(self):
        scorer = PrincipleScorer()
        frames = self._make_walk_frames(10)
        report = scorer.score_clip(frames)
        d = report.to_dict()
        assert "aggregate_score" in d
        assert "squash_stretch" in d


# ── End-to-End Pipeline ────────────────────────────────────────────────────

from mathart.animation.motion_2d_pipeline import (
    Motion2DPipeline,
    PipelineConfig,
    PipelineResult,
)


class TestMotion2DPipeline:
    def test_biped_walk_pipeline(self):
        pipeline = Motion2DPipeline(PipelineConfig())
        result = pipeline.run_biped_walk(n_frames=15)
        assert result.total_frames == 15
        assert result.clip_2d is not None
        assert len(result.clip_2d.frames) == 15
        assert result.projection_quality is not None
        assert result.ik_quality is not None
        assert result.principles_report is not None
        assert len(result.nsm_frames) == 15

    def test_quadruped_trot_pipeline(self):
        pipeline = Motion2DPipeline(PipelineConfig())
        result = pipeline.run_quadruped_trot(n_frames=15)
        assert result.total_frames == 15
        assert result.clip_2d is not None
        assert len(result.clip_2d.frames) == 15
        assert result.projection_quality is not None
        assert result.principles_report is not None

    def test_spine_json_export(self):
        pipeline = Motion2DPipeline(PipelineConfig())
        result = pipeline.run_biped_walk(n_frames=10)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = pipeline.export_spine_json(result, Path(tmpdir) / "walk.json")
            assert path.exists()
            data = json.loads(path.read_text())
            assert "skeleton" in data
            assert "animations" in data
            assert "ik" in data

    def test_pipeline_result_to_dict(self):
        pipeline = Motion2DPipeline(PipelineConfig())
        result = pipeline.run_biped_walk(n_frames=5)
        d = result.to_dict()
        assert "total_frames" in d
        assert "pipeline_pass" in d
        assert "projection_quality" in d
