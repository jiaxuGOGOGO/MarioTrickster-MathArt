"""Tensorized Gray-Scott Reaction-Diffusion solver with PBR derivation.

SESSION-119 (P1-NEW-2) — closes the long-standing gap for an organic
reaction-diffusion texture engine.  The implementation is grounded in:

1. **Karl Sims — Reaction-Diffusion Tutorial** (https://www.karlsims.com/rd.html)
   The canonical 3×3 Laplacian stencil ``[[.05,.2,.05],[.2,-1,.2],[.05,.2,.05]]``,
   the stability-respecting defaults (``D_u=1.0``, ``D_v=0.5``, ``Δt=1.0``),
   and the named patterns (Coral ``f=.0545,k=.062`` and Mitosis
   ``f=.0367,k=.0649``) are lifted verbatim from that page.
2. **Pearson's Extended Classification** (http://www.mrob.com/pub/comp/xmorphia/)
   The extra locked-in parameter presets (MAZE, SPOTS, ALIEN_SKIN, FLOW) are
   chosen from the peer-reviewed Pearson parameter map.
3. **Data-Oriented Tensorized Laplacian** — every PDE update is a call into
   ``scipy.ndimage.convolve(..., mode='wrap')`` (a single C/OpenMP call),
   with **zero** Python per-pixel loops.  Periodic boundary conditions make
   the resulting texture seamless, satisfying the 3A material-pipeline
   "no visible seam" bar.
4. **Procedural PBR Material Derivation** — the ``V`` concentration field is
   reinterpreted as a height map and pushed through Sobel operators (also
   ``mode='wrap'``) to produce a strictly-normalized tangent-space normal
   map, a palette-interpolated albedo, and a threshold mask.

Red-Line Guards (enforced by tests in ``tests/test_reaction_diffusion.py``):

- **Anti-scalar-loop**: the hot path uses ``scipy.ndimage.convolve`` only.
- **Anti-divergence / CFL**: Δt is clamped to the stability budget
  ``Δt ≤ 1 / (2 · max(D_u, D_v))`` and ``np.clip`` bounds U, V to [0, 1]
  every step.
- **Anti-boundary-artifact**: Laplacian and Sobel operators run with
  ``mode='wrap'`` so the texture tiles seamlessly.
- **Future-proof**: ``GrayScottSolverConfig.advection_field`` is the
  reserved hook for ``P1-RESEARCH-30C`` thermodynamic advection coupling.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

import numpy as np
from scipy.ndimage import convolve

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Canonical stencils
# ---------------------------------------------------------------------------

#: The 3×3 Gray-Scott Laplacian kernel from Karl Sims' tutorial.
#: Centre −1, 4 edge neighbours 0.2, 4 corner neighbours 0.05.  Chosen so the
#: four cardinal weights dominate but diagonals still bleed, matching Karl's
#: reference formula.  The kernel sums to 0.0 (as any discrete Laplacian must).
GRAY_SCOTT_LAPLACIAN_KERNEL: np.ndarray = np.array(
    [
        [0.05, 0.20, 0.05],
        [0.20, -1.00, 0.20],
        [0.05, 0.20, 0.05],
    ],
    dtype=np.float64,
)

#: Normalized Sobel X operator (horizontal gradient).
SOBEL_X: np.ndarray = (
    np.array([[-1.0, 0.0, 1.0], [-2.0, 0.0, 2.0], [-1.0, 0.0, 1.0]], dtype=np.float64)
    / 8.0
)

#: Normalized Sobel Y operator (vertical gradient).
SOBEL_Y: np.ndarray = (
    np.array([[-1.0, -2.0, -1.0], [0.0, 0.0, 0.0], [1.0, 2.0, 1.0]], dtype=np.float64)
    / 8.0
)


# ---------------------------------------------------------------------------
# Pearson / Karl Sims preset library
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GrayScottPreset:
    """Immutable parameter pack selecting a named reaction-diffusion regime.

    Parameters
    ----------
    name : str
        Upper-case identifier, used as preset registry key.
    feed : float
        Feed rate ``f`` in the Gray-Scott system (≈ 0.01 .. 0.10).
    kill : float
        Kill rate ``k`` in the Gray-Scott system (≈ 0.04 .. 0.07).
    diffusion_u : float
        Diffusion coefficient for the substrate chemical ``U`` (Karl's A).
    diffusion_v : float
        Diffusion coefficient for the autocatalyst ``V`` (Karl's B).
    palette_low : tuple[float, float, float]
        Linear-space RGB colour (0..1) used when ``V`` concentration is low.
    palette_high : tuple[float, float, float]
        Linear-space RGB colour (0..1) used when ``V`` concentration is high.
    description : str
        Human-readable art direction.
    pearson_class : str
        Pearson class letter (α, β, … μ, σ) for provenance audits.
    """

    name: str
    feed: float
    kill: float
    diffusion_u: float = 1.0
    diffusion_v: float = 0.5
    palette_low: tuple[float, float, float] = (0.05, 0.05, 0.08)
    palette_high: tuple[float, float, float] = (0.95, 0.92, 0.85)
    description: str = ""
    pearson_class: str = ""


GRAY_SCOTT_PRESETS: Mapping[str, GrayScottPreset] = {
    "CORAL": GrayScottPreset(
        name="CORAL",
        feed=0.0545,
        kill=0.0620,
        palette_low=(0.10, 0.03, 0.04),
        palette_high=(0.95, 0.60, 0.50),
        description="Karl Sims 'coral growth' preset — branching, embossed reef texture.",
        pearson_class="θ/κ",
    ),
    "MITOSIS": GrayScottPreset(
        name="MITOSIS",
        feed=0.0367,
        kill=0.0649,
        palette_low=(0.02, 0.04, 0.02),
        palette_high=(0.55, 0.85, 0.40),
        description="Karl Sims 'mitosis' preset — spots that split like dividing cells.",
        pearson_class="ζ",
    ),
    "MAZE": GrayScottPreset(
        name="MAZE",
        feed=0.0290,
        kill=0.0570,
        palette_low=(0.02, 0.02, 0.05),
        palette_high=(0.70, 0.80, 0.95),
        description="Labyrinthine stable stripes — Pearson class λ — ideal for circuit textures.",
        pearson_class="λ",
    ),
    "SPOTS": GrayScottPreset(
        name="SPOTS",
        feed=0.0500,
        kill=0.0620,
        palette_low=(0.04, 0.03, 0.02),
        palette_high=(0.90, 0.80, 0.35),
        description="Hexagonal spots — Pearson class κ — classic Turing dot pattern.",
        pearson_class="κ",
    ),
    "ALIEN_SKIN": GrayScottPreset(
        name="ALIEN_SKIN",
        feed=0.0780,
        kill=0.0610,
        palette_low=(0.10, 0.02, 0.10),
        palette_high=(0.80, 0.30, 0.90),
        description="Breathing holes in a dense substrate — Pearson μ/ν border.",
        pearson_class="μ",
    ),
    "FLOW": GrayScottPreset(
        name="FLOW",
        feed=0.0820,
        kill=0.0600,
        palette_low=(0.02, 0.08, 0.10),
        palette_high=(0.70, 0.95, 0.95),
        description="Rolling fronts — base regime for P1-RESEARCH-30C advection coupling.",
        pearson_class="ν",
    ),
}


def list_preset_names() -> tuple[str, ...]:
    """Return the canonical list of Gray-Scott preset names."""
    return tuple(GRAY_SCOTT_PRESETS.keys())


def get_preset(name: str) -> GrayScottPreset:
    """Resolve a preset by case-insensitive name, raising ``KeyError`` if unknown."""
    key = str(name).strip().upper()
    if key not in GRAY_SCOTT_PRESETS:
        raise KeyError(
            f"Unknown Gray-Scott preset {name!r}. "
            f"Available: {sorted(GRAY_SCOTT_PRESETS.keys())}"
        )
    return GRAY_SCOTT_PRESETS[key]


# ---------------------------------------------------------------------------
# Solver configuration & state
# ---------------------------------------------------------------------------

@dataclass
class GrayScottSolverConfig:
    """Configuration for a single Gray-Scott simulation run.

    Attributes
    ----------
    width, height : int
        Grid size (must both be ≥ 8 to avoid degenerate wrap behaviour).
    feed, kill : float
        Gray-Scott parameters (see ``GrayScottPreset``).
    diffusion_u, diffusion_v : float
        Diffusion coefficients for U and V respectively.
    dt : float
        Euler integration step.  Automatically clamped to the CFL budget
        ``dt_max = 1.0 / (2.0 * max(diffusion_u, diffusion_v))``.
    steps : int
        Number of iterations to run per ``solver.run()`` call.
    seed : int
        Deterministic seed for the initial-condition perturbation.
    seed_patch_fraction : float
        Linear fraction of the grid used for the central V = 1 seed patch.
    seed_noise_amplitude : float
        Amplitude of uniform noise sprinkled into U and V before stepping.
    advection_field : np.ndarray | None
        Reserved hook for ``P1-RESEARCH-30C`` thermodynamic coupling.  When
        not ``None`` the shape must be ``(2, height, width)``.  Currently
        validated only; advection integration will be wired in a follow-up
        session via ``solver.set_advection`` (see docstring).
    advection_scheme : str
        One of ``{"none", "upwind", "semi_lagrangian"}``.  Preserved in the
        config for provenance even when advection is disabled.
    """

    width: int = 128
    height: int = 128
    feed: float = 0.0545
    kill: float = 0.0620
    diffusion_u: float = 1.0
    diffusion_v: float = 0.5
    dt: float = 1.0
    steps: int = 2000
    seed: int = 2026
    seed_patch_fraction: float = 0.08
    seed_noise_amplitude: float = 0.05
    advection_field: np.ndarray | None = None
    advection_scheme: str = "none"

    def cfl_limit(self) -> float:
        """Largest stable Δt for explicit Forward-Euler + Karl's stencil."""
        return 1.0 / (2.0 * max(self.diffusion_u, self.diffusion_v, 1e-9))

    def effective_dt(self) -> float:
        """Return dt clamped to the CFL stability budget."""
        return float(min(self.dt, self.cfl_limit()))

    def validate(self) -> list[str]:
        """Sanity-check configuration, returning a list of human warnings."""
        warnings: list[str] = []
        if self.width < 8 or self.height < 8:
            warnings.append(
                f"grid {self.width}×{self.height} is below the recommended "
                f"minimum 8×8; wrap convolutions may alias"
            )
        if not (0.0 <= self.feed <= 0.15):
            warnings.append(f"feed={self.feed} is outside the canonical [0, 0.15] range")
        if not (0.0 <= self.kill <= 0.10):
            warnings.append(f"kill={self.kill} is outside the canonical [0, 0.10] range")
        if self.dt > self.cfl_limit():
            warnings.append(
                f"dt={self.dt} exceeds CFL limit {self.cfl_limit():.4f}; "
                f"will be clamped."
            )
        if self.advection_field is not None:
            exp = (2, self.height, self.width)
            if tuple(self.advection_field.shape) != exp:
                raise ValueError(
                    f"advection_field must have shape {exp}, got "
                    f"{tuple(self.advection_field.shape)}"
                )
            if self.advection_scheme not in {"none", "upwind", "semi_lagrangian"}:
                warnings.append(
                    f"advection_scheme={self.advection_scheme!r} not recognised; "
                    f"defaulting to 'semi_lagrangian' in a future session."
                )
        return warnings


@dataclass
class GrayScottState:
    """Instantaneous state of a Gray-Scott simulation."""

    u: np.ndarray
    v: np.ndarray
    step: int = 0

    def copy(self) -> "GrayScottState":
        return GrayScottState(u=self.u.copy(), v=self.v.copy(), step=self.step)


# ---------------------------------------------------------------------------
# Tensorized solver
# ---------------------------------------------------------------------------

class GrayScottSolver:
    """Pure-NumPy/SciPy tensorized Gray-Scott integrator.

    The solver is deliberately a thin, stateless-per-step class so it can be
    composed by the ``ReactionDiffusionBackend`` plugin, by future
    ``advection`` coupling layers (``P1-RESEARCH-30C``), and by ad-hoc
    research scripts.

    Usage::

        solver = GrayScottSolver(GrayScottSolverConfig(width=256, height=256))
        state = solver.initialise()
        final = solver.run(state)      # returns a new GrayScottState

    Notes
    -----
    The hot path is a single pair of calls to
    ``scipy.ndimage.convolve(..., mode='wrap')``.  There are **no Python
    per-pixel loops** anywhere in the solver — the outer iteration counter
    over time steps is the only ``for`` statement in the module and it does
    not touch individual pixels.
    """

    def __init__(self, config: GrayScottSolverConfig | None = None) -> None:
        self.config = config or GrayScottSolverConfig()
        warnings = self.config.validate()
        for w in warnings:
            logger.warning("[gray_scott] %s", w)

    # ---------- Initial condition ----------
    def initialise(
        self,
        *,
        seed_positions: np.ndarray | None = None,
    ) -> GrayScottState:
        """Return a fresh ``GrayScottState`` with U=1, V=0 plus a seed patch.

        Parameters
        ----------
        seed_positions : np.ndarray | None
            Optional integer array of shape ``(N, 2)`` giving ``(row, col)``
            seed centres.  When ``None`` a single central square seed patch
            is sprinkled, following Karl Sims' reference implementation.
        """
        cfg = self.config
        rng = np.random.default_rng(cfg.seed)
        u = np.ones((cfg.height, cfg.width), dtype=np.float64)
        v = np.zeros((cfg.height, cfg.width), dtype=np.float64)

        patch = max(2, int(min(cfg.width, cfg.height) * cfg.seed_patch_fraction))
        if seed_positions is None:
            ch = cfg.height // 2
            cw = cfg.width // 2
            r0 = max(0, ch - patch // 2)
            r1 = min(cfg.height, r0 + patch)
            c0 = max(0, cw - patch // 2)
            c1 = min(cfg.width, c0 + patch)
            v[r0:r1, c0:c1] = 1.0
            u[r0:r1, c0:c1] = 0.0
        else:
            positions = np.asarray(seed_positions, dtype=np.int64)
            if positions.ndim != 2 or positions.shape[1] != 2:
                raise ValueError("seed_positions must have shape (N, 2)")
            for row, col in positions:  # N is tiny — O(seeds), not O(pixels)
                r0 = int(max(0, row - patch // 2))
                r1 = int(min(cfg.height, r0 + patch))
                c0 = int(max(0, col - patch // 2))
                c1 = int(min(cfg.width, c0 + patch))
                v[r0:r1, c0:c1] = 1.0
                u[r0:r1, c0:c1] = 0.0

        if cfg.seed_noise_amplitude > 0.0:
            noise = rng.uniform(
                -cfg.seed_noise_amplitude,
                cfg.seed_noise_amplitude,
                size=u.shape,
            )
            u = np.clip(u + noise, 0.0, 1.0)
            v = np.clip(v + rng.uniform(
                -cfg.seed_noise_amplitude,
                cfg.seed_noise_amplitude,
                size=v.shape,
            ), 0.0, 1.0)

        return GrayScottState(u=u, v=v, step=0)

    # ---------- Single-step update ----------
    def step(self, state: GrayScottState) -> GrayScottState:
        """Advance the state by exactly one Euler step (tensorized)."""
        cfg = self.config
        dt = cfg.effective_dt()

        lap_u = convolve(state.u, GRAY_SCOTT_LAPLACIAN_KERNEL, mode="wrap")
        lap_v = convolve(state.v, GRAY_SCOTT_LAPLACIAN_KERNEL, mode="wrap")

        uvv = state.u * state.v * state.v
        du = cfg.diffusion_u * lap_u - uvv + cfg.feed * (1.0 - state.u)
        dv = cfg.diffusion_v * lap_v + uvv - (cfg.feed + cfg.kill) * state.v

        new_u = np.clip(state.u + dt * du, 0.0, 1.0)
        new_v = np.clip(state.v + dt * dv, 0.0, 1.0)

        return GrayScottState(u=new_u, v=new_v, step=state.step + 1)

    # ---------- Multi-step run ----------
    def run(
        self,
        state: GrayScottState | None = None,
        *,
        steps: int | None = None,
        on_step: Callable[[int, GrayScottState], None] | None = None,
    ) -> GrayScottState:
        """Iterate ``steps`` Euler updates and return the final state."""
        cfg = self.config
        current = state.copy() if state is not None else self.initialise()
        total = int(steps if steps is not None else cfg.steps)
        if total <= 0:
            return current
        for _ in range(total):
            current = self.step(current)
            if on_step is not None:
                on_step(current.step, current)
        return current


# ---------------------------------------------------------------------------
# PBR derivation (height → normal/albedo/mask)
# ---------------------------------------------------------------------------

@dataclass
class PBRDerivationResult:
    """Container for PBR channels derived from a concentration field."""

    height: np.ndarray           # (H, W) float32 ∈ [0, 1]
    normal_rgb: np.ndarray       # (H, W, 3) float32 ∈ [0, 1]
    normal_vec: np.ndarray       # (H, W, 3) float32, unit-length
    albedo_rgb: np.ndarray       # (H, W, 3) float32 ∈ [0, 1]
    mask: np.ndarray             # (H, W) float32 ∈ [0, 1]
    metadata: dict[str, Any] = field(default_factory=dict)


def derive_pbr_from_concentration(
    v: np.ndarray,
    *,
    preset: GrayScottPreset | None = None,
    normal_strength: float = 6.0,
    mask_threshold: float = 0.25,
) -> PBRDerivationResult:
    """Derive PBR channels from a Gray-Scott ``V`` concentration field.

    Vectorized pipeline:

    1. Treat ``V`` as a height map h.
    2. Compute ``∂h/∂x``, ``∂h/∂y`` with Sobel kernels + ``mode='wrap'``.
    3. Form tangent-space normal ``N = normalize(-sx·gx, -sy·gy, 1)``.
    4. Encode ``(N + 1) / 2`` as RGB normal map.
    5. Linearly interpolate albedo along ``V`` between
       ``preset.palette_low`` and ``preset.palette_high``.
    6. Threshold ``V`` to produce a soft mask.

    All operations are matrix ops — zero Python per-pixel loops.
    """
    if v.ndim != 2:
        raise ValueError(f"V field must be 2-D, got shape {v.shape}")
    preset = preset or GRAY_SCOTT_PRESETS["CORAL"]

    h = np.clip(v.astype(np.float64), 0.0, 1.0)
    gx = convolve(h, SOBEL_X, mode="wrap")
    gy = convolve(h, SOBEL_Y, mode="wrap")

    # Tangent-space normal assembly.
    nx = -normal_strength * gx
    ny = -normal_strength * gy
    nz = np.ones_like(h)
    mag = np.sqrt(nx * nx + ny * ny + nz * nz)
    # Guard against zero-magnitude (can't happen because nz≥1, but be safe).
    mag = np.maximum(mag, 1e-12)
    nx /= mag
    ny /= mag
    nz /= mag
    normal_vec = np.stack([nx, ny, nz], axis=-1).astype(np.float32)

    normal_rgb = (0.5 * (normal_vec + 1.0)).astype(np.float32)
    normal_rgb = np.clip(normal_rgb, 0.0, 1.0)

    # Albedo = lerp(palette_low, palette_high, h).  Fully vectorized.
    low = np.asarray(preset.palette_low, dtype=np.float32)
    high = np.asarray(preset.palette_high, dtype=np.float32)
    albedo_rgb = (low[None, None, :] * (1.0 - h[..., None])
                  + high[None, None, :] * h[..., None]).astype(np.float32)
    albedo_rgb = np.clip(albedo_rgb, 0.0, 1.0)

    # Soft mask — smoothstep around threshold.
    t = np.clip((h - mask_threshold) / max(1e-6, 1.0 - mask_threshold), 0.0, 1.0)
    mask = (t * t * (3.0 - 2.0 * t)).astype(np.float32)

    return PBRDerivationResult(
        height=h.astype(np.float32),
        normal_rgb=normal_rgb,
        normal_vec=normal_vec,
        albedo_rgb=albedo_rgb,
        mask=mask,
        metadata={
            "preset_name": preset.name,
            "pearson_class": preset.pearson_class,
            "feed": preset.feed,
            "kill": preset.kill,
            "normal_strength": float(normal_strength),
            "mask_threshold": float(mask_threshold),
            "palette_low": tuple(preset.palette_low),
            "palette_high": tuple(preset.palette_high),
        },
    )


# ---------------------------------------------------------------------------
# Channel encoders (float → uint8 images)
# ---------------------------------------------------------------------------

def encode_height_map(height: np.ndarray) -> np.ndarray:
    """Encode a [0,1] height map to an 8-bit grayscale image array."""
    return np.clip(height * 255.0, 0.0, 255.0).astype(np.uint8)


def encode_normal_map(normal_rgb: np.ndarray) -> np.ndarray:
    """Encode a [0,1] tangent-space normal map to 8-bit RGB."""
    return np.clip(normal_rgb * 255.0, 0.0, 255.0).astype(np.uint8)


def encode_albedo_map(albedo_rgb: np.ndarray) -> np.ndarray:
    """Encode a [0,1] albedo map to 8-bit RGB."""
    return np.clip(albedo_rgb * 255.0, 0.0, 255.0).astype(np.uint8)


def encode_mask_map(mask: np.ndarray) -> np.ndarray:
    """Encode a [0,1] soft mask to 8-bit grayscale."""
    return np.clip(mask * 255.0, 0.0, 255.0).astype(np.uint8)


__all__ = [
    "GRAY_SCOTT_LAPLACIAN_KERNEL",
    "SOBEL_X",
    "SOBEL_Y",
    "GrayScottPreset",
    "GrayScottSolver",
    "GrayScottSolverConfig",
    "GrayScottState",
    "PBRDerivationResult",
    "GRAY_SCOTT_PRESETS",
    "derive_pbr_from_concentration",
    "encode_albedo_map",
    "encode_height_map",
    "encode_mask_map",
    "encode_normal_map",
    "get_preset",
    "list_preset_names",
]
