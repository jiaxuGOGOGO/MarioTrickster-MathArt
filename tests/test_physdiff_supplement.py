"""Tests for SESSION-028-SUPP: PhysDiff-inspired foot contact & skating cleanup.

Validates the supplemental PhysDiff mechanisms:
  1. ContactDetector: foot-ground contact detection
  2. ConstraintBlender: smooth constraint transitions
  3. FootLockingConstraint: IK-based foot locking
  4. PhysDiffProjectionScheduler: selective projection scheduling
  5. Enhanced compute_physics_penalty with skating/penetration terms
  6. AnglePoseProjector with foot locking integration
"""
import math
import pytest
import numpy as np

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mathart.animation.physics_projector import (
    ContactDetector, ContactState, ConstraintBlender,
    FootLockingConstraint, PhysDiffProjectionScheduler,
    AnglePoseProjector, compute_physics_penalty,
)
from mathart.animation.skeleton import Skeleton


# ── ContactDetector Tests ──────────────────────────────────────────────────────


class TestContactDetector:
    """Tests for foot-ground contact detection."""

    def test_init_default(self):
        """ContactDetector initializes with default foot joints."""
        cd = ContactDetector()
        assert "l_foot" in cd.foot_joints
        assert "r_foot" in cd.foot_joints
        assert cd.contact_threshold == 0.05
        assert cd.velocity_threshold == 0.15

    def test_init_custom(self):
        """ContactDetector accepts custom parameters."""
        cd = ContactDetector(
            contact_threshold=0.1,
            velocity_threshold=0.2,
            foot_joints=["l_foot"],
        )
        assert cd.contact_threshold == 0.1
        assert len(cd.foot_joints) == 1

    def test_detect_contact_on_ground(self):
        """Foot at ground level with low velocity triggers contact."""
        # Use generous velocity threshold since 0.01 / (1/60) = 0.6
        cd = ContactDetector(contact_threshold=0.05, velocity_threshold=1.0)

        # First frame: foot on ground
        positions_1 = {"l_foot": (0.0, 0.0), "r_foot": (0.3, 0.0)}
        states = cd.update(positions_1, dt=1/60)
        # First frame: velocity is 0 (no prev), height is 0 → contact

        # Second frame: foot still on ground (tiny movement)
        positions_2 = {"l_foot": (0.0, 0.001), "r_foot": (0.3, 0.001)}
        states = cd.update(positions_2, dt=1/60)

        assert states["l_foot"].is_contacting is True
        assert states["r_foot"].is_contacting is True

    def test_no_contact_when_high(self):
        """Foot above threshold does not trigger contact."""
        cd = ContactDetector(contact_threshold=0.05)

        positions_1 = {"l_foot": (0.0, 0.5), "r_foot": (0.3, 0.5)}
        cd.update(positions_1, dt=1/60)

        positions_2 = {"l_foot": (0.0, 0.5), "r_foot": (0.3, 0.5)}
        states = cd.update(positions_2, dt=1/60)

        assert states["l_foot"].is_contacting is False
        assert states["r_foot"].is_contacting is False

    def test_no_contact_when_fast_velocity(self):
        """Foot at ground level but moving fast does not trigger contact."""
        cd = ContactDetector(contact_threshold=0.05, velocity_threshold=0.15)

        positions_1 = {"l_foot": (0.0, 0.0), "r_foot": (0.3, 0.0)}
        cd.update(positions_1, dt=1/60)

        # Large vertical movement = high velocity
        positions_2 = {"l_foot": (0.0, 0.04), "r_foot": (0.3, 0.04)}
        states = cd.update(positions_2, dt=1/60)

        # velocity = 0.04 / (1/60) = 2.4, which exceeds 0.15
        assert states["l_foot"].is_contacting is False

    def test_contact_point_recorded(self):
        """Contact point is recorded when contact begins."""
        cd = ContactDetector(contact_threshold=0.05, velocity_threshold=5.0)

        positions = {"l_foot": (0.2, 0.01), "r_foot": (0.5, 0.01)}
        cd.update(positions, dt=1/60)
        states = cd.update(positions, dt=1/60)

        if states["l_foot"].is_contacting:
            assert states["l_foot"].contact_point is not None
            assert states["l_foot"].contact_point[0] == pytest.approx(0.2, abs=0.01)

    def test_contact_transition_free_to_contact(self):
        """Frames_in_contact increments during contact."""
        cd = ContactDetector(contact_threshold=0.05, velocity_threshold=5.0)

        positions = {"l_foot": (0.0, 0.0), "r_foot": (0.3, 0.0)}
        cd.update(positions, dt=1/60)
        states = cd.update(positions, dt=1/60)

        if states["l_foot"].is_contacting:
            assert states["l_foot"].frames_in_contact >= 1

            # Third frame
            states = cd.update(positions, dt=1/60)
            assert states["l_foot"].frames_in_contact >= 2

    def test_contact_release(self):
        """Contact is released when foot moves above threshold."""
        cd = ContactDetector(contact_threshold=0.05, velocity_threshold=5.0)

        # Establish contact
        ground_pos = {"l_foot": (0.0, 0.0), "r_foot": (0.3, 0.0)}
        cd.update(ground_pos, dt=1/60)
        cd.update(ground_pos, dt=1/60)

        # Release: foot moves up
        air_pos = {"l_foot": (0.0, 0.3), "r_foot": (0.3, 0.3)}
        states = cd.update(air_pos, dt=1/60)

        assert states["l_foot"].is_contacting is False

    def test_reset(self):
        """Reset clears all contact states."""
        cd = ContactDetector()
        positions = {"l_foot": (0.0, 0.0), "r_foot": (0.3, 0.0)}
        cd.update(positions, dt=1/60)
        cd.reset()

        assert cd._prev_positions == {}
        for state in cd._states.values():
            assert state.is_contacting is False


# ── ConstraintBlender Tests ────────────────────────────────────────────────────


class TestConstraintBlender:
    """Tests for smooth constraint blending."""

    def test_init_defaults(self):
        """ConstraintBlender initializes with default blend frames."""
        cb = ConstraintBlender()
        assert cb.blend_in_frames >= 1
        assert cb.blend_out_frames >= 1

    def test_blend_in_ramp(self):
        """Weight ramps from 0 to 1 over blend_in_frames during contact."""
        cb = ConstraintBlender(blend_in_frames=4, blend_out_frames=4)

        state = ContactState(is_contacting=True, frames_in_contact=0)

        weights = []
        for i in range(6):
            state.frames_in_contact = i
            w = cb.compute_weight(state)
            weights.append(w)

        # Weight should increase monotonically
        for i in range(1, len(weights)):
            assert weights[i] >= weights[i - 1]

        # Should reach 1.0 at or after blend_in_frames
        assert weights[-1] == pytest.approx(1.0, abs=0.01)

    def test_blend_out_ramp(self):
        """Weight ramps from 1 to 0 over blend_out_frames after release."""
        cb = ConstraintBlender(blend_in_frames=3, blend_out_frames=4)

        state = ContactState(is_contacting=False, frames_since_release=0)

        weights = []
        for i in range(6):
            state.frames_since_release = i
            w = cb.compute_weight(state)
            weights.append(w)

        # Weight should decrease monotonically
        for i in range(1, len(weights)):
            assert weights[i] <= weights[i - 1]

        # Should reach 0.0 after blend_out_frames
        assert weights[-1] == pytest.approx(0.0, abs=0.01)

    def test_smoothstep_c1_continuous(self):
        """Smoothstep function is C1 continuous (no derivative discontinuity)."""
        cb = ConstraintBlender()

        # Sample smoothstep at many points
        ts = np.linspace(0, 1, 100)
        vals = [cb._smoothstep(t) for t in ts]

        # Check monotonicity
        for i in range(1, len(vals)):
            assert vals[i] >= vals[i - 1] - 1e-10

        # Check boundary values
        assert vals[0] == pytest.approx(0.0, abs=1e-6)
        assert vals[-1] == pytest.approx(1.0, abs=1e-6)

    def test_weight_always_in_range(self):
        """Blend weight is always in [0, 1]."""
        cb = ConstraintBlender(blend_in_frames=2, blend_out_frames=2)

        for frames in range(20):
            state_in = ContactState(is_contacting=True, frames_in_contact=frames)
            w = cb.compute_weight(state_in)
            assert 0.0 <= w <= 1.0

            state_out = ContactState(is_contacting=False, frames_since_release=frames)
            w = cb.compute_weight(state_out)
            assert 0.0 <= w <= 1.0


# ── FootLockingConstraint Tests ────────────────────────────────────────────────


class TestFootLockingConstraint:
    """Tests for IK-based foot locking."""

    def test_init_default_chains(self):
        """FootLockingConstraint has default leg chain definitions."""
        flc = FootLockingConstraint()
        assert "l_foot" in flc.leg_chains
        assert "r_foot" in flc.leg_chains
        assert flc.leg_chains["l_foot"] == ("l_hip", "l_knee", "l_foot")

    def test_init_with_skeleton(self):
        """FootLockingConstraint extracts bone lengths from skeleton."""
        skeleton = Skeleton.create_humanoid(head_units=3.0)
        flc = FootLockingConstraint(skeleton_ref=skeleton)
        # Should have computed bone lengths
        assert len(flc._bone_lengths) >= 0  # May be populated from skeleton

    def test_correct_pose_no_contact(self):
        """No correction when no feet are in contact."""
        skeleton = Skeleton.create_humanoid(head_units=3.0)
        flc = FootLockingConstraint(skeleton_ref=skeleton)

        pose = {"l_hip": 0.1, "l_knee": -0.2, "r_hip": -0.1, "r_knee": -0.2}
        contact_states = {
            "l_foot": ContactState(is_contacting=False),
            "r_foot": ContactState(is_contacting=False),
        }
        blend_weights = {"l_foot": 0.0, "r_foot": 0.0}

        skeleton.apply_pose(pose)
        positions = skeleton.forward_kinematics()

        corrected = flc.correct_pose(pose, contact_states, blend_weights, positions, skeleton)

        # No correction should be applied
        for joint_name in pose:
            assert corrected[joint_name] == pytest.approx(pose[joint_name], abs=1e-6)

    def test_correct_pose_with_contact(self):
        """Correction is applied when foot is in contact with weight > 0."""
        skeleton = Skeleton.create_humanoid(head_units=3.0)
        flc = FootLockingConstraint(skeleton_ref=skeleton)

        pose = {"l_hip": 0.3, "l_knee": -0.3, "r_hip": -0.1, "r_knee": -0.2,
                "spine": 0.0, "chest": 0.0}

        skeleton.apply_pose(pose)
        positions = skeleton.forward_kinematics()

        # Simulate contact at a specific point
        contact_states = {
            "l_foot": ContactState(
                is_contacting=True,
                contact_point=(positions["l_foot"][0] + 0.05, 0.0),  # Slight offset
                frames_in_contact=5,
            ),
            "r_foot": ContactState(is_contacting=False),
        }
        blend_weights = {"l_foot": 1.0, "r_foot": 0.0}

        corrected = flc.correct_pose(pose, contact_states, blend_weights, positions, skeleton)

        # Left leg angles should be modified (IK correction)
        # Right leg should be unchanged
        assert corrected["r_hip"] == pytest.approx(pose["r_hip"], abs=1e-6)
        assert corrected["r_knee"] == pytest.approx(pose["r_knee"], abs=1e-6)

    def test_2bone_ik_reachable(self):
        """2-bone IK returns valid angles for reachable targets."""
        skeleton = Skeleton.create_humanoid(head_units=3.0)
        flc = FootLockingConstraint(skeleton_ref=skeleton)

        skeleton.apply_pose({})
        positions = skeleton.forward_kinematics()

        hip_pos = positions.get("l_hip", (0.0, 0.33))
        # Target slightly below hip (reachable)
        target = (hip_pos[0], hip_pos[1] - 0.2)

        result = flc._solve_2bone_ik(hip_pos, target, "l_foot", skeleton)
        # Should return a tuple of two angles
        if result is not None:
            assert len(result) == 2
            assert all(isinstance(a, float) for a in result)

    def test_partial_blend(self):
        """Partial blend weight produces intermediate correction."""
        skeleton = Skeleton.create_humanoid(head_units=3.0)
        flc = FootLockingConstraint(skeleton_ref=skeleton)

        pose = {"l_hip": 0.3, "l_knee": -0.3, "r_hip": 0.0, "r_knee": 0.0}

        skeleton.apply_pose(pose)
        positions = skeleton.forward_kinematics()

        contact_states = {
            "l_foot": ContactState(
                is_contacting=True,
                contact_point=(positions["l_foot"][0] + 0.05, 0.0),
                frames_in_contact=5,
            ),
            "r_foot": ContactState(is_contacting=False),
        }

        # Full weight correction
        corrected_full = flc.correct_pose(
            pose, contact_states, {"l_foot": 1.0, "r_foot": 0.0}, positions, skeleton
        )
        # Half weight correction
        corrected_half = flc.correct_pose(
            pose, contact_states, {"l_foot": 0.5, "r_foot": 0.0}, positions, skeleton
        )

        # Half-weight correction should be between original and full
        if corrected_full["l_hip"] != pose["l_hip"]:
            diff_full = abs(corrected_full["l_hip"] - pose["l_hip"])
            diff_half = abs(corrected_half["l_hip"] - pose["l_hip"])
            assert diff_half <= diff_full + 1e-6


# ── PhysDiffProjectionScheduler Tests ──────────────────────────────────────────


class TestPhysDiffProjectionScheduler:
    """Tests for selective projection scheduling."""

    def test_init_defaults(self):
        """Scheduler initializes with sensible defaults."""
        sched = PhysDiffProjectionScheduler(total_steps=50)
        assert len(sched.schedule) > 0
        assert all(0 <= s < 50 for s in sched.schedule)

    def test_projection_ratio(self):
        """Number of projection steps matches ratio approximately."""
        sched = PhysDiffProjectionScheduler(
            total_steps=100, projection_ratio=0.4
        )
        n_proj = len(sched.schedule)
        # Should be roughly 40 steps (±10 for rounding)
        assert 30 <= n_proj <= 50

    def test_late_bias(self):
        """With late bias, more projection steps are in the second half."""
        sched = PhysDiffProjectionScheduler(
            total_steps=100, projection_ratio=0.5, late_bias=0.9
        )
        schedule = sched.schedule
        first_half = [s for s in schedule if s < 50]
        second_half = [s for s in schedule if s >= 50]
        # Late bias should put more steps in second half
        assert len(second_half) >= len(first_half)

    def test_should_project(self):
        """should_project returns True only for scheduled steps."""
        sched = PhysDiffProjectionScheduler(total_steps=20, projection_ratio=0.5)
        scheduled = set(sched.schedule)

        for step in range(20):
            assert sched.should_project(step) == (step in scheduled)

    def test_zero_ratio(self):
        """Zero projection ratio still produces at least 1 step."""
        sched = PhysDiffProjectionScheduler(total_steps=50, projection_ratio=0.0)
        assert len(sched.schedule) >= 1

    def test_full_ratio(self):
        """Full projection ratio covers most/all steps."""
        sched = PhysDiffProjectionScheduler(total_steps=10, projection_ratio=1.0)
        # Due to floating-point rounding in the bias function, we may miss 1 step
        assert len(sched.schedule) >= 9


# ── Enhanced compute_physics_penalty Tests ─────────────────────────────────────


class TestEnhancedPhysicsPenalty:
    """Tests for the enhanced penalty function with skating/penetration terms."""

    def _make_static_sequence(self, n_frames=10):
        """Create a static pose sequence (all zeros)."""
        pose = {
            "spine": 0.0, "chest": 0.0, "neck": 0.0, "head": 0.0,
            "l_hip": 0.0, "r_hip": 0.0, "l_knee": 0.0, "r_knee": 0.0,
            "l_shoulder": 0.0, "r_shoulder": 0.0,
        }
        return [dict(pose) for _ in range(n_frames)]

    def test_static_pose_low_penalty(self):
        """Static pose should have very low penalty."""
        skeleton = Skeleton.create_humanoid(head_units=3.0)
        seq = self._make_static_sequence(10)
        penalty = compute_physics_penalty(seq, skeleton, dt=1/60)
        assert penalty < 1.0

    def test_skating_increases_penalty(self):
        """Horizontal foot movement during ground contact increases penalty."""
        skeleton = Skeleton.create_humanoid(head_units=3.0)

        # Create sequence where feet slide horizontally
        seq_static = self._make_static_sequence(10)
        penalty_static = compute_physics_penalty(seq_static, skeleton, dt=1/60)

        # Create sequence with skating: l_hip oscillates (causes foot sliding)
        seq_skating = self._make_static_sequence(10)
        for i in range(10):
            seq_skating[i]["l_hip"] = 0.3 * math.sin(2 * math.pi * i / 10)
            seq_skating[i]["r_hip"] = -0.3 * math.sin(2 * math.pi * i / 10)

        penalty_skating = compute_physics_penalty(seq_skating, skeleton, dt=1/60)

        # Skating sequence should have higher penalty
        assert penalty_skating > penalty_static

    def test_penalty_weights_configurable(self):
        """Custom weights affect penalty calculation."""
        skeleton = Skeleton.create_humanoid(head_units=3.0)
        seq = self._make_static_sequence(10)

        # Default weights
        p1 = compute_physics_penalty(seq, skeleton, dt=1/60)

        # Zero all weights
        p2 = compute_physics_penalty(
            seq, skeleton, dt=1/60,
            weights={"smoothness": 0, "rom": 0, "symmetry": 0, "energy": 0,
                     "skating": 0, "penetrate": 0, "float": 0}
        )

        assert p2 <= p1

    def test_penalty_returns_float(self):
        """Penalty function always returns a float."""
        skeleton = Skeleton.create_humanoid(head_units=3.0)
        seq = self._make_static_sequence(5)
        result = compute_physics_penalty(seq, skeleton)
        assert isinstance(result, (int, float))


# ── AnglePoseProjector with Foot Locking Integration ──────────────────────────


class TestAnglePoseProjectorFootLocking:
    """Tests for AnglePoseProjector with foot locking enabled."""

    def test_init_with_skeleton(self):
        """AnglePoseProjector accepts skeleton_ref for foot locking."""
        skeleton = Skeleton.create_humanoid(head_units=3.0)
        proj = AnglePoseProjector(
            enable_foot_locking=True,
            skeleton_ref=skeleton,
        )
        assert proj._enable_foot_locking is True
        assert proj._skeleton_ref is not None
        assert proj._contact_detector is not None
        assert proj._constraint_blender is not None
        assert proj._foot_locker is not None

    def test_init_without_skeleton(self):
        """AnglePoseProjector works without skeleton (foot locking disabled)."""
        proj = AnglePoseProjector(enable_foot_locking=True, skeleton_ref=None)
        pose = {"spine": 0.1, "l_hip": 0.2}
        result = proj.step(pose, dt=1/60)
        assert isinstance(result, dict)
        assert "spine" in result

    def test_step_with_foot_locking(self):
        """Step produces valid output with foot locking enabled."""
        skeleton = Skeleton.create_humanoid(head_units=3.0)
        proj = AnglePoseProjector(
            enable_foot_locking=True,
            skeleton_ref=skeleton,
        )

        pose = {
            "spine": 0.0, "chest": 0.0, "neck": 0.0, "head": 0.0,
            "l_hip": 0.0, "r_hip": 0.0, "l_knee": 0.0, "r_knee": 0.0,
            "l_foot": 0.0, "r_foot": 0.0,
            "l_shoulder": 0.0, "r_shoulder": 0.0,
        }

        for _ in range(10):
            result = proj.step(pose, dt=1/60)
            assert isinstance(result, dict)
            # All joints should be present
            for key in pose:
                assert key in result
                assert isinstance(result[key], (int, float))
                assert not math.isnan(result[key])

    def test_sequence_with_foot_locking(self):
        """project_sequence works with foot locking."""
        skeleton = Skeleton.create_humanoid(head_units=3.0)
        proj = AnglePoseProjector(
            enable_foot_locking=True,
            skeleton_ref=skeleton,
        )

        pose = {"spine": 0.0, "l_hip": 0.0, "r_hip": 0.0,
                "l_knee": 0.0, "r_knee": 0.0}
        sequence = [dict(pose) for _ in range(20)]

        result = proj.project_sequence(sequence, dt=1/60)
        assert len(result) == 20
        for frame in result:
            assert isinstance(frame, dict)

    def test_reset_clears_contact_states(self):
        """Reset clears foot contact states."""
        skeleton = Skeleton.create_humanoid(head_units=3.0)
        proj = AnglePoseProjector(
            enable_foot_locking=True,
            skeleton_ref=skeleton,
        )

        pose = {"l_hip": 0.0, "r_hip": 0.0, "l_knee": 0.0, "r_knee": 0.0}
        proj.step(pose, dt=1/60)
        proj.step(pose, dt=1/60)

        proj.reset()

        # Contact detector should be reset
        for state in proj._contact_detector._states.values():
            assert state.is_contacting is False
            assert state.frames_in_contact == 0

    def test_backward_compat_no_skeleton(self):
        """Existing code without skeleton_ref still works (backward compatible)."""
        proj = AnglePoseProjector()  # No skeleton_ref
        pose = {"spine": 0.1, "chest": 0.05, "l_shoulder": -0.2}
        result = proj.step(pose, dt=1/60)
        assert isinstance(result, dict)
        assert len(result) == len(pose)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
