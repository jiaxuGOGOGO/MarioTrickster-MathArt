"""Physics-based animation components.

Implements spring-damper systems and FABRIK IK solver for
procedural secondary animation (capes, hair, accessories).

Mathematical foundations:
  - Spring-Damper: Hooke's Law F = -kx - cv (second-order ODE)
  - FABRIK IK: Forward And Backward Reaching Inverse Kinematics
  - Verlet Integration: x(t+dt) = 2x(t) - x(t-dt) + a(t)*dt²

Distilled knowledge applied:
  - physics_sim.md: spring_k range 5-50, damping_c range 1-10
  - anatomy.md: joint ROM constraints applied during IK solve
  - animation.md: secondary animation follows primary with 2-4 frame delay

Usage::

    from mathart.animation.physics import SpringDamper, FABRIKSolver
    spring = SpringDamper(spring_k=15.0, damping_c=4.0, mass=1.0)
    pos = spring.step(target=(1.0, 0.0), dt=1/60)

    solver = FABRIKSolver(chain_lengths=[0.3, 0.25, 0.2])
    angles = solver.solve(target=(0.5, 0.8))
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np


# ── Spring-Damper System ──

@dataclass
class SpringDamperState:
    """State of a 2D spring-damper particle."""
    position: np.ndarray = field(default_factory=lambda: np.zeros(2))
    velocity: np.ndarray = field(default_factory=lambda: np.zeros(2))


class SpringDamper:
    """2D Spring-Damper system for secondary animation.

    Simulates a mass attached to a spring, used for accessories like
    capes, hair, and weapon attachments that follow the character body.

    The equation of motion is:
        m * a = -k * (x - x_target) - c * v

    where:
        m = mass
        k = spring stiffness (Hooke's constant)
        c = damping coefficient
        x = current position
        x_target = target/rest position
        v = velocity

    Parameters
    ----------
    spring_k : float
        Spring stiffness. Higher = snappier response.
        Distilled range: 5-50 (knowledge/physics_sim.md)
    damping_c : float
        Damping coefficient. Higher = less oscillation.
        Critical damping: c = 2 * sqrt(k * m)
        Distilled range: 1-10
    mass : float
        Simulated mass. Higher = more inertia.
    """

    def __init__(
        self,
        spring_k: float = 15.0,
        damping_c: float = 4.0,
        mass: float = 1.0,
    ):
        # Clamp to distilled safe ranges
        self.spring_k = float(np.clip(spring_k, 1.0, 200.0))
        self.damping_c = float(np.clip(damping_c, 0.1, 50.0))
        self.mass = float(np.clip(mass, 0.01, 100.0))
        self._state = SpringDamperState()

    @property
    def critical_damping(self) -> float:
        """Critical damping coefficient (no oscillation)."""
        return 2.0 * math.sqrt(self.spring_k * self.mass)

    @property
    def damping_ratio(self) -> float:
        """Damping ratio ζ = c / (2*sqrt(k*m)).
        ζ < 1: underdamped (oscillates)
        ζ = 1: critically damped (fastest no-oscillation)
        ζ > 1: overdamped (slow return)
        """
        return self.damping_c / self.critical_damping

    def reset(self, position: tuple[float, float] = (0.0, 0.0)) -> None:
        """Reset state to a given position with zero velocity."""
        self._state.position = np.array(position, dtype=float)
        self._state.velocity = np.zeros(2)

    def step(
        self,
        target: tuple[float, float],
        dt: float = 1.0 / 60.0,
    ) -> tuple[float, float]:
        """Advance simulation by one timestep.

        Parameters
        ----------
        target : (x, y)
            Target/rest position (typically the attachment point on the character).
        dt : float
            Timestep in seconds (default 1/60 for 60fps).

        Returns
        -------
        (x, y) : current position of the spring particle
        """
        target_arr = np.array(target, dtype=float)

        # Spring force: F_spring = -k * displacement
        displacement = self._state.position - target_arr
        f_spring = -self.spring_k * displacement

        # Damping force: F_damp = -c * velocity
        f_damp = -self.damping_c * self._state.velocity

        # Total force → acceleration
        acceleration = (f_spring + f_damp) / self.mass

        # Semi-implicit Euler integration (more stable than explicit Euler)
        self._state.velocity += acceleration * dt
        self._state.position += self._state.velocity * dt

        return (float(self._state.position[0]), float(self._state.position[1]))

    def simulate(
        self,
        targets: list[tuple[float, float]],
        dt: float = 1.0 / 60.0,
    ) -> list[tuple[float, float]]:
        """Simulate a sequence of target positions.

        Parameters
        ----------
        targets : list of (x, y)
            Sequence of target positions (one per frame).
        dt : float
            Timestep per frame.

        Returns
        -------
        list of (x, y) : simulated positions for each frame
        """
        positions = []
        for target in targets:
            pos = self.step(target, dt)
            positions.append(pos)
        return positions


# ── FABRIK Inverse Kinematics ──

class FABRIKSolver:
    """FABRIK 2D Inverse Kinematics solver.

    FABRIK (Forward And Backward Reaching Inverse Kinematics) is an
    iterative IK algorithm that is fast, stable, and handles joint
    constraints naturally.

    Algorithm:
    1. Forward pass: move end effector to target, pull chain forward
    2. Backward pass: fix root, push chain backward
    3. Repeat until convergence or max_iterations reached

    Parameters
    ----------
    chain_lengths : list[float]
        Lengths of each bone in the chain (root to tip).
    joint_constraints : list[tuple[float, float]] or None
        Min/max angle (radians) for each joint. None = unconstrained.
        Distilled from anatomy.md ROM values.
    max_iterations : int
        Maximum FABRIK iterations per solve.
    tolerance : float
        Convergence tolerance (distance to target).
    """

    def __init__(
        self,
        chain_lengths: list[float],
        joint_constraints: Optional[list[tuple[float, float]]] = None,
        max_iterations: int = 10,
        tolerance: float = 0.001,
    ):
        self.chain_lengths = list(chain_lengths)
        self.n_joints = len(chain_lengths) + 1  # n bones → n+1 joints
        self.joint_constraints = joint_constraints
        self.max_iterations = max_iterations
        self.tolerance = tolerance
        self.total_length = sum(chain_lengths)

        # Initialize joint positions along Y axis
        self._joints = np.zeros((self.n_joints, 2))
        y = 0.0
        for i, length in enumerate(chain_lengths):
            y += length
            self._joints[i + 1] = [0.0, y]

    def set_root(self, position: tuple[float, float]) -> None:
        """Set the root joint position."""
        offset = np.array(position) - self._joints[0]
        self._joints += offset

    def solve(
        self,
        target: tuple[float, float],
        root: Optional[tuple[float, float]] = None,
    ) -> list[tuple[float, float]]:
        """Solve IK for a target position.

        Parameters
        ----------
        target : (x, y)
            Target position for the end effector (tip of chain).
        root : (x, y), optional
            Root position (if None, uses current root position).

        Returns
        -------
        list of (x, y) : joint positions [root, ..., tip]
        """
        if root is not None:
            self.set_root(root)

        target_arr = np.array(target, dtype=float)
        root_pos = self._joints[0].copy()

        # Check if target is reachable
        dist_to_target = np.linalg.norm(target_arr - root_pos)
        if dist_to_target > self.total_length:
            # Target out of reach: stretch chain toward target
            for i in range(self.n_joints - 1):
                direction = (target_arr - self._joints[i])
                norm = np.linalg.norm(direction)
                if norm > 1e-8:
                    direction /= norm
                self._joints[i + 1] = self._joints[i] + direction * self.chain_lengths[i]
            return [(float(j[0]), float(j[1])) for j in self._joints]

        # FABRIK iterations
        for _ in range(self.max_iterations):
            # Check convergence
            if np.linalg.norm(self._joints[-1] - target_arr) < self.tolerance:
                break

            # Forward pass: move tip to target, pull chain
            self._joints[-1] = target_arr.copy()
            for i in range(self.n_joints - 2, -1, -1):
                direction = self._joints[i] - self._joints[i + 1]
                norm = np.linalg.norm(direction)
                if norm > 1e-8:
                    direction /= norm
                self._joints[i] = self._joints[i + 1] + direction * self.chain_lengths[i]

            # Backward pass: fix root, push chain
            self._joints[0] = root_pos.copy()
            for i in range(self.n_joints - 1):
                direction = self._joints[i + 1] - self._joints[i]
                norm = np.linalg.norm(direction)
                if norm > 1e-8:
                    direction /= norm
                self._joints[i + 1] = self._joints[i] + direction * self.chain_lengths[i]

            # Apply joint constraints if provided
            if self.joint_constraints:
                self._apply_constraints()

        return [(float(j[0]), float(j[1])) for j in self._joints]

    def _apply_constraints(self) -> None:
        """Apply joint angle constraints after FABRIK solve."""
        if not self.joint_constraints:
            return

        for i in range(1, self.n_joints - 1):
            if i - 1 >= len(self.joint_constraints):
                break
            min_angle, max_angle = self.joint_constraints[i - 1]

            # Compute current joint angle relative to parent bone
            parent_dir = self._joints[i] - self._joints[i - 1]
            child_dir = self._joints[i + 1] - self._joints[i]

            parent_angle = math.atan2(float(parent_dir[1]), float(parent_dir[0]))
            child_angle = math.atan2(float(child_dir[1]), float(child_dir[0]))
            relative_angle = child_angle - parent_angle

            # Normalize to [-pi, pi]
            while relative_angle > math.pi:
                relative_angle -= 2 * math.pi
            while relative_angle < -math.pi:
                relative_angle += 2 * math.pi

            # Clamp to constraint range
            clamped = float(np.clip(relative_angle, min_angle, max_angle))

            if abs(clamped - relative_angle) > 1e-6:
                # Recompute child position with clamped angle
                new_child_angle = parent_angle + clamped
                bone_length = self.chain_lengths[i]
                self._joints[i + 1] = self._joints[i] + np.array([
                    math.cos(new_child_angle) * bone_length,
                    math.sin(new_child_angle) * bone_length,
                ])

    def get_joint_angles(self) -> list[float]:
        """Get current joint angles in radians."""
        angles = []
        for i in range(self.n_joints - 1):
            direction = self._joints[i + 1] - self._joints[i]
            angle = math.atan2(float(direction[1]), float(direction[0]))
            angles.append(angle)
        return angles


# ── Noise-based Procedural Animation ──

class PerlinAnimator:
    """Procedural animation using layered Perlin-like noise.

    Generates smooth, organic-looking motion for idle animations,
    ambient effects, and secondary motion.

    Parameters
    ----------
    frequency : float
        Base frequency of the noise (cycles per second).
    amplitude : float
        Base amplitude of the motion.
    octaves : int
        Number of noise octaves (more = more detail).
    persistence : float
        Amplitude scaling per octave (0.5 = halved each octave).
    """

    def __init__(
        self,
        frequency: float = 1.0,
        amplitude: float = 0.1,
        octaves: int = 3,
        persistence: float = 0.5,
        seed: int = 42,
    ):
        self.frequency = frequency
        self.amplitude = amplitude
        self.octaves = octaves
        self.persistence = persistence
        self._rng = np.random.default_rng(seed)
        # Pre-generate gradient table
        self._gradients = self._rng.uniform(-1, 1, (256, 2))
        self._perm = self._rng.permutation(256)

    def sample(self, t: float) -> float:
        """Sample 1D noise at time t.

        Returns a value approximately in [-amplitude, amplitude].
        """
        value = 0.0
        amp = self.amplitude
        freq = self.frequency

        for _ in range(self.octaves):
            value += self._noise1d(t * freq) * amp
            amp *= self.persistence
            freq *= 2.0

        return value

    def sample_2d(self, t: float) -> tuple[float, float]:
        """Sample 2D noise at time t (for 2D motion)."""
        x = self.sample(t)
        y = self.sample(t + 100.0)  # Offset for independence
        return (x, y)

    def _noise1d(self, x: float) -> float:
        """Simple 1D gradient noise."""
        x0 = int(math.floor(x)) & 255
        x1 = (x0 + 1) & 255
        dx = x - math.floor(x)

        # Smooth step
        t = dx * dx * dx * (dx * (dx * 6 - 15) + 10)

        g0 = float(self._gradients[self._perm[x0], 0])
        g1 = float(self._gradients[self._perm[x1], 0])

        return g0 * dx + (g1 * (dx - 1) - g0 * dx) * t
