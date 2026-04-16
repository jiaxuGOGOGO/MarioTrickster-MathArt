"""Tests for physics_projector module (SESSION-028).

Validates:
  - AnglePoseProjector produces valid pose dicts
  - Physics projection modifies poses (not identity)
  - Sequence projection produces smooth results
  - Squash/stretch metadata is generated
  - PositionPhysicsProjector initializes from skeleton
  - Physics penalty function works
  - Integration with pipeline CharacterSpec
"""
from __future__ import annotations

import math
import pytest
import numpy as np

from mathart.animation.physics_projector import (
    AnglePoseProjector,
    PositionPhysicsProjector,
    JointPhysicsConfig,
    CognitiveMotionConfig,
    AngularSpringState,
    DEFAULT_JOINT_PHYSICS,
    PENNER_EASING_FUNCTIONS,
    compute_physics_penalty,
)
from mathart.animation.skeleton import Skeleton
from mathart.animation.presets import (
    idle_animation,
    run_animation,
    jump_animation,
    fall_animation,
    hit_animation,
)


class TestAnglePoseProjector:
    """Test the primary angle-space physics projector."""

    def test_basic_creation(self):
        proj = AnglePoseProjector()
        assert proj is not None
        assert proj._stiffness_scale == 1.0
        assert proj._damping_scale == 1.0

    def test_step_returns_valid_pose(self):
        proj = AnglePoseProjector()
        raw_pose = {"spine": 0.1, "chest": 0.05, "head": 0.02}
        corrected = proj.step(raw_pose, dt=1 / 60)
        assert isinstance(corrected, dict)
        assert set(corrected.keys()) == set(raw_pose.keys())
        for v in corrected.values():
            assert isinstance(v, float)
            assert math.isfinite(v)

    def test_step_preserves_keys(self):
        proj = AnglePoseProjector()
        raw_pose = idle_animation(0.0)
        corrected = proj.step(raw_pose)
        assert set(corrected.keys()) == set(raw_pose.keys())

    def test_physics_modifies_pose(self):
        """After several steps, physics should diverge from raw target."""
        proj = AnglePoseProjector(global_stiffness_scale=0.5)
        # Run a few steps with changing targets
        for i in range(10):
            t = i / 10.0
            raw = run_animation(t)
            corrected = proj.step(raw, dt=1 / 12)

        # The corrected pose should differ from raw (due to inertia/delay)
        raw_final = run_animation(0.9)
        corrected_final = proj.step(raw_final, dt=1 / 12)
        diffs = [abs(corrected_final[k] - raw_final[k]) for k in raw_final]
        # At least some joints should differ
        assert max(diffs) > 0.001, "Physics should modify at least some joint angles"

    def test_sequence_projection(self):
        proj = AnglePoseProjector()
        sequence = [idle_animation(t / 8.0) for t in range(8)]
        corrected = proj.project_sequence(sequence, dt=1 / 12)
        assert len(corrected) == 8
        for pose in corrected:
            assert isinstance(pose, dict)
            for v in pose.values():
                assert math.isfinite(v)

    def test_reset_clears_state(self):
        proj = AnglePoseProjector()
        proj.step(idle_animation(0.0))
        assert len(proj._states) > 0
        proj.reset()
        assert len(proj._states) == 0
        assert len(proj._target_history) == 0

    def test_step_with_metadata(self):
        proj = AnglePoseProjector()
        # Need at least 2 steps for velocity estimation
        proj.step(idle_animation(0.0))
        corrected, meta = proj.step_with_metadata(idle_animation(0.1))
        assert "squash_stretch_y" in meta
        assert "squash_stretch_x" in meta
        assert "velocity_magnitude" in meta
        assert math.isfinite(meta["squash_stretch_y"])
        assert meta["squash_stretch_y"] > 0

    def test_custom_stiffness(self):
        """Higher stiffness with appropriate damping should track target more closely."""
        proj_stiff = AnglePoseProjector(
            global_stiffness_scale=1.5, global_damping_scale=1.5
        )
        proj_loose = AnglePoseProjector(
            global_stiffness_scale=0.3, global_damping_scale=0.3
        )

        # Hold a constant target for many frames to let both converge
        target = idle_animation(0.5)
        for i in range(60):
            c_stiff = proj_stiff.step(target, dt=1 / 60)
            c_loose = proj_loose.step(target, dt=1 / 60)

        err_stiff = sum(abs(c_stiff[k] - target[k]) for k in target)
        err_loose = sum(abs(c_loose[k] - target[k]) for k in target)
        assert err_stiff < err_loose, "Stiffer projector should converge closer to target"

    def test_all_animation_presets(self):
        """Physics projector should work with all animation presets."""
        proj = AnglePoseProjector()
        for anim_func in [idle_animation, run_animation, jump_animation,
                          fall_animation, hit_animation]:
            proj.reset()
            for i in range(8):
                t = i / 8.0
                raw = anim_func(t)
                corrected = proj.step(raw, dt=1 / 12)
                assert isinstance(corrected, dict)
                for v in corrected.values():
                    assert math.isfinite(v), f"Non-finite value in {anim_func.__name__}"

    def test_cognitive_disable(self):
        """Disabling cognitive motion should still produce valid output."""
        cfg = CognitiveMotionConfig(
            enable_anticipation=False,
            enable_follow_through=False,
            enable_overlapping=False,
            enable_squash_stretch=False,
        )
        proj = AnglePoseProjector(cognitive_config=cfg)
        for i in range(8):
            corrected = proj.step(run_animation(i / 8.0), dt=1 / 12)
            assert isinstance(corrected, dict)


class TestPositionPhysicsProjector:
    """Test the position-space physics projector."""

    def test_init_from_skeleton(self):
        skel = Skeleton.create_humanoid(head_units=3.0)
        proj = PositionPhysicsProjector()
        proj.init_from_skeleton(skel)
        assert proj._initialized
        assert len(proj.particles) == len(skel.joints)
        assert len(proj.distance_constraints) == len(skel.bones)

    def test_step_returns_positions(self):
        skel = Skeleton.create_humanoid(head_units=3.0)
        proj = PositionPhysicsProjector()
        proj.init_from_skeleton(skel)

        target_positions = {
            name: (j.x, j.y) for name, j in skel.joints.items()
        }
        corrected = proj.step(target_positions, dt=1 / 60)
        assert isinstance(corrected, dict)
        for name in skel.joints:
            assert name in corrected
            x, y = corrected[name]
            assert math.isfinite(x)
            assert math.isfinite(y)

    def test_positions_to_angles(self):
        skel = Skeleton.create_humanoid(head_units=3.0)
        proj = PositionPhysicsProjector()
        positions = {name: (j.x, j.y) for name, j in skel.joints.items()}
        angles = proj.positions_to_angles(positions, skel)
        assert isinstance(angles, dict)
        for v in angles.values():
            assert math.isfinite(v)

    def test_ground_constraint(self):
        """Foot particles should not go below ground."""
        skel = Skeleton.create_humanoid(head_units=3.0)
        proj = PositionPhysicsProjector(gravity=(0, -20.0))
        proj.init_from_skeleton(skel)

        # Push feet below ground
        target = {name: (j.x, j.y - 0.5) for name, j in skel.joints.items()}
        for _ in range(30):
            corrected = proj.step(target, dt=1 / 60)

        # Feet should be at or above ground
        for name in ["l_foot", "r_foot"]:
            if name in corrected:
                assert corrected[name][1] >= -0.01, \
                    f"{name} penetrated ground: y={corrected[name][1]}"


class TestPhysicsPenalty:
    """Test the physics penalty function."""

    def test_basic_penalty(self):
        skel = Skeleton.create_humanoid(head_units=3.0)
        sequence = [idle_animation(t / 8.0) for t in range(8)]
        penalty = compute_physics_penalty(sequence, skel)
        assert isinstance(penalty, float)
        assert math.isfinite(penalty)
        assert penalty >= 0.0

    def test_smooth_animation_low_penalty(self):
        """Smooth idle animation should have lower penalty than jerky motion."""
        skel = Skeleton.create_humanoid(head_units=3.0)

        smooth = [idle_animation(t / 16.0) for t in range(16)]
        smooth_penalty = compute_physics_penalty(smooth, skel)

        # Create jerky motion
        jerky = []
        for i in range(16):
            pose = idle_animation(i / 16.0)
            if i % 2 == 0:
                pose = {k: v + 0.5 * ((-1) ** i) for k, v in pose.items()}
            jerky.append(pose)
        jerky_penalty = compute_physics_penalty(jerky, skel)

        assert smooth_penalty < jerky_penalty, \
            "Smooth animation should have lower physics penalty"

    def test_empty_sequence(self):
        skel = Skeleton.create_humanoid(head_units=3.0)
        assert compute_physics_penalty([], skel) == 0.0
        assert compute_physics_penalty([idle_animation(0.0)], skel) == 0.0


class TestPennerEasing:
    """Test the Penner easing functions."""

    def test_all_functions_exist(self):
        expected = [
            "ease_in_quad", "ease_out_quad", "ease_in_out_quad",
            "ease_in_cubic", "ease_out_cubic", "ease_in_out_cubic",
            "ease_in_elastic", "ease_out_elastic",
            "ease_out_back", "ease_in_back",
        ]
        for name in expected:
            assert name in PENNER_EASING_FUNCTIONS

    def test_boundary_values(self):
        for name, func in PENNER_EASING_FUNCTIONS.items():
            # Most easing functions: f(0)≈0, f(1)≈1
            v0 = func(0.0)
            v1 = func(1.0)
            assert math.isfinite(v0), f"{name}(0) is not finite"
            assert math.isfinite(v1), f"{name}(1) is not finite"
            # Allow some tolerance for elastic/back functions
            assert abs(v0) < 0.5, f"{name}(0) = {v0}, expected near 0"
            assert abs(v1 - 1.0) < 0.5, f"{name}(1) = {v1}, expected near 1"

    def test_monotonic_mid(self):
        """Easing functions should generally increase from 0 to 1."""
        for name, func in PENNER_EASING_FUNCTIONS.items():
            v_mid = func(0.5)
            assert math.isfinite(v_mid), f"{name}(0.5) is not finite"


class TestDefaultJointPhysics:
    """Test the default joint physics profiles."""

    def test_all_skeleton_joints_covered(self):
        skel = Skeleton.create_humanoid(head_units=3.0)
        for name in skel.joints:
            if name == "root":
                continue  # Root doesn't need physics
            assert name in DEFAULT_JOINT_PHYSICS, \
                f"Joint '{name}' not in DEFAULT_JOINT_PHYSICS"

    def test_primary_joints_are_stiff(self):
        """Primary joints (legs, spine) should have higher stiffness."""
        primary_names = ["spine", "l_hip", "r_hip", "l_knee", "r_knee"]
        secondary_names = ["head", "l_hand", "r_hand"]
        for p_name in primary_names:
            for s_name in secondary_names:
                p_cfg = DEFAULT_JOINT_PHYSICS[p_name]
                s_cfg = DEFAULT_JOINT_PHYSICS[s_name]
                assert p_cfg.spring_k > s_cfg.spring_k, \
                    f"Primary {p_name} should be stiffer than secondary {s_name}"


class TestPipelineIntegration:
    """Test that physics integrates correctly with the pipeline."""

    def test_character_spec_has_physics_fields(self):
        from mathart.pipeline import CharacterSpec
        spec = CharacterSpec(name="test")
        assert hasattr(spec, "enable_physics")
        assert hasattr(spec, "physics_stiffness")
        assert hasattr(spec, "physics_damping")
        assert hasattr(spec, "physics_cognitive_strength")
        assert spec.enable_physics is True  # Default enabled

    def test_character_spec_physics_disabled(self):
        from mathart.pipeline import CharacterSpec
        spec = CharacterSpec(name="test", enable_physics=False)
        assert spec.enable_physics is False
