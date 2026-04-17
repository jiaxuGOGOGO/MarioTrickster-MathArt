"""XPBD collision detection: spatial hashing, body contacts, self-collision.

This module provides the collision detection layer that feeds contact and
self-collision constraints into the unified XPBD solver.  It implements:

  1. **Spatial Hash Table** — O(1) amortised neighbour queries for particle
     pairs, following Matthias Müller's Ten Minute Physics tutorial 11/15.
  2. **Body Contact Proxies** — circle/capsule collision proxies for the
     character skeleton, generating unilateral contact constraints.
  3. **Self-Collision Detection** — particle-pair minimum-separation
     constraints with friction, following the 5-trick recipe from Müller.
  4. **Ground Plane** — simple half-space constraint.

Research distillation:
  - Matthias Müller, *Ten Minute Physics* tutorials 11 (spatial hashing),
    15 (self-collision), 23 (sweep & prune).
  - Carmen Cincotti, *Cloth Self Collisions | XPBD* (2022).
  - Key stability rules:
    • rest_length >= 2 * particle_radius (avoid constraint fighting)
    • v_max = 0.2 * radius / Δt_sub (tunnelling guard)
    • Friction damping after collision response

All arrays are pure NumPy for vectorised performance.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np

from .xpbd_solver import (
    ConstraintKind,
    XPBDConstraint,
    XPBDSolver,
    XPBDSolverConfig,
    _EPS,
)


# ---------------------------------------------------------------------------
# Spatial Hash Table
# ---------------------------------------------------------------------------

class SpatialHashGrid:
    """Fixed-cell-size spatial hash for fast neighbour queries in 2D.

    Implementation follows Müller's Ten Minute Physics tutorial 11:
    hash(ix, iy) = (ix * P1 ^ iy * P2) mod table_size
    """

    _PRIME1 = 73856093
    _PRIME2 = 19349663

    def __init__(self, cell_size: float = 0.05, table_size: int = 4096):
        self.cell_size = max(cell_size, _EPS)
        self.table_size = table_size
        self._table: dict[int, list[int]] = {}

    def clear(self) -> None:
        self._table.clear()

    def _hash(self, ix: int, iy: int) -> int:
        return ((ix * self._PRIME1) ^ (iy * self._PRIME2)) % self.table_size

    def _cell(self, x: float, y: float) -> tuple[int, int]:
        return (int(np.floor(x / self.cell_size)), int(np.floor(y / self.cell_size)))

    def insert(self, positions: np.ndarray) -> None:
        """Insert all particles into the hash grid."""
        self.clear()
        for idx in range(len(positions)):
            ix, iy = self._cell(positions[idx, 0], positions[idx, 1])
            h = self._hash(ix, iy)
            if h not in self._table:
                self._table[h] = []
            self._table[h].append(idx)

    def query_neighbours(self, x: float, y: float, radius: float) -> list[int]:
        """Return indices of particles within radius of (x, y)."""
        result: list[int] = []
        cells_to_check = int(np.ceil(radius / self.cell_size)) + 1
        cx, cy = self._cell(x, y)
        for dx in range(-cells_to_check, cells_to_check + 1):
            for dy in range(-cells_to_check, cells_to_check + 1):
                h = self._hash(cx + dx, cy + dy)
                if h in self._table:
                    result.extend(self._table[h])
        return result

    def find_all_pairs(
        self,
        positions: np.ndarray,
        radii: np.ndarray,
        min_separation: float,
    ) -> list[tuple[int, int, float]]:
        """Find all particle pairs closer than min_separation.

        Returns list of (i, j, distance) tuples where i < j.
        """
        self.insert(positions)
        pairs: list[tuple[int, int, float]] = []
        seen: set[tuple[int, int]] = set()

        for i in range(len(positions)):
            search_radius = min_separation + radii[i]
            neighbours = self.query_neighbours(
                positions[i, 0], positions[i, 1], search_radius
            )
            for j in neighbours:
                if j <= i:
                    continue
                pair_key = (i, j)
                if pair_key in seen:
                    continue
                seen.add(pair_key)

                dist = float(np.linalg.norm(positions[i] - positions[j]))
                threshold = radii[i] + radii[j]
                if dist < threshold:
                    pairs.append((i, j, dist))

        return pairs


# ---------------------------------------------------------------------------
# Body Collision Proxy
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BodyCollisionProxy:
    """Circle proxy for a skeleton joint in world space."""
    center: tuple[float, float]
    radius: float
    label: str = "body"
    # Proxy particle index in the solver (if registered)
    particle_index: Optional[int] = None


# ---------------------------------------------------------------------------
# Collision Manager
# ---------------------------------------------------------------------------

class XPBDCollisionManager:
    """Manages collision detection and constraint generation for the XPBD solver.

    Workflow per frame:
      1. Update body proxies from skeleton joint positions.
      2. Generate contact constraints (soft nodes vs body proxies).
      3. Generate self-collision constraints (spatial hash query).
      4. Generate ground-plane constraints.
      5. Feed all transient constraints into the solver.
    """

    def __init__(
        self,
        solver: XPBDSolver,
        config: Optional[XPBDSolverConfig] = None,
    ):
        self._solver = solver
        self._config = config or solver.config
        self._spatial_hash = SpatialHashGrid(
            cell_size=self._config.self_collision_radius * 4.0,
        )
        self._body_proxies: list[BodyCollisionProxy] = []
        self._ground_y: Optional[float] = None

    def set_ground(self, y: float) -> None:
        """Set the ground plane Y coordinate."""
        self._ground_y = y

    def update_body_proxies(self, proxies: list[BodyCollisionProxy]) -> None:
        """Update the body collision proxies from skeleton positions."""
        self._body_proxies = proxies

    def generate_constraints(
        self,
        soft_particle_indices: list[int],
        chain_connectivity: Optional[set[tuple[int, int]]] = None,
    ) -> int:
        """Generate all transient collision constraints for this frame.

        Args:
            soft_particle_indices: Indices of soft-body particles to check.
            chain_connectivity: Set of (i, j) pairs that are directly
                connected by distance constraints (skip self-collision for these).

        Returns:
            Total number of constraints generated.
        """
        # Clear previous transient constraints
        self._solver.clear_transient_constraints()
        count = 0

        # --- Body contact constraints ---
        count += self._generate_body_contacts(soft_particle_indices)

        # --- Self-collision constraints ---
        if self._config.enable_self_collision and len(soft_particle_indices) > 1:
            count += self._generate_self_collisions(
                soft_particle_indices, chain_connectivity
            )

        # --- Ground plane constraints ---
        if self._ground_y is not None:
            count += self._generate_ground_constraints(soft_particle_indices)

        return count

    def _generate_body_contacts(self, soft_indices: list[int]) -> int:
        """Generate contact constraints between soft particles and body proxies."""
        count = 0
        positions = self._solver.positions

        for proxy in self._body_proxies:
            center = np.array(proxy.center, dtype=np.float64)
            for idx in soft_indices:
                if self._solver._inv_masses[idx] <= 0:
                    continue
                delta = positions[idx] - center
                dist = float(np.linalg.norm(delta))
                min_dist = proxy.radius + self._solver._radii[idx]

                if dist < min_dist:
                    # Create a contact constraint
                    # We need a proxy particle for the body center
                    # For simplicity, we directly correct the position
                    if dist < _EPS:
                        continue

                    # Add as unilateral contact constraint
                    # We register the proxy as a temporary kinematic particle
                    if proxy.particle_index is not None:
                        c = XPBDConstraint(
                            kind=ConstraintKind.CONTACT,
                            particle_indices=(idx, proxy.particle_index),
                            rest_value=min_dist,
                            compliance=0.0,
                        )
                        self._solver._constraints.append(c)
                    else:
                        # Direct position correction (no proxy particle)
                        n = delta / dist
                        correction = (min_dist - dist) * n
                        self._solver._positions[idx] += correction
                    count += 1

        return count

    def _generate_self_collisions(
        self,
        soft_indices: list[int],
        connectivity: Optional[set[tuple[int, int]]] = None,
    ) -> int:
        """Generate self-collision constraints using spatial hashing."""
        if len(soft_indices) < 2:
            return 0

        positions = self._solver.positions
        radii = self._solver._radii

        # Build subset arrays
        subset_pos = positions[soft_indices]
        subset_radii = radii[soft_indices]

        # Find close pairs
        min_sep = self._config.self_collision_radius * 2.0
        pairs = self._spatial_hash.find_all_pairs(subset_pos, subset_radii, min_sep)

        count = 0
        connectivity = connectivity or set()

        for local_i, local_j, dist in pairs:
            global_i = soft_indices[local_i]
            global_j = soft_indices[local_j]

            # Skip directly connected particles
            pair = (min(global_i, global_j), max(global_i, global_j))
            if pair in connectivity:
                continue

            # Skip if both have zero inverse mass
            if (self._solver._inv_masses[global_i] <= 0 and
                    self._solver._inv_masses[global_j] <= 0):
                continue

            # Add self-collision constraint (minimum separation)
            min_dist = radii[global_i] + radii[global_j]
            c = XPBDConstraint(
                kind=ConstraintKind.SELF_COLLISION,
                particle_indices=(global_i, global_j),
                rest_value=min_dist,
                compliance=0.0,
            )
            self._solver._constraints.append(c)
            count += 1

        return count

    def _generate_ground_constraints(self, soft_indices: list[int]) -> int:
        """Generate ground-plane contact constraints."""
        if self._ground_y is None:
            return 0

        count = 0
        for idx in soft_indices:
            if self._solver._inv_masses[idx] <= 0:
                continue
            radius = self._solver._radii[idx]
            min_y = self._ground_y + radius
            if self._solver._positions[idx, 1] < min_y:
                self._solver._positions[idx, 1] = min_y
                # Zero out downward velocity
                if self._solver._velocities[idx, 1] < 0:
                    self._solver._velocities[idx, 1] *= -0.2  # Slight bounce
                count += 1

        return count


# ---------------------------------------------------------------------------
# Convenience: build body proxies from skeleton joint positions
# ---------------------------------------------------------------------------

def build_body_proxies_from_joints(
    joint_positions: dict[str, tuple[float, float]],
    joint_names: tuple[str, ...],
    joint_radii: tuple[float, ...],
) -> list[BodyCollisionProxy]:
    """Create body collision proxies from skeleton joint positions."""
    proxies: list[BodyCollisionProxy] = []
    for name, radius in zip(joint_names, joint_radii):
        if name in joint_positions:
            proxies.append(BodyCollisionProxy(
                center=joint_positions[name],
                radius=float(radius),
                label=name,
            ))
    return proxies


__all__ = [
    "SpatialHashGrid",
    "BodyCollisionProxy",
    "XPBDCollisionManager",
    "build_body_proxies_from_joints",
]
