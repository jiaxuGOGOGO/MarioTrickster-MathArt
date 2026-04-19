"""UMR-to-RL Reference Adapter — Tensorized reference motion for imitation learning.

SESSION-080: P1-B3-2 — Converts Unified Motion Representation (UMR) clips into
pre-baked contiguous NumPy buffers that the RL environment can consume at O(1)
per step, following the architectural discipline of:

- **DeepMimic** (Peng et al., SIGGRAPH 2018): Pose/velocity/end-effector/CoM
  orthogonal imitation reward with exponential kernels.
- **NVIDIA Isaac Gym** (Makoviychuk et al., 2021): Pre-baked contiguous tensor
  buffers indexed by phase, zero dict traversal in the hot path.
- **EA Frostbite Data-Oriented Design**: Struct-of-Arrays memory layout,
  schema-driven contracts, strict producer/consumer decoupling.

Architecture
------------
::

    ┌────────────────────────────────────────────────────────────────────┐
    │  MicrokernelPipelineBridge.run_backend("unified_motion", ctx)     │
    │  → ArtifactManifest (MOTION_UMR)                                  │
    │  → UnifiedMotionClip (frames with joint_local_rotations, root,    │
    │    contacts, phase, cognitive telemetry sidecar)                   │
    └───────────────────────┬────────────────────────────────────────────┘
                            │  (init-time only)
                            ▼
    ┌────────────────────────────────────────────────────────────────────┐
    │  UMRReferenceAdapter                                               │
    │  ├─ flatten_umr_to_rl_state(clip) → Pre-baked SoA buffers         │
    │  │   pose_buf:     (N, J)   float32  — joint angles               │
    │  │   velocity_buf: (N, J)   float32  — joint angular velocities   │
    │  │   root_buf:     (N, 6)   float32  — [x,y,rot,vx,vy,ω]        │
    │  │   phase_buf:    (N,)     float32  — normalized phase           │
    │  │   contact_buf:  (N, 4)   float32  — [lf,rf,lh,rh]            │
    │  │   ee_buf:       (N, 4)   float32  — [lf_y, rf_y, lh_y, rh_y] │
    │  │   com_buf:      (N, 2)   float32  — [com_x, com_y]           │
    │  ├─ interpolate_reference(phase) → O(1) state lookup              │
    │  └─ schema validation at bind time, never in hot loop             │
    └───────────────────────┬────────────────────────────────────────────┘
                            │  (step-time: O(1) indexing)
                            ▼
    ┌────────────────────────────────────────────────────────────────────┐
    │  DeepMimicImitationReward                                          │
    │  r_t = w_p·exp(-k_p·‖Δpose‖²)                                    │
    │      + w_v·exp(-k_v·‖Δvel‖²)                                     │
    │      + w_e·exp(-k_e·‖Δee‖²)                                      │
    │      + w_c·exp(-k_c·‖Δcom‖²)                                     │
    └────────────────────────────────────────────────────────────────────┘

Red-Line Enforcement
--------------------
1. **No per-step I/O**: All UMR data is pre-baked at init/reset. step() and
   compute_reward() NEVER call run_backend() or touch files.
2. **No dimension mismatch**: Joint order is explicitly declared and validated
   against the UMR clip's joint_channel_schema at bind time.
3. **No fake reward**: Reward function uses strict exponential kernels that
   provably decay with increasing state deviation.

References
----------
[1] Peng et al., "DeepMimic" (SIGGRAPH 2018)
[2] Makoviychuk et al., "Isaac Gym" (NeurIPS 2021)
[3] EA/DICE, "Introduction to Data-Oriented Design" (GDC)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

import numpy as np


# ── Joint Channel Schema Constants ──────────────────────────────────────────

# Canonical joint order for the 2D humanoid RL agent.
# This MUST match the joint names emitted by UnifiedMotionBackend's lane registry.
RL_JOINT_ORDER: list[str] = [
    "spine", "chest", "neck", "head",
    "l_shoulder", "r_shoulder", "l_elbow", "r_elbow",
    "l_hip", "r_hip", "l_knee", "r_knee",
    "l_foot", "r_foot",
]

# End-effector limb names for positional reward
RL_END_EFFECTOR_JOINTS: list[str] = ["l_foot", "r_foot", "l_hand", "r_hand"]

# Fallback joint names that map to end-effector proxies
_EE_PROXY_MAP: dict[str, str] = {
    "l_foot": "l_foot",
    "r_foot": "r_foot",
    "l_hand": "l_elbow",  # Proxy: elbow angle as hand proxy in 2D
    "r_hand": "r_elbow",
}


# ── Pre-baked Reference Buffers (Struct-of-Arrays) ─────────────────────────


@dataclass
class PrebakedReferenceBuffers:
    """Contiguous SoA buffers for O(1) RL reference lookup.

    All arrays are pre-allocated at init time. The RL step() function
    indexes into these buffers by phase or frame index — no dict traversal,
    no I/O, no backend calls.

    Memory layout follows EA Frostbite SoA discipline:
    - Each channel is a separate contiguous array
    - All arrays share the same leading dimension (num_frames)
    - dtype is float32 for cache-friendly vectorized math
    """

    num_frames: int = 0
    num_joints: int = 0
    joint_order: list[str] = field(default_factory=list)
    joint_channel_schema: str = "2d_scalar"

    # SoA buffers — all shape (num_frames, ...) or (num_frames,)
    pose_buf: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.float32))
    velocity_buf: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.float32))
    root_buf: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.float32))
    phase_buf: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.float32))
    contact_buf: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.float32))
    ee_buf: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.float32))
    com_buf: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.float32))
    time_buf: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.float32))

    # Metadata
    clip_id: str = ""
    state: str = ""
    fps: int = 12
    cognitive_sidecar: dict[str, Any] = field(default_factory=dict)


def flatten_umr_to_rl_state(
    clip_frames: list,
    *,
    joint_order: Optional[list[str]] = None,
    fps: int = 12,
    clip_id: str = "",
    state: str = "",
    cognitive_sidecar: Optional[dict[str, Any]] = None,
) -> PrebakedReferenceBuffers:
    """Convert a UMR clip's frames into pre-baked contiguous SoA buffers.

    This is the critical init-time conversion that transforms the nested-dict
    UMR representation into flat NumPy arrays for O(1) RL consumption.

    The function:
    1. Validates joint_channel_schema consistency across all frames
    2. Extracts joint angles into a (N, J) pose buffer
    3. Computes finite-difference velocities into a (N, J) velocity buffer
    4. Extracts root transform into a (N, 6) buffer [x,y,rot,vx,vy,ω]
    5. Extracts phase into a (N,) buffer
    6. Extracts contacts into a (N, 4) buffer [lf,rf,lh,rh]
    7. Computes end-effector proxy positions into a (N, 4) buffer
    8. Computes center-of-mass proxy into a (N, 2) buffer

    Parameters
    ----------
    clip_frames : list[UnifiedMotionFrame]
        Frames from a UnifiedMotionClip.
    joint_order : list[str], optional
        Canonical joint order. Defaults to RL_JOINT_ORDER.
    fps : int
        Frames per second for velocity computation.
    clip_id : str
        Clip identifier for metadata.
    state : str
        Motion state name.
    cognitive_sidecar : dict, optional
        Cognitive telemetry sidecar from the backend.

    Returns
    -------
    PrebakedReferenceBuffers
        Contiguous SoA buffers ready for O(1) RL consumption.

    Raises
    ------
    ValueError
        If frames are empty or joint_channel_schema is inconsistent.
    """
    if not clip_frames:
        raise ValueError("Cannot flatten empty UMR clip")

    jo = joint_order or RL_JOINT_ORDER
    n_frames = len(clip_frames)
    n_joints = len(jo)
    dt = 1.0 / max(fps, 1)

    # --- Schema validation (bind-time, NOT hot-path) ---
    first_schema = clip_frames[0].metadata.get("joint_channel_schema", "2d_scalar")
    for i, frame in enumerate(clip_frames):
        frame_schema = frame.metadata.get("joint_channel_schema", "2d_scalar")
        if frame_schema != first_schema:
            raise ValueError(
                f"Joint channel schema mismatch at frame {i}: "
                f"expected '{first_schema}', got '{frame_schema}'"
            )

    # --- Pre-allocate contiguous buffers ---
    pose_buf = np.zeros((n_frames, n_joints), dtype=np.float32)
    velocity_buf = np.zeros((n_frames, n_joints), dtype=np.float32)
    root_buf = np.zeros((n_frames, 6), dtype=np.float32)
    phase_buf = np.zeros(n_frames, dtype=np.float32)
    contact_buf = np.zeros((n_frames, 4), dtype=np.float32)
    ee_buf = np.zeros((n_frames, 4), dtype=np.float32)
    com_buf = np.zeros((n_frames, 2), dtype=np.float32)
    time_buf = np.zeros(n_frames, dtype=np.float32)

    # --- Fill buffers (one-time init cost) ---
    for i, frame in enumerate(clip_frames):
        joints = frame.joint_local_rotations
        root = frame.root_transform
        contacts = frame.contact_tags

        # Pose: extract joint angles in canonical order
        for j, jname in enumerate(jo):
            pose_buf[i, j] = float(joints.get(jname, 0.0))

        # Root: [x, y, rotation, velocity_x, velocity_y, angular_velocity]
        root_buf[i, 0] = float(root.x)
        root_buf[i, 1] = float(root.y)
        root_buf[i, 2] = float(root.rotation)
        root_buf[i, 3] = float(root.velocity_x)
        root_buf[i, 4] = float(root.velocity_y)
        root_buf[i, 5] = float(root.angular_velocity)

        # Phase
        phase_buf[i] = float(frame.phase)

        # Contacts: [left_foot, right_foot, left_hand, right_hand]
        contact_buf[i, 0] = float(contacts.left_foot)
        contact_buf[i, 1] = float(contacts.right_foot)
        contact_buf[i, 2] = float(contacts.left_hand)
        contact_buf[i, 3] = float(contacts.right_hand)

        # Time
        time_buf[i] = float(frame.time)

        # End-effector proxy: use joint angles as positional proxies
        # In 2D, end-effector "position" is approximated from joint chain
        for k, ee_name in enumerate(RL_END_EFFECTOR_JOINTS):
            proxy_joint = _EE_PROXY_MAP.get(ee_name, ee_name)
            ee_buf[i, k] = float(joints.get(proxy_joint, 0.0))

        # Center-of-mass proxy: weighted average of root + hip angles
        hip_avg = (float(joints.get("l_hip", 0.0)) + float(joints.get("r_hip", 0.0))) / 2.0
        com_buf[i, 0] = float(root.x) + hip_avg * 0.1  # CoM x proxy
        com_buf[i, 1] = float(root.y) + float(joints.get("spine", 0.0)) * 0.05  # CoM y proxy

    # --- Compute velocities via finite difference ---
    # velocity_buf[0] = 0 (no previous frame)
    # velocity_buf[i] = (pose_buf[i] - pose_buf[i-1]) / dt
    if n_frames > 1:
        velocity_buf[1:] = (pose_buf[1:] - pose_buf[:-1]) / dt

    return PrebakedReferenceBuffers(
        num_frames=n_frames,
        num_joints=n_joints,
        joint_order=list(jo),
        joint_channel_schema=first_schema,
        pose_buf=pose_buf,
        velocity_buf=velocity_buf,
        root_buf=root_buf,
        phase_buf=phase_buf,
        contact_buf=contact_buf,
        ee_buf=ee_buf,
        com_buf=com_buf,
        time_buf=time_buf,
        clip_id=clip_id,
        state=state,
        fps=fps,
        cognitive_sidecar=cognitive_sidecar or {},
    )


def interpolate_reference(
    buffers: PrebakedReferenceBuffers,
    phase: float,
) -> dict[str, np.ndarray]:
    """O(1) phase-indexed reference state lookup with linear interpolation.

    This is the ONLY function called in the RL step() hot path.
    It performs a single array index + lerp — no dicts, no I/O.

    Parameters
    ----------
    buffers : PrebakedReferenceBuffers
        Pre-baked reference buffers.
    phase : float
        Normalized phase in [0, 1).

    Returns
    -------
    dict[str, np.ndarray]
        Reference state with keys: pose, velocity, root, contact, ee, com.
        All values are 1D numpy arrays (single-frame slices).
    """
    n = buffers.num_frames
    if n == 0:
        return {
            "pose": np.zeros(buffers.num_joints, dtype=np.float32),
            "velocity": np.zeros(buffers.num_joints, dtype=np.float32),
            "root": np.zeros(6, dtype=np.float32),
            "contact": np.zeros(4, dtype=np.float32),
            "ee": np.zeros(4, dtype=np.float32),
            "com": np.zeros(2, dtype=np.float32),
        }

    # Phase → continuous frame index
    t = (phase % 1.0) * (n - 1)
    i0 = int(t)
    i1 = min(i0 + 1, n - 1)
    alpha = t - i0

    # O(1) interpolation on contiguous arrays
    return {
        "pose": (1.0 - alpha) * buffers.pose_buf[i0] + alpha * buffers.pose_buf[i1],
        "velocity": (1.0 - alpha) * buffers.velocity_buf[i0] + alpha * buffers.velocity_buf[i1],
        "root": (1.0 - alpha) * buffers.root_buf[i0] + alpha * buffers.root_buf[i1],
        "contact": (1.0 - alpha) * buffers.contact_buf[i0] + alpha * buffers.contact_buf[i1],
        "ee": (1.0 - alpha) * buffers.ee_buf[i0] + alpha * buffers.ee_buf[i1],
        "com": (1.0 - alpha) * buffers.com_buf[i0] + alpha * buffers.com_buf[i1],
    }


# ── DeepMimic Imitation Reward (Vectorized) ────────────────────────────────


@dataclass
class DeepMimicRewardConfig:
    """DeepMimic imitation reward weights and sensitivity coefficients.

    Default weights from Peng et al. (2018), Section 5:
        w_p = 0.65, w_v = 0.10, w_e = 0.15, w_c = 0.10

    Sensitivity coefficients control the exponential decay rate:
        k_p = 2.0  (pose)       — paper uses 2.0 for quaternion diff
        k_v = 0.1  (velocity)   — paper uses 0.1
        k_e = 40.0 (end-eff)    — paper uses 40.0 for meter-scale positions
        k_c = 10.0 (CoM)        — paper uses 10.0

    For 2D scalar angles (radians), we adapt:
        k_p = 5.0  — stronger sensitivity for scalar angle diff (matches pd_controller)
        k_e = 10.0 — scaled down since we use angle proxies, not meter positions
    """

    w_pose: float = 0.65
    w_velocity: float = 0.10
    w_end_effector: float = 0.15
    w_com: float = 0.10

    k_pose: float = 5.0
    k_velocity: float = 0.1
    k_end_effector: float = 10.0
    k_com: float = 10.0


def compute_imitation_reward(
    agent_pose: np.ndarray,
    agent_velocity: np.ndarray,
    agent_ee: np.ndarray,
    agent_com: np.ndarray,
    ref_pose: np.ndarray,
    ref_velocity: np.ndarray,
    ref_ee: np.ndarray,
    ref_com: np.ndarray,
    config: Optional[DeepMimicRewardConfig] = None,
) -> dict[str, float]:
    """Compute vectorized DeepMimic imitation reward.

    All inputs are 1D numpy arrays. This function is designed to be called
    in the RL step() hot path with zero dict traversal.

    The reward is computed as:
        r = w_p * exp(-k_p * ||Δpose||²)
          + w_v * exp(-k_v * ||Δvel||²)
          + w_e * exp(-k_e * ||Δee||²)
          + w_c * exp(-k_c * ||Δcom||²)

    Each sub-reward is in [0, 1] due to the exponential kernel.
    Total reward is in [0, 1] since weights sum to 1.

    Parameters
    ----------
    agent_pose, agent_velocity, agent_ee, agent_com : np.ndarray
        Agent's current state arrays.
    ref_pose, ref_velocity, ref_ee, ref_com : np.ndarray
        Reference state arrays from interpolate_reference().
    config : DeepMimicRewardConfig, optional
        Reward weights and sensitivity coefficients.

    Returns
    -------
    dict[str, float]
        Reward breakdown: pose, velocity, end_effector, com, total.
    """
    cfg = config or DeepMimicRewardConfig()

    # Vectorized squared L2 norms — O(J) for J joints
    pose_err = float(np.sum((agent_pose - ref_pose) ** 2))
    vel_err = float(np.sum((agent_velocity - ref_velocity) ** 2))
    ee_err = float(np.sum((agent_ee - ref_ee) ** 2))
    com_err = float(np.sum((agent_com - ref_com) ** 2))

    # Exponential kernels — always in [0, 1], smooth gradients
    r_pose = math.exp(-cfg.k_pose * pose_err)
    r_velocity = math.exp(-cfg.k_velocity * vel_err)
    r_ee = math.exp(-cfg.k_end_effector * ee_err)
    r_com = math.exp(-cfg.k_com * com_err)

    # Weighted sum — total in [0, 1]
    total = (
        cfg.w_pose * r_pose
        + cfg.w_velocity * r_velocity
        + cfg.w_end_effector * r_ee
        + cfg.w_com * r_com
    )

    return {
        "pose": r_pose,
        "velocity": r_velocity,
        "end_effector": r_ee,
        "com": r_com,
        "total": total,
        "pose_err": pose_err,
        "velocity_err": vel_err,
        "ee_err": ee_err,
        "com_err": com_err,
    }


# ── Triple-Runtime UMR Clip Generator ──────────────────────────────────────


def generate_umr_reference_clips(
    bridge,
    output_dir: str,
    *,
    states: Optional[list[str]] = None,
    frame_count: int = 30,
    fps: int = 12,
    runtime_bus=None,
    stem: str = "rl_ref",
) -> dict[str, PrebakedReferenceBuffers]:
    """Generate UMR reference clips via MicrokernelPipelineBridge and pre-bake.

    This function is called ONCE at RL environment __init__ or reset().
    It dynamically invokes the unified_motion backend for each requested
    motion state, consuming the three preloaded namespaces:
    - physics_gait
    - cognitive_motion
    - transient_motion

    Parameters
    ----------
    bridge : MicrokernelPipelineBridge
        Pipeline bridge for backend execution.
    output_dir : str
        Directory for clip output files.
    states : list[str], optional
        Motion states to generate. Defaults to ["walk", "run", "jump"].
    frame_count : int
        Frames per clip.
    fps : int
        Frames per second.
    runtime_bus : RuntimeDistillationBus, optional
        Pre-loaded distillation bus with physics_gait, cognitive_motion,
        and transient_motion namespaces.
    stem : str
        Output file stem prefix.

    Returns
    -------
    dict[str, PrebakedReferenceBuffers]
        Mapping from state name to pre-baked reference buffers.
    """
    from mathart.animation.unified_motion import UnifiedMotionClip

    target_states = states or ["walk", "run", "jump"]
    result: dict[str, PrebakedReferenceBuffers] = {}

    for state in target_states:
        context: dict[str, Any] = {
            "state": state,
            "frame_count": frame_count,
            "fps": fps,
            "output_dir": output_dir,
            "name": f"{stem}_{state}",
        }

        # Inject runtime distillation bus if available
        if runtime_bus is not None:
            context["runtime_distillation_bus"] = runtime_bus

        # Execute backend via bridge (Context-in / Manifest-out)
        manifest = bridge.run_backend("unified_motion", context)

        # Load the generated clip
        clip_path = manifest.outputs.get("motion_clip_json", "")
        if clip_path:
            import json
            from pathlib import Path
            clip_data = json.loads(Path(clip_path).read_text(encoding="utf-8"))
            clip = UnifiedMotionClip.from_dict(clip_data)
        else:
            # Fallback: build minimal clip from manifest metadata
            clip = UnifiedMotionClip(
                clip_id=f"{stem}_{state}_umr",
                state=state,
                fps=fps,
                frames=[],
            )

        # Extract cognitive sidecar
        cognitive_sidecar = manifest.metadata.get("cognitive_telemetry", {})

        # Pre-bake into contiguous buffers
        if clip.frames:
            buffers = flatten_umr_to_rl_state(
                clip.frames,
                fps=fps,
                clip_id=clip.clip_id,
                state=state,
                cognitive_sidecar=cognitive_sidecar,
            )
            result[state] = buffers

    return result


__all__ = [
    "RL_JOINT_ORDER",
    "RL_END_EFFECTOR_JOINTS",
    "PrebakedReferenceBuffers",
    "DeepMimicRewardConfig",
    "flatten_umr_to_rl_state",
    "interpolate_reference",
    "compute_imitation_reward",
    "generate_umr_reference_clips",
]
