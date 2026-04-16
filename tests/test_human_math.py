"""SESSION-031 tests for the distilled human-math stack.

These tests verify that the repository now supports a compact, 2D-compatible
fusion of:
- SMPL/SMPL-X style body parameterization
- VPoser-like anatomical pose regularization
- Dual-quaternion transform blending
- Motion matching over compact pose features
- Layer 3 physics fitness integration
"""
from __future__ import annotations

import math

import numpy as np

from mathart.animation.human_math import (
    SMPLShapeLatent,
    DistilledSMPLBodyModel,
    VPoserDistilledPrior,
    DualQuaternion,
    MotionMatcher2D,
    DistilledHumanMathRuntime,
)
from mathart.animation.skeleton import Skeleton
from mathart.animation.physics_genotype import (
    create_physics_genotype,
    create_locomotion_genotype,
    evaluate_physics_fitness,
)


class TestDistilledSMPLBodyModel:
    def test_shape_to_proportion_modifiers_changes_body(self):
        shape = SMPLShapeLatent(
            stature=0.6,
            shoulder_width=0.8,
            head_scale=-0.3,
            limb_thickness=0.5,
        )
        mods = DistilledSMPLBodyModel.shape_to_proportion_modifiers(shape)
        assert set(mods.keys()) == {
            "head_radius_mod", "torso_width_mod", "torso_height_mod",
            "arm_thickness_mod", "leg_thickness_mod", "hand_radius_mod",
            "foot_width_mod", "foot_height_mod",
        }
        assert mods["torso_width_mod"] > 0.0
        assert mods["arm_thickness_mod"] > 0.0

    def test_apply_shape_to_skeleton_deforms_joint_layout(self):
        skel = Skeleton.create_humanoid(head_units=3.0)
        model = DistilledSMPLBodyModel()
        shape = SMPLShapeLatent(shoulder_width=0.9, leg_length=0.8, torso_height=0.5)
        deformed = model.apply_shape_to_skeleton(skel, shape)
        assert deformed is not skel
        assert deformed.joints["l_shoulder"].x < skel.joints["l_shoulder"].x
        assert deformed.joints["r_shoulder"].x > skel.joints["r_shoulder"].x
        assert deformed.joints["l_foot"].y >= skel.joints["root"].y


class TestVPoserDistilledPrior:
    def test_project_pose_fixes_impossible_hinge_directions(self):
        skel = Skeleton.create_humanoid(head_units=3.0)
        prior = VPoserDistilledPrior()
        raw_pose = {
            "l_elbow": -1.0,
            "r_elbow": -0.7,
            "l_knee": 0.9,
            "r_knee": 1.1,
            "spine": 0.4,
            "chest": -0.5,
        }
        projected = prior.project_pose(raw_pose, skeleton=skel)
        assert projected["l_elbow"] >= 0.0
        assert projected["r_elbow"] >= 0.0
        assert projected["l_knee"] <= 0.0
        assert projected["r_knee"] <= 0.0

    def test_score_pose_rewards_projected_pose(self):
        skel = Skeleton.create_humanoid(head_units=3.0)
        prior = VPoserDistilledPrior()
        raw_pose = {"l_elbow": -1.2, "r_knee": 1.0, "head": 2.0}
        projected = prior.project_pose(raw_pose, skeleton=skel)
        raw_score = prior.score_pose(raw_pose, skeleton=skel).total
        proj_score = prior.score_pose(projected, skeleton=skel).total
        assert proj_score >= raw_score

    def test_skeleton_apply_pose_can_use_prior(self):
        skel = Skeleton.create_humanoid(head_units=3.0)
        skel.apply_pose({"l_elbow": -1.0, "l_knee": 1.0}, use_pose_prior=True)
        assert skel.joints["l_elbow"].angle >= 0.0
        assert skel.joints["l_knee"].angle <= 0.0


class TestDualQuaternion:
    def test_transform_point_with_z_rotation(self):
        dq = DualQuaternion.from_z_rotation(math.pi / 2.0, translation_xy=(1.0, 2.0))
        point = dq.transform_point((1.0, 0.0, 0.0))
        assert np.allclose(point[:2], np.array([1.0, 3.0]), atol=1e-5)

    def test_blend_interpolates_translation(self):
        a = DualQuaternion.from_z_rotation(0.0, translation_xy=(0.0, 0.0))
        b = DualQuaternion.from_z_rotation(0.0, translation_xy=(2.0, 0.0))
        blended = DualQuaternion.blend([(0.5, a), (0.5, b)])
        point = blended.transform_point((0.0, 0.0, 0.0))
        assert np.allclose(point[:2], np.array([1.0, 0.0]), atol=1e-5)


class TestMotionMatcher2D:
    def test_query_prefers_matching_clip_family(self):
        walk_seq = [
            {"hip": 0.0, "l_knee": -0.2, "r_knee": -0.4, "l_foot": 0.1, "r_foot": -0.1},
            {"hip": 0.05, "l_knee": -0.3, "r_knee": -0.2, "l_foot": 0.2, "r_foot": -0.2},
        ]
        run_seq = [
            {"hip": 0.2, "l_knee": -0.7, "r_knee": -0.9, "l_foot": 0.5, "r_foot": -0.5},
            {"hip": 0.25, "l_knee": -0.8, "r_knee": -0.7, "l_foot": 0.6, "r_foot": -0.6},
        ]
        matcher = MotionMatcher2D()
        matcher.build_from_clips(
            {"walk": walk_seq, "run": run_seq},
            trajectory_hints={"walk": (0.8, 0.0, 1.0, 0.0), "run": (1.6, 0.0, 1.0, 0.0)},
        )
        result = matcher.query(run_seq[0], desired_trajectory=(1.6, 0.0, 1.0, 0.0))
        assert result.clip_name == "run"
        assert 0.0 <= result.similarity <= 1.0


class TestDistilledHumanMathRuntime:
    def test_runtime_process_pose_returns_match_and_prior_scores(self):
        clips = {
            "idle": [{"hip": 0.0, "l_knee": -0.1, "r_knee": -0.1, "l_elbow": 0.2, "r_elbow": 0.2}],
            "run": [{"hip": 0.2, "l_knee": -0.7, "r_knee": -0.8, "l_elbow": 0.7, "r_elbow": 0.7}],
        }
        runtime = DistilledHumanMathRuntime(motion_clips=clips)
        result = runtime.process_pose(
            {"hip": 0.2, "l_knee": 0.9, "r_knee": -0.8, "l_elbow": -0.6, "r_elbow": 0.7},
            desired_trajectory=(1.6, 0.0, 1.0, 0.0),
        )
        assert "projected_pose" in result
        assert "pose_prior_score" in result
        assert "motion_match" in result
        assert result["projected_pose"]["l_knee"] <= 0.0
        assert result["projected_pose"]["l_elbow"] >= 0.0


class TestLayer3FitnessIntegration:
    def test_evaluate_physics_fitness_reports_new_human_math_metrics(self):
        physics_geno = create_physics_genotype("hero")
        loco_geno = create_locomotion_genotype("hero")
        fitness = evaluate_physics_fitness(physics_geno, loco_geno, n_eval_steps=12)
        assert "anatomical_score" in fitness
        assert "motion_match_score" in fitness
        assert 0.0 <= fitness["anatomical_score"] <= 1.0
        assert 0.0 <= fitness["motion_match_score"] <= 1.0
        assert 0.0 <= fitness["overall"] <= 1.0
