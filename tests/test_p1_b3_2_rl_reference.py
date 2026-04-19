"""End-to-end test suite for P1-B3-2: UMR → RL Reference Motion Asset Closed Loop.

SESSION-080: Validates the full pipeline from UnifiedMotionBackend generation
through UMR→RL tensorized adapter to DeepMimic imitation reward closure.

Architecture discipline
-----------------------
This test suite proves three critical properties with mathematical assertions:

1. **Triple-Runtime Consumption**: The RL environment dynamically consumes
   unified_motion backend output via MicrokernelPipelineBridge, using all
   three preloaded namespaces (physics_gait, cognitive_motion, transient_motion).

2. **Tensorized Pre-baking**: UMR nested-dict frames are correctly flattened
   into contiguous SoA NumPy buffers with O(1) phase-indexed lookup.

3. **DeepMimic Reward Closure**: The imitation reward provably decays with
   increasing agent-reference deviation — zero-deviation yields reward ≈ 1.0,
   large deviation yields reward near 0.0.

Red-line enforcement
--------------------
- No per-step I/O: All reference data is pre-baked at init time.
- No dimension mismatch: Joint order and channel schema are validated.
- No fake reward: Mathematical assertions prove reward sensitivity.

References
----------
[1] Peng et al., "DeepMimic" (SIGGRAPH 2018)
[2] Makoviychuk et al., "Isaac Gym" (NeurIPS 2021)
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from mathart.animation.unified_motion import (
    MotionContactState,
    MotionRootTransform,
    PhaseState,
    UnifiedMotionClip,
    UnifiedMotionFrame,
)
from mathart.animation.umr_rl_adapter import (
    RL_JOINT_ORDER,
    DeepMimicRewardConfig,
    PrebakedReferenceBuffers,
    compute_imitation_reward,
    flatten_umr_to_rl_state,
    generate_umr_reference_clips,
    interpolate_reference,
)
from mathart.core.pipeline_bridge import MicrokernelPipelineBridge
from mathart.distill.compiler import Constraint, ParameterSpace
from mathart.distill.runtime_bus import RuntimeDistillationBus


# ═══════════════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════════════


def _build_synthetic_umr_clip(
    state: str = "walk",
    n_frames: int = 24,
    fps: int = 12,
    *,
    amplitude: float = 0.3,
) -> UnifiedMotionClip:
    """Build a deterministic synthetic UMR clip for testing.

    Generates a sinusoidal walk cycle with known joint angles, root motion,
    and contact patterns. This allows mathematical verification of the
    flatten/interpolate/reward pipeline.
    """
    frames: list[UnifiedMotionFrame] = []
    dt = 1.0 / max(fps, 1)

    for i in range(n_frames):
        phase = float(i) / max(n_frames - 1, 1)
        t = float(i) * dt
        theta = 2.0 * math.pi * phase

        # Sinusoidal joint angles with known amplitude
        joints: dict[str, float] = {}
        for j, jname in enumerate(RL_JOINT_ORDER):
            # Each joint gets a phase-shifted sine wave
            joints[jname] = amplitude * math.sin(theta + j * 0.5)

        # Root motion: linear progression with sinusoidal vertical
        root = MotionRootTransform(
            x=t * 1.5,
            y=0.02 * math.sin(2.0 * theta),
            rotation=0.0,
            velocity_x=1.5,
            velocity_y=0.02 * 2.0 * math.cos(2.0 * theta) * 2.0 * math.pi * fps,
            angular_velocity=0.0,
        )

        # Contact pattern: alternating feet
        left_contact = phase < 0.5
        right_contact = phase >= 0.5
        contacts = MotionContactState(
            left_foot=left_contact,
            right_foot=right_contact,
        )

        frame = UnifiedMotionFrame(
            time=t,
            phase=phase,
            root_transform=root,
            joint_local_rotations=joints,
            contact_tags=contacts,
            frame_index=i,
            source_state=state,
            metadata={"joint_channel_schema": "2d_scalar"},
            phase_state=PhaseState.cyclic(phase),
        )
        frames.append(frame)

    return UnifiedMotionClip(
        clip_id=f"test_{state}_umr",
        state=state,
        fps=fps,
        frames=frames,
        metadata={"generator": "test_synthetic"},
    )


def _make_runtime_bus(
    tmp_path: Path,
    *,
    blend_time: float = 0.2,
    phase_weight: float = 1.0,
) -> RuntimeDistillationBus:
    """Create a RuntimeDistillationBus with physics_gait namespace registered."""
    bus = RuntimeDistillationBus(project_root=tmp_path)
    space = ParameterSpace(name="physics_gait_test")
    space.add_constraint(Constraint(
        param_name="physics_gait.blend_time",
        min_value=0.01,
        max_value=1.0,
        default_value=blend_time,
        is_hard=False,
        source_rule_id="P1-B3-2",
    ))
    space.add_constraint(Constraint(
        param_name="physics_gait.phase_weight",
        min_value=0.0,
        max_value=1.0,
        default_value=phase_weight,
        is_hard=False,
        source_rule_id="P1-B3-2",
    ))
    bus.register_space("physics_gait", space)
    return bus


# ═══════════════════════════════════════════════════════════════════════════
#  Test 1: UMR → Pre-baked Buffers (Tensorized Adapter)
# ═══════════════════════════════════════════════════════════════════════════


class TestFlattenUMRToRLState:
    """Validate the UMR-to-RL tensorized flattening contract."""

    def test_buffer_shapes_match_clip(self) -> None:
        """Pre-baked buffers have correct shapes matching frame/joint counts."""
        clip = _build_synthetic_umr_clip(n_frames=24, fps=12)
        buffers = flatten_umr_to_rl_state(clip.frames, fps=12)

        assert buffers.num_frames == 24
        assert buffers.num_joints == len(RL_JOINT_ORDER)
        assert buffers.pose_buf.shape == (24, len(RL_JOINT_ORDER))
        assert buffers.velocity_buf.shape == (24, len(RL_JOINT_ORDER))
        assert buffers.root_buf.shape == (24, 6)
        assert buffers.phase_buf.shape == (24,)
        assert buffers.contact_buf.shape == (24, 4)
        assert buffers.ee_buf.shape == (24, 4)
        assert buffers.com_buf.shape == (24, 2)
        assert buffers.time_buf.shape == (24,)

    def test_buffers_are_contiguous_float32(self) -> None:
        """All buffers are contiguous float32 arrays (Isaac Gym discipline)."""
        clip = _build_synthetic_umr_clip(n_frames=12)
        buffers = flatten_umr_to_rl_state(clip.frames, fps=12)

        for name in ["pose_buf", "velocity_buf", "root_buf", "phase_buf",
                      "contact_buf", "ee_buf", "com_buf", "time_buf"]:
            arr = getattr(buffers, name)
            assert arr.dtype == np.float32, f"{name} dtype is {arr.dtype}"
            assert arr.flags["C_CONTIGUOUS"], f"{name} is not C-contiguous"

    def test_pose_values_match_umr_frames(self) -> None:
        """Pose buffer values exactly match UMR frame joint angles."""
        clip = _build_synthetic_umr_clip(n_frames=8, amplitude=0.5)
        buffers = flatten_umr_to_rl_state(clip.frames, fps=12)

        for i, frame in enumerate(clip.frames):
            for j, jname in enumerate(RL_JOINT_ORDER):
                expected = float(frame.joint_local_rotations.get(jname, 0.0))
                actual = float(buffers.pose_buf[i, j])
                assert abs(actual - expected) < 1e-6, (
                    f"Frame {i}, joint {jname}: expected {expected}, got {actual}"
                )

    def test_velocity_finite_difference(self) -> None:
        """Velocity buffer is computed via finite difference from pose buffer."""
        clip = _build_synthetic_umr_clip(n_frames=12, fps=12)
        buffers = flatten_umr_to_rl_state(clip.frames, fps=12)

        dt = 1.0 / 12.0
        # First frame velocity should be zero
        np.testing.assert_allclose(buffers.velocity_buf[0], 0.0, atol=1e-6)

        # Subsequent frames: velocity = (pose[i] - pose[i-1]) / dt
        for i in range(1, 12):
            expected_vel = (buffers.pose_buf[i] - buffers.pose_buf[i - 1]) / dt
            np.testing.assert_allclose(
                buffers.velocity_buf[i], expected_vel, atol=1e-5,
                err_msg=f"Velocity mismatch at frame {i}",
            )

    def test_phase_monotonic_for_cyclic_clip(self) -> None:
        """Phase buffer is monotonically increasing within a single cycle.

        Note: The last frame of a cyclic clip may wrap back to 0.0
        (phase = frame_index / (n_frames - 1), so frame n-1 maps to 1.0
        which PhaseState normalizes to 0.0 for cyclic phases). We test
        monotonicity up to the second-to-last frame.
        """
        clip = _build_synthetic_umr_clip(n_frames=24, state="walk")
        buffers = flatten_umr_to_rl_state(clip.frames, fps=12)

        # Monotonic up to n-2 (last frame may wrap due to cyclic phase)
        for i in range(1, 23):
            assert buffers.phase_buf[i] >= buffers.phase_buf[i - 1], (
                f"Phase not monotonic at frame {i}: "
                f"{buffers.phase_buf[i]} < {buffers.phase_buf[i-1]}"
            )

    def test_contact_binary_values(self) -> None:
        """Contact buffer contains only 0.0 or 1.0 values."""
        clip = _build_synthetic_umr_clip(n_frames=12)
        buffers = flatten_umr_to_rl_state(clip.frames, fps=12)

        unique_vals = set(buffers.contact_buf.flatten().tolist())
        assert unique_vals.issubset({0.0, 1.0}), (
            f"Contact buffer has non-binary values: {unique_vals}"
        )

    def test_empty_clip_raises_error(self) -> None:
        """Flattening an empty clip raises ValueError."""
        with pytest.raises(ValueError, match="Cannot flatten empty"):
            flatten_umr_to_rl_state([], fps=12)

    def test_schema_mismatch_raises_error(self) -> None:
        """Inconsistent joint_channel_schema across frames raises ValueError."""
        clip = _build_synthetic_umr_clip(n_frames=4)
        # Mutate one frame's schema
        bad_frame = UnifiedMotionFrame(
            time=0.5,
            phase=0.5,
            root_transform=MotionRootTransform(),
            joint_local_rotations={"spine": 0.1},
            metadata={"joint_channel_schema": "3d_euler"},
            frame_index=99,
            source_state="walk",
        )
        mixed_frames = list(clip.frames[:2]) + [bad_frame] + list(clip.frames[3:])
        with pytest.raises(ValueError, match="Joint channel schema mismatch"):
            flatten_umr_to_rl_state(mixed_frames, fps=12)


# ═══════════════════════════════════════════════════════════════════════════
#  Test 2: O(1) Phase-Indexed Interpolation
# ═══════════════════════════════════════════════════════════════════════════


class TestInterpolateReference:
    """Validate O(1) phase-indexed reference lookup with linear interpolation."""

    def test_phase_zero_returns_first_frame(self) -> None:
        """Phase 0.0 returns exactly the first frame's data."""
        clip = _build_synthetic_umr_clip(n_frames=12, amplitude=0.4)
        buffers = flatten_umr_to_rl_state(clip.frames, fps=12)

        ref = interpolate_reference(buffers, 0.0)
        np.testing.assert_allclose(ref["pose"], buffers.pose_buf[0], atol=1e-6)
        np.testing.assert_allclose(ref["root"], buffers.root_buf[0], atol=1e-6)

    def test_phase_one_returns_last_frame(self) -> None:
        """Phase 1.0 (wraps to 0.0) returns the first frame's data."""
        clip = _build_synthetic_umr_clip(n_frames=12, amplitude=0.4)
        buffers = flatten_umr_to_rl_state(clip.frames, fps=12)

        ref = interpolate_reference(buffers, 1.0)
        np.testing.assert_allclose(ref["pose"], buffers.pose_buf[0], atol=1e-6)

    def test_midpoint_interpolation(self) -> None:
        """Phase at exact midpoint between two frames returns their average."""
        clip = _build_synthetic_umr_clip(n_frames=12, amplitude=0.5)
        buffers = flatten_umr_to_rl_state(clip.frames, fps=12)

        # Phase that maps to exactly between frame 5 and frame 6
        # t = phase * (n-1) = phase * 11
        # For t = 5.5: phase = 5.5 / 11 = 0.5
        ref = interpolate_reference(buffers, 0.5)
        expected_pose = 0.5 * buffers.pose_buf[5] + 0.5 * buffers.pose_buf[6]
        np.testing.assert_allclose(ref["pose"], expected_pose, atol=1e-5)

    def test_interpolation_returns_correct_keys(self) -> None:
        """Interpolated reference contains all required state keys."""
        clip = _build_synthetic_umr_clip(n_frames=8)
        buffers = flatten_umr_to_rl_state(clip.frames, fps=12)

        ref = interpolate_reference(buffers, 0.3)
        assert set(ref.keys()) == {"pose", "velocity", "root", "contact", "ee", "com"}

    def test_empty_buffers_return_zeros(self) -> None:
        """Empty buffers return zero arrays."""
        buffers = PrebakedReferenceBuffers(num_joints=14)
        ref = interpolate_reference(buffers, 0.5)
        assert ref["pose"].shape == (14,)
        np.testing.assert_allclose(ref["pose"], 0.0)


# ═══════════════════════════════════════════════════════════════════════════
#  Test 3: DeepMimic Imitation Reward — Mathematical Closure
# ═══════════════════════════════════════════════════════════════════════════


class TestDeepMimicImitationReward:
    """Prove that the imitation reward provably decays with deviation.

    This is the critical "no fake reward" test. We construct scenarios with
    known deviations and assert that the reward function responds correctly.
    """

    def test_zero_deviation_yields_maximum_reward(self) -> None:
        """When agent perfectly matches reference, total reward ≈ 1.0.

        Mathematical proof: exp(-k * 0²) = exp(0) = 1.0 for all k > 0.
        Weighted sum of 1.0's with weights summing to 1.0 = 1.0.
        """
        n_joints = len(RL_JOINT_ORDER)
        pose = np.zeros(n_joints, dtype=np.float32)
        velocity = np.zeros(n_joints, dtype=np.float32)
        ee = np.zeros(4, dtype=np.float32)
        com = np.zeros(2, dtype=np.float32)

        result = compute_imitation_reward(
            agent_pose=pose, agent_velocity=velocity,
            agent_ee=ee, agent_com=com,
            ref_pose=pose, ref_velocity=velocity,
            ref_ee=ee, ref_com=com,
        )

        assert result["total"] == pytest.approx(1.0, abs=1e-10)
        assert result["pose"] == pytest.approx(1.0, abs=1e-10)
        assert result["velocity"] == pytest.approx(1.0, abs=1e-10)
        assert result["end_effector"] == pytest.approx(1.0, abs=1e-10)
        assert result["com"] == pytest.approx(1.0, abs=1e-10)

    def test_large_pose_deviation_yields_near_zero_reward(self) -> None:
        """Large pose deviation causes reward to decay toward zero.

        Mathematical proof: exp(-5.0 * 14 * π²) ≈ exp(-691) ≈ 0.0
        (14 joints, each with π radian error)
        """
        n_joints = len(RL_JOINT_ORDER)
        ref_pose = np.zeros(n_joints, dtype=np.float32)
        agent_pose = np.full(n_joints, math.pi, dtype=np.float32)
        velocity = np.zeros(n_joints, dtype=np.float32)
        ee = np.zeros(4, dtype=np.float32)
        com = np.zeros(2, dtype=np.float32)

        result = compute_imitation_reward(
            agent_pose=agent_pose, agent_velocity=velocity,
            agent_ee=ee, agent_com=com,
            ref_pose=ref_pose, ref_velocity=velocity,
            ref_ee=ee, ref_com=com,
        )

        # Pose reward should be extremely close to zero
        assert result["pose"] < 1e-10
        # Total reward should be significantly less than 1.0
        # (velocity, ee, com are still 1.0 but pose dominates with w=0.65)
        assert result["total"] < 0.36  # 1.0 - 0.65 = 0.35 max without pose

    def test_reward_monotonically_decreases_with_deviation(self) -> None:
        """Reward strictly decreases as pose deviation increases.

        This is the core "no fake reward" proof: we sweep deviation from
        0 to 2π and assert strict monotonic decrease.
        """
        n_joints = len(RL_JOINT_ORDER)
        ref_pose = np.zeros(n_joints, dtype=np.float32)
        velocity = np.zeros(n_joints, dtype=np.float32)
        ee = np.zeros(4, dtype=np.float32)
        com = np.zeros(2, dtype=np.float32)

        deviations = [0.0, 0.1, 0.3, 0.5, 1.0, 2.0, math.pi]
        pose_rewards: list[float] = []

        for dev in deviations:
            agent_pose = np.full(n_joints, dev, dtype=np.float32)
            result = compute_imitation_reward(
                agent_pose=agent_pose, agent_velocity=velocity,
                agent_ee=ee, agent_com=com,
                ref_pose=ref_pose, ref_velocity=velocity,
                ref_ee=ee, ref_com=com,
            )
            pose_rewards.append(result["pose"])

        # Assert strict monotonic decrease of the POSE sub-reward.
        # The total reward floors at (1-w_pose)=0.35 when pose→0 and
        # other channels remain perfect, so we test the pose channel
        # directly to prove exponential decay with deviation.
        for i in range(1, len(pose_rewards)):
            assert pose_rewards[i] < pose_rewards[i - 1], (
                f"Pose reward not monotonically decreasing: "
                f"dev={deviations[i]:.2f} → r_pose={pose_rewards[i]:.10f} >= "
                f"dev={deviations[i-1]:.2f} → r_pose={pose_rewards[i-1]:.10f}"
            )

    def test_velocity_deviation_reduces_velocity_reward(self) -> None:
        """Velocity deviation specifically reduces the velocity sub-reward."""
        n_joints = len(RL_JOINT_ORDER)
        pose = np.zeros(n_joints, dtype=np.float32)
        ref_vel = np.zeros(n_joints, dtype=np.float32)
        agent_vel = np.full(n_joints, 5.0, dtype=np.float32)
        ee = np.zeros(4, dtype=np.float32)
        com = np.zeros(2, dtype=np.float32)

        result = compute_imitation_reward(
            agent_pose=pose, agent_velocity=agent_vel,
            agent_ee=ee, agent_com=com,
            ref_pose=pose, ref_velocity=ref_vel,
            ref_ee=ee, ref_com=com,
        )

        # Velocity reward should be significantly reduced
        assert result["velocity"] < 0.5
        # Pose reward should still be perfect (no pose deviation)
        assert result["pose"] == pytest.approx(1.0, abs=1e-10)

    def test_com_deviation_reduces_com_reward(self) -> None:
        """CoM deviation specifically reduces the CoM sub-reward."""
        n_joints = len(RL_JOINT_ORDER)
        pose = np.zeros(n_joints, dtype=np.float32)
        velocity = np.zeros(n_joints, dtype=np.float32)
        ee = np.zeros(4, dtype=np.float32)
        ref_com = np.zeros(2, dtype=np.float32)
        agent_com = np.array([1.0, 1.0], dtype=np.float32)

        result = compute_imitation_reward(
            agent_pose=pose, agent_velocity=velocity,
            agent_ee=ee, agent_com=agent_com,
            ref_pose=pose, ref_velocity=velocity,
            ref_ee=ee, ref_com=ref_com,
        )

        # CoM reward should be significantly reduced
        assert result["com"] < 0.01  # exp(-10 * 2.0) ≈ 2.06e-9
        # Pose reward should still be perfect
        assert result["pose"] == pytest.approx(1.0, abs=1e-10)

    def test_reward_weights_sum_to_one(self) -> None:
        """Default reward weights sum to exactly 1.0."""
        cfg = DeepMimicRewardConfig()
        total_weight = cfg.w_pose + cfg.w_velocity + cfg.w_end_effector + cfg.w_com
        assert total_weight == pytest.approx(1.0, abs=1e-10)

    def test_all_subrewards_in_unit_interval(self) -> None:
        """All sub-rewards are in [0, 1] for arbitrary inputs."""
        rng = np.random.RandomState(42)
        n_joints = len(RL_JOINT_ORDER)

        for _ in range(100):
            result = compute_imitation_reward(
                agent_pose=rng.randn(n_joints).astype(np.float32),
                agent_velocity=rng.randn(n_joints).astype(np.float32),
                agent_ee=rng.randn(4).astype(np.float32),
                agent_com=rng.randn(2).astype(np.float32),
                ref_pose=rng.randn(n_joints).astype(np.float32),
                ref_velocity=rng.randn(n_joints).astype(np.float32),
                ref_ee=rng.randn(4).astype(np.float32),
                ref_com=rng.randn(2).astype(np.float32),
            )
            for key in ["pose", "velocity", "end_effector", "com", "total"]:
                assert 0.0 <= result[key] <= 1.0 + 1e-10, (
                    f"{key} = {result[key]} out of [0, 1]"
                )


# ═══════════════════════════════════════════════════════════════════════════
#  Test 4: Full Pipeline — Backend → Adapter → Reward Closure
# ═══════════════════════════════════════════════════════════════════════════


class TestFullPipelineClosure:
    """End-to-end test: Backend generates UMR → adapter pre-bakes → reward scores."""

    def test_backend_to_prebaked_buffers(self, tmp_path: Path) -> None:
        """UnifiedMotionBackend output is correctly pre-baked into RL buffers."""
        bridge = MicrokernelPipelineBridge(project_root=".")

        manifest = bridge.run_backend("unified_motion", {
            "state": "walk",
            "frame_count": 12,
            "fps": 12,
            "name": "test_walk",
            "output_dir": str(tmp_path / "walk"),
        })

        # Load the generated clip
        clip_path = manifest.outputs["motion_clip_json"]
        clip_data = json.loads(Path(clip_path).read_text(encoding="utf-8"))
        clip = UnifiedMotionClip.from_dict(clip_data)

        assert len(clip.frames) == 12

        # Pre-bake into RL buffers
        buffers = flatten_umr_to_rl_state(
            clip.frames,
            fps=12,
            clip_id=clip.clip_id,
            state="walk",
        )

        assert buffers.num_frames == 12
        assert buffers.pose_buf.dtype == np.float32
        assert buffers.pose_buf.flags["C_CONTIGUOUS"]

        # Verify non-trivial data (not all zeros)
        assert np.any(buffers.pose_buf != 0.0), "Pose buffer is all zeros"

    def test_backend_with_runtime_bus_changes_output(self, tmp_path: Path) -> None:
        """Injecting RuntimeDistillationBus changes the backend output.

        This proves the triple-runtime consumption is active: the RL environment
        gets different reference motions when distilled parameters change.
        """
        bridge = MicrokernelPipelineBridge(project_root=".")

        # Baseline: no runtime bus
        baseline_manifest = bridge.run_backend("unified_motion", {
            "state": "run",
            "frame_count": 8,
            "fps": 12,
            "name": "baseline",
            "output_dir": str(tmp_path / "baseline"),
        })

        # Distilled: with runtime bus (extreme blend_time)
        bus = _make_runtime_bus(tmp_path, blend_time=0.99, phase_weight=0.0)
        distilled_manifest = bridge.run_backend("unified_motion", {
            "state": "run",
            "frame_count": 8,
            "fps": 12,
            "name": "distilled",
            "output_dir": str(tmp_path / "distilled"),
            "runtime_distillation_bus": bus,
        })

        # Verify metadata reflects runtime bus injection
        assert distilled_manifest.metadata["gait_param_source"] == "runtime_distillation_bus"
        assert distilled_manifest.metadata["gait_blend_time"] == pytest.approx(0.99)

        # Load both clips and compare
        baseline_clip = UnifiedMotionClip.from_dict(
            json.loads(Path(baseline_manifest.outputs["motion_clip_json"]).read_text())
        )
        distilled_clip = UnifiedMotionClip.from_dict(
            json.loads(Path(distilled_manifest.outputs["motion_clip_json"]).read_text())
        )

        baseline_bufs = flatten_umr_to_rl_state(baseline_clip.frames, fps=12)
        distilled_bufs = flatten_umr_to_rl_state(distilled_clip.frames, fps=12)

        # Root trajectories should differ due to different blend parameters
        max_root_delta = float(np.max(np.abs(
            baseline_bufs.root_buf - distilled_bufs.root_buf
        )))
        assert max_root_delta > 1e-6, (
            "Runtime bus injection did not change root trajectory"
        )

    def test_reward_closure_with_backend_reference(self, tmp_path: Path) -> None:
        """Full closure: backend reference → pre-bake → reward computation.

        Proves that the reward function "sees" the backend-generated reference
        and responds to agent deviations.
        """
        bridge = MicrokernelPipelineBridge(project_root=".")

        manifest = bridge.run_backend("unified_motion", {
            "state": "walk",
            "frame_count": 12,
            "fps": 12,
            "name": "reward_test",
            "output_dir": str(tmp_path / "reward"),
        })

        clip = UnifiedMotionClip.from_dict(
            json.loads(Path(manifest.outputs["motion_clip_json"]).read_text())
        )
        buffers = flatten_umr_to_rl_state(clip.frames, fps=12)

        # Get reference at phase 0.5
        ref = interpolate_reference(buffers, 0.5)

        # Agent perfectly matches reference → reward ≈ 1.0
        perfect_result = compute_imitation_reward(
            agent_pose=ref["pose"],
            agent_velocity=ref["velocity"],
            agent_ee=ref["ee"],
            agent_com=ref["com"],
            ref_pose=ref["pose"],
            ref_velocity=ref["velocity"],
            ref_ee=ref["ee"],
            ref_com=ref["com"],
        )
        assert perfect_result["total"] == pytest.approx(1.0, abs=1e-10)

        # Agent has large deviation → reward significantly lower
        deviated_pose = ref["pose"] + np.full_like(ref["pose"], 1.0)
        deviated_result = compute_imitation_reward(
            agent_pose=deviated_pose,
            agent_velocity=ref["velocity"],
            agent_ee=ref["ee"],
            agent_com=ref["com"],
            ref_pose=ref["pose"],
            ref_velocity=ref["velocity"],
            ref_ee=ref["ee"],
            ref_com=ref["com"],
        )

        # Mathematical assertion: reward must be strictly less
        assert deviated_result["total"] < perfect_result["total"]
        assert deviated_result["pose"] < 0.5  # exp(-5 * 14 * 1²) ≈ exp(-70) ≈ 0

    def test_cognitive_sidecar_propagation(self, tmp_path: Path) -> None:
        """Cognitive telemetry sidecar is propagated through the adapter."""
        bridge = MicrokernelPipelineBridge(project_root=".")

        manifest = bridge.run_backend("unified_motion", {
            "state": "walk",
            "frame_count": 12,
            "fps": 12,
            "name": "sidecar_test",
            "output_dir": str(tmp_path / "sidecar"),
        })

        # Verify cognitive telemetry exists in manifest
        assert "cognitive_telemetry" in manifest.metadata
        cognitive = manifest.metadata["cognitive_telemetry"]
        assert "frame_count" in cognitive
        assert "traces" in cognitive

        # Pre-bake with sidecar
        clip = UnifiedMotionClip.from_dict(
            json.loads(Path(manifest.outputs["motion_clip_json"]).read_text())
        )
        buffers = flatten_umr_to_rl_state(
            clip.frames,
            fps=12,
            cognitive_sidecar=cognitive,
        )

        # Verify sidecar is preserved in buffers
        assert buffers.cognitive_sidecar.get("frame_count") == 12
        assert "traces" in buffers.cognitive_sidecar


# ═══════════════════════════════════════════════════════════════════════════
#  Test 5: generate_umr_reference_clips Integration
# ═══════════════════════════════════════════════════════════════════════════


class TestGenerateUMRReferenceClips:
    """Validate the high-level reference clip generation function."""

    def test_generates_multiple_states(self, tmp_path: Path) -> None:
        """Generate reference clips for multiple motion states."""
        bridge = MicrokernelPipelineBridge(project_root=".")

        clips = generate_umr_reference_clips(
            bridge,
            output_dir=str(tmp_path / "multi"),
            states=["walk", "run"],
            frame_count=8,
            fps=12,
        )

        assert "walk" in clips
        assert "run" in clips
        assert clips["walk"].num_frames == 8
        assert clips["run"].num_frames == 8

    def test_with_runtime_bus(self, tmp_path: Path) -> None:
        """Generate reference clips with injected RuntimeDistillationBus."""
        bridge = MicrokernelPipelineBridge(project_root=".")
        bus = _make_runtime_bus(tmp_path, blend_time=0.5, phase_weight=0.8)

        clips = generate_umr_reference_clips(
            bridge,
            output_dir=str(tmp_path / "bus"),
            states=["walk"],
            frame_count=8,
            fps=12,
            runtime_bus=bus,
        )

        assert "walk" in clips
        assert clips["walk"].num_frames == 8
        assert clips["walk"].pose_buf.dtype == np.float32


# ═══════════════════════════════════════════════════════════════════════════
#  Test 6: Reward Sensitivity Sweep (Quantitative Proof)
# ═══════════════════════════════════════════════════════════════════════════


class TestRewardSensitivitySweep:
    """Quantitative proof that reward function is sensitive to all channels."""

    @pytest.mark.parametrize("channel", ["pose", "velocity", "end_effector", "com"])
    def test_individual_channel_sensitivity(self, channel: str) -> None:
        """Each reward channel independently responds to its deviation.

        This proves that no channel is "dead" — every channel contributes
        to the total reward when its corresponding state deviates.
        """
        n_joints = len(RL_JOINT_ORDER)
        zero_pose = np.zeros(n_joints, dtype=np.float32)
        zero_vel = np.zeros(n_joints, dtype=np.float32)
        zero_ee = np.zeros(4, dtype=np.float32)
        zero_com = np.zeros(2, dtype=np.float32)

        # Baseline: all zeros → reward = 1.0
        baseline = compute_imitation_reward(
            agent_pose=zero_pose, agent_velocity=zero_vel,
            agent_ee=zero_ee, agent_com=zero_com,
            ref_pose=zero_pose, ref_velocity=zero_vel,
            ref_ee=zero_ee, ref_com=zero_com,
        )
        assert baseline["total"] == pytest.approx(1.0, abs=1e-10)

        # Deviate only the target channel
        if channel == "pose":
            deviated = compute_imitation_reward(
                agent_pose=np.full(n_joints, 1.0, dtype=np.float32),
                agent_velocity=zero_vel,
                agent_ee=zero_ee, agent_com=zero_com,
                ref_pose=zero_pose, ref_velocity=zero_vel,
                ref_ee=zero_ee, ref_com=zero_com,
            )
        elif channel == "velocity":
            deviated = compute_imitation_reward(
                agent_pose=zero_pose,
                agent_velocity=np.full(n_joints, 10.0, dtype=np.float32),
                agent_ee=zero_ee, agent_com=zero_com,
                ref_pose=zero_pose, ref_velocity=zero_vel,
                ref_ee=zero_ee, ref_com=zero_com,
            )
        elif channel == "end_effector":
            deviated = compute_imitation_reward(
                agent_pose=zero_pose, agent_velocity=zero_vel,
                agent_ee=np.full(4, 1.0, dtype=np.float32),
                agent_com=zero_com,
                ref_pose=zero_pose, ref_velocity=zero_vel,
                ref_ee=zero_ee, ref_com=zero_com,
            )
        else:  # com
            deviated = compute_imitation_reward(
                agent_pose=zero_pose, agent_velocity=zero_vel,
                agent_ee=zero_ee,
                agent_com=np.array([1.0, 1.0], dtype=np.float32),
                ref_pose=zero_pose, ref_velocity=zero_vel,
                ref_ee=zero_ee, ref_com=zero_com,
            )

        # The deviated channel's sub-reward should be less than 1.0
        assert deviated[channel] < 1.0, (
            f"Channel '{channel}' did not respond to deviation"
        )
        # Total reward should be less than baseline
        assert deviated["total"] < baseline["total"], (
            f"Total reward did not decrease for '{channel}' deviation"
        )
