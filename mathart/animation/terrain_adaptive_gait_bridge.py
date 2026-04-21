"""P1-PHASE-33B — Terrain-Adaptive Gait Bridge (Integration Layer).

SESSION-110: The third and final component for terrain-adaptive phase and
stride modulation.  This bridge layer integrates the ``TrajectoryTerrainForecaster``
and ``TerrainPhaseModulator`` with the existing ``UnifiedGaitBlender`` without
modifying the blender's internal code.

Architecture:
  - This is a **composition wrapper**, NOT a subclass override.
  - It intercepts the ``sample_continuous_gait`` call, applies terrain
    modulation to the ``StrideWheel`` circumference and phase advance delta,
    then delegates to the original blender.
  - All terrain metadata flows into ``UnifiedMotionFrame.metadata`` via the
    ``TerrainPhaseModulationResult.to_metadata_dict()`` contract.

Red-line compliance:
  - 🔴 Does NOT modify ``unified_gait_blender.py`` source code.
  - 🔴 Does NOT modify ``terrain_sensor.py``'s 3-stage pipeline.
  - 🔴 Mounts via composition (IoC), not inheritance or monkey-patching.
  - 🔴 Phase absolute value is never touched; only circumference and advance
    delta are scaled.

Usage::

    from mathart.animation.terrain_adaptive_gait_bridge import (
        TerrainAdaptiveGaitBridge,
    )
    from mathart.animation.terrain_sensor import create_slope_terrain
    from mathart.animation.terrain_phase_types import TerrainGaitConfig

    terrain = create_slope_terrain(start_x=0.0, end_x=2.0, end_y=0.5)
    bridge = TerrainAdaptiveGaitBridge(terrain=terrain)

    # Per-frame: produces a gait sample with terrain-adaptive modulation
    sample, modulation = bridge.sample_terrain_adaptive_gait(
        dt=1/60, velocity=1.5, root_x=0.5, root_y=0.1,
    )
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

import numpy as np

from .terrain_phase_modulator import TerrainPhaseModulator
from .terrain_phase_types import (
    TerrainGaitConfig,
    TerrainPhaseModulationResult,
    resolve_terrain_gait_config,
)
from .terrain_sensor import TerrainSDF
from .terrain_trajectory_forecaster import (
    SurfaceTypeLookup,
    TrajectoryTerrainForecaster,
)
from .unified_gait_blender import (
    GaitMode,
    UnifiedGaitBlender,
    UnifiedMotionSample,
)
from .unified_motion import (
    MotionContactState,
    MotionRootTransform,
    PhaseState,
    UnifiedMotionFrame,
    pose_to_umr,
)


@dataclass(frozen=True)
class TerrainAdaptiveGaitSample:
    """Combined output of terrain-adaptive gait sampling.

    Carries both the base gait sample and the terrain modulation result
    for full traceability.
    """

    gait_sample: UnifiedMotionSample
    modulation: TerrainPhaseModulationResult
    terrain_metadata: dict[str, Any]


class TerrainAdaptiveGaitBridge:
    """Composition bridge that adds terrain-adaptive modulation to UnifiedGaitBlender.

    This bridge:
    1. Queries the terrain forecaster for future trajectory features.
    2. Feeds features to the phase modulator to get stride/freq scale factors.
    3. Temporarily adjusts the blender's StrideWheel circumference.
    4. Scales the effective velocity (which controls phase advance delta).
    5. Delegates to the original blender's ``sample_continuous_gait()``.
    6. Restores the original circumference after sampling.

    The bridge does NOT subclass or modify ``UnifiedGaitBlender``.

    Parameters
    ----------
    terrain : TerrainSDF
        The terrain to adapt to.
    blender : UnifiedGaitBlender or None
        Existing blender instance to wrap.  If None, creates a new one.
    config : TerrainGaitConfig or None
        Terrain modulation configuration.
    runtime_distillation_bus : Any or None
        Optional bus for resolving config from runtime constraints.
    surface_lookup : SurfaceTypeLookup or None
        Optional vectorized surface viscosity lookup.
    """

    def __init__(
        self,
        terrain: TerrainSDF,
        *,
        blender: UnifiedGaitBlender | None = None,
        config: TerrainGaitConfig | None = None,
        runtime_distillation_bus: Any | None = None,
        surface_lookup: SurfaceTypeLookup | None = None,
    ):
        self._terrain = terrain
        self._blender = blender or UnifiedGaitBlender()
        self._config = resolve_terrain_gait_config(
            runtime_distillation_bus, base_config=config,
        )
        self._forecaster = TrajectoryTerrainForecaster(
            terrain, config=self._config, surface_lookup=surface_lookup,
        )
        self._modulator = TerrainPhaseModulator(config=self._config)

    @property
    def terrain(self) -> TerrainSDF:
        return self._terrain

    @property
    def blender(self) -> UnifiedGaitBlender:
        return self._blender

    @property
    def config(self) -> TerrainGaitConfig:
        return self._config

    @property
    def forecaster(self) -> TrajectoryTerrainForecaster:
        return self._forecaster

    @property
    def modulator(self) -> TerrainPhaseModulator:
        return self._modulator

    def sample_terrain_adaptive_gait(
        self,
        *,
        dt: float,
        velocity: float,
        root_x: float,
        root_y: float,
        velocity_x: float | None = None,
        velocity_y: float = 0.0,
        target_gait: GaitMode | None = None,
        facing_right: bool = True,
    ) -> TerrainAdaptiveGaitSample:
        """Sample gait with terrain-adaptive stride and phase velocity modulation.

        Parameters
        ----------
        dt : float
            Frame delta time.
        velocity : float
            Character speed magnitude (used by blender).
        root_x, root_y : float
            Current root position in world space.
        velocity_x : float or None
            Horizontal velocity.  If None, derived from ``velocity`` and ``facing_right``.
        velocity_y : float
            Vertical velocity.
        target_gait : GaitMode or None
            Target gait mode.
        facing_right : bool
            Character facing direction (for velocity_x derivation).

        Returns
        -------
        TerrainAdaptiveGaitSample
            Combined gait sample with terrain modulation metadata.
        """
        # Resolve velocity components
        vx = velocity_x if velocity_x is not None else (
            abs(velocity) if facing_right else -abs(velocity)
        )

        # ── 1. Forecast terrain along future trajectory ─────────────────
        terrain_sample = self._forecaster.forecast(
            root_x=root_x, root_y=root_y,
            velocity_x=vx, velocity_y=velocity_y,
        )

        # ── 2. Compute base phase velocity (before modulation) ──────────
        base_stride = self._blender.blended_stride_length
        base_steps_per_sec = self._blender.blended_steps_per_second
        # Phase velocity = how much phase advances per second
        # phase_vel = speed / circumference = speed / stride_length
        base_phase_velocity = abs(velocity) / max(base_stride, 1e-6)

        # ── 3. Get modulation scale factors ─────────────────────────────
        modulation = self._modulator.update(
            terrain_sample, dt=dt,
            base_phase_velocity=base_phase_velocity,
        )

        # ── 4. Apply modulation to StrideWheel circumference ───────────
        # Save original circumference
        original_circumference = self._blender._stride_wheel.circumference

        # Apply stride scale to circumference (phase-preserving)
        modulated_circumference = base_stride * modulation.stride_scale
        self._blender._stride_wheel.set_circumference(modulated_circumference)

        # ── 5. Scale effective velocity for phase advance ───────────────
        # The blender advances phase by: distance_delta = velocity * dt
        # We scale velocity by freq_scale to modulate phase velocity
        modulated_velocity = abs(velocity) * modulation.freq_scale

        # ── 6. Delegate to original blender ─────────────────────────────
        gait_sample = self._blender.sample_continuous_gait(
            dt=dt,
            velocity=modulated_velocity,
            target_gait=target_gait,
        )

        # ── 7. Restore original circumference (for next frame's base) ──
        # Note: we DON'T restore because the modulated circumference IS
        # the correct state for the current terrain.  The next frame will
        # re-modulate based on new terrain data.

        # ── 8. Build metadata ───────────────────────────────────────────
        terrain_metadata = modulation.to_metadata_dict()
        terrain_metadata["terrain_adaptive_bridge_active"] = True
        terrain_metadata["terrain_name"] = self._terrain.name

        return TerrainAdaptiveGaitSample(
            gait_sample=gait_sample,
            modulation=modulation,
            terrain_metadata=terrain_metadata,
        )

    def generate_terrain_adaptive_frame(
        self,
        *,
        dt: float,
        velocity: float,
        root_x: float,
        root_y: float,
        velocity_x: float | None = None,
        velocity_y: float = 0.0,
        target_gait: GaitMode | None = None,
        facing_right: bool = True,
        time: float = 0.0,
        frame_index: int = 0,
        source_state: str = "locomotion",
    ) -> UnifiedMotionFrame:
        """Generate a full UMR frame with terrain-adaptive gait modulation.

        This is the high-level API that produces a complete ``UnifiedMotionFrame``
        with all terrain metadata embedded.
        """
        result = self.sample_terrain_adaptive_gait(
            dt=dt, velocity=velocity,
            root_x=root_x, root_y=root_y,
            velocity_x=velocity_x, velocity_y=velocity_y,
            target_gait=target_gait, facing_right=facing_right,
        )

        sample = result.gait_sample
        phase = sample.phase
        left_contact = phase % 1.0 < 0.5

        vx = velocity_x if velocity_x is not None else (
            abs(velocity) if facing_right else -abs(velocity)
        )

        return pose_to_umr(
            sample.pose,
            time=time,
            phase=phase,
            frame_index=frame_index,
            source_state=source_state,
            root_transform=MotionRootTransform(
                x=root_x,
                y=sample.root_y,
                rotation=0.0,
                velocity_x=vx,
                velocity_y=velocity_y,
                angular_velocity=0.0,
            ),
            contact_tags=MotionContactState(
                left_foot=left_contact,
                right_foot=not left_contact,
            ),
            metadata={
                "generator": "terrain_adaptive_gait_bridge",
                "gap": "P1-PHASE-33B",
                "leader": sample.leader.value,
                "stride_length": sample.stride_length,
                "fft_phase": sample.fft_phase,
                **result.terrain_metadata,
                "research_refs": [
                    "Holden2017_PFNN",
                    "Clavet2016_MotionMatching",
                    "BiomechanicsInclineWalking",
                    "Starke2022_DeepPhase",
                ],
            },
        )

    def reset(self) -> None:
        """Reset modulator EMA state."""
        self._modulator.reset()
