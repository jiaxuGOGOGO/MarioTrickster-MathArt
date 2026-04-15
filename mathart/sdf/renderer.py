"""
SDF → Pixel Art renderer with professional pixel art techniques.

Renders SDFs to pixel-perfect sprites with:
  - Multi-band lighting (highlight / midtone / shadow / deep shadow)
  - Hue shifting (warm highlights, cool shadows — pixel art standard)
  - Ordered dithering (Bayer matrix) for smooth color transitions
  - SDF-based ambient occlusion for depth
  - Normal-map derived lighting from SDF gradients
  - Outline with variable thickness
  - Texture mapping from noise onto SDF surfaces

Compatible with Unity:
  - PPU = 32 (from TA_AssetValidator)
  - Point filtering (no anti-aliasing at final output)
  - Transparent background (RGBA)

Mathematical foundations:
  - SDF gradient → surface normal: n = normalize(grad SDF)
  - Lambertian lighting: I = max(0, dot(n, L))
  - Hue shifting: highlights → +15 deg hue, shadows → -15 deg hue (Lospec standard)
  - Bayer dithering: threshold[x,y] = M[x%n, y%n] / n^2 - 0.5
  - AO from SDF: ao = 1 - clamp(sum(max(0, expected - actual) / expected), 0, 1)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional
import numpy as np
from PIL import Image

from ..oklab.palette import Palette
from ..oklab.color_space import (
    oklab_to_srgb,
    srgb_to_oklab,
    oklab_to_oklch,
    oklch_to_oklab,
)

SDFFunc = Callable[[np.ndarray, np.ndarray], np.ndarray]


# ── Bayer Dithering Matrices ──────────────────────────────────────────────────

def _bayer_matrix(n: int) -> np.ndarray:
    """Generate n x n Bayer ordered dithering matrix (n must be power of 2).

    The Bayer matrix B_n is defined recursively:
      B_1 = [[0]]
      B_{2n} = [[4*B_n, 4*B_n+2], [4*B_n+3, 4*B_n+1]]

    Normalized to [0, 1) range.
    """
    if n == 1:
        return np.array([[0.0]])
    half = _bayer_matrix(n // 2)
    m = np.zeros((n, n))
    m[: n // 2, : n // 2] = 4 * half
    m[: n // 2, n // 2 :] = 4 * half + 2
    m[n // 2 :, : n // 2] = 4 * half + 3
    m[n // 2 :, n // 2 :] = 4 * half + 1
    return m / (n * n)


BAYER_2 = _bayer_matrix(2)
BAYER_4 = _bayer_matrix(4)
BAYER_8 = _bayer_matrix(8)


def _apply_ordered_dither(
    value: np.ndarray, levels: int, bayer: np.ndarray
) -> np.ndarray:
    """Apply ordered dithering to a value array.

    Args:
        value: Array of values in [0, 1].
        levels: Number of output levels.
        bayer: Bayer matrix to use.

    Returns:
        Dithered values quantized to ``levels`` levels.
    """
    h, w = value.shape
    bn = bayer.shape[0]
    threshold = np.tile(bayer, (h // bn + 1, w // bn + 1))[:h, :w]
    scaled = value * (levels - 1) + (threshold - 0.5)
    quantized = np.clip(np.round(scaled), 0, levels - 1).astype(int)
    return quantized


# ── SDF Normal Computation ────────────────────────────────────────────────────

def _compute_sdf_normals(
    sdf_func: SDFFunc, X: np.ndarray, Y: np.ndarray, eps: float = 0.01
) -> tuple[np.ndarray, np.ndarray]:
    """Compute surface normals from SDF using central differences.

    Normal = normalize(grad SDF) = normalize(dSDF/dx, dSDF/dy)
    """
    dx = sdf_func(X + eps, Y) - sdf_func(X - eps, Y)
    dy = sdf_func(X, Y + eps) - sdf_func(X, Y - eps)
    length = np.sqrt(dx ** 2 + dy ** 2) + 1e-8
    return dx / length, dy / length


def _compute_sdf_ao(
    sdf_func: SDFFunc,
    X: np.ndarray,
    Y: np.ndarray,
    nx: np.ndarray,
    ny: np.ndarray,
    steps: int = 5,
    step_size: float = 0.02,
) -> np.ndarray:
    """Compute ambient occlusion from SDF (Inigo Quilez technique).

    AO = 1 - sum_i(max(0, expected_i - actual_i) / expected_i) / steps
    """
    ao = np.zeros_like(X)
    for i in range(1, steps + 1):
        expected = i * step_size
        actual = sdf_func(X + nx * expected, Y + ny * expected)
        ao += np.maximum(0.0, expected - actual) / expected
    return 1.0 - np.clip(ao / steps, 0, 1)


# ── Hue Shifting ──────────────────────────────────────────────────────────────

def _hue_shift_color(
    base_oklab: np.ndarray,
    shift_degrees: float,
    lightness_delta: float,
    chroma_scale: float,
) -> np.ndarray:
    """Shift a color in OKLCH space (hue shifting for pixel art).

    Pixel art standard:
      - Highlights: shift hue toward warm (+15 deg), increase lightness
      - Shadows: shift hue toward cool (-15 deg), decrease lightness
    """
    lch = oklab_to_oklch(base_oklab)
    if lch.ndim == 1:
        lch = lch[np.newaxis, :]
    result = lch.copy()
    result[:, 0] = np.clip(result[:, 0] + lightness_delta, 0, 1)
    result[:, 1] = np.clip(result[:, 1] * chroma_scale, 0, 0.4)
    result[:, 2] = result[:, 2] + np.radians(shift_degrees)
    return oklch_to_oklab(result)


def _generate_color_ramp(
    base_srgb: tuple, levels: int = 5
) -> list[tuple[int, ...]]:
    """Generate a pixel-art color ramp with hue shifting.

    Creates: deep_shadow -> shadow -> midtone -> highlight -> specular
    Using OKLAB for perceptually uniform transitions.
    """
    base = np.array(base_srgb[:3], dtype=np.float64) / 255.0
    base_oklab = srgb_to_oklab(base)

    shifts = [
        (-20, -0.25, 0.7),   # deep shadow
        (-10, -0.12, 0.85),  # shadow
        (0, 0.0, 1.0),       # midtone
        (10, 0.10, 1.1),     # highlight
        (20, 0.20, 0.95),    # specular
    ]

    if levels <= len(shifts):
        indices = np.linspace(0, len(shifts) - 1, levels).astype(int)
    else:
        indices = list(range(len(shifts)))

    ramp: list[tuple[int, ...]] = []
    for idx in indices:
        hue_shift, l_delta, c_scale = shifts[idx]
        shifted = _hue_shift_color(base_oklab, hue_shift, l_delta, c_scale)
        srgb = oklab_to_srgb(shifted, as_uint8=True)
        if srgb.ndim > 1:
            srgb = srgb[0]
        ramp.append(tuple(int(c) for c in srgb) + (255,))
    return ramp


# ── Main Renderer ─────────────────────────────────────────────────────────────

# ── SESSION-020: Multi-layer Render Compositing ─────────────────────────────

@dataclass
class LayeredRenderResult:
    """Result of multi-layer SDF rendering.

    SESSION-020: Separates the rendering pipeline into independent layers
    for artistic control. Each layer is an RGBA PIL Image that can be
    individually adjusted (opacity, color grading, blur) before final
    compositing.

    Layers (back to front):
      1. **base_layer**: Flat fill using the midtone color ramp, no lighting.
      2. **texture_layer**: Texture/noise contribution (premultiplied alpha).
      3. **lighting_layer**: Directional light + AO as a luminosity overlay.
      4. **outline_layer**: 1px outline on a transparent background.
      5. **composite**: All layers merged in standard order.

    Each layer uses the same inside/outline masks derived from the SDF,
    so they align pixel-perfectly.
    """
    base_layer: Image.Image
    texture_layer: Image.Image
    lighting_layer: Image.Image
    outline_layer: Image.Image
    composite: Image.Image
    width: int = 0
    height: int = 0
    metadata: dict = field(default_factory=dict)

    def export_layers(self, prefix: str) -> list[str]:
        """Save all layers to files and return paths."""
        paths = []
        for name in ["base", "texture", "lighting", "outline", "composite"]:
            layer = getattr(self, f"{name}_layer" if name != "composite" else "composite")
            path = f"{prefix}_{name}.png"
            layer.save(path)
            paths.append(path)
        return paths


def render_sdf_layered(
    sdf_func: SDFFunc,
    width: int = 32,
    height: int = 32,
    palette: Optional[Palette] = None,
    outline_width: float = 0.03,
    fill_color: Optional[tuple[int, ...]] = None,
    outline_color: Optional[tuple[int, ...]] = None,
    bg_transparent: bool = True,
    enable_lighting: bool = True,
    light_angle: float = 0.785,
    enable_dithering: bool = True,
    dither_matrix_size: int = 4,
    enable_ao: bool = True,
    enable_hue_shift: bool = True,
    enable_outline: bool = True,
    ao_strength: float = 0.4,
    color_ramp_levels: int = 5,
    texture_func: Optional[Callable[[np.ndarray, np.ndarray], np.ndarray]] = None,
    texture_strength: float = 0.3,
    palette_constrained: bool = False,
    palette_colors: Optional[list[tuple[int, int, int]]] = None,
    palette_dither: bool = True,
) -> LayeredRenderResult:
    """Render an SDF to separate compositable layers.

    SESSION-020 (P0-NEW-4): Multi-layer render compositing.

    Produces four independent RGBA layers plus a pre-composited result.
    Each layer can be individually adjusted before final compositing,
    enabling artistic control over lighting intensity, outline color/width,
    texture blending, and base color grading.

    The composite is equivalent to ``render_sdf()`` output, ensuring
    backward compatibility.

    Parameters
    ----------
    (Same as ``render_sdf``.)

    Returns
    -------
    LayeredRenderResult
        Contains base_layer, texture_layer, lighting_layer, outline_layer,
        and composite.
    """
    xs = np.linspace(-1, 1, width)
    ys = np.linspace(-1, 1, height)
    X, Y = np.meshgrid(xs, ys)
    dist = sdf_func(X, Y)

    # ── base colours ──
    if palette is not None and palette.count >= 2:
        srgb = palette.colors_srgb
        base_fill = tuple(int(c) for c in srgb[0]) + (255,)
        base_outline = outline_color or tuple(int(c) for c in srgb[-1]) + (255,)
    else:
        base_fill = fill_color or (200, 80, 80, 255)
        base_outline = outline_color or (40, 20, 20, 255)

    # ── colour ramp ──
    if enable_hue_shift:
        ramp = _generate_color_ramp(base_fill[:3], color_ramp_levels)
    else:
        base = np.array(base_fill[:3], dtype=np.float64)
        ramp = []
        for i in range(color_ramp_levels):
            t = i / max(1, color_ramp_levels - 1)
            factor = 0.4 + t * 0.8
            c = np.clip(base * factor, 0, 255).astype(int)
            ramp.append(tuple(c) + (255,))

    # ── masks ──
    inside_mask = dist < 0
    if enable_outline:
        outline_mask = (dist >= -outline_width) & (dist < outline_width * 0.5)
        inner_mask = dist < -outline_width
    else:
        outline_mask = np.zeros_like(dist, dtype=bool)
        inner_mask = inside_mask

    # ── lighting ──
    if enable_lighting:
        nx, ny = _compute_sdf_normals(sdf_func, X, Y)
        lx = np.cos(light_angle)
        ly = -np.sin(light_angle)
        light_intensity = np.clip(nx * lx + ny * ly, 0, 1)
    else:
        light_intensity = np.full_like(dist, 0.5)

    # ── ambient occlusion ──
    if enable_ao:
        nx_ao, ny_ao = _compute_sdf_normals(sdf_func, X, Y)
        ao = _compute_sdf_ao(sdf_func, X, Y, nx_ao, ny_ao)
    else:
        ao = np.ones_like(dist)

    # ── texture ──
    if texture_func is not None:
        tex_val = texture_func(X, Y)
        t_min, t_max = tex_val.min(), tex_val.max()
        if t_max - t_min > 1e-8:
            tex_val = (tex_val - t_min) / (t_max - t_min)
        else:
            tex_val = np.full_like(tex_val, 0.5)
    else:
        tex_val = np.full_like(dist, 0.5)

    # ═══════════════════════════════════════════════════════════════════
    # LAYER 1: BASE — flat fill using midtone ramp color
    # ═══════════════════════════════════════════════════════════════════
    base_layer = np.zeros((height, width, 4), dtype=np.uint8)
    midtone_idx = len(ramp) // 2
    midtone_color = ramp[midtone_idx]
    base_layer[inner_mask] = midtone_color
    base_img = Image.fromarray(base_layer, "RGBA")

    # ═══════════════════════════════════════════════════════════════════
    # LAYER 2: TEXTURE — texture contribution as color-tinted overlay
    # ═══════════════════════════════════════════════════════════════════
    texture_layer = np.zeros((height, width, 4), dtype=np.uint8)
    if texture_func is not None:
        # Map texture value to ramp colors
        tex_ramp_idx = np.clip(
            (tex_val * (len(ramp) - 1)).astype(int), 0, len(ramp) - 1
        )
        for i, color in enumerate(ramp):
            mask = inner_mask & (tex_ramp_idx == i)
            texture_layer[mask] = color[:3] + (int(255 * texture_strength),)
    texture_img = Image.fromarray(texture_layer, "RGBA")

    # ═══════════════════════════════════════════════════════════════════
    # LAYER 3: LIGHTING — light + AO as luminosity overlay
    # ═══════════════════════════════════════════════════════════════════
    lighting_layer = np.zeros((height, width, 4), dtype=np.uint8)
    combined_light = light_intensity * (1.0 - ao_strength + ao_strength * ao)
    combined_light = np.clip(combined_light, 0, 1)
    # Encode as grayscale: 128 = neutral, >128 = lighten, <128 = darken
    light_gray = np.clip(combined_light * 255, 0, 255).astype(np.uint8)
    lighting_layer[inner_mask, 0] = light_gray[inner_mask]
    lighting_layer[inner_mask, 1] = light_gray[inner_mask]
    lighting_layer[inner_mask, 2] = light_gray[inner_mask]
    lighting_layer[inner_mask, 3] = 255
    lighting_img = Image.fromarray(lighting_layer, "RGBA")

    # ═══════════════════════════════════════════════════════════════════
    # LAYER 4: OUTLINE — outline pixels on transparent background
    # ═══════════════════════════════════════════════════════════════════
    outline_layer = np.zeros((height, width, 4), dtype=np.uint8)
    if enable_outline:
        outline_layer[outline_mask & inside_mask] = base_outline
    outline_img = Image.fromarray(outline_layer, "RGBA")

    # ═══════════════════════════════════════════════════════════════════
    # COMPOSITE — standard render_sdf equivalent
    # ═══════════════════════════════════════════════════════════════════
    # Use the same compositing logic as render_sdf for full compatibility
    combined = light_intensity * (1.0 - ao_strength + ao_strength * ao)
    combined = combined * (1.0 - texture_strength) + tex_val * texture_strength
    combined = np.clip(combined, 0, 1)

    if enable_dithering:
        bayer = {2: BAYER_2, 4: BAYER_4, 8: BAYER_8}.get(
            dither_matrix_size, BAYER_4
        )
        ramp_idx = _apply_ordered_dither(combined, len(ramp), bayer)
    else:
        ramp_idx = np.clip(
            (combined * (len(ramp) - 1)).astype(int), 0, len(ramp) - 1
        )

    comp = np.zeros((height, width, 4), dtype=np.uint8)
    if not bg_transparent:
        comp[:, :] = [30, 30, 30, 255]

    for i, color in enumerate(ramp):
        mask = inner_mask & (ramp_idx == i)
        comp[mask] = color

    if enable_outline:
        comp[outline_mask & inside_mask] = base_outline

    composite_img = Image.fromarray(comp, "RGBA")

    if palette_constrained and palette_colors:
        composite_img = _apply_palette_constraint(
            composite_img, palette_colors, dither=palette_dither
        )

    return LayeredRenderResult(
        base_layer=base_img,
        texture_layer=texture_img,
        lighting_layer=lighting_img,
        outline_layer=outline_img,
        composite=composite_img,
        width=width,
        height=height,
        metadata={
            "fill_color": base_fill,
            "outline_color": base_outline,
            "ramp_levels": color_ramp_levels,
            "light_angle": light_angle,
            "ao_strength": ao_strength,
            "texture_strength": texture_strength,
            "outline_width": outline_width,
            "has_texture": texture_func is not None,
            "palette_constrained": palette_constrained,
        },
    )


def composite_layers(
    base: Image.Image,
    texture: Optional[Image.Image] = None,
    lighting: Optional[Image.Image] = None,
    outline: Optional[Image.Image] = None,
    base_opacity: float = 1.0,
    texture_opacity: float = 1.0,
    lighting_opacity: float = 1.0,
    outline_opacity: float = 1.0,
) -> Image.Image:
    """Composite individual layers with adjustable opacity.

    SESSION-020: Allows artists to tweak layer contributions after rendering.
    For example, reduce lighting_opacity for a flatter look, or increase
    outline_opacity for bolder outlines.

    Parameters
    ----------
    base : Image
        Base fill layer (required).
    texture : Image, optional
        Texture overlay layer.
    lighting : Image, optional
        Lighting/AO layer (grayscale encoded).
    outline : Image, optional
        Outline layer.
    *_opacity : float
        Opacity multiplier for each layer (0.0 - 1.0).

    Returns
    -------
    Image
        Composited RGBA image.
    """
    result = base.copy().convert("RGBA")

    if base_opacity < 1.0:
        arr = np.array(result)
        arr[:, :, 3] = (arr[:, :, 3] * base_opacity).astype(np.uint8)
        result = Image.fromarray(arr, "RGBA")

    # Texture: alpha-composite with opacity
    if texture is not None:
        tex = texture.copy()
        if texture_opacity < 1.0:
            arr = np.array(tex)
            arr[:, :, 3] = (arr[:, :, 3] * texture_opacity).astype(np.uint8)
            tex = Image.fromarray(arr, "RGBA")
        result = Image.alpha_composite(result, tex)

    # Lighting: apply as luminosity adjustment
    if lighting is not None:
        result_arr = np.array(result, dtype=np.float32)
        light_arr = np.array(lighting, dtype=np.float32)
        light_alpha = light_arr[:, :, 3] / 255.0 * lighting_opacity
        # Normalize light: 128 = neutral, scale to [0, 2]
        light_factor = light_arr[:, :, 0] / 128.0
        for c in range(3):
            result_arr[:, :, c] = np.clip(
                result_arr[:, :, c] * (
                    1.0 - light_alpha + light_alpha * light_factor
                ),
                0, 255,
            )
        result = Image.fromarray(result_arr.astype(np.uint8), "RGBA")

    # Outline: alpha-composite on top
    if outline is not None:
        out = outline.copy()
        if outline_opacity < 1.0:
            arr = np.array(out)
            arr[:, :, 3] = (arr[:, :, 3] * outline_opacity).astype(np.uint8)
            out = Image.fromarray(arr, "RGBA")
        result = Image.alpha_composite(result, out)

    return result


def render_sdf(
    sdf_func: SDFFunc,
    width: int = 32,
    height: int = 32,
    palette: Optional[Palette] = None,
    outline_width: float = 0.03,
    fill_color: Optional[tuple[int, ...]] = None,
    outline_color: Optional[tuple[int, ...]] = None,
    bg_transparent: bool = True,
    # Professional pixel art options
    enable_lighting: bool = True,
    light_angle: float = 0.785,
    enable_dithering: bool = True,
    dither_matrix_size: int = 4,
    enable_ao: bool = True,
    enable_hue_shift: bool = True,
    enable_outline: bool = True,
    ao_strength: float = 0.4,
    color_ramp_levels: int = 5,
    texture_func: Optional[Callable[[np.ndarray, np.ndarray], np.ndarray]] = None,
    texture_strength: float = 0.3,
    # SESSION-019: Palette-constrained rendering
    palette_constrained: bool = False,
    palette_colors: Optional[list[tuple[int, int, int]]] = None,
    palette_dither: bool = True,
) -> Image.Image:
    """Render an SDF to a professional pixel art sprite.

    Uses SDF-gradient normals, Lambertian lighting, ambient occlusion,
    hue-shifted color ramps, and Bayer-matrix dithering to produce output
    comparable to hand-drawn pixel art.

    SESSION-019: Added ``palette_constrained`` mode. When enabled, the
    final image is quantized to the given ``palette_colors`` using OKLAB
    nearest-neighbor mapping with optional Floyd-Steinberg dithering.
    This produces true pixel art with exact palette colors instead of
    continuous-color renders that are post-quantized.

    All new keyword arguments are optional and default to producing a
    visually rich result. Callers that relied on the old 2-colour output
    can pass ``enable_lighting=False, enable_dithering=False,
    enable_ao=False, enable_hue_shift=False`` to get the legacy look.
    """
    xs = np.linspace(-1, 1, width)
    ys = np.linspace(-1, 1, height)
    X, Y = np.meshgrid(xs, ys)
    dist = sdf_func(X, Y)

    # ── base colours ──
    if palette is not None and palette.count >= 2:
        srgb = palette.colors_srgb
        base_fill = tuple(int(c) for c in srgb[0]) + (255,)
        base_outline = tuple(int(c) for c in srgb[-1]) + (255,)
    else:
        base_fill = fill_color or (200, 80, 80, 255)
        base_outline = outline_color or (40, 20, 20, 255)

    # ── colour ramp ──
    if enable_hue_shift:
        ramp = _generate_color_ramp(base_fill[:3], color_ramp_levels)
    else:
        base = np.array(base_fill[:3], dtype=np.float64)
        ramp = []
        for i in range(color_ramp_levels):
            t = i / max(1, color_ramp_levels - 1)
            factor = 0.4 + t * 0.8
            c = np.clip(base * factor, 0, 255).astype(int)
            ramp.append(tuple(c) + (255,))

    # ── lighting ──
    if enable_lighting:
        nx, ny = _compute_sdf_normals(sdf_func, X, Y)
        lx = np.cos(light_angle)
        ly = -np.sin(light_angle)
        light_intensity = np.clip(nx * lx + ny * ly, 0, 1)
    else:
        light_intensity = np.full_like(dist, 0.5)

    # ── ambient occlusion ──
    if enable_ao:
        nx_ao, ny_ao = _compute_sdf_normals(sdf_func, X, Y)
        ao = _compute_sdf_ao(sdf_func, X, Y, nx_ao, ny_ao)
    else:
        ao = np.ones_like(dist)

    # ── texture ──
    if texture_func is not None:
        tex_val = texture_func(X, Y)
        t_min, t_max = tex_val.min(), tex_val.max()
        if t_max - t_min > 1e-8:
            tex_val = (tex_val - t_min) / (t_max - t_min)
        else:
            tex_val = np.full_like(tex_val, 0.5)
    else:
        tex_val = np.full_like(dist, 0.5)

    # ── combine into final intensity ──
    combined = light_intensity * (1.0 - ao_strength + ao_strength * ao)
    combined = combined * (1.0 - texture_strength) + tex_val * texture_strength
    combined = np.clip(combined, 0, 1)

    # ── map to colour ramp ──
    if enable_dithering:
        bayer = {2: BAYER_2, 4: BAYER_4, 8: BAYER_8}.get(
            dither_matrix_size, BAYER_4
        )
        ramp_idx = _apply_ordered_dither(combined, len(ramp), bayer)
    else:
        ramp_idx = np.clip(
            (combined * (len(ramp) - 1)).astype(int), 0, len(ramp) - 1
        )

    # ── build output image ──
    img = np.zeros((height, width, 4), dtype=np.uint8)
    if not bg_transparent:
        img[:, :] = [30, 30, 30, 255]

    inside_mask = dist < 0

    if enable_outline:
        outline_mask = (dist >= -outline_width) & (dist < outline_width * 0.5)
        inner_mask = dist < -outline_width
    else:
        outline_mask = np.zeros_like(dist, dtype=bool)
        inner_mask = inside_mask

    for i, color in enumerate(ramp):
        mask = inner_mask & (ramp_idx == i)
        img[mask] = color

    if enable_outline:
        img[outline_mask & inside_mask] = base_outline

    result_img = Image.fromarray(img, "RGBA")

    # SESSION-019: Palette-constrained post-processing
    if palette_constrained and palette_colors:
        result_img = _apply_palette_constraint(
            result_img, palette_colors, dither=palette_dither
        )

    return result_img


def _apply_palette_constraint(
    img: Image.Image,
    palette_colors: list[tuple[int, int, int]],
    dither: bool = True,
) -> Image.Image:
    """Quantize rendered image to strict palette using OKLAB space.

    SESSION-019: This is the key function that bridges continuous SDF
    rendering with discrete pixel art palettes. Uses the existing
    OKLAB quantizer for perceptually accurate color mapping.
    """
    try:
        from ..oklab.quantizer import quantize_image
        from ..oklab.palette import Palette

        # Build Palette object from color list
        pal = Palette.from_srgb_list(palette_colors)
        return quantize_image(img, pal, dither=dither, preserve_alpha=True)
    except Exception:
        # Fallback: manual nearest-neighbor quantization
        arr = np.array(img)
        if arr.shape[2] < 4:
            return img
        alpha = arr[:, :, 3].copy()
        rgb = arr[:, :, :3].astype(np.float32)
        pal_arr = np.array(palette_colors, dtype=np.float32)  # (N, 3)

        # Reshape for broadcasting
        h, w = rgb.shape[:2]
        pixels = rgb.reshape(-1, 1, 3)  # (H*W, 1, 3)
        pal_exp = pal_arr[np.newaxis, :, :]  # (1, N, 3)

        # Nearest neighbor in RGB space
        dists = np.sum((pixels - pal_exp) ** 2, axis=2)  # (H*W, N)
        indices = np.argmin(dists, axis=1)  # (H*W,)
        result_rgb = pal_arr[indices].reshape(h, w, 3).astype(np.uint8)

        out = np.zeros((h, w, 4), dtype=np.uint8)
        out[:, :, :3] = result_rgb
        out[:, :, 3] = alpha
        return Image.fromarray(out, "RGBA")


def render_sdf_simple(
    sdf_func: SDFFunc,
    width: int = 32,
    height: int = 32,
    palette: Optional[Palette] = None,
    outline_width: float = 0.03,
    fill_color: Optional[tuple[int, ...]] = None,
    outline_color: Optional[tuple[int, ...]] = None,
    bg_transparent: bool = True,
) -> Image.Image:
    """Legacy simple renderer (2-colour fill + outline).

    Kept for backward compatibility and fast previews.
    """
    xs = np.linspace(-1, 1, width)
    ys = np.linspace(-1, 1, height)
    X, Y = np.meshgrid(xs, ys)
    dist = sdf_func(X, Y)

    if palette is not None and palette.count >= 2:
        srgb = palette.colors_srgb
        fc = (*srgb[0], 255)
        oc = (*srgb[-1], 255)
    else:
        fc = fill_color or (200, 80, 80, 255)
        oc = outline_color or (40, 20, 20, 255)

    img = np.zeros((height, width, 4), dtype=np.uint8)
    if bg_transparent:
        img[:, :, 3] = 0
    else:
        img[:, :] = [30, 30, 30, 255]

    fill_mask = dist < -outline_width
    img[fill_mask] = fc
    outline_mask = (dist >= -outline_width) & (dist < 0)
    img[outline_mask] = oc
    return Image.fromarray(img, "RGBA")


def render_spritesheet(
    sdf_func_animated: Callable[[np.ndarray, np.ndarray, float], np.ndarray],
    frames: int = 8,
    width: int = 32,
    height: int = 32,
    palette: Optional[Palette] = None,
    outline_width: float = 0.03,
    **kwargs,
) -> Image.Image:
    """Render an animated SDF to a horizontal sprite sheet.

    Args:
        sdf_func_animated: SDF function with time parameter (x, y, t) -> distance.
        frames: Number of frames in the animation.
        width, height: Size of each frame in pixels.
        palette: Color palette for rendering.

    Returns:
        PIL Image (RGBA) with frames arranged horizontally.
    """
    sheet = Image.new("RGBA", (width * frames, height), (0, 0, 0, 0))
    for i in range(frames):
        t = i / frames

        def frame_sdf(x, y, _t=t):
            return sdf_func_animated(x, y, _t)

        frame = render_sdf(
            frame_sdf, width, height, palette, outline_width, **kwargs
        )
        sheet.paste(frame, (i * width, 0))
    return sheet


def render_textured_sdf(
    sdf_func: SDFFunc,
    texture_type: str = "stone",
    width: int = 32,
    height: int = 32,
    palette: Optional[Palette] = None,
    **kwargs,
) -> Image.Image:
    """Render an SDF with a procedural noise texture applied.

    Args:
        sdf_func: SDF function.
        texture_type: One of ``stone``, ``wood``, ``metal``, ``organic``,
            ``crystal``.
        width, height: Output size.
        palette: Color palette.

    Returns:
        Textured pixel art sprite.
    """
    from .noise import fbm

    texture_configs = {
        "stone": {"scale": 8.0, "octaves": 4, "persistence": 0.5},
        "wood": {"scale": 3.0, "octaves": 6, "persistence": 0.6},
        "metal": {"scale": 12.0, "octaves": 2, "persistence": 0.3},
        "organic": {"scale": 5.0, "octaves": 5, "persistence": 0.55},
        "crystal": {"scale": 6.0, "octaves": 3, "persistence": 0.4},
    }
    config = texture_configs.get(texture_type, texture_configs["stone"])
    noise_arr = fbm(width * 2, height * 2, **config)

    def tex_func(x: np.ndarray, y: np.ndarray) -> np.ndarray:
        ix = ((x + 1) / 2 * (noise_arr.shape[1] - 1)).astype(int)
        iy = ((y + 1) / 2 * (noise_arr.shape[0] - 1)).astype(int)
        ix = np.clip(ix, 0, noise_arr.shape[1] - 1)
        iy = np.clip(iy, 0, noise_arr.shape[0] - 1)
        return noise_arr[iy, ix]

    return render_sdf(
        sdf_func,
        width,
        height,
        palette,
        texture_func=tex_func,
        texture_strength=0.35,
        **kwargs,
    )
