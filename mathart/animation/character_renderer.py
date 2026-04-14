"""
High-fidelity character renderer using SDF body parts.

Replaces the old line+circle skeleton renderer with:
  1. SDF-based body parts (head, torso, limbs) with proper shapes
  2. Per-part fill colors from palette
  3. Directional lighting (highlight + shadow bands)
  4. Pixel-perfect outline (1px dark border)
  5. Optional ordered dithering for color transitions

Rendering pipeline per frame:
  skeleton.apply_pose(pose)
  positions = skeleton.get_joint_positions()
  for each part (sorted by z_order):
      transform part SDF to world space using joint position
      evaluate SDF on pixel grid
      composite: outline → shadow → fill → highlight
  final: quantize to palette if needed

Distilled knowledge:
  - 暖光冷影: highlight = warm shift, shadow = cool shift in OKLAB
  - 3値色階: highlight / midtone / shadow per part
  - 1px outline is standard for 32px pixel art sprites
"""
from __future__ import annotations
from typing import Callable
import numpy as np
from PIL import Image

from .skeleton import Skeleton
from .parts import CharacterStyle, BodyPart, assemble_character
from ..oklab.palette import Palette, PaletteGenerator
from ..oklab.color_space import srgb_to_oklab, oklab_to_srgb


def _transform_coords(
    x: np.ndarray, y: np.ndarray,
    tx: float, ty: float,
    angle: float = 0.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Transform world coords to local part coords (inverse transform)."""
    # Translate
    lx = x - tx
    ly = y - ty
    # Rotate (inverse = negative angle)
    if abs(angle) > 1e-6:
        cos_a = np.cos(-angle)
        sin_a = np.sin(-angle)
        rx = lx * cos_a - ly * sin_a
        ry = lx * sin_a + ly * cos_a
        return rx, ry
    return lx, ly


def _compute_light_value(
    x: np.ndarray, y: np.ndarray,
    dist: np.ndarray,
    light_angle: float,
) -> np.ndarray:
    """Compute per-pixel light value based on surface normal approximation.

    Returns values in [-1, 1]: -1 = full shadow, 0 = midtone, 1 = full highlight.
    Uses the SDF gradient as a proxy for surface normal.
    """
    # Light direction
    lx = np.cos(light_angle)
    ly = np.sin(light_angle)

    # Approximate normal from SDF gradient (central differences)
    eps = 0.02
    # We use the position relative to shape center as a simple normal proxy
    # (works well for convex shapes which is most body parts)
    r = np.sqrt(x**2 + y**2) + 1e-6
    nx = x / r
    ny = y / r

    # Dot product with light direction
    ndotl = nx * lx + ny * ly

    return np.clip(ndotl, -1, 1)


def _bayer_dither_2x2() -> np.ndarray:
    """2x2 Bayer dithering matrix, normalized to [0, 1]."""
    return np.array([
        [0, 2],
        [3, 1],
    ], dtype=np.float32) / 4.0


def _apply_dither(
    value: np.ndarray,
    threshold: float,
    width: int, height: int,
) -> np.ndarray:
    """Apply ordered dithering to a value array.

    Returns boolean mask where value > dithered threshold.
    """
    bayer = _bayer_dither_2x2()
    # Tile the Bayer matrix across the image
    bh, bw = bayer.shape
    tiled = np.tile(bayer, (height // bh + 1, width // bw + 1))[:height, :width]
    return value > (threshold + (tiled - 0.5) * 0.3)


def _get_part_colors(
    base_color_srgb: np.ndarray,
    light_angle: float,
) -> dict[str, tuple[int, ...]]:
    """Generate highlight/midtone/shadow colors from a base color.

    Uses OKLAB for perceptually correct light/shadow shifts.
    Distilled knowledge: 暖光冷影 — highlight warm, shadow cool.
    """
    base_oklab = srgb_to_oklab(base_color_srgb[:3])

    L, a, b = base_oklab[0], base_oklab[1], base_oklab[2]

    # Highlight: +L, warm shift (+a, +b slightly)
    hl_L = min(L + 0.15, 0.95)
    hl_a = a + 0.02
    hl_b = b + 0.03
    highlight = oklab_to_srgb(np.array([hl_L, hl_a, hl_b]))

    # Shadow: -L, cool shift (-a, -b slightly)
    sh_L = max(L - 0.18, 0.08)
    sh_a = a - 0.02
    sh_b = b - 0.04
    shadow = oklab_to_srgb(np.array([sh_L, sh_a, sh_b]))

    return {
        "highlight": (*highlight.astype(int), 255),
        "midtone": (*base_color_srgb[:3].astype(int), 255),
        "shadow": (*shadow.astype(int), 255),
    }


def render_character_frame(
    skeleton: Skeleton,
    pose: dict[str, float],
    style: CharacterStyle,
    width: int = 32,
    height: int = 32,
    palette: Palette | None = None,
    enable_dither: bool = True,
    enable_outline: bool = True,
    enable_lighting: bool = True,
) -> Image.Image:
    """Render a single character frame with full pixel art quality.

    Args:
        skeleton: Character skeleton.
        pose: Joint angles for this frame.
        style: Character visual style.
        width, height: Frame size in pixels.
        palette: Color palette (at least 6 colors).
        enable_dither: Apply ordered dithering at light/shadow boundaries.
        enable_outline: Draw 1px dark outline around each part.
        enable_lighting: Apply directional highlight/shadow.

    Returns:
        RGBA PIL Image.
    """
    skeleton.apply_pose(pose)
    positions = skeleton.get_joint_positions()

    # Build pixel coordinate grids
    # Skeleton space: x ∈ [-0.5, 0.5], y ∈ [0, 1]
    # We add padding for outline
    xs = np.linspace(-0.6, 0.6, width)
    ys = np.linspace(1.1, -0.1, height)  # Y flipped (top=high Y)
    X, Y = np.meshgrid(xs, ys)

    # Get palette colors
    if palette is not None and palette.count >= 6:
        pal_srgb = palette.colors_srgb
    else:
        # Default Mario-ish colors
        pal_srgb = np.array([
            [240, 200, 160],  # skin
            [200, 50, 50],    # hair/hat (red)
            [200, 50, 50],    # shirt (red)
            [80, 80, 200],    # pants (blue)
            [100, 60, 30],    # shoes (brown)
            [30, 20, 15],     # outline (dark)
        ], dtype=np.float64)

    # Assemble character parts
    parts = assemble_character(style)

    # Output buffer: RGBA
    img = np.zeros((height, width, 4), dtype=np.uint8)

    # Outline buffer: tracks which pixels are "inside any part"
    # We'll do a two-pass: first collect all part masks, then outline
    all_inside = np.zeros((height, width), dtype=bool)
    part_layers: list[tuple[BodyPart, np.ndarray, np.ndarray, np.ndarray]] = []

    for part in parts:
        # Get joint position for this part
        if part.joint_name not in positions:
            continue
        jx, jy = positions[part.joint_name]

        # Transform pixel coords to part-local coords
        local_x, local_y = _transform_coords(
            X, Y, jx + part.offset_x, jy + part.offset_y, part.rotation
        )

        # Evaluate SDF
        dist = part.sdf(local_x, local_y)

        # Inside mask
        inside = dist < 0

        # Store for later rendering
        part_layers.append((part, dist, local_x, local_y))
        if not part.is_outline_only:
            all_inside |= inside

    # Compute outline mask: pixels that are within ~1px of any part boundary
    # but not deep inside
    outline_mask = np.zeros((height, width), dtype=bool)
    if enable_outline:
        # Dilate all_inside by 1px and XOR
        from scipy.ndimage import binary_dilation
        dilated = binary_dilation(all_inside, iterations=1)
        outline_mask = dilated & ~all_inside

    # Render parts back-to-front
    outline_color = tuple(int(c) for c in pal_srgb[style.outline_color_idx]) + (255,)

    for part, dist, local_x, local_y in part_layers:
        inside = dist < 0

        if part.is_outline_only:
            # Eyes, mustache: just fill solid
            color = tuple(int(c) for c in pal_srgb[part.color_idx]) + (255,)
            img[inside] = color
            continue

        # Get base color and generate highlight/shadow
        base_srgb = pal_srgb[part.color_idx]

        if enable_lighting:
            colors = _get_part_colors(base_srgb, style.light_angle)

            # Compute light value
            light_val = _compute_light_value(local_x, local_y, dist, style.light_angle)

            if enable_dither:
                # Three-band with dithered transitions
                highlight_mask = inside & _apply_dither(light_val, 0.3, width, height)
                shadow_mask = inside & _apply_dither(-light_val, 0.3, width, height)
                midtone_mask = inside & ~highlight_mask & ~shadow_mask

                img[shadow_mask] = colors["shadow"]
                img[midtone_mask] = colors["midtone"]
                img[highlight_mask] = colors["highlight"]
            else:
                # Hard three-band
                highlight_mask = inside & (light_val > 0.3)
                shadow_mask = inside & (light_val < -0.3)
                midtone_mask = inside & ~highlight_mask & ~shadow_mask

                img[shadow_mask] = colors["shadow"]
                img[midtone_mask] = colors["midtone"]
                img[highlight_mask] = colors["highlight"]
        else:
            # Flat color
            color = tuple(int(c) for c in base_srgb) + (255,)
            img[inside] = color

    # Apply outline
    if enable_outline:
        img[outline_mask] = outline_color

        # Also outline individual parts for internal edges
        for part, dist, local_x, local_y in part_layers:
            if part.is_outline_only:
                continue
            # Thin outline at part boundary (where dist is close to 0)
            pixel_size = 1.2 / width  # Approximate pixel size in normalized coords
            boundary = (dist > -pixel_size * 1.5) & (dist < pixel_size * 0.5)
            # Only where there's already a filled pixel nearby
            img[boundary & all_inside] = outline_color

    return Image.fromarray(img, "RGBA")


def render_character_sheet(
    skeleton: Skeleton,
    animation_func: Callable[[float], dict[str, float]],
    style: CharacterStyle,
    frames: int = 8,
    frame_width: int = 32,
    frame_height: int = 32,
    palette: Palette | None = None,
    **kwargs,
) -> Image.Image:
    """Render a full animation as a horizontal sprite sheet.

    Args:
        skeleton: Character skeleton.
        animation_func: Function mapping t ∈ [0, 1] → pose dict.
        style: Character visual style.
        frames: Number of frames.
        frame_width, frame_height: Size per frame.
        palette: Color palette.

    Returns:
        RGBA PIL Image (width = frame_width * frames).
    """
    sheet = Image.new("RGBA", (frame_width * frames, frame_height), (0, 0, 0, 0))

    for i in range(frames):
        t = i / frames
        pose = animation_func(t)
        # Create a fresh skeleton for each frame to avoid state leakage
        fresh_skel = Skeleton.create_humanoid(skeleton.head_units)
        frame = render_character_frame(
            fresh_skel, pose, style, frame_width, frame_height, palette, **kwargs
        )
        sheet.paste(frame, (i * frame_width, 0))

    return sheet
