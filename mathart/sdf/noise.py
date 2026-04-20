"""Procedural noise texture generators for pixel art.

Provides Perlin noise, Simplex noise, and multi-octave fractal noise (fBm)
for generating tileable textures such as terrain, clouds, lava, and water.

Each generator returns a normalized 2D numpy array in [0, 1] that can be
rendered to a PIL Image via ``render_noise_texture()``.

Mathematical foundations:
  - Perlin noise: gradient noise on integer lattice with fade curve
    t -> 6t^5 - 15t^4 + 10t^3 (improved Perlin, 2002)
  - Simplex noise: gradient noise on simplex lattice (Perlin 2001),
    fewer multiplications, no directional artifacts
  - fBm (fractional Brownian motion): sum of octaves with lacunarity
    and persistence: sum_{i=0}^{n-1} persistence^i * noise(p * lacunarity^i)

Texture presets map to MarioTrickster game elements:
  - terrain: low-frequency fBm for ground/platform surfaces
  - clouds: high-persistence fBm for background parallax layers
  - lava: warped fBm with domain distortion for animated lava
  - water: ridged noise for water caustics
  - stone: high-frequency fBm for brick/stone textures
  - magic: turbulence noise for magical effects
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np
from PIL import Image


# ── Permutation table ─────────────────────────────────────────────────────────

def _build_permutation(seed: int = 0) -> np.ndarray:
    """Build a 512-element permutation table for noise hashing.

    Uses np.random.default_rng (NEP-19) instead of legacy RandomState.
    """
    rng = np.random.default_rng(seed)
    p = np.arange(256, dtype=np.int32)
    rng.shuffle(p)
    return np.concatenate([p, p])  # Double for overflow-free indexing


# ── Perlin Noise 2D ──────────────────────────────────────────────────────────

def _fade(t: np.ndarray) -> np.ndarray:
    """Improved Perlin fade curve: 6t^5 - 15t^4 + 10t^3."""
    return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)


def _lerp(a: np.ndarray, b: np.ndarray, t: np.ndarray) -> np.ndarray:
    """Linear interpolation."""
    return a + t * (b - a)


def _grad2d(h: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Compute gradient dot product for 2D Perlin noise."""
    # Use lower 2 bits to select gradient direction
    mask = h & 3
    u = np.where(mask < 2, x, y)
    v = np.where(mask < 2, y, x)
    return np.where(mask & 1, -u, u) + np.where(mask & 2, -v, v)


def perlin_2d(
    width: int,
    height: int,
    scale: float = 8.0,
    offset_x: float = 0.0,
    offset_y: float = 0.0,
    seed: int = 0,
) -> np.ndarray:
    """Generate 2D Perlin noise.

    Parameters
    ----------
    width, height : int
        Output dimensions in pixels.
    scale : float
        Noise frequency (higher = more detail).
    offset_x, offset_y : float
        Spatial offset for tiling/animation.
    seed : int
        Random seed for the permutation table.

    Returns
    -------
    np.ndarray
        2D array of shape (height, width), values in [0, 1].
    """
    perm = _build_permutation(seed)

    xs = np.linspace(offset_x, offset_x + scale, width, endpoint=False)
    ys = np.linspace(offset_y, offset_y + scale, height, endpoint=False)
    X, Y = np.meshgrid(xs, ys)

    # Integer and fractional parts
    xi = X.astype(np.int32) & 255
    yi = Y.astype(np.int32) & 255
    xf = X - np.floor(X)
    yf = Y - np.floor(Y)

    # Fade curves
    u = _fade(xf)
    v = _fade(yf)

    # Hash corners
    aa = perm[perm[xi] + yi]
    ab = perm[perm[xi] + yi + 1]
    ba = perm[perm[xi + 1] + yi]
    bb = perm[perm[xi + 1] + yi + 1]

    # Gradient dot products and interpolation
    x1 = _lerp(_grad2d(aa, xf, yf), _grad2d(ba, xf - 1, yf), u)
    x2 = _lerp(_grad2d(ab, xf, yf - 1), _grad2d(bb, xf - 1, yf - 1), u)
    result = _lerp(x1, x2, v)

    # Normalize from [-1, 1] to [0, 1]
    return (result + 1.0) * 0.5


# ── Simplex Noise 2D ─────────────────────────────────────────────────────────

# Skew/unskew constants for 2D simplex
_F2 = 0.5 * (np.sqrt(3.0) - 1.0)
_G2 = (3.0 - np.sqrt(3.0)) / 6.0

# 2D gradient table (12 directions)
_GRAD2 = np.array([
    [1, 1], [-1, 1], [1, -1], [-1, -1],
    [1, 0], [-1, 0], [0, 1], [0, -1],
    [1, 1], [-1, 1], [1, -1], [-1, -1],
], dtype=np.float64)


def simplex_2d(
    width: int,
    height: int,
    scale: float = 8.0,
    offset_x: float = 0.0,
    offset_y: float = 0.0,
    seed: int = 0,
) -> np.ndarray:
    """Generate 2D Simplex noise.

    Parameters
    ----------
    width, height : int
        Output dimensions in pixels.
    scale : float
        Noise frequency.
    offset_x, offset_y : float
        Spatial offset.
    seed : int
        Random seed.

    Returns
    -------
    np.ndarray
        2D array of shape (height, width), values in [0, 1].
    """
    perm = _build_permutation(seed)

    xs = np.linspace(offset_x, offset_x + scale, width, endpoint=False)
    ys = np.linspace(offset_y, offset_y + scale, height, endpoint=False)
    X, Y = np.meshgrid(xs, ys)

    # Skew input space to determine simplex cell
    s = (X + Y) * _F2
    i = np.floor(X + s).astype(np.int32)
    j = np.floor(Y + s).astype(np.int32)

    t = (i + j) * _G2
    x0 = X - (i - t)
    y0 = Y - (j - t)

    # Determine which simplex we're in
    i1 = np.where(x0 > y0, 1, 0)
    j1 = np.where(x0 > y0, 0, 1)

    x1 = x0 - i1 + _G2
    y1 = y0 - j1 + _G2
    x2 = x0 - 1.0 + 2.0 * _G2
    y2 = y0 - 1.0 + 2.0 * _G2

    ii = i & 255
    jj = j & 255

    # Gradient indices
    gi0 = perm[ii + perm[jj]] % 12
    gi1 = perm[ii + i1 + perm[jj + j1]] % 12
    gi2 = perm[ii + 1 + perm[jj + 1]] % 12

    # Contribution from each corner
    def _corner(gx, gy, gi):
        t_val = 0.5 - gx * gx - gy * gy
        mask = t_val > 0
        t_val = np.where(mask, t_val, 0.0)
        t_val = t_val * t_val
        grad = _GRAD2[gi]
        dot = gx * grad[..., 0] + gy * grad[..., 1]
        return np.where(mask, t_val * t_val * dot, 0.0)

    n0 = _corner(x0, y0, gi0)
    n1 = _corner(x1, y1, gi1)
    n2 = _corner(x2, y2, gi2)

    # Scale to [0, 1]
    result = 70.0 * (n0 + n1 + n2)
    return (result + 1.0) * 0.5


# ── Fractal Brownian Motion (fBm) ────────────────────────────────────────────

def fbm(
    width: int,
    height: int,
    octaves: int = 6,
    lacunarity: float = 2.0,
    persistence: float = 0.5,
    scale: float = 8.0,
    noise_func: str = "perlin",
    offset_x: float = 0.0,
    offset_y: float = 0.0,
    seed: int = 0,
) -> np.ndarray:
    """Generate fractal Brownian motion (multi-octave noise).

    Parameters
    ----------
    width, height : int
        Output dimensions.
    octaves : int
        Number of noise layers (more = finer detail).
    lacunarity : float
        Frequency multiplier per octave (typically 2.0).
    persistence : float
        Amplitude multiplier per octave (typically 0.5).
    scale : float
        Base frequency.
    noise_func : str
        Base noise function: "perlin" or "simplex".
    offset_x, offset_y : float
        Spatial offset.
    seed : int
        Random seed.

    Returns
    -------
    np.ndarray
        2D array of shape (height, width), values in [0, 1].
    """
    noise_fn = perlin_2d if noise_func == "perlin" else simplex_2d

    result = np.zeros((height, width), dtype=np.float64)
    amplitude = 1.0
    frequency = 1.0
    max_amplitude = 0.0

    for i in range(octaves):
        noise = noise_fn(
            width, height,
            scale=scale * frequency,
            offset_x=offset_x + i * 31.7,
            offset_y=offset_y + i * 17.3,
            seed=seed + i,
        )
        # noise is in [0, 1], shift to [-1, 1] for proper fBm summation
        result += amplitude * (noise * 2.0 - 1.0)
        max_amplitude += amplitude
        amplitude *= persistence
        frequency *= lacunarity

    # Normalize back to [0, 1]
    result = (result / max_amplitude + 1.0) * 0.5
    return np.clip(result, 0.0, 1.0)


# ── Ridged Noise ──────────────────────────────────────────────────────────────

def ridged_noise(
    width: int,
    height: int,
    octaves: int = 6,
    lacunarity: float = 2.0,
    gain: float = 0.5,
    scale: float = 8.0,
    noise_func: str = "perlin",
    offset_x: float = 0.0,
    offset_y: float = 0.0,
    seed: int = 0,
) -> np.ndarray:
    """Generate ridged multi-fractal noise.

    Creates sharp ridges by taking abs(noise) and inverting. Useful for
    mountain terrain, water caustics, and crack patterns.

    Returns
    -------
    np.ndarray
        2D array of shape (height, width), values in [0, 1].
    """
    noise_fn = perlin_2d if noise_func == "perlin" else simplex_2d

    result = np.zeros((height, width), dtype=np.float64)
    amplitude = 1.0
    frequency = 1.0
    weight = 1.0

    for i in range(octaves):
        noise = noise_fn(
            width, height,
            scale=scale * frequency,
            offset_x=offset_x + i * 31.7,
            offset_y=offset_y + i * 17.3,
            seed=seed + i,
        )
        # Convert to [-1, 1], take abs, invert
        signal = 1.0 - np.abs(noise * 2.0 - 1.0)
        signal = signal * signal  # Sharpen ridges
        signal *= weight
        weight = np.clip(signal * gain, 0.0, 1.0)

        result += amplitude * signal
        frequency *= lacunarity
        amplitude *= gain

    return np.clip(result, 0.0, 1.0)


# ── Turbulence ────────────────────────────────────────────────────────────────

def turbulence(
    width: int,
    height: int,
    octaves: int = 6,
    lacunarity: float = 2.0,
    persistence: float = 0.5,
    scale: float = 8.0,
    noise_func: str = "perlin",
    offset_x: float = 0.0,
    offset_y: float = 0.0,
    seed: int = 0,
) -> np.ndarray:
    """Generate turbulence noise (absolute-value fBm).

    Creates billowy, cloud-like patterns. Useful for smoke, fire, and
    magical effects.

    Returns
    -------
    np.ndarray
        2D array of shape (height, width), values in [0, 1].
    """
    noise_fn = perlin_2d if noise_func == "perlin" else simplex_2d

    result = np.zeros((height, width), dtype=np.float64)
    amplitude = 1.0
    frequency = 1.0
    max_amplitude = 0.0

    for i in range(octaves):
        noise = noise_fn(
            width, height,
            scale=scale * frequency,
            offset_x=offset_x + i * 31.7,
            offset_y=offset_y + i * 17.3,
            seed=seed + i,
        )
        result += amplitude * np.abs(noise * 2.0 - 1.0)
        max_amplitude += amplitude
        amplitude *= persistence
        frequency *= lacunarity

    return np.clip(result / max_amplitude, 0.0, 1.0)


# ── Domain Warping ────────────────────────────────────────────────────────────

def domain_warp(
    width: int,
    height: int,
    warp_strength: float = 0.5,
    octaves: int = 4,
    scale: float = 8.0,
    noise_func: str = "perlin",
    seed: int = 0,
) -> np.ndarray:
    """Generate domain-warped noise.

    Feeds noise into itself to create organic, flowing distortions.
    Excellent for lava, marble, and alien textures.

    Returns
    -------
    np.ndarray
        2D array of shape (height, width), values in [0, 1].
    """
    # First pass: generate warp offsets
    warp_x = fbm(width, height, octaves=octaves, scale=scale,
                  noise_func=noise_func, seed=seed)
    warp_y = fbm(width, height, octaves=octaves, scale=scale,
                  noise_func=noise_func, seed=seed + 100)

    # Blend warped with original for more complexity
    base = fbm(width, height, octaves=octaves, scale=scale,
               noise_func=noise_func, seed=seed + 300)

    # Per-pixel warp using the offset maps
    xs = np.linspace(0, scale, width, endpoint=False)
    ys = np.linspace(0, scale, height, endpoint=False)
    X, Y = np.meshgrid(xs, ys)

    warped_x = X + warp_strength * (warp_x * 2 - 1) * scale
    warped_y = Y + warp_strength * (warp_y * 2 - 1) * scale

    # Use the mean warped offset for the final pass
    final = fbm(
        width, height,
        octaves=octaves,
        scale=scale,
        offset_x=float(np.mean(warped_x - X)),
        offset_y=float(np.mean(warped_y - Y)),
        noise_func=noise_func,
        seed=seed + 400,
    )

    return np.clip(0.6 * final + 0.4 * base, 0.0, 1.0)


# ── Texture Presets ───────────────────────────────────────────────────────────

@dataclass
class TexturePreset:
    """A named texture configuration."""
    name: str
    description: str
    generator: str           # Function name: fbm, ridged_noise, turbulence, domain_warp
    params: dict             # Keyword arguments for the generator
    colormap: str = "gray"   # Default colormap name


# Built-in presets for MarioTrickster game elements
TEXTURE_PRESETS: dict[str, TexturePreset] = {
    "terrain": TexturePreset(
        name="terrain",
        description="Low-frequency ground/platform surface texture",
        generator="fbm",
        params={"octaves": 4, "lacunarity": 2.0, "persistence": 0.45, "scale": 6.0},
        colormap="earth",
    ),
    "clouds": TexturePreset(
        name="clouds",
        description="Soft cloud texture for background parallax layers",
        generator="fbm",
        params={"octaves": 5, "lacunarity": 2.2, "persistence": 0.6, "scale": 10.0},
        colormap="sky",
    ),
    "lava": TexturePreset(
        name="lava",
        description="Warped flowing lava texture with domain distortion",
        generator="domain_warp",
        params={"warp_strength": 0.6, "octaves": 5, "scale": 8.0},
        colormap="lava",
    ),
    "water": TexturePreset(
        name="water",
        description="Ridged caustic pattern for water surfaces",
        generator="ridged_noise",
        params={"octaves": 5, "lacunarity": 2.0, "gain": 0.5, "scale": 8.0},
        colormap="water",
    ),
    "stone": TexturePreset(
        name="stone",
        description="High-frequency stone/brick texture",
        generator="fbm",
        params={"octaves": 6, "lacunarity": 2.5, "persistence": 0.4, "scale": 12.0},
        colormap="stone",
    ),
    "magic": TexturePreset(
        name="magic",
        description="Turbulence-based magical energy texture",
        generator="turbulence",
        params={"octaves": 5, "lacunarity": 2.0, "persistence": 0.55, "scale": 8.0},
        colormap="magic",
    ),
}


# ── Colormaps ─────────────────────────────────────────────────────────────────

def _colormap_gray(t: np.ndarray) -> np.ndarray:
    """Grayscale colormap."""
    v = (t * 255).astype(np.uint8)
    return np.stack([v, v, v], axis=-1)


def _colormap_earth(t: np.ndarray) -> np.ndarray:
    """Earth tones: dark brown → tan → green."""
    r = np.clip((80 + t * 120), 0, 255).astype(np.uint8)
    g = np.clip((50 + t * 100), 0, 255).astype(np.uint8)
    b = np.clip((30 + t * 40), 0, 255).astype(np.uint8)
    return np.stack([r, g, b], axis=-1)


def _colormap_sky(t: np.ndarray) -> np.ndarray:
    """Sky: deep blue → light blue → white."""
    r = np.clip((100 + t * 155), 0, 255).astype(np.uint8)
    g = np.clip((140 + t * 115), 0, 255).astype(np.uint8)
    b = np.clip((200 + t * 55), 0, 255).astype(np.uint8)
    return np.stack([r, g, b], axis=-1)


def _colormap_lava(t: np.ndarray) -> np.ndarray:
    """Lava: black → dark red → orange → yellow."""
    r = np.clip((t * 2 * 255), 0, 255).astype(np.uint8)
    g = np.clip(((t - 0.3) * 2.5 * 255), 0, 255).astype(np.uint8)
    b = np.clip(((t - 0.7) * 3.0 * 200), 0, 255).astype(np.uint8)
    return np.stack([r, g, b], axis=-1)


def _colormap_water(t: np.ndarray) -> np.ndarray:
    """Water: deep blue → teal → light cyan."""
    r = np.clip((20 + t * 80), 0, 255).astype(np.uint8)
    g = np.clip((40 + t * 160), 0, 255).astype(np.uint8)
    b = np.clip((120 + t * 135), 0, 255).astype(np.uint8)
    return np.stack([r, g, b], axis=-1)


def _colormap_stone(t: np.ndarray) -> np.ndarray:
    """Stone: dark gray → medium gray → light gray."""
    v = np.clip((60 + t * 140), 0, 255).astype(np.uint8)
    r = v
    g = np.clip(v.astype(np.int16) - 5, 0, 255).astype(np.uint8)
    b = np.clip(v.astype(np.int16) - 10, 0, 255).astype(np.uint8)
    return np.stack([r, g, b], axis=-1)


def _colormap_magic(t: np.ndarray) -> np.ndarray:
    """Magic: deep purple → magenta → bright pink → white."""
    r = np.clip((80 + t * 175), 0, 255).astype(np.uint8)
    g = np.clip((20 + t * 100), 0, 255).astype(np.uint8)
    b = np.clip((160 + t * 95), 0, 255).astype(np.uint8)
    return np.stack([r, g, b], axis=-1)


COLORMAPS: dict[str, Callable] = {
    "gray": _colormap_gray,
    "earth": _colormap_earth,
    "sky": _colormap_sky,
    "lava": _colormap_lava,
    "water": _colormap_water,
    "stone": _colormap_stone,
    "magic": _colormap_magic,
}


# ── Rendering ─────────────────────────────────────────────────────────────────

def render_noise_texture(
    noise_array: np.ndarray,
    colormap: str = "gray",
    palette: Optional[list[tuple[int, int, int]]] = None,
    transparent_below: Optional[float] = None,
) -> Image.Image:
    """Render a noise array to a PIL Image.

    Parameters
    ----------
    noise_array : np.ndarray
        2D array with values in [0, 1].
    colormap : str
        Colormap name (gray, earth, sky, lava, water, stone, magic).
    palette : list of (R, G, B), optional
        If provided, quantize to this palette instead of using colormap.
    transparent_below : float, optional
        Make pixels with noise value below this threshold transparent.

    Returns
    -------
    PIL.Image.Image
        RGBA image.
    """
    h, w = noise_array.shape

    if palette:
        # Quantize to palette
        n_colors = len(palette)
        indices = np.clip((noise_array * n_colors).astype(int), 0, n_colors - 1)
        pal_arr = np.array(palette, dtype=np.uint8)
        rgb = pal_arr[indices]
    else:
        cmap_fn = COLORMAPS.get(colormap, _colormap_gray)
        rgb = cmap_fn(noise_array)

    # Build RGBA
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    rgba[:, :, :3] = rgb
    rgba[:, :, 3] = 255

    if transparent_below is not None:
        rgba[noise_array < transparent_below, 3] = 0

    return Image.fromarray(rgba, "RGBA")


def generate_texture(
    preset: str = "terrain",
    width: int = 64,
    height: int = 64,
    seed: int = 42,
    colormap: Optional[str] = None,
    palette: Optional[list[tuple[int, int, int]]] = None,
    **override_params,
) -> Image.Image:
    """Generate a texture from a named preset.

    Parameters
    ----------
    preset : str
        Preset name (terrain, clouds, lava, water, stone, magic).
    width, height : int
        Output size in pixels.
    seed : int
        Random seed.
    colormap : str, optional
        Override the preset's default colormap.
    palette : list, optional
        Quantize to this palette instead of using colormap.
    **override_params
        Override any preset parameter.

    Returns
    -------
    PIL.Image.Image
        RGBA texture image.
    """
    if preset not in TEXTURE_PRESETS:
        available = ", ".join(TEXTURE_PRESETS.keys())
        raise ValueError(f"Unknown preset '{preset}'. Available: {available}")

    tp = TEXTURE_PRESETS[preset]
    params = {**tp.params, **override_params, "seed": seed}

    # Select generator
    generators = {
        "fbm": fbm,
        "ridged_noise": ridged_noise,
        "turbulence": turbulence,
        "domain_warp": domain_warp,
    }
    gen_fn = generators[tp.generator]
    noise_array = gen_fn(width, height, **params)

    cmap = colormap or tp.colormap
    return render_noise_texture(noise_array, colormap=cmap, palette=palette)
