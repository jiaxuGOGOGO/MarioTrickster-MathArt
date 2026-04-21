"""Grid-based vector-field fluid VFX for physics-driven smoke and dust.

SESSION-046: Gap C2 closure scaffold.

This module upgrades the repository's VFX stack from purely emitter-local
particles to a **Jos Stam style stable-fluid-guided** workflow:

1. A lightweight 2D grid stores velocity and density.
2. Character or action velocity is injected as a force impulse.
3. Semi-Lagrangian advection + implicit diffusion + projection produce a
   divergence-free vector field that naturally curls and swirls.
4. Optional obstacle masks make smoke avoid solid silhouettes.
5. Lightweight particles ride the vector field for stylized pixel-art accents.

The implementation is intentionally NumPy-only so it fits the current project
constraints and the three-layer evolution system.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

import numpy as np
from PIL import Image

try:
    from scipy.ndimage import affine_transform as _scipy_affine
    _HAS_SCIPY = True
except ImportError:  # pragma: no cover
    _HAS_SCIPY = False


@dataclass
class FluidGridConfig:
    """Configuration for the underlying stable-fluid grid."""

    grid_size: int = 32
    dt: float = 1.0 / 12.0
    diffusion: float = 0.0002
    viscosity: float = 0.00012
    iterations: int = 16
    density_dissipation: float = 0.985
    velocity_dissipation: float = 0.992
    seed: int = 42


@dataclass
class FluidImpulse:
    """One per-frame force / density injection into the fluid field."""

    center_x: float
    center_y: float
    velocity_x: float
    velocity_y: float
    density: float = 1.0
    radius: float = 0.12
    label: str = ""


@dataclass
class FluidFrameDiagnostics:
    """Per-frame diagnostics used by rendering, tests, and evolution."""

    frame_index: int
    driver_speed: float
    mean_flow_energy: float
    max_flow_speed: float
    density_mass: float
    obstacle_leak_ratio: float
    obstacle_coverage: float
    active_particles: int

    def to_dict(self) -> dict[str, float | int]:
        return {
            "frame_index": self.frame_index,
            "driver_speed": self.driver_speed,
            "mean_flow_energy": self.mean_flow_energy,
            "max_flow_speed": self.max_flow_speed,
            "density_mass": self.density_mass,
            "obstacle_leak_ratio": self.obstacle_leak_ratio,
            "obstacle_coverage": self.obstacle_coverage,
            "active_particles": self.active_particles,
        }


@dataclass
class FluidParticle:
    """A lightweight particle advected by the grid-based vector field."""

    x: float
    y: float
    vx: float
    vy: float
    lifetime: float
    max_lifetime: float
    size: float
    alive: bool = True

    @property
    def age_ratio(self) -> float:
        return 1.0 - max(0.0, self.lifetime / max(self.max_lifetime, 1e-6))


@dataclass
class FluidVFXConfig:
    """Configuration for the fluid-guided smoke/dust effect."""

    canvas_size: int = 64
    fluid: FluidGridConfig = field(default_factory=FluidGridConfig)
    emit_rate: int = 8
    max_particles: int = 160
    particle_lifetime_min: float = 0.35
    particle_lifetime_max: float = 1.20
    particle_size_min: float = 1.0
    particle_size_max: float = 3.0
    particle_follow_strength: float = 0.84
    particle_drag: float = 0.86
    particle_jitter: float = 5.0
    density_radius: float = 0.12
    velocity_radius: float = 0.14
    density_gain: float = 1.15
    smoke_alpha: float = 0.82
    color_birth: tuple[int, int, int] = (210, 210, 210)
    color_mid: tuple[int, int, int] = (152, 152, 152)
    color_death: tuple[int, int, int] = (82, 82, 82)
    particle_color: tuple[int, int, int] = (220, 220, 220)
    seed: int = 42
    driver_mode: str = "smoke"
    source_x: float = 0.50
    source_y: float = 0.76
    source_wobble: float = 0.04
    source_velocity_scale: float = 18.0

    @classmethod
    def smoke_fluid(cls, canvas_size: int = 64) -> "FluidVFXConfig":
        return cls(
            canvas_size=canvas_size,
            fluid=FluidGridConfig(grid_size=32, diffusion=0.00025, viscosity=0.0001),
            emit_rate=10,
            max_particles=180,
            particle_jitter=3.0,
            density_gain=1.00,
            driver_mode="smoke",
            source_x=0.50,
            source_y=0.78,
            source_velocity_scale=16.0,
        )

    @classmethod
    def dash_smoke(cls, canvas_size: int = 64) -> "FluidVFXConfig":
        return cls(
            canvas_size=canvas_size,
            fluid=FluidGridConfig(grid_size=32, diffusion=0.00015, viscosity=0.00008),
            emit_rate=14,
            max_particles=220,
            particle_jitter=6.0,
            density_gain=1.28,
            driver_mode="dash",
            source_x=0.22,
            source_y=0.74,
            source_velocity_scale=25.0,
        )

    @classmethod
    def slash_smoke(cls, canvas_size: int = 64) -> "FluidVFXConfig":
        return cls(
            canvas_size=canvas_size,
            fluid=FluidGridConfig(grid_size=36, diffusion=0.00018, viscosity=0.00009),
            emit_rate=12,
            max_particles=220,
            particle_jitter=7.0,
            density_gain=1.15,
            driver_mode="slash",
            source_x=0.36,
            source_y=0.62,
            source_velocity_scale=22.0,
        )


class FluidGrid2D:
    """A lightweight stable-fluids 2D solver with optional internal obstacles."""

    def __init__(self, config: FluidGridConfig):
        self.config = config
        n = config.grid_size
        shape = (n + 2, n + 2)
        self.u = np.zeros(shape, dtype=np.float64)
        self.v = np.zeros(shape, dtype=np.float64)
        self.u_prev = np.zeros(shape, dtype=np.float64)
        self.v_prev = np.zeros(shape, dtype=np.float64)
        self.density = np.zeros(shape, dtype=np.float64)
        self.density_prev = np.zeros(shape, dtype=np.float64)
        self.obstacles = np.zeros(shape, dtype=bool)

    @property
    def n(self) -> int:
        return self.config.grid_size

    def clear_sources(self) -> None:
        self.u_prev.fill(0.0)
        self.v_prev.fill(0.0)
        self.density_prev.fill(0.0)

    def set_obstacle_mask(self, mask: Optional[np.ndarray]) -> None:
        """Set interior solid obstacles using an (N,N) boolean-like mask."""
        self.obstacles.fill(False)
        if mask is None:
            return
        arr = np.asarray(mask)
        if arr.ndim != 2:
            raise ValueError("Obstacle mask must be 2D.")
        if arr.shape != (self.n, self.n):
            raise ValueError(f"Obstacle mask shape must be {(self.n, self.n)}, got {arr.shape}.")
        self.obstacles[1 : self.n + 1, 1 : self.n + 1] = arr.astype(bool)
        self._apply_obstacle_constraints(self.u, 1)
        self._apply_obstacle_constraints(self.v, 2)
        self._apply_obstacle_constraints(self.density, 0)

    def update_dynamic_obstacle(
        self,
        mask: np.ndarray,
        obstacle_velocity: Optional[np.ndarray] = None,
    ) -> None:
        """Update obstacle mask for the current frame (SESSION-114 / P1-VFX-1A).

        Implements the Solid-Fluid Coupling volume clearing protocol:
        1. Identify newly-covered cells (new mask & ~old mask).
        2. Zero out density and velocity in newly-covered cells to prevent
           divergence explosion (Anti-Divergence NaN Guard).
        3. Update the obstacle mask for the current frame.

        Parameters
        ----------
        mask : np.ndarray
            Boolean (N, N) obstacle mask for this frame.
        obstacle_velocity : np.ndarray, optional
            (N, N, 2) velocity field of the obstacle.  Used for free-slip
            boundary conditions.  If None, obstacle velocity is zero.
        """
        arr = np.asarray(mask)
        if arr.ndim != 2 or arr.shape != (self.n, self.n):
            raise ValueError(f"Obstacle mask shape must be {(self.n, self.n)}, got {arr.shape}.")
        new_mask_padded = np.zeros_like(self.obstacles)
        new_mask_padded[1 : self.n + 1, 1 : self.n + 1] = arr.astype(bool)

        # Volume clearing: zero density/velocity in NEWLY covered cells
        newly_covered = new_mask_padded & ~self.obstacles
        if np.any(newly_covered):
            self.density[newly_covered] = 0.0
            self.u[newly_covered] = 0.0
            self.v[newly_covered] = 0.0
            self.density_prev[newly_covered] = 0.0
            self.u_prev[newly_covered] = 0.0
            self.v_prev[newly_covered] = 0.0

        self.obstacles = new_mask_padded

        # Store obstacle velocity for free-slip BC (P1-VFX-1B bridge)
        if obstacle_velocity is not None:
            ov = np.asarray(obstacle_velocity)
            if ov.shape == (self.n, self.n, 2):
                self._obstacle_velocity_u = np.zeros_like(self.u)
                self._obstacle_velocity_v = np.zeros_like(self.v)
                self._obstacle_velocity_u[1:self.n+1, 1:self.n+1] = ov[..., 0]
                self._obstacle_velocity_v[1:self.n+1, 1:self.n+1] = ov[..., 1]
            else:
                self._obstacle_velocity_u = None
                self._obstacle_velocity_v = None
        else:
            self._obstacle_velocity_u = None
            self._obstacle_velocity_v = None

        # Apply constraints to current fields
        self._apply_obstacle_constraints(self.u, 1)
        self._apply_obstacle_constraints(self.v, 2)
        self._apply_obstacle_constraints(self.density, 0)

    def add_velocity_impulse(
        self,
        center: tuple[float, float],
        velocity: tuple[float, float],
        radius: float = 0.12,
    ) -> None:
        cx, cy = center
        vx, vy = velocity
        yy, xx = np.mgrid[1 : self.n + 1, 1 : self.n + 1]
        px = (xx - 0.5) / self.n
        py = (yy - 0.5) / self.n
        dist2 = (px - cx) ** 2 + (py - cy) ** 2
        sigma2 = max(radius * radius, 1e-6)
        weight = np.exp(-dist2 / (2.0 * sigma2))
        self.u_prev[1 : self.n + 1, 1 : self.n + 1] += vx * weight
        self.v_prev[1 : self.n + 1, 1 : self.n + 1] += vy * weight
        self._apply_obstacle_constraints(self.u_prev, 1)
        self._apply_obstacle_constraints(self.v_prev, 2)

    def add_density_impulse(
        self,
        center: tuple[float, float],
        amount: float,
        radius: float = 0.10,
    ) -> None:
        cx, cy = center
        yy, xx = np.mgrid[1 : self.n + 1, 1 : self.n + 1]
        px = (xx - 0.5) / self.n
        py = (yy - 0.5) / self.n
        dist2 = (px - cx) ** 2 + (py - cy) ** 2
        sigma2 = max(radius * radius, 1e-6)
        weight = np.exp(-dist2 / (2.0 * sigma2))
        self.density_prev[1 : self.n + 1, 1 : self.n + 1] += amount * weight
        self._apply_obstacle_constraints(self.density_prev, 0)

    def sample_velocity(self, x_norm: float, y_norm: float) -> tuple[float, float]:
        """Bilinearly sample the interior velocity field using normalized coords."""
        x = min(max(x_norm * self.n + 0.5, 0.5), self.n + 0.5)
        y = min(max(y_norm * self.n + 0.5, 0.5), self.n + 0.5)
        i0 = int(math.floor(x))
        j0 = int(math.floor(y))
        i1 = min(i0 + 1, self.n + 1)
        j1 = min(j0 + 1, self.n + 1)
        s1 = x - i0
        s0 = 1.0 - s1
        t1 = y - j0
        t0 = 1.0 - t1
        u = (
            s0 * (t0 * self.u[i0, j0] + t1 * self.u[i0, j1])
            + s1 * (t0 * self.u[i1, j0] + t1 * self.u[i1, j1])
        )
        v = (
            s0 * (t0 * self.v[i0, j0] + t1 * self.v[i0, j1])
            + s1 * (t0 * self.v[i1, j0] + t1 * self.v[i1, j1])
        )
        return float(u), float(v)

    def step(self) -> None:
        self._velocity_step()
        self._density_step()
        self.u *= self.config.velocity_dissipation
        self.v *= self.config.velocity_dissipation
        self.density *= self.config.density_dissipation
        self._apply_obstacle_constraints(self.u, 1)
        self._apply_obstacle_constraints(self.v, 2)
        self._apply_obstacle_constraints(self.density, 0)
        self.clear_sources()

    def interior_density(self) -> np.ndarray:
        return self.density[1 : self.n + 1, 1 : self.n + 1]

    def interior_speed(self) -> np.ndarray:
        ui = self.u[1 : self.n + 1, 1 : self.n + 1]
        vi = self.v[1 : self.n + 1, 1 : self.n + 1]
        return np.sqrt(ui * ui + vi * vi)

    def obstacle_mask(self) -> np.ndarray:
        return self.obstacles[1 : self.n + 1, 1 : self.n + 1].copy()

    def render_density_image(
        self,
        canvas_size: int,
        color_birth: tuple[int, int, int],
        color_mid: tuple[int, int, int],
        color_death: tuple[int, int, int],
        alpha_scale: float = 0.80,
    ) -> Image.Image:
        dens = np.clip(self.interior_density(), 0.0, None)
        if dens.max() > 1e-9:
            dens = dens / max(dens.max(), 1.0)
        dens_img = Image.fromarray((dens * 255).astype(np.uint8), mode="L")
        dens_img = dens_img.resize((canvas_size, canvas_size), resample=Image.Resampling.BILINEAR)
        arr = np.asarray(dens_img, dtype=np.float32) / 255.0
        rgba = np.zeros((canvas_size, canvas_size, 4), dtype=np.uint8)

        low_mask = arr < 0.5
        high_mask = ~low_mask

        t = np.zeros_like(arr)
        t[low_mask] = arr[low_mask] * 2.0
        t[high_mask] = (arr[high_mask] - 0.5) * 2.0

        birth = np.asarray(color_birth, dtype=np.float32)
        mid = np.asarray(color_mid, dtype=np.float32)
        death = np.asarray(color_death, dtype=np.float32)

        for c in range(3):
            ch = np.zeros_like(arr, dtype=np.float32)
            ch[low_mask] = birth[c] * (1.0 - t[low_mask]) + mid[c] * t[low_mask]
            ch[high_mask] = mid[c] * (1.0 - t[high_mask]) + death[c] * t[high_mask]
            rgba[..., c] = np.clip(ch, 0, 255).astype(np.uint8)

        alpha = np.clip(arr * 255.0 * alpha_scale, 0, 255)
        rgba[..., 3] = alpha.astype(np.uint8)
        return Image.fromarray(rgba, mode="RGBA")

    def _add_source(self, field: np.ndarray, source: np.ndarray) -> np.ndarray:
        return field + self.config.dt * source

    def _velocity_step(self) -> None:
        u = self._add_source(self.u, self.u_prev)
        v = self._add_source(self.v, self.v_prev)
        u0 = u.copy()
        v0 = v.copy()
        u = self._diffuse(1, u, u0, self.config.viscosity)
        v = self._diffuse(2, v, v0, self.config.viscosity)
        u, v = self._project(u, v)
        u0 = u.copy()
        v0 = v.copy()
        u = self._advect(1, u, u0, u0, v0)
        v = self._advect(2, v, v0, u0, v0)
        u, v = self._project(u, v)
        self.u = u
        self.v = v

    def _density_step(self) -> None:
        d = self._add_source(self.density, self.density_prev)
        d0 = d.copy()
        d = self._diffuse(0, d, d0, self.config.diffusion)
        d0 = d.copy()
        d = self._advect(0, d, d0, self.u, self.v)
        self.density = d

    def _diffuse(self, b: int, x: np.ndarray, x0: np.ndarray, diff: float) -> np.ndarray:
        a = self.config.dt * diff * self.n * self.n
        return self._lin_solve(b, x.copy(), x0, a, 1.0 + 4.0 * a)

    def _lin_solve(self, b: int, x: np.ndarray, x0: np.ndarray, a: float, c: float) -> np.ndarray:
        if a <= 1e-12:
            self._set_bnd(b, x)
            return x
        c_recip = 1.0 / max(c, 1e-12)
        for _ in range(max(1, self.config.iterations)):
            x[1 : self.n + 1, 1 : self.n + 1] = (
                x0[1 : self.n + 1, 1 : self.n + 1]
                + a
                * (
                    x[0:self.n, 1 : self.n + 1]
                    + x[2 : self.n + 2, 1 : self.n + 1]
                    + x[1 : self.n + 1, 0:self.n]
                    + x[1 : self.n + 1, 2 : self.n + 2]
                )
            ) * c_recip
            self._set_bnd(b, x)
        return x

    def _advect(
        self,
        b: int,
        d: np.ndarray,
        d0: np.ndarray,
        u: np.ndarray,
        v: np.ndarray,
    ) -> np.ndarray:
        """Semi-Lagrangian advection — fully vectorised (SESSION-114).

        Replaces the original O(N^2) scalar double-loop with pure NumPy
        tensor operations.  Anti-Scalar Rasterization Guard: zero Python
        ``for`` loops in the hot path.
        """
        result = d.copy()
        n = self.n
        dt0 = self.config.dt * n

        # Build grid coordinate arrays for interior cells [1..n]
        ii, jj = np.mgrid[1:n+1, 1:n+1]  # (n, n) each
        ii_f = ii.astype(np.float64)
        jj_f = jj.astype(np.float64)

        # Back-trace departure points
        x = ii_f - dt0 * u[1:n+1, 1:n+1]
        y = jj_f - dt0 * v[1:n+1, 1:n+1]

        # Clamp to valid range [0.5, n+0.5]
        x = np.clip(x, 0.5, n + 0.5)
        y = np.clip(y, 0.5, n + 0.5)

        # Integer floor indices
        i0 = np.floor(x).astype(int)
        j0 = np.floor(y).astype(int)
        i1 = np.minimum(i0 + 1, n + 1)
        j1 = np.minimum(j0 + 1, n + 1)

        # Interpolation weights
        s1 = x - i0.astype(np.float64)
        s0 = 1.0 - s1
        t1 = y - j0.astype(np.float64)
        t0 = 1.0 - t1

        # Bilinear interpolation (pure tensor indexing)
        result[1:n+1, 1:n+1] = (
            s0 * (t0 * d0[i0, j0] + t1 * d0[i0, j1])
            + s1 * (t0 * d0[i1, j0] + t1 * d0[i1, j1])
        )

        # Force obstacle cells to zero (dynamic boundary enforcement)
        obs_interior = self.obstacles[1:n+1, 1:n+1]
        result[1:n+1, 1:n+1][obs_interior] = 0.0

        self._set_bnd(b, result)
        return result

    def _project(self, u: np.ndarray, v: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        p = np.zeros_like(u)
        div = np.zeros_like(u)
        h = 1.0 / self.n
        div[1 : self.n + 1, 1 : self.n + 1] = -0.5 * h * (
            u[2 : self.n + 2, 1 : self.n + 1] - u[0:self.n, 1 : self.n + 1]
            + v[1 : self.n + 1, 2 : self.n + 2] - v[1 : self.n + 1, 0:self.n]
        )
        self._set_bnd(0, div)
        self._set_bnd(0, p)
        p = self._lin_solve(0, p, div, 1.0, 4.0)
        u[1 : self.n + 1, 1 : self.n + 1] -= 0.5 * (
            p[2 : self.n + 2, 1 : self.n + 1] - p[0:self.n, 1 : self.n + 1]
        ) / h
        v[1 : self.n + 1, 1 : self.n + 1] -= 0.5 * (
            p[1 : self.n + 1, 2 : self.n + 2] - p[1 : self.n + 1, 0:self.n]
        ) / h
        self._set_bnd(1, u)
        self._set_bnd(2, v)
        return u, v

    def _set_bnd(self, b: int, x: np.ndarray) -> None:
        """Boundary conditions — vectorised (SESSION-114)."""
        n = self.n
        s = slice(1, n + 1)
        if b == 1:
            x[0, s] = -x[1, s]
            x[n + 1, s] = -x[n, s]
        else:
            x[0, s] = x[1, s]
            x[n + 1, s] = x[n, s]
        if b == 2:
            x[s, 0] = -x[s, 1]
            x[s, n + 1] = -x[s, n]
        else:
            x[s, 0] = x[s, 1]
            x[s, n + 1] = x[s, n]
        x[0, 0] = 0.5 * (x[1, 0] + x[0, 1])
        x[0, n + 1] = 0.5 * (x[1, n + 1] + x[0, n])
        x[n + 1, 0] = 0.5 * (x[n, 0] + x[n + 1, 1])
        x[n + 1, n + 1] = 0.5 * (x[n, n + 1] + x[n + 1, n])
        self._apply_obstacle_constraints(x, b)

    def _apply_obstacle_constraints(self, x: np.ndarray, b: int) -> None:
        if not np.any(self.obstacles):
            return
        interior = self.obstacles[1 : self.n + 1, 1 : self.n + 1]
        target = x[1 : self.n + 1, 1 : self.n + 1]
        if b == 0:
            target[interior] = 0.0
        else:
            target[interior] = 0.0


class FluidDrivenVFXSystem:
    """Smoke/dust renderer driven by a Stable Fluids style 2D grid."""

    def __init__(self, config: FluidVFXConfig):
        self.config = config
        self.rng = random.Random(config.seed)
        self.fluid = FluidGrid2D(config.fluid)
        self.particles: list[FluidParticle] = []
        self.last_diagnostics: list[FluidFrameDiagnostics] = []

    def simulate_and_render(
        self,
        n_frames: int = 16,
        driver_impulses: Optional[Sequence[FluidImpulse]] = None,
        obstacle_mask: Optional[np.ndarray] = None,
        dynamic_obstacle_masks: Optional[Sequence[np.ndarray]] = None,
    ) -> list[Image.Image]:
        """Run the fluid simulation and render frames.

        Parameters
        ----------
        n_frames : int
            Number of frames to simulate.
        driver_impulses : sequence of FluidImpulse, optional
            Per-frame force/density injections.
        obstacle_mask : np.ndarray, optional
            Static (N, N) boolean obstacle mask applied once before the loop.
            Backward-compatible with SESSION-046 contract.
        dynamic_obstacle_masks : sequence of np.ndarray, optional
            Per-frame (N, N) boolean obstacle masks (SESSION-114 / P1-VFX-1A).
            When provided, each frame's mask is injected via
            ``update_dynamic_obstacle`` with volume clearing.  Takes
            precedence over ``obstacle_mask``.
        """
        # Static mask fallback (backward-compatible)
        if dynamic_obstacle_masks is None:
            if obstacle_mask is not None:
                self.fluid.set_obstacle_mask(obstacle_mask)
            elif self.config.driver_mode in {"dash", "slash"}:
                self.fluid.set_obstacle_mask(
                    default_character_obstacle_mask(self.config.fluid.grid_size)
                )

        frames: list[Image.Image] = []
        diagnostics: list[FluidFrameDiagnostics] = []

        for frame_idx in range(n_frames):
            # Dynamic mask injection (P1-VFX-1A)
            if dynamic_obstacle_masks is not None and frame_idx < len(dynamic_obstacle_masks):
                self.fluid.update_dynamic_obstacle(dynamic_obstacle_masks[frame_idx])

            impulse = (
                driver_impulses[frame_idx]
                if driver_impulses is not None and frame_idx < len(driver_impulses)
                else self._default_impulse(frame_idx, n_frames)
            )
            self._apply_impulse(impulse)
            self.fluid.step()
            self._emit_particles(impulse)
            self._step_particles()
            img, diag = self._render_frame(frame_idx, impulse)
            frames.append(img)
            diagnostics.append(diag)

        self.last_diagnostics = diagnostics
        return frames

    def build_metadata(self, preset_name: str, n_frames: int) -> dict[str, Any]:
        diag_dicts = [d.to_dict() for d in self.last_diagnostics]
        mean_energy = float(np.mean([d.mean_flow_energy for d in self.last_diagnostics])) if self.last_diagnostics else 0.0
        max_speed = float(np.max([d.max_flow_speed for d in self.last_diagnostics])) if self.last_diagnostics else 0.0
        leak = float(np.mean([d.obstacle_leak_ratio for d in self.last_diagnostics])) if self.last_diagnostics else 0.0
        return {
            "meta": {
                "generator": "MarioTrickster-MathArt/FluidDrivenVFXSystem",
                "image_count": n_frames,
                "canvas_size": self.config.canvas_size,
                "driver_mode": self.config.driver_mode,
                "grid_size": self.config.fluid.grid_size,
                "stable_fluids": True,
                "preset": preset_name,
            },
            "fluid": {
                "dt": self.config.fluid.dt,
                "diffusion": self.config.fluid.diffusion,
                "viscosity": self.config.fluid.viscosity,
                "iterations": self.config.fluid.iterations,
                "density_dissipation": self.config.fluid.density_dissipation,
                "velocity_dissipation": self.config.fluid.velocity_dissipation,
                "mean_flow_energy": mean_energy,
                "max_flow_speed": max_speed,
                "mean_obstacle_leak_ratio": leak,
            },
            "frames": diag_dicts,
        }

    def export_spritesheet(self, frames: list[Image.Image], path: str) -> dict[str, Any]:
        n = len(frames)
        cs = self.config.canvas_size
        sheet = Image.new("RGBA", (cs * n, cs), (0, 0, 0, 0))
        for i, frame in enumerate(frames):
            sheet.paste(frame, (i * cs, 0))
        sheet.save(path)
        return {
            "meta": {
                "image": str(path),
                "format": "RGBA8888",
                "size": {"w": cs * n, "h": cs},
                "generator": "MarioTrickster-MathArt/FluidDrivenVFXSystem",
            },
            "frames": [
                {
                    "name": f"fluid_vfx_{i:02d}",
                    "rect": [i * cs, 0, cs, cs],
                    "duration": int(self.config.fluid.dt * 1000),
                }
                for i in range(n)
            ],
        }

    def export_gif(self, frames: list[Image.Image], path: str, loop: bool = True) -> None:
        duration = max(16, int(self.config.fluid.dt * 1000))
        frames[0].save(
            path,
            save_all=True,
            append_images=frames[1:],
            duration=duration,
            loop=0 if loop else 1,
            disposal=2,
        )

    def _default_impulse(self, frame_idx: int, n_frames: int) -> FluidImpulse:
        cfg = self.config
        t = frame_idx / max(n_frames - 1, 1)
        wobble = math.sin(t * math.tau * 1.5) * cfg.source_wobble
        if cfg.driver_mode == "dash":
            x = 0.18 + 0.56 * t
            y = cfg.source_y + 0.02 * math.sin(t * math.tau)
            vx = cfg.source_velocity_scale
            vy = -2.0 + 4.0 * math.sin(t * math.pi)
            density = cfg.density_gain * 1.2
        elif cfg.driver_mode == "slash":
            angle = math.pi * (0.9 - 1.2 * t)
            radius = 0.18
            cx = 0.50
            cy = 0.58
            x = cx + math.cos(angle) * radius
            y = cy + math.sin(angle) * radius
            tangent = angle - math.pi / 2.0
            vx = math.cos(tangent) * cfg.source_velocity_scale
            vy = math.sin(tangent) * cfg.source_velocity_scale
            density = cfg.density_gain * (0.9 + 0.3 * math.sin(t * math.pi))
        else:
            x = cfg.source_x + wobble
            y = cfg.source_y - 0.08 * t
            vx = 2.0 * math.sin(t * math.tau)
            vy = -cfg.source_velocity_scale
            density = cfg.density_gain
        return FluidImpulse(
            center_x=min(max(x, 0.08), 0.92),
            center_y=min(max(y, 0.08), 0.92),
            velocity_x=vx,
            velocity_y=vy,
            density=density,
            radius=cfg.velocity_radius,
            label=cfg.driver_mode,
        )

    def _apply_impulse(self, impulse: FluidImpulse) -> None:
        scale = max(self.config.canvas_size, 1)
        vel = (impulse.velocity_x / scale, impulse.velocity_y / scale)
        center = (impulse.center_x, impulse.center_y)
        self.fluid.add_velocity_impulse(center, vel, radius=impulse.radius)
        self.fluid.add_density_impulse(center, impulse.density, radius=self.config.density_radius)

    def _emit_particles(self, impulse: FluidImpulse) -> None:
        cfg = self.config
        for _ in range(cfg.emit_rate):
            if len(self.particles) >= cfg.max_particles:
                break
            px = impulse.center_x + self.rng.uniform(-0.03, 0.03)
            py = impulse.center_y + self.rng.uniform(-0.03, 0.03)
            flowx, flowy = self.fluid.sample_velocity(px, py)
            life = self.rng.uniform(cfg.particle_lifetime_min, cfg.particle_lifetime_max)
            size = self.rng.uniform(cfg.particle_size_min, cfg.particle_size_max)
            self.particles.append(
                FluidParticle(
                    x=min(max(px, 0.0), 1.0),
                    y=min(max(py, 0.0), 1.0),
                    vx=flowx * cfg.canvas_size,
                    vy=flowy * cfg.canvas_size,
                    lifetime=life,
                    max_lifetime=life,
                    size=size,
                )
            )

    def _step_particles(self) -> None:
        cfg = self.config
        obstacle = self.fluid.obstacle_mask()
        for p in self.particles:
            if not p.alive:
                continue
            p.lifetime -= cfg.fluid.dt
            if p.lifetime <= 0:
                p.alive = False
                continue
            flowx, flowy = self.fluid.sample_velocity(p.x, p.y)
            flowx *= cfg.canvas_size
            flowy *= cfg.canvas_size
            p.vx = p.vx * cfg.particle_drag + flowx * cfg.particle_follow_strength + self.rng.gauss(0.0, cfg.particle_jitter)
            p.vy = p.vy * cfg.particle_drag + flowy * cfg.particle_follow_strength + self.rng.gauss(0.0, cfg.particle_jitter)
            p.x = min(max(p.x + (p.vx * cfg.fluid.dt) / cfg.canvas_size, 0.0), 1.0)
            p.y = min(max(p.y + (p.vy * cfg.fluid.dt) / cfg.canvas_size, 0.0), 1.0)
            ix = min(max(int(p.x * self.fluid.n), 0), self.fluid.n - 1)
            iy = min(max(int(p.y * self.fluid.n), 0), self.fluid.n - 1)
            if obstacle[iy, ix]:
                p.alive = False
        self.particles = [p for p in self.particles if p.alive]

    def _render_frame(self, frame_idx: int, impulse: FluidImpulse) -> tuple[Image.Image, FluidFrameDiagnostics]:
        base = self.fluid.render_density_image(
            self.config.canvas_size,
            self.config.color_birth,
            self.config.color_mid,
            self.config.color_death,
            alpha_scale=self.config.smoke_alpha,
        )
        buffer = np.asarray(base, dtype=np.float32) / 255.0
        cs = self.config.canvas_size
        for p in self.particles:
            t = p.age_ratio
            alpha = (1.0 - t) * 0.55
            size = p.size * (1.0 - 0.65 * t)
            half = max(0.5, size * 0.5)
            px = int(round(p.x * (cs - 1)))
            py = int(round(p.y * (cs - 1)))
            half_i = int(math.ceil(half))
            for dy in range(-half_i, half_i + 1):
                for dx in range(-half_i, half_i + 1):
                    ix = px + dx
                    iy = py + dy
                    if 0 <= ix < cs and 0 <= iy < cs and max(abs(dx), abs(dy)) <= half:
                        dst_a = buffer[iy, ix, 3]
                        src_a = alpha
                        out_a = src_a + dst_a * (1.0 - src_a)
                        if out_a <= 1e-6:
                            continue
                        for c, value in enumerate(self.config.particle_color):
                            src_c = value / 255.0
                            buffer[iy, ix, c] = (
                                src_c * src_a + buffer[iy, ix, c] * dst_a * (1.0 - src_a)
                            ) / out_a
                        buffer[iy, ix, 3] = out_a
        buffer = np.clip(buffer, 0.0, 1.0)
        img = Image.fromarray((buffer * 255).astype(np.uint8), mode="RGBA")

        speed = self.fluid.interior_speed()
        density = self.fluid.interior_density()
        obs = self.fluid.obstacle_mask()
        obstacle_coverage = float(obs.mean()) if obs.size else 0.0
        if np.any(obs):
            obstacle_leak_ratio = float(density[obs].sum() / max(density.sum(), 1e-9))
        else:
            obstacle_leak_ratio = 0.0
        diag = FluidFrameDiagnostics(
            frame_index=frame_idx,
            driver_speed=float(math.hypot(impulse.velocity_x, impulse.velocity_y)),
            mean_flow_energy=float(np.mean(speed * speed)),
            max_flow_speed=float(np.max(speed)) if speed.size else 0.0,
            density_mass=float(np.sum(density)),
            obstacle_leak_ratio=obstacle_leak_ratio,
            obstacle_coverage=obstacle_coverage,
            active_particles=len(self.particles),
        )
        return img, diag


# ══════════════════════════════════════════════════════════════════════════════
# SESSION-114 / P1-VFX-1A — Dynamic Mask Projection & Boundary-Aware Solver
# ══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class MaskProjectionConfig:
    """Configuration for cross-space silhouette-to-grid projection.

    Defines the affine mapping from a high-resolution character alpha mask
    (render space) to the low-resolution fluid grid (simulation space).

    Attributes
    ----------
    world_bbox : tuple[float, float, float, float]
        Physical bounding box of the character in world space (x0, y0, x1, y1).
        This defines where the character sits in the fluid domain [0, 1]^2.
    threshold : float
        Alpha threshold for converting soft mask to boolean obstacle.
    """
    world_bbox: tuple[float, float, float, float] = (0.3, 0.4, 0.7, 0.9)
    threshold: float = 0.3


class FluidMaskProjector:
    """Cross-space tensor silhouette-to-grid projector (SESSION-114 / P1-VFX-1A).

    Projects a high-resolution character alpha mask from render space into the
    fluid simulation grid using pure tensor operations (NumPy/SciPy).  Zero
    Python scalar loops.  Grounded in Ghost of Tsushima (SIGGRAPH 2021) local
    affine injection and Data-Oriented Grid Rasterization principles.

    The projector computes an affine transform matrix that maps grid
    coordinates to source mask coordinates, then applies anti-aliased
    bilinear interpolation via ``scipy.ndimage.affine_transform`` (or a
    pure-NumPy fallback).
    """

    def __init__(self, grid_size: int, config: Optional[MaskProjectionConfig] = None):
        self.grid_size = grid_size
        self.config = config or MaskProjectionConfig()

    def project(
        self,
        alpha_mask: np.ndarray,
        world_bbox: Optional[tuple[float, float, float, float]] = None,
        threshold: Optional[float] = None,
    ) -> np.ndarray:
        """Project a high-res alpha mask onto the fluid grid.

        Parameters
        ----------
        alpha_mask : np.ndarray
            2D float32/uint8 array of shape (H_src, W_src) with values in
            [0, 1] (float) or [0, 255] (uint8).  Represents the character
            silhouette alpha channel.
        world_bbox : tuple, optional
            Override for the character's physical bounding box in normalised
            fluid domain coordinates [0, 1]^2.  Format: (x0, y0, x1, y1).
        threshold : float, optional
            Override alpha threshold for boolean conversion.

        Returns
        -------
        np.ndarray
            Boolean mask of shape (grid_size, grid_size).
        """
        bbox = world_bbox or self.config.world_bbox
        thresh = threshold if threshold is not None else self.config.threshold
        N = self.grid_size

        # Normalise input to float [0, 1]
        src = np.asarray(alpha_mask, dtype=np.float64)
        if src.ndim == 3:
            src = src[..., -1]  # take alpha channel
        if src.max() > 1.5:
            src = src / 255.0
        H_src, W_src = src.shape

        x0, y0, x1, y1 = bbox
        # Grid cell centres in normalised coords
        # Grid coord (i, j) maps to normalised (j+0.5)/N, (i+0.5)/N
        # We need affine: grid_coord -> src_coord
        # src_row = (grid_row - y0*N) / ((y1-y0)*N) * H_src
        # src_col = (grid_col - x0*N) / ((x1-x0)*N) * W_src

        dy = max(y1 - y0, 1e-9)
        dx = max(x1 - x0, 1e-9)

        # Affine matrix: maps output (grid) coords to input (src) coords
        # output[i, j] = src[A @ [i, j] + b]
        scale_row = H_src / (dy * N)
        scale_col = W_src / (dx * N)
        offset_row = -y0 * N * scale_row
        offset_col = -x0 * N * scale_col

        if _HAS_SCIPY:
            matrix = np.array([[scale_row, 0.0], [0.0, scale_col]])
            offset = np.array([offset_row, offset_col])
            projected = _scipy_affine(
                src, matrix, offset,
                output_shape=(N, N),
                order=1,  # bilinear
                mode='constant',
                cval=0.0,
            )
        else:
            # Pure NumPy fallback: build coordinate grids and sample
            gi, gj = np.mgrid[0:N, 0:N]
            si = gi.astype(np.float64) * scale_row + offset_row
            sj = gj.astype(np.float64) * scale_col + offset_col
            # Clamp to valid range
            si = np.clip(si, 0, H_src - 1)
            sj = np.clip(sj, 0, W_src - 1)
            # Nearest-neighbour (fallback)
            si_int = np.clip(np.round(si).astype(int), 0, H_src - 1)
            sj_int = np.clip(np.round(sj).astype(int), 0, W_src - 1)
            projected = src[si_int, sj_int]

        return (projected >= thresh).astype(bool)

    def project_sequence(
        self,
        alpha_masks: Sequence[np.ndarray],
        world_bboxes: Optional[Sequence[tuple[float, float, float, float]]] = None,
    ) -> list[np.ndarray]:
        """Project a sequence of alpha masks (one per frame).

        Returns a list of boolean (N, N) masks.
        """
        result = []
        for i, mask in enumerate(alpha_masks):
            bbox = world_bboxes[i] if world_bboxes is not None else None
            result.append(self.project(mask, world_bbox=bbox))
        return result


@dataclass(frozen=True)
class DynamicObstacleContext:
    """Per-frame dynamic obstacle context for the fluid solver.

    This is the strong-typed contract for injecting dynamic obstacle masks
    into the fluid simulation loop.  Downstream consumers (e.g. P1-VFX-1B)
    can extend this with velocity fields.

    Attributes
    ----------
    mask : np.ndarray
        Boolean (N, N) obstacle mask for this frame.
    velocity : np.ndarray or None
        Optional (N, N, 2) obstacle velocity field.  If provided, the solver
        uses it for free-slip boundary conditions.  If None, obstacle velocity
        is assumed zero.
    """
    mask: np.ndarray
    velocity: Optional[np.ndarray] = None


def resize_mask_to_grid(mask: np.ndarray | Image.Image, grid_size: int) -> np.ndarray:
    """Resize an alpha or grayscale mask into an (N,N) boolean obstacle mask."""
    if isinstance(mask, Image.Image):
        img = mask.convert("L")
    else:
        arr = np.asarray(mask)
        if arr.ndim == 3:
            arr = arr[..., -1]
        arr = np.clip(arr, 0, 255).astype(np.uint8)
        img = Image.fromarray(arr, mode="L")
    img = img.resize((grid_size, grid_size), resample=Image.Resampling.NEAREST)
    return (np.asarray(img, dtype=np.uint8) > 0).astype(bool)


def default_character_obstacle_mask(grid_size: int) -> np.ndarray:
    """A simple capsule-like obstacle used for standalone dash/slash demos.

    This is not meant to replace real character silhouette masks. It exists so the
    fluid presets demonstrate wrap-around behavior even when no external sprite
    mask is supplied. Real pipeline integrations can pass a true mask later.
    """
    yy, xx = np.mgrid[0:grid_size, 0:grid_size]
    nx = (xx + 0.5) / grid_size
    ny = (yy + 0.5) / grid_size
    torso = ((nx - 0.5) / 0.11) ** 2 + ((ny - 0.60) / 0.18) ** 2 <= 1.0
    head = ((nx - 0.5) / 0.07) ** 2 + ((ny - 0.42) / 0.08) ** 2 <= 1.0
    return torso | head


__all__ = [
    "FluidGridConfig",
    "FluidImpulse",
    "FluidFrameDiagnostics",
    "FluidParticle",
    "FluidVFXConfig",
    "FluidGrid2D",
    "FluidDrivenVFXSystem",
    "resize_mask_to_grid",
    "default_character_obstacle_mask",
    # SESSION-114 / P1-VFX-1A
    "MaskProjectionConfig",
    "FluidMaskProjector",
    "DynamicObstacleContext",
]
