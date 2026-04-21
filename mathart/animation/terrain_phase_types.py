"""P1-PHASE-33B — Terrain-Adaptive Phase Modulation: Strong-Typed Data Contracts.

SESSION-110: Research-grounded data contracts for terrain-adaptive phase and
stride modulation.  All intermediate and output parameters are frozen dataclasses
with explicit numeric fields — no ad-hoc dicts or tuples cross module boundaries.

Research foundations:
  1. **Holden et al., PFNN (SIGGRAPH 2017)**: Terrain heightmap sampling along
     future trajectory for phase progression modulation.
  2. **Biomechanics of Incline Walking**: Continuous slope→stride/frequency
     mapping grounded in metabolic cost literature.
  3. **Clavet, Motion Matching (GDC 2016)**: Batch trajectory forecasting for
     predictive terrain adaptation.

Architecture:
  - All config parameters are exposed as ``TerrainGaitConfig`` fields so they
    can be resolved through ``RuntimeDistillationBus.resolve_scalar()`` without
    any magic numbers in the hot path.
  - ``TrajectoryTerrainSample`` is the immutable tensor-level output of the
    vectorized trajectory forecaster.
  - ``TerrainPhaseModulationResult`` carries the final stride/frequency scale
    factors plus smoothed phase velocity for downstream consumption.
  - ``TransientPhaseMetadata`` is the UMR-compatible metadata payload that flows
    into ``UnifiedMotionFrame.metadata`` for end-to-end traceability.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

import numpy as np


# ══════════════════════════════════════════════════════════════════════════════
# 1. TerrainGaitConfig — strongly typed tuning surface (no magic numbers)
# ══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class SurfaceTypeEntry:
    """A single surface-type descriptor with viscosity and friction coefficients.

    Viscosity ∈ [0, 1]: 0 = frictionless ice, 1 = deep mud.
    Friction  ∈ [0, 1]: kinetic friction coefficient proxy.
    """

    name: str = "default"
    viscosity: float = 0.0
    friction: float = 0.6

    def __post_init__(self) -> None:
        object.__setattr__(self, "viscosity", float(max(0.0, min(1.0, self.viscosity))))
        object.__setattr__(self, "friction", float(max(0.0, min(1.0, self.friction))))


# Pre-defined surface vocabulary (extensible by callers)
SURFACE_DEFAULT = SurfaceTypeEntry("default", viscosity=0.0, friction=0.6)
SURFACE_ICE = SurfaceTypeEntry("ice", viscosity=0.0, friction=0.1)
SURFACE_GRASS = SurfaceTypeEntry("grass", viscosity=0.15, friction=0.55)
SURFACE_MUD = SurfaceTypeEntry("mud", viscosity=0.6, friction=0.35)
SURFACE_SAND = SurfaceTypeEntry("sand", viscosity=0.4, friction=0.45)
SURFACE_STONE = SurfaceTypeEntry("stone", viscosity=0.05, friction=0.7)

KNOWN_SURFACE_TYPES: dict[str, SurfaceTypeEntry] = {
    s.name: s for s in [
        SURFACE_DEFAULT, SURFACE_ICE, SURFACE_GRASS,
        SURFACE_MUD, SURFACE_SAND, SURFACE_STONE,
    ]
}


@dataclass(frozen=True)
class TerrainGaitConfig:
    """Strongly typed configuration for terrain-adaptive gait modulation.

    Every tunable parameter has a physically motivated default and explicit
    bounds.  The config is frozen so it can be resolved once outside the hot
    path and passed by reference.

    Parameters
    ----------
    trajectory_sample_count : int
        Number of future trajectory points to sample (N).
    trajectory_sample_dt : float
        Time interval between trajectory samples in seconds.
    slope_stride_alpha : float
        Sensitivity of stride length to slope angle (α_s in research notes).
        stride_scale = 1.0 - slope_stride_alpha * sin(θ).
    slope_freq_alpha : float
        Sensitivity of step frequency to slope angle (α_f).
        freq_scale = 1.0 + slope_freq_alpha * sin(θ).
    viscosity_stride_beta : float
        Sensitivity of stride length to surface viscosity (β_s).
    viscosity_freq_beta : float
        Sensitivity of step frequency to surface viscosity (β_f).
    stride_scale_min : float
        Lower clamp for stride scale factor.
    stride_scale_max : float
        Upper clamp for stride scale factor.
    freq_scale_min : float
        Lower clamp for frequency scale factor.
    freq_scale_max : float
        Upper clamp for frequency scale factor.
    ema_smoothing_tau : float
        Time constant (seconds) for EMA low-pass filter on phase velocity.
        Prevents C1 discontinuity (anti-phase-pop).
    gradient_eps : float
        Epsilon for central-difference gradient computation.
    slope_weight_decay : float
        Exponential decay factor for weighting future trajectory samples.
        Closer samples have higher weight.  weight_i = exp(-decay * i).
    default_surface : SurfaceTypeEntry
        Default surface type when no surface map is available.
    """

    trajectory_sample_count: int = 8
    trajectory_sample_dt: float = 0.05
    slope_stride_alpha: float = 0.35
    slope_freq_alpha: float = 0.25
    viscosity_stride_beta: float = 0.30
    viscosity_freq_beta: float = 0.15
    stride_scale_min: float = 0.4
    stride_scale_max: float = 1.6
    freq_scale_min: float = 0.5
    freq_scale_max: float = 2.0
    ema_smoothing_tau: float = 0.12
    gradient_eps: float = 1e-4
    slope_weight_decay: float = 0.3
    default_surface: SurfaceTypeEntry = field(default_factory=lambda: SURFACE_DEFAULT)

    def __post_init__(self) -> None:
        object.__setattr__(self, "trajectory_sample_count",
                           max(1, int(self.trajectory_sample_count)))
        object.__setattr__(self, "trajectory_sample_dt",
                           max(1e-4, float(self.trajectory_sample_dt)))
        object.__setattr__(self, "slope_stride_alpha",
                           float(max(0.0, min(1.0, self.slope_stride_alpha))))
        object.__setattr__(self, "slope_freq_alpha",
                           float(max(0.0, min(1.0, self.slope_freq_alpha))))
        object.__setattr__(self, "viscosity_stride_beta",
                           float(max(0.0, min(1.0, self.viscosity_stride_beta))))
        object.__setattr__(self, "viscosity_freq_beta",
                           float(max(0.0, min(1.0, self.viscosity_freq_beta))))
        object.__setattr__(self, "ema_smoothing_tau",
                           float(max(1e-4, self.ema_smoothing_tau)))
        object.__setattr__(self, "gradient_eps",
                           float(max(1e-8, self.gradient_eps)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "trajectory_sample_count": self.trajectory_sample_count,
            "trajectory_sample_dt": self.trajectory_sample_dt,
            "slope_stride_alpha": self.slope_stride_alpha,
            "slope_freq_alpha": self.slope_freq_alpha,
            "viscosity_stride_beta": self.viscosity_stride_beta,
            "viscosity_freq_beta": self.viscosity_freq_beta,
            "stride_scale_min": self.stride_scale_min,
            "stride_scale_max": self.stride_scale_max,
            "freq_scale_min": self.freq_scale_min,
            "freq_scale_max": self.freq_scale_max,
            "ema_smoothing_tau": self.ema_smoothing_tau,
            "gradient_eps": self.gradient_eps,
            "slope_weight_decay": self.slope_weight_decay,
            "default_surface_name": self.default_surface.name,
        }


# ══════════════════════════════════════════════════════════════════════════════
# 2. TrajectoryTerrainSample — vectorized terrain query output
# ══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class TrajectoryTerrainSample:
    """Immutable output of the vectorized trajectory terrain forecaster.

    All arrays have shape (N,) where N = trajectory_sample_count.
    This is the tensor-level contract between the forecaster and the modulator.
    """

    # Future trajectory world positions: (N, 2) — [x, y] per sample
    positions: np.ndarray  # shape (N, 2), dtype float64

    # SDF values at each trajectory point: (N,)
    sdf_values: np.ndarray  # shape (N,), dtype float64

    # Surface gradient (∇SDF) at each point: (N, 2) — [gx, gy]
    gradients: np.ndarray  # shape (N, 2), dtype float64

    # Slope angle in radians at each point: (N,)
    # Positive = uphill (surface rises in movement direction)
    # Negative = downhill
    slope_angles: np.ndarray  # shape (N,), dtype float64

    # Surface viscosity at each point: (N,)
    surface_viscosities: np.ndarray  # shape (N,), dtype float64

    # Exponential-decay-weighted average slope (scalar)
    weighted_slope: float = 0.0

    # Exponential-decay-weighted average viscosity (scalar)
    weighted_viscosity: float = 0.0

    # Number of valid samples
    sample_count: int = 0

    # Movement direction unit vector used for slope sign computation
    movement_direction: tuple[float, float] = (1.0, 0.0)

    def __post_init__(self) -> None:
        # Enforce immutable numpy arrays
        for attr in ("positions", "sdf_values", "gradients",
                     "slope_angles", "surface_viscosities"):
            arr = getattr(self, attr)
            if not isinstance(arr, np.ndarray):
                object.__setattr__(self, attr, np.asarray(arr, dtype=np.float64))
            arr = getattr(self, attr)
            arr.flags.writeable = False

    def to_metadata_dict(self) -> dict[str, Any]:
        """Project to a serializable metadata dict for UMR frame embedding."""
        return {
            "terrain_forecast_sample_count": int(self.sample_count),
            "terrain_forecast_weighted_slope": float(self.weighted_slope),
            "terrain_forecast_weighted_viscosity": float(self.weighted_viscosity),
            "terrain_forecast_slope_mean": float(np.mean(self.slope_angles))
            if self.sample_count > 0 else 0.0,
            "terrain_forecast_slope_std": float(np.std(self.slope_angles))
            if self.sample_count > 0 else 0.0,
            "terrain_forecast_movement_dir_x": float(self.movement_direction[0]),
            "terrain_forecast_movement_dir_y": float(self.movement_direction[1]),
        }


# ══════════════════════════════════════════════════════════════════════════════
# 3. TerrainPhaseModulationResult — modulator output
# ══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class TerrainPhaseModulationResult:
    """Output of the terrain phase modulator: scale factors for stride and frequency.

    These are multiplicative factors applied to the base StrideWheel parameters:
      - new_circumference = base_circumference * stride_scale
      - new_phase_delta   = base_phase_delta * freq_scale

    The ``smoothed_phase_velocity`` is the EMA-filtered phase velocity that
    ensures C1 continuity across terrain transitions.
    """

    # Multiplicative stride length scale (affects StrideWheel.circumference)
    stride_scale: float = 1.0

    # Multiplicative phase velocity scale (affects Δφ per frame)
    freq_scale: float = 1.0

    # EMA-smoothed target phase velocity (rad/frame or normalized/frame)
    smoothed_phase_velocity: float = 0.0

    # Raw (unsmoothed) target phase velocity for diagnostics
    raw_phase_velocity: float = 0.0

    # The terrain sample that produced this result (for traceability)
    terrain_sample: Optional[TrajectoryTerrainSample] = None

    # Whether modulation is active (terrain data was available)
    active: bool = False

    def to_metadata_dict(self) -> dict[str, Any]:
        """Project to UMR-compatible metadata dict."""
        d: dict[str, Any] = {
            "terrain_modulation_active": self.active,
            "terrain_stride_scale": float(self.stride_scale),
            "terrain_freq_scale": float(self.freq_scale),
            "terrain_smoothed_phase_velocity": float(self.smoothed_phase_velocity),
            "terrain_raw_phase_velocity": float(self.raw_phase_velocity),
        }
        if self.terrain_sample is not None:
            d.update(self.terrain_sample.to_metadata_dict())
        return d


# ══════════════════════════════════════════════════════════════════════════════
# 4. Config resolver for RuntimeDistillationBus integration
# ══════════════════════════════════════════════════════════════════════════════


def resolve_terrain_gait_config(
    runtime_distillation_bus: Any | None = None,
    *,
    base_config: TerrainGaitConfig | None = None,
) -> TerrainGaitConfig:
    """Resolve TerrainGaitConfig from RuntimeDistillationBus or defaults.

    This follows the same pattern as ``resolve_unified_gait_runtime_config``
    in ``unified_gait_blender.py`` — resolve once outside the hot path.
    """
    cfg = base_config or TerrainGaitConfig()
    resolver = getattr(runtime_distillation_bus, "resolve_scalar", None)
    if not callable(resolver):
        return cfg

    return TerrainGaitConfig(
        trajectory_sample_count=int(resolver([
            "terrain_gait.trajectory_sample_count",
            "trajectory_sample_count",
            "terrain_forecast_samples",
        ], cfg.trajectory_sample_count)),
        trajectory_sample_dt=float(resolver([
            "terrain_gait.trajectory_sample_dt",
            "trajectory_sample_dt",
            "terrain_forecast_dt",
        ], cfg.trajectory_sample_dt)),
        slope_stride_alpha=float(resolver([
            "terrain_gait.slope_stride_alpha",
            "slope_stride_alpha",
            "terrain_stride_sensitivity",
        ], cfg.slope_stride_alpha)),
        slope_freq_alpha=float(resolver([
            "terrain_gait.slope_freq_alpha",
            "slope_freq_alpha",
            "terrain_freq_sensitivity",
        ], cfg.slope_freq_alpha)),
        viscosity_stride_beta=float(resolver([
            "terrain_gait.viscosity_stride_beta",
            "viscosity_stride_beta",
        ], cfg.viscosity_stride_beta)),
        viscosity_freq_beta=float(resolver([
            "terrain_gait.viscosity_freq_beta",
            "viscosity_freq_beta",
        ], cfg.viscosity_freq_beta)),
        stride_scale_min=float(resolver([
            "terrain_gait.stride_scale_min",
            "terrain_stride_scale_min",
        ], cfg.stride_scale_min)),
        stride_scale_max=float(resolver([
            "terrain_gait.stride_scale_max",
            "terrain_stride_scale_max",
        ], cfg.stride_scale_max)),
        freq_scale_min=float(resolver([
            "terrain_gait.freq_scale_min",
            "terrain_freq_scale_min",
        ], cfg.freq_scale_min)),
        freq_scale_max=float(resolver([
            "terrain_gait.freq_scale_max",
            "terrain_freq_scale_max",
        ], cfg.freq_scale_max)),
        ema_smoothing_tau=float(resolver([
            "terrain_gait.ema_smoothing_tau",
            "terrain_phase_smoothing_tau",
            "phase_velocity_ema_tau",
        ], cfg.ema_smoothing_tau)),
        gradient_eps=cfg.gradient_eps,
        slope_weight_decay=float(resolver([
            "terrain_gait.slope_weight_decay",
            "terrain_slope_weight_decay",
        ], cfg.slope_weight_decay)),
        default_surface=cfg.default_surface,
    )
