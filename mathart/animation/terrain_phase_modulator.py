"""P1-PHASE-33B — Terrain-Adaptive Phase Modulator.

SESSION-110: The second of three components for terrain-adaptive phase and
stride modulation.  This modulator intercepts the ``StrideWheel`` and gait
update hot path, mapping terrain features (slope, viscosity) to continuous
scale factors for circumference (stride length) and phase velocity (step
frequency).

Research foundations:
  1. **Holden et al., PFNN (SIGGRAPH 2017)**: Phase velocity modulated by
     terrain geometry via nonlinear mapping.
  2. **Biomechanics of Incline Walking**: Uphill → shorter stride + higher
     frequency; downhill → longer stride + lower frequency.
  3. **Clavet, Motion Matching (GDC 2016)**: Predictive trajectory-based
     terrain adaptation.

Architecture constraints:
  - 🔴 **Anti-Phase-Pop**: Only modifies phase VELOCITY (dφ/dt), never the
    absolute phase value.  Uses EMA low-pass filter for C1 continuity.
  - 🔴 **Anti-Magic-Number**: All parameters from ``TerrainGaitConfig``.
    Mapping functions are continuous (cos/sin/smoothstep), no ``if slope > X``.
  - 🔴 **Anti-Scalar-Loop**: Terrain sampling is delegated to the vectorized
    ``TrajectoryTerrainForecaster``; this module only processes aggregated
    scalars.
  - Mounts as an independent component; does NOT modify ``unified_gait_blender``
    internals.  Instead, it produces scale factors that the integration layer
    applies to the existing ``StrideWheel`` and ``sample_continuous_gait`` API.

Usage::

    from mathart.animation.terrain_phase_modulator import TerrainPhaseModulator
    from mathart.animation.terrain_trajectory_forecaster import TrajectoryTerrainForecaster
    from mathart.animation.terrain_sensor import create_slope_terrain
    from mathart.animation.terrain_phase_types import TerrainGaitConfig

    terrain = create_slope_terrain(start_x=0.0, end_x=2.0, end_y=0.5)
    config = TerrainGaitConfig()
    forecaster = TrajectoryTerrainForecaster(terrain, config=config)
    modulator = TerrainPhaseModulator(config=config)

    # Per-frame update
    sample = forecaster.forecast(root_x=0.5, root_y=0.1, velocity_x=1.0, velocity_y=0.0)
    result = modulator.update(sample, dt=1/60)
    # result.stride_scale, result.freq_scale → apply to StrideWheel
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np

from .terrain_phase_types import (
    TerrainGaitConfig,
    TerrainPhaseModulationResult,
    TrajectoryTerrainSample,
)


# ══════════════════════════════════════════════════════════════════════════════
# Continuous mathematical mapping functions (no if/else step functions)
# ══════════════════════════════════════════════════════════════════════════════


def _smoothstep(edge0: float, edge1: float, x: float) -> float:
    """Hermite smoothstep interpolation — C1 continuous."""
    t = max(0.0, min(1.0, (x - edge0) / (edge1 - edge0 + 1e-12)))
    return t * t * (3.0 - 2.0 * t)


def _slope_to_stride_scale(
    slope_rad: float,
    alpha: float,
    scale_min: float,
    scale_max: float,
) -> float:
    """Continuous biomechanics-grounded mapping: slope → stride length scale.

    Based on empirical findings:
      - Uphill (slope > 0): stride shortens → scale < 1.0
      - Downhill (slope < 0): stride lengthens → scale > 1.0
      - Flat (slope ≈ 0): no change → scale = 1.0

    Uses sin(slope) for smooth, bounded, odd-symmetric response.
    The mapping is: scale = 1.0 - α * sin(θ)
    """
    raw = 1.0 - alpha * math.sin(slope_rad)
    return max(scale_min, min(scale_max, raw))


def _slope_to_freq_scale(
    slope_rad: float,
    alpha: float,
    scale_min: float,
    scale_max: float,
) -> float:
    """Continuous biomechanics-grounded mapping: slope → step frequency scale.

    Based on empirical findings:
      - Uphill (slope > 0): frequency increases → scale > 1.0
      - Downhill (slope < 0): frequency decreases → scale < 1.0
      - Flat (slope ≈ 0): no change → scale = 1.0

    Uses sin(slope) for smooth, bounded, odd-symmetric response.
    The mapping is: scale = 1.0 + α * sin(θ)
    """
    raw = 1.0 + alpha * math.sin(slope_rad)
    return max(scale_min, min(scale_max, raw))


def _viscosity_stride_scale(
    viscosity: float,
    beta: float,
    scale_min: float,
) -> float:
    """Surface viscosity → stride length attenuation.

    Higher viscosity (mud, sand) → shorter stride.
    Uses linear attenuation: scale = 1.0 - β * viscosity.
    """
    raw = 1.0 - beta * max(0.0, min(1.0, viscosity))
    return max(scale_min, raw)


def _viscosity_freq_scale(
    viscosity: float,
    beta: float,
    scale_min: float,
) -> float:
    """Surface viscosity → step frequency attenuation.

    Higher viscosity → slightly slower cadence.
    Uses linear attenuation: scale = 1.0 - β * viscosity.
    """
    raw = 1.0 - beta * max(0.0, min(1.0, viscosity))
    return max(scale_min, raw)


# ══════════════════════════════════════════════════════════════════════════════
# TerrainPhaseModulator — stateful EMA-smoothed modulator
# ══════════════════════════════════════════════════════════════════════════════


class TerrainPhaseModulator:
    """Terrain-adaptive phase and stride modulator with C1-continuous smoothing.

    This modulator maintains internal EMA state to ensure that phase velocity
    changes are smooth (C1 continuous) even when terrain changes abruptly
    (e.g., flat → steep slope transition).

    The modulator does NOT directly modify any phase or stride wheel state.
    Instead, it produces ``TerrainPhaseModulationResult`` containing scale
    factors that the integration layer applies externally.

    Parameters
    ----------
    config : TerrainGaitConfig
        Strongly typed configuration.  Resolved once at construction.
    """

    def __init__(self, config: TerrainGaitConfig | None = None):
        self._config = config or TerrainGaitConfig()

        # EMA state for C1-continuous phase velocity smoothing
        self._smoothed_stride_scale: float = 1.0
        self._smoothed_freq_scale: float = 1.0
        self._smoothed_phase_velocity: float = 0.0
        self._initialized: bool = False

    @property
    def config(self) -> TerrainGaitConfig:
        return self._config

    @property
    def smoothed_stride_scale(self) -> float:
        return self._smoothed_stride_scale

    @property
    def smoothed_freq_scale(self) -> float:
        return self._smoothed_freq_scale

    def reset(self) -> None:
        """Reset EMA state to defaults."""
        self._smoothed_stride_scale = 1.0
        self._smoothed_freq_scale = 1.0
        self._smoothed_phase_velocity = 0.0
        self._initialized = False

    def update(
        self,
        terrain_sample: TrajectoryTerrainSample,
        dt: float,
        *,
        base_phase_velocity: float = 0.0,
    ) -> TerrainPhaseModulationResult:
        """Compute terrain-adaptive modulation for the current frame.

        Parameters
        ----------
        terrain_sample : TrajectoryTerrainSample
            Output from ``TrajectoryTerrainForecaster.forecast()``.
        dt : float
            Frame delta time in seconds.
        base_phase_velocity : float
            The unmodulated phase velocity (Δφ/frame) from the gait engine.
            Used to compute the smoothed absolute phase velocity.

        Returns
        -------
        TerrainPhaseModulationResult
            Scale factors and smoothed phase velocity.
        """
        cfg = self._config
        dt = max(float(dt), 1e-6)

        # ── 1. Compute raw scale factors from terrain features ──────────
        slope = terrain_sample.weighted_slope
        viscosity = terrain_sample.weighted_viscosity

        # Slope-based modulation (continuous sin mapping)
        raw_stride_slope = _slope_to_stride_scale(
            slope, cfg.slope_stride_alpha,
            cfg.stride_scale_min, cfg.stride_scale_max,
        )
        raw_freq_slope = _slope_to_freq_scale(
            slope, cfg.slope_freq_alpha,
            cfg.freq_scale_min, cfg.freq_scale_max,
        )

        # Viscosity-based modulation (linear attenuation)
        visc_stride = _viscosity_stride_scale(
            viscosity, cfg.viscosity_stride_beta, cfg.stride_scale_min,
        )
        visc_freq = _viscosity_freq_scale(
            viscosity, cfg.viscosity_freq_beta, cfg.freq_scale_min,
        )

        # Combined scale = slope_scale * viscosity_scale
        raw_stride_scale = max(cfg.stride_scale_min,
                               min(cfg.stride_scale_max, raw_stride_slope * visc_stride))
        raw_freq_scale = max(cfg.freq_scale_min,
                             min(cfg.freq_scale_max, raw_freq_slope * visc_freq))

        # ── 2. EMA low-pass filter for C1 continuity ───────────────────
        # α = 1 - exp(-dt / τ)  — standard EMA coefficient
        tau = cfg.ema_smoothing_tau
        ema_alpha = 1.0 - math.exp(-dt / tau)

        if not self._initialized:
            # First frame: snap to target (no smoothing artifact)
            self._smoothed_stride_scale = raw_stride_scale
            self._smoothed_freq_scale = raw_freq_scale
            self._initialized = True
        else:
            # EMA update: smoothed += α * (target - smoothed)
            self._smoothed_stride_scale += ema_alpha * (
                raw_stride_scale - self._smoothed_stride_scale
            )
            self._smoothed_freq_scale += ema_alpha * (
                raw_freq_scale - self._smoothed_freq_scale
            )

        # ── 3. Compute smoothed phase velocity ─────────────────────────
        raw_pv = base_phase_velocity * raw_freq_scale
        smoothed_pv = base_phase_velocity * self._smoothed_freq_scale
        self._smoothed_phase_velocity = smoothed_pv

        return TerrainPhaseModulationResult(
            stride_scale=self._smoothed_stride_scale,
            freq_scale=self._smoothed_freq_scale,
            smoothed_phase_velocity=smoothed_pv,
            raw_phase_velocity=raw_pv,
            terrain_sample=terrain_sample,
            active=True,
        )

    def compute_static(
        self,
        slope_rad: float,
        viscosity: float = 0.0,
    ) -> TerrainPhaseModulationResult:
        """Stateless computation for testing / offline analysis.

        Does NOT update EMA state.
        """
        cfg = self._config

        raw_stride_slope = _slope_to_stride_scale(
            slope_rad, cfg.slope_stride_alpha,
            cfg.stride_scale_min, cfg.stride_scale_max,
        )
        raw_freq_slope = _slope_to_freq_scale(
            slope_rad, cfg.slope_freq_alpha,
            cfg.freq_scale_min, cfg.freq_scale_max,
        )
        visc_stride = _viscosity_stride_scale(
            viscosity, cfg.viscosity_stride_beta, cfg.stride_scale_min,
        )
        visc_freq = _viscosity_freq_scale(
            viscosity, cfg.viscosity_freq_beta, cfg.freq_scale_min,
        )

        stride_scale = max(cfg.stride_scale_min,
                           min(cfg.stride_scale_max, raw_stride_slope * visc_stride))
        freq_scale = max(cfg.freq_scale_min,
                         min(cfg.freq_scale_max, raw_freq_slope * visc_freq))

        return TerrainPhaseModulationResult(
            stride_scale=stride_scale,
            freq_scale=freq_scale,
            smoothed_phase_velocity=0.0,
            raw_phase_velocity=0.0,
            terrain_sample=None,
            active=True,
        )
