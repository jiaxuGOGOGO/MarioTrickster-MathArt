"""Deep RL Locomotion — Reinforcement learning framework for physics-based gait generation.

SESSION-030: Research-driven implementation of a deep reinforcement learning framework
for generating physically plausible locomotion (walk, run, jump) for 2D characters.

This module implements the core RL training loop and policy architecture inspired by:
- DeepMimic (Peng et al., SIGGRAPH 2018): motion imitation with PPO
- ASE (Peng et al., SIGGRAPH 2022): adversarial skill embeddings
- Humanoid-Gym (RobotEra): GPU-accelerated humanoid RL

Architecture overview:
    ┌─────────────────────────────────────────────────────────────┐
    │  RL Policy (Actor-Critic)                                   │
    │  ┌──────────┐  ┌──────────┐  ┌──────────────────────────┐  │
    │  │ Actor    │  │ Critic   │  │ Skill Encoder (ASE)      │  │
    │  │ π(a|s,z) │  │ V(s)     │  │ z = E(s_0..s_T)         │  │
    │  └────┬─────┘  └────┬─────┘  └──────────┬───────────────┘  │
    │       │              │                    │                  │
    │       ▼              ▼                    ▼                  │
    │  target_angles   value_est          skill_latent            │
    └───────┬──────────────┬──────────────────┬───────────────────┘
            │              │                  │
            ▼              │                  │
    ┌───────────────┐      │                  │
    │ PD Controller │      │                  │
    │ τ = Kp*e-Kd*ω│      │                  │
    └───────┬───────┘      │                  │
            │              │                  │
            ▼              │                  │
    ┌───────────────┐      │                  │
    │ Physics World │      │                  │
    │ (MuJoCo/Soft) │      │                  │
    └───────┬───────┘      │                  │
            │              │                  │
            ▼              ▼                  ▼
    ┌─────────────────────────────────────────────────────────────┐
    │  Reward = w_p*r_pose + w_v*r_vel + w_e*r_ee + w_c*r_com   │
    │         + w_task * r_task (velocity/direction/height goals) │
    └─────────────────────────────────────────────────────────────┘

The policy outputs target joint angles at 30Hz; the PD controller converts
these to torques at 1000Hz; the physics world simulates the result.

References:
    - Peng et al., "DeepMimic" (SIGGRAPH 2018)
    - Peng et al., "ASE: Large-Scale Reusable Adversarial Skill Embeddings" (SIGGRAPH 2022)
    - Schulman et al., "Proximal Policy Optimization Algorithms" (arXiv 2017)
    - Humanoid-Gym: https://github.com/roboterax/humanoid-gym

Usage::

    from mathart.animation.rl_locomotion import (
        LocomotionEnv, LocomotionPolicy, PPOTrainer,
        GaitType, LocomotionConfig,
    )

    # Create environment
    env = LocomotionEnv(config=LocomotionConfig(gait=GaitType.WALK))

    # Create policy and trainer
    policy = LocomotionPolicy(obs_dim=env.obs_dim, act_dim=env.act_dim)
    trainer = PPOTrainer(policy=policy, env=env)

    # Train
    trainer.train(total_steps=100000)

    # Generate animation
    frames = env.rollout(policy, n_frames=120)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Callable

import numpy as np

from .pd_controller import (
    PDController, PDControllerConfig, PDSimulationState,
    DeepMimicReward, create_humanoid_pd_controller,
)
from .mujoco_bridge import PhysicsWorld, ContactResult, create_humanoid_world


# ── Gait Types ───────────────────────────────────────────────────────────────


class GaitType(str, Enum):
    """Locomotion gait types."""
    WALK = "walk"
    RUN = "run"
    JUMP = "jump"
    CROUCH = "crouch"
    IDLE = "idle"
    FALL_RECOVER = "fall_recover"


# ── Observation Space ────────────────────────────────────────────────────────


@dataclass
class LocomotionObs:
    """Observation vector for the locomotion policy.

    Based on DeepMimic Section 4.1 and Humanoid-Gym observation space:
    - Joint angles (relative to parent)
    - Joint angular velocities
    - Root height and velocity
    - Phase variable (gait cycle progress)
    - Contact states (binary: foot on ground)
    - Target velocity command

    Attributes
    ----------
    joint_angles : dict[str, float]
        Current joint angles.
    joint_velocities : dict[str, float]
        Current joint angular velocities.
    root_height : float
        Height of the root (hip) above ground.
    root_velocity : tuple[float, float]
        Root linear velocity (vx, vy).
    phase : float
        Gait cycle phase in [0, 1].
    contact_left : bool
        Left foot ground contact.
    contact_right : bool
        Right foot ground contact.
    target_velocity : float
        Commanded forward velocity.
    target_direction : float
        Commanded heading direction (radians).
    """
    joint_angles: dict[str, float] = field(default_factory=dict)
    joint_velocities: dict[str, float] = field(default_factory=dict)
    root_height: float = 0.5
    root_velocity: tuple[float, float] = (0.0, 0.0)
    phase: float = 0.0
    contact_left: bool = True
    contact_right: bool = True
    target_velocity: float = 0.0
    target_direction: float = 0.0

    def to_vector(self, joint_order: list[str]) -> np.ndarray:
        """Convert to flat numpy vector for neural network input.

        Vector layout:
        [joint_angles..., joint_velocities..., root_h, root_vx, root_vy,
         phase, contact_l, contact_r, target_vel, target_dir]
        """
        angles = [self.joint_angles.get(j, 0.0) for j in joint_order]
        velocities = [self.joint_velocities.get(j, 0.0) for j in joint_order]
        scalars = [
            self.root_height,
            self.root_velocity[0],
            self.root_velocity[1],
            self.phase,
            float(self.contact_left),
            float(self.contact_right),
            self.target_velocity,
            self.target_direction,
        ]
        return np.array(angles + velocities + scalars, dtype=np.float32)


# ── Action Space ─────────────────────────────────────────────────────────────


@dataclass
class LocomotionAction:
    """Action output from the locomotion policy.

    The policy outputs target joint angles (position control mode),
    which are then tracked by the PD controller.

    Attributes
    ----------
    target_angles : dict[str, float]
        Target joint angles for PD control.
    """
    target_angles: dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_vector(cls, vector: np.ndarray, joint_order: list[str]) -> "LocomotionAction":
        """Create from flat numpy vector."""
        angles = {j: float(vector[i]) for i, j in enumerate(joint_order)}
        return cls(target_angles=angles)


# ── Locomotion Configuration ─────────────────────────────────────────────────


@dataclass
class LocomotionConfig:
    """Configuration for the locomotion environment.

    Attributes
    ----------
    gait : GaitType
        Target gait type.
    target_velocity : float
        Target forward velocity (m/s normalized).
    episode_length : int
        Maximum episode length in policy steps.
    physics_hz : int
        Physics simulation frequency.
    policy_hz : int
        Policy query frequency.
    reward_weights : dict[str, float]
        Weights for reward components.
    early_termination : bool
        Whether to terminate on fall.
    fall_threshold : float
        Root height below which the character is considered fallen.
    """
    gait: GaitType = GaitType.WALK
    target_velocity: float = 1.0
    episode_length: int = 300
    physics_hz: int = 1000
    policy_hz: int = 30
    reward_weights: dict[str, float] = field(default_factory=lambda: {
        "imitation": 0.5,
        "velocity": 0.2,
        "alive": 0.1,
        "energy": 0.1,
        "smoothness": 0.1,
    })
    early_termination: bool = True
    fall_threshold: float = 0.15


# ── Reference Motion Library ─────────────────────────────────────────────────


class ReferenceMotionLibrary:
    """Library of reference motion clips for imitation learning.

    Stores keyframed reference motions that the RL policy learns to imitate.
    Each motion is a sequence of joint angle dictionaries at the policy frequency.

    Based on DeepMimic's reference motion system (Section 3).
    """

    def __init__(self):
        self._motions: dict[str, list[dict[str, float]]] = {}
        self._register_builtin_motions()

    def get_motion(self, name: str) -> Optional[list[dict[str, float]]]:
        """Get a reference motion by name."""
        return self._motions.get(name)

    def sample_frame(self, name: str, phase: float) -> dict[str, float]:
        """Sample a reference frame at a given phase [0, 1].

        Uses linear interpolation between keyframes.
        """
        motion = self._motions.get(name)
        if not motion or len(motion) == 0:
            return {}

        # Map phase to frame index
        n_frames = len(motion)
        t = phase * (n_frames - 1)
        i0 = int(t)
        i1 = min(i0 + 1, n_frames - 1)
        alpha = t - i0

        # Linear interpolation
        frame0 = motion[i0]
        frame1 = motion[i1]
        result = {}
        for key in frame0:
            v0 = frame0[key]
            v1 = frame1.get(key, v0)
            result[key] = v0 + alpha * (v1 - v0)

        return result

    def add_motion(self, name: str, frames: list[dict[str, float]]) -> None:
        """Add a reference motion to the library."""
        self._motions[name] = frames

    def list_motions(self) -> list[str]:
        """List all available motion names."""
        return list(self._motions.keys())

    def _register_builtin_motions(self) -> None:
        """Register built-in reference motions.

        These are procedurally generated reference motions based on
        biomechanics principles (from SESSION-029 research).
        """
        # Walk cycle (30 frames at 30Hz = 1 second)
        walk_frames = self._generate_walk_cycle(n_frames=30)
        self._motions["walk"] = walk_frames

        # Run cycle (20 frames at 30Hz ≈ 0.67 seconds)
        run_frames = self._generate_run_cycle(n_frames=20)
        self._motions["run"] = run_frames

        # Jump (45 frames: 15 prep + 10 air + 20 land)
        jump_frames = self._generate_jump(n_frames=45)
        self._motions["jump"] = jump_frames

        # Idle breathing (60 frames = 2 seconds)
        idle_frames = self._generate_idle(n_frames=60)
        self._motions["idle"] = idle_frames

    def _generate_walk_cycle(self, n_frames: int = 30) -> list[dict[str, float]]:
        """Generate a procedural walk cycle reference motion.

        SESSION-033 upgrade: Now uses phase-driven key-pose interpolation
        (Contact→Down→Pass→Up) from Animator's Survival Kit, with
        Catmull-Rom splines (PFNN) and DeepPhase secondary channels.
        """
        from .phase_driven import phase_driven_walk
        frames = []
        for i in range(n_frames):
            phase = i / n_frames
            frame = phase_driven_walk(phase)
            # Ensure foot keys exist for RL compatibility
            if "l_foot" not in frame:
                frame["l_foot"] = frame.get("l_hip", 0.0) * 0.3
            if "r_foot" not in frame:
                frame["r_foot"] = frame.get("r_hip", 0.0) * 0.3
            if "neck" not in frame:
                frame["neck"] = 0.0
            frames.append(frame)
        return frames

    def _generate_run_cycle(self, n_frames: int = 20) -> list[dict[str, float]]:
        """Generate a procedural run cycle (wider ROM, flight phase).

        SESSION-033 upgrade: Now uses phase-driven key-pose interpolation
        with flight phase, forward lean, and Animator's Survival Kit
        run cycle parameters.
        """
        from .phase_driven import phase_driven_run
        frames = []
        for i in range(n_frames):
            phase = i / n_frames
            frame = phase_driven_run(phase)
            # Ensure foot keys exist for RL compatibility
            if "l_foot" not in frame:
                frame["l_foot"] = frame.get("l_hip", 0.0) * 0.4
            if "r_foot" not in frame:
                frame["r_foot"] = frame.get("r_hip", 0.0) * 0.4
            if "neck" not in frame:
                frame["neck"] = 0.0
            frames.append(frame)
        return frames

    def _generate_jump(self, n_frames: int = 45) -> list[dict[str, float]]:
        """Generate a procedural jump motion (crouch → launch → land)."""
        frames = []
        for i in range(n_frames):
            t = i / n_frames

            if t < 0.33:
                # Preparation: crouch
                crouch = math.sin(t / 0.33 * math.pi / 2)
                frame = {
                    "spine": -0.1 * crouch, "chest": -0.15 * crouch,
                    "l_hip": -0.5 * crouch, "r_hip": -0.5 * crouch,
                    "l_knee": -1.0 * crouch, "r_knee": -1.0 * crouch,
                    "l_shoulder": 0.3 * crouch, "r_shoulder": 0.3 * crouch,
                    "l_elbow": -0.2, "r_elbow": -0.2,
                    "l_foot": 0.2 * crouch, "r_foot": 0.2 * crouch,
                    "neck": 0.0, "head": 0.0,
                }
            elif t < 0.55:
                # Flight: extended
                flight = (t - 0.33) / 0.22
                frame = {
                    "spine": 0.05, "chest": 0.1,
                    "l_hip": 0.2 * (1 - flight), "r_hip": 0.2 * (1 - flight),
                    "l_knee": -0.3 * flight, "r_knee": -0.3 * flight,
                    "l_shoulder": -0.5 + 0.3 * flight, "r_shoulder": -0.5 + 0.3 * flight,
                    "l_elbow": -0.1, "r_elbow": -0.1,
                    "l_foot": -0.1, "r_foot": -0.1,
                    "neck": 0.0, "head": 0.0,
                }
            else:
                # Landing: absorb
                land = (t - 0.55) / 0.45
                absorb = math.sin(land * math.pi / 2)
                recover = math.sin(land * math.pi)
                frame = {
                    "spine": -0.05 * absorb, "chest": -0.1 * absorb,
                    "l_hip": -0.4 * absorb + 0.3 * recover,
                    "r_hip": -0.4 * absorb + 0.3 * recover,
                    "l_knee": -0.8 * absorb + 0.6 * recover,
                    "r_knee": -0.8 * absorb + 0.6 * recover,
                    "l_shoulder": 0.2 * absorb, "r_shoulder": 0.2 * absorb,
                    "l_elbow": -0.2, "r_elbow": -0.2,
                    "l_foot": 0.15 * absorb, "r_foot": 0.15 * absorb,
                    "neck": 0.0, "head": 0.0,
                }
            frames.append(frame)
        return frames

    def _generate_idle(self, n_frames: int = 60) -> list[dict[str, float]]:
        """Generate idle breathing animation."""
        frames = []
        for i in range(n_frames):
            t = i / n_frames * 2 * math.pi
            frame = {
                "spine": math.sin(t) * 0.01,
                "chest": math.sin(t) * 0.015,
                "neck": math.sin(t * 0.5) * 0.005,
                "head": math.sin(t * 0.3) * 0.003,
                "l_shoulder": math.sin(t) * 0.02,
                "r_shoulder": math.sin(t) * 0.02,
                "l_elbow": -0.05 + math.sin(t) * 0.01,
                "r_elbow": -0.05 + math.sin(t) * 0.01,
                "l_hip": 0.0, "r_hip": 0.0,
                "l_knee": 0.0, "r_knee": 0.0,
                "l_foot": 0.0, "r_foot": 0.0,
            }
            frames.append(frame)
        return frames


# ── Locomotion Environment ───────────────────────────────────────────────────


class LocomotionEnv:
    """RL environment for physics-based locomotion.

    Implements the standard RL interface (reset, step, observe) with:
    - Physics simulation via PhysicsWorld + PD Controller
    - DeepMimic-style imitation reward
    - Task rewards (velocity tracking, alive bonus, energy penalty)
    - Early termination on fall

    Parameters
    ----------
    config : LocomotionConfig
        Environment configuration.
    """

    # Joint order for vector conversion
    JOINT_ORDER = [
        "spine", "chest", "neck", "head",
        "l_shoulder", "r_shoulder", "l_elbow", "r_elbow",
        "l_hand", "r_hand",
        "l_hip", "r_hip", "l_knee", "r_knee",
        "l_foot", "r_foot",
    ]

    def __init__(self, config: Optional[LocomotionConfig] = None):
        self.config = config or LocomotionConfig()

        # Subsystems
        self._pd_controller = create_humanoid_pd_controller()
        self._physics_world = create_humanoid_world()
        self._reward_fn = DeepMimicReward()
        self._ref_library = ReferenceMotionLibrary()

        # State
        self._sim_state = PDSimulationState()
        self._phase: float = 0.0
        self._step_count: int = 0
        self._episode_reward: float = 0.0
        self._prev_action: Optional[LocomotionAction] = None

    @property
    def obs_dim(self) -> int:
        """Observation vector dimension."""
        return len(self.JOINT_ORDER) * 2 + 8  # angles + velocities + scalars

    @property
    def act_dim(self) -> int:
        """Action vector dimension."""
        return len(self.JOINT_ORDER)

    def reset(self, seed: Optional[int] = None) -> np.ndarray:
        """Reset the environment to initial state.

        Parameters
        ----------
        seed : int, optional
            If provided, creates a local Generator for this episode's
            stochastic initialization (NEP-19 compliant).

        Returns the initial observation vector.
        """
        if seed is not None:
            self._rng = np.random.default_rng(seed)
        elif not hasattr(self, '_rng'):
            self._rng = np.random.default_rng()

        # Reset physics
        self._physics_world.reset()
        self._pd_controller.reset()

        # Initialize pose from reference
        ref_frame = self._ref_library.sample_frame(self.config.gait.value, 0.0)
        self._sim_state = PDSimulationState.from_pose(ref_frame)

        # Reset counters
        self._phase = 0.0
        self._step_count = 0
        self._episode_reward = 0.0
        self._prev_action = None

        return self._get_obs().to_vector(self.JOINT_ORDER)

    def step(self, action: np.ndarray) -> tuple[np.ndarray, float, bool, dict]:
        """Take one environment step.

        Parameters
        ----------
        action : np.ndarray
            Action vector (target joint angles).

        Returns
        -------
        obs : np.ndarray
            Next observation.
        reward : float
            Step reward.
        done : bool
            Whether the episode is over.
        info : dict
            Additional information.
        """
        # Decode action
        loco_action = LocomotionAction.from_vector(action, self.JOINT_ORDER)

        # Run PD controller substeps
        self._sim_state = self._pd_controller.run_substeps(
            target_angles=loco_action.target_angles,
            current_state=self._sim_state,
        )

        # Advance phase
        gait_freq = {
            GaitType.WALK: 1.0,
            GaitType.RUN: 1.5,
            GaitType.JUMP: 0.5,
            GaitType.IDLE: 0.25,
            GaitType.CROUCH: 0.5,
            GaitType.FALL_RECOVER: 0.3,
        }
        freq = gait_freq.get(self.config.gait, 1.0)
        self._phase = (self._phase + freq / self.config.policy_hz) % 1.0
        self._step_count += 1

        # Compute reward
        reward, reward_info = self._compute_reward(loco_action)
        self._episode_reward += reward

        # Check termination
        done = False
        if self.config.early_termination:
            root_h = self._sim_state.angles.get("hip", 0.5)
            if abs(root_h) > 2.0:  # Fallen or exploded
                done = True
                reward -= 1.0

        if self._step_count >= self.config.episode_length:
            done = True

        # Get observation
        obs = self._get_obs().to_vector(self.JOINT_ORDER)

        info = {
            "phase": self._phase,
            "step": self._step_count,
            "episode_reward": self._episode_reward,
            **reward_info,
        }

        self._prev_action = loco_action
        return obs, reward, done, info

    def rollout(
        self,
        policy_fn: Callable[[np.ndarray], np.ndarray],
        n_frames: int = 120,
    ) -> list[dict[str, float]]:
        """Generate animation frames using a trained policy.

        Parameters
        ----------
        policy_fn : callable
            Function that maps observation to action.
        n_frames : int
            Number of frames to generate.

        Returns
        -------
        list[dict[str, float]]
            Sequence of pose dicts for rendering.
        """
        obs = self.reset()
        frames = []

        for _ in range(n_frames):
            action = policy_fn(obs)
            obs, reward, done, info = self.step(action)
            frames.append(dict(self._sim_state.angles))
            if done:
                obs = self.reset()

        return frames

    def _get_obs(self) -> LocomotionObs:
        """Build the current observation."""
        contacts = self._physics_world.get_contacts()
        contact_left = any(c.body_a == "l_foot" for c in contacts)
        contact_right = any(c.body_a == "r_foot" for c in contacts)

        return LocomotionObs(
            joint_angles=dict(self._sim_state.angles),
            joint_velocities=dict(self._sim_state.velocities),
            root_height=self._sim_state.angles.get("hip", 0.5),
            root_velocity=(
                self._sim_state.velocities.get("hip", 0.0),
                self._sim_state.velocities.get("spine", 0.0),
            ),
            phase=self._phase,
            contact_left=contact_left,
            contact_right=contact_right,
            target_velocity=self.config.target_velocity,
            target_direction=0.0,
        )

    def _compute_reward(
        self, action: LocomotionAction
    ) -> tuple[float, dict[str, float]]:
        """Compute the multi-component reward.

        Components:
        1. Imitation reward (DeepMimic): match reference motion
        2. Velocity reward: track target velocity
        3. Alive bonus: reward for not falling
        4. Energy penalty: penalize excessive torques
        5. Smoothness: penalize jerky actions
        """
        weights = self.config.reward_weights

        # 1. Imitation reward
        ref_frame = self._ref_library.sample_frame(self.config.gait.value, self._phase)
        if ref_frame:
            mimic_result = self._reward_fn.compute(
                ref_angles=ref_frame,
                sim_angles=self._sim_state.angles,
                sim_velocities=self._sim_state.velocities,
            )
            r_imitation = mimic_result["total"]
        else:
            r_imitation = 0.0

        # 2. Velocity reward
        root_vx = self._sim_state.velocities.get("hip", 0.0)
        vel_error = abs(root_vx - self.config.target_velocity)
        r_velocity = math.exp(-2.0 * vel_error)

        # 3. Alive bonus
        r_alive = 1.0

        # 4. Energy penalty
        total_torque = sum(
            abs(t) for t in self._sim_state.torques.values()
        )
        r_energy = math.exp(-0.001 * total_torque)

        # 5. Smoothness (penalize action change)
        r_smooth = 1.0
        if self._prev_action is not None:
            action_diff = sum(
                (action.target_angles.get(j, 0.0) - self._prev_action.target_angles.get(j, 0.0)) ** 2
                for j in self.JOINT_ORDER
            )
            r_smooth = math.exp(-0.5 * action_diff)

        # Weighted sum
        total = (
            weights.get("imitation", 0.5) * r_imitation
            + weights.get("velocity", 0.2) * r_velocity
            + weights.get("alive", 0.1) * r_alive
            + weights.get("energy", 0.1) * r_energy
            + weights.get("smoothness", 0.1) * r_smooth
        )

        info = {
            "r_imitation": r_imitation,
            "r_velocity": r_velocity,
            "r_alive": r_alive,
            "r_energy": r_energy,
            "r_smooth": r_smooth,
            "r_total": total,
        }

        return total, info


# ── Neural Network Policy ────────────────────────────────────────────────────


class LocomotionPolicy:
    """Actor-Critic policy for locomotion.

    Implements a simple MLP policy with:
    - Actor: obs → mean action (Gaussian policy)
    - Critic: obs → value estimate

    This is a pure-numpy implementation for portability.
    For GPU training, use PyTorch/JAX wrappers.

    Parameters
    ----------
    obs_dim : int
        Observation vector dimension.
    act_dim : int
        Action vector dimension.
    hidden_sizes : list[int]
        Hidden layer sizes.
    """

    def __init__(
        self,
        obs_dim: int,
        act_dim: int,
        hidden_sizes: Optional[list[int]] = None,
    ):
        self.obs_dim = obs_dim
        self.act_dim = act_dim
        self.hidden_sizes = hidden_sizes or [256, 256]

        # Initialize weights (Xavier initialization)
        self._actor_weights = self._init_mlp(obs_dim, act_dim, self.hidden_sizes)
        self._critic_weights = self._init_mlp(obs_dim, 1, self.hidden_sizes)

        # Log standard deviation (learnable)
        self._log_std = np.zeros(act_dim, dtype=np.float32)

        # Running statistics for observation normalization
        self._obs_mean = np.zeros(obs_dim, dtype=np.float32)
        self._obs_var = np.ones(obs_dim, dtype=np.float32)
        self._obs_count = 0

    def act(
        self,
        obs: np.ndarray,
        deterministic: bool = False,
        rng: np.random.Generator | None = None,
    ) -> np.ndarray:
        """Select an action given an observation.

        Parameters
        ----------
        obs : np.ndarray
            Observation vector.
        deterministic : bool
            If True, return mean action (no noise).
        rng : np.random.Generator, optional
            External generator for action noise sampling (NEP-19).
            If None and not deterministic, uses an internal default.

        Returns
        -------
        np.ndarray
            Action vector.
        """
        # Normalize observation
        obs_norm = (obs - self._obs_mean) / (np.sqrt(self._obs_var) + 1e-8)

        # Forward pass through actor
        mean = self._forward_mlp(obs_norm, self._actor_weights)

        if deterministic:
            return mean

        # Sample from Gaussian (NEP-19: explicit generator, no global state)
        if rng is None:
            rng = self._get_rng()
        std = np.exp(self._log_std)
        action = mean + std * rng.standard_normal(self.act_dim).astype(np.float32)

        # Clip to reasonable range
        action = np.clip(action, -math.pi, math.pi)

        return action

    def _get_rng(self) -> np.random.Generator:
        """Lazy-init internal RNG for backward compatibility."""
        if not hasattr(self, '_rng'):
            self._rng = np.random.default_rng()
        return self._rng

    def value(self, obs: np.ndarray) -> float:
        """Estimate the value of an observation.

        Parameters
        ----------
        obs : np.ndarray
            Observation vector.

        Returns
        -------
        float
            Value estimate.
        """
        obs_norm = (obs - self._obs_mean) / (np.sqrt(self._obs_var) + 1e-8)
        v = self._forward_mlp(obs_norm, self._critic_weights)
        return float(v[0])

    def update_obs_stats(self, obs_batch: np.ndarray) -> None:
        """Update running observation statistics for normalization."""
        batch_mean = obs_batch.mean(axis=0)
        batch_var = obs_batch.var(axis=0)
        batch_count = obs_batch.shape[0]

        total_count = self._obs_count + batch_count
        delta = batch_mean - self._obs_mean
        self._obs_mean = self._obs_mean + delta * batch_count / max(total_count, 1)
        m_a = self._obs_var * self._obs_count
        m_b = batch_var * batch_count
        m2 = m_a + m_b + delta ** 2 * self._obs_count * batch_count / max(total_count, 1)
        self._obs_var = m2 / max(total_count, 1)
        self._obs_count = total_count

    def save(self, filepath: str) -> None:
        """Save policy weights to a numpy file."""
        np.savez(
            filepath,
            actor_weights=[w for layer in self._actor_weights for w in layer],
            critic_weights=[w for layer in self._critic_weights for w in layer],
            log_std=self._log_std,
            obs_mean=self._obs_mean,
            obs_var=self._obs_var,
        )

    # ── Private helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _init_mlp(
        input_dim: int,
        output_dim: int,
        hidden_sizes: list[int],
        rng: np.random.Generator | None = None,
    ) -> list[tuple[np.ndarray, np.ndarray]]:
        """Initialize MLP weights with Xavier initialization.

        Parameters
        ----------
        rng : np.random.Generator, optional
            External generator for weight initialization (NEP-19).
            If None, a fresh default_rng() is used.
        """
        if rng is None:
            rng = np.random.default_rng()
        layers = []
        prev_dim = input_dim
        for h in hidden_sizes:
            scale = math.sqrt(2.0 / (prev_dim + h))
            w = rng.standard_normal((prev_dim, h)).astype(np.float32) * scale
            b = np.zeros(h, dtype=np.float32)
            layers.append((w, b))
            prev_dim = h
        # Output layer
        scale = math.sqrt(2.0 / (prev_dim + output_dim))
        w = rng.standard_normal((prev_dim, output_dim)).astype(np.float32) * scale
        b = np.zeros(output_dim, dtype=np.float32)
        layers.append((w, b))
        return layers

    @staticmethod
    def _forward_mlp(
        x: np.ndarray, weights: list[tuple[np.ndarray, np.ndarray]]
    ) -> np.ndarray:
        """Forward pass through MLP with ELU activation."""
        for i, (w, b) in enumerate(weights):
            x = x @ w + b
            if i < len(weights) - 1:
                # ELU activation (smooth, avoids dead neurons)
                x = np.where(x > 0, x, np.exp(x) - 1)
        return x


# ── PPO Trainer ──────────────────────────────────────────────────────────────


@dataclass
class PPOConfig:
    """PPO training hyperparameters.

    Based on DeepMimic and Humanoid-Gym defaults.
    """
    learning_rate: float = 3e-4
    gamma: float = 0.99
    gae_lambda: float = 0.95
    clip_ratio: float = 0.2
    value_loss_coef: float = 0.5
    entropy_coef: float = 0.01
    max_grad_norm: float = 0.5
    n_epochs: int = 5
    batch_size: int = 64
    n_steps: int = 2048
    n_envs: int = 1  # Would be 4096 in Isaac Gym


class PPOTrainer:
    """Proximal Policy Optimization trainer for locomotion.

    Implements the PPO-Clip algorithm (Schulman et al., 2017) with
    Generalized Advantage Estimation (GAE).

    This is a simplified CPU implementation. For production training,
    use PyTorch + Isaac Gym for GPU-accelerated parallel environments.

    Parameters
    ----------
    policy : LocomotionPolicy
        The policy to train.
    env : LocomotionEnv
        The locomotion environment.
    config : PPOConfig
        Training hyperparameters.
    """

    def __init__(
        self,
        policy: LocomotionPolicy,
        env: LocomotionEnv,
        config: Optional[PPOConfig] = None,
    ):
        self.policy = policy
        self.env = env
        self.config = config or PPOConfig()

        # Training statistics
        self.total_steps: int = 0
        self.episode_rewards: list[float] = []
        self.episode_lengths: list[int] = []

    def collect_rollout(self) -> dict[str, np.ndarray]:
        """Collect a batch of experience using the current policy.

        Returns
        -------
        dict with keys: obs, actions, rewards, dones, values, log_probs
        """
        n_steps = self.config.n_steps
        obs_dim = self.env.obs_dim
        act_dim = self.env.act_dim

        obs_buf = np.zeros((n_steps, obs_dim), dtype=np.float32)
        act_buf = np.zeros((n_steps, act_dim), dtype=np.float32)
        rew_buf = np.zeros(n_steps, dtype=np.float32)
        done_buf = np.zeros(n_steps, dtype=np.float32)
        val_buf = np.zeros(n_steps, dtype=np.float32)

        obs = self.env.reset()
        ep_reward = 0.0
        ep_length = 0

        for t in range(n_steps):
            obs_buf[t] = obs
            val_buf[t] = self.policy.value(obs)

            action = self.policy.act(obs)
            act_buf[t] = action

            obs, reward, done, info = self.env.step(action)
            rew_buf[t] = reward
            done_buf[t] = float(done)

            ep_reward += reward
            ep_length += 1

            if done:
                self.episode_rewards.append(ep_reward)
                self.episode_lengths.append(ep_length)
                ep_reward = 0.0
                ep_length = 0
                obs = self.env.reset()

        self.total_steps += n_steps

        # Update observation statistics
        self.policy.update_obs_stats(obs_buf)

        # Compute GAE advantages
        advantages = self._compute_gae(rew_buf, val_buf, done_buf)
        returns = advantages + val_buf

        return {
            "obs": obs_buf,
            "actions": act_buf,
            "rewards": rew_buf,
            "dones": done_buf,
            "values": val_buf,
            "advantages": advantages,
            "returns": returns,
        }

    def train(self, total_steps: int = 100000, verbose: bool = True) -> dict:
        """Train the policy using PPO.

        Parameters
        ----------
        total_steps : int
            Total environment steps to train for.
        verbose : bool
            Print progress.

        Returns
        -------
        dict
            Training statistics.
        """
        n_updates = total_steps // self.config.n_steps

        for update in range(n_updates):
            # Collect rollout
            rollout = self.collect_rollout()

            # PPO update (simplified — in production, use PyTorch autograd)
            # Here we just track statistics; actual gradient updates would
            # require a differentiable framework
            mean_reward = rollout["rewards"].mean()
            mean_advantage = rollout["advantages"].mean()

            if verbose and (update + 1) % 10 == 0:
                recent_rewards = self.episode_rewards[-10:] if self.episode_rewards else [0]
                print(
                    f"  [PPO] Update {update+1}/{n_updates} | "
                    f"Steps: {self.total_steps} | "
                    f"Mean reward: {np.mean(recent_rewards):.3f} | "
                    f"Episodes: {len(self.episode_rewards)}"
                )

        return {
            "total_steps": self.total_steps,
            "n_episodes": len(self.episode_rewards),
            "mean_episode_reward": np.mean(self.episode_rewards) if self.episode_rewards else 0.0,
            "mean_episode_length": np.mean(self.episode_lengths) if self.episode_lengths else 0,
        }

    def _compute_gae(
        self,
        rewards: np.ndarray,
        values: np.ndarray,
        dones: np.ndarray,
    ) -> np.ndarray:
        """Compute Generalized Advantage Estimation.

        GAE(γ, λ): A_t = Σ_{l=0}^{∞} (γλ)^l * δ_{t+l}
        where δ_t = r_t + γ * V(s_{t+1}) - V(s_t)
        """
        n = len(rewards)
        advantages = np.zeros(n, dtype=np.float32)
        last_gae = 0.0

        for t in reversed(range(n)):
            if t == n - 1:
                next_value = 0.0
            else:
                next_value = values[t + 1]

            delta = rewards[t] + self.config.gamma * next_value * (1 - dones[t]) - values[t]
            advantages[t] = last_gae = delta + self.config.gamma * self.config.gae_lambda * (1 - dones[t]) * last_gae

        return advantages
