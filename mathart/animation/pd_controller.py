"""PD Controller — Proportional-Derivative joint torque control for physics-based animation.

SESSION-030: Research-driven implementation of PD (Proportional-Derivative) controllers
for simulating virtual muscle torque in physics-based character animation.

This module implements the mathematical foundation used by DeepMimic (Peng et al., 2018)
and NVIDIA Isaac Sim / Humanoid-Gym for controlling articulated characters in physics
engines. The PD controller computes joint torques that drive a character's joints toward
target angles, producing physically plausible motion with natural muscle-like response.

Core equation (from DeepMimic, Section 4):
    τ = k_p * (θ_target - θ_current) - k_d * ω_current

where:
    τ         = output torque (N·m in normalized units)
    k_p       = proportional gain (stiffness) — "how hard the muscle pulls"
    k_d       = derivative gain (damping) — "how much the muscle resists velocity"
    θ_target  = desired joint angle from RL policy or animation
    θ_current = actual joint angle from physics simulation
    ω_current = actual joint angular velocity (dθ/dt)

Design decisions:
    - Gains are per-joint (different muscles have different strength)
    - Torque limits enforce realistic force bounds (prevent infinite force)
    - Stability analysis: k_d ≥ 2*sqrt(k_p*I) for critical damping
    - 30Hz policy → 1000Hz PD (DeepMimic uses 30Hz RL, 1200Hz physics)
    - Integrates with existing AnglePoseProjector and BiomechanicsProjector

References:
    - Peng et al., "DeepMimic: Example-Guided Deep Reinforcement Learning of
      Physics-Based Character Skills" (SIGGRAPH 2018)
    - Peng et al., "ASE: Large-Scale Reusable Adversarial Skill Embeddings"
      (SIGGRAPH 2022)
    - Humanoid-Gym (RobotEra): PD gains for humanoid locomotion
    - MuJoCo documentation: actuator/position and actuator/velocity models

Usage::

    from mathart.animation.pd_controller import (
        PDController, PDJointConfig, PDControllerConfig,
        HumanoidPDPreset, create_humanoid_pd_controller,
    )

    # Create controller with humanoid preset
    controller = create_humanoid_pd_controller()

    # In simulation loop (1000Hz physics, 30Hz policy):
    for physics_step in range(substeps_per_policy):
        torques = controller.compute_torques(
            target_angles=policy_output,
            current_angles=sim_state.angles,
            current_velocities=sim_state.velocities,
            dt=1.0 / 1000.0,
        )
        # Apply torques to physics engine
        sim_state = physics_engine.step(torques, dt)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import numpy as np


# ── Per-Joint PD Configuration ───────────────────────────────────────────────


@dataclass
class PDJointConfig:
    """PD controller configuration for a single joint.

    Attributes
    ----------
    k_p : float
        Proportional gain (stiffness). Higher = stronger tracking.
        Typical range: 50-500 for primary joints, 10-100 for secondary.
    k_d : float
        Derivative gain (damping). Higher = less oscillation.
        For critical damping: k_d = 2 * sqrt(k_p * inertia).
    max_torque : float
        Maximum absolute torque output. Prevents unrealistic forces.
        Typical range: 50-500 N·m (normalized).
    inertia : float
        Approximate moment of inertia for this joint's subtree.
        Used for stability analysis and critical damping computation.
    target_velocity : float
        Optional target velocity for velocity-mode PD control.
        When set, adds velocity tracking: τ += k_d * (ω_target - ω_current).
    """
    k_p: float = 200.0
    k_d: float = 20.0
    max_torque: float = 300.0
    inertia: float = 1.0
    target_velocity: float = 0.0

    @property
    def critical_damping(self) -> float:
        """Critical damping coefficient for this joint."""
        return 2.0 * math.sqrt(self.k_p * self.inertia)

    @property
    def damping_ratio(self) -> float:
        """Damping ratio ζ. ζ=1: critical, ζ<1: underdamped, ζ>1: overdamped."""
        cc = self.critical_damping
        return self.k_d / cc if cc > 1e-8 else 1.0

    @property
    def natural_frequency(self) -> float:
        """Natural frequency ω_n = sqrt(k_p / I) in rad/s."""
        return math.sqrt(self.k_p / max(self.inertia, 1e-8))

    @property
    def settling_time(self) -> float:
        """Approximate settling time (2% criterion) in seconds.

        For underdamped: t_s ≈ 4 / (ζ * ω_n)
        For overdamped: t_s ≈ 4 * ζ / ω_n
        """
        zeta = self.damping_ratio
        omega_n = self.natural_frequency
        if omega_n < 1e-8:
            return float('inf')
        if zeta < 1.0:
            return 4.0 / (zeta * omega_n) if zeta > 1e-8 else float('inf')
        else:
            return 4.0 * zeta / omega_n


# ── PD Controller Configuration ─────────────────────────────────────────────


@dataclass
class PDControllerConfig:
    """Global PD controller configuration.

    Attributes
    ----------
    physics_hz : int
        Physics simulation frequency (Hz). DeepMimic uses 1200Hz.
    policy_hz : int
        RL policy query frequency (Hz). DeepMimic uses 30Hz.
    gravity : float
        Gravitational acceleration (m/s² in normalized units).
    enable_gravity_compensation : bool
        If True, adds gravity compensation torque to counteract gravity.
    enable_torque_smoothing : bool
        If True, applies exponential smoothing to torque output.
    torque_smoothing_alpha : float
        Smoothing factor (0 = no smoothing, 1 = full smoothing).
    """
    physics_hz: int = 1000
    policy_hz: int = 30
    gravity: float = 9.81
    enable_gravity_compensation: bool = True
    enable_torque_smoothing: bool = True
    torque_smoothing_alpha: float = 0.2

    @property
    def substeps_per_policy(self) -> int:
        """Number of physics substeps per policy query."""
        return max(1, self.physics_hz // self.policy_hz)

    @property
    def physics_dt(self) -> float:
        """Physics timestep in seconds."""
        return 1.0 / self.physics_hz

    @property
    def policy_dt(self) -> float:
        """Policy timestep in seconds."""
        return 1.0 / self.policy_hz


# ── Humanoid PD Gain Presets ─────────────────────────────────────────────────


class HumanoidPDPreset(str, Enum):
    """Preset PD gain profiles for different character types.

    Based on DeepMimic Table 1 and Humanoid-Gym default configs.
    """
    DEEPMIMIC_STANDARD = "deepmimic_standard"
    HUMANOID_GYM_H1 = "humanoid_gym_h1"
    CHIBI_LIGHT = "chibi_light"
    HEAVY_MONSTER = "heavy_monster"


# DeepMimic-style gains (Peng et al., 2018)
# Higher k_p for primary joints (hip, knee), lower for extremities
_DEEPMIMIC_GAINS: dict[str, PDJointConfig] = {
    "hip":        PDJointConfig(k_p=500.0, k_d=50.0,  max_torque=400.0, inertia=2.0),
    "spine":      PDJointConfig(k_p=400.0, k_d=40.0,  max_torque=350.0, inertia=1.5),
    "chest":      PDJointConfig(k_p=300.0, k_d=30.0,  max_torque=300.0, inertia=1.2),
    "neck":       PDJointConfig(k_p=100.0, k_d=10.0,  max_torque=100.0, inertia=0.3),
    "head":       PDJointConfig(k_p=80.0,  k_d=8.0,   max_torque=80.0,  inertia=0.5),
    "l_shoulder": PDJointConfig(k_p=200.0, k_d=20.0,  max_torque=200.0, inertia=0.8),
    "r_shoulder": PDJointConfig(k_p=200.0, k_d=20.0,  max_torque=200.0, inertia=0.8),
    "l_elbow":    PDJointConfig(k_p=150.0, k_d=15.0,  max_torque=150.0, inertia=0.5),
    "r_elbow":    PDJointConfig(k_p=150.0, k_d=15.0,  max_torque=150.0, inertia=0.5),
    "l_hand":     PDJointConfig(k_p=50.0,  k_d=5.0,   max_torque=50.0,  inertia=0.2),
    "r_hand":     PDJointConfig(k_p=50.0,  k_d=5.0,   max_torque=50.0,  inertia=0.2),
    "l_hip":      PDJointConfig(k_p=500.0, k_d=50.0,  max_torque=500.0, inertia=2.5),
    "r_hip":      PDJointConfig(k_p=500.0, k_d=50.0,  max_torque=500.0, inertia=2.5),
    "l_knee":     PDJointConfig(k_p=400.0, k_d=40.0,  max_torque=400.0, inertia=2.0),
    "r_knee":     PDJointConfig(k_p=400.0, k_d=40.0,  max_torque=400.0, inertia=2.0),
    "l_foot":     PDJointConfig(k_p=200.0, k_d=20.0,  max_torque=200.0, inertia=0.8),
    "r_foot":     PDJointConfig(k_p=200.0, k_d=20.0,  max_torque=200.0, inertia=0.8),
}

# Chibi-style gains (lighter, snappier for pixel art characters)
_CHIBI_GAINS: dict[str, PDJointConfig] = {
    name: PDJointConfig(
        k_p=cfg.k_p * 0.6,
        k_d=cfg.k_d * 0.5,
        max_torque=cfg.max_torque * 0.5,
        inertia=cfg.inertia * 0.4,
    )
    for name, cfg in _DEEPMIMIC_GAINS.items()
}

# Heavy monster gains (sluggish, powerful)
_HEAVY_GAINS: dict[str, PDJointConfig] = {
    name: PDJointConfig(
        k_p=cfg.k_p * 1.5,
        k_d=cfg.k_d * 2.0,
        max_torque=cfg.max_torque * 2.0,
        inertia=cfg.inertia * 3.0,
    )
    for name, cfg in _DEEPMIMIC_GAINS.items()
}

_PRESET_GAINS: dict[str, dict[str, PDJointConfig]] = {
    HumanoidPDPreset.DEEPMIMIC_STANDARD.value: _DEEPMIMIC_GAINS,
    HumanoidPDPreset.HUMANOID_GYM_H1.value: _DEEPMIMIC_GAINS,  # Same base
    HumanoidPDPreset.CHIBI_LIGHT.value: _CHIBI_GAINS,
    HumanoidPDPreset.HEAVY_MONSTER.value: _HEAVY_GAINS,
}


# ── Core PD Controller ──────────────────────────────────────────────────────


class PDController:
    """Multi-joint PD controller for physics-based character animation.

    Computes torques for all joints simultaneously using the PD control law:
        τ_i = k_p_i * (θ_target_i - θ_current_i) - k_d_i * ω_current_i

    With optional extensions:
        - Gravity compensation: τ_gravity = m * g * L * sin(θ)
        - Torque smoothing: τ_smooth = α * τ_prev + (1-α) * τ_raw
        - Velocity tracking: τ += k_d * (ω_target - ω_current)

    Parameters
    ----------
    joint_configs : dict[str, PDJointConfig]
        PD configuration per joint name.
    config : PDControllerConfig
        Global controller configuration.
    """

    def __init__(
        self,
        joint_configs: dict[str, PDJointConfig],
        config: Optional[PDControllerConfig] = None,
    ):
        self.joint_configs = dict(joint_configs)
        self.config = config or PDControllerConfig()

        # State tracking
        self._prev_torques: dict[str, float] = {}
        self._torque_history: list[dict[str, float]] = []
        self._step_count: int = 0

        # Joint mass distribution for gravity compensation
        self._joint_masses: dict[str, float] = {
            "hip": 0.15, "spine": 0.10, "chest": 0.15, "neck": 0.02,
            "head": 0.08, "l_shoulder": 0.02, "r_shoulder": 0.02,
            "l_elbow": 0.03, "r_elbow": 0.03, "l_hand": 0.01, "r_hand": 0.01,
            "l_hip": 0.05, "r_hip": 0.05, "l_knee": 0.08, "r_knee": 0.08,
            "l_foot": 0.03, "r_foot": 0.03,
        }

        # Approximate link lengths (normalized, for gravity compensation)
        self._link_lengths: dict[str, float] = {
            "hip": 0.15, "spine": 0.12, "chest": 0.12, "neck": 0.05,
            "head": 0.08, "l_shoulder": 0.10, "r_shoulder": 0.10,
            "l_elbow": 0.12, "r_elbow": 0.12, "l_hand": 0.08, "r_hand": 0.08,
            "l_hip": 0.18, "r_hip": 0.18, "l_knee": 0.18, "r_knee": 0.18,
            "l_foot": 0.06, "r_foot": 0.06,
        }

    def compute_torques(
        self,
        target_angles: dict[str, float],
        current_angles: dict[str, float],
        current_velocities: Optional[dict[str, float]] = None,
        dt: Optional[float] = None,
    ) -> dict[str, float]:
        """Compute PD torques for all joints.

        Parameters
        ----------
        target_angles : dict[str, float]
            Desired joint angles (from RL policy or animation).
        current_angles : dict[str, float]
            Actual joint angles (from physics simulation).
        current_velocities : dict[str, float], optional
            Actual joint angular velocities. If None, estimated from
            finite differences of current_angles.
        dt : float, optional
            Timestep. Defaults to config.physics_dt.

        Returns
        -------
        dict[str, float]
            Torque for each joint (N·m in normalized units).
        """
        if dt is None:
            dt = self.config.physics_dt

        torques: dict[str, float] = {}

        for joint_name, pd_cfg in self.joint_configs.items():
            # Get current state
            theta_target = target_angles.get(joint_name, 0.0)
            theta_current = current_angles.get(joint_name, 0.0)

            # Angular error (normalize to [-π, π])
            error = theta_target - theta_current
            while error > math.pi:
                error -= 2.0 * math.pi
            while error < -math.pi:
                error += 2.0 * math.pi

            # Angular velocity
            if current_velocities and joint_name in current_velocities:
                omega = current_velocities[joint_name]
            else:
                # Estimate from finite differences
                omega = self._estimate_velocity(joint_name, theta_current, dt)

            # ── Core PD law ──
            # τ = k_p * (θ_target - θ_current) - k_d * ω
            tau = pd_cfg.k_p * error - pd_cfg.k_d * omega

            # ── Velocity tracking (optional) ──
            if abs(pd_cfg.target_velocity) > 1e-8:
                tau += pd_cfg.k_d * (pd_cfg.target_velocity - omega)

            # ── Gravity compensation (optional) ──
            if self.config.enable_gravity_compensation:
                tau += self._gravity_torque(joint_name, theta_current)

            # ── Torque clamping ──
            tau = float(np.clip(tau, -pd_cfg.max_torque, pd_cfg.max_torque))

            # ── Torque smoothing (optional) ──
            if self.config.enable_torque_smoothing and joint_name in self._prev_torques:
                alpha = self.config.torque_smoothing_alpha
                tau = alpha * self._prev_torques[joint_name] + (1.0 - alpha) * tau

            torques[joint_name] = tau

        # Update state
        self._prev_torques = dict(torques)
        self._step_count += 1

        # Record history (keep last 100 steps)
        self._torque_history.append(dict(torques))
        if len(self._torque_history) > 100:
            self._torque_history.pop(0)

        return torques

    def compute_torques_batch(
        self,
        target_angles: np.ndarray,
        current_angles: np.ndarray,
        current_velocities: np.ndarray,
        joint_names: list[str],
    ) -> np.ndarray:
        """Vectorized torque computation for GPU-accelerated simulation.

        Implements the same PD law but in batch form for efficiency:
            τ = K_p * (θ_target - θ_current) - K_d * ω

        Parameters
        ----------
        target_angles : np.ndarray, shape (n_envs, n_joints)
            Target angles for all environments and joints.
        current_angles : np.ndarray, shape (n_envs, n_joints)
            Current angles.
        current_velocities : np.ndarray, shape (n_envs, n_joints)
            Current angular velocities.
        joint_names : list[str]
            Joint names corresponding to the joint axis.

        Returns
        -------
        np.ndarray, shape (n_envs, n_joints)
            Computed torques.
        """
        n_envs, n_joints = target_angles.shape

        # Build gain vectors
        k_p = np.array([self.joint_configs.get(j, PDJointConfig()).k_p for j in joint_names])
        k_d = np.array([self.joint_configs.get(j, PDJointConfig()).k_d for j in joint_names])
        max_tau = np.array([self.joint_configs.get(j, PDJointConfig()).max_torque for j in joint_names])

        # Compute angular error with wrapping
        error = target_angles - current_angles
        error = (error + np.pi) % (2 * np.pi) - np.pi

        # PD law (vectorized)
        torques = k_p[None, :] * error - k_d[None, :] * current_velocities

        # Clamp
        torques = np.clip(torques, -max_tau[None, :], max_tau[None, :])

        return torques

    def step_simulation(
        self,
        target_angles: dict[str, float],
        current_state: "PDSimulationState",
        dt: Optional[float] = None,
    ) -> "PDSimulationState":
        """Advance the PD-controlled simulation by one physics step.

        This integrates the PD controller with simple rigid-body dynamics:
            I * α = τ_pd + τ_gravity
            ω_new = ω + α * dt
            θ_new = θ + ω_new * dt

        Parameters
        ----------
        target_angles : dict[str, float]
            Target angles from RL policy.
        current_state : PDSimulationState
            Current simulation state.
        dt : float, optional
            Timestep.

        Returns
        -------
        PDSimulationState
            Updated state after one physics step.
        """
        if dt is None:
            dt = self.config.physics_dt

        # Compute torques
        torques = self.compute_torques(
            target_angles=target_angles,
            current_angles=current_state.angles,
            current_velocities=current_state.velocities,
            dt=dt,
        )

        # Integrate dynamics: I * α = τ → α = τ / I
        new_angles = dict(current_state.angles)
        new_velocities = dict(current_state.velocities)

        for joint_name, tau in torques.items():
            inertia = self.joint_configs.get(joint_name, PDJointConfig()).inertia
            alpha = tau / max(inertia, 1e-8)

            # Semi-implicit Euler
            omega = current_state.velocities.get(joint_name, 0.0)
            omega_new = omega + alpha * dt
            theta_new = current_state.angles.get(joint_name, 0.0) + omega_new * dt

            new_velocities[joint_name] = omega_new
            new_angles[joint_name] = theta_new

        return PDSimulationState(
            angles=new_angles,
            velocities=new_velocities,
            torques=torques,
            time=current_state.time + dt,
        )

    def run_substeps(
        self,
        target_angles: dict[str, float],
        current_state: "PDSimulationState",
        n_substeps: Optional[int] = None,
    ) -> "PDSimulationState":
        """Run multiple physics substeps for one policy step.

        DeepMimic pattern: 30Hz policy → 1000Hz physics = ~33 substeps.

        Parameters
        ----------
        target_angles : dict[str, float]
            Target angles from RL policy (held constant for all substeps).
        current_state : PDSimulationState
            Current state.
        n_substeps : int, optional
            Number of substeps. Defaults to config.substeps_per_policy.

        Returns
        -------
        PDSimulationState
            State after all substeps.
        """
        if n_substeps is None:
            n_substeps = self.config.substeps_per_policy

        state = current_state
        for _ in range(n_substeps):
            state = self.step_simulation(target_angles, state)

        return state

    def stability_report(self) -> dict[str, dict[str, float]]:
        """Generate a stability analysis report for all joints.

        Returns
        -------
        dict[str, dict]
            Per-joint stability metrics.
        """
        report = {}
        for name, cfg in self.joint_configs.items():
            report[name] = {
                "k_p": cfg.k_p,
                "k_d": cfg.k_d,
                "damping_ratio": cfg.damping_ratio,
                "natural_frequency_hz": cfg.natural_frequency / (2 * math.pi),
                "settling_time_s": cfg.settling_time,
                "is_stable": cfg.damping_ratio > 0.0,
                "is_critically_damped": abs(cfg.damping_ratio - 1.0) < 0.1,
                "is_underdamped": cfg.damping_ratio < 0.9,
                "is_overdamped": cfg.damping_ratio > 1.1,
            }
        return report

    def reset(self) -> None:
        """Reset controller state."""
        self._prev_torques.clear()
        self._torque_history.clear()
        self._step_count = 0

    # ── Private helpers ──────────────────────────────────────────────────────

    _prev_angles: dict[str, float] = {}

    def _estimate_velocity(
        self, joint_name: str, current_angle: float, dt: float
    ) -> float:
        """Estimate angular velocity from finite differences."""
        if joint_name in self._prev_angles:
            prev = self._prev_angles[joint_name]
            omega = (current_angle - prev) / max(dt, 1e-8)
        else:
            omega = 0.0
        self._prev_angles[joint_name] = current_angle
        return omega

    def _gravity_torque(self, joint_name: str, theta: float) -> float:
        """Compute gravity compensation torque.

        τ_gravity = m * g * L * sin(θ)

        where L is the distance from joint to the center of mass of the
        subtree below this joint.
        """
        mass = self._joint_masses.get(joint_name, 0.01)
        length = self._link_lengths.get(joint_name, 0.1)
        return mass * self.config.gravity * length * math.sin(theta)


# ── Simulation State ─────────────────────────────────────────────────────────


@dataclass
class PDSimulationState:
    """State of the PD-controlled physics simulation.

    Attributes
    ----------
    angles : dict[str, float]
        Current joint angles (radians).
    velocities : dict[str, float]
        Current joint angular velocities (rad/s).
    torques : dict[str, float]
        Last computed torques (N·m).
    time : float
        Simulation time (seconds).
    """
    angles: dict[str, float] = field(default_factory=dict)
    velocities: dict[str, float] = field(default_factory=dict)
    torques: dict[str, float] = field(default_factory=dict)
    time: float = 0.0

    @classmethod
    def from_pose(cls, pose: dict[str, float]) -> "PDSimulationState":
        """Create initial state from a pose dict (zero velocity)."""
        return cls(
            angles=dict(pose),
            velocities={k: 0.0 for k in pose},
            torques={k: 0.0 for k in pose},
            time=0.0,
        )

    def kinetic_energy(self, joint_configs: dict[str, PDJointConfig]) -> float:
        """Compute total kinetic energy: KE = Σ 0.5 * I * ω²."""
        ke = 0.0
        for name, omega in self.velocities.items():
            inertia = joint_configs.get(name, PDJointConfig()).inertia
            ke += 0.5 * inertia * omega * omega
        return ke

    def potential_energy(
        self,
        joint_configs: dict[str, PDJointConfig],
        target_angles: dict[str, float],
    ) -> float:
        """Compute total potential energy: PE = Σ 0.5 * k_p * (θ - θ_target)²."""
        pe = 0.0
        for name, theta in self.angles.items():
            k_p = joint_configs.get(name, PDJointConfig()).k_p
            target = target_angles.get(name, 0.0)
            error = theta - target
            pe += 0.5 * k_p * error * error
        return pe


# ── DeepMimic Imitation Reward ───────────────────────────────────────────────


class DeepMimicReward:
    """Compute DeepMimic-style imitation reward for RL training.

    The reward function from Peng et al. (2018) Section 5:
        r_t = w_p * r_p + w_v * r_v + w_e * r_e + w_c * r_c

    where:
        r_p = exp(-5 * Σ ||q̂_j - q_j||²)       — pose reward
        r_v = exp(-0.1 * Σ ||q̇̂_j - q̇_j||²)    — velocity reward
        r_e = exp(-40 * Σ ||p̂_e - p_e||²)       — end-effector reward
        r_c = exp(-10 * ||p̂_com - p_com||²)     — center of mass reward

    Default weights: w_p=0.65, w_v=0.1, w_e=0.15, w_c=0.1

    Parameters
    ----------
    w_pose : float
        Weight for pose matching reward.
    w_velocity : float
        Weight for velocity matching reward.
    w_end_effector : float
        Weight for end-effector position reward.
    w_com : float
        Weight for center of mass reward.
    """

    def __init__(
        self,
        w_pose: float = 0.65,
        w_velocity: float = 0.10,
        w_end_effector: float = 0.15,
        w_com: float = 0.10,
    ):
        self.w_pose = w_pose
        self.w_velocity = w_velocity
        self.w_end_effector = w_end_effector
        self.w_com = w_com

    def compute(
        self,
        ref_angles: dict[str, float],
        sim_angles: dict[str, float],
        ref_velocities: Optional[dict[str, float]] = None,
        sim_velocities: Optional[dict[str, float]] = None,
        ref_end_effectors: Optional[dict[str, tuple[float, float]]] = None,
        sim_end_effectors: Optional[dict[str, tuple[float, float]]] = None,
        ref_com: Optional[tuple[float, float]] = None,
        sim_com: Optional[tuple[float, float]] = None,
    ) -> dict[str, float]:
        """Compute the full DeepMimic reward.

        Returns
        -------
        dict with keys: 'pose', 'velocity', 'end_effector', 'com', 'total'
        """
        # Pose reward: r_p = exp(-5 * Σ ||q̂_j - q_j||²)
        pose_err = 0.0
        joint_names = set(ref_angles.keys()) & set(sim_angles.keys())
        for j in joint_names:
            diff = ref_angles[j] - sim_angles[j]
            # Normalize angle difference
            diff = (diff + math.pi) % (2 * math.pi) - math.pi
            pose_err += diff * diff
        r_pose = math.exp(-5.0 * pose_err)

        # Velocity reward: r_v = exp(-0.1 * Σ ||q̇̂_j - q̇_j||²)
        r_velocity = 1.0
        if ref_velocities and sim_velocities:
            vel_err = 0.0
            for j in joint_names:
                dv = ref_velocities.get(j, 0.0) - sim_velocities.get(j, 0.0)
                vel_err += dv * dv
            r_velocity = math.exp(-0.1 * vel_err)

        # End-effector reward: r_e = exp(-40 * Σ ||p̂_e - p_e||²)
        r_ee = 1.0
        if ref_end_effectors and sim_end_effectors:
            ee_err = 0.0
            for name in ref_end_effectors:
                if name in sim_end_effectors:
                    ref_p = ref_end_effectors[name]
                    sim_p = sim_end_effectors[name]
                    dx = ref_p[0] - sim_p[0]
                    dy = ref_p[1] - sim_p[1]
                    ee_err += dx * dx + dy * dy
            r_ee = math.exp(-40.0 * ee_err)

        # CoM reward: r_c = exp(-10 * ||p̂_com - p_com||²)
        r_com = 1.0
        if ref_com and sim_com:
            dx = ref_com[0] - sim_com[0]
            dy = ref_com[1] - sim_com[1]
            r_com = math.exp(-10.0 * (dx * dx + dy * dy))

        # Weighted sum
        total = (
            self.w_pose * r_pose
            + self.w_velocity * r_velocity
            + self.w_end_effector * r_ee
            + self.w_com * r_com
        )

        return {
            "pose": r_pose,
            "velocity": r_velocity,
            "end_effector": r_ee,
            "com": r_com,
            "total": total,
        }


# ── Factory Functions ────────────────────────────────────────────────────────


def create_humanoid_pd_controller(
    preset: HumanoidPDPreset = HumanoidPDPreset.DEEPMIMIC_STANDARD,
    config: Optional[PDControllerConfig] = None,
) -> PDController:
    """Create a PD controller with preset gains for a humanoid character.

    Parameters
    ----------
    preset : HumanoidPDPreset
        Gain preset to use.
    config : PDControllerConfig, optional
        Global configuration overrides.

    Returns
    -------
    PDController
    """
    gains = _PRESET_GAINS.get(preset.value, _DEEPMIMIC_GAINS)
    return PDController(joint_configs=gains, config=config)


def create_pd_from_skeleton(
    skeleton,
    base_k_p: float = 200.0,
    base_k_d: float = 20.0,
    config: Optional[PDControllerConfig] = None,
) -> PDController:
    """Create a PD controller from a Skeleton object.

    Automatically scales gains based on joint mass and bone length.

    Parameters
    ----------
    skeleton : Skeleton
        The skeleton to create a controller for.
    base_k_p : float
        Base proportional gain (scaled per joint).
    base_k_d : float
        Base derivative gain (scaled per joint).
    config : PDControllerConfig, optional
        Global configuration.

    Returns
    -------
    PDController
    """
    joint_configs = {}
    for bone in skeleton.bones:
        # Scale gains by bone length (longer bones need more torque)
        length_scale = bone.length / 0.15  # Normalize to average bone length
        mass_scale = 1.0  # Could be derived from joint mass

        joint_configs[bone.child_joint] = PDJointConfig(
            k_p=base_k_p * length_scale,
            k_d=base_k_d * length_scale,
            max_torque=base_k_p * length_scale * 1.5,
            inertia=length_scale * 0.5,
        )

    return PDController(joint_configs=joint_configs, config=config)

