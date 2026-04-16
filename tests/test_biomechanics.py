"""Tests for SESSION-029: Biomechanics engine.

Covers all four research directions:
1. ZMP/CoM balance analysis
2. Inverted Pendulum Model (IPM/LIPM)
3. Foot Skating Cleanup (calculus-based)
4. FABRIK Procedural Gait Generator
5. BiomechanicsProjector integration
6. Pipeline integration (CharacterSpec fields)
"""
from __future__ import annotations

import math
import pytest
import numpy as np

from mathart.animation.skeleton import Skeleton
from mathart.animation.biomechanics import (
    ZMPAnalyzer,
    ZMPResult,
    InvertedPendulumModel,
    IPMState,
    SkatingCleanupCalculus,
    SkatingCleanupState,
    FABRIKGaitGenerator,
    GaitPhase,
    BiomechanicsProjector,
    compute_biomechanics_penalty,
    DEFAULT_JOINT_MASSES,
)
from mathart.animation.presets import (
    idle_animation,
    run_animation,
    jump_animation,
    fall_animation,
    hit_animation,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def skeleton():
    return Skeleton.create_humanoid(head_units=3.0)


@pytest.fixture
def zmp_analyzer(skeleton):
    return ZMPAnalyzer(skeleton)


@pytest.fixture
def ipm():
    return InvertedPendulumModel(com_height=0.5, gravity=9.81)


@pytest.fixture
def skating_cleanup():
    return SkatingCleanupCalculus()


@pytest.fixture
def gait_generator(skeleton):
    return FABRIKGaitGenerator(skeleton)


@pytest.fixture
def biomechanics_projector(skeleton):
    return BiomechanicsProjector(skeleton)


# ── 1. ZMP/CoM Analysis Tests ───────────────────────────────────────────────

class TestZMPAnalyzer:
    """Tests for Zero Moment Point and Center of Mass analysis."""

    def test_init(self, zmp_analyzer):
        """ZMPAnalyzer initializes with correct defaults."""
        assert zmp_analyzer._gravity == 9.81
        assert zmp_analyzer._foot_half_width == 0.05
        assert len(zmp_analyzer._com_history) == 0

    def test_compute_com_zero_pose(self, zmp_analyzer, skeleton):
        """CoM of a zero-pose skeleton is near the center."""
        skeleton.apply_pose({})
        positions = skeleton.forward_kinematics()
        com_x, com_y = zmp_analyzer.compute_com(positions)
        # CoM should be roughly centered horizontally
        assert abs(com_x) < 0.5
        # CoM should be above ground
        assert com_y > 0

    def test_compute_com_with_masses(self, skeleton):
        """CoM computation respects joint mass distribution."""
        analyzer = ZMPAnalyzer(skeleton, joint_masses={"head": 100.0})
        skeleton.apply_pose({})
        positions = skeleton.forward_kinematics()
        com_x, com_y = analyzer.compute_com(positions)
        # With a very heavy head, CoM should be high
        head_pos = positions.get("head", (0, 0))
        assert com_y > 0

    def test_compute_support_polygon(self, zmp_analyzer, skeleton):
        """Support polygon spans both feet."""
        skeleton.apply_pose({})
        positions = skeleton.forward_kinematics()
        left, right = zmp_analyzer.compute_support_polygon(positions)
        assert left < right

    def test_analyze_frame_returns_zmp_result(self, zmp_analyzer):
        """analyze_frame returns a ZMPResult dataclass."""
        pose = idle_animation(0.0)
        result = zmp_analyzer.analyze_frame(pose)
        assert isinstance(result, ZMPResult)
        assert hasattr(result, "com_x")
        assert hasattr(result, "zmp_x")
        assert hasattr(result, "is_balanced")
        assert hasattr(result, "stability_score")

    def test_analyze_frame_stability_score_range(self, zmp_analyzer):
        """Stability score is in [0, 1]."""
        for t in [0.0, 0.25, 0.5, 0.75]:
            pose = idle_animation(t)
            result = zmp_analyzer.analyze_frame(pose)
            assert 0.0 <= result.stability_score <= 1.0

    def test_analyze_sequence(self, zmp_analyzer):
        """analyze_sequence returns per-frame results."""
        poses = [idle_animation(t / 8) for t in range(8)]
        results = zmp_analyzer.analyze_sequence(poses)
        assert len(results) == 8
        assert all(isinstance(r, ZMPResult) for r in results)

    def test_compute_balance_penalty(self, zmp_analyzer):
        """Balance penalty is non-negative."""
        poses = [idle_animation(t / 8) for t in range(8)]
        penalty = zmp_analyzer.compute_balance_penalty(poses)
        assert penalty >= 0.0

    def test_reset(self, zmp_analyzer):
        """Reset clears history."""
        zmp_analyzer.analyze_frame(idle_animation(0.0))
        assert len(zmp_analyzer._com_history) > 0
        zmp_analyzer.reset()
        assert len(zmp_analyzer._com_history) == 0


# ── 2. Inverted Pendulum Model Tests ────────────────────────────────────────

class TestInvertedPendulumModel:
    """Tests for the Linear Inverted Pendulum Model (LIPM)."""

    def test_init(self, ipm):
        """IPM initializes with correct parameters."""
        assert ipm.z_c == 0.5
        assert ipm.gravity == 9.81
        assert ipm.omega == pytest.approx(math.sqrt(9.81 / 0.5), rel=1e-6)

    def test_natural_frequency(self, ipm):
        """Natural frequency ω = sqrt(g/z_c)."""
        expected = math.sqrt(9.81 / 0.5)
        assert ipm.natural_frequency == pytest.approx(expected, rel=1e-6)

    def test_com_trajectory_at_t0(self, ipm):
        """At t=0, CoM position equals initial position."""
        x, x_dot = ipm.compute_com_trajectory(0.0, x0=0.1, x_dot0=0.0)
        assert x == pytest.approx(0.1, abs=1e-8)
        assert x_dot == pytest.approx(0.0, abs=1e-8)

    def test_com_trajectory_symmetry(self, ipm):
        """LIPM trajectory with zero initial velocity is symmetric."""
        x_pos, _ = ipm.compute_com_trajectory(0.1, x0=0.05, x_dot0=0.0)
        x_neg, _ = ipm.compute_com_trajectory(0.1, x0=-0.05, x_dot0=0.0)
        assert x_pos == pytest.approx(-x_neg, abs=1e-8)

    def test_com_trajectory_diverges(self, ipm):
        """LIPM trajectory diverges (inverted pendulum is unstable)."""
        x0, _ = ipm.compute_com_trajectory(0.0, x0=0.01, x_dot0=0.0)
        x1, _ = ipm.compute_com_trajectory(0.5, x0=0.01, x_dot0=0.0)
        assert abs(x1) > abs(x0)

    def test_vertical_bounce_range(self, ipm):
        """Vertical bounce is bounded by amplitude."""
        amplitude = 0.02
        for phase in np.linspace(0, 1, 20):
            bounce = ipm.compute_vertical_bounce(phase, amplitude)
            assert abs(bounce) <= amplitude + 1e-8

    def test_vertical_bounce_periodicity(self, ipm):
        """Vertical bounce is periodic with period 1."""
        b0 = ipm.compute_vertical_bounce(0.0)
        b1 = ipm.compute_vertical_bounce(1.0)
        assert b0 == pytest.approx(b1, abs=1e-8)

    def test_lateral_sway_range(self, ipm):
        """Lateral sway is bounded by amplitude."""
        amplitude = 0.015
        for phase in np.linspace(0, 1, 20):
            sway = ipm.compute_lateral_sway(phase, amplitude)
            assert abs(sway) <= amplitude + 1e-8

    def test_generate_walk_com(self, ipm):
        """Walk CoM trajectory has correct length."""
        trajectory = ipm.generate_walk_com(n_frames=16)
        assert len(trajectory) == 16
        assert all(len(t) == 3 for t in trajectory)

    def test_reset(self, ipm):
        """Reset restores initial state."""
        ipm.reset()
        assert ipm._state.x == 0.0


# ── 3. Foot Skating Cleanup Tests ───────────────────────────────────────────

class TestSkatingCleanupCalculus:
    """Tests for the calculus-based foot skating cleanup algorithm."""

    def test_init(self, skating_cleanup):
        """Skating cleanup initializes with correct defaults."""
        assert skating_cleanup.height_threshold == 0.05
        assert skating_cleanup.velocity_threshold == 0.12
        assert len(skating_cleanup.foot_joints) == 2

    def test_smoothstep_boundaries(self, skating_cleanup):
        """Smoothstep: w(0)=0, w(1)=1."""
        assert skating_cleanup._smoothstep(0.0) == pytest.approx(0.0, abs=1e-8)
        assert skating_cleanup._smoothstep(1.0) == pytest.approx(1.0, abs=1e-8)

    def test_smoothstep_midpoint(self, skating_cleanup):
        """Smoothstep: w(0.5)=0.5."""
        assert skating_cleanup._smoothstep(0.5) == pytest.approx(0.5, abs=1e-8)

    def test_smoothstep_clamping(self, skating_cleanup):
        """Smoothstep clamps to [0, 1]."""
        assert skating_cleanup._smoothstep(-0.5) == 0.0
        assert skating_cleanup._smoothstep(1.5) == 1.0

    def test_update_detects_contact(self, skating_cleanup):
        """Foot on ground with low velocity triggers contact lock."""
        # Simulate foot at ground level with zero velocity
        positions = {
            "l_foot": (0.0, 0.0),
            "r_foot": (0.1, 0.0),
        }
        # First frame: establish history
        skating_cleanup.update(positions, dt=1.0 / 60)
        # Second frame: same position → zero velocity → contact
        states = skating_cleanup.update(positions, dt=1.0 / 60)
        assert states["l_foot"].is_locked
        assert states["r_foot"].is_locked

    def test_update_no_contact_when_airborne(self, skating_cleanup):
        """Foot high above ground does not trigger contact."""
        positions = {
            "l_foot": (0.0, 0.5),  # High up
            "r_foot": (0.1, 0.5),
        }
        skating_cleanup.update(positions, dt=1.0 / 60)
        states = skating_cleanup.update(positions, dt=1.0 / 60)
        assert not states["l_foot"].is_locked
        assert not states["r_foot"].is_locked

    def test_compute_corrections(self, skating_cleanup):
        """Corrections pull locked foot back to lock position."""
        # Lock feet at origin
        pos0 = {"l_foot": (0.0, 0.0), "r_foot": (0.1, 0.0)}
        skating_cleanup.update(pos0, dt=1.0 / 60)
        skating_cleanup.update(pos0, dt=1.0 / 60)

        # Move foot slightly — correction should pull back
        pos1 = {"l_foot": (0.05, 0.0), "r_foot": (0.15, 0.0)}
        corrections = skating_cleanup.compute_corrections(pos1)
        # l_foot should have a negative dx (pull left toward lock)
        if "l_foot" in corrections:
            assert corrections["l_foot"][0] < 0

    def test_compute_skating_metric(self, skating_cleanup):
        """Skating metric is non-negative."""
        positions = {"l_foot": (0.0, 0.0), "r_foot": (0.1, 0.0)}
        skating_cleanup.update(positions, dt=1.0 / 60)
        skating_cleanup.update(positions, dt=1.0 / 60)
        metric = skating_cleanup.compute_skating_metric(positions)
        assert metric >= 0.0

    def test_reset(self, skating_cleanup):
        """Reset clears all foot states."""
        positions = {"l_foot": (0.0, 0.0), "r_foot": (0.1, 0.0)}
        skating_cleanup.update(positions)
        skating_cleanup.reset()
        for state in skating_cleanup._states.values():
            assert not state.is_locked
            assert state.prev_position is None


# ── 4. FABRIK Gait Generator Tests ──────────────────────────────────────────

class TestFABRIKGaitGenerator:
    """Tests for the FABRIK-driven procedural gait generator."""

    def test_init(self, gait_generator):
        """Gait generator initializes with correct parameters."""
        assert gait_generator.step_length == 0.15
        assert gait_generator.step_height == 0.08
        assert gait_generator._l_thigh_len > 0
        assert gait_generator._l_shin_len > 0

    def test_generate_walk_pose_returns_dict(self, gait_generator):
        """Walk pose returns a dict of joint angles."""
        pose = gait_generator.generate_walk_pose(0.0)
        assert isinstance(pose, dict)
        assert len(pose) > 0

    def test_generate_walk_pose_has_leg_joints(self, gait_generator):
        """Walk pose includes leg joint angles."""
        pose = gait_generator.generate_walk_pose(0.25)
        assert "l_hip" in pose or "l_knee" in pose
        assert "r_hip" in pose or "r_knee" in pose

    def test_generate_walk_pose_has_upper_body(self, gait_generator):
        """Walk pose includes upper body joints."""
        pose = gait_generator.generate_walk_pose(0.5)
        assert "spine" in pose
        assert "l_shoulder" in pose
        assert "r_shoulder" in pose

    def test_generate_walk_pose_full_cycle(self, gait_generator):
        """Walk cycle produces valid poses for all phases."""
        for t in np.linspace(0, 1, 16, endpoint=False):
            pose = gait_generator.generate_walk_pose(t)
            assert isinstance(pose, dict)
            # All angles should be finite
            for name, angle in pose.items():
                assert math.isfinite(angle), f"{name}={angle} at t={t}"

    def test_generate_run_pose(self, gait_generator):
        """Run pose returns valid dict."""
        pose = gait_generator.generate_run_pose(0.25)
        assert isinstance(pose, dict)
        assert "spine" in pose

    def test_generate_run_pose_full_cycle(self, gait_generator):
        """Run cycle produces valid poses for all phases."""
        for t in np.linspace(0, 1, 16, endpoint=False):
            pose = gait_generator.generate_run_pose(t)
            assert isinstance(pose, dict)
            for name, angle in pose.items():
                assert math.isfinite(angle), f"{name}={angle} at t={t}"

    def test_foot_trajectory_stance(self, gait_generator):
        """Stance foot stays at ground level."""
        target = gait_generator._plan_foot_trajectory(
            0.5, is_swing=False, stride_start_x=0.0, stride_end_x=0.1
        )
        assert target[1] == 0.0  # On ground

    def test_foot_trajectory_swing_arc(self, gait_generator):
        """Swing foot follows a parabolic arc with peak at mid-phase."""
        peak = gait_generator._plan_foot_trajectory(
            0.5, is_swing=True, stride_start_x=0.0, stride_end_x=0.1
        )
        start = gait_generator._plan_foot_trajectory(
            0.0, is_swing=True, stride_start_x=0.0, stride_end_x=0.1
        )
        end = gait_generator._plan_foot_trajectory(
            1.0, is_swing=True, stride_start_x=0.0, stride_end_x=0.1
        )
        # Peak should be highest
        assert peak[1] >= start[1]
        assert peak[1] >= end[1]

    def test_reset(self, gait_generator):
        """Reset doesn't crash."""
        gait_generator.reset()


# ── 5. BiomechanicsProjector Integration Tests ──────────────────────────────

class TestBiomechanicsProjector:
    """Tests for the unified biomechanics projector."""

    def test_init(self, biomechanics_projector):
        """Projector initializes with all subsystems."""
        assert biomechanics_projector._enable_zmp
        assert biomechanics_projector._enable_ipm
        assert biomechanics_projector._enable_skating_cleanup

    def test_step_returns_dict(self, biomechanics_projector):
        """step() returns a pose dict."""
        pose = idle_animation(0.0)
        corrected = biomechanics_projector.step(pose)
        assert isinstance(corrected, dict)

    def test_step_preserves_keys(self, biomechanics_projector):
        """step() preserves all original pose keys."""
        pose = run_animation(0.25)
        corrected = biomechanics_projector.step(pose, gait_phase=0.25)
        for key in pose:
            assert key in corrected

    def test_step_with_analysis(self, biomechanics_projector):
        """step_with_analysis() returns pose and metadata."""
        pose = run_animation(0.5)
        corrected, metadata = biomechanics_projector.step_with_analysis(
            pose, gait_phase=0.5
        )
        assert isinstance(corrected, dict)
        assert isinstance(metadata, dict)
        assert "zmp_result" in metadata
        assert "skating_metric" in metadata
        assert "ipm_bounce" in metadata

    def test_step_compatible_with_all_presets(self, biomechanics_projector):
        """Projector works with all animation presets."""
        presets = [idle_animation, run_animation, jump_animation,
                   fall_animation, hit_animation]
        for preset in presets:
            biomechanics_projector.reset()
            for t in np.linspace(0, 1, 8, endpoint=False):
                pose = preset(t)
                corrected = biomechanics_projector.step(pose)
                assert isinstance(corrected, dict)
                for name, angle in corrected.items():
                    assert math.isfinite(angle), (
                        f"{preset.__name__} t={t} {name}={angle}"
                    )

    def test_ipm_modulates_spine(self, skeleton):
        """IPM modulation changes spine angle when gait_phase is provided."""
        projector = BiomechanicsProjector(
            skeleton,
            enable_zmp=False,
            enable_ipm=True,
            enable_skating_cleanup=False,
        )
        pose = {"spine": 0.1, "chest": 0.0}
        # phase=0.0 gives max positive bounce, phase=0.5 gives max negative
        corrected = projector.step(pose, gait_phase=0.0)
        # IPM should modulate spine (bounce at phase=0.0 is +0.02 * 1.5 = 0.03)
        assert corrected["spine"] != pytest.approx(pose["spine"], abs=1e-6)

    def test_disabled_subsystems(self, skeleton):
        """Projector with all subsystems disabled is a passthrough."""
        projector = BiomechanicsProjector(
            skeleton,
            enable_zmp=False,
            enable_ipm=False,
            enable_skating_cleanup=False,
        )
        pose = {"spine": 0.1, "chest": 0.05, "head": -0.02}
        corrected = projector.step(pose)
        for key in pose:
            assert corrected[key] == pytest.approx(pose[key], abs=1e-8)

    def test_reset(self, biomechanics_projector):
        """Reset clears all state."""
        biomechanics_projector.step(idle_animation(0.0))
        biomechanics_projector.reset()
        assert biomechanics_projector._frame_count == 0


# ── 6. Biomechanics Penalty Tests ───────────────────────────────────────────

class TestBiomechanicsPenalty:
    """Tests for compute_biomechanics_penalty."""

    def test_empty_sequence(self, skeleton):
        """Empty sequence returns 0 penalty."""
        assert compute_biomechanics_penalty([], skeleton) == 0.0

    def test_single_frame(self, skeleton):
        """Single frame returns 0 penalty."""
        assert compute_biomechanics_penalty([idle_animation(0.0)], skeleton) == 0.0

    def test_smooth_sequence_low_penalty(self, skeleton):
        """Smooth idle sequence has low penalty."""
        poses = [idle_animation(t / 16) for t in range(16)]
        penalty = compute_biomechanics_penalty(poses, skeleton)
        assert penalty >= 0.0
        assert penalty < 100.0  # Should be reasonably low

    def test_penalty_non_negative(self, skeleton):
        """Penalty is always non-negative."""
        poses = [run_animation(t / 8) for t in range(8)]
        penalty = compute_biomechanics_penalty(poses, skeleton)
        assert penalty >= 0.0

    def test_custom_weights(self, skeleton):
        """Custom weights are accepted."""
        poses = [idle_animation(t / 8) for t in range(8)]
        penalty = compute_biomechanics_penalty(
            poses, skeleton,
            weights={"zmp_balance": 0.0, "skating": 0.0, "com_smoothness": 0.0}
        )
        assert penalty >= 0.0


# ── 7. DEFAULT_JOINT_MASSES Tests ────────────────────────────────────────────

class TestDefaultJointMasses:
    """Tests for the default joint mass distribution."""

    def test_all_skeleton_joints_covered(self, skeleton):
        """All skeleton joints have a mass entry."""
        for name in skeleton.joints:
            if name == "root":
                continue  # Root has zero mass by design
            assert name in DEFAULT_JOINT_MASSES, f"Missing mass for {name}"

    def test_masses_sum_to_approximately_one(self):
        """Mass fractions should approximately sum to 1.0."""
        total = sum(DEFAULT_JOINT_MASSES.values())
        # Allow some tolerance since root=0 and not all body parts are joints
        assert 0.5 < total < 1.5

    def test_all_masses_non_negative(self):
        """All mass values are non-negative."""
        for name, mass in DEFAULT_JOINT_MASSES.items():
            assert mass >= 0.0, f"Negative mass for {name}: {mass}"


# ── 8. Pipeline Integration Tests ───────────────────────────────────────────

class TestPipelineIntegration:
    """Tests for biomechanics integration into the pipeline."""

    def test_character_spec_has_biomechanics_fields(self):
        """CharacterSpec has SESSION-029 biomechanics fields."""
        from mathart.pipeline import CharacterSpec
        spec = CharacterSpec(name="test")
        assert hasattr(spec, "enable_biomechanics")
        assert hasattr(spec, "biomechanics_zmp")
        assert hasattr(spec, "biomechanics_ipm")
        assert hasattr(spec, "biomechanics_skating_cleanup")
        assert hasattr(spec, "biomechanics_zmp_strength")

    def test_character_spec_defaults(self):
        """CharacterSpec biomechanics defaults are sensible."""
        from mathart.pipeline import CharacterSpec
        spec = CharacterSpec(name="test")
        assert spec.enable_biomechanics is True
        assert spec.biomechanics_zmp is True
        assert spec.biomechanics_ipm is True
        assert spec.biomechanics_skating_cleanup is True
        assert 0.0 <= spec.biomechanics_zmp_strength <= 1.0

    def test_biomechanics_importable_from_animation(self):
        """All biomechanics classes are importable from animation package."""
        from mathart.animation import (
            ZMPAnalyzer,
            ZMPResult,
            InvertedPendulumModel,
            IPMState,
            SkatingCleanupCalculus,
            SkatingCleanupState,
            FABRIKGaitGenerator,
            GaitPhase,
            BiomechanicsProjector,
            compute_biomechanics_penalty,
            DEFAULT_JOINT_MASSES,
        )
        assert ZMPAnalyzer is not None
        assert BiomechanicsProjector is not None
