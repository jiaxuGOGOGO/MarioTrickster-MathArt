"""XPBD (Extended Position-Based Dynamics) unified rigid-soft coupling solver.

This module implements a full XPBD solver that places rigid-body centres of mass
and soft-body chain nodes into the **same constraint pool**, achieving true
two-way coupling through inverse-mass weighting.  When a heavy weapon swings to
its limit, the solver automatically generates a **reaction impulse** that pulls
the character's centre of mass, producing realistic stagger and force
compensation — exactly as Newton's Third Law demands.

Research distillation:
  - Macklin, Müller, Chentanez, *XPBD: Position-Based Simulation of Compliant
    Constrained Dynamics* (MIG / SIGGRAPH 2016).
  - Müller, Macklin, Chentanez, Jeschke, Kim, *Detailed Rigid Body Simulation
    with Extended Position Based Dynamics* (SCA / SIGGRAPH 2020).
  - Matthias Müller, *Ten Minute Physics* tutorials 09/10/14/15/22/25.

Core design choices for MarioTrickster-MathArt:
  1. Pure NumPy vectorised arrays — no external C/GPU dependency.
  2. Compliance α replaces stiffness; α̃ = α / Δt² decouples from time step and
     iteration count.
  3. Lagrange multiplier λ accumulates per constraint for force estimation.
  4. Rigid body CoM and soft chain nodes share the same particle array; inverse
     mass weighting automatically distributes corrections bidirectionally.
  5. Sub-stepping divides Δt for stability; each sub-step runs the full
     predict → solve → update loop.
  6. Backward-compatible with existing SecondaryChainProjector interface.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional, Sequence

import numpy as np

_EPS = 1e-8


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class ParticleKind(Enum):
    """Distinguish particle roles inside the unified solver."""
    RIGID_COM = auto()      # Character rigid-body centre of mass
    SOFT_NODE = auto()      # Soft-body chain node (cape, hair, weapon)
    KINEMATIC = auto()       # Pinned / animated (inv_mass = 0)


class ConstraintKind(Enum):
    """Supported XPBD constraint types."""
    DISTANCE = auto()       # |x_i - x_j| = d
    CONTACT = auto()        # Unilateral penetration correction
    SELF_COLLISION = auto() # Particle-particle minimum separation
    ATTACHMENT = auto()     # Pin soft root to rigid CoM
    BENDING = auto()        # Angular stiffness between 3 consecutive nodes


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class XPBDSolverConfig:
    """Top-level solver configuration."""

    sub_steps: int = 4
    solver_iterations: int = 8
    gravity: tuple[float, float] = (0.0, -9.81)
    default_compliance: float = 0.0        # 0 = perfectly rigid
    default_damping: float = 0.0           # Rayleigh damping compliance
    velocity_damping: float = 0.98         # Retention for component-relative velocity only
    max_velocity: float = 50.0             # Tunnelling guard
    enable_self_collision: bool = True
    self_collision_radius: float = 0.015
    friction_coefficient: float = 0.3
    enable_two_way_coupling: bool = True   # THE key switch


@dataclass(frozen=True)
class XPBDChainPreset:
    """Preset for a soft-body chain (cape, hair, weapon ribbon)."""

    name: str
    anchor_joint: str
    segment_count: int = 6
    segment_length: float = 0.08
    compliance: float = 1e-6               # Near-rigid distance constraints
    damping_compliance: float = 1e-4       # Slight damping
    bending_compliance: float = 1e-3       # Moderate bending resistance
    particle_mass: float = 0.1             # kg per node
    tip_mass_scale: float = 1.35
    particle_radius: float = 0.015
    rest_direction: tuple[float, float] = (0.0, -1.0)
    anchor_offset: tuple[float, float] = (0.0, 0.0)
    body_collision_joints: tuple[str, ...] = ("head", "neck", "chest", "spine", "hip")
    body_collision_radii: tuple[float, ...] = (0.10, 0.08, 0.12, 0.10, 0.09)
    ground_y: Optional[float] = 0.0


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

@dataclass
class XPBDDiagnostics:
    """Per-frame solver diagnostics."""

    total_particles: int = 0
    total_constraints: int = 0
    mean_constraint_error: float = 0.0
    max_constraint_error: float = 0.0
    rigid_com_displacement: float = 0.0    # How much the rigid body moved
    reaction_impulse_magnitude: float = 0.0
    self_collision_count: int = 0
    contact_collision_count: int = 0
    sub_steps_used: int = 0
    iterations_per_substep: int = 0
    max_velocity_observed: float = 0.0
    energy_estimate: float = 0.0

    def to_dict(self) -> dict[str, float | int]:
        return {
            "total_particles": self.total_particles,
            "total_constraints": self.total_constraints,
            "mean_constraint_error": float(self.mean_constraint_error),
            "max_constraint_error": float(self.max_constraint_error),
            "rigid_com_displacement": float(self.rigid_com_displacement),
            "reaction_impulse_magnitude": float(self.reaction_impulse_magnitude),
            "self_collision_count": self.self_collision_count,
            "contact_collision_count": self.contact_collision_count,
            "sub_steps_used": self.sub_steps_used,
            "iterations_per_substep": self.iterations_per_substep,
            "max_velocity_observed": float(self.max_velocity_observed),
            "energy_estimate": float(self.energy_estimate),
        }


# ---------------------------------------------------------------------------
# Constraint representation
# ---------------------------------------------------------------------------

@dataclass
class XPBDConstraint:
    """A single XPBD constraint in the unified pool."""

    kind: ConstraintKind
    particle_indices: tuple[int, ...]       # Which particles are involved
    rest_value: float = 0.0                 # Rest length / angle / etc.
    compliance: float = 0.0                 # α (inverse stiffness)
    damping_compliance: float = 0.0         # β for Rayleigh damping
    lambda_accumulated: float = 0.0         # Running Lagrange multiplier


# ---------------------------------------------------------------------------
# Core XPBD Solver
# ---------------------------------------------------------------------------

class XPBDSolver:
    """Unified XPBD constraint solver with two-way rigid-soft coupling.

    The solver maintains flat NumPy arrays for positions, previous positions,
    inverse masses, and velocities.  Constraints are stored in a list and
    solved via Gauss-Seidel iteration with compliance-regularised Lagrange
    multiplier updates (Macklin & Müller 2016, Eq 18).

    Two-way coupling is achieved by placing the rigid-body CoM as a particle
    with finite inverse mass (1/m_body) alongside soft-body nodes.  When a
    distance constraint between them generates a correction Δx, the solver
    distributes it proportionally to inverse masses — automatically producing
    Newton's Third Law reaction forces.
    """

    def __init__(self, config: Optional[XPBDSolverConfig] = None):
        self.config = config or XPBDSolverConfig()
        # Particle state arrays (grow dynamically)
        self._positions = np.zeros((0, 2), dtype=np.float64)
        self._prev_positions = np.zeros((0, 2), dtype=np.float64)
        self._predicted = np.zeros((0, 2), dtype=np.float64)
        self._inv_masses = np.zeros(0, dtype=np.float64)
        self._velocities = np.zeros((0, 2), dtype=np.float64)
        self._kinds = np.zeros(0, dtype=np.int32)
        self._radii = np.zeros(0, dtype=np.float64)
        # Constraint pool
        self._constraints: list[XPBDConstraint] = []
        # Tracking
        self._particle_count = 0
        self._last_diagnostics = XPBDDiagnostics()
        self._rigid_com_index: Optional[int] = None
        self._rigid_com_initial: Optional[np.ndarray] = None

    # -----------------------------------------------------------------------
    # Particle management
    # -----------------------------------------------------------------------

    def add_particle(
        self,
        position: tuple[float, float],
        mass: float,
        kind: ParticleKind = ParticleKind.SOFT_NODE,
        radius: float = 0.015,
    ) -> int:
        """Add a particle and return its index."""
        idx = self._particle_count
        new_pos = np.array([[position[0], position[1]]], dtype=np.float64)
        inv_m = 0.0 if (mass <= 0 or kind == ParticleKind.KINEMATIC) else 1.0 / mass

        if idx == 0:
            self._positions = new_pos.copy()
            self._prev_positions = new_pos.copy()
            self._predicted = new_pos.copy()
            self._inv_masses = np.array([inv_m], dtype=np.float64)
            self._velocities = np.zeros((1, 2), dtype=np.float64)
            self._kinds = np.array([kind.value], dtype=np.int32)
            self._radii = np.array([radius], dtype=np.float64)
        else:
            self._positions = np.vstack([self._positions, new_pos])
            self._prev_positions = np.vstack([self._prev_positions, new_pos])
            self._predicted = np.vstack([self._predicted, new_pos])
            self._inv_masses = np.append(self._inv_masses, inv_m)
            self._velocities = np.vstack([self._velocities, np.zeros((1, 2))])
            self._kinds = np.append(self._kinds, kind.value)
            self._radii = np.append(self._radii, radius)

        if kind == ParticleKind.RIGID_COM:
            self._rigid_com_index = idx

        self._particle_count += 1
        return idx

    def set_rigid_com(self, index: int, mass: float) -> None:
        """Designate a particle as the rigid-body centre of mass."""
        self._kinds[index] = ParticleKind.RIGID_COM.value
        self._inv_masses[index] = 1.0 / max(mass, _EPS) if self.config.enable_two_way_coupling else 0.0
        self._rigid_com_index = index

    def set_kinematic(self, index: int) -> None:
        """Pin a particle (infinite mass)."""
        self._kinds[index] = ParticleKind.KINEMATIC.value
        self._inv_masses[index] = 0.0

    def update_position(self, index: int, position: tuple[float, float]) -> None:
        """Externally set a particle position (for kinematic driving)."""
        self._positions[index] = [position[0], position[1]]
        self._prev_positions[index] = [position[0], position[1]]

    # -----------------------------------------------------------------------
    # Constraint management
    # -----------------------------------------------------------------------

    def add_distance_constraint(
        self,
        i: int,
        j: int,
        rest_length: Optional[float] = None,
        compliance: float = 0.0,
        damping: float = 0.0,
    ) -> int:
        """Add a distance constraint between particles i and j."""
        if rest_length is None:
            rest_length = float(np.linalg.norm(self._positions[i] - self._positions[j]))
        c = XPBDConstraint(
            kind=ConstraintKind.DISTANCE,
            particle_indices=(i, j),
            rest_value=max(rest_length, _EPS),
            compliance=compliance,
            damping_compliance=damping,
        )
        self._constraints.append(c)
        return len(self._constraints) - 1

    def add_attachment_constraint(
        self,
        soft_index: int,
        rigid_index: int,
        compliance: float = 0.0,
    ) -> int:
        """Attach a soft node to a rigid CoM (zero rest length = co-located)."""
        rest = float(np.linalg.norm(
            self._positions[soft_index] - self._positions[rigid_index]
        ))
        c = XPBDConstraint(
            kind=ConstraintKind.ATTACHMENT,
            particle_indices=(soft_index, rigid_index),
            rest_value=rest,
            compliance=compliance,
        )
        self._constraints.append(c)
        return len(self._constraints) - 1

    def add_bending_constraint(
        self,
        i: int,
        j: int,
        k: int,
        compliance: float = 1e-3,
    ) -> int:
        """Add a bending constraint between three consecutive particles."""
        rest_angle = self._compute_angle(i, j, k)
        c = XPBDConstraint(
            kind=ConstraintKind.BENDING,
            particle_indices=(i, j, k),
            rest_value=rest_angle,
            compliance=compliance,
        )
        self._constraints.append(c)
        return len(self._constraints) - 1

    def clear_transient_constraints(self) -> None:
        """Remove contact and self-collision constraints (regenerated each step)."""
        self._constraints = [
            c for c in self._constraints
            if c.kind not in (ConstraintKind.CONTACT, ConstraintKind.SELF_COLLISION)
        ]

    # -----------------------------------------------------------------------
    # Angle computation helper
    # -----------------------------------------------------------------------

    def _compute_angle(self, i: int, j: int, k: int) -> float:
        """Compute the angle at particle j formed by i-j-k."""
        a = self._positions[i] - self._positions[j]
        b = self._positions[k] - self._positions[j]
        cos_angle = np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + _EPS)
        return float(np.arccos(np.clip(cos_angle, -1.0, 1.0)))

    # -----------------------------------------------------------------------
    # Core XPBD simulation step
    # -----------------------------------------------------------------------

    def step(self, dt: float = 1.0 / 60.0) -> XPBDDiagnostics:
        """Run one full XPBD time step with sub-stepping.

        Algorithm (per sub-step):
          1. Predict positions: x̃ = x + Δt·v + 0.5·Δt²·a_ext
          2. Initialise solve: x_i ← x̃, λ ← 0
          3. For each solver iteration:
             a. For each constraint: compute Δλ (Eq 18), Δx (Eq 17)
             b. Update λ and x
          4. Update velocities: v = (x - x_prev) / Δt
          5. Apply Galilean-invariant component-relative damping and clamping
        """
        n = self._particle_count
        if n == 0:
            return self._last_diagnostics

        sub_dt = dt / max(self.config.sub_steps, 1)
        gravity = np.array(self.config.gravity, dtype=np.float64)

        # Track rigid CoM for diagnostics
        if self._rigid_com_index is not None:
            self._rigid_com_initial = self._positions[self._rigid_com_index].copy()

        total_constraint_errors = []
        self_collision_count = 0
        contact_collision_count = 0
        max_vel = 0.0

        for _sub in range(self.config.sub_steps):
            # --- 1. Predict ---
            external_velocities = self._velocities.copy()
            gravity_step = gravity * sub_dt
            gravity_drift = 0.5 * gravity * (sub_dt * sub_dt)
            for i in range(n):
                if self._inv_masses[i] <= 0:
                    self._predicted[i] = self._positions[i].copy()
                    continue
                external_velocities[i] = self._velocities[i] + gravity_step
                self._predicted[i] = (
                    self._positions[i]
                    + self._velocities[i] * sub_dt
                    + gravity_drift
                )

            # --- 2. Initialise solve ---
            solve_x = self._predicted.copy()
            for c in self._constraints:
                c.lambda_accumulated = 0.0

            # --- 3. Gauss-Seidel iterations ---
            for _iter in range(self.config.solver_iterations):
                for c in self._constraints:
                    if c.kind == ConstraintKind.DISTANCE or c.kind == ConstraintKind.ATTACHMENT:
                        err = self._solve_distance_constraint(c, solve_x, sub_dt)
                        total_constraint_errors.append(abs(err))
                    elif c.kind == ConstraintKind.BENDING:
                        err = self._solve_bending_constraint(c, solve_x, sub_dt)
                        total_constraint_errors.append(abs(err))
                    elif c.kind == ConstraintKind.CONTACT:
                        err = self._solve_contact_constraint(c, solve_x, sub_dt)
                        if abs(err) > _EPS:
                            contact_collision_count += 1
                        total_constraint_errors.append(abs(err))
                    elif c.kind == ConstraintKind.SELF_COLLISION:
                        err = self._solve_distance_constraint(c, solve_x, sub_dt)
                        if abs(err) > _EPS:
                            self_collision_count += 1
                        total_constraint_errors.append(abs(err))

            # --- 4. Update velocities and positions ---
            candidate_velocities = external_velocities + (solve_x - self._predicted) / sub_dt
            damped_velocities = self._apply_component_relative_damping(candidate_velocities)
            for i in range(n):
                if self._inv_masses[i] <= 0:
                    continue
                new_vel = damped_velocities[i]
                # Velocity clamping (tunnelling guard)
                speed = float(np.linalg.norm(new_vel))
                if speed > self.config.max_velocity:
                    new_vel = new_vel / speed * self.config.max_velocity
                max_vel = max(max_vel, speed)
                self._velocities[i] = new_vel
                self._prev_positions[i] = self._positions[i].copy()
                self._positions[i] = solve_x[i].copy()

        # --- Diagnostics ---
        rigid_disp = 0.0
        reaction_impulse = 0.0
        if self._rigid_com_index is not None and self._rigid_com_initial is not None:
            delta = self._positions[self._rigid_com_index] - self._rigid_com_initial
            rigid_disp = float(np.linalg.norm(delta))
            if self._inv_masses[self._rigid_com_index] > 0:
                rigid_mass = 1.0 / self._inv_masses[self._rigid_com_index]
                reaction_impulse = rigid_mass * rigid_disp / max(dt, _EPS)

        errors_arr = np.array(total_constraint_errors) if total_constraint_errors else np.zeros(1)
        energy = 0.5 * float(np.sum(
            np.sum(self._velocities ** 2, axis=1) / np.maximum(self._inv_masses, _EPS)
        ))

        self._last_diagnostics = XPBDDiagnostics(
            total_particles=n,
            total_constraints=len(self._constraints),
            mean_constraint_error=float(np.mean(errors_arr)),
            max_constraint_error=float(np.max(errors_arr)),
            rigid_com_displacement=rigid_disp,
            reaction_impulse_magnitude=reaction_impulse,
            self_collision_count=self_collision_count,
            contact_collision_count=contact_collision_count,
            sub_steps_used=self.config.sub_steps,
            iterations_per_substep=self.config.solver_iterations,
            max_velocity_observed=max_vel,
            energy_estimate=energy,
        )
        return self._last_diagnostics

    # -----------------------------------------------------------------------
    # Velocity post-processing
    # -----------------------------------------------------------------------

    def _apply_component_relative_damping(self, candidate_velocities: np.ndarray) -> np.ndarray:
        """Apply damping only to internal relative motion, never to rigid translation.

        The previous implementation multiplied every particle velocity by
        ``config.velocity_damping``. That makes free-fall deviate from the
        analytical solution because gravity-induced rigid translation is damped
        together with genuine internal constraint motion.

        To preserve Galilean invariance we now compute a mass-weighted velocity
        centroid for each internal constraint-connected component (distance /
        attachment / bending constraints only) and damp only the velocity
        residual relative to that component velocity. Single-particle components
        are left untouched, so a free particle under gravity reproduces the
        semi-implicit Euler baseline exactly.
        """
        retention = float(self.config.velocity_damping)
        if retention >= 1.0 or candidate_velocities.size == 0:
            return candidate_velocities.copy()

        damped = candidate_velocities.copy()
        parent = list(range(self._particle_count))

        def find(idx: int) -> int:
            while parent[idx] != idx:
                parent[idx] = parent[parent[idx]]
                idx = parent[idx]
            return idx

        def union(a: int, b: int) -> None:
            ra = find(a)
            rb = find(b)
            if ra != rb:
                parent[rb] = ra

        for c in self._constraints:
            if c.kind not in {
                ConstraintKind.DISTANCE,
                ConstraintKind.ATTACHMENT,
                ConstraintKind.BENDING,
            }:
                continue
            indices = c.particle_indices
            if len(indices) < 2:
                continue
            root = indices[0]
            for idx in indices[1:]:
                union(root, idx)

        components: dict[int, list[int]] = {}
        for idx in range(self._particle_count):
            if self._inv_masses[idx] <= 0.0:
                continue
            components.setdefault(find(idx), []).append(idx)

        for members in components.values():
            if len(members) <= 1:
                continue
            member_idx = np.array(members, dtype=np.int32)
            masses = 1.0 / np.maximum(self._inv_masses[member_idx], _EPS)
            total_mass = float(np.sum(masses))
            if total_mass <= _EPS:
                continue
            component_velocity = np.sum(
                damped[member_idx] * masses[:, None],
                axis=0,
            ) / total_mass
            relative_velocity = damped[member_idx] - component_velocity
            damped[member_idx] = component_velocity + retention * relative_velocity

        return damped

    # -----------------------------------------------------------------------
    # Constraint solvers (XPBD Eq 17-18 with damping Eq 26)
    # -----------------------------------------------------------------------

    def _solve_distance_constraint(
        self,
        c: XPBDConstraint,
        x: np.ndarray,
        dt: float,
    ) -> float:
        """Solve a distance constraint using XPBD Gauss-Seidel update.

        Δλ = (-C - α̃·λ) / (∇C·M⁻¹·∇Cᵀ + α̃)
        Δx = M⁻¹·∇Cᵀ·Δλ
        """
        i, j = c.particle_indices[0], c.particle_indices[1]
        w_i = self._inv_masses[i]
        w_j = self._inv_masses[j]
        w_sum = w_i + w_j
        if w_sum < _EPS:
            return 0.0

        delta = x[i] - x[j]
        dist = float(np.linalg.norm(delta))
        if dist < _EPS:
            return 0.0

        # Constraint value: C = |x_i - x_j| - rest
        C = dist - c.rest_value

        # Constraint gradient: ∇C = n (unit direction)
        n = delta / dist

        # ∇C · M⁻¹ · ∇Cᵀ = w_i + w_j (for distance constraint)
        grad_inv_mass_grad = w_sum

        # Compliance: α̃ = α / Δt²
        alpha_tilde = c.compliance / (dt * dt + _EPS)

        # Damping: γ = α̃ · β / Δt
        gamma = 0.0
        damping_term = 0.0
        if c.damping_compliance > 0:
            beta_tilde = c.damping_compliance / (dt * dt + _EPS)
            gamma = alpha_tilde * beta_tilde / (dt + _EPS)
            # Velocity along constraint direction
            v_i = x[i] - self._positions[i]
            v_j = x[j] - self._positions[j]
            C_dot = float(np.dot(v_i - v_j, n)) / (dt + _EPS)
            damping_term = gamma * C_dot * dt

        # Lagrange multiplier update (Eq 18 / 26)
        denominator = (1.0 + gamma) * grad_inv_mass_grad + alpha_tilde
        if abs(denominator) < _EPS:
            return C

        delta_lambda = (-C - alpha_tilde * c.lambda_accumulated - damping_term) / denominator
        c.lambda_accumulated += delta_lambda

        # Position correction (Eq 17)
        correction = n * delta_lambda
        x[i] += w_i * correction
        x[j] -= w_j * correction

        return C

    def _solve_contact_constraint(
        self,
        c: XPBDConstraint,
        x: np.ndarray,
        dt: float,
    ) -> float:
        """Solve a unilateral contact constraint (C >= 0)."""
        i, j = c.particle_indices[0], c.particle_indices[1]
        w_i = self._inv_masses[i]
        w_j = self._inv_masses[j]
        w_sum = w_i + w_j
        if w_sum < _EPS:
            return 0.0

        delta = x[i] - x[j]
        dist = float(np.linalg.norm(delta))
        if dist < _EPS:
            return 0.0

        # Contact: minimum separation
        C = dist - c.rest_value
        if C >= 0:
            return 0.0  # No penetration

        n = delta / dist
        alpha_tilde = c.compliance / (dt * dt + _EPS)
        denominator = w_sum + alpha_tilde
        if abs(denominator) < _EPS:
            return C

        delta_lambda = (-C - alpha_tilde * c.lambda_accumulated) / denominator
        # Unilateral: only push apart, never pull together
        delta_lambda = max(delta_lambda, -c.lambda_accumulated)
        c.lambda_accumulated += delta_lambda

        correction = n * delta_lambda
        x[i] += w_i * correction
        x[j] -= w_j * correction

        # Friction
        if self.config.friction_coefficient > 0 and abs(delta_lambda) > _EPS:
            self._apply_friction(i, j, x, n, delta_lambda, dt)

        return C

    def _solve_bending_constraint(
        self,
        c: XPBDConstraint,
        x: np.ndarray,
        dt: float,
    ) -> float:
        """Solve a bending constraint (angle at middle particle)."""
        i, j, k = c.particle_indices
        a = x[i] - x[j]
        b = x[k] - x[j]
        la = float(np.linalg.norm(a))
        lb = float(np.linalg.norm(b))
        if la < _EPS or lb < _EPS:
            return 0.0

        cos_angle = np.dot(a, b) / (la * lb)
        cos_angle = float(np.clip(cos_angle, -1.0, 1.0))
        current_angle = float(np.arccos(cos_angle))
        C = current_angle - c.rest_value

        if abs(C) < _EPS:
            return 0.0

        # Approximate gradient: use perpendicular directions
        # For 2D: rotate a and b by 90 degrees
        perp_a = np.array([-a[1], a[0]], dtype=np.float64) / (la + _EPS)
        perp_b = np.array([-b[1], b[0]], dtype=np.float64) / (lb + _EPS)

        # Gradient magnitudes
        grad_i = perp_a / (la + _EPS)
        grad_k = -perp_b / (lb + _EPS)
        grad_j = -(grad_i + grad_k)

        w_i = self._inv_masses[i]
        w_j = self._inv_masses[j]
        w_k = self._inv_masses[k]

        denom = (
            w_i * float(np.dot(grad_i, grad_i))
            + w_j * float(np.dot(grad_j, grad_j))
            + w_k * float(np.dot(grad_k, grad_k))
        )
        alpha_tilde = c.compliance / (dt * dt + _EPS)
        denom += alpha_tilde

        if denom < _EPS:
            return C

        delta_lambda = (-C - alpha_tilde * c.lambda_accumulated) / denom
        c.lambda_accumulated += delta_lambda

        x[i] += w_i * grad_i * delta_lambda
        x[j] += w_j * grad_j * delta_lambda
        x[k] += w_k * grad_k * delta_lambda

        return C

    def _apply_friction(
        self,
        i: int,
        j: int,
        x: np.ndarray,
        normal: np.ndarray,
        normal_lambda: float,
        dt: float,
    ) -> None:
        """Apply Coulomb friction correction after contact."""
        w_i = self._inv_masses[i]
        w_j = self._inv_masses[j]
        if w_i + w_j < _EPS:
            return

        # Tangential velocity
        v_rel = (x[i] - self._positions[i]) - (x[j] - self._positions[j])
        v_tangent = v_rel - np.dot(v_rel, normal) * normal
        v_t_mag = float(np.linalg.norm(v_tangent))

        if v_t_mag < _EPS:
            return

        # Coulomb friction: |f_t| <= μ * |f_n|
        max_friction = self.config.friction_coefficient * abs(normal_lambda)
        correction_mag = min(v_t_mag, max_friction)
        t_dir = v_tangent / v_t_mag

        w_sum = w_i + w_j
        x[i] -= w_i / w_sum * correction_mag * t_dir
        x[j] += w_j / w_sum * correction_mag * t_dir

    # -----------------------------------------------------------------------
    # State access
    # -----------------------------------------------------------------------

    @property
    def positions(self) -> np.ndarray:
        return self._positions.copy()

    @property
    def velocities(self) -> np.ndarray:
        return self._velocities.copy()

    @property
    def particle_count(self) -> int:
        return self._particle_count

    @property
    def constraint_count(self) -> int:
        return len(self._constraints)

    @property
    def last_diagnostics(self) -> XPBDDiagnostics:
        return self._last_diagnostics

    def get_position(self, index: int) -> tuple[float, float]:
        return (float(self._positions[index, 0]), float(self._positions[index, 1]))

    def get_velocity(self, index: int) -> tuple[float, float]:
        return (float(self._velocities[index, 0]), float(self._velocities[index, 1]))

    def get_constraint_force(self, constraint_index: int, dt: float) -> float:
        """Estimate constraint force from accumulated Lagrange multiplier."""
        if constraint_index >= len(self._constraints):
            return 0.0
        return self._constraints[constraint_index].lambda_accumulated / max(dt, _EPS)


# ---------------------------------------------------------------------------
# Chain builder: create a soft-body chain attached to a rigid CoM
# ---------------------------------------------------------------------------

def build_xpbd_chain(
    solver: XPBDSolver,
    preset: XPBDChainPreset,
    anchor_position: tuple[float, float],
    rigid_com_index: Optional[int] = None,
) -> list[int]:
    """Build a soft-body chain in the solver and return particle indices.

    If rigid_com_index is provided, the chain root is attached to the rigid
    body CoM via an attachment constraint — enabling two-way coupling.
    """
    direction = np.array(preset.rest_direction, dtype=np.float64)
    norm = float(np.linalg.norm(direction))
    if norm < _EPS:
        direction = np.array([0.0, -1.0], dtype=np.float64)
    else:
        direction = direction / norm

    offset = np.array(preset.anchor_offset, dtype=np.float64)
    root_pos = np.array(anchor_position, dtype=np.float64) + offset

    indices: list[int] = []
    mass_ramp = np.linspace(preset.particle_mass, preset.particle_mass * preset.tip_mass_scale, preset.segment_count)

    for seg in range(preset.segment_count):
        pos = root_pos + direction * (preset.segment_length * seg)
        if seg == 0:
            # Root node: kinematic if no rigid coupling, else soft with attachment
            if rigid_com_index is not None:
                idx = solver.add_particle(
                    (float(pos[0]), float(pos[1])),
                    mass=float(mass_ramp[seg]),
                    kind=ParticleKind.SOFT_NODE,
                    radius=preset.particle_radius,
                )
                solver.add_attachment_constraint(idx, rigid_com_index, compliance=preset.compliance)
            else:
                idx = solver.add_particle(
                    (float(pos[0]), float(pos[1])),
                    mass=0.0,
                    kind=ParticleKind.KINEMATIC,
                    radius=preset.particle_radius,
                )
        else:
            idx = solver.add_particle(
                (float(pos[0]), float(pos[1])),
                mass=float(mass_ramp[seg]),
                kind=ParticleKind.SOFT_NODE,
                radius=preset.particle_radius,
            )
        indices.append(idx)

    # Distance constraints between consecutive nodes
    for seg in range(preset.segment_count - 1):
        solver.add_distance_constraint(
            indices[seg],
            indices[seg + 1],
            rest_length=preset.segment_length,
            compliance=preset.compliance,
            damping=preset.damping_compliance,
        )

    # Bending constraints for every 3 consecutive nodes
    if preset.segment_count >= 3:
        for seg in range(preset.segment_count - 2):
            solver.add_bending_constraint(
                indices[seg],
                indices[seg + 1],
                indices[seg + 2],
                compliance=preset.bending_compliance,
            )

    return indices


def create_default_xpbd_presets(head_units: float = 3.0) -> list[XPBDChainPreset]:
    """Create repository-native XPBD cape/hair presets."""
    hu = 1.0 / max(float(head_units), 1.0)
    return [
        XPBDChainPreset(
            name="cape",
            anchor_joint="chest",
            segment_count=6,
            segment_length=hu * 0.38,
            compliance=1e-7,
            damping_compliance=1e-5,
            bending_compliance=5e-4,
            particle_mass=0.15,
            tip_mass_scale=1.45,
            particle_radius=hu * 0.10,
            rest_direction=(0.0, -1.0),
            anchor_offset=(0.0, -hu * 0.18),
            body_collision_joints=("head", "neck", "chest", "spine", "hip"),
            body_collision_radii=(hu * 0.34, hu * 0.24, hu * 0.38, hu * 0.28, hu * 0.24),
            ground_y=0.0,
        ),
        XPBDChainPreset(
            name="hair",
            anchor_joint="head",
            segment_count=5,
            segment_length=hu * 0.20,
            compliance=1e-6,
            damping_compliance=1e-4,
            bending_compliance=1e-3,
            particle_mass=0.08,
            tip_mass_scale=1.20,
            particle_radius=hu * 0.08,
            rest_direction=(0.0, -1.0),
            anchor_offset=(0.0, -hu * 0.06),
            body_collision_joints=("head", "neck", "chest"),
            body_collision_radii=(hu * 0.32, hu * 0.18, hu * 0.20),
            ground_y=0.0,
        ),
    ]


__all__ = [
    "ParticleKind",
    "ConstraintKind",
    "XPBDSolverConfig",
    "XPBDChainPreset",
    "XPBDDiagnostics",
    "XPBDConstraint",
    "XPBDSolver",
    "build_xpbd_chain",
    "create_default_xpbd_presets",
]
