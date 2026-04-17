"""
SESSION-061: 2D Terrain-Adaptive IK Closed Loop (FABRIK + SDF Terrain)

Research foundations:
  1. **Sebastian Starke — MANN (SIGGRAPH 2018)**: Gating network for
     quadruped asymmetric gaits.  Our 2D IK solver consumes the per-limb
     contact labels from the distilled NSM runtime and uses them to decide
     which feet to pin to the terrain surface.

  2. **Daniel Holden — PFNN (SIGGRAPH 2017)**: Terrain heightmap as
     first-class input to the motion controller.  We implement the 2D
     equivalent: ``TerrainSDF.query(x, y)`` provides ground height at
     any horizontal position, and the IK solver adjusts ankle targets
     to match.

  3. **FABRIK (Aristidou & Lasenby 2011)**: Forward And Backward Reaching
     Inverse Kinematics — a fast, iterative IK solver that works by
     alternating forward and backward passes along a bone chain.  We
     implement a 2D specialisation that is compatible with the existing
     ``FABRIKGaitGenerator`` in ``biomechanics.py``.

Architecture:
    ┌──────────────────────────────────────────────────────────────────────┐
    │  TerrainProbe2D                                                     │
    │  ├─ probe_ground(x) → ground_y at horizontal position x            │
    │  ├─ probe_ahead(x, lookahead) → ground_y array for upcoming tiles  │
    │  └─ surface_normal_2d(x) → (nx, ny) surface normal                 │
    ├──────────────────────────────────────────────────────────────────────┤
    │  FABRIK2DSolver                                                     │
    │  ├─ solve(chain, target) → adjusted joint positions                 │
    │  ├─ solve_with_constraints(chain, target, angle_limits)             │
    │  └─ iterations / tolerance configuration                            │
    ├──────────────────────────────────────────────────────────────────────┤
    │  TerrainAdaptiveIKLoop                                              │
    │  ├─ adapt_pose(pose_2d, terrain) → terrain-adapted pose             │
    │  ├─ pin_feet_to_ground(pose, contact_labels, terrain)               │
    │  ├─ adjust_hip_height(pose, terrain_offset)                         │
    │  └─ evaluate_ik_quality(original, adapted) → IKQualityMetrics       │
    └──────────────────────────────────────────────────────────────────────┘

Usage::

    from mathart.animation.terrain_ik_2d import (
        TerrainProbe2D, FABRIK2DSolver, TerrainAdaptiveIKLoop,
        IKConfig, IKQualityMetrics,
    )

    probe = TerrainProbe2D(terrain_sdf)
    solver = FABRIK2DSolver(IKConfig(max_iterations=10, tolerance=0.001))
    loop = TerrainAdaptiveIKLoop(probe, solver)
    adapted_pose = loop.adapt_pose(pose_2d, contact_labels)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from typing import Any, Optional, Sequence, Tuple

import numpy as np


# ═══════════════════════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class IKConfig:
    """Configuration for the 2D FABRIK IK solver."""

    max_iterations: int = 10
    tolerance: float = 0.001
    hip_adjustment_factor: float = 0.5
    foot_ground_offset: float = 0.0
    ankle_softness: float = 0.0
    min_angle: float = -160.0
    max_angle: float = 160.0


@dataclass
class IKQualityMetrics:
    """Quality metrics for evaluating IK adaptation."""

    foot_terrain_error: float = 0.0
    hip_height_delta: float = 0.0
    knee_angle_validity: float = 0.0
    convergence_iterations: float = 0.0
    contact_accuracy: float = 0.0
    total_chains_solved: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ═══════════════════════════════════════════════════════════════════════════════
# Terrain Probe 2D
# ═══════════════════════════════════════════════════════════════════════════════

class TerrainProbe2D:
    """2D terrain height query interface.

    Wraps a ``TerrainSDF`` instance (from ``terrain_sensor.py``) to provide
    ground-height queries in 2D.  This is the 2D equivalent of
    ``Physics2D.Raycast`` in Unity — it casts a vertical ray downward from
    a given X position and returns the Y coordinate of the terrain surface.
    """

    def __init__(self, terrain_sdf: Any = None) -> None:
        self._terrain = terrain_sdf

    def probe_ground(self, x: float, ray_origin_y: float = 10.0) -> float:
        """Return the ground Y coordinate at horizontal position *x*.

        If no terrain SDF is available, returns 0.0 (flat ground).
        """
        if self._terrain is None:
            return 0.0
        try:
            dist = self._terrain.query(x, ray_origin_y)
            return ray_origin_y - abs(dist)
        except (AttributeError, TypeError):
            return 0.0

    def probe_ahead(
        self,
        x: float,
        lookahead: float = 1.0,
        n_samples: int = 5,
    ) -> list[tuple[float, float]]:
        """Probe terrain heights ahead of position *x*.

        Returns a list of ``(sample_x, ground_y)`` pairs.
        """
        results: list[tuple[float, float]] = []
        dx = lookahead / max(n_samples - 1, 1)
        for i in range(n_samples):
            sx = x + i * dx
            gy = self.probe_ground(sx)
            results.append((sx, gy))
        return results

    def surface_normal_2d(self, x: float, epsilon: float = 0.01) -> tuple[float, float]:
        """Compute the approximate 2D surface normal at position *x*."""
        y_left = self.probe_ground(x - epsilon)
        y_right = self.probe_ground(x + epsilon)
        dx = 2.0 * epsilon
        dy = y_right - y_left
        length = math.sqrt(dx * dx + dy * dy)
        if length < 1e-12:
            return (0.0, 1.0)
        nx = -dy / length
        ny = dx / length
        return (nx, ny)


# ═══════════════════════════════════════════════════════════════════════════════
# FABRIK 2D Solver
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Joint2D:
    """A 2D joint position in a kinematic chain."""

    x: float = 0.0
    y: float = 0.0
    name: str = ""


class FABRIK2DSolver:
    """Forward And Backward Reaching Inverse Kinematics in 2D.

    This is a pure 2D implementation of the FABRIK algorithm
    (Aristidou & Lasenby 2011) specialised for leg chains in
    side-scrolling character animation.  It supports optional
    angular constraints to prevent hyper-extension.
    """

    def __init__(self, config: Optional[IKConfig] = None) -> None:
        self.config = config or IKConfig()

    def _distance(self, a: Joint2D, b: Joint2D) -> float:
        return math.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2)

    def _move_towards(
        self,
        current: Joint2D,
        target: Joint2D,
        desired_dist: float,
    ) -> Joint2D:
        """Move *current* so that it is *desired_dist* away from *target*."""
        dx = current.x - target.x
        dy = current.y - target.y
        dist = math.sqrt(dx * dx + dy * dy)
        if dist < 1e-12:
            return Joint2D(target.x + desired_dist, target.y, current.name)
        ratio = desired_dist / dist
        return Joint2D(
            target.x + dx * ratio,
            target.y + dy * ratio,
            current.name,
        )

    def solve(
        self,
        chain: list[Joint2D],
        target: Joint2D,
    ) -> tuple[list[Joint2D], int]:
        """Solve IK for a chain of joints to reach *target*.

        Returns the adjusted chain and the number of iterations used.
        """
        if len(chain) < 2:
            return chain, 0

        # Compute segment lengths
        n = len(chain)
        lengths = [
            self._distance(chain[i], chain[i + 1]) for i in range(n - 1)
        ]
        total_length = sum(lengths)

        # Check reachability
        root_to_target = self._distance(chain[0], target)
        if root_to_target > total_length:
            # Target unreachable: stretch towards it
            for i in range(n - 1):
                t_dist = self._distance(chain[i], target)
                if t_dist < 1e-12:
                    continue
                ratio = lengths[i] / t_dist
                chain[i + 1] = Joint2D(
                    chain[i].x + (target.x - chain[i].x) * ratio,
                    chain[i].y + (target.y - chain[i].y) * ratio,
                    chain[i + 1].name,
                )
            return chain, 1

        root = Joint2D(chain[0].x, chain[0].y, chain[0].name)
        iterations = 0

        for _ in range(self.config.max_iterations):
            iterations += 1

            # Forward pass: move end effector to target
            chain[n - 1] = Joint2D(target.x, target.y, chain[n - 1].name)
            for i in range(n - 2, -1, -1):
                chain[i] = self._move_towards(chain[i], chain[i + 1], lengths[i])

            # Backward pass: move root back to original position
            chain[0] = Joint2D(root.x, root.y, chain[0].name)
            for i in range(n - 1):
                chain[i + 1] = self._move_towards(
                    chain[i + 1], chain[i], lengths[i]
                )

            # Check convergence
            end_dist = self._distance(chain[n - 1], target)
            if end_dist < self.config.tolerance:
                break

        return chain, iterations

    def solve_with_constraints(
        self,
        chain: list[Joint2D],
        target: Joint2D,
        angle_limits: Optional[list[tuple[float, float]]] = None,
    ) -> tuple[list[Joint2D], int]:
        """Solve IK with optional per-joint angular constraints.

        Angular constraints are specified as ``(min_angle, max_angle)``
        pairs in degrees for each joint (excluding the root).
        """
        chain, iters = self.solve(chain, target)

        if angle_limits and len(chain) >= 3:
            for i in range(1, len(chain) - 1):
                if i - 1 >= len(angle_limits):
                    break
                min_a, max_a = angle_limits[i - 1]

                # Compute angle at joint i
                prev = chain[i - 1]
                curr = chain[i]
                nxt = chain[i + 1]

                a1 = math.atan2(prev.y - curr.y, prev.x - curr.x)
                a2 = math.atan2(nxt.y - curr.y, nxt.x - curr.x)
                angle = math.degrees(a2 - a1)
                angle = ((angle + 180) % 360) - 180

                if angle < min_a:
                    clamped = min_a
                elif angle > max_a:
                    clamped = max_a
                else:
                    continue

                # Reposition next joint to satisfy constraint
                dist = self._distance(curr, nxt)
                base_angle = math.atan2(prev.y - curr.y, prev.x - curr.x)
                new_angle = base_angle + math.radians(clamped)
                chain[i + 1] = Joint2D(
                    curr.x + dist * math.cos(new_angle),
                    curr.y + dist * math.sin(new_angle),
                    nxt.name,
                )

        return chain, iters


# ═══════════════════════════════════════════════════════════════════════════════
# Terrain-Adaptive IK Loop
# ═══════════════════════════════════════════════════════════════════════════════

class TerrainAdaptiveIKLoop:
    """Closed-loop 2D IK that pins character feet to terrain.

    This is the core integration point that connects:
    - NSM gait output (contact labels, target offsets)
    - Terrain SDF queries (ground height at foot X position)
    - FABRIK 2D solver (ankle-to-terrain pinning)

    The loop implements the terrain adaptation strategy described in
    Phase 3: for each foot in ground contact, query the terrain height,
    set the ankle IK target to that height, solve the leg chain with
    FABRIK, and adjust the hip height to maintain body coherence.
    """

    def __init__(
        self,
        probe: Optional[TerrainProbe2D] = None,
        solver: Optional[FABRIK2DSolver] = None,
        config: Optional[IKConfig] = None,
    ) -> None:
        self.probe = probe or TerrainProbe2D()
        self.config = config or IKConfig()
        self.solver = solver or FABRIK2DSolver(self.config)

    def _build_leg_chain(
        self,
        hip_x: float,
        hip_y: float,
        knee_x: float,
        knee_y: float,
        ankle_x: float,
        ankle_y: float,
        side: str = "l",
    ) -> list[Joint2D]:
        """Build a 3-joint leg chain (hip → knee → ankle)."""
        return [
            Joint2D(hip_x, hip_y, f"{side}_hip"),
            Joint2D(knee_x, knee_y, f"{side}_knee"),
            Joint2D(ankle_x, ankle_y, f"{side}_ankle"),
        ]

    def pin_foot_to_ground(
        self,
        chain: list[Joint2D],
        ground_y: float,
    ) -> tuple[list[Joint2D], int]:
        """Pin the end effector (ankle/foot) of a leg chain to ground_y."""
        if not chain:
            return chain, 0
        ankle = chain[-1]
        target = Joint2D(
            ankle.x,
            ground_y + self.config.foot_ground_offset,
            ankle.name,
        )
        knee_limits = [
            (self.config.min_angle, self.config.max_angle),
        ]
        return self.solver.solve_with_constraints(chain, target, knee_limits)

    def adapt_pose(
        self,
        pose_data: dict[str, Any],
        contact_labels: dict[str, float],
        hip_position: tuple[float, float] = (0.0, 0.8),
    ) -> dict[str, Any]:
        """Adapt a 2D pose to terrain using IK.

        Parameters
        ----------
        pose_data : dict
            Joint positions as ``{joint_name: (x, y)}``.
        contact_labels : dict
            Per-foot contact probability from NSM (0.0 = swing, 1.0 = stance).
        hip_position : tuple
            Current hip (x, y) position.

        Returns
        -------
        dict
            Adapted pose data with terrain-pinned feet and adjusted hip.
        """
        result = dict(pose_data)
        hip_x, hip_y = hip_position
        total_ground_offset = 0.0
        n_contacts = 0
        ik_iterations_total = 0

        for side in ("l", "r"):
            foot_key = f"{side}_foot"
            contact = contact_labels.get(foot_key, 0.0)

            if contact < 0.5:
                continue

            # Get joint positions from pose data
            hip_pos = pose_data.get(f"{side}_hip", (hip_x + (-0.1 if side == "l" else 0.1), hip_y))
            knee_pos = pose_data.get(f"{side}_knee", (hip_pos[0], hip_pos[1] - 0.3))
            ankle_pos = pose_data.get(f"{side}_ankle", (knee_pos[0], knee_pos[1] - 0.3))

            # Query terrain height at ankle X
            ground_y = self.probe.probe_ground(ankle_pos[0])

            # Build chain and solve
            chain = self._build_leg_chain(
                hip_pos[0], hip_pos[1],
                knee_pos[0], knee_pos[1],
                ankle_pos[0], ankle_pos[1],
                side,
            )
            solved_chain, iters = self.pin_foot_to_ground(chain, ground_y)
            ik_iterations_total += iters

            # Write back solved positions
            for joint in solved_chain:
                result[joint.name] = (joint.x, joint.y)

            # Track ground offset for hip adjustment
            original_ankle_y = ankle_pos[1]
            solved_ankle_y = solved_chain[-1].y
            total_ground_offset += (solved_ankle_y - original_ankle_y)
            n_contacts += 1

        # Adjust hip height based on average ground offset
        if n_contacts > 0:
            avg_offset = total_ground_offset / n_contacts
            adjusted_hip_y = hip_y + avg_offset * self.config.hip_adjustment_factor
            result["hip"] = (hip_x, adjusted_hip_y)
            result["_hip_adjustment"] = avg_offset * self.config.hip_adjustment_factor

        result["_ik_iterations"] = ik_iterations_total
        result["_contacts_solved"] = n_contacts
        return result

    def adapt_quadruped_pose(
        self,
        pose_data: dict[str, Any],
        contact_labels: dict[str, float],
        spine_position: tuple[float, float] = (0.0, 0.5),
    ) -> dict[str, Any]:
        """Adapt a quadruped 2D pose to terrain using IK.

        Handles four legs: front_left, front_right, hind_left, hind_right.
        Each leg is a 3-joint chain (shoulder/hip → elbow/knee → paw).
        """
        result = dict(pose_data)
        spine_x, spine_y = spine_position
        total_offset = 0.0
        n_contacts = 0

        limb_map = {
            "front_left": ("fl_upper", "fl_lower", "fl_paw"),
            "front_right": ("fr_upper", "fr_lower", "fr_paw"),
            "hind_left": ("hl_upper", "hl_lower", "hl_paw"),
            "hind_right": ("hr_upper", "hr_lower", "hr_paw"),
        }

        for limb_name, (upper, lower, paw) in limb_map.items():
            contact = contact_labels.get(limb_name, 0.0)
            if contact < 0.5:
                continue

            upper_pos = pose_data.get(upper, (spine_x, spine_y - 0.05))
            lower_pos = pose_data.get(lower, (upper_pos[0], upper_pos[1] - 0.2))
            paw_pos = pose_data.get(paw, (lower_pos[0], lower_pos[1] - 0.2))

            ground_y = self.probe.probe_ground(paw_pos[0])

            prefix = limb_name.replace("_", "")[:2]
            chain = [
                Joint2D(upper_pos[0], upper_pos[1], upper),
                Joint2D(lower_pos[0], lower_pos[1], lower),
                Joint2D(paw_pos[0], paw_pos[1], paw),
            ]

            target = Joint2D(paw_pos[0], ground_y + self.config.foot_ground_offset, paw)
            solved, iters = self.solver.solve(chain, target)

            for joint in solved:
                result[joint.name] = (joint.x, joint.y)

            total_offset += (solved[-1].y - paw_pos[1])
            n_contacts += 1

        if n_contacts > 0:
            avg_offset = total_offset / n_contacts
            result["spine_base"] = (spine_x, spine_y + avg_offset * self.config.hip_adjustment_factor)
            result["_spine_adjustment"] = avg_offset * self.config.hip_adjustment_factor

        result["_quadruped_contacts_solved"] = n_contacts
        return result

    def evaluate_ik_quality(
        self,
        original_pose: dict[str, Any],
        adapted_pose: dict[str, Any],
        contact_labels: dict[str, float],
    ) -> IKQualityMetrics:
        """Evaluate the quality of IK terrain adaptation."""
        metrics = IKQualityMetrics()

        foot_errors: list[float] = []
        for side in ("l", "r"):
            foot_key = f"{side}_foot"
            ankle_key = f"{side}_ankle"
            if contact_labels.get(foot_key, 0.0) < 0.5:
                continue
            if ankle_key in adapted_pose:
                ax, ay = adapted_pose[ankle_key]
                ground_y = self.probe.probe_ground(ax)
                foot_errors.append(abs(ay - ground_y - self.config.foot_ground_offset))
                metrics.total_chains_solved += 1

        metrics.foot_terrain_error = (
            float(np.mean(foot_errors)) if foot_errors else 0.0
        )
        metrics.hip_height_delta = abs(
            float(adapted_pose.get("_hip_adjustment", 0.0))
        )
        metrics.convergence_iterations = float(
            adapted_pose.get("_ik_iterations", 0)
        )

        # Contact accuracy: did we solve all contacts?
        expected = sum(1 for v in contact_labels.values() if v >= 0.5)
        solved = int(adapted_pose.get("_contacts_solved", 0))
        metrics.contact_accuracy = (
            solved / expected if expected > 0 else 1.0
        )

        return metrics


# ═══════════════════════════════════════════════════════════════════════════════
# Convenience
# ═══════════════════════════════════════════════════════════════════════════════

def create_terrain_ik_loop(
    terrain_sdf: Any = None,
    config: Optional[IKConfig] = None,
) -> TerrainAdaptiveIKLoop:
    """Factory function for creating a terrain-adaptive IK loop."""
    cfg = config or IKConfig()
    probe = TerrainProbe2D(terrain_sdf)
    solver = FABRIK2DSolver(cfg)
    return TerrainAdaptiveIKLoop(probe, solver, cfg)


__all__ = [
    "IKConfig",
    "IKQualityMetrics",
    "TerrainProbe2D",
    "Joint2D",
    "FABRIK2DSolver",
    "TerrainAdaptiveIKLoop",
    "create_terrain_ik_loop",
]
