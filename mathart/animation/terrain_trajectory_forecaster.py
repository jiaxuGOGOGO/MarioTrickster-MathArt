"""P1-PHASE-33B — Vectorized Trajectory Terrain Forecaster.

SESSION-110: Tensor-level terrain extraction along the character's predicted
future trajectory.  This module is the first of three components that implement
terrain-adaptive phase modulation.

Research foundations:
  1. **Holden et al., PFNN (SIGGRAPH 2017)**: Heightmap/SDF sampling at
     multiple points along the character's future path for phase modulation.
  2. **Clavet, Motion Matching (GDC 2016)**: Trajectory prediction as a
     spring-damper on desired velocity; batch terrain queries for predictive
     adaptation.
  3. **Pontón et al. (SIGGRAPH 2025)**: Environment features integrated into
     motion cost functions via ellipse proxies.

Architecture constraints:
  - 🔴 **Anti-Scalar-Loop**: All N trajectory points are queried in a single
    vectorized NumPy operation.  Zero Python ``for`` loops in the hot path.
  - 🔴 **Anti-Magic-Number**: All tuning parameters come from the frozen
    ``TerrainGaitConfig`` dataclass.
  - 🔴 **Anti-Data-Silo**: Output is the frozen ``TrajectoryTerrainSample``
    dataclass with immutable NumPy arrays.
  - Does NOT modify ``terrain_sensor.py``'s existing 3-stage pipeline.
  - Mounts as an independent component consumed by ``TerrainPhaseModulator``.

Usage::

    from mathart.animation.terrain_trajectory_forecaster import (
        TrajectoryTerrainForecaster,
    )
    from mathart.animation.terrain_sensor import create_slope_terrain
    from mathart.animation.terrain_phase_types import TerrainGaitConfig

    terrain = create_slope_terrain(start_x=0.0, end_x=2.0, end_y=0.5)
    config = TerrainGaitConfig(trajectory_sample_count=8)
    forecaster = TrajectoryTerrainForecaster(terrain, config=config)

    sample = forecaster.forecast(
        root_x=0.5, root_y=0.1,
        velocity_x=1.0, velocity_y=0.0,
    )
    print(sample.weighted_slope, sample.slope_angles)
"""
from __future__ import annotations

import math
from typing import Callable, Optional

import numpy as np

from .terrain_phase_types import (
    TerrainGaitConfig,
    TrajectoryTerrainSample,
    SurfaceTypeEntry,
    SURFACE_DEFAULT,
)
from .terrain_sensor import TerrainSDF


# Type alias for optional surface-type lookup function
SurfaceTypeLookup = Callable[[np.ndarray, np.ndarray], np.ndarray]
"""(x_array, y_array) → viscosity_array.  Returns viscosity ∈ [0,1] per point."""


class TrajectoryTerrainForecaster:
    """Vectorized future-trajectory terrain sampler.

    Given the character's current root position and velocity, predicts N future
    trajectory points and batch-queries the terrain SDF to extract:
      - SDF distance at each point
      - Surface gradient (∇SDF) via central differences
      - Slope angle relative to movement direction
      - Surface viscosity (from optional lookup or config default)

    All operations are fully vectorized — no Python ``for`` loops touch the
    per-sample hot path.

    Parameters
    ----------
    terrain : TerrainSDF
        The SDF terrain to query.
    config : TerrainGaitConfig
        Strongly typed configuration (sample count, dt, eps, etc.).
    surface_lookup : SurfaceTypeLookup or None
        Optional vectorized surface-type query.  If None, uses
        ``config.default_surface.viscosity`` for all points.
    """

    def __init__(
        self,
        terrain: TerrainSDF,
        *,
        config: TerrainGaitConfig | None = None,
        surface_lookup: SurfaceTypeLookup | None = None,
    ):
        self._terrain = terrain
        self._config = config or TerrainGaitConfig()
        self._surface_lookup = surface_lookup

        # Pre-compute time offsets for trajectory samples: (N,)
        N = self._config.trajectory_sample_count
        dt = self._config.trajectory_sample_dt
        self._t_offsets = np.arange(1, N + 1, dtype=np.float64) * dt

        # Pre-compute exponential decay weights: (N,)
        decay = self._config.slope_weight_decay
        self._weights = np.exp(-decay * np.arange(N, dtype=np.float64))
        weight_sum = self._weights.sum()
        if weight_sum > 1e-12:
            self._weights_normalized = self._weights / weight_sum
        else:
            self._weights_normalized = np.ones(N, dtype=np.float64) / max(N, 1)

    @property
    def config(self) -> TerrainGaitConfig:
        return self._config

    @property
    def terrain(self) -> TerrainSDF:
        return self._terrain

    def forecast(
        self,
        root_x: float,
        root_y: float,
        velocity_x: float,
        velocity_y: float,
        *,
        surface_lookup_override: SurfaceTypeLookup | None = None,
    ) -> TrajectoryTerrainSample:
        """Predict future trajectory and batch-query terrain features.

        Parameters
        ----------
        root_x, root_y : float
            Current character root position.
        velocity_x, velocity_y : float
            Current root velocity (world space).
        surface_lookup_override : SurfaceTypeLookup or None
            Per-call override for surface viscosity lookup.

        Returns
        -------
        TrajectoryTerrainSample
            Frozen dataclass with all terrain features along the trajectory.
        """
        cfg = self._config
        N = cfg.trajectory_sample_count
        eps = cfg.gradient_eps

        # ── 1. Predict future trajectory positions ──────────────────────
        # Vectorized: positions[i] = root + velocity * t_offsets[i]
        # Shape: (N, 2)
        root = np.array([float(root_x), float(root_y)], dtype=np.float64)
        vel = np.array([float(velocity_x), float(velocity_y)], dtype=np.float64)

        # (N, 2) = (1, 2) + (1, 2) * (N, 1)
        positions = root[np.newaxis, :] + vel[np.newaxis, :] * self._t_offsets[:, np.newaxis]

        px = positions[:, 0]  # (N,)
        py = positions[:, 1]  # (N,)

        # ── 2. Batch SDF query ──────────────────────────────────────────
        sdf_values = self._terrain.query_batch(px, py)  # (N,)

        # ── 3. Batch gradient via central differences (4 SDF queries) ───
        # ∂SDF/∂x ≈ (SDF(x+ε, y) - SDF(x-ε, y)) / (2ε)
        # ∂SDF/∂y ≈ (SDF(x, y+ε) - SDF(x, y-ε)) / (2ε)
        sdf_xp = self._terrain.query_batch(px + eps, py)
        sdf_xn = self._terrain.query_batch(px - eps, py)
        sdf_yp = self._terrain.query_batch(px, py + eps)
        sdf_yn = self._terrain.query_batch(px, py - eps)

        grad_x = (sdf_xp - sdf_xn) / (2.0 * eps)  # (N,)
        grad_y = (sdf_yp - sdf_yn) / (2.0 * eps)  # (N,)

        gradients = np.stack([grad_x, grad_y], axis=-1)  # (N, 2)

        # ── 4. Compute slope angles relative to movement direction ──────
        # The SDF gradient points away from the surface (outward normal).
        # For a 2D side-scroller, the "surface normal" is (grad_x, grad_y).
        # The slope angle relative to movement is:
        #   θ = atan2(surface_rise_in_movement_dir, 1)
        #
        # More precisely: project the surface tangent onto the movement
        # direction to get the signed slope.
        speed = math.sqrt(velocity_x * velocity_x + velocity_y * velocity_y)
        if speed > 1e-8:
            move_dir = np.array([velocity_x / speed, velocity_y / speed],
                                dtype=np.float64)
        else:
            move_dir = np.array([1.0, 0.0], dtype=np.float64)

        # Surface normal = normalized gradient
        grad_mag = np.sqrt(grad_x * grad_x + grad_y * grad_y)  # (N,)
        safe_mag = np.maximum(grad_mag, 1e-12)
        norm_x = grad_x / safe_mag  # (N,)
        norm_y = grad_y / safe_mag  # (N,)

        # Surface tangent (90° rotation of normal): (-norm_y, norm_x)
        # Slope = angle between tangent and horizontal
        # For movement direction d, the slope "felt" by the character is:
        #   sin(θ) = dot(normal, movement_direction)
        # This gives positive for uphill, negative for downhill.
        sin_slope = norm_x * move_dir[0] + norm_y * move_dir[1]  # (N,)

        # Clamp to valid range and compute angle
        sin_slope = np.clip(sin_slope, -1.0, 1.0)
        slope_angles = np.arcsin(sin_slope)  # (N,) radians

        # ── 5. Surface viscosity ────────────────────────────────────────
        lookup = surface_lookup_override or self._surface_lookup
        if lookup is not None:
            surface_viscosities = np.asarray(lookup(px, py), dtype=np.float64)
        else:
            surface_viscosities = np.full(N, cfg.default_surface.viscosity,
                                          dtype=np.float64)

        # ── 6. Weighted aggregation ─────────────────────────────────────
        weighted_slope = float(np.dot(self._weights_normalized, slope_angles))
        weighted_viscosity = float(np.dot(self._weights_normalized, surface_viscosities))

        return TrajectoryTerrainSample(
            positions=positions,
            sdf_values=sdf_values,
            gradients=gradients,
            slope_angles=slope_angles,
            surface_viscosities=surface_viscosities,
            weighted_slope=weighted_slope,
            weighted_viscosity=weighted_viscosity,
            sample_count=N,
            movement_direction=(float(move_dir[0]), float(move_dir[1])),
        )

    def forecast_batch_roots(
        self,
        root_positions: np.ndarray,
        velocities: np.ndarray,
    ) -> list[TrajectoryTerrainSample]:
        """Forecast terrain for multiple characters/time-steps.

        Parameters
        ----------
        root_positions : np.ndarray
            Shape (M, 2) — M root positions.
        velocities : np.ndarray
            Shape (M, 2) — M velocity vectors.

        Returns
        -------
        list[TrajectoryTerrainSample]
            One sample per root position.

        Note: This is a convenience for batch offline processing.  For
        single-character real-time use, call ``forecast()`` directly.
        """
        M = root_positions.shape[0]
        results: list[TrajectoryTerrainSample] = []
        # Each character's forecast is independent; we vectorize within
        # each forecast (N points), not across characters (M).
        for i in range(M):
            results.append(self.forecast(
                root_x=float(root_positions[i, 0]),
                root_y=float(root_positions[i, 1]),
                velocity_x=float(velocities[i, 0]),
                velocity_y=float(velocities[i, 1]),
            ))
        return results
