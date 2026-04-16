"""
SESSION-033: Phase-Driven Animation Control — Comprehensive Test Suite

Tests cover:
1. PhaseVariable — phase tracking, advancement, contact events
2. KeyPose / PhaseInterpolator — key-pose interpolation, mirroring
3. PhaseChannel — DeepPhase-inspired periodic channels
4. PhaseDrivenAnimator — walk/run/sneak generation
5. Drop-in replacements — phase_driven_walk/run compatibility
6. FFT parameter extraction — DeepPhase signal analysis
7. Integration — presets.py delegation, rl_locomotion.py upgrade
8. Biomechanics compatibility — works with BiomechanicsProjector
9. Animation quality — pelvis height trajectory, contact timing
"""
import math
import numpy as np
import pytest

from mathart.animation.phase_driven import (
    PhaseDrivenAnimator, PhaseVariable, GaitMode,
    PhaseInterpolator, PhaseChannel, KeyPose,
    phase_driven_walk, phase_driven_run,
    extract_phase_parameters, create_phase_channel_from_signal,
    WALK_KEY_POSES, RUN_KEY_POSES, WALK_CHANNELS, RUN_CHANNELS,
    _catmull_rom, _catmull_rom_array,
)


# ── PhaseVariable Tests ─────────────────────────────────────────────────────


class TestPhaseVariable:
    """Tests for PFNN-inspired phase variable."""

    def test_initial_state(self):
        pv = PhaseVariable()
        assert pv.phase == 0.0
        assert pv.phase_2pi == 0.0
        assert pv.cycle_count == 0

    def test_custom_initial_phase(self):
        pv = PhaseVariable(initial_phase=0.3)
        assert abs(pv.phase - 0.3) < 1e-10

    def test_advance_basic(self):
        pv = PhaseVariable()
        new_p = pv.advance(dt=1 / 60, speed=1.0, steps_per_second=2.0)
        # Expected: dt * speed * sps / 2 = (1/60) * 1.0 * 2.0 / 2.0 = 1/60
        assert abs(new_p - 1 / 60) < 1e-10

    def test_advance_wraps(self):
        pv = PhaseVariable(initial_phase=0.99)
        pv.advance(dt=0.1, speed=1.0, steps_per_second=2.0)
        assert 0.0 <= pv.phase < 1.0

    def test_cycle_count_increments(self):
        pv = PhaseVariable(initial_phase=0.99)
        pv.advance(dt=0.1, speed=1.0, steps_per_second=2.0)
        assert pv.cycle_count == 1

    def test_left_contact_event(self):
        pv = PhaseVariable(initial_phase=0.95)
        pv.advance(dt=0.1, speed=1.0, steps_per_second=2.0)
        # Phase should wrap past 0 → left contact
        assert pv.left_contact

    def test_right_contact_event(self):
        pv = PhaseVariable(initial_phase=0.45)
        pv.advance(dt=0.1, speed=1.0, steps_per_second=2.0)
        # Phase should cross 0.5 → right contact
        assert pv.right_contact

    def test_phase_2pi_conversion(self):
        pv = PhaseVariable(initial_phase=0.25)
        assert abs(pv.phase_2pi - math.pi / 2) < 1e-10

    def test_set_phase(self):
        pv = PhaseVariable()
        pv.set_phase(0.7)
        assert abs(pv.phase - 0.7) < 1e-10

    def test_reset(self):
        pv = PhaseVariable(initial_phase=0.5)
        pv.advance(dt=0.1, speed=1.0)
        pv.reset()
        assert pv.phase == 0.0
        assert pv.cycle_count == 0

    def test_speed_modulation(self):
        pv1 = PhaseVariable()
        pv2 = PhaseVariable()
        pv1.advance(dt=1 / 60, speed=1.0)
        pv2.advance(dt=1 / 60, speed=2.0)
        assert abs(pv2.phase - 2 * pv1.phase) < 1e-10


# ── Catmull-Rom Spline Tests ────────────────────────────────────────────────


class TestCatmullRom:
    """Tests for PFNN-style Catmull-Rom interpolation."""

    def test_passes_through_p1(self):
        result = _catmull_rom(0.0, 1.0, 2.0, 3.0, 0.0)
        assert abs(result - 1.0) < 1e-10

    def test_passes_through_p2(self):
        result = _catmull_rom(0.0, 1.0, 2.0, 3.0, 1.0)
        assert abs(result - 2.0) < 1e-10

    def test_midpoint_smooth(self):
        result = _catmull_rom(0.0, 1.0, 2.0, 3.0, 0.5)
        assert abs(result - 1.5) < 0.1  # Should be near midpoint

    def test_array_cyclic(self):
        values = [0.0, 1.0, 0.0, -1.0]
        # At t=0.0 should be near values[0]
        result = _catmull_rom_array(values, 0.0)
        assert abs(result - 0.0) < 0.2

    def test_array_midpoint(self):
        values = [0.0, 1.0, 0.0, -1.0]
        result = _catmull_rom_array(values, 0.25)
        assert abs(result - 1.0) < 0.2


# ── KeyPose and PhaseInterpolator Tests ──────────────────────────────────────


class TestPhaseInterpolator:
    """Tests for key-pose interpolation with mirroring."""

    def test_walk_poses_defined(self):
        assert len(WALK_KEY_POSES) >= 4
        names = [kp.name for kp in WALK_KEY_POSES]
        assert "contact" in names
        assert "down" in names
        assert "passing" in names
        assert "up" in names

    def test_run_poses_defined(self):
        assert len(RUN_KEY_POSES) >= 4
        names = [kp.name for kp in RUN_KEY_POSES]
        assert "contact" in names
        assert "flight" in names

    def test_interpolator_returns_dict(self):
        interp = PhaseInterpolator(WALK_KEY_POSES)
        pose, pelvis_h = interp.evaluate(0.0)
        assert isinstance(pose, dict)
        assert isinstance(pelvis_h, float)

    def test_interpolator_has_required_joints(self):
        interp = PhaseInterpolator(WALK_KEY_POSES)
        pose, _ = interp.evaluate(0.0)
        required = ["l_hip", "r_hip", "l_knee", "r_knee", "spine"]
        for joint in required:
            assert joint in pose, f"Missing joint: {joint}"

    def test_mirroring_symmetry(self):
        """Left step at phase=0 should mirror right step at phase=0.5."""
        interp = PhaseInterpolator(WALK_KEY_POSES)
        pose_0, _ = interp.evaluate(0.0)
        pose_half, _ = interp.evaluate(0.5)

        # l_hip at 0.0 should equal r_hip at 0.5
        assert abs(pose_0["l_hip"] - pose_half["r_hip"]) < 1e-6
        assert abs(pose_0["r_hip"] - pose_half["l_hip"]) < 1e-6

    def test_pelvis_height_trajectory(self):
        """Pelvis should be lowest at Down and highest at Up."""
        interp = PhaseInterpolator(WALK_KEY_POSES)
        _, h_contact = interp.evaluate(0.0)
        _, h_down = interp.evaluate(0.125)
        _, h_pass = interp.evaluate(0.25)
        _, h_up = interp.evaluate(0.375)

        # Down should be lowest
        assert h_down < h_contact
        assert h_down < h_pass
        # Up should be highest
        assert h_up >= h_pass

    def test_all_phases_finite(self):
        """All poses across full cycle should have finite values."""
        interp = PhaseInterpolator(WALK_KEY_POSES)
        for i in range(100):
            p = i / 100
            pose, h = interp.evaluate(p)
            for joint, val in pose.items():
                assert math.isfinite(val), f"Non-finite at p={p}, joint={joint}"
            assert math.isfinite(h)


# ── PhaseChannel Tests ──────────────────────────────────────────────────────


class TestPhaseChannel:
    """Tests for DeepPhase-inspired periodic channels."""

    def test_basic_sine(self):
        ch = PhaseChannel(amplitude=1.0, frequency=1.0, phase_shift=0.0, offset=0.0)
        assert abs(ch.evaluate(0.0)) < 1e-10  # sin(0) = 0
        assert abs(ch.evaluate(0.25) - 1.0) < 1e-10  # sin(π/2) = 1

    def test_offset(self):
        ch = PhaseChannel(amplitude=1.0, frequency=1.0, phase_shift=0.0, offset=0.5)
        assert abs(ch.evaluate(0.0) - 0.5) < 1e-10

    def test_frequency_doubling(self):
        ch = PhaseChannel(amplitude=1.0, frequency=2.0, phase_shift=0.0, offset=0.0)
        # At p=0.125, angle = 2π * 2 * 0.125 = π/2 → sin = 1
        assert abs(ch.evaluate(0.125) - 1.0) < 1e-10

    def test_phase_shift(self):
        ch = PhaseChannel(amplitude=1.0, frequency=1.0, phase_shift=0.25, offset=0.0)
        # At p=0.25, angle = 2π(1*0.25 - 0.25) = 0 → sin = 0
        assert abs(ch.evaluate(0.25)) < 1e-10

    def test_2d_representation(self):
        ch = PhaseChannel(amplitude=1.0, frequency=1.0, phase_shift=0.0, offset=0.0)
        px, py = ch.evaluate_2d(0.0)
        # cos(0) = 1, sin(0) = 0
        assert abs(px - 1.0) < 1e-10
        assert abs(py) < 1e-10

    def test_walk_channels_defined(self):
        assert "torso_bob" in WALK_CHANNELS
        assert "torso_twist" in WALK_CHANNELS
        assert "head_stabilize" in WALK_CHANNELS

    def test_run_channels_defined(self):
        assert "arm_pump" in RUN_CHANNELS
        assert "torso_bob" in RUN_CHANNELS


# ── PhaseDrivenAnimator Tests ───────────────────────────────────────────────


class TestPhaseDrivenAnimator:
    """Tests for the main animator integration."""

    @pytest.fixture
    def animator(self):
        return PhaseDrivenAnimator()

    def test_walk_returns_dict(self, animator):
        pose = animator.walk_pose(0.0)
        assert isinstance(pose, dict)
        assert len(pose) > 0

    def test_run_returns_dict(self, animator):
        pose = animator.run_pose(0.0)
        assert isinstance(pose, dict)
        assert len(pose) > 0

    def test_walk_has_core_joints(self, animator):
        pose = animator.walk_pose(0.0)
        core = ["spine", "l_hip", "r_hip", "l_knee", "r_knee",
                "l_shoulder", "r_shoulder"]
        for joint in core:
            assert joint in pose, f"Walk missing: {joint}"

    def test_run_has_core_joints(self, animator):
        pose = animator.run_pose(0.0)
        core = ["spine", "l_hip", "r_hip", "l_knee", "r_knee",
                "l_shoulder", "r_shoulder"]
        for joint in core:
            assert joint in pose, f"Run missing: {joint}"

    def test_walk_full_cycle_finite(self, animator):
        for i in range(100):
            t = i / 100
            pose = animator.walk_pose(t)
            for joint, val in pose.items():
                assert math.isfinite(val), f"Non-finite walk at t={t}, {joint}"

    def test_run_full_cycle_finite(self, animator):
        for i in range(100):
            t = i / 100
            pose = animator.run_pose(t)
            for joint, val in pose.items():
                assert math.isfinite(val), f"Non-finite run at t={t}, {joint}"

    def test_walk_alternation(self, animator):
        """Left and right legs should alternate."""
        pose_0 = animator.walk_pose(0.0)
        pose_half = animator.walk_pose(0.5)
        # At t=0 left leg forward, at t=0.5 right leg forward
        assert pose_0["l_hip"] > 0  # Left forward
        assert pose_0["r_hip"] < 0  # Right back
        assert pose_half["r_hip"] > 0  # Right forward
        assert pose_half["l_hip"] < 0  # Left back

    def test_run_forward_lean(self, animator):
        """Run should have forward lean (positive spine)."""
        for i in range(20):
            t = i / 20
            pose = animator.run_pose(t)
            assert pose["spine"] > 0, f"Run not leaning forward at t={t}"

    def test_generate_with_phase_variable(self, animator):
        pv = PhaseVariable(initial_phase=0.3)
        pose = animator.generate(pv, gait=GaitMode.WALK)
        assert isinstance(pose, dict)
        assert len(pose) > 0

    def test_generate_gait_modes(self, animator):
        for gait in GaitMode:
            pose = animator.generate(0.25, gait=gait)
            assert isinstance(pose, dict)

    def test_speed_modulation(self, animator):
        """Higher speed should increase forward lean."""
        pose_slow = animator.generate(0.25, GaitMode.RUN, speed=0.5)
        pose_fast = animator.generate(0.25, GaitMode.RUN, speed=2.0)
        assert pose_fast["spine"] > pose_slow["spine"]

    def test_sneak_more_crouched(self, animator):
        """Sneak should have more bent knees than walk."""
        walk = animator.generate(0.25, GaitMode.WALK)
        sneak = animator.generate(0.25, GaitMode.SNEAK)
        # Sneak knees should be more negative (more bent)
        assert sneak["l_knee"] < walk["l_knee"]


# ── Drop-in Replacement Tests ───────────────────────────────────────────────


class TestDropInReplacements:
    """Tests that phase_driven_walk/run work as presets replacements."""

    def test_walk_returns_dict(self):
        pose = phase_driven_walk(0.0)
        assert isinstance(pose, dict)

    def test_run_returns_dict(self):
        pose = phase_driven_run(0.0)
        assert isinstance(pose, dict)

    def test_walk_compatible_with_presets(self):
        """Should have same key structure as old run_animation."""
        pose = phase_driven_walk(0.0)
        required = ["spine", "l_hip", "r_hip", "l_knee", "r_knee",
                     "l_shoulder", "r_shoulder"]
        for key in required:
            assert key in pose

    def test_run_compatible_with_presets(self):
        pose = phase_driven_run(0.0)
        required = ["spine", "l_hip", "r_hip", "l_knee", "r_knee",
                     "l_shoulder", "r_shoulder"]
        for key in required:
            assert key in pose

    def test_presets_delegation(self):
        """presets.run_animation should now delegate to phase_driven_run."""
        from mathart.animation.presets import run_animation
        pose = run_animation(0.3)
        assert isinstance(pose, dict)
        assert "l_hip" in pose
        # Verify it's the phase-driven version (has l_foot)
        assert "l_foot" in pose

    def test_presets_walk_animation(self):
        """New walk_animation preset should work."""
        from mathart.animation.presets import walk_animation
        pose = walk_animation(0.3)
        assert isinstance(pose, dict)
        assert "l_hip" in pose

    def test_legacy_preserved(self):
        """Legacy run_animation_legacy should still work."""
        from mathart.animation.presets import run_animation_legacy
        pose = run_animation_legacy(0.3)
        assert isinstance(pose, dict)
        assert "l_hip" in pose


# ── FFT Parameter Extraction Tests ──────────────────────────────────────────


class TestPhaseExtraction:
    """Tests for DeepPhase-inspired signal analysis."""

    def test_extract_pure_sine(self):
        t = np.linspace(0, 1, 100)
        signal = np.sin(2 * np.pi * 5 * t)
        params = extract_phase_parameters(signal, sample_rate=100.0)
        assert abs(params["frequency"] - 5.0) < 0.5
        assert abs(params["offset"]) < 0.1

    def test_extract_with_offset(self):
        t = np.linspace(0, 1, 100)
        signal = np.sin(2 * np.pi * 3 * t) + 2.0
        params = extract_phase_parameters(signal, sample_rate=100.0)
        assert abs(params["offset"] - 2.0) < 0.2

    def test_create_channel_from_signal(self):
        t = np.linspace(0, 1, 100)
        signal = 0.5 * np.sin(2 * np.pi * 4 * t)
        ch = create_phase_channel_from_signal(signal, sample_rate=100.0)
        assert isinstance(ch, PhaseChannel)
        assert abs(ch.frequency - 4.0) < 0.5

    def test_short_signal(self):
        """Should handle very short signals gracefully."""
        signal = np.array([1.0, 2.0])
        params = extract_phase_parameters(signal, sample_rate=30.0)
        assert all(math.isfinite(v) for v in params.values())


# ── Integration Tests ────────────────────────────────────────────────────────


class TestIntegration:
    """Integration tests with existing project modules."""

    def test_import_from_animation_package(self):
        """Should be importable from the main animation package."""
        from mathart.animation import (
            PhaseDrivenAnimator, PhaseVariable, GaitMode,
            phase_driven_walk, phase_driven_run,
        )
        assert PhaseDrivenAnimator is not None

    def test_rl_locomotion_walk_cycle(self):
        """RL reference motion walk should use phase-driven."""
        from mathart.animation.rl_locomotion import ReferenceMotionLibrary
        lib = ReferenceMotionLibrary()
        walk = lib.get_motion("walk")
        assert len(walk) > 0
        # Check first frame has expected keys
        assert "l_hip" in walk[0]
        assert "l_foot" in walk[0]

    def test_rl_locomotion_run_cycle(self):
        """RL reference motion run should use phase-driven."""
        from mathart.animation.rl_locomotion import ReferenceMotionLibrary
        lib = ReferenceMotionLibrary()
        run = lib.get_motion("run")
        assert len(run) > 0
        assert "l_hip" in run[0]

    def test_run_animation_returns_more_joints(self):
        """Phase-driven run_animation should return more joints than legacy."""
        from mathart.animation.presets import run_animation, run_animation_legacy
        new_pose = run_animation(0.3)
        old_pose = run_animation_legacy(0.3)
        # New version should have foot joints
        assert "l_foot" in new_pose
        assert "l_foot" not in old_pose

    def test_full_cycle_continuity(self):
        """Poses should change smoothly across the cycle (no jumps)."""
        animator = PhaseDrivenAnimator()
        prev_pose = animator.walk_pose(0.0)
        max_delta = 0.0
        for i in range(1, 100):
            t = i / 100
            pose = animator.walk_pose(t)
            for joint in prev_pose:
                if joint in pose:
                    delta = abs(pose[joint] - prev_pose[joint])
                    max_delta = max(max_delta, delta)
            prev_pose = pose
        # Maximum change between adjacent 1% steps should be reasonable
        assert max_delta < 0.5, f"Discontinuity detected: max_delta={max_delta}"


# ── Animation Quality Tests ──────────────────────────────────────────────────


class TestAnimationQuality:
    """Tests for animation quality based on research criteria."""

    def test_walk_pelvis_down_at_contact_plus(self):
        """Pelvis should dip after contact (Williams: 'weight goes DOWN just after the step')."""
        interp = PhaseInterpolator(WALK_KEY_POSES)
        _, h_contact = interp.evaluate(0.0)
        _, h_down = interp.evaluate(0.125)
        assert h_down < h_contact

    def test_walk_pelvis_up_after_passing(self):
        """Pelvis should rise after passing position."""
        interp = PhaseInterpolator(WALK_KEY_POSES)
        _, h_pass = interp.evaluate(0.25)
        _, h_up = interp.evaluate(0.375)
        assert h_up > h_pass or abs(h_up - h_pass) < 0.01

    def test_walk_arms_oppose_legs(self):
        """Arms should counter-rotate to legs (Williams principle)."""
        pose = phase_driven_walk(0.0)
        # Left leg forward → left arm back
        assert pose["l_hip"] > 0  # Left leg forward
        assert pose["l_shoulder"] < 0  # Left arm back

    def test_run_greater_lean_than_walk(self):
        """Run should have more forward lean than walk."""
        walk = phase_driven_walk(0.0)
        run = phase_driven_run(0.0)
        assert run["spine"] > walk["spine"]

    def test_run_flight_phase_exists(self):
        """Run should have a flight phase key pose."""
        names = [kp.name for kp in RUN_KEY_POSES]
        assert "flight" in names

    def test_knee_always_negative(self):
        """Knees should always bend backward (negative values)."""
        for i in range(100):
            t = i / 100
            walk = phase_driven_walk(t)
            if "l_knee" in walk:
                assert walk["l_knee"] <= 0.01, f"Walk l_knee positive at t={t}"
            if "r_knee" in walk:
                assert walk["r_knee"] <= 0.01, f"Walk r_knee positive at t={t}"

    def test_walk_down_arms_widest(self):
        """Arms should be at widest at Down position (Williams p.108)."""
        interp = PhaseInterpolator(WALK_KEY_POSES)
        pose_contact, _ = interp.evaluate(0.0)
        pose_down, _ = interp.evaluate(0.125)

        # Arm spread = |l_shoulder - r_shoulder|
        spread_contact = abs(pose_contact.get("l_shoulder", 0) - pose_contact.get("r_shoulder", 0))
        spread_down = abs(pose_down.get("l_shoulder", 0) - pose_down.get("r_shoulder", 0))
        assert spread_down >= spread_contact - 0.05  # Down should have wider or equal arms
