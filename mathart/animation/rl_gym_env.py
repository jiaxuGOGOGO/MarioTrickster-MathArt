"""Gymnasium RL environment backed by pre-baked UMR reference buffers.

SESSION-083 / P1-B4-1
---------------------
This module lands a **Gymnasium-compliant** imitation-learning environment on
 top of the SESSION-080 UMR reference adapter.  The design follows three hard
rules:

1. **No hot-path I/O** — UMR clips are converted into contiguous NumPy
   buffers once during environment construction. ``step()`` only performs
   O(1) reference interpolation plus vector math.
2. **Reference State Initialization (RSI)** — ``reset()`` samples a random
   valid phase from the pre-baked reference trajectory and aligns the agent
   state to that reference frame instead of always starting at phase 0.
3. **Early Termination discipline** — task-defined failure states set
   ``terminated=True`` while horizon exhaustion sets ``truncated=True``.

External reference grounding
----------------------------
- Gymnasium Env API: reset returns ``(obs, info)`` and step returns
  ``(obs, reward, terminated, truncated, info)``.
- DeepMimic (Peng et al., 2018): RSI and early termination materially improve
  imitation-learning stability.
- Farama Step API guidance: failure states should be reported via
  ``terminated`` while exogenous rollout caps should be reported via
  ``truncated``.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import numpy as np

try:
    import gymnasium as gym
    from gymnasium import spaces
except ImportError:  # pragma: no cover — lightweight sandbox mode
    gym = None  # type: ignore[assignment]
    spaces = None  # type: ignore[assignment]

# SESSION-149: When gymnasium is absent (CPU-only sandbox / minimal install),
# the class statement ``class LocomotionRLEnv(gym.Env[np.ndarray, np.ndarray])``
# below evaluates the subscript at *class definition time* and crashes the
# module import with ``'NoneType' object has no attribute 'Env'`` — which in
# turn poisons every other consumer of ``mathart.animation`` (notably the
# pseudo3d_shell backend that re-imports ``mathart.animation.dqs_engine``).
# Falling back to a plain ``object`` base class preserves import integrity
# everywhere; instantiating ``LocomotionRLEnv`` without gymnasium will still
# fail loudly inside ``__init__`` because ``spaces.Box`` is None, so the RL
# happy-path is unaffected.  This is consistent with the SESSION-146
# lazy-import discipline.
if gym is not None:
    _RL_ENV_BASE = gym.Env[np.ndarray, np.ndarray]  # type: ignore[misc]
else:
    _RL_ENV_BASE = object  # type: ignore[assignment]

from mathart.animation.umr_rl_adapter import (
    DeepMimicRewardConfig,
    PrebakedReferenceBuffers,
    compute_imitation_reward,
    flatten_umr_to_rl_state,
    generate_umr_reference_clips,
    interpolate_reference,
)
from mathart.animation.unified_motion import UnifiedMotionClip
from mathart.core.artifact_schema import ArtifactManifest
from mathart.core.pipeline_bridge import MicrokernelPipelineBridge


@dataclass(slots=True)
class LocomotionRLEnvConfig:
    """Configuration for the Gymnasium locomotion imitation environment."""

    motion_state: str = "walk"
    frame_count: int = 30
    fps: int = 12
    max_episode_steps: int = 96
    action_scale: float = 0.12
    control_gain: float = 0.55
    observation_clip: float = 20.0
    action_clip: float = 1.0
    early_termination_reward_threshold: float = 0.08
    early_termination_pose_l2_threshold: float = 0.35
    early_termination_com_l2_threshold: float = 0.60
    early_termination_root_y_threshold: float = 1.25
    reward_config: DeepMimicRewardConfig = field(default_factory=DeepMimicRewardConfig)

    @property
    def phase_advance(self) -> float:
        """Normalized phase increment per environment step."""
        return 1.0 / max(self.frame_count, 1)


class LocomotionRLEnv(_RL_ENV_BASE):  # type: ignore[misc]
    """Gymnasium-compliant locomotion environment over pre-baked UMR buffers.

    The environment uses a lightweight control update rather than a full
    heavyweight external simulator so that CI and fallback training can execute
    a complete rollout even without optional RL frameworks.  Crucially, the
    reference data path remains the same one required by the project: UMR clips
    are pre-baked once into contiguous struct-of-arrays buffers and the hot path
    never re-enters backend execution or filesystem access.
    """

    metadata = {"render_modes": [], "render_fps": 12}

    def __init__(
        self,
        config: Optional[LocomotionRLEnvConfig] = None,
        *,
        output_dir: str | Path = "artifacts/rl_training",
        project_root: str | Path | None = None,
        session_id: str = "SESSION-083",
        reference_manifest: ArtifactManifest | None = None,
        runtime_distillation_bus: Any | None = None,
    ) -> None:
        self.config = config or LocomotionRLEnvConfig()
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.project_root = Path(project_root) if project_root is not None else Path(__file__).resolve().parents[2]
        self.session_id = session_id
        self.runtime_distillation_bus = runtime_distillation_bus
        self.render_mode: str | None = None

        self.reference_buffers = self._load_reference_buffers(reference_manifest)
        self._joint_order = list(self.reference_buffers.joint_order)
        self._joint_index = {
            name: idx for idx, name in enumerate(self._joint_order)
        }

        self.action_space = spaces.Box(
            low=-self.config.action_clip,
            high=self.config.action_clip,
            shape=(self.reference_buffers.num_joints,),
            dtype=np.float32,
        )
        self.observation_space = spaces.Box(
            low=-self.config.observation_clip,
            high=self.config.observation_clip,
            shape=self._observation_dim(),
            dtype=np.float32,
        )

        self._agent_pose = np.zeros(self.reference_buffers.num_joints, dtype=np.float32)
        self._agent_velocity = np.zeros(self.reference_buffers.num_joints, dtype=np.float32)
        self._agent_root = np.zeros(6, dtype=np.float32)
        self._agent_contact = np.zeros(4, dtype=np.float32)
        self._agent_ee = np.zeros(4, dtype=np.float32)
        self._agent_com = np.zeros(2, dtype=np.float32)

        self._phase: float = 0.0
        self._step_count: int = 0
        self._episode_reward: float = 0.0
        self._last_reset_phase: float | None = None
        self._needs_reset: bool = True

    # ------------------------------------------------------------------
    # Gymnasium API
    # ------------------------------------------------------------------

    def reset(
        self,
        *,
        seed: int | None = None,
        options: dict[str, Any] | None = None,
    ) -> tuple[np.ndarray, dict[str, Any]]:
        """Reset using Reference State Initialization (RSI).

        ``super().reset(seed=seed)`` is called first to comply with Gymnasium's
        seeding contract.  The initial phase is then sampled from the reference
        motion unless the caller explicitly supplies ``options['phase']``.
        """
        super().reset(seed=seed)
        opts = options or {}

        if "phase" in opts:
            phase = float(opts["phase"])
        else:
            phase = self._sample_rsi_phase()
        phase = float(np.clip(phase, 0.0, np.nextafter(1.0, 0.0)))

        ref_state = interpolate_reference(self.reference_buffers, phase)
        self._agent_pose = ref_state["pose"].astype(np.float32, copy=True)
        self._agent_velocity = ref_state["velocity"].astype(np.float32, copy=True)
        self._agent_root = ref_state["root"].astype(np.float32, copy=True)
        self._agent_contact = ref_state["contact"].astype(np.float32, copy=True)
        self._agent_ee = ref_state["ee"].astype(np.float32, copy=True)
        self._agent_com = ref_state["com"].astype(np.float32, copy=True)

        self._phase = phase
        self._step_count = 0
        self._episode_reward = 0.0
        self._last_reset_phase = phase
        self._needs_reset = False

        obs = self._build_observation(ref_state)
        info = {
            "reference_phase": phase,
            "reference_frame_index": self._phase_to_frame_index(phase),
            "motion_state": self.config.motion_state,
            "reference_clip_id": self.reference_buffers.clip_id,
            "rsi_enabled": True,
        }
        return obs, info

    def step(
        self, action: np.ndarray,
    ) -> tuple[np.ndarray, float, bool, bool, dict[str, Any]]:
        """Advance one control step.

        The dynamics here are intentionally lightweight and deterministic:
        actions steer the current pose toward a target pose offset around the
        next reference pose, while reward and failure checks are computed against
        the same reference frame using the SESSION-080 adapter utilities.
        """
        if self._needs_reset:
            raise RuntimeError("LocomotionRLEnv.step() called after episode end; call reset() first")

        action_arr = np.asarray(action, dtype=np.float32).reshape(-1)
        if action_arr.shape != self.action_space.shape:
            raise ValueError(
                f"Expected action shape {self.action_space.shape}, got {action_arr.shape}"
            )
        action_arr = np.clip(action_arr, self.action_space.low, self.action_space.high)

        next_phase = (self._phase + self.config.phase_advance) % 1.0
        ref_state = interpolate_reference(self.reference_buffers, next_phase)

        target_pose = ref_state["pose"] + self.config.action_scale * action_arr
        new_pose = self._agent_pose + self.config.control_gain * (target_pose - self._agent_pose)
        new_velocity = (new_pose - self._agent_pose) * float(self.config.fps)

        new_root = ref_state["root"].astype(np.float32, copy=True)
        root_shift_x = 0.025 * float(np.mean(action_arr))
        new_root[0] += root_shift_x
        new_root[3] += root_shift_x * float(self.config.fps)

        new_contact = ref_state["contact"].astype(np.float32, copy=True)
        new_ee = self._project_end_effectors(new_pose)
        new_com = self._project_center_of_mass(new_pose, new_root)

        reward_breakdown = compute_imitation_reward(
            agent_pose=new_pose,
            agent_velocity=new_velocity,
            agent_ee=new_ee,
            agent_com=new_com,
            ref_pose=ref_state["pose"],
            ref_velocity=ref_state["velocity"],
            ref_ee=ref_state["ee"],
            ref_com=ref_state["com"],
            config=self.config.reward_config,
        )
        reward = float(reward_breakdown["total"])

        pose_l2 = float(np.linalg.norm(new_pose - ref_state["pose"], ord=2))
        com_l2 = float(np.linalg.norm(new_com - ref_state["com"], ord=2))
        terminated, terminated_reason = self._check_termination(
            reward=reward,
            pose_l2=pose_l2,
            com_l2=com_l2,
            root_state=new_root,
        )

        self._step_count += 1
        truncated = self._step_count >= self.config.max_episode_steps and not terminated
        truncated_reason = "max_episode_steps" if truncated else ""

        self._agent_pose = new_pose.astype(np.float32, copy=False)
        self._agent_velocity = new_velocity.astype(np.float32, copy=False)
        self._agent_root = new_root
        self._agent_contact = new_contact
        self._agent_ee = new_ee.astype(np.float32, copy=False)
        self._agent_com = new_com.astype(np.float32, copy=False)
        self._phase = next_phase
        self._episode_reward += reward
        self._needs_reset = bool(terminated or truncated)

        obs = self._build_observation(ref_state)
        info = {
            "phase": float(self._phase),
            "reference_phase": float(next_phase),
            "step_count": int(self._step_count),
            "episode_reward": float(self._episode_reward),
            "terminated_reason": terminated_reason,
            "truncated_reason": truncated_reason,
            "pose_l2": pose_l2,
            "com_l2": com_l2,
            **reward_breakdown,
        }
        return obs, reward, terminated, truncated, info

    def render(self) -> None:
        """No-op render hook for API completeness."""
        return None

    def close(self) -> None:
        """No-op close hook for API completeness."""
        return None

    # ------------------------------------------------------------------
    # Convenience properties
    # ------------------------------------------------------------------

    @property
    def obs_dim(self) -> int:
        return int(self.observation_space.shape[0])

    @property
    def act_dim(self) -> int:
        return int(self.action_space.shape[0])

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _observation_dim(self) -> tuple[int, ...]:
        joints = self.reference_buffers.num_joints
        dim = (
            joints + joints + 6 + 4 + 4 + 2 + joints + joints + 4 + 4 + 2 + 1
        )
        return (dim,)

    def _load_reference_buffers(
        self,
        reference_manifest: ArtifactManifest | None,
    ) -> PrebakedReferenceBuffers:
        if reference_manifest is not None:
            maybe = self._buffers_from_manifest(reference_manifest)
            if maybe is not None:
                return maybe
        return self._buffers_via_bridge()

    def _buffers_via_bridge(self) -> PrebakedReferenceBuffers:
        bridge = MicrokernelPipelineBridge(
            project_root=str(self.project_root),
            session_id=self.session_id,
        )
        clip_map = generate_umr_reference_clips(
            bridge,
            output_dir=str(self.output_dir),
            states=[self.config.motion_state],
            frame_count=self.config.frame_count,
            fps=self.config.fps,
            runtime_bus=self.runtime_distillation_bus,
            stem="session083_rl_ref",
        )
        buffers = clip_map.get(self.config.motion_state)
        if buffers is None:
            raise RuntimeError(
                f"Failed to pre-bake reference buffers for state {self.config.motion_state!r}"
            )
        return buffers

    def _buffers_from_manifest(
        self,
        manifest: ArtifactManifest,
    ) -> PrebakedReferenceBuffers | None:
        clip_path = manifest.outputs.get("motion_clip_json", "")
        if not clip_path:
            return None
        path = Path(clip_path)
        if not path.exists():
            return None

        clip_data = json.loads(path.read_text(encoding="utf-8"))
        clip = UnifiedMotionClip.from_dict(clip_data)
        if not clip.frames:
            return None
        return flatten_umr_to_rl_state(
            clip.frames,
            fps=int(getattr(clip, "fps", self.config.fps) or self.config.fps),
            clip_id=getattr(clip, "clip_id", f"{self.config.motion_state}_umr"),
            state=self.config.motion_state,
            cognitive_sidecar=manifest.metadata.get("cognitive_telemetry", {}),
        )

    def _sample_rsi_phase(self) -> float:
        phase = float(self.np_random.uniform(0.0, 1.0))
        if (
            self._last_reset_phase is not None
            and self.reference_buffers.num_frames > 1
            and math.isclose(phase, self._last_reset_phase, rel_tol=0.0, abs_tol=1e-9)
        ):
            phase = (phase + 1.0 / float(self.reference_buffers.num_frames)) % 1.0
        return phase

    def _phase_to_frame_index(self, phase: float) -> int:
        return min(
            int((phase % 1.0) * max(self.reference_buffers.num_frames - 1, 0)),
            max(self.reference_buffers.num_frames - 1, 0),
        )

    def _build_observation(self, ref_state: dict[str, np.ndarray]) -> np.ndarray:
        obs = np.concatenate(
            [
                self._agent_pose,
                self._agent_velocity,
                self._agent_root,
                self._agent_contact,
                self._agent_ee,
                self._agent_com,
                ref_state["pose"].astype(np.float32, copy=False),
                ref_state["velocity"].astype(np.float32, copy=False),
                ref_state["contact"].astype(np.float32, copy=False),
                ref_state["ee"].astype(np.float32, copy=False),
                ref_state["com"].astype(np.float32, copy=False),
                np.array([self._phase], dtype=np.float32),
            ],
            dtype=np.float32,
        )
        return np.clip(obs, self.observation_space.low, self.observation_space.high)

    def _project_end_effectors(self, pose: np.ndarray) -> np.ndarray:
        def _joint(name: str, fallback: float = 0.0) -> float:
            idx = self._joint_index.get(name)
            if idx is None:
                return fallback
            return float(pose[idx])

        return np.array(
            [
                _joint("l_foot"),
                _joint("r_foot"),
                _joint("l_elbow"),
                _joint("r_elbow"),
            ],
            dtype=np.float32,
        )

    def _project_center_of_mass(
        self,
        pose: np.ndarray,
        root_state: np.ndarray,
    ) -> np.ndarray:
        def _joint(name: str) -> float:
            idx = self._joint_index.get(name)
            if idx is None:
                return 0.0
            return float(pose[idx])

        hip_avg = 0.5 * (_joint("l_hip") + _joint("r_hip"))
        spine = _joint("spine")
        return np.array(
            [
                float(root_state[0]) + 0.1 * hip_avg,
                float(root_state[1]) + 0.05 * spine,
            ],
            dtype=np.float32,
        )

    def _check_termination(
        self,
        *,
        reward: float,
        pose_l2: float,
        com_l2: float,
        root_state: np.ndarray,
    ) -> tuple[bool, str]:
        if reward < self.config.early_termination_reward_threshold:
            return True, "reward_collapse"
        if pose_l2 > self.config.early_termination_pose_l2_threshold:
            return True, "pose_diverged"
        if com_l2 > self.config.early_termination_com_l2_threshold:
            return True, "com_diverged"
        if abs(float(root_state[1])) > self.config.early_termination_root_y_threshold:
            return True, "root_height_diverged"
        return False, ""


RLEnvConfig = LocomotionRLEnvConfig


__all__ = [
    "LocomotionRLEnvConfig",
    "LocomotionRLEnv",
    "RLEnvConfig",
]
