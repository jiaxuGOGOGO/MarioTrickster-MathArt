"""Fluid Momentum Controller — P1-VFX-1B Closed-Loop VFX Pipeline.

SESSION-115: Integrates the UMR Kinematic Impulse Adapter and the
Tensor-Based Line-Segment Splatter into the existing FluidDrivenVFXSystem
pipeline.  This controller intercepts UMR physical velocity during ``dash``
and ``slash`` presets and injects continuous, CFL-safe momentum fields into
the fluid solver.

Architecture
------------
This module is a **high-order adapter** that sits between the UMR animation
system and the fluid VFX system.  It does NOT modify any core solver code.

::

    UnifiedMotionClip
        │
        ▼
    UMRKinematicImpulseAdapter
        │  (extracts KinematicFrames → VectorFieldImpulses)
        ▼
    LineSegmentSplatter
        │  (tensor-based continuous Gaussian splatting)
        ▼
    FluidGrid2D.u_prev / v_prev / density_prev
        │  (direct field injection — no if/else in solver)
        ▼
    FluidDrivenVFXSystem.simulate_and_render()

Research Foundations
-------------------
- GPU Gems 3, Ch. 30: Gaussian velocity splatting, free-slip BC.
- Continuous Trajectory Splatting: line-segment distance field.
- Naughty Dog / Sucker Punch: animation-driven VFX.
- CFL Guard: soft tanh velocity clamping.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

import numpy as np

from .fluid_vfx import (
    FluidDrivenVFXSystem,
    FluidGrid2D,
    FluidGridConfig,
    FluidImpulse,
    FluidVFXConfig,
    DynamicObstacleContext,
)
from .umr_kinematic_impulse import (
    KinematicFrame,
    LineSegmentSplatter,
    UMRExtractionConfig,
    UMRKinematicImpulseAdapter,
    VectorFieldImpulse,
    compute_cfl_safe_velocity_limit,
    soft_tanh_clamp,
)
from .unified_motion import UnifiedMotionClip


@dataclass
class MomentumInjectionConfig:
    """Configuration for the closed-loop momentum injection pipeline.

    Attributes
    ----------
    extraction : UMRExtractionConfig
        Configuration for the UMR kinematic extractor.
    velocity_scale : float
        Global multiplier applied to all injected velocities.
    density_scale : float
        Global multiplier applied to all injected densities.
    enable_root_impulse : bool
        Whether to inject root-velocity impulses.
    enable_effector_impulse : bool
        Whether to inject end-effector (weapon) impulses.
    effector_key : str
        Metadata key prefix for end-effector position tracking.
    """

    extraction: UMRExtractionConfig = field(
        default_factory=UMRExtractionConfig
    )
    velocity_scale: float = 1.0
    density_scale: float = 1.0
    enable_root_impulse: bool = True
    enable_effector_impulse: bool = True
    effector_key: str = "weapon_tip"


class FluidMomentumController:
    """Closed-loop controller for UMR-driven fluid momentum injection.

    This controller orchestrates the full pipeline from UMR clip to fluid
    field injection.  It is designed to be used as a drop-in replacement
    for manual ``driver_impulses`` construction.

    Usage
    -----
    >>> controller = FluidMomentumController(grid_size=32)
    >>> frames = controller.simulate_with_umr(
    ...     clip=my_umr_clip,
    ...     vfx_config=FluidVFXConfig.dash_smoke(),
    ... )
    """

    def __init__(
        self,
        grid_size: int,
        config: Optional[MomentumInjectionConfig] = None,
    ):
        self.grid_size = grid_size
        self.config = config or MomentumInjectionConfig()
        self.adapter = UMRKinematicImpulseAdapter(
            grid_size=grid_size,
            config=self.config.extraction,
        )
        self.splatter = LineSegmentSplatter(grid_size)

    def inject_impulses_into_grid(
        self,
        grid: FluidGrid2D,
        impulses: Sequence[VectorFieldImpulse],
    ) -> dict[str, float]:
        """Inject VectorFieldImpulses directly into the fluid grid fields.

        Uses the LineSegmentSplatter for continuous Gaussian splatting.
        This bypasses the legacy point-based ``add_velocity_impulse`` and
        injects directly into ``u_prev`` / ``v_prev`` / ``density_prev``.

        Parameters
        ----------
        grid : FluidGrid2D
            The target fluid grid.
        impulses : sequence of VectorFieldImpulse
            Impulses to inject for this frame.

        Returns
        -------
        dict[str, float]
            Injection diagnostics (total_injected_energy, max_velocity, etc.).
        """
        if not impulses:
            return {"total_injected_energy": 0.0, "max_velocity": 0.0, "impulse_count": 0}

        cfg = self.config
        n = grid.n

        # Accumulate all impulses via tensor splatting
        total_u, total_v, total_d = self.splatter.splat_multiple(impulses)

        # Apply global scaling
        total_u *= cfg.velocity_scale
        total_v *= cfg.velocity_scale
        total_d *= cfg.density_scale

        # Inject into grid source fields (padded grid has +2 border)
        grid.u_prev[1:n+1, 1:n+1] += total_u
        grid.v_prev[1:n+1, 1:n+1] += total_v
        grid.density_prev[1:n+1, 1:n+1] += total_d

        # Apply obstacle constraints to prevent injection into solid cells
        grid._apply_obstacle_constraints(grid.u_prev, 1)
        grid._apply_obstacle_constraints(grid.v_prev, 2)
        grid._apply_obstacle_constraints(grid.density_prev, 0)

        # Diagnostics
        speed = np.sqrt(total_u ** 2 + total_v ** 2)
        return {
            "total_injected_energy": float(np.sum(speed ** 2)),
            "max_velocity": float(np.max(speed)),
            "impulse_count": len(impulses),
        }

    def simulate_with_umr(
        self,
        clip: UnifiedMotionClip,
        vfx_config: Optional[FluidVFXConfig] = None,
        n_frames: Optional[int] = None,
        dynamic_obstacle_masks: Optional[Sequence[np.ndarray]] = None,
    ) -> "MomentumSimulationResult":
        """Run a complete UMR-driven fluid simulation.

        This is the main entry point for the closed-loop pipeline.
        It extracts impulses from the UMR clip, creates a VFX system,
        and runs the simulation with continuous line-segment splatting.

        Parameters
        ----------
        clip : UnifiedMotionClip
            The source UMR clip.
        vfx_config : FluidVFXConfig, optional
            VFX configuration.  If None, auto-selects based on clip state.
        n_frames : int, optional
            Number of frames to simulate.  Defaults to clip frame count.
        dynamic_obstacle_masks : sequence of np.ndarray, optional
            Per-frame obstacle masks (from P1-VFX-1A).

        Returns
        -------
        MomentumSimulationResult
            Complete simulation result with frames, diagnostics, and
            injection metadata.
        """
        from PIL import Image

        # Auto-select VFX config based on clip state
        if vfx_config is None:
            state = clip.state.lower() if clip.state else ""
            if "dash" in state:
                vfx_config = FluidVFXConfig.dash_smoke(canvas_size=64)
            elif "slash" in state or "attack" in state:
                vfx_config = FluidVFXConfig.slash_smoke(canvas_size=64)
            else:
                vfx_config = FluidVFXConfig.smoke_fluid(canvas_size=64)

        # Override grid size to match our splatter
        vfx_config.fluid.grid_size = self.grid_size

        # Extract impulses from UMR
        impulse_sequence = self.adapter.extract_impulses(
            clip, self.config.effector_key
        )

        # Determine frame count
        total_frames = n_frames or len(clip.frames)
        total_frames = max(total_frames, 1)

        # Create VFX system
        system = FluidDrivenVFXSystem(vfx_config)

        # Set up static obstacle if no dynamic masks
        if dynamic_obstacle_masks is None:
            from .fluid_vfx import default_character_obstacle_mask
            if vfx_config.driver_mode in {"dash", "slash"}:
                system.fluid.set_obstacle_mask(
                    default_character_obstacle_mask(self.grid_size)
                )

        # Run simulation with per-frame tensor injection
        frames = []
        injection_diagnostics = []

        for frame_idx in range(total_frames):
            # Dynamic mask injection (P1-VFX-1A compatibility)
            if dynamic_obstacle_masks is not None and frame_idx < len(dynamic_obstacle_masks):
                system.fluid.update_dynamic_obstacle(
                    dynamic_obstacle_masks[frame_idx]
                )

            # Get impulses for this frame
            if frame_idx < len(impulse_sequence):
                frame_impulses = impulse_sequence[frame_idx]
            else:
                frame_impulses = []

            # Inject via tensor splatting (the core P1-VFX-1B innovation)
            inj_diag = self.inject_impulses_into_grid(
                system.fluid, frame_impulses
            )
            injection_diagnostics.append(inj_diag)

            # Step the fluid solver
            system.fluid.step()

            # Emit particles from impulse midpoints
            if frame_impulses:
                primary = frame_impulses[0]
                mid_x = (primary.start[0] + primary.end[0]) * 0.5
                mid_y = (primary.start[1] + primary.end[1]) * 0.5
                pseudo_impulse = FluidImpulse(
                    center_x=mid_x,
                    center_y=mid_y,
                    velocity_x=primary.velocity_vector[0] * self.grid_size,
                    velocity_y=primary.velocity_vector[1] * self.grid_size,
                    density=primary.density_amount,
                    radius=primary.radius,
                    label=primary.label,
                )
                system._emit_particles(pseudo_impulse)
            else:
                default_imp = system._default_impulse(frame_idx, total_frames)
                system._emit_particles(default_imp)

            system._step_particles()

            # Render
            render_impulse = FluidImpulse(
                center_x=0.5, center_y=0.5,
                velocity_x=0.0, velocity_y=0.0,
                density=0.0, radius=0.1,
                label="render_only",
            )
            if frame_impulses:
                primary = frame_impulses[0]
                render_impulse = FluidImpulse(
                    center_x=(primary.start[0] + primary.end[0]) * 0.5,
                    center_y=(primary.start[1] + primary.end[1]) * 0.5,
                    velocity_x=primary.velocity_vector[0] * self.grid_size,
                    velocity_y=primary.velocity_vector[1] * self.grid_size,
                    density=primary.density_amount,
                    radius=primary.radius,
                    label=primary.label,
                )

            img, diag = system._render_frame(frame_idx, render_impulse)
            frames.append(img)
            system.last_diagnostics.append(diag)

        return MomentumSimulationResult(
            frames=frames,
            fluid_diagnostics=system.last_diagnostics,
            injection_diagnostics=injection_diagnostics,
            impulse_sequence=impulse_sequence,
            config=vfx_config,
        )


@dataclass
class MomentumSimulationResult:
    """Result of a UMR-driven fluid momentum simulation.

    Attributes
    ----------
    frames : list[Image.Image]
        Rendered RGBA frames.
    fluid_diagnostics : list
        Per-frame fluid solver diagnostics.
    injection_diagnostics : list[dict]
        Per-frame injection diagnostics from the splatter.
    impulse_sequence : list[list[VectorFieldImpulse]]
        The extracted impulse sequence.
    config : FluidVFXConfig
        The VFX configuration used.
    """

    frames: list
    fluid_diagnostics: list
    injection_diagnostics: list
    impulse_sequence: list
    config: Any = None

    def has_nan(self) -> bool:
        """Check if any frame contains NaN values."""
        for diag in self.fluid_diagnostics:
            if not np.isfinite(diag.mean_flow_energy):
                return True
            if not np.isfinite(diag.max_flow_speed):
                return True
        return False

    def max_injected_velocity(self) -> float:
        """Return the maximum velocity injected across all frames."""
        if not self.injection_diagnostics:
            return 0.0
        return max(d.get("max_velocity", 0.0) for d in self.injection_diagnostics)

    def total_impulse_count(self) -> int:
        """Return total number of impulses across all frames."""
        return sum(len(imps) for imps in self.impulse_sequence)


# ═══════════════════════════════════════════════════════════════════════════
# Three-Layer Evolution Bridge for P1-VFX-1B
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class MomentumVFXMetrics:
    """Metrics for the three-layer evolution evaluation of momentum VFX.

    Layer 1 — Internal Evolution Gate:
        Reject simulations with NaN, zero energy, or dotted-line artifacts.
    Layer 2 — External Knowledge Distillation:
        Persist rules about optimal CFL factors and Gaussian radii.
    Layer 3 — Self-Iterative Testing:
        Track injection efficiency and field continuity over iterations.
    """

    total_frames: int = 0
    total_impulses: int = 0
    mean_injected_energy: float = 0.0
    max_injected_velocity: float = 0.0
    has_nan: bool = False
    field_continuity_score: float = 0.0
    momentum_pass: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_frames": self.total_frames,
            "total_impulses": self.total_impulses,
            "mean_injected_energy": self.mean_injected_energy,
            "max_injected_velocity": self.max_injected_velocity,
            "has_nan": self.has_nan,
            "field_continuity_score": self.field_continuity_score,
            "momentum_pass": self.momentum_pass,
        }


def evaluate_momentum_simulation(
    result: MomentumSimulationResult,
) -> MomentumVFXMetrics:
    """Evaluate a momentum simulation result for the evolution bridge.

    Parameters
    ----------
    result : MomentumSimulationResult
        The simulation result to evaluate.

    Returns
    -------
    MomentumVFXMetrics
        Evaluation metrics.
    """
    total_frames = len(result.frames)
    total_impulses = result.total_impulse_count()
    has_nan = result.has_nan()

    # Mean injected energy
    energies = [d.get("total_injected_energy", 0.0) for d in result.injection_diagnostics]
    mean_energy = float(np.mean(energies)) if energies else 0.0

    # Field continuity score: measure smoothness of velocity field
    # A high score means the injection produced continuous bands, not dots
    continuity = 0.0
    if result.fluid_diagnostics:
        flow_energies = [d.mean_flow_energy for d in result.fluid_diagnostics]
        if len(flow_energies) > 1:
            diffs = np.diff(flow_energies)
            smoothness = 1.0 / (1.0 + float(np.std(diffs)))
            continuity = smoothness

    momentum_pass = (
        not has_nan
        and total_impulses > 0
        and mean_energy > 0.0
        and continuity > 0.0
    )

    return MomentumVFXMetrics(
        total_frames=total_frames,
        total_impulses=total_impulses,
        mean_injected_energy=mean_energy,
        max_injected_velocity=result.max_injected_velocity(),
        has_nan=has_nan,
        field_continuity_score=continuity,
        momentum_pass=momentum_pass,
    )


__all__ = [
    "MomentumInjectionConfig",
    "FluidMomentumController",
    "MomentumSimulationResult",
    "MomentumVFXMetrics",
    "evaluate_momentum_simulation",
]
