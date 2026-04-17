"""SESSION-049: Gap B3 — Phase-Preserving Gait Transition Blending Tests.

Validates the Marker-based DTW system for walk/run/sneak transitions:
- SyncMarker definition and phase normalization
- GaitSyncProfile properties
- PhaseWarper marker alignment
- StrideWheel distance-to-phase mapping
- GaitBlendLayer initialization
- GaitBlender full pipeline
- Convenience blend functions
- Adaptive bounce physics
- No foot sliding invariant
- Leader-follower transitions
"""

import math
import pytest

from mathart.animation.gait_blend import (
    SyncMarker,
    GaitSyncProfile,
    GaitBlendLayer,
    GaitBlender,
    StrideWheel,
    BIPEDAL_SYNC_MARKERS,
    WALK_SYNC_PROFILE,
    RUN_SYNC_PROFILE,
    SNEAK_SYNC_PROFILE,
    phase_warp,
    adaptive_bounce,
    blend_walk_run,
    blend_gaits_at_phase,
    _marker_segment,
)
from mathart.animation.phase_driven import GaitMode


# ── SyncMarker Tests ─────────────────────────────────────────────────────────


class TestSyncMarker:
    """Validate SyncMarker data structure."""

    def test_basic_creation(self):
        m = SyncMarker(name="left_foot_down", phase=0.0)
        assert m.name == "left_foot_down"
        assert m.phase == 0.0

    def test_phase_normalization(self):
        m = SyncMarker(name="test", phase=1.5)
        assert 0.0 <= m.phase < 1.0
        assert abs(m.phase - 0.5) < 1e-9

    def test_negative_phase_normalization(self):
        m = SyncMarker(name="test", phase=-0.3)
        assert 0.0 <= m.phase < 1.0

    def test_frozen(self):
        m = SyncMarker(name="test", phase=0.25)
        with pytest.raises(AttributeError):
            m.name = "changed"  # type: ignore[misc]

    def test_bipedal_markers(self):
        assert len(BIPEDAL_SYNC_MARKERS) == 2
        assert BIPEDAL_SYNC_MARKERS[0].name == "left_foot_down"
        assert BIPEDAL_SYNC_MARKERS[0].phase == 0.0
        assert BIPEDAL_SYNC_MARKERS[1].name == "right_foot_down"
        assert BIPEDAL_SYNC_MARKERS[1].phase == 0.5


# ── GaitSyncProfile Tests ───────────────────────────────────────────────────


class TestGaitSyncProfile:
    """Validate GaitSyncProfile properties."""

    def test_walk_profile(self):
        p = WALK_SYNC_PROFILE
        assert p.gait == GaitMode.WALK
        assert p.stride_length == 0.8
        assert p.steps_per_second == 2.0
        assert p.cycle_duration == 1.0  # 2/2 = 1s
        assert abs(p.cycle_velocity - 0.8) < 1e-9

    def test_run_profile(self):
        p = RUN_SYNC_PROFILE
        assert p.gait == GaitMode.RUN
        assert p.stride_length == 2.0
        assert p.steps_per_second == 3.0
        assert abs(p.cycle_duration - 2.0 / 3.0) < 1e-6
        assert abs(p.cycle_velocity - 3.0) < 1e-6

    def test_sneak_profile(self):
        p = SNEAK_SYNC_PROFILE
        assert p.gait == GaitMode.SNEAK
        assert p.stride_length == 0.5
        assert p.steps_per_second == 1.5


# ── Phase Warper Tests ───────────────────────────────────────────────────────


class TestPhaseWarper:
    """Validate phase warping for marker alignment."""

    def test_identity_warp(self):
        """Same markers → phase passes through unchanged."""
        for p in [0.0, 0.1, 0.25, 0.49, 0.5, 0.75, 0.99]:
            warped = phase_warp(p, BIPEDAL_SYNC_MARKERS, BIPEDAL_SYNC_MARKERS)
            assert abs(warped - p) < 1e-6, f"Phase {p} warped to {warped}"

    def test_marker_alignment_at_contacts(self):
        """At marker positions, warped phase should match exactly."""
        for marker in BIPEDAL_SYNC_MARKERS:
            warped = phase_warp(
                marker.phase,
                BIPEDAL_SYNC_MARKERS,
                BIPEDAL_SYNC_MARKERS,
            )
            assert abs(warped - marker.phase) < 1e-6

    def test_midpoint_alignment(self):
        """Midpoint between markers should map to midpoint."""
        warped = phase_warp(0.25, BIPEDAL_SYNC_MARKERS, BIPEDAL_SYNC_MARKERS)
        assert abs(warped - 0.25) < 1e-6

    def test_empty_markers_passthrough(self):
        """Empty markers → passthrough."""
        assert abs(phase_warp(0.3, (), BIPEDAL_SYNC_MARKERS) - 0.3) < 1e-6
        assert abs(phase_warp(0.3, BIPEDAL_SYNC_MARKERS, ()) - 0.3) < 1e-6

    def test_warp_preserves_range(self):
        """Warped phase must be in [0, 1)."""
        for p in [0.0, 0.1, 0.3, 0.5, 0.7, 0.9, 0.99]:
            warped = phase_warp(p, BIPEDAL_SYNC_MARKERS, BIPEDAL_SYNC_MARKERS)
            assert 0.0 <= warped < 1.0, f"Warped phase {warped} out of range"

    def test_custom_markers_warp(self):
        """Different marker positions should correctly remap phases."""
        leader_markers = (
            SyncMarker("a", 0.0),
            SyncMarker("b", 0.5),
        )
        follower_markers = (
            SyncMarker("a", 0.0),
            SyncMarker("b", 0.6),  # Follower's "b" is at 0.6 instead of 0.5
        )
        # At leader phase 0.0, follower should be at 0.0
        assert abs(phase_warp(0.0, leader_markers, follower_markers)) < 1e-6
        # At leader phase 0.5, follower should be at 0.6
        warped = phase_warp(0.5, leader_markers, follower_markers)
        assert abs(warped - 0.6) < 1e-6


# ── Marker Segment Tests ────────────────────────────────────────────────────


class TestMarkerSegment:
    """Validate _marker_segment helper."""

    def test_first_segment(self):
        seg, t = _marker_segment(BIPEDAL_SYNC_MARKERS, 0.0)
        assert seg == 0
        assert abs(t) < 1e-6

    def test_midpoint_first_segment(self):
        seg, t = _marker_segment(BIPEDAL_SYNC_MARKERS, 0.25)
        assert seg == 0
        assert abs(t - 0.5) < 1e-6

    def test_second_segment_start(self):
        seg, t = _marker_segment(BIPEDAL_SYNC_MARKERS, 0.5)
        assert seg == 1
        assert abs(t) < 1e-6

    def test_second_segment_midpoint(self):
        seg, t = _marker_segment(BIPEDAL_SYNC_MARKERS, 0.75)
        assert seg == 1
        assert abs(t - 0.5) < 1e-6


# ── Stride Wheel Tests ──────────────────────────────────────────────────────


class TestStrideWheel:
    """Validate David Rosen's Stride Wheel implementation."""

    def test_initial_phase(self):
        wheel = StrideWheel(circumference=1.0)
        assert wheel.phase == 0.0

    def test_advance_half_cycle(self):
        wheel = StrideWheel(circumference=2.0)
        phase = wheel.advance(1.0)
        assert abs(phase - 0.5) < 1e-9

    def test_advance_full_cycle(self):
        wheel = StrideWheel(circumference=1.0)
        phase = wheel.advance(1.0)
        assert abs(phase) < 1e-9  # Wraps to 0.0

    def test_phase_wraps(self):
        wheel = StrideWheel(circumference=1.0)
        wheel.advance(1.5)
        assert 0.0 <= wheel.phase < 1.0
        assert abs(wheel.phase - 0.5) < 1e-9

    def test_negative_distance_uses_abs(self):
        wheel = StrideWheel(circumference=1.0)
        phase = wheel.advance(-0.25)
        assert abs(phase - 0.25) < 1e-9

    def test_set_circumference_preserves_phase(self):
        """Phase must be preserved when circumference changes (Rosen fix)."""
        wheel = StrideWheel(circumference=1.0)
        wheel.advance(0.5)  # phase = 0.5
        wheel.set_circumference(2.0)
        # Phase should be PRESERVED at 0.5 (distance rescaled)
        assert abs(wheel.phase - 0.5) < 1e-9

    def test_reset(self):
        wheel = StrideWheel(circumference=1.0)
        wheel.advance(0.5)
        wheel.reset()
        assert wheel.phase == 0.0


# ── GaitBlendLayer Tests ────────────────────────────────────────────────────


class TestGaitBlendLayer:
    """Validate GaitBlendLayer initialization."""

    def test_walk_layer_default(self):
        layer = GaitBlendLayer(profile=WALK_SYNC_PROFILE)
        assert layer.weight == 0.0
        assert layer.phase == 0.0
        assert layer.interpolator is not None

    def test_run_layer_default(self):
        layer = GaitBlendLayer(profile=RUN_SYNC_PROFILE)
        assert layer.interpolator is not None
        assert "arm_pump" in layer.channels


# ── GaitBlender Tests ────────────────────────────────────────────────────────


class TestGaitBlender:
    """Validate the main GaitBlender orchestrator."""

    def test_initial_state(self):
        blender = GaitBlender()
        assert blender.leader == GaitMode.WALK
        assert len(blender.active_gaits) == 1
        assert GaitMode.WALK in blender.active_gaits

    def test_update_produces_pose(self):
        blender = GaitBlender()
        pose = blender.update(dt=1.0 / 60.0, velocity=1.0)
        assert isinstance(pose, dict)
        assert "_phase" in pose
        assert "_root_y" in pose
        # Should have joint angles
        joint_keys = [k for k in pose if not k.startswith("_")]
        assert len(joint_keys) > 0

    def test_walk_to_run_transition(self):
        blender = GaitBlender()
        # Start in walk
        blender.update(dt=0.016, velocity=1.0)
        assert blender.leader == GaitMode.WALK

        # Transition to run
        for _ in range(120):  # ~2 seconds at 60fps
            blender.update(dt=0.016, velocity=3.0, target_gait=GaitMode.RUN)

        # After enough time, run should be leader
        assert blender.leader == GaitMode.RUN

    def test_blend_weights_normalize(self):
        blender = GaitBlender()
        blender.set_weights({
            GaitMode.WALK: 0.5,
            GaitMode.RUN: 0.5,
        })
        total = sum(l.weight for l in blender._layers.values())
        assert abs(total - 1.0) < 1e-6

    def test_phase_continuity(self):
        """Phase should advance smoothly without jumps."""
        blender = GaitBlender()
        prev_phase = 0.0
        for i in range(100):
            pose = blender.update(dt=0.016, velocity=1.0)
            phase = pose["_phase"]
            # Phase should not jump more than a reasonable amount per frame
            # At velocity=1.0, stride=0.8: delta = 0.016/0.8 = 0.02 per frame
            if i > 0:
                delta = (phase - prev_phase) % 1.0
                assert delta < 0.1, f"Phase jump too large: {delta}"
            prev_phase = phase

    def test_no_foot_sliding_invariant(self):
        """Stride wheel ensures distance matches phase progression.

        The fundamental invariant: if the character moves D distance,
        the phase should advance by D / stride_length.
        """
        blender = GaitBlender()
        velocity = 1.5
        dt = 0.016
        total_distance = 0.0
        total_phase_delta = 0.0
        prev_phase = 0.0

        for i in range(200):
            pose = blender.update(dt=dt, velocity=velocity)
            phase = pose["_phase"]
            total_distance += velocity * dt

            if i > 0:
                delta = (phase - prev_phase) % 1.0
                if delta > 0.5:
                    delta -= 1.0  # Handle wrap
                total_phase_delta += delta
            prev_phase = phase

        # Expected phase from distance: total_distance / stride_length
        stride = blender.blended_stride_length
        expected_cycles = total_distance / stride
        actual_cycles = total_phase_delta

        # Should match within 5% (accounting for floating point)
        assert abs(actual_cycles - expected_cycles) / max(expected_cycles, 1e-9) < 0.05

    def test_generate_frame(self):
        blender = GaitBlender()
        frame = blender.generate_frame(dt=0.016, velocity=1.0)
        assert "pose" in frame
        assert "root_y" in frame
        assert "phase" in frame
        assert "leader" in frame
        assert "metadata" in frame
        assert frame["metadata"]["gap"] == "B3"

    def test_blend_state_serializable(self):
        blender = GaitBlender()
        blender.update(dt=0.016, velocity=1.0)
        state = blender.get_blend_state()
        assert "leader" in state
        assert "weights" in state
        assert "phases" in state

    def test_sneak_transition(self):
        blender = GaitBlender()
        for _ in range(120):
            blender.update(dt=0.016, velocity=0.5, target_gait=GaitMode.SNEAK)
        assert blender.leader == GaitMode.SNEAK


# ── Adaptive Bounce Tests ───────────────────────────────────────────────────


class TestAdaptiveBounce:
    """Validate David Rosen's bounce gravity physics."""

    def test_bounce_at_zero_phase(self):
        """Bounce should be near zero at phase 0 (sin(0) = 0)."""
        b = adaptive_bounce(0.0, 1.0)
        assert abs(b) < 1e-6

    def test_bounce_decreases_with_speed(self):
        """Faster speed → smaller bounce (Rosen's insight)."""
        slow_bounce = abs(adaptive_bounce(0.125, 1.0))
        fast_bounce = abs(adaptive_bounce(0.125, 3.0))
        assert fast_bounce < slow_bounce

    def test_bounce_periodic(self):
        """Bounce should be periodic with 2x frequency."""
        b1 = adaptive_bounce(0.0, 1.0)
        b2 = adaptive_bounce(0.5, 1.0)
        # Both at sin(0) and sin(2π) ≈ 0
        assert abs(b1) < 1e-6
        assert abs(b2) < 1e-6

    def test_bounce_range(self):
        """Bounce should stay within reasonable bounds."""
        for p in [i / 100.0 for i in range(100)]:
            b = adaptive_bounce(p, 1.0, base_amplitude=0.03)
            assert abs(b) <= 0.1


# ── Convenience Function Tests ──────────────────────────────────────────────


class TestBlendWalkRun:
    """Validate the stateless walk-run blend function."""

    def test_pure_walk(self):
        pose, root_y = blend_walk_run(0.25, alpha=0.0)
        assert isinstance(pose, dict)
        assert len(pose) > 0

    def test_pure_run(self):
        pose, root_y = blend_walk_run(0.25, alpha=1.0)
        assert isinstance(pose, dict)
        assert len(pose) > 0

    def test_50_50_blend(self):
        walk_pose, _ = blend_walk_run(0.25, alpha=0.0)
        run_pose, _ = blend_walk_run(0.25, alpha=1.0)
        blend_pose, _ = blend_walk_run(0.25, alpha=0.5)

        # Blended values should be between walk and run
        for joint in blend_pose:
            w = walk_pose.get(joint, 0.0)
            r = run_pose.get(joint, 0.0)
            b = blend_pose[joint]
            low = min(w, r) - 0.01  # Small tolerance
            high = max(w, r) + 0.01
            assert low <= b <= high, (
                f"Joint {joint}: blend={b} not between walk={w} and run={r}"
            )

    def test_alpha_clamping(self):
        pose_neg, _ = blend_walk_run(0.25, alpha=-0.5)
        pose_zero, _ = blend_walk_run(0.25, alpha=0.0)
        # Alpha < 0 should clamp to 0
        for joint in pose_neg:
            assert abs(pose_neg[joint] - pose_zero.get(joint, 0.0)) < 1e-6

    def test_all_phases_produce_valid_poses(self):
        for p in [i / 20.0 for i in range(20)]:
            for alpha in [0.0, 0.25, 0.5, 0.75, 1.0]:
                pose, root_y = blend_walk_run(p, alpha)
                assert isinstance(pose, dict)
                assert all(math.isfinite(v) for v in pose.values())
                assert math.isfinite(root_y)


class TestBlendGaitsAtPhase:
    """Validate multi-gait blend function."""

    def test_single_gait(self):
        pose, root_y = blend_gaits_at_phase(
            0.25, {GaitMode.WALK: 1.0}
        )
        assert isinstance(pose, dict)
        assert len(pose) > 0

    def test_three_gait_blend(self):
        pose, root_y = blend_gaits_at_phase(
            0.25,
            {GaitMode.WALK: 0.5, GaitMode.RUN: 0.3, GaitMode.SNEAK: 0.2},
        )
        assert isinstance(pose, dict)
        assert math.isfinite(root_y)

    def test_empty_weights_fallback(self):
        pose, root_y = blend_gaits_at_phase(0.25, {})
        assert isinstance(pose, dict)

    def test_all_phases_finite(self):
        for p in [i / 10.0 for i in range(10)]:
            pose, root_y = blend_gaits_at_phase(
                p, {GaitMode.WALK: 0.6, GaitMode.RUN: 0.4}
            )
            assert all(math.isfinite(v) for v in pose.values())
            assert math.isfinite(root_y)


# ── Integration Tests ────────────────────────────────────────────────────────


class TestGaitBlendIntegration:
    """End-to-end integration tests for the full blending pipeline."""

    def test_walk_run_walk_roundtrip(self):
        """Walk → Run → Walk transition should produce valid poses throughout."""
        blender = GaitBlender()
        all_poses = []

        # Walk phase
        for _ in range(60):
            pose = blender.update(dt=0.016, velocity=1.0, target_gait=GaitMode.WALK)
            all_poses.append(pose)

        # Transition to run
        for _ in range(120):
            pose = blender.update(dt=0.016, velocity=3.0, target_gait=GaitMode.RUN)
            all_poses.append(pose)

        # Back to walk
        for _ in range(120):
            pose = blender.update(dt=0.016, velocity=1.0, target_gait=GaitMode.WALK)
            all_poses.append(pose)

        # All poses should be valid
        for i, pose in enumerate(all_poses):
            joint_keys = [k for k in pose if not k.startswith("_")]
            assert len(joint_keys) > 0, f"Frame {i} has no joint data"
            for k in joint_keys:
                assert math.isfinite(pose[k]), f"Frame {i}, joint {k} = {pose[k]}"

    def test_rapid_gait_switching(self):
        """Rapid gait switches should not cause NaN or crashes."""
        blender = GaitBlender()
        gaits = [GaitMode.WALK, GaitMode.RUN, GaitMode.SNEAK]

        for i in range(300):
            target = gaits[i % 3]
            pose = blender.update(dt=0.016, velocity=1.5, target_gait=target)
            for k, v in pose.items():
                assert math.isfinite(v), f"Frame {i}: {k} = {v}"

    def test_zero_velocity_stable(self):
        """Zero velocity should not cause division by zero."""
        blender = GaitBlender()
        pose = blender.update(dt=0.016, velocity=0.0)
        for k, v in pose.items():
            assert math.isfinite(v), f"{k} = {v}"

    def test_large_dt_stable(self):
        """Large time steps should not cause instability."""
        blender = GaitBlender()
        pose = blender.update(dt=1.0, velocity=5.0)
        for k, v in pose.items():
            assert math.isfinite(v), f"{k} = {v}"

    def test_import_from_animation_package(self):
        """Verify gait_blend is importable from mathart.animation."""
        from mathart.animation.gait_blend import GaitBlender as GB
        assert GB is GaitBlender
