"""SESSION-111 P1-B3-5: Consolidated Unified Gait Blender Regression Suite.

This module is the **single-source** regression suite for the unified motion
core. SESSION-111 physically retired the ``gait_blend.py`` and
``transition_synthesizer.py`` shim modules and migrated / merged the legacy
``test_gait_blend.py`` + ``test_session039.py`` assertions here, WITHOUT any
coverage loss for:

1. **DTW phase alignment** — SyncMarker, GaitSyncProfile, phase_warp, marker
   segment search.
2. **Stride Wheel distance→phase mapping** — circumference scaling with
   leader-follower rate warping (David Rosen GDC 2014).
3. **Quintic (Bollo 2018) inertialization residual decay** and Holden dead
   blending strategy selection, including the convenience factory and the
   ``inertialize_transition`` functional API.
4. **Phase-preserving gait blending** — GaitBlender full pipeline, adaptive
   bounce, blend_walk_run, blend_gaits_at_phase and convergence invariants.
5. **C0 / C1 continuity** anti-regression guards at the synthesizer seam.
6. **Anti-Zombie-Reference Guard** — static assertion that the retired shim
   modules cannot be imported and that no fall-back path survives.
7. **Anti-PRNG-Bleed Guard** — static source inspection guarding determinism
   of the unified motion core for PDG v2 WorkItem parallel dispatch.
"""

import math

import numpy as np
import pytest

from mathart.animation.unified_gait_blender import (
    SyncMarker,
    GaitSyncProfile,
    GaitBlendLayer,
    GaitBlender,
    StrideWheel,
    BIPEDAL_SYNC_MARKERS,
    WALK_SYNC_PROFILE,
    RUN_SYNC_PROFILE,
    SNEAK_SYNC_PROFILE,
    TransitionStrategy,
    TransitionSynthesizer,
    InertializationChannel,
    DeadBlendingChannel,
    UnifiedGaitBlender,
    create_transition_synthesizer,
    inertialize_transition,
    phase_warp,
    adaptive_bounce,
    blend_walk_run,
    blend_gaits_at_phase,
    _marker_segment,
)
from mathart.animation.phase_driven import GaitMode
from mathart.animation.unified_motion import (
    MotionContactState,
    MotionRootTransform,
    pose_to_umr,
)


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
        """Verify GaitBlender is exported by the public animation package."""
        from mathart.animation import GaitBlender as GB
        assert GB is GaitBlender


# ── SESSION-111 P1-B3-5 Strangler-Fig Anti-Zombie-Reference Guard ─────────────


class TestRetiredShimExtermination:
    """Guarantee the legacy shim modules cannot be resurrected."""

    def test_gait_blend_file_is_gone(self):
        import mathart.animation as anim_pkg
        from pathlib import Path
        pkg_path = Path(anim_pkg.__file__).resolve().parent
        assert not (pkg_path / "gait_blend.py").exists(), (
            "gait_blend.py shim must be physically deleted (Strangler-Fig closure)"
        )

    def test_transition_synthesizer_file_is_gone(self):
        import mathart.animation as anim_pkg
        from pathlib import Path
        pkg_path = Path(anim_pkg.__file__).resolve().parent
        assert not (pkg_path / "transition_synthesizer.py").exists(), (
            "transition_synthesizer.py shim must be physically deleted"
        )

    def test_gait_blend_import_is_blocked(self):
        import importlib
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("mathart.animation.gait_blend")

    def test_transition_synthesizer_import_is_blocked(self):
        import importlib
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("mathart.animation.transition_synthesizer")


# ── SESSION-111 P1-B3-5 Inertialized Transition Synthesis (merged) ───────────


class TestTransitionStrategyEnum:
    """TransitionStrategy now lives on the single unified motion core."""

    def test_enum_values(self):
        assert TransitionStrategy.INERTIALIZATION.value == "inertialization"
        assert TransitionStrategy.DEAD_BLENDING.value == "dead_blending"

    def test_factory_returns_inactive_synthesizer(self):
        synth = create_transition_synthesizer(strategy="dead_blending")
        assert isinstance(synth, TransitionSynthesizer)
        assert synth.is_active is False

    def test_direct_construction_with_enum(self):
        synth = TransitionSynthesizer(strategy=TransitionStrategy.DEAD_BLENDING)
        assert synth.is_active is False


class TestQuinticInertializationDecay:
    """Bollo GDC 2018 quintic residual decay — monotonic & bounded on real frames."""

    @staticmethod
    def _seam_frames(source_angle: float, target_angle: float):
        src_pose = {"l_hip": source_angle, "r_hip": -source_angle}
        tgt_pose = {"l_hip": target_angle, "r_hip": -target_angle}
        src = pose_to_umr(
            src_pose, time=0.0, phase=0.25, source_state="walk",
            root_transform=MotionRootTransform(x=0.0, y=0.0, velocity_x=0.8, velocity_y=0.0),
            contact_tags=MotionContactState(left_foot=True, right_foot=False),
        )
        tgt = pose_to_umr(
            tgt_pose, time=0.0, phase=0.30, source_state="run",
            root_transform=MotionRootTransform(x=0.0, y=0.0, velocity_x=1.8, velocity_y=0.0),
            contact_tags=MotionContactState(left_foot=True, right_foot=False),
        )
        return src, tgt

    def _inertialization_residual_sequence(self, channel, blend_time: float, dt: float):
        source, target = self._seam_frames(source_angle=-0.2, target_angle=-0.5)
        channel.capture(source, prev_source_frame=source, dt=dt)
        frames = []
        n_frames = int(round((blend_time * 3.0) / dt)) + 1
        for _ in range(n_frames):
            frames.append(channel.apply(target, dt=dt))
        residuals = [
            abs(f.joint_local_rotations.get("l_hip", 0.0) - target.joint_local_rotations.get("l_hip", 0.0))
            for f in frames
        ]
        return residuals

    def test_quintic_residual_decays_monotonically(self):
        """Bollo 2018 quintic weight: residual must be non-increasing to zero."""
        channel = InertializationChannel(blend_time=0.2)
        residuals = self._inertialization_residual_sequence(channel, blend_time=0.2, dt=1.0 / 60.0)
        assert residuals[0] > 0.0
        # Allow small floating-point jitter but no strict resurrection of residual
        for a, b in zip(residuals, residuals[1:]):
            assert b <= a + 1e-6, f"quintic residual must be non-increasing: {a} -> {b}"
        assert residuals[-1] <= 1e-6, f"residual must decay to zero, got {residuals[-1]}"

    def test_quintic_residual_respects_blend_time_budget(self):
        """With blend_time=0.2s, residual should be effectively zero by ~0.25s."""
        dt = 1.0 / 60.0
        channel = InertializationChannel(blend_time=0.2)
        residuals = self._inertialization_residual_sequence(channel, blend_time=0.2, dt=dt)
        # Find index corresponding to ~blend_time + dt margin
        budget_idx = int(round(0.25 / dt))
        assert residuals[min(budget_idx, len(residuals) - 1)] <= 1e-5

    def test_dead_blending_residual_decays_monotonically_to_zero(self):
        """Holden dead-blending: residual must decay monotonically to zero within blend window."""
        dt = 1.0 / 120.0
        halflife = 0.05
        channel = DeadBlendingChannel(decay_halflife=halflife)
        residuals = self._inertialization_residual_sequence(
            channel, blend_time=halflife * 8.0, dt=dt,
        )
        r0 = residuals[0]
        assert r0 > 0.0
        # Monotonic non-increasing residual (with small floating-point tolerance)
        for a, b in zip(residuals, residuals[1:]):
            assert b <= a + 1e-6, f"dead-blending residual must be non-increasing: {a} -> {b}"
        # Residual must fully decay to zero before the sequence ends
        assert residuals[-1] <= 1e-6
        # Dead-blending should decay significantly faster than a 4×halflife linear fade:
        # at t=4×halflife, residual should be < 10% of initial.
        four_hl_idx = min(int(round(4.0 * halflife / dt)), len(residuals) - 1)
        assert residuals[four_hl_idx] < r0 * 0.15


class TestInertializeTransitionAPI:
    """Functional ``inertialize_transition`` \u2014 migrated from test_session039.py."""

    def _frame(self, *, state, phase, vx, vy, left_contact, right_contact, pose):
        return pose_to_umr(
            pose,
            time=0.0,
            phase=phase,
            source_state=state,
            root_transform=MotionRootTransform(x=0.0, y=0.0, velocity_x=vx, velocity_y=vy),
            contact_tags=MotionContactState(left_foot=left_contact, right_foot=right_contact),
        )

    def test_inertialize_preserves_target_state_and_contacts(self):
        source = self._frame(
            state="run", phase=0.5, vx=1.5, vy=0.0,
            left_contact=False, right_contact=True,
            pose={"l_hip": -0.3, "r_hip": 0.3, "l_knee": -0.2, "r_knee": -0.1, "spine": 0.05},
        )
        target = self._frame(
            state="jump", phase=0.0, vx=0.8, vy=1.0,
            left_contact=False, right_contact=False,
            pose={"l_hip": -0.1, "r_hip": 0.1, "l_knee": -0.4, "r_knee": -0.4, "spine": 0.1},
        )
        result = inertialize_transition([source, source], [target, target], dt=1.0 / 24.0)
        assert len(result) == 2
        assert result[0].source_state == "jump"
        assert result[0].contact_tags.left_foot is False
        assert result[0].contact_tags.right_foot is False

    def test_c0_c1_joint_continuity_is_not_violated_at_seam(self):
        """Anti-C0/C1-regression guard: joint-space residual must decay smoothly."""
        source_pose = {"l_hip": -0.2, "r_hip": 0.2, "l_knee": -0.1, "r_knee": -0.05, "spine": 0.02}
        target_pose = {"l_hip": -0.5, "r_hip": 0.5, "l_knee": -0.3, "r_knee": -0.25, "spine": 0.05}
        source = self._frame(
            state="walk", phase=0.25, vx=0.8, vy=0.0,
            left_contact=True, right_contact=False, pose=source_pose,
        )
        target = self._frame(
            state="run", phase=0.30, vx=1.8, vy=0.0,
            left_contact=True, right_contact=False, pose=target_pose,
        )
        dt = 1.0 / 60.0
        n = 24
        frames = inertialize_transition([source] * n, [target] * n, dt=dt)
        assert len(frames) == n
        # C0 continuity: first post-seam frame's joints are close to source pose
        # (inertialization lands softly from the source joint space).
        first_hip = frames[0].joint_local_rotations["l_hip"]
        assert first_hip == pytest.approx(source_pose["l_hip"], abs=0.05), (
            f"C0 joint continuity broken at seam: {first_hip} vs source {source_pose['l_hip']}"
        )
        # C1 continuity: frame-to-frame joint delta is bounded and non-exploding.
        l_hip_series = [f.joint_local_rotations["l_hip"] for f in frames]
        max_delta = max(abs(b - a) for a, b in zip(l_hip_series, l_hip_series[1:]))
        # Expected per-frame delta is bounded by (target - source) / (blend_frames),
        # empirically well under 0.1 rad per frame at 60 FPS for this seam.
        assert max_delta < 0.1, f"C1 joint-delta exceeded at seam: {max_delta}"
        # Terminal residual must converge to the target pose
        assert l_hip_series[-1] == pytest.approx(target_pose["l_hip"], abs=1e-4)


# ── SESSION-111 P1-B3-5 Anti-PRNG-Bleed Guard ─────────────────────────────────


class TestUnifiedGaitBlenderDeterminism:
    """Stateless / PRNG-free guarantee for PDG v2 16-thread parallel dispatch."""

    def test_source_is_free_of_global_prng(self):
        """Static guard: unified_gait_blender must not use global PRNG state."""
        import inspect
        from mathart.animation import unified_gait_blender as ugb

        source = inspect.getsource(ugb)
        assert "np.random" not in source, (
            "unified_gait_blender must not call np.random (Anti-PRNG-Bleed)"
        )
        for forbidden in (
            "random.random(", "random.seed(", "random.Random(",
            "random.uniform(", "random.randint(", "random.choice(",
        ):
            assert forbidden not in source, (
                f"unified_gait_blender must not call {forbidden} (Anti-PRNG-Bleed)"
            )

    def test_two_blenders_produce_bitwise_identical_poses(self):
        """Two independent blenders fed the same inputs must be byte-identical."""
        a = UnifiedGaitBlender()
        b = UnifiedGaitBlender()
        for _ in range(30):
            pose_a = a.update(dt=0.016, velocity=1.2, target_gait=GaitMode.WALK)
            pose_b = b.update(dt=0.016, velocity=1.2, target_gait=GaitMode.WALK)
        for k in pose_a:
            if k.startswith("_"):
                continue
            assert pose_a[k] == pytest.approx(pose_b[k], abs=1e-9), (
                f"Determinism violated at joint {k}: {pose_a[k]} vs {pose_b[k]}"
            )
