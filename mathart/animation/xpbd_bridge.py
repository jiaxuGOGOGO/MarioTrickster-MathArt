"""XPBD Bridge: drop-in replacement for SecondaryChainProjector using XPBD.

This module bridges the new XPBD solver with the existing animation pipeline.
It provides ``XPBDChainProjector`` — a class with the **same public interface**
as ``SecondaryChainProjector`` so that the rest of the codebase (pipeline.py,
frame export, diagnostics) can switch between Jakobsen and XPBD with a single
flag.

Key architectural decisions:
  1. The rigid-body CoM is registered as a particle with finite inverse mass
     (``1/m_body``).  When two-way coupling is enabled, constraint corrections
     on soft nodes automatically produce reaction impulses on the CoM.
  2. Body collision proxies are regenerated every frame from skeleton FK.
  3. Self-collision constraints are regenerated every frame via spatial hashing.
  4. The bridge exposes ``reaction_impulse`` and ``com_displacement`` so that
     downstream systems (AI polish, motion graph) can observe the physical
     consequence of heavy-weapon swings.

Backward compatibility:
  - ``step_frame()`` and ``project_frame_sequence()`` have identical signatures
    to ``SecondaryChainProjector``.
  - ``SecondaryChainSnapshot`` and ``SecondaryChainDiagnostics`` are reused.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Optional

import numpy as np

from .skeleton import Skeleton
from .unified_motion import UnifiedMotionFrame, umr_to_pose
from .jakobsen_chain import (
    SecondaryChainDiagnostics,
    SecondaryChainSnapshot,
)
from .xpbd_solver import (
    ParticleKind,
    XPBDSolver,
    XPBDSolverConfig,
    XPBDChainPreset,
    XPBDDiagnostics,
    build_xpbd_chain,
    create_default_xpbd_presets,
)
from .xpbd_collision import (
    XPBDCollisionManager,
    BodyCollisionProxy,
    build_body_proxies_from_joints,
)

_EPS = 1e-8


class XPBDChainProjector:
    """XPBD-based secondary chain projector with true two-way coupling.

    Drop-in replacement for ``SecondaryChainProjector``.
    """

    def __init__(
        self,
        presets: Optional[list[XPBDChainPreset]] = None,
        *,
        solver_config: Optional[XPBDSolverConfig] = None,
        skeleton_ref: Optional[Skeleton] = None,
        head_units: float = 3.0,
        rigid_body_mass: float = 70.0,      # Character mass in kg
    ):
        self._skeleton = skeleton_ref or Skeleton.create_humanoid(head_units=head_units)
        self._solver_config = solver_config or XPBDSolverConfig()
        self._presets = presets or create_default_xpbd_presets(head_units=head_units)
        self._rigid_body_mass = rigid_body_mass
        self._head_units = head_units

        # State
        self._solver: Optional[XPBDSolver] = None
        self._collision_mgr: Optional[XPBDCollisionManager] = None
        self._rigid_com_index: Optional[int] = None
        self._chain_indices: dict[str, list[int]] = {}
        self._chain_connectivity: set[tuple[int, int]] = set()
        self._all_soft_indices: list[int] = []
        self._initialized = False
        self._last_xpbd_diagnostics: Optional[XPBDDiagnostics] = None

    def reset(self) -> None:
        """Reset solver state."""
        self._solver = None
        self._collision_mgr = None
        self._rigid_com_index = None
        self._chain_indices.clear()
        self._chain_connectivity.clear()
        self._all_soft_indices.clear()
        self._initialized = False
        self._last_xpbd_diagnostics = None

    def _initialize(self, anchor_positions: dict[str, tuple[float, float]], root_pos: tuple[float, float]) -> None:
        """Build the solver, register particles and constraints."""
        self._solver = XPBDSolver(self._solver_config)
        self._collision_mgr = XPBDCollisionManager(self._solver, self._solver_config)

        # Register rigid-body CoM
        self._rigid_com_index = self._solver.add_particle(
            root_pos,
            mass=self._rigid_body_mass,
            kind=ParticleKind.RIGID_COM,
        )

        # Build each chain
        self._chain_indices.clear()
        self._chain_connectivity.clear()
        self._all_soft_indices.clear()

        for preset in self._presets:
            if preset.anchor_joint not in anchor_positions:
                continue
            anchor_pos = anchor_positions[preset.anchor_joint]
            indices = build_xpbd_chain(
                self._solver,
                preset,
                anchor_pos,
                rigid_com_index=self._rigid_com_index if self._solver_config.enable_two_way_coupling else None,
            )
            self._chain_indices[preset.name] = indices
            self._all_soft_indices.extend(indices)

            # Track connectivity for self-collision exclusion
            for k in range(len(indices) - 1):
                pair = (min(indices[k], indices[k + 1]), max(indices[k], indices[k + 1]))
                self._chain_connectivity.add(pair)

            # Set ground
            if preset.ground_y is not None:
                self._collision_mgr.set_ground(preset.ground_y)

        self._initialized = True

    def _joint_positions_for_frame(self, frame: UnifiedMotionFrame) -> dict[str, tuple[float, float]]:
        """Extract world-space joint positions from a UMR frame."""
        pose = umr_to_pose(frame)
        self._skeleton.apply_pose(pose)
        positions = self._skeleton.forward_kinematics()
        root = frame.root_transform
        return {
            name: (float(x + root.x), float(y + root.y))
            for name, (x, y) in positions.items()
        }

    def step_frame(self, frame: UnifiedMotionFrame, dt: float = 1.0 / 60.0) -> UnifiedMotionFrame:
        """Advance one frame — same interface as SecondaryChainProjector."""
        joint_positions = self._joint_positions_for_frame(frame)
        root = frame.root_transform
        root_pos = (float(root.x), float(root.y))

        if not self._initialized:
            self._initialize(joint_positions, root_pos)

        # --- Update kinematic / rigid CoM position ---
        if self._rigid_com_index is not None:
            self._solver.update_position(self._rigid_com_index, root_pos)
            # Keep CoM as driven but with finite mass for coupling
            if self._solver_config.enable_two_way_coupling:
                self._solver._inv_masses[self._rigid_com_index] = 1.0 / max(self._rigid_body_mass, _EPS)
            else:
                self._solver._inv_masses[self._rigid_com_index] = 0.0

        # Update chain roots (kinematic nodes)
        for preset in self._presets:
            if preset.name not in self._chain_indices:
                continue
            if preset.anchor_joint not in joint_positions:
                continue
            indices = self._chain_indices[preset.name]
            anchor_pos = joint_positions[preset.anchor_joint]
            offset = np.array(preset.anchor_offset, dtype=np.float64)
            root_node_pos = (anchor_pos[0] + offset[0], anchor_pos[1] + offset[1])

            # If root is kinematic (no two-way coupling), directly pin it
            if not self._solver_config.enable_two_way_coupling:
                self._solver.update_position(indices[0], root_node_pos)

        # --- Generate collision constraints ---
        body_proxies: list[BodyCollisionProxy] = []
        for preset in self._presets:
            if preset.name not in self._chain_indices:
                continue
            proxies = build_body_proxies_from_joints(
                joint_positions,
                preset.body_collision_joints,
                preset.body_collision_radii,
            )
            body_proxies.extend(proxies)

        if self._collision_mgr is not None:
            self._collision_mgr.update_body_proxies(body_proxies)
            self._collision_mgr.generate_constraints(
                self._all_soft_indices,
                self._chain_connectivity,
            )

        # --- Step the solver ---
        xpbd_diag = self._solver.step(dt)
        self._last_xpbd_diagnostics = xpbd_diag

        # --- Extract chain snapshots ---
        chain_tracks: dict[str, list[list[float]]] = {}
        chain_debug: dict[str, dict] = {}

        for preset in self._presets:
            if preset.name not in self._chain_indices:
                continue
            indices = self._chain_indices[preset.name]
            points = [self._solver.get_position(idx) for idx in indices]

            # Compute Jakobsen-compatible diagnostics
            positions_arr = np.array(points, dtype=np.float64)
            if len(positions_arr) > 1:
                segments = np.diff(positions_arr, axis=0)
                lengths = np.linalg.norm(segments, axis=1)
                rest_lengths = np.full(len(lengths), preset.segment_length)
                errors = np.abs(lengths - rest_lengths)
                mean_err = float(np.mean(errors))
                max_err = float(np.max(errors))
                stretch = float(np.mean(lengths / np.maximum(rest_lengths, _EPS)))
            else:
                mean_err = max_err = 0.0
                stretch = 1.0

            tip_vel = self._solver.get_velocity(indices[-1])
            tip_speed = float(np.linalg.norm(tip_vel))
            anchor_pos = np.array(points[0], dtype=np.float64)
            tip_pos = np.array(points[-1], dtype=np.float64)
            tip_lag = float(np.linalg.norm(tip_pos - anchor_pos))

            chain_tracks[preset.name] = [[p[0], p[1]] for p in points]
            chain_debug[preset.name] = {
                "anchor_joint": preset.anchor_joint,
                "mean_constraint_error": mean_err,
                "max_constraint_error": max_err,
                "collision_count": xpbd_diag.contact_collision_count + xpbd_diag.self_collision_count,
                "tip_speed": tip_speed,
                "tip_lag": tip_lag,
                "anchor_speed": 0.0,
                "stretch_ratio": stretch,
                # XPBD-specific diagnostics
                "xpbd_rigid_com_displacement": xpbd_diag.rigid_com_displacement,
                "xpbd_reaction_impulse": xpbd_diag.reaction_impulse_magnitude,
                "xpbd_self_collision_count": xpbd_diag.self_collision_count,
                "xpbd_energy_estimate": xpbd_diag.energy_estimate,
                "xpbd_max_velocity": xpbd_diag.max_velocity_observed,
            }

        # --- Merge into frame metadata ---
        merged_metadata = dict(frame.metadata)
        merged_metadata.update({
            "secondary_chain_projected": True,
            "secondary_chain_count": len(chain_tracks),
            "secondary_chains": chain_tracks,
            "secondary_chain_debug": chain_debug,
            "xpbd_enabled": True,
            "xpbd_two_way_coupling": self._solver_config.enable_two_way_coupling,
            "xpbd_solver_diagnostics": xpbd_diag.to_dict(),
        })

        # If two-way coupling produced CoM displacement, record it
        if self._solver_config.enable_two_way_coupling and self._rigid_com_index is not None:
            com_pos = self._solver.get_position(self._rigid_com_index)
            com_vel = self._solver.get_velocity(self._rigid_com_index)
            merged_metadata["xpbd_com_position"] = list(com_pos)
            merged_metadata["xpbd_com_velocity"] = list(com_vel)
            merged_metadata["xpbd_reaction_impulse"] = xpbd_diag.reaction_impulse_magnitude

        return replace(frame, metadata=merged_metadata)

    def project_frame_sequence(
        self,
        frames: list[UnifiedMotionFrame],
        dt: float = 1.0 / 60.0,
    ) -> list[UnifiedMotionFrame]:
        """Project an entire frame sequence — same interface as SecondaryChainProjector."""
        self.reset()
        return [self.step_frame(frame, dt=dt) for frame in frames]

    @property
    def last_xpbd_diagnostics(self) -> Optional[XPBDDiagnostics]:
        return self._last_xpbd_diagnostics


__all__ = [
    "XPBDChainProjector",
]
