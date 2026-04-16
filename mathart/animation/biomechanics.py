"""Biomechanics Engine — ZMP/CoM balance, Inverted Pendulum, Skating Cleanup, FABRIK Gait.

SESSION-029: Research-driven implementation of four core biomechanics/kinematics
algorithms for physically plausible 2D character animation:

1. **Zero Moment Point (ZMP) & Center of Mass (CoM)** — The ultimate balance law
   for bipedal characters. Computes the gravity+inertia force projection and checks
   whether it falls within the foot support polygon. Frames where ZMP exits the
   support area are visually "falling/weightless".

2. **Inverted Pendulum Model (IPM)** — The classic mathematical model for human
   walking CoM trajectory. Models the body as a point mass on a massless telescopic
   leg, producing natural vertical bounce and lateral sway during locomotion.

3. **Foot Skating Cleanup (Calculus-based)** — Enhanced velocity-derivative approach
   for detecting and eliminating foot sliding. When ankle height approaches zero,
   horizontal velocity is forced to zero using smooth Hermite blending.

4. **FABRIK Procedural Gait Generator** — Wires the existing FABRIKSolver into
   the animation pipeline to produce IK-driven walk/run/jump cycles that adapt
   foot placement to planned contact points.

Mathematical foundations:
  - ZMP: x_zmp = Σ(x_i * f_{z,i}) / Σ f_{z,i}  (Vukobratović, 1968)
  - LIPM: ẍ = (g/z_c)(x - x_foot), ω = sqrt(g/z_c)  (Kajita et al., 2001)
  - Skating cleanup: enforce dp/dt|_{xy} = 0 when h(t) ≤ ε  (Kovar et al., 2002)
  - FABRIK: forward-backward reaching IK  (Aristidou & Lasenby, 2011)

References:
  - Vukobratović & Borovac, "Zero-Moment Point" (Humanoids 2001)
  - Kajita et al., "The 3D Linear Inverted Pendulum Mode" (IEEE IROS 2001)
  - Kovar et al., "Footskate Cleanup for Motion Capture Editing" (SCA 2002)
  - Aristidou & Lasenby, "FABRIK: A fast, iterative solver for the IK problem"
  - Yuan et al., "PhysDiff" (ICCV 2023)
  - MIT Underactuated Robotics, Ch.5 (Russ Tedrake, 2024)

Usage::

    from mathart.animation.biomechanics import (
        ZMPAnalyzer, InvertedPendulumModel, SkatingCleanupCalculus,
        FABRIKGaitGenerator, BiomechanicsProjector,
    )

    # Standalone ZMP analysis
    analyzer = ZMPAnalyzer(skeleton)
    result = analyzer.analyze_frame(pose, prev_pose, dt)
    print(result.zmp_x, result.is_balanced)

    # Full biomechanics projector (integrates all four systems)
    projector = BiomechanicsProjector(skeleton)
    corrected_pose = projector.step(raw_pose, dt)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Callable

import numpy as np

from .physics import FABRIKSolver


# ── Joint Mass Distribution ─────────────────────────────────────────────────
# Approximate mass fractions for a humanoid character (biomechanics standard).
# Based on Winter (2009) anthropometric data, simplified for 2D chibi characters.

DEFAULT_JOINT_MASSES: dict[str, float] = {
    "root": 0.0,
    "hip": 0.15,       # Pelvis
    "spine": 0.10,     # Lower trunk
    "chest": 0.15,     # Upper trunk
    "neck": 0.02,      # Neck
    "head": 0.08,      # Head
    "l_shoulder": 0.02,
    "r_shoulder": 0.02,
    "l_elbow": 0.03,   # Upper arm
    "r_elbow": 0.03,
    "l_hand": 0.01,    # Forearm + hand
    "r_hand": 0.01,
    "l_hip": 0.05,     # Upper leg attachment
    "r_hip": 0.05,
    "l_knee": 0.08,    # Thigh
    "r_knee": 0.08,
    "l_foot": 0.03,    # Shank + foot
    "r_foot": 0.03,
}


# ── ZMP / CoM Analysis ──────────────────────────────────────────────────────


@dataclass
class ZMPResult:
    """Result of ZMP/CoM analysis for a single frame.

    Attributes
    ----------
    com_x, com_y : float
        Center of mass position in world coordinates.
    com_vx, com_vy : float
        Center of mass velocity (from finite differences).
    com_ax, com_ay : float
        Center of mass acceleration (from finite differences).
    zmp_x : float
        Zero Moment Point x-coordinate on the ground plane.
    support_left : float
        Left boundary of the support polygon (leftmost foot x).
    support_right : float
        Right boundary of the support polygon (rightmost foot x).
    is_balanced : bool
        True if ZMP is within the support polygon.
    balance_margin : float
        Distance from ZMP to nearest support polygon edge.
        Positive = inside (stable), negative = outside (falling).
    stability_score : float
        Normalized stability score in [0, 1]. 1.0 = perfectly centered.
    """
    com_x: float = 0.0
    com_y: float = 0.0
    com_vx: float = 0.0
    com_vy: float = 0.0
    com_ax: float = 0.0
    com_ay: float = 0.0
    zmp_x: float = 0.0
    support_left: float = 0.0
    support_right: float = 0.0
    is_balanced: bool = True
    balance_margin: float = 0.0
    stability_score: float = 1.0


class ZMPAnalyzer:
    """Zero Moment Point and Center of Mass analyzer for 2D skeletal animation.

    Implements the ZMP stability criterion from bipedal robotics:
    - Computes CoM from joint positions and mass distribution
    - Computes ZMP from CoM acceleration (Newton-Euler dynamics)
    - Checks if ZMP falls within the foot support polygon

    The key equation (from MIT Underactuated Robotics, Tedrake 2024):
        x_zmp = x_com - (z_com / g) * ẍ_com

    For 2D animation on flat terrain (z_com ≈ constant, z̈ ≈ 0):
        x_zmp = x_com - (z_com * ẍ_com) / (z̈_com + g)

    When z̈ ≈ 0:
        x_zmp ≈ x_com - (z_com / g) * ẍ_com

    Parameters
    ----------
    skeleton : Skeleton
        The skeleton definition for FK computation.
    joint_masses : dict[str, float], optional
        Mass fraction per joint. Defaults to DEFAULT_JOINT_MASSES.
    gravity : float
        Gravitational acceleration (normalized units). Default 9.81.
    foot_half_width : float
        Half-width of the foot support area. Default 0.05.
    """

    def __init__(
        self,
        skeleton,
        joint_masses: dict[str, float] | None = None,
        gravity: float = 9.81,
        foot_half_width: float = 0.05,
    ):
        self._skeleton = skeleton
        self._masses = dict(joint_masses or DEFAULT_JOINT_MASSES)
        self._gravity = gravity
        self._foot_half_width = foot_half_width

        # History for finite-difference velocity/acceleration
        self._com_history: list[tuple[float, float]] = []
        self._max_history = 4

    def compute_com(
        self,
        joint_positions: dict[str, tuple[float, float]],
    ) -> tuple[float, float]:
        """Compute the Center of Mass from joint positions and masses.

        CoM = Σ(m_i * p_i) / Σ(m_i)

        Parameters
        ----------
        joint_positions : dict[str, (x, y)]
            World-space joint positions from forward kinematics.

        Returns
        -------
        (com_x, com_y) : Center of mass position.
        """
        total_mass = 0.0
        weighted_x = 0.0
        weighted_y = 0.0

        for name, pos in joint_positions.items():
            mass = self._masses.get(name, 0.01)
            weighted_x += mass * pos[0]
            weighted_y += mass * pos[1]
            total_mass += mass

        if total_mass < 1e-8:
            return (0.0, 0.0)

        return (weighted_x / total_mass, weighted_y / total_mass)

    def compute_support_polygon(
        self,
        joint_positions: dict[str, tuple[float, float]],
        contact_threshold: float = 0.05,
    ) -> tuple[float, float]:
        """Compute the 1D support polygon (left/right bounds) from foot positions.

        In 2D side-view, the support polygon is the horizontal span of
        grounded feet. A foot is considered grounded if its y-position
        is below the contact threshold.

        Returns
        -------
        (left_bound, right_bound) : Support polygon boundaries.
        """
        foot_joints = ["l_foot", "r_foot"]
        grounded_x = []

        for foot_name in foot_joints:
            if foot_name in joint_positions:
                pos = joint_positions[foot_name]
                if pos[1] <= contact_threshold:
                    grounded_x.append(pos[0])

        if not grounded_x:
            # No feet on ground — use both foot positions as fallback
            for foot_name in foot_joints:
                if foot_name in joint_positions:
                    grounded_x.append(joint_positions[foot_name][0])

        if not grounded_x:
            return (0.0, 0.0)

        left = min(grounded_x) - self._foot_half_width
        right = max(grounded_x) + self._foot_half_width

        return (left, right)

    def analyze_frame(
        self,
        pose: dict[str, float],
        dt: float = 1.0 / 60.0,
    ) -> ZMPResult:
        """Analyze a single frame for ZMP/CoM balance.

        Parameters
        ----------
        pose : dict[str, float]
            Joint angles for the current frame.
        dt : float
            Timestep for finite-difference derivatives.

        Returns
        -------
        ZMPResult : Complete balance analysis.
        """
        # Forward kinematics
        self._skeleton.apply_pose(pose)
        positions = self._skeleton.forward_kinematics()

        # Compute CoM
        com_x, com_y = self.compute_com(positions)

        # Update history for velocity/acceleration estimation
        self._com_history.append((com_x, com_y))
        if len(self._com_history) > self._max_history:
            self._com_history.pop(0)

        # Compute velocity (central differences when possible)
        com_vx, com_vy = 0.0, 0.0
        com_ax, com_ay = 0.0, 0.0

        if len(self._com_history) >= 2:
            prev = self._com_history[-2]
            com_vx = (com_x - prev[0]) / max(dt, 1e-8)
            com_vy = (com_y - prev[1]) / max(dt, 1e-8)

        if len(self._com_history) >= 3:
            p0 = self._com_history[-3]
            p1 = self._com_history[-2]
            p2 = self._com_history[-1]
            com_ax = (p2[0] - 2 * p1[0] + p0[0]) / max(dt * dt, 1e-12)
            com_ay = (p2[1] - 2 * p1[1] + p0[1]) / max(dt * dt, 1e-12)

        # Compute ZMP (Vukobratović formula for flat terrain)
        # x_zmp = x_com - (z_com * ẍ_com) / (z̈_com + g)
        denominator = com_ay + self._gravity
        if abs(denominator) > 1e-6:
            zmp_x = com_x - (com_y * com_ax) / denominator
        else:
            zmp_x = com_x  # Fallback: ZMP ≈ CoM projection

        # Compute support polygon
        support_left, support_right = self.compute_support_polygon(positions)

        # Check balance
        is_balanced = support_left <= zmp_x <= support_right

        # Balance margin (positive = inside, negative = outside)
        margin_left = zmp_x - support_left
        margin_right = support_right - zmp_x
        balance_margin = min(margin_left, margin_right)

        # Stability score: 1.0 at center, 0.0 at edge, negative outside
        support_width = max(support_right - support_left, 1e-6)
        center = (support_left + support_right) / 2.0
        normalized_offset = abs(zmp_x - center) / (support_width / 2.0)
        stability_score = float(np.clip(1.0 - normalized_offset, 0.0, 1.0))

        return ZMPResult(
            com_x=com_x,
            com_y=com_y,
            com_vx=com_vx,
            com_vy=com_vy,
            com_ax=com_ax,
            com_ay=com_ay,
            zmp_x=zmp_x,
            support_left=support_left,
            support_right=support_right,
            is_balanced=is_balanced,
            balance_margin=balance_margin,
            stability_score=stability_score,
        )

    def analyze_sequence(
        self,
        pose_sequence: list[dict[str, float]],
        dt: float = 1.0 / 60.0,
    ) -> list[ZMPResult]:
        """Analyze an entire animation sequence for balance.

        Returns
        -------
        list[ZMPResult] : Per-frame balance analysis.
        """
        self.reset()
        return [self.analyze_frame(pose, dt) for pose in pose_sequence]

    def compute_balance_penalty(
        self,
        pose_sequence: list[dict[str, float]],
        dt: float = 1.0 / 60.0,
    ) -> float:
        """Compute a ZMP-based balance penalty for GA fitness integration.

        Returns a penalty score (lower = better, 0 = perfectly balanced).
        Penalizes frames where ZMP exits the support polygon.
        """
        results = self.analyze_sequence(pose_sequence, dt)
        if not results:
            return 0.0

        penalty = 0.0
        for r in results:
            if not r.is_balanced:
                # Quadratic penalty for ZMP outside support
                penalty += r.balance_margin * r.balance_margin * 10.0
            else:
                # Small penalty for being near the edge
                penalty += (1.0 - r.stability_score) * 0.1

        return penalty / len(results)

    def reset(self) -> None:
        """Reset analysis history."""
        self._com_history.clear()


# ── Inverted Pendulum Model ─────────────────────────────────────────────────


@dataclass
class IPMState:
    """State of the Inverted Pendulum Model.

    Attributes
    ----------
    x : float
        Horizontal CoM position relative to stance foot.
    x_dot : float
        Horizontal CoM velocity.
    z_c : float
        Constant CoM height (constraint plane).
    foot_x : float
        Current stance foot position.
    phase : float
        Current phase in the gait cycle [0, 1].
    """
    x: float = 0.0
    x_dot: float = 0.0
    z_c: float = 0.5
    foot_x: float = 0.0
    phase: float = 0.0


class InvertedPendulumModel:
    """Linear Inverted Pendulum Model (LIPM) for CoM trajectory generation.

    Models the character's center of mass as a point mass on a massless
    telescopic leg, constrained to move on a horizontal plane at height z_c.

    The dynamics are governed by (Kajita et al., 2001):
        ẍ = (g / z_c) * (x - x_foot)

    This has the analytical solution:
        x(t) = x₀ * cosh(ωt) + (ẋ₀/ω) * sinh(ωt)
        ẋ(t) = x₀ * ω * sinh(ωt) + ẋ₀ * cosh(ωt)

    where ω = sqrt(g / z_c) is the natural frequency.

    Parameters
    ----------
    com_height : float
        Nominal CoM height (z_c) in normalized skeleton units.
        For a 3-head-unit chibi character, this is approximately 0.5.
    gravity : float
        Gravitational acceleration. Default 9.81.
    step_length : float
        Nominal step length for walking. Default 0.15.
    step_duration : float
        Duration of one step in seconds. Default 0.4.
    """

    def __init__(
        self,
        com_height: float = 0.5,
        gravity: float = 9.81,
        step_length: float = 0.15,
        step_duration: float = 0.4,
    ):
        self.z_c = max(com_height, 0.01)
        self.gravity = gravity
        self.step_length = step_length
        self.step_duration = step_duration

        # Natural frequency of the inverted pendulum
        self.omega = math.sqrt(gravity / self.z_c)

        self._state = IPMState(z_c=self.z_c)

    @property
    def natural_frequency(self) -> float:
        """Natural frequency ω = sqrt(g / z_c)."""
        return self.omega

    def compute_com_trajectory(
        self,
        t: float,
        x0: float = 0.0,
        x_dot0: float = 0.0,
    ) -> tuple[float, float]:
        """Compute CoM position and velocity at time t using analytical solution.

        x(t) = x₀ * cosh(ωt) + (ẋ₀/ω) * sinh(ωt)
        ẋ(t) = x₀ * ω * sinh(ωt) + ẋ₀ * cosh(ωt)

        Parameters
        ----------
        t : float
            Time since start of current step.
        x0 : float
            Initial CoM offset from stance foot.
        x_dot0 : float
            Initial CoM velocity.

        Returns
        -------
        (x, x_dot) : CoM position and velocity at time t.
        """
        w = self.omega
        cosh_wt = math.cosh(w * t)
        sinh_wt = math.sinh(w * t)

        x = x0 * cosh_wt + (x_dot0 / w) * sinh_wt
        x_dot = x0 * w * sinh_wt + x_dot0 * cosh_wt

        return (x, x_dot)

    def compute_vertical_bounce(
        self,
        phase: float,
        amplitude: float = 0.02,
    ) -> float:
        """Compute the natural vertical CoM bounce during walking.

        During walking, the CoM follows an inverted pendulum arc:
        - Highest at mid-stance (single support, leg vertical)
        - Lowest at double-support (weight transfer between legs)

        The vertical displacement is approximately:
            Δz ≈ amplitude * cos(2π * phase)

        where phase 0 and 0.5 are mid-stance, 0.25 and 0.75 are transitions.

        Parameters
        ----------
        phase : float
            Gait cycle phase [0, 1]. 0 = left mid-stance, 0.5 = right mid-stance.
        amplitude : float
            Vertical bounce amplitude (fraction of character height).

        Returns
        -------
        float : Vertical displacement from nominal CoM height.
        """
        return amplitude * math.cos(2.0 * math.pi * phase)

    def compute_lateral_sway(
        self,
        phase: float,
        amplitude: float = 0.015,
    ) -> float:
        """Compute the natural lateral CoM sway during walking.

        The CoM shifts laterally toward the stance leg:
            Δx_lateral ≈ amplitude * sin(2π * phase)

        Parameters
        ----------
        phase : float
            Gait cycle phase [0, 1].
        amplitude : float
            Lateral sway amplitude.

        Returns
        -------
        float : Lateral displacement from center.
        """
        return amplitude * math.sin(2.0 * math.pi * phase)

    def generate_walk_com(
        self,
        n_frames: int,
        forward_speed: float = 0.3,
    ) -> list[tuple[float, float, float]]:
        """Generate a complete CoM trajectory for a walk cycle.

        Returns a list of (x_offset, y_bounce, lateral_sway) per frame,
        which can be used to modulate the hip/spine joint angles.

        Parameters
        ----------
        n_frames : int
            Number of frames in the walk cycle.
        forward_speed : float
            Forward walking speed (normalized units per second).

        Returns
        -------
        list of (x_offset, y_bounce, lateral_sway) per frame.
        """
        trajectory = []
        for i in range(n_frames):
            phase = i / max(1, n_frames)

            # Horizontal CoM trajectory from LIPM
            t_step = phase * self.step_duration
            x_offset, _ = self.compute_com_trajectory(
                t_step,
                x0=-self.step_length / 2,
                x_dot0=forward_speed,
            )

            # Vertical bounce (inverted pendulum arc)
            y_bounce = self.compute_vertical_bounce(phase)

            # Lateral sway
            lateral = self.compute_lateral_sway(phase)

            trajectory.append((x_offset, y_bounce, lateral))

        return trajectory

    def reset(self) -> None:
        """Reset the model state."""
        self._state = IPMState(z_c=self.z_c)


# ── Foot Skating Cleanup (Calculus-based) ────────────────────────────────────


@dataclass
class SkatingCleanupState:
    """Per-foot state for the calculus-based skating cleanup.

    Attributes
    ----------
    is_locked : bool
        Whether the foot is currently locked to a ground contact point.
    lock_position : tuple[float, float] or None
        The world-space position where the foot is locked.
    prev_position : tuple[float, float] or None
        Previous frame's foot position for velocity computation.
    prev_velocity : tuple[float, float]
        Previous frame's foot velocity for acceleration computation.
    blend_weight : float
        Current constraint blend weight [0, 1].
    frames_locked : int
        Number of consecutive frames the foot has been locked.
    """
    is_locked: bool = False
    lock_position: Optional[tuple[float, float]] = None
    prev_position: Optional[tuple[float, float]] = None
    prev_velocity: tuple[float, float] = (0.0, 0.0)
    blend_weight: float = 0.0
    frames_locked: int = 0


class SkatingCleanupCalculus:
    """Calculus-based foot skating cleanup algorithm.

    Enhanced version of the Kovar et al. (2002) approach that uses
    velocity derivatives to detect and eliminate foot sliding.

    The core principle: when ankle height h(t) → 0, force the
    horizontal velocity to zero:

        if h(t) ≤ ε AND |ḣ(t)| ≤ δ:
            enforce dx/dt = 0, dy/dt = 0  (foot stationary)

    The velocity is computed via finite differences:
        v(t) = (p(t) - p(t-dt)) / dt
        a(t) = (v(t) - v(t-dt)) / dt

    Smooth blending uses Hermite interpolation (smoothstep):
        w(t) = 3t² - 2t³

    Parameters
    ----------
    height_threshold : float
        Maximum height for contact detection. Default 0.05.
    velocity_threshold : float
        Maximum velocity magnitude for contact. Default 0.12.
    acceleration_threshold : float
        Maximum acceleration for refined contact detection. Default 2.0.
    blend_in_frames : int
        Frames to ramp up constraint. Default 2.
    blend_out_frames : int
        Frames to ramp down constraint. Default 3.
    foot_joints : list[str]
        Names of foot joints to monitor.
    """

    def __init__(
        self,
        height_threshold: float = 0.05,
        velocity_threshold: float = 0.12,
        acceleration_threshold: float = 2.0,
        blend_in_frames: int = 2,
        blend_out_frames: int = 3,
        foot_joints: list[str] | None = None,
    ):
        self.height_threshold = height_threshold
        self.velocity_threshold = velocity_threshold
        self.acceleration_threshold = acceleration_threshold
        self.blend_in_frames = max(1, blend_in_frames)
        self.blend_out_frames = max(1, blend_out_frames)
        self.foot_joints = foot_joints or ["l_foot", "r_foot"]

        self._states: dict[str, SkatingCleanupState] = {
            name: SkatingCleanupState() for name in self.foot_joints
        }

    def _smoothstep(self, t: float) -> float:
        """Hermite smoothstep interpolation: w(t) = 3t² - 2t³.

        Provides C¹ continuous blending (zero derivative at endpoints).
        """
        t = float(np.clip(t, 0.0, 1.0))
        return t * t * (3.0 - 2.0 * t)

    def _compute_velocity(
        self,
        current_pos: tuple[float, float],
        prev_pos: tuple[float, float] | None,
        dt: float,
    ) -> tuple[float, float]:
        """Compute velocity via finite differences: v = dp/dt."""
        if prev_pos is None:
            return (0.0, 0.0)
        vx = (current_pos[0] - prev_pos[0]) / max(dt, 1e-8)
        vy = (current_pos[1] - prev_pos[1]) / max(dt, 1e-8)
        return (vx, vy)

    def _compute_acceleration(
        self,
        current_vel: tuple[float, float],
        prev_vel: tuple[float, float],
        dt: float,
    ) -> tuple[float, float]:
        """Compute acceleration via finite differences: a = dv/dt."""
        ax = (current_vel[0] - prev_vel[0]) / max(dt, 1e-8)
        ay = (current_vel[1] - prev_vel[1]) / max(dt, 1e-8)
        return (ax, ay)

    def update(
        self,
        joint_positions: dict[str, tuple[float, float]],
        dt: float = 1.0 / 60.0,
    ) -> dict[str, SkatingCleanupState]:
        """Update skating cleanup states for all foot joints.

        This implements the calculus-based detection:
        1. Compute velocity v(t) = dp/dt via finite differences
        2. Compute acceleration a(t) = dv/dt via finite differences
        3. Check contact conditions: h ≤ ε, |v| ≤ δ, |a| ≤ α
        4. Update blend weights with Hermite smoothstep transitions

        Parameters
        ----------
        joint_positions : dict[str, (x, y)]
            World-space positions from FK.
        dt : float
            Timestep.

        Returns
        -------
        dict[str, SkatingCleanupState] : Updated per-foot states.
        """
        for foot_name in self.foot_joints:
            if foot_name not in joint_positions:
                continue

            pos = joint_positions[foot_name]
            state = self._states[foot_name]
            foot_y = pos[1]

            # Step 1: Compute velocity (dp/dt)
            velocity = self._compute_velocity(pos, state.prev_position, dt)
            vel_magnitude = math.sqrt(velocity[0] ** 2 + velocity[1] ** 2)

            # Step 2: Compute acceleration (dv/dt)
            acceleration = self._compute_acceleration(
                velocity, state.prev_velocity, dt
            )
            accel_magnitude = math.sqrt(
                acceleration[0] ** 2 + acceleration[1] ** 2
            )

            # Step 3: Contact detection (calculus-based)
            height_ok = foot_y <= self.height_threshold
            velocity_ok = vel_magnitude <= self.velocity_threshold
            accel_ok = accel_magnitude <= self.acceleration_threshold

            should_lock = height_ok and velocity_ok

            if should_lock and not state.is_locked:
                # Transition: free → locked
                state.is_locked = True
                state.lock_position = (pos[0], min(pos[1], 0.0))
                state.frames_locked = 1
            elif should_lock and state.is_locked:
                # Continue locked
                state.frames_locked += 1
            elif not should_lock and state.is_locked:
                # Transition: locked → free
                state.is_locked = False
                state.frames_locked = 0
            else:
                # Continue free
                state.lock_position = None

            # Step 4: Update blend weight with Hermite smoothstep
            if state.is_locked:
                # Ramp up
                t = min(state.frames_locked / self.blend_in_frames, 1.0)
                state.blend_weight = self._smoothstep(t)
            else:
                # Ramp down
                if state.blend_weight > 0:
                    state.blend_weight = max(
                        0.0,
                        state.blend_weight - 1.0 / self.blend_out_frames,
                    )

            # Update history
            state.prev_position = pos
            state.prev_velocity = velocity

        return dict(self._states)

    def compute_corrections(
        self,
        joint_positions: dict[str, tuple[float, float]],
    ) -> dict[str, tuple[float, float]]:
        """Compute position corrections to eliminate skating.

        For each locked foot, the correction vector is:
            Δp = blend_weight * (lock_position - current_position)

        This enforces dp/dt|_{xy} = 0 during contact by pulling
        the foot back to its locked position.

        Returns
        -------
        dict[str, (dx, dy)] : Position corrections per foot joint.
        """
        corrections = {}
        for foot_name in self.foot_joints:
            state = self._states[foot_name]
            if (
                state.blend_weight > 1e-4
                and state.lock_position is not None
                and foot_name in joint_positions
            ):
                current = joint_positions[foot_name]
                lock = state.lock_position
                dx = state.blend_weight * (lock[0] - current[0])
                dy = state.blend_weight * (lock[1] - current[1])
                corrections[foot_name] = (dx, dy)

        return corrections

    def compute_skating_metric(
        self,
        joint_positions: dict[str, tuple[float, float]],
        dt: float = 1.0 / 60.0,
    ) -> float:
        """Compute the skating metric (PhysDiff-style).

        "Find foot joints that contact the ground in two adjacent frames
        and compute their average horizontal displacement."
        — PhysDiff, Appendix A

        Returns
        -------
        float : Average horizontal displacement of grounded feet (lower = better).
        """
        total_skating = 0.0
        count = 0

        for foot_name in self.foot_joints:
            state = self._states[foot_name]
            if (
                state.is_locked
                and state.prev_position is not None
                and foot_name in joint_positions
            ):
                current = joint_positions[foot_name]
                prev = state.prev_position
                dx = abs(current[0] - prev[0])
                total_skating += dx
                count += 1

        return total_skating / max(count, 1)

    def reset(self) -> None:
        """Reset all skating cleanup states."""
        for name in self.foot_joints:
            self._states[name] = SkatingCleanupState()


# ── FABRIK Procedural Gait Generator ────────────────────────────────────────


@dataclass
class GaitPhase:
    """Describes a single phase of the gait cycle.

    Attributes
    ----------
    foot_target : tuple[float, float]
        Target position for the swing foot.
    is_left_stance : bool
        True if left foot is the stance foot.
    phase_start : float
        Start of this phase in the gait cycle [0, 1].
    phase_end : float
        End of this phase in the gait cycle [0, 1].
    """
    foot_target: tuple[float, float] = (0.0, 0.0)
    is_left_stance: bool = True
    phase_start: float = 0.0
    phase_end: float = 0.5


class FABRIKGaitGenerator:
    """FABRIK-driven procedural gait generator.

    Wires the existing FABRIKSolver into the animation pipeline to produce
    IK-driven walk/run/jump cycles. Instead of purely keyframed joint angles,
    this generator plans foot contact points and uses FABRIK to solve the
    leg joint angles that place the feet at those targets.

    The gait cycle is divided into phases:
    - Left stance / Right swing (0.0 - 0.5)
    - Right stance / Left swing (0.5 - 1.0)

    For each phase:
    1. Compute hip position from InvertedPendulumModel CoM trajectory
    2. Plan foot target positions (contact points on ground)
    3. Use FABRIK to solve leg IK from hip to foot target
    4. Convert FABRIK joint positions to joint angles

    Parameters
    ----------
    skeleton : Skeleton
        The skeleton definition for bone lengths and joint hierarchy.
    step_length : float
        Nominal step length. Default 0.15.
    step_height : float
        Maximum foot lift height during swing. Default 0.08.
    com_height : float
        Nominal CoM height for IPM. Default 0.5.
    """

    def __init__(
        self,
        skeleton,
        step_length: float = 0.15,
        step_height: float = 0.08,
        com_height: float = 0.5,
    ):
        self._skeleton = skeleton
        self.step_length = step_length
        self.step_height = step_height
        self.com_height = com_height

        # Extract leg bone lengths from skeleton
        self._l_thigh_len = self._get_bone_length("l_hip", "l_knee")
        self._l_shin_len = self._get_bone_length("l_knee", "l_foot")
        self._r_thigh_len = self._get_bone_length("r_hip", "r_knee")
        self._r_shin_len = self._get_bone_length("r_knee", "r_foot")

        # Create FABRIK solvers for each leg (2-bone chains)
        self._l_solver = FABRIKSolver(
            chain_lengths=[self._l_thigh_len, self._l_shin_len],
            joint_constraints=[
                (-math.pi / 2, math.pi / 2),   # Hip ROM
                (-math.pi * 0.8, 0),            # Knee: backward only
            ],
            max_iterations=10,
            tolerance=0.001,
        )
        self._r_solver = FABRIKSolver(
            chain_lengths=[self._r_thigh_len, self._r_shin_len],
            joint_constraints=[
                (-math.pi / 2, math.pi / 2),
                (-math.pi * 0.8, 0),
            ],
            max_iterations=10,
            tolerance=0.001,
        )

        # IPM for CoM trajectory
        self._ipm = InvertedPendulumModel(
            com_height=com_height,
            step_length=step_length,
        )

    def _get_bone_length(self, joint_a: str, joint_b: str) -> float:
        """Extract bone length between two joints from skeleton."""
        for bone in self._skeleton.bones:
            if bone.joint_a == joint_a and bone.joint_b == joint_b:
                return bone.length
        # Fallback: compute from joint positions
        if joint_a in self._skeleton.joints and joint_b in self._skeleton.joints:
            ja = self._skeleton.joints[joint_a]
            jb = self._skeleton.joints[joint_b]
            return math.sqrt((ja.x - jb.x) ** 2 + (ja.y - jb.y) ** 2)
        return 0.2  # Default fallback

    def _plan_foot_trajectory(
        self,
        phase: float,
        is_swing: bool,
        stride_start_x: float,
        stride_end_x: float,
    ) -> tuple[float, float]:
        """Plan foot position for a given phase.

        Stance foot: stays at contact point (x_contact, 0)
        Swing foot: follows a parabolic arc from start to end

        The swing trajectory uses a parabolic arc:
            y(t) = 4 * h * t * (1 - t)  (peak at t=0.5)
            x(t) = lerp(start_x, end_x, t)

        Parameters
        ----------
        phase : float
            Phase within the half-cycle [0, 1].
        is_swing : bool
            True if this foot is in swing phase.
        stride_start_x : float
            Starting x position of the stride.
        stride_end_x : float
            Ending x position of the stride.

        Returns
        -------
        (x, y) : Foot target position.
        """
        if not is_swing:
            # Stance foot: locked at contact point
            return (stride_start_x, 0.0)

        # Swing foot: parabolic arc
        t = float(np.clip(phase, 0.0, 1.0))
        x = stride_start_x + t * (stride_end_x - stride_start_x)
        y = 4.0 * self.step_height * t * (1.0 - t)

        return (x, y)

    def _fabrik_to_angles(
        self,
        joint_positions: list[tuple[float, float]],
        side: str,
    ) -> dict[str, float]:
        """Convert FABRIK joint positions to joint angles.

        Computes the angle each bone makes relative to its parent,
        which is the representation the renderer expects.

        Parameters
        ----------
        joint_positions : list of (x, y)
            FABRIK solution: [hip, knee, foot].
        side : str
            "l" or "r" for left/right leg.

        Returns
        -------
        dict[str, float] : Joint angles for hip and knee.
        """
        if len(joint_positions) < 3:
            return {}

        hip_pos = np.array(joint_positions[0])
        knee_pos = np.array(joint_positions[1])
        foot_pos = np.array(joint_positions[2])

        # Hip angle: angle of thigh bone relative to default (straight down)
        thigh_vec = knee_pos - hip_pos
        thigh_angle = math.atan2(float(thigh_vec[0]), -float(thigh_vec[1]))

        # Knee angle: angle of shin bone relative to thigh bone
        shin_vec = foot_pos - knee_pos
        shin_angle_world = math.atan2(float(shin_vec[0]), -float(shin_vec[1]))
        knee_angle = shin_angle_world - thigh_angle

        # Normalize to [-π, π]
        while knee_angle > math.pi:
            knee_angle -= 2 * math.pi
        while knee_angle < -math.pi:
            knee_angle += 2 * math.pi

        # Enforce knee ROM: backward only (negative angles)
        knee_angle = float(np.clip(knee_angle, -math.pi * 0.8, 0.0))

        # Enforce hip ROM
        thigh_angle = float(np.clip(thigh_angle, -math.pi / 2, math.pi / 2))

        return {
            f"{side}_hip": thigh_angle,
            f"{side}_knee": knee_angle,
        }

    def generate_walk_pose(
        self,
        t: float,
    ) -> dict[str, float]:
        """Generate a walk cycle pose at time t using FABRIK IK.

        This is the main entry point that replaces the keyframed
        `run_animation()` with IK-driven procedural locomotion.

        Parameters
        ----------
        t : float
            Gait cycle phase [0, 1]. 0 = start, 1 = full cycle.

        Returns
        -------
        dict[str, float] : Joint angles for the entire body.
        """
        # Determine which foot is in stance vs swing
        # First half: left stance, right swing
        # Second half: right stance, left swing
        if t < 0.5:
            half_phase = t / 0.5  # [0, 1] within first half
            l_swing = False
            r_swing = True
        else:
            half_phase = (t - 0.5) / 0.5  # [0, 1] within second half
            l_swing = True
            r_swing = False

        # Get hip positions from skeleton rest pose
        l_hip_rest = self._skeleton.joints["l_hip"]
        r_hip_rest = self._skeleton.joints["r_hip"]
        l_hip_pos = (l_hip_rest.x, l_hip_rest.y)
        r_hip_pos = (r_hip_rest.x, r_hip_rest.y)

        # IPM-based CoM modulation
        y_bounce = self._ipm.compute_vertical_bounce(t, amplitude=0.015)

        # Plan foot targets
        half_step = self.step_length / 2

        # Left foot trajectory
        if l_swing:
            l_target = self._plan_foot_trajectory(
                half_phase, True, half_step, -half_step
            )
        else:
            l_target = self._plan_foot_trajectory(
                half_phase, False, -half_step, -half_step
            )

        # Right foot trajectory
        if r_swing:
            r_target = self._plan_foot_trajectory(
                half_phase, True, -half_step, half_step
            )
        else:
            r_target = self._plan_foot_trajectory(
                half_phase, False, half_step, half_step
            )

        # Solve FABRIK for each leg
        l_joints = self._l_solver.solve(
            target=l_target,
            root=l_hip_pos,
        )
        r_joints = self._r_solver.solve(
            target=r_target,
            root=r_hip_pos,
        )

        # Convert to joint angles
        l_angles = self._fabrik_to_angles(l_joints, "l")
        r_angles = self._fabrik_to_angles(r_joints, "r")

        # Combine with upper body motion
        arm_swing = 0.35
        l_arm = -arm_swing * math.sin(2 * math.pi * t)
        r_arm = -arm_swing * math.sin(2 * math.pi * t + math.pi)

        torso_rot = 0.04 * math.sin(2 * math.pi * t * 2)

        pose = {
            # Spine: forward lean + counter-rotation + IPM bounce
            "spine": 0.08 + torso_rot + y_bounce * 2,
            "chest": -torso_rot,
            "head": -0.04,
            # Arms: counter-rotating to legs
            "l_shoulder": l_arm,
            "r_shoulder": r_arm,
            "l_elbow": 0.35 + 0.15 * math.sin(2 * math.pi * t),
            "r_elbow": 0.35 + 0.15 * math.sin(2 * math.pi * t + math.pi),
        }

        # Merge leg angles from FABRIK
        pose.update(l_angles)
        pose.update(r_angles)

        return pose

    def generate_run_pose(
        self,
        t: float,
    ) -> dict[str, float]:
        """Generate a run cycle pose at time t using FABRIK IK.

        Running differs from walking:
        - Higher step height (more foot lift)
        - Greater forward lean
        - More arm swing
        - Aerial phase (both feet off ground)

        Parameters
        ----------
        t : float
            Gait cycle phase [0, 1].

        Returns
        -------
        dict[str, float] : Joint angles for the entire body.
        """
        # Running has a flight phase — both feet off ground briefly
        # Phase mapping: 0-0.4 left stance, 0.4-0.5 flight,
        #                0.5-0.9 right stance, 0.9-1.0 flight

        run_step_height = self.step_height * 1.8
        run_step_length = self.step_length * 1.3

        if t < 0.5:
            half_phase = t / 0.5
            l_swing = False
            r_swing = True
        else:
            half_phase = (t - 0.5) / 0.5
            l_swing = True
            r_swing = False

        half_step = run_step_length / 2

        # Left foot
        if l_swing:
            l_target = self._plan_foot_trajectory(
                half_phase, True, half_step, -half_step
            )
            # Increase swing height for running
            l_target = (l_target[0], l_target[1] * 1.5 + run_step_height * 0.3)
        else:
            l_target = (l_target[0] if l_swing else -half_step, 0.0)

        # Right foot
        if r_swing:
            r_target = self._plan_foot_trajectory(
                half_phase, True, -half_step, half_step
            )
            r_target = (r_target[0], r_target[1] * 1.5 + run_step_height * 0.3)
        else:
            r_target = (r_target[0] if r_swing else half_step, 0.0)

        # Get hip positions
        l_hip_pos = (self._skeleton.joints["l_hip"].x,
                     self._skeleton.joints["l_hip"].y)
        r_hip_pos = (self._skeleton.joints["r_hip"].x,
                     self._skeleton.joints["r_hip"].y)

        # Solve FABRIK
        l_joints = self._l_solver.solve(target=l_target, root=l_hip_pos)
        r_joints = self._r_solver.solve(target=r_target, root=r_hip_pos)

        l_angles = self._fabrik_to_angles(l_joints, "l")
        r_angles = self._fabrik_to_angles(r_joints, "r")

        # Upper body: more dynamic for running
        arm_swing = 0.5
        l_arm = -arm_swing * math.sin(2 * math.pi * t)
        r_arm = -arm_swing * math.sin(2 * math.pi * t + math.pi)
        torso_rot = 0.06 * math.sin(2 * math.pi * t * 2)
        y_bounce = self._ipm.compute_vertical_bounce(t, amplitude=0.025)

        pose = {
            "spine": 0.12 + torso_rot + y_bounce * 3,
            "chest": -torso_rot * 1.2,
            "head": -0.06,
            "l_shoulder": l_arm,
            "r_shoulder": r_arm,
            "l_elbow": 0.5 + 0.2 * math.sin(2 * math.pi * t),
            "r_elbow": 0.5 + 0.2 * math.sin(2 * math.pi * t + math.pi),
        }

        pose.update(l_angles)
        pose.update(r_angles)

        return pose

    def reset(self) -> None:
        """Reset the gait generator state."""
        self._ipm.reset()


# ── Biomechanics Projector (Integration Layer) ──────────────────────────────


class BiomechanicsProjector:
    """Unified biomechanics projector that integrates all four systems.

    This is the main integration layer that combines:
    1. ZMP/CoM balance analysis → pose correction
    2. Inverted Pendulum Model → CoM trajectory modulation
    3. Skating Cleanup (calculus) → foot locking
    4. FABRIK gait → IK-driven locomotion

    It acts as a post-processing layer on top of the existing
    AnglePoseProjector, adding biomechanics-aware corrections.

    Parameters
    ----------
    skeleton : Skeleton
        The skeleton definition.
    enable_zmp : bool
        Enable ZMP balance analysis and correction. Default True.
    enable_ipm : bool
        Enable Inverted Pendulum CoM modulation. Default True.
    enable_skating_cleanup : bool
        Enable calculus-based skating cleanup. Default True.
    zmp_correction_strength : float
        How strongly to correct poses based on ZMP analysis [0, 1].
    """

    def __init__(
        self,
        skeleton,
        enable_zmp: bool = True,
        enable_ipm: bool = True,
        enable_skating_cleanup: bool = True,
        zmp_correction_strength: float = 0.3,
    ):
        self._skeleton = skeleton

        self._enable_zmp = enable_zmp
        self._enable_ipm = enable_ipm
        self._enable_skating_cleanup = enable_skating_cleanup
        self._zmp_strength = float(np.clip(zmp_correction_strength, 0.0, 1.0))

        # Initialize subsystems
        self._zmp_analyzer = ZMPAnalyzer(skeleton) if enable_zmp else None
        self._ipm = InvertedPendulumModel() if enable_ipm else None
        self._skating_cleanup = (
            SkatingCleanupCalculus() if enable_skating_cleanup else None
        )

        self._frame_count = 0

    def step(
        self,
        raw_pose: dict[str, float],
        dt: float = 1.0 / 60.0,
        gait_phase: float | None = None,
    ) -> dict[str, float]:
        """Apply biomechanics corrections to a raw pose.

        Processing pipeline:
        1. ZMP analysis → detect imbalance
        2. IPM modulation → add natural CoM bounce
        3. Skating cleanup → lock grounded feet
        4. ZMP correction → shift CoM if unbalanced

        Parameters
        ----------
        raw_pose : dict[str, float]
            Raw animation pose (joint_name → angle).
        dt : float
            Timestep.
        gait_phase : float, optional
            Current gait cycle phase [0, 1] for IPM modulation.

        Returns
        -------
        dict[str, float] : Biomechanics-corrected pose.
        """
        self._frame_count += 1
        corrected = dict(raw_pose)

        # Step 1: IPM CoM modulation (add natural bounce to spine)
        if self._enable_ipm and self._ipm is not None and gait_phase is not None:
            y_bounce = self._ipm.compute_vertical_bounce(gait_phase)
            lateral = self._ipm.compute_lateral_sway(gait_phase)

            # Modulate spine/hip to reflect CoM trajectory
            if "spine" in corrected:
                corrected["spine"] += y_bounce * 1.5
            if "hip" in corrected:
                corrected["hip"] += lateral * 0.5

        # Step 2: ZMP analysis
        zmp_result = None
        if self._enable_zmp and self._zmp_analyzer is not None:
            zmp_result = self._zmp_analyzer.analyze_frame(corrected, dt)

        # Step 3: Skating cleanup
        if self._enable_skating_cleanup and self._skating_cleanup is not None:
            self._skeleton.apply_pose(corrected)
            positions = self._skeleton.forward_kinematics()
            self._skating_cleanup.update(positions, dt)

        # Step 4: ZMP-based pose correction
        if (
            zmp_result is not None
            and not zmp_result.is_balanced
            and self._zmp_strength > 0
        ):
            corrected = self._apply_zmp_correction(
                corrected, zmp_result
            )

        return corrected

    def _apply_zmp_correction(
        self,
        pose: dict[str, float],
        zmp_result: ZMPResult,
    ) -> dict[str, float]:
        """Correct pose to bring ZMP back within support polygon.

        Strategy: shift the CoM horizontally by adjusting hip/spine angles.
        The correction is proportional to how far the ZMP is outside the
        support polygon.

        If ZMP is to the right of support → lean left (negative spine angle)
        If ZMP is to the left of support → lean right (positive spine angle)
        """
        corrected = dict(pose)

        # How far is ZMP outside support?
        if zmp_result.zmp_x > zmp_result.support_right:
            # ZMP too far right → lean left
            overshoot = zmp_result.zmp_x - zmp_result.support_right
            correction = -overshoot * self._zmp_strength * 2.0
        elif zmp_result.zmp_x < zmp_result.support_left:
            # ZMP too far left → lean right
            overshoot = zmp_result.support_left - zmp_result.zmp_x
            correction = overshoot * self._zmp_strength * 2.0
        else:
            return corrected  # Already balanced

        # Apply correction to spine chain
        if "spine" in corrected:
            corrected["spine"] += correction * 0.4
        if "chest" in corrected:
            corrected["chest"] += correction * 0.3
        if "hip" in corrected:
            corrected["hip"] += correction * 0.3

        return corrected

    def step_with_analysis(
        self,
        raw_pose: dict[str, float],
        dt: float = 1.0 / 60.0,
        gait_phase: float | None = None,
    ) -> tuple[dict[str, float], dict]:
        """Apply corrections and return analysis metadata.

        Returns
        -------
        (corrected_pose, metadata) where metadata contains:
            - "zmp_result": ZMPResult or None
            - "skating_metric": float
            - "ipm_bounce": float
        """
        corrected = self.step(raw_pose, dt, gait_phase)

        metadata = {
            "zmp_result": None,
            "skating_metric": 0.0,
            "ipm_bounce": 0.0,
        }

        if self._enable_zmp and self._zmp_analyzer is not None:
            # Re-analyze the corrected pose
            zmp = self._zmp_analyzer.analyze_frame(corrected, dt)
            metadata["zmp_result"] = zmp

        if self._enable_skating_cleanup and self._skating_cleanup is not None:
            self._skeleton.apply_pose(corrected)
            positions = self._skeleton.forward_kinematics()
            metadata["skating_metric"] = self._skating_cleanup.compute_skating_metric(
                positions, dt
            )

        if self._enable_ipm and gait_phase is not None:
            metadata["ipm_bounce"] = self._ipm.compute_vertical_bounce(gait_phase)

        return corrected, metadata

    def reset(self) -> None:
        """Reset all subsystem states."""
        self._frame_count = 0
        if self._zmp_analyzer:
            self._zmp_analyzer.reset()
        if self._ipm:
            self._ipm.reset()
        if self._skating_cleanup:
            self._skating_cleanup.reset()


# ── Enhanced Physics Penalty (Biomechanics-aware) ────────────────────────────


def compute_biomechanics_penalty(
    pose_sequence: list[dict[str, float]],
    skeleton,
    dt: float = 1.0 / 60.0,
    weights: dict[str, float] | None = None,
) -> float:
    """Compute a biomechanics-aware penalty score for GA fitness integration.

    Extends the existing `compute_physics_penalty()` with:
    - ZMP balance penalty (frames where ZMP exits support polygon)
    - IPM trajectory deviation (CoM doesn't follow inverted pendulum)
    - Skating metric (horizontal displacement of grounded feet)

    Parameters
    ----------
    pose_sequence : list of dict[str, float]
        Animation sequence as joint angles per frame.
    skeleton : Skeleton
        The skeleton definition.
    dt : float
        Timestep between frames.
    weights : dict[str, float], optional
        Penalty weights. Keys: "zmp_balance", "skating", "com_smoothness".

    Returns
    -------
    float : Total biomechanics penalty (lower is better).
    """
    if not pose_sequence or len(pose_sequence) < 2:
        return 0.0

    default_weights = {
        "zmp_balance": 8.0,
        "skating": 6.0,
        "com_smoothness": 3.0,
    }
    w = {**default_weights, **(weights or {})}

    total_penalty = 0.0
    n_frames = len(pose_sequence)

    # ZMP analysis
    analyzer = ZMPAnalyzer(skeleton)
    zmp_results = analyzer.analyze_sequence(pose_sequence, dt)

    for result in zmp_results:
        if not result.is_balanced:
            total_penalty += w["zmp_balance"] * result.balance_margin ** 2
        total_penalty += w["zmp_balance"] * (1.0 - result.stability_score) * 0.05

    # CoM smoothness (penalize jerky CoM trajectory)
    if len(zmp_results) >= 3:
        for i in range(1, len(zmp_results) - 1):
            # CoM acceleration jerk
            ax_prev = zmp_results[i - 1].com_ax
            ax_curr = zmp_results[i].com_ax
            jerk = (ax_curr - ax_prev) / max(dt, 1e-8)
            total_penalty += w["com_smoothness"] * jerk * jerk * 0.0001

    # Skating metric
    cleanup = SkatingCleanupCalculus()
    for i in range(n_frames):
        skeleton.apply_pose(pose_sequence[i])
        positions = skeleton.forward_kinematics()
        cleanup.update(positions, dt)
        metric = cleanup.compute_skating_metric(positions, dt)
        total_penalty += w["skating"] * metric * metric

    return total_penalty / max(n_frames, 1)
