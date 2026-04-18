"""3D XPBD solver — first-class three-dimensional extension of ``xpbd_solver``.

SESSION-071 (P1-XPBD-3) — Real 3D constraint solver, not a 2D shell.

This module is the structural complement of ``mathart.animation.xpbd_solver``
(which remains the authoritative 2D path). It is built fresh so that:

* particle positions, velocities, predictions and previous positions are
  ``(N, 3)`` arrays;
* every constraint gradient ``\u2207C`` is computed in three components
  ``[x, y, z]``;
* the bending constraint uses an honest 3D gradient via the cross product
  rather than a 2D 90\u00b0 rotation;
* contact and self-collision constraints take a true 3D normal.

The 2D solver is *not* removed and *not* monkey-patched. Existing 2D backends,
tests and tools continue to call ``XPBDSolver``. ``XPBDSolver3D`` is only
invoked by the new ``Physics3DBackend`` (see
``mathart.core.physics3d_backend``).

References
----------
[1] M. Macklin, M. M\u00fcller, N. Chentanez, "XPBD: Position-Based Simulation of
    Compliant Constrained Dynamics", MIG/SIGGRAPH 2016 \u2014 Eqs. 17, 18, 26.
[2] M. M\u00fcller, M. Macklin, N. Chentanez, S. Jeschke, T. Kim, "Detailed Rigid
    Body Simulation with Extended Position Based Dynamics", SCA/SIGGRAPH 2020.
[3] NVIDIA PhysX SDK 5 contact manifold conventions and UE5 Chaos Physics
    contact persistence model \u2014 used to motivate the
    ``ContactManifoldRecord`` 3D fields produced by this solver.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional

import numpy as np

# Reuse the 2D enums to keep downstream tooling (audit/diagnostics) consistent.
from .xpbd_solver import ConstraintKind, ParticleKind, _EPS


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class XPBDSolver3DConfig:
    """Configuration for the 3D XPBD solver.

    The 3D config intentionally mirrors :class:`XPBDSolverConfig` field-for-
    field, but ``gravity`` is a length-3 vector instead of a length-2 tuple.
    """

    sub_steps: int = 4
    solver_iterations: int = 8
    gravity: tuple[float, float, float] = (0.0, -9.81, 0.0)
    default_compliance: float = 0.0
    default_damping: float = 0.0
    velocity_damping: float = 0.98
    max_velocity: float = 50.0
    enable_self_collision: bool = True
    self_collision_radius: float = 0.015
    friction_coefficient: float = 0.3
    enable_two_way_coupling: bool = True
    # SESSION-072 (P1-DISTILL-1A): per-constraint-type compliance knobs.
    # Exposed to the global JIT and Layer 3 (Optuna) tuning closed loop.
    # When ``None``, the solver falls back to ``default_compliance``.
    compliance_distance: float | None = None
    compliance_bending: float | None = None


@dataclass
class XPBDSolver3DDiagnostics:
    """Per-frame diagnostics for the 3D solver."""

    total_particles: int = 0
    total_constraints: int = 0
    mean_constraint_error: float = 0.0
    max_constraint_error: float = 0.0
    rigid_com_displacement: float = 0.0
    reaction_impulse_magnitude: float = 0.0
    self_collision_count: int = 0
    contact_collision_count: int = 0
    sub_steps_used: int = 0
    iterations_per_substep: int = 0
    max_velocity_observed: float = 0.0
    energy_estimate: float = 0.0
    z_axis_active: bool = False  # True when any particle has |z| > _EPS

    def to_dict(self) -> dict[str, float | int | bool]:
        return {
            "total_particles": self.total_particles,
            "total_constraints": self.total_constraints,
            "mean_constraint_error": float(self.mean_constraint_error),
            "max_constraint_error": float(self.max_constraint_error),
            "rigid_com_displacement": float(self.rigid_com_displacement),
            "reaction_impulse_magnitude": float(self.reaction_impulse_magnitude),
            "self_collision_count": int(self.self_collision_count),
            "contact_collision_count": int(self.contact_collision_count),
            "sub_steps_used": int(self.sub_steps_used),
            "iterations_per_substep": int(self.iterations_per_substep),
            "max_velocity_observed": float(self.max_velocity_observed),
            "energy_estimate": float(self.energy_estimate),
            "z_axis_active": bool(self.z_axis_active),
        }


# ---------------------------------------------------------------------------
# Constraint
# ---------------------------------------------------------------------------

@dataclass
class XPBDConstraint3D:
    kind: ConstraintKind
    particle_indices: tuple[int, ...]
    rest_value: float = 0.0
    compliance: float = 0.0
    damping_compliance: float = 0.0
    lambda_accumulated: float = 0.0
    # Optional explicit world-space contact normal (length 3) for CONTACT
    # constraints whose other "body" is a kinematic plane/proxy and not a
    # registered particle. When ``None`` the gradient is derived from the
    # two participating particles, matching the 2D solver convention.
    contact_normal: Optional[tuple[float, float, float]] = None


# ---------------------------------------------------------------------------
# 3D Spatial Hash
# ---------------------------------------------------------------------------

class SpatialHashGrid3D:
    """Fixed-cell-size 3D spatial hash following Teschner et al. 2003 / M\u00fcller's
    Ten Minute Physics tutorial 11, generalised to three integer cell axes.

    The hashing function is ``hash(ix, iy, iz) = (ix*P1 ^ iy*P2 ^ iz*P3) mod N``
    with three large primes \u2014 this is the canonical 3D extension and the
    reason why a 2D-only ``SpatialHashGrid`` cannot be reused: the 2D hash
    collapses every (ix, iy, *, *) bucket onto a single bin, producing
    catastrophic false positives once particles span the Z axis.
    """

    _PRIME1 = 73856093
    _PRIME2 = 19349663
    _PRIME3 = 83492791

    def __init__(self, cell_size: float = 0.05, table_size: int = 8192) -> None:
        self.cell_size = max(float(cell_size), _EPS)
        self.table_size = int(table_size)
        self._table: dict[int, list[int]] = {}

    def clear(self) -> None:
        self._table.clear()

    def _cell(self, x: float, y: float, z: float) -> tuple[int, int, int]:
        c = self.cell_size
        return (
            int(np.floor(x / c)),
            int(np.floor(y / c)),
            int(np.floor(z / c)),
        )

    def _hash(self, ix: int, iy: int, iz: int) -> int:
        return (
            (ix * self._PRIME1) ^ (iy * self._PRIME2) ^ (iz * self._PRIME3)
        ) % self.table_size

    def insert(self, positions: np.ndarray) -> None:
        if positions.ndim != 2 or positions.shape[1] != 3:
            raise ValueError(
                "SpatialHashGrid3D requires (N, 3) positions; "
                f"got shape {positions.shape}"
            )
        self.clear()
        for idx in range(len(positions)):
            ix, iy, iz = self._cell(
                positions[idx, 0], positions[idx, 1], positions[idx, 2]
            )
            h = self._hash(ix, iy, iz)
            self._table.setdefault(h, []).append(idx)

    def query_neighbours(
        self, x: float, y: float, z: float, radius: float,
    ) -> list[int]:
        cells = int(np.ceil(radius / self.cell_size)) + 1
        cx, cy, cz = self._cell(x, y, z)
        result: list[int] = []
        for dx in range(-cells, cells + 1):
            for dy in range(-cells, cells + 1):
                for dz in range(-cells, cells + 1):
                    h = self._hash(cx + dx, cy + dy, cz + dz)
                    bucket = self._table.get(h)
                    if bucket is not None:
                        result.extend(bucket)
        return result

    def find_all_pairs(
        self,
        positions: np.ndarray,
        radii: np.ndarray,
        min_separation: float,
    ) -> list[tuple[int, int, float]]:
        """Return all (i, j, distance) pairs within ``min_separation`` (i < j)."""
        self.insert(positions)
        seen: set[tuple[int, int]] = set()
        pairs: list[tuple[int, int, float]] = []
        for i in range(len(positions)):
            search_radius = float(min_separation + radii[i])
            neighbours = self.query_neighbours(
                positions[i, 0], positions[i, 1], positions[i, 2], search_radius,
            )
            for j in neighbours:
                if j <= i:
                    continue
                key = (i, j)
                if key in seen:
                    continue
                seen.add(key)
                d = float(np.linalg.norm(positions[i] - positions[j]))
                if d < (radii[i] + radii[j] + min_separation):
                    pairs.append((i, j, d))
        return pairs


# ---------------------------------------------------------------------------
# Solver
# ---------------------------------------------------------------------------

class XPBDSolver3D:
    """Three-dimensional XPBD solver.

    Particles live in :math:`\\mathbb{R}^3`. The Gauss\u2013Seidel sweep updates
    Lagrange multipliers per constraint exactly as in Macklin & M\u00fcller 2016
    (Eq. 18) but with three-component gradients. ``compliance`` is decoupled
    from the time step via the canonical
    :math:`\\tilde{\\alpha} = \\alpha / \\Delta t^2` reparameterisation.
    """

    def __init__(self, config: Optional[XPBDSolver3DConfig] = None) -> None:
        self.config = config or XPBDSolver3DConfig()
        self._positions = np.zeros((0, 3), dtype=np.float64)
        self._prev_positions = np.zeros((0, 3), dtype=np.float64)
        self._predicted = np.zeros((0, 3), dtype=np.float64)
        self._velocities = np.zeros((0, 3), dtype=np.float64)
        self._inv_masses = np.zeros(0, dtype=np.float64)
        self._kinds = np.zeros(0, dtype=np.int32)
        self._radii = np.zeros(0, dtype=np.float64)
        self._constraints: list[XPBDConstraint3D] = []
        self._particle_count = 0
        self._rigid_com_index: Optional[int] = None
        self._rigid_com_initial: Optional[np.ndarray] = None
        self._last_diagnostics = XPBDSolver3DDiagnostics()

    # ------------------------------------------------------------------ state

    @property
    def particle_count(self) -> int:
        return self._particle_count

    @property
    def constraint_count(self) -> int:
        return len(self._constraints)

    @property
    def positions(self) -> np.ndarray:
        return self._positions.copy()

    @property
    def velocities(self) -> np.ndarray:
        return self._velocities.copy()

    @property
    def last_diagnostics(self) -> XPBDSolver3DDiagnostics:
        return self._last_diagnostics

    def get_position(self, index: int) -> tuple[float, float, float]:
        return (
            float(self._positions[index, 0]),
            float(self._positions[index, 1]),
            float(self._positions[index, 2]),
        )

    # --------------------------------------------------------------- particles

    def add_particle(
        self,
        position: tuple[float, float, float],
        mass: float,
        kind: ParticleKind = ParticleKind.SOFT_NODE,
        radius: float = 0.015,
    ) -> int:
        idx = self._particle_count
        new_pos = np.array(
            [[float(position[0]), float(position[1]), float(position[2])]],
            dtype=np.float64,
        )
        inv_m = 0.0 if (mass <= 0.0 or kind == ParticleKind.KINEMATIC) else 1.0 / float(mass)

        if idx == 0:
            self._positions = new_pos.copy()
            self._prev_positions = new_pos.copy()
            self._predicted = new_pos.copy()
            self._inv_masses = np.array([inv_m], dtype=np.float64)
            self._velocities = np.zeros((1, 3), dtype=np.float64)
            self._kinds = np.array([kind.value], dtype=np.int32)
            self._radii = np.array([float(radius)], dtype=np.float64)
        else:
            self._positions = np.vstack([self._positions, new_pos])
            self._prev_positions = np.vstack([self._prev_positions, new_pos])
            self._predicted = np.vstack([self._predicted, new_pos])
            self._inv_masses = np.append(self._inv_masses, inv_m)
            self._velocities = np.vstack([self._velocities, np.zeros((1, 3))])
            self._kinds = np.append(self._kinds, kind.value)
            self._radii = np.append(self._radii, float(radius))

        if kind == ParticleKind.RIGID_COM:
            self._rigid_com_index = idx

        self._particle_count += 1
        return idx

    def set_rigid_com(self, index: int, mass: float) -> None:
        self._kinds[index] = ParticleKind.RIGID_COM.value
        if self.config.enable_two_way_coupling:
            self._inv_masses[index] = 1.0 / max(float(mass), _EPS)
        else:
            self._inv_masses[index] = 0.0
        self._rigid_com_index = index

    def set_kinematic(self, index: int) -> None:
        self._kinds[index] = ParticleKind.KINEMATIC.value
        self._inv_masses[index] = 0.0

    # --------------------------------------------------------------- constraints

    def add_distance_constraint(
        self,
        i: int,
        j: int,
        rest_length: Optional[float] = None,
        compliance: float = 0.0,
        damping: float = 0.0,
    ) -> int:
        if rest_length is None:
            rest_length = float(np.linalg.norm(self._positions[i] - self._positions[j]))
        c = XPBDConstraint3D(
            kind=ConstraintKind.DISTANCE,
            particle_indices=(int(i), int(j)),
            rest_value=max(float(rest_length), _EPS),
            compliance=float(compliance),
            damping_compliance=float(damping),
        )
        self._constraints.append(c)
        return len(self._constraints) - 1

    def add_attachment_constraint(
        self,
        soft_index: int,
        rigid_index: int,
        compliance: float = 0.0,
    ) -> int:
        rest = float(np.linalg.norm(
            self._positions[soft_index] - self._positions[rigid_index]
        ))
        c = XPBDConstraint3D(
            kind=ConstraintKind.ATTACHMENT,
            particle_indices=(int(soft_index), int(rigid_index)),
            rest_value=rest,
            compliance=float(compliance),
        )
        self._constraints.append(c)
        return len(self._constraints) - 1

    def add_bending_constraint(
        self, i: int, j: int, k: int, compliance: float = 1e-3,
    ) -> int:
        rest_angle = self._compute_angle(i, j, k)
        c = XPBDConstraint3D(
            kind=ConstraintKind.BENDING,
            particle_indices=(int(i), int(j), int(k)),
            rest_value=rest_angle,
            compliance=float(compliance),
        )
        self._constraints.append(c)
        return len(self._constraints) - 1

    def add_contact_constraint(
        self,
        i: int,
        j: int,
        rest_distance: float,
        normal: Optional[tuple[float, float, float]] = None,
        compliance: float = 0.0,
    ) -> int:
        c = XPBDConstraint3D(
            kind=ConstraintKind.CONTACT,
            particle_indices=(int(i), int(j)),
            rest_value=float(rest_distance),
            compliance=float(compliance),
            contact_normal=tuple(float(v) for v in normal) if normal is not None else None,
        )
        self._constraints.append(c)
        return len(self._constraints) - 1

    def clear_transient_constraints(self) -> None:
        self._constraints = [
            c for c in self._constraints
            if c.kind not in (ConstraintKind.CONTACT, ConstraintKind.SELF_COLLISION)
        ]

    # ------------------------------------------------------------------ helpers

    def _compute_angle(self, i: int, j: int, k: int) -> float:
        a = self._positions[i] - self._positions[j]
        b = self._positions[k] - self._positions[j]
        denom = float(np.linalg.norm(a) * np.linalg.norm(b)) + _EPS
        cos_angle = float(np.clip(np.dot(a, b) / denom, -1.0, 1.0))
        return float(np.arccos(cos_angle))

    # ------------------------------------------------------------------- step

    def step(self, dt: float = 1.0 / 60.0) -> XPBDSolver3DDiagnostics:
        """Advance one full XPBD frame with sub-stepping (Eq. 17, 18)."""
        n = self._particle_count
        if n == 0:
            return self._last_diagnostics

        sub_dt = float(dt) / max(self.config.sub_steps, 1)
        gravity = np.array(self.config.gravity, dtype=np.float64)
        if gravity.shape != (3,):
            raise ValueError(
                f"gravity must be length-3, got shape {gravity.shape}"
            )

        if self._rigid_com_index is not None:
            self._rigid_com_initial = self._positions[self._rigid_com_index].copy()

        errors: list[float] = []
        self_collision_count = 0
        contact_collision_count = 0
        max_vel = 0.0

        for _sub in range(self.config.sub_steps):
            # ---- 1. Predict ------------------------------------------------
            for i in range(n):
                if self._inv_masses[i] <= 0.0:
                    self._predicted[i] = self._positions[i].copy()
                    continue
                self._velocities[i] += gravity * sub_dt
                self._predicted[i] = self._positions[i] + self._velocities[i] * sub_dt

            # ---- 2. Initialise ---------------------------------------------
            solve_x = self._predicted.copy()
            for c in self._constraints:
                c.lambda_accumulated = 0.0

            # ---- 3. Gauss\u2013Seidel ---------------------------------------
            for _it in range(self.config.solver_iterations):
                for c in self._constraints:
                    if c.kind in (ConstraintKind.DISTANCE, ConstraintKind.ATTACHMENT):
                        err = self._solve_distance(c, solve_x, sub_dt)
                        errors.append(abs(err))
                    elif c.kind == ConstraintKind.BENDING:
                        err = self._solve_bending(c, solve_x, sub_dt)
                        errors.append(abs(err))
                    elif c.kind == ConstraintKind.CONTACT:
                        err = self._solve_contact(c, solve_x, sub_dt)
                        if abs(err) > _EPS:
                            contact_collision_count += 1
                        errors.append(abs(err))
                    elif c.kind == ConstraintKind.SELF_COLLISION:
                        err = self._solve_self_collision(c, solve_x, sub_dt)
                        if abs(err) > _EPS:
                            self_collision_count += 1
                        errors.append(abs(err))

            # ---- 4. Update velocities & positions --------------------------
            for i in range(n):
                if self._inv_masses[i] <= 0.0:
                    continue
                new_v = (solve_x[i] - self._positions[i]) / sub_dt
                new_v *= self.config.velocity_damping
                speed = float(np.linalg.norm(new_v))
                if speed > self.config.max_velocity:
                    new_v = new_v / speed * self.config.max_velocity
                max_vel = max(max_vel, speed)
                self._velocities[i] = new_v
                self._prev_positions[i] = self._positions[i].copy()
                self._positions[i] = solve_x[i].copy()

        # ---- Diagnostics --------------------------------------------------
        rigid_disp = 0.0
        reaction = 0.0
        if self._rigid_com_index is not None and self._rigid_com_initial is not None:
            delta = self._positions[self._rigid_com_index] - self._rigid_com_initial
            rigid_disp = float(np.linalg.norm(delta))
            inv_m = float(self._inv_masses[self._rigid_com_index])
            if inv_m > 0.0:
                reaction = (1.0 / inv_m) * rigid_disp / max(float(dt), _EPS)

        err_arr = np.array(errors) if errors else np.zeros(1)
        energy = 0.5 * float(np.sum(
            np.sum(self._velocities ** 2, axis=1)
            / np.maximum(self._inv_masses, _EPS)
        ))

        z_active = bool(np.any(np.abs(self._positions[:, 2]) > _EPS))

        self._last_diagnostics = XPBDSolver3DDiagnostics(
            total_particles=n,
            total_constraints=len(self._constraints),
            mean_constraint_error=float(np.mean(err_arr)),
            max_constraint_error=float(np.max(err_arr)),
            rigid_com_displacement=rigid_disp,
            reaction_impulse_magnitude=reaction,
            self_collision_count=self_collision_count,
            contact_collision_count=contact_collision_count,
            sub_steps_used=self.config.sub_steps,
            iterations_per_substep=self.config.solver_iterations,
            max_velocity_observed=max_vel,
            energy_estimate=energy,
            z_axis_active=z_active,
        )
        return self._last_diagnostics

    # ------------------------------------------------------------- solvers

    def _solve_distance(
        self, c: XPBDConstraint3D, x: np.ndarray, dt: float,
    ) -> float:
        """3D distance constraint. \u2207C is the unit 3-vector from j to i."""
        i, j = c.particle_indices[0], c.particle_indices[1]
        w_i = float(self._inv_masses[i])
        w_j = float(self._inv_masses[j])
        w_sum = w_i + w_j
        if w_sum < _EPS:
            return 0.0

        delta = x[i] - x[j]                        # shape (3,)
        dist = float(np.linalg.norm(delta))
        if dist < _EPS:
            return 0.0

        C = dist - c.rest_value
        n = delta / dist                           # unit gradient in 3D

        alpha_tilde = c.compliance / (dt * dt + _EPS)

        gamma = 0.0
        damping_term = 0.0
        if c.damping_compliance > 0.0:
            beta_tilde = c.damping_compliance / (dt * dt + _EPS)
            gamma = alpha_tilde * beta_tilde / (dt + _EPS)
            v_i = x[i] - self._positions[i]
            v_j = x[j] - self._positions[j]
            C_dot = float(np.dot(v_i - v_j, n)) / (dt + _EPS)
            damping_term = gamma * C_dot * dt

        denom = (1.0 + gamma) * w_sum + alpha_tilde
        if abs(denom) < _EPS:
            return C

        d_lambda = (-C - alpha_tilde * c.lambda_accumulated - damping_term) / denom
        c.lambda_accumulated += d_lambda

        correction = n * d_lambda
        x[i] += w_i * correction
        x[j] -= w_j * correction
        return C

    def _solve_bending(
        self, c: XPBDConstraint3D, x: np.ndarray, dt: float,
    ) -> float:
        """3D bending constraint via the cross product.

        Following M\u00fcller, *Position-Based Dynamics* (J VRC 2007) and the
        SCA 2020 paper: the bending constraint penalises the angle deviation
        at the middle particle. In 3D the perpendicular gradient direction is
        :math:`\\hat{n} = (a \\times b) \\times a / \\|\\dots\\|`, which reduces
        to the 2D 90\u00b0 rotation when ``a`` and ``b`` lie in the XY plane.
        """
        i, j, k = c.particle_indices
        a = x[i] - x[j]
        b = x[k] - x[j]
        la = float(np.linalg.norm(a))
        lb = float(np.linalg.norm(b))
        if la < _EPS or lb < _EPS:
            return 0.0

        cos_angle = float(np.clip(np.dot(a, b) / (la * lb), -1.0, 1.0))
        current_angle = float(np.arccos(cos_angle))
        C = current_angle - c.rest_value
        if abs(C) < _EPS:
            return 0.0

        # Build a stable in-plane normal via a double cross product.
        cross_ab = np.cross(a, b)
        cross_norm = float(np.linalg.norm(cross_ab))
        if cross_norm < _EPS:
            # Degenerate (collinear) \u2014 skip to avoid NaNs; the next
            # sub-step will catch the configuration once it perturbs.
            return 0.0
        plane_normal = cross_ab / cross_norm

        perp_a = np.cross(plane_normal, a)
        perp_b = np.cross(b, plane_normal)
        perp_a_n = float(np.linalg.norm(perp_a))
        perp_b_n = float(np.linalg.norm(perp_b))
        if perp_a_n < _EPS or perp_b_n < _EPS:
            return 0.0
        perp_a /= perp_a_n
        perp_b /= perp_b_n

        grad_i = perp_a / (la + _EPS)
        grad_k = -perp_b / (lb + _EPS)
        grad_j = -(grad_i + grad_k)

        w_i = float(self._inv_masses[i])
        w_j = float(self._inv_masses[j])
        w_k = float(self._inv_masses[k])

        denom = (
            w_i * float(np.dot(grad_i, grad_i))
            + w_j * float(np.dot(grad_j, grad_j))
            + w_k * float(np.dot(grad_k, grad_k))
        )
        alpha_tilde = c.compliance / (dt * dt + _EPS)
        denom += alpha_tilde
        if denom < _EPS:
            return C

        d_lambda = (-C - alpha_tilde * c.lambda_accumulated) / denom
        c.lambda_accumulated += d_lambda
        x[i] += w_i * grad_i * d_lambda
        x[j] += w_j * grad_j * d_lambda
        x[k] += w_k * grad_k * d_lambda
        return C

    def _solve_contact(
        self, c: XPBDConstraint3D, x: np.ndarray, dt: float,
    ) -> float:
        """Unilateral 3D contact (C >= 0) with optional explicit normal."""
        i, j = c.particle_indices[0], c.particle_indices[1]
        w_i = float(self._inv_masses[i])
        w_j = float(self._inv_masses[j])
        w_sum = w_i + w_j
        if w_sum < _EPS:
            return 0.0

        if c.contact_normal is not None:
            # Half-space style: signed distance along the supplied normal.
            n_vec = np.array(c.contact_normal, dtype=np.float64)
            n_norm = float(np.linalg.norm(n_vec))
            if n_norm < _EPS:
                return 0.0
            n = n_vec / n_norm
            C = float(np.dot(x[i] - x[j], n)) - c.rest_value
            if C >= 0.0:
                return 0.0
        else:
            delta = x[i] - x[j]
            dist = float(np.linalg.norm(delta))
            if dist < _EPS:
                return 0.0
            C = dist - c.rest_value
            if C >= 0.0:
                return 0.0
            n = delta / dist

        alpha_tilde = c.compliance / (dt * dt + _EPS)
        denom = w_sum + alpha_tilde
        if abs(denom) < _EPS:
            return C

        d_lambda = (-C - alpha_tilde * c.lambda_accumulated) / denom
        # Unilateral: never pull together.
        d_lambda = max(d_lambda, -c.lambda_accumulated)
        c.lambda_accumulated += d_lambda

        correction = n * d_lambda
        x[i] += w_i * correction
        x[j] -= w_j * correction

        if self.config.friction_coefficient > 0.0 and abs(d_lambda) > _EPS:
            self._apply_friction(i, j, x, n, d_lambda)
        return C

    def _solve_self_collision(
        self, c: XPBDConstraint3D, x: np.ndarray, dt: float,
    ) -> float:
        # Self-collision is geometrically a distance constraint with
        # unilateral semantics; reuse the contact path.
        return self._solve_contact(c, x, dt)

    def _apply_friction(
        self,
        i: int,
        j: int,
        x: np.ndarray,
        normal: np.ndarray,
        normal_lambda: float,
    ) -> None:
        w_i = float(self._inv_masses[i])
        w_j = float(self._inv_masses[j])
        if w_i + w_j < _EPS:
            return
        v_rel = (x[i] - self._positions[i]) - (x[j] - self._positions[j])
        v_t = v_rel - np.dot(v_rel, normal) * normal
        v_t_mag = float(np.linalg.norm(v_t))
        if v_t_mag < _EPS:
            return
        max_friction = self.config.friction_coefficient * abs(normal_lambda)
        correction_mag = min(v_t_mag, max_friction)
        t_dir = v_t / v_t_mag
        w_sum = w_i + w_j
        x[i] -= w_i / w_sum * correction_mag * t_dir
        x[j] += w_j / w_sum * correction_mag * t_dir


__all__ = [
    "XPBDSolver3DConfig",
    "XPBDSolver3DDiagnostics",
    "XPBDConstraint3D",
    "SpatialHashGrid3D",
    "XPBDSolver3D",
]
