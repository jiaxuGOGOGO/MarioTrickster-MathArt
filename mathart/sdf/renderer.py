"""
SDF → Pixel Art renderer.

Renders SDFs to pixel-perfect sprites compatible with Unity's requirements:
  - PPU = 32 (from TA_AssetValidator)
  - Point filtering (no anti-aliasing at final output)
  - Transparent background (RGBA)

Supports rendering animated sprite sheets for time-varying SDFs.
"""
from __future__ import annotations
from typing import Callable
import numpy as np
from PIL import Image

from ..oklab.palette import Palette
from ..oklab.color_space import oklab_to_srgb

SDFFunc = Callable[[np.ndarray, np.ndarray], np.ndarray]


def render_sdf(
    sdf_func: SDFFunc,
    width: int = 32,
    height: int = 32,
    palette: Palette | None = None,
    outline_width: float = 0.03,
    fill_color: tuple[int, ...] | None = None,
    outline_color: tuple[int, ...] | None = None,
    bg_transparent: bool = True,
) -> Image.Image:
    """Render an SDF to a pixel art sprite.

    Args:
        sdf_func: SDF function mapping (x, y) → distance.
        width, height: Output size in pixels.
        palette: If provided, use palette colors for fill/outline.
        outline_width: Width of outline in normalized coords.
        fill_color: RGBA fill color (overrides palette).
        outline_color: RGBA outline color (overrides palette).
        bg_transparent: If True, background is transparent.

    Returns:
        PIL Image (RGBA).
    """
    # Create coordinate grid in [-1, 1]
    xs = np.linspace(-1, 1, width)
    ys = np.linspace(-1, 1, height)
    X, Y = np.meshgrid(xs, ys)

    dist = sdf_func(X, Y)

    # Determine colors
    if palette is not None and palette.count >= 2:
        srgb = palette.colors_srgb
        fc = (*srgb[0], 255)
        oc = (*srgb[-1], 255)
    else:
        fc = fill_color or (200, 80, 80, 255)
        oc = outline_color or (40, 20, 20, 255)

    # Render: outline → fill → transparent
    img = np.zeros((height, width, 4), dtype=np.uint8)

    if bg_transparent:
        img[:, :, 3] = 0
    else:
        img[:, :] = [30, 30, 30, 255]

    # Fill: inside the shape
    fill_mask = dist < -outline_width
    img[fill_mask] = fc

    # Outline: on the boundary
    outline_mask = (dist >= -outline_width) & (dist < 0)
    img[outline_mask] = oc

    return Image.fromarray(img, "RGBA")


def render_spritesheet(
    sdf_func_animated: Callable[[np.ndarray, np.ndarray, float], np.ndarray],
    frames: int = 8,
    width: int = 32,
    height: int = 32,
    palette: Palette | None = None,
    outline_width: float = 0.03,
    **kwargs,
) -> Image.Image:
    """Render an animated SDF to a horizontal sprite sheet.

    Args:
        sdf_func_animated: SDF function with time parameter (x, y, t) → distance.
            t ranges from 0 to 1 over the animation cycle.
        frames: Number of frames in the animation.
        width, height: Size of each frame in pixels.
        palette: Color palette for rendering.

    Returns:
        PIL Image (RGBA) with frames arranged horizontally.
        Compatible with AI_SpriteSlicer (colCount = frames).
    """
    sheet = Image.new("RGBA", (width * frames, height), (0, 0, 0, 0))

    for i in range(frames):
        t = i / frames

        def frame_sdf(x, y, _t=t):
            return sdf_func_animated(x, y, _t)

        frame = render_sdf(frame_sdf, width, height, palette, outline_width, **kwargs)
        sheet.paste(frame, (i * width, 0))

    return sheet
