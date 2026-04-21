"""UMR Kinematic Impulse Adapter — P1-VFX-1B.

SESSION-115: Drive fluid VFX directly from UMR root velocity and weapon
trajectories.  This module is the **sole bridge** between the Unified Motion
Representation (UMR) and the Stable-Fluids VFX system.  It reads
``UnifiedMotionClip`` data, extracts physically grounded kinematic vectors,
and emits strongly-typed impulse contracts that the fluid solver consumes
without any ``if action == 'slash'`` branching in the core solver.

Research Foundations
--------------------
1. **GPU Gems 3, Ch. 30 (Crane, Llamas, Tariq)** — Gaussian-kernel velocity
   splatting into Eulerian grids; free-slip BC via obstacle-velocity
   voxelization; anti-leak advection constraints.
2. **Continuous Trajectory Splatting** — High-speed weapon arcs must inject
   momentum along the *full line segment* between consecutive tip positions,
   not at discrete points.  The distance field is the analytical minimum
   distance from every grid cell to the trajectory segment, weighted by a
   Gaussian kernel exp(-d² / r²).
3. **Naughty Dog / Sucker Punch Animation-Driven VFX** — Fluid forces
   inherit the character's physical kinetic energy.  Root-node displacement
   deltas and end-effector tangential velocities are the canonical source of
   external force F_ext.
4. **CFL Guard (Courant-Friedrichs-Lewy)** — Injected velocities are
   soft-clamped via tanh scaling to keep the Courant number below unity,
   preventing advection blow-up and NaN propagation.

Architecture Discipline
-----------------------
- **No core-solver modification**: This adapter is a standalone module.
  It does NOT touch ``FluidGrid2D``, ``FluidDrivenVFXSystem``, or any
  orchestrator / pipeline routing code.
- **Strong-typed contracts**: All outputs are frozen dataclasses
  (``VectorFieldImpulse``, ``KinematicFrame``) with explicit physical units.
- **Pure tensor operations**: All hot-path computations use NumPy
  broadcasting.  Zero Python ``for`` loops in the grid-computation path.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

import numpy as np

from .unified_motion import (
    MotionRootTransform,
    UnifiedMotionClip,
    UnifiedMotionFrame,
)

# ═══════════════════════════════════════════════════════════════════════════
# Strong-Typed Contracts
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class KinematicFrame:
    """Per-frame kinematic state extracted from a UMR clip.

    All positions are in normalised fluid-domain coordinates [0, 1]².
    Velocities are in normalised-domain units per second.

    Attributes
    ----------
    time : float
        Absolute time of this frame in seconds.
    root_position : tuple[float, float]
        (x, y) root position in normalised domain.
    root_velocity : tuple[float, float]
        (vx, vy) root velocity (central finite difference or UMR native).
    effector_positions : dict[str, tuple[float, float]]
        Named end-effector positions (e.g. ``weapon_tip``).
    effector_velocities : dict[str, tuple[float, float]]
        Named end-effector velocities.
    source_state : str
        The UMR source_state label (e.g. ``dash``, ``slash``, ``idle``).
    """

    time: float = 0.0
    root_position: tuple[float, float] = (0.5, 0.5)
    root_velocity: tuple[float, float] = (0.0, 0.0)
    effector_positions: dict[str, tuple[float, float]] = field(
        default_factory=dict
    )
    effector_velocities: dict[str, tuple[float, float]] = field(
        default_factory=dict
    )
    source_state: str = ""


@dataclass(frozen=True)
class VectorFieldImpulse:
    """A continuous line-segment impulse for the fluid solver.

    Represents a momentum injection along a trajectory segment from
    ``start`` to ``end`` with a Gaussian influence ``radius`` and a
    physical ``velocity_vector``.

    The fluid solver should compute, for every grid cell, the analytical
    minimum distance to this segment and apply:

        weight = exp(-d² / radius²)
        F_ext += velocity_vector * weight

    Attributes
    ----------
    start : tuple[float, float]
        Segment start in normalised domain [0, 1]².
    end : tuple[float, float]
        Segment end in normalised domain [0, 1]².
    velocity_vector : tuple[float, float]
        Physical velocity to inject (already CFL-clamped).
    radius : float
        Gaussian influence radius in normalised domain units.
    density_amount : float
        Density to inject along the segment (smoke/dust mass).
    label : str
        Human-readable label for diagnostics.
    gaussian_decay : float
        Decay exponent modifier (default 1.0 = standard Gaussian).
    """

    start: tuple[float, float] = (0.5, 0.5)
    end: tuple[float, float] = (0.5, 0.5)
    velocity_vector: tuple[float, float] = (0.0, 0.0)
    radius: float = 0.12
    density_amount: float = 1.0
    label: str = ""
    gaussian_decay: float = 1.0


# ═══════════════════════════════════════════════════════════════════════════
# CFL-Safe Velocity Clamping (Anti-Energy-Explosion Guard)
# ═══════════════════════════════════════════════════════════════════════════


def soft_tanh_clamp(
    velocity: tuple[float, float],
    v_max: float,
) -> tuple[float, float]:
    """Soft-clamp a velocity vector using tanh scaling.

    Preserves direction while smoothly limiting magnitude to ``v_max``.
    This prevents CFL violation when injecting high-speed weapon arcs
    into the fluid grid.

    Parameters
    ----------
    velocity : tuple[float, float]
        Raw velocity (vx, vy).
    v_max : float
        Maximum safe velocity magnitude.

    Returns
    -------
    tuple[float, float]
        Clamped velocity.
    """
    vx, vy = velocity
    mag = math.hypot(vx, vy)
    if mag < 1e-12:
        return (0.0, 0.0)
    # tanh(mag / v_max) * v_max → asymptotes to v_max
    clamped_mag = v_max * math.tanh(mag / max(v_max, 1e-12))
    scale = clamped_mag / mag
    return (vx * scale, vy * scale)


def compute_cfl_safe_velocity_limit(
    grid_size: int,
    dt: float,
    cfl_factor: float = 0.5,
) -> float:
    """Compute the maximum safe injection velocity for CFL stability.

    The CFL condition requires: |u| * dt / dx <= C_max.
    With dx = 1/grid_size, this gives |u| <= C_max * grid_size / dt.

    Parameters
    ----------
    grid_size : int
        Fluid grid resolution (N).
    dt : float
        Simulation time step.
    cfl_factor : float
        Target Courant number (0 < C <= 1).  Default 0.5 for safety margin.

    Returns
    -------
    float
        Maximum safe velocity magnitude in normalised domain units.
    """
    dx = 1.0 / max(grid_size, 1)
    return cfl_factor * dx / max(dt, 1e-12)


# ═══════════════════════════════════════════════════════════════════════════
# Tensor-Based Line-Segment Splatter
# ═══════════════════════════════════════════════════════════════════════════


class LineSegmentSplatter:
    """Tensor-based continuous line-segment impulse injection.

    Computes, for an entire (N, N) fluid grid, the analytical minimum
    distance from every cell centre to a trajectory line segment, then
    applies a Gaussian-weighted velocity field.

    **Anti-Dotted-Line Guard**: This class NEVER iterates over individual
    points.  All distance computations use ``np.meshgrid`` and NumPy
    broadcasting — zero Python ``for`` loops.

    **Anti-Scalar-Grid-Loop Guard**: The hot path is a single vectorised
    expression over the full grid tensor.

    Research grounding:
    - GPU Gems 3, Ch. 30: Gaussian velocity splatting into Eulerian grids.
    - Continuous Trajectory Splatting: line-segment distance field for
      smooth crescent-shaped momentum injection.
    """

    def __init__(self, grid_size: int):
        self.grid_size = grid_size
        # Pre-compute normalised cell-centre coordinates [0, 1]²
        # Shape: (N, N) each — row = y, col = x
        lin = (np.arange(grid_size) + 0.5) / grid_size
        self._cx, self._cy = np.meshgrid(lin, lin)  # (N, N)

    def compute_segment_distance_field(
        self,
        p0: tuple[float, float],
        p1: tuple[float, float],
    ) -> np.ndarray:
        """Compute analytical minimum distance from all cells to segment P0→P1.

        Uses the standard parametric projection:
            t = clamp(dot(Q - P0, P1 - P0) / |P1 - P0|², 0, 1)
            nearest = P0 + t * (P1 - P0)
            distance = |Q - nearest|

        All operations are pure tensor (NumPy broadcasting).

        Parameters
        ----------
        p0, p1 : tuple[float, float]
            Segment endpoints in normalised [0, 1]² domain.

        Returns
        -------
        np.ndarray
            (N, N) distance field.
        """
        ax, ay = p0
        bx, by = p1
        dx = bx - ax
        dy = by - ay
        seg_len_sq = dx * dx + dy * dy

        if seg_len_sq < 1e-18:
            # Degenerate segment → point distance
            return np.sqrt(
                (self._cx - ax) ** 2 + (self._cy - ay) ** 2
            )

        # Parametric projection of each cell onto the segment
        # t = dot(Q - A, B - A) / |B - A|²
        t = ((self._cx - ax) * dx + (self._cy - ay) * dy) / seg_len_sq
        t = np.clip(t, 0.0, 1.0)

        # Nearest point on segment
        nearest_x = ax + t * dx
        nearest_y = ay + t * dy

        # Distance from cell to nearest point
        dist = np.sqrt(
            (self._cx - nearest_x) ** 2 + (self._cy - nearest_y) ** 2
        )
        return dist

    def splat_impulse(
        self,
        impulse: VectorFieldImpulse,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Splat a VectorFieldImpulse onto the grid.

        Returns velocity-x, velocity-y, and density contribution fields,
        each of shape (N, N).

        Parameters
        ----------
        impulse : VectorFieldImpulse
            The impulse to inject.

        Returns
        -------
        tuple[np.ndarray, np.ndarray, np.ndarray]
            (force_u, force_v, density) fields of shape (N, N).
        """
        dist = self.compute_segment_distance_field(impulse.start, impulse.end)
        r = max(impulse.radius, 1e-9)
        decay = impulse.gaussian_decay

        # Gaussian weight: exp(-d² / r²)  (decay modifies exponent)
        weight = np.exp(-(dist ** 2) / (r ** 2) * decay)

        vx, vy = impulse.velocity_vector
        force_u = vx * weight
        force_v = vy * weight
        density = impulse.density_amount * weight

        return force_u, force_v, density

    def splat_multiple(
        self,
        impulses: Sequence[VectorFieldImpulse],
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Splat multiple impulses, accumulating into a single field.

        Parameters
        ----------
        impulses : sequence of VectorFieldImpulse
            All impulses for this frame.

        Returns
        -------
        tuple[np.ndarray, np.ndarray, np.ndarray]
            Accumulated (force_u, force_v, density) fields.
        """
        n = self.grid_size
        total_u = np.zeros((n, n), dtype=np.float64)
        total_v = np.zeros((n, n), dtype=np.float64)
        total_d = np.zeros((n, n), dtype=np.float64)

        for imp in impulses:
            fu, fv, fd = self.splat_impulse(imp)
            total_u += fu
            total_v += fv
            total_d += fd

        return total_u, total_v, total_d


# ═══════════════════════════════════════════════════════════════════════════
# UMR Kinematic Momentum Extractor
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class UMRExtractionConfig:
    """Configuration for the kinematic momentum extractor.

    Attributes
    ----------
    domain_scale_x : float
        Horizontal scale mapping world units to normalised [0, 1] domain.
    domain_scale_y : float
        Vertical scale mapping world units to normalised [0, 1] domain.
    domain_offset_x : float
        Horizontal offset for world-to-domain mapping.
    domain_offset_y : float
        Vertical offset for world-to-domain mapping.
    root_impulse_radius : float
        Gaussian radius for root-velocity impulses.
    effector_impulse_radius : float
        Gaussian radius for end-effector (weapon) impulses.
    root_density_gain : float
        Density multiplier for root-driven impulses.
    effector_density_gain : float
        Density multiplier for effector-driven impulses.
    cfl_factor : float
        CFL safety factor for velocity clamping.
    use_central_difference : bool
        If True, compute velocities via central finite difference even
        when UMR frames already carry velocity fields.  Useful for
        verifying UMR velocity accuracy.
    """

    domain_scale_x: float = 1.0
    domain_scale_y: float = 1.0
    domain_offset_x: float = 0.0
    domain_offset_y: float = 0.0
    root_impulse_radius: float = 0.10
    effector_impulse_radius: float = 0.08
    root_density_gain: float = 1.0
    effector_density_gain: float = 1.2
    cfl_factor: float = 0.5
    use_central_difference: bool = False


class UMRKinematicImpulseAdapter:
    """Adapter that converts UMR clip data into fluid VectorFieldImpulses.

    This is the canonical bridge between the animation system (UMR) and the
    fluid VFX system.  It reads a ``UnifiedMotionClip``, extracts per-frame
    kinematic vectors (root velocity, weapon-tip trajectory segments), and
    emits a sequence of ``VectorFieldImpulse`` lists — one list per frame.

    The adapter does NOT modify any core solver code.  It is a standalone
    high-order module that plugs into the ``FluidDrivenVFXSystem`` via the
    existing ``driver_impulses`` parameter.

    Usage
    -----
    >>> adapter = UMRKinematicImpulseAdapter(grid_size=32)
    >>> impulse_sequence = adapter.extract_impulses(clip)
    >>> # impulse_sequence[i] is a list of VectorFieldImpulse for frame i
    """

    def __init__(
        self,
        grid_size: int,
        config: Optional[UMRExtractionConfig] = None,
    ):
        self.grid_size = grid_size
        self.config = config or UMRExtractionConfig()

    def _world_to_domain(self, x: float, y: float) -> tuple[float, float]:
        """Map world coordinates to normalised [0, 1]² domain."""
        cfg = self.config
        nx = (x - cfg.domain_offset_x) * cfg.domain_scale_x
        ny = (y - cfg.domain_offset_y) * cfg.domain_scale_y
        return (
            min(max(nx, 0.02), 0.98),
            min(max(ny, 0.02), 0.98),
        )

    def extract_kinematic_frames(
        self,
        clip: UnifiedMotionClip,
        effector_key: str = "weapon_tip",
    ) -> list[KinematicFrame]:
        """Extract per-frame kinematic state from a UMR clip.

        Uses central finite difference for root velocity when configured,
        otherwise uses the native UMR velocity fields.

        For end-effector tracking, looks for ``effector_key`` in the frame
        metadata (expected as ``{effector_key}_x``, ``{effector_key}_y``).

        Parameters
        ----------
        clip : UnifiedMotionClip
            The source UMR clip.
        effector_key : str
            Metadata key prefix for end-effector position.

        Returns
        -------
        list[KinematicFrame]
            One KinematicFrame per clip frame.
        """
        frames = clip.frames
        n = len(frames)
        if n == 0:
            return []

        fps = max(clip.fps, 1)
        dt = 1.0 / fps
        result: list[KinematicFrame] = []

        for i, frame in enumerate(frames):
            rt = frame.root_transform

            # Root position
            rx, ry = self._world_to_domain(rt.x, rt.y)

            # Root velocity: central finite difference or native UMR
            if self.config.use_central_difference and n >= 3:
                if 0 < i < n - 1:
                    prev_rt = frames[i - 1].root_transform
                    next_rt = frames[i + 1].root_transform
                    vx = (next_rt.x - prev_rt.x) / (2.0 * dt)
                    vy = (next_rt.y - prev_rt.y) / (2.0 * dt)
                elif i == 0 and n > 1:
                    next_rt = frames[1].root_transform
                    vx = (next_rt.x - rt.x) / dt
                    vy = (next_rt.y - rt.y) / dt
                else:
                    prev_rt = frames[n - 2].root_transform
                    vx = (rt.x - prev_rt.x) / dt
                    vy = (rt.y - prev_rt.y) / dt
                # Scale to domain
                vx *= self.config.domain_scale_x
                vy *= self.config.domain_scale_y
            else:
                vx = rt.velocity_x * self.config.domain_scale_x
                vy = rt.velocity_y * self.config.domain_scale_y

            # End-effector positions from metadata
            eff_positions: dict[str, tuple[float, float]] = {}
            eff_velocities: dict[str, tuple[float, float]] = {}

            ex_key = f"{effector_key}_x"
            ey_key = f"{effector_key}_y"
            if ex_key in frame.metadata and ey_key in frame.metadata:
                ex = float(frame.metadata[ex_key])
                ey = float(frame.metadata[ey_key])
                eff_positions[effector_key] = self._world_to_domain(ex, ey)

                # Effector velocity via finite difference
                if i > 0:
                    prev_meta = frames[i - 1].metadata
                    if ex_key in prev_meta and ey_key in prev_meta:
                        prev_ex = float(prev_meta[ex_key])
                        prev_ey = float(prev_meta[ey_key])
                        evx = (ex - prev_ex) / dt * self.config.domain_scale_x
                        evy = (ey - prev_ey) / dt * self.config.domain_scale_y
                        eff_velocities[effector_key] = (evx, evy)

            result.append(KinematicFrame(
                time=frame.time,
                root_position=(rx, ry),
                root_velocity=(vx, vy),
                effector_positions=eff_positions,
                effector_velocities=eff_velocities,
                source_state=frame.source_state,
            ))

        return result

    def extract_impulses(
        self,
        clip: UnifiedMotionClip,
        effector_key: str = "weapon_tip",
    ) -> list[list[VectorFieldImpulse]]:
        """Extract per-frame VectorFieldImpulse lists from a UMR clip.

        This is the main entry point.  For each frame, it produces:
        1. A root-velocity impulse (line segment from prev to current root pos).
        2. An effector-trajectory impulse (line segment from prev to current
           effector pos) if effector data is available.

        All velocities are CFL-clamped via soft tanh scaling.

        Parameters
        ----------
        clip : UnifiedMotionClip
            The source UMR clip.
        effector_key : str
            Metadata key prefix for end-effector position.

        Returns
        -------
        list[list[VectorFieldImpulse]]
            One list of impulses per frame.
        """
        cfg = self.config
        kinematic_frames = self.extract_kinematic_frames(clip, effector_key)
        n = len(kinematic_frames)
        if n == 0:
            return []

        # Compute CFL-safe velocity limit
        fps = max(clip.fps, 1)
        dt = 1.0 / fps
        v_max = compute_cfl_safe_velocity_limit(
            self.grid_size, dt, cfg.cfl_factor
        )

        impulse_sequence: list[list[VectorFieldImpulse]] = []

        for i, kf in enumerate(kinematic_frames):
            frame_impulses: list[VectorFieldImpulse] = []

            # --- Root velocity impulse ---
            clamped_root_v = soft_tanh_clamp(kf.root_velocity, v_max)
            root_speed = math.hypot(*clamped_root_v)

            if root_speed > 1e-6:
                # Line segment from previous root position to current
                if i > 0:
                    prev_pos = kinematic_frames[i - 1].root_position
                else:
                    prev_pos = kf.root_position

                frame_impulses.append(VectorFieldImpulse(
                    start=prev_pos,
                    end=kf.root_position,
                    velocity_vector=clamped_root_v,
                    radius=cfg.root_impulse_radius,
                    density_amount=cfg.root_density_gain * min(root_speed * 2.0, 3.0),
                    label=f"root_{kf.source_state}",
                ))

            # --- Effector trajectory impulse ---
            for eff_name, eff_pos in kf.effector_positions.items():
                if eff_name in kf.effector_velocities:
                    raw_v = kf.effector_velocities[eff_name]
                    clamped_v = soft_tanh_clamp(raw_v, v_max)
                    eff_speed = math.hypot(*clamped_v)

                    if eff_speed > 1e-6 and i > 0:
                        prev_kf = kinematic_frames[i - 1]
                        if eff_name in prev_kf.effector_positions:
                            prev_eff_pos = prev_kf.effector_positions[eff_name]
                        else:
                            prev_eff_pos = eff_pos

                        frame_impulses.append(VectorFieldImpulse(
                            start=prev_eff_pos,
                            end=eff_pos,
                            velocity_vector=clamped_v,
                            radius=cfg.effector_impulse_radius,
                            density_amount=cfg.effector_density_gain * min(eff_speed * 1.5, 4.0),
                            label=f"{eff_name}_{kf.source_state}",
                        ))

            impulse_sequence.append(frame_impulses)

        return impulse_sequence


# ═══════════════════════════════════════════════════════════════════════════
# FluidImpulse Bridge — convert VectorFieldImpulse to legacy FluidImpulse
# ═══════════════════════════════════════════════════════════════════════════


def vector_field_impulse_to_fluid_impulse(
    vfi: VectorFieldImpulse,
    grid_size: int,
) -> "FluidImpulse":
    """Convert a VectorFieldImpulse to a legacy FluidImpulse.

    The legacy ``FluidImpulse`` uses a single centre point and scalar
    velocity components.  For backward compatibility, we use the segment
    midpoint as the centre.

    This is a convenience bridge for code paths that still use the old
    ``driver_impulses`` interface.  The preferred path is to use
    ``LineSegmentSplatter`` directly for continuous injection.
    """
    from .fluid_vfx import FluidImpulse

    mid_x = (vfi.start[0] + vfi.end[0]) * 0.5
    mid_y = (vfi.start[1] + vfi.end[1]) * 0.5
    return FluidImpulse(
        center_x=mid_x,
        center_y=mid_y,
        velocity_x=vfi.velocity_vector[0] * grid_size,
        velocity_y=vfi.velocity_vector[1] * grid_size,
        density=vfi.density_amount,
        radius=vfi.radius,
        label=vfi.label,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════

__all__ = [
    "KinematicFrame",
    "VectorFieldImpulse",
    "UMRExtractionConfig",
    "UMRKinematicImpulseAdapter",
    "LineSegmentSplatter",
    "soft_tanh_clamp",
    "compute_cfl_safe_velocity_limit",
    "vector_field_impulse_to_fluid_impulse",
]
