"""
SESSION-042: Generalized Phase State (Gap 1 Resolution) — Comprehensive Tests

Tests cover:
  1. PhaseState construction, normalization, and factories
  2. Gate mechanism in PhaseDrivenAnimator.generate_frame()
  3. Backward compatibility with legacy float phase
  4. PhaseState propagation through UMR pipeline
  5. Serialization / deserialization roundtrip
  6. Transient pose generation via unified entry point
  7. PhaseState in downstream consumers (motion matching, feature extraction)

Research grounding:
  - Local Motion Phases (Starke et al., SIGGRAPH 2020)
  - DeepPhase / Periodic Autoencoder (Starke, Mason, Komura, SIGGRAPH 2022)
"""
from __future__ import annotations

import math
import pytest

from mathart.animation.unified_motion import (
    PhaseState,
    UnifiedMotionFrame,
    MotionRootTransform,
    MotionContactState,
    pose_to_umr,
)
from mathart.animation.phase_driven import (
    PhaseDrivenAnimator,
    PhaseVariable,
    GaitMode,
    phase_driven_walk,
    phase_driven_jump,
    phase_driven_fall,
    phase_driven_hit,
    jump_distance_phase,
    fall_distance_phase,
    hit_recovery_phase,
)


# ── 1. PhaseState Construction & Normalization ────────────────────────────────


class TestPhaseStateConstruction:
    """Test PhaseState data class invariants."""

    def test_cyclic_wraps_modulo(self):
        ps = PhaseState(value=1.3, is_cyclic=True)
        assert 0.0 <= ps.value < 1.0
        assert abs(ps.value - 0.3) < 1e-6

    def test_cyclic_negative_wraps(self):
        ps = PhaseState(value=-0.2, is_cyclic=True)
        assert 0.0 <= ps.value < 1.0
        assert abs(ps.value - 0.8) < 1e-6

    def test_transient_clamps_to_01(self):
        ps = PhaseState(value=1.5, is_cyclic=False)
        assert ps.value == 1.0

    def test_transient_clamps_negative(self):
        ps = PhaseState(value=-0.3, is_cyclic=False)
        assert ps.value == 0.0

    def test_transient_preserves_valid(self):
        ps = PhaseState(value=0.65, is_cyclic=False, phase_kind="distance_to_apex")
        assert abs(ps.value - 0.65) < 1e-6

    def test_amplitude_non_negative(self):
        ps = PhaseState(value=0.5, amplitude=-1.0)
        assert ps.amplitude >= 0.0

    def test_frozen_immutability(self):
        ps = PhaseState(value=0.5)
        with pytest.raises(AttributeError):
            ps.value = 0.8  # type: ignore[misc]


class TestPhaseStateFactories:
    """Test factory class methods."""

    def test_cyclic_factory(self):
        ps = PhaseState.cyclic(0.25, phase_kind="walk")
        assert ps.is_cyclic is True
        assert ps.phase_kind == "walk"
        assert abs(ps.value - 0.25) < 1e-6

    def test_transient_factory(self):
        ps = PhaseState.transient(0.7, phase_kind="distance_to_apex", amplitude=0.8)
        assert ps.is_cyclic is False
        assert ps.phase_kind == "distance_to_apex"
        assert abs(ps.value - 0.7) < 1e-6
        assert abs(ps.amplitude - 0.8) < 1e-6


class TestPhaseStateSerialization:
    """Test to_dict / from_dict roundtrip."""

    def test_roundtrip_cyclic(self):
        ps = PhaseState.cyclic(0.42, phase_kind="run")
        d = ps.to_dict()
        ps2 = PhaseState.from_dict(d)
        assert abs(ps.value - ps2.value) < 1e-6
        assert ps.is_cyclic == ps2.is_cyclic
        assert ps.phase_kind == ps2.phase_kind

    def test_roundtrip_transient(self):
        ps = PhaseState.transient(0.88, phase_kind="hit_recovery", amplitude=0.6)
        d = ps.to_dict()
        ps2 = PhaseState.from_dict(d)
        assert abs(ps.value - ps2.value) < 1e-6
        assert ps.is_cyclic == ps2.is_cyclic
        assert ps.phase_kind == ps2.phase_kind
        assert abs(ps.amplitude - ps2.amplitude) < 1e-6

    def test_from_dict_legacy_phase_key(self):
        """Backward compat: from_dict accepts 'phase' key as fallback."""
        d = {"phase": 0.33, "is_cyclic": True, "phase_kind": "walk"}
        ps = PhaseState.from_dict(d)
        assert abs(ps.value - 0.33) < 1e-6


class TestPhaseStateTrigEncoding:
    """Test sin/cos circular encoding."""

    def test_sin_cos_at_zero(self):
        ps = PhaseState(value=0.0)
        s, c = ps.to_sin_cos()
        assert abs(s - 0.0) < 1e-6
        assert abs(c - 1.0) < 1e-6

    def test_sin_cos_at_quarter(self):
        ps = PhaseState(value=0.25)
        s, c = ps.to_sin_cos()
        assert abs(s - 1.0) < 1e-6
        assert abs(c - 0.0) < 1e-6

    def test_sin_cos_at_half(self):
        ps = PhaseState(value=0.5)
        s, c = ps.to_sin_cos()
        assert abs(s - 0.0) < 1e-6
        assert abs(c - (-1.0)) < 1e-6


# ── 2. Gate Mechanism in PhaseDrivenAnimator ──────────────────────────────────


class TestGateMechanism:
    """Test the cyclic/transient gate in generate_frame()."""

    def setup_method(self):
        self.animator = PhaseDrivenAnimator()

    def test_cyclic_gate_walk(self):
        ps = PhaseState.cyclic(0.25, phase_kind="walk")
        frame = self.animator.generate_frame(ps, gait=GaitMode.WALK)
        assert isinstance(frame, UnifiedMotionFrame)
        assert frame.phase_state is not None
        assert frame.phase_state.is_cyclic is True
        assert frame.metadata.get("phase_gate") == "cyclic"

    def test_cyclic_gate_run(self):
        ps = PhaseState.cyclic(0.5, phase_kind="run")
        frame = self.animator.generate_frame(ps, gait=GaitMode.RUN)
        assert frame.phase_state.is_cyclic is True
        assert frame.metadata.get("phase_gate") == "cyclic"

    def test_transient_gate_jump(self):
        ps = PhaseState.transient(0.6, phase_kind="distance_to_apex")
        frame = self.animator.generate_frame(ps)
        assert isinstance(frame, UnifiedMotionFrame)
        assert frame.phase_state is not None
        assert frame.phase_state.is_cyclic is False
        assert frame.metadata.get("phase_gate") == "transient"

    def test_transient_gate_fall(self):
        ps = PhaseState.transient(0.4, phase_kind="distance_to_ground")
        frame = self.animator.generate_frame(ps)
        assert frame.phase_state.is_cyclic is False
        assert frame.metadata.get("phase_gate") == "transient"

    def test_transient_gate_hit(self):
        ps = PhaseState.transient(0.8, phase_kind="hit_recovery")
        frame = self.animator.generate_frame(ps)
        assert frame.phase_state.is_cyclic is False
        assert frame.metadata.get("phase_gate") == "transient"

    def test_float_backward_compat(self):
        """Bare float should default to cyclic gate."""
        frame = self.animator.generate_frame(0.3, gait=GaitMode.WALK)
        assert frame.phase_state is not None
        assert frame.phase_state.is_cyclic is True

    def test_phase_variable_backward_compat(self):
        """PhaseVariable should default to cyclic gate."""
        pv = PhaseVariable()
        pv.advance(1 / 60, 1.0)
        frame = self.animator.generate_frame(pv, gait=GaitMode.WALK)
        assert frame.phase_state.is_cyclic is True

    def test_unified_entry_point_all_states(self):
        """All motion states can go through generate_frame()."""
        states = [
            PhaseState.cyclic(0.25, "walk"),
            PhaseState.cyclic(0.5, "run"),
            PhaseState.cyclic(0.1, "idle"),
            PhaseState.transient(0.3, "distance_to_apex"),
            PhaseState.transient(0.7, "distance_to_ground"),
            PhaseState.transient(0.5, "hit_recovery"),
        ]
        for ps in states:
            frame = self.animator.generate_frame(ps)
            assert isinstance(frame, UnifiedMotionFrame), f"Failed for {ps.phase_kind}"
            assert frame.phase_state is not None
            assert len(frame.joint_local_rotations) > 0

    def test_transient_pose_varies_with_progress(self):
        """Transient poses should change as progress goes 0→1."""
        poses = []
        for v in [0.0, 0.25, 0.5, 0.75, 1.0]:
            ps = PhaseState.transient(v, "distance_to_apex")
            frame = self.animator.generate_frame(ps)
            poses.append(frame.joint_local_rotations)
        # At least some joints should differ between start and end
        start = poses[0]
        end = poses[-1]
        diffs = [abs(start.get(k, 0) - end.get(k, 0)) for k in start]
        assert max(diffs) > 0.01, "Transient pose should vary across progress"


# ── 3. PhaseState in UMR Pipeline ────────────────────────────────────────────


class TestPhaseStateUMRIntegration:
    """Test PhaseState propagation through UMR frames."""

    def test_pose_to_umr_with_phase_state(self):
        ps = PhaseState.transient(0.6, "distance_to_apex")
        frame = pose_to_umr(
            {"spine": 0.1, "head": -0.05},
            time=0.5,
            phase=0.6,
            phase_state=ps,
        )
        assert frame.phase_state is not None
        assert frame.phase_state.is_cyclic is False
        assert frame.phase_state.phase_kind == "distance_to_apex"
        assert abs(frame.phase - 0.6) < 1e-6

    def test_pose_to_umr_auto_constructs_phase_state(self):
        """Legacy call without phase_state should auto-construct one."""
        frame = pose_to_umr(
            {"spine": 0.1},
            phase=0.3,
            metadata={"phase_kind": "distance_to_ground"},
        )
        assert frame.phase_state is not None
        assert frame.phase_state.is_cyclic is False
        assert frame.phase_state.phase_kind == "distance_to_ground"

    def test_pose_to_umr_default_cyclic(self):
        """No metadata → default cyclic PhaseState."""
        frame = pose_to_umr({"spine": 0.1}, phase=0.5)
        assert frame.phase_state is not None
        assert frame.phase_state.is_cyclic is True

    def test_frame_to_dict_includes_phase_state(self):
        ps = PhaseState.transient(0.4, "hit_recovery")
        frame = pose_to_umr({"spine": 0.0}, phase=0.4, phase_state=ps)
        d = frame.to_dict()
        assert "phase_state" in d
        assert d["phase_state"]["is_cyclic"] is False
        assert d["phase_state"]["phase_kind"] == "hit_recovery"

    def test_phase_state_in_metadata_propagation(self):
        """PhaseState.phase_kind should propagate to metadata."""
        ps = PhaseState.transient(0.5, "distance_to_apex")
        frame = pose_to_umr({"spine": 0.0}, phase=0.5, phase_state=ps)
        assert frame.metadata.get("phase_kind") == "distance_to_apex"


# ── 4. Transient Phase Generators → PhaseState ───────────────────────────────


class TestTransientGeneratorsPhaseState:
    """Test that existing transient generators produce data compatible with PhaseState."""

    def test_jump_distance_phase_to_phase_state(self):
        metrics = jump_distance_phase(root_y=0.1, apex_height=0.2)
        ps = PhaseState.transient(
            float(metrics["phase"]),
            phase_kind=str(metrics["phase_kind"]),
        )
        assert ps.is_cyclic is False
        assert 0.0 <= ps.value <= 1.0

    def test_fall_distance_phase_to_phase_state(self):
        metrics = fall_distance_phase(root_y=0.15, ground_height=0.0)
        ps = PhaseState.transient(
            float(metrics["phase"]),
            phase_kind=str(metrics["phase_kind"]),
        )
        assert ps.is_cyclic is False
        assert 0.0 <= ps.value <= 1.0

    def test_hit_recovery_phase_to_phase_state(self):
        metrics = hit_recovery_phase(0.3, impact_energy=0.8)
        ps = PhaseState.transient(
            float(metrics["phase"]),
            phase_kind=str(metrics["phase_kind"]),
        )
        assert ps.is_cyclic is False
        assert 0.0 <= ps.value <= 1.0


# ── 5. Generate Method with PhaseState ────────────────────────────────────────


class TestGenerateWithPhaseState:
    """Test the generate() method (pose-only, no UMR frame) with PhaseState."""

    def setup_method(self):
        self.animator = PhaseDrivenAnimator()

    def test_cyclic_generate(self):
        ps = PhaseState.cyclic(0.3)
        pose = self.animator.generate(ps, gait=GaitMode.WALK)
        assert isinstance(pose, dict)
        assert "spine" in pose

    def test_transient_generate(self):
        ps = PhaseState.transient(0.5, "distance_to_apex")
        pose = self.animator.generate(ps)
        assert isinstance(pose, dict)
        assert "spine" in pose

    def test_transient_hit_generate(self):
        ps = PhaseState.transient(0.8, "hit_recovery")
        pose = self.animator.generate(ps)
        assert isinstance(pose, dict)
        assert "spine" in pose


# ── 6. End-to-End: Full Motion Sequence Through Unified Entry Point ──────────


class TestEndToEndUnifiedSequence:
    """Test generating a complete motion sequence mixing cyclic and transient
    phases through the single unified generate_frame() entry point."""

    def test_walk_to_jump_to_fall_sequence(self):
        animator = PhaseDrivenAnimator()
        frames = []

        # Walk phase (cyclic)
        for i in range(12):
            p = i / 12.0
            ps = PhaseState.cyclic(p, "walk")
            frame = animator.generate_frame(ps, gait=GaitMode.WALK, time=i / 12.0, frame_index=i)
            frames.append(frame)
            assert frame.phase_state.is_cyclic is True

        # Jump phase (transient)
        for i in range(6):
            p = i / 5.0
            ps = PhaseState.transient(min(p, 1.0), "distance_to_apex")
            frame = animator.generate_frame(ps, time=1.0 + i / 12.0, frame_index=12 + i)
            frames.append(frame)
            assert frame.phase_state.is_cyclic is False

        # Fall phase (transient)
        for i in range(6):
            p = i / 5.0
            ps = PhaseState.transient(min(p, 1.0), "distance_to_ground")
            frame = animator.generate_frame(ps, time=1.5 + i / 12.0, frame_index=18 + i)
            frames.append(frame)
            assert frame.phase_state.is_cyclic is False

        assert len(frames) == 24
        # All frames have valid joint data
        for f in frames:
            assert len(f.joint_local_rotations) > 0
            assert f.phase_state is not None
