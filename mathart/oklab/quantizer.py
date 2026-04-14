"""
Image quantization to a limited palette in OKLAB space.

Pixel art requires strict palette adherence. This module maps arbitrary
images to a given Palette using nearest-neighbor in OKLAB (perceptually
uniform, so "nearest" = "most similar to human eye").

Unity constraint: output must be PNG with alpha transparency preserved.
"""
from __future__ import annotations
import numpy as np
from PIL import Image

from .color_space import srgb_to_oklab, oklab_to_srgb
from .palette import Palette


def quantize_image(
    image: Image.Image | np.ndarray,
    palette: Palette,
    dither: bool = False,
    preserve_alpha: bool = True,
) -> Image.Image:
    """Quantize an image to the given palette colors.

    Args:
        image: Input image (PIL Image or numpy array).
        palette: Target palette.
        dither: If True, apply Floyd-Steinberg dithering in OKLAB space.
        preserve_alpha: If True, preserve alpha channel from input.

    Returns:
        Quantized PIL Image (RGBA if alpha present, else RGB).
    """
    if isinstance(image, Image.Image):
        has_alpha = image.mode == "RGBA"
        if has_alpha:
            arr = np.array(image.convert("RGBA"))
            alpha = arr[:, :, 3]
            rgb = arr[:, :, :3]
        else:
            rgb = np.array(image.convert("RGB"))
            alpha = None
    else:
        rgb = image[:, :, :3]
        alpha = image[:, :, 3] if image.shape[2] == 4 else None

    h, w, _ = rgb.shape
    pixels = rgb.reshape(-1, 3).astype(np.float64)

    # Convert to OKLAB
    pixels_lab = srgb_to_oklab(pixels)
    palette_lab = palette.colors_oklab

    if dither:
        result_lab = _floyd_steinberg_oklab(pixels_lab.reshape(h, w, 3), palette_lab)
        result_lab = result_lab.reshape(-1, 3)
    else:
        # Nearest neighbor in OKLAB
        result_lab = _nearest_palette(pixels_lab, palette_lab)

    # Convert back to sRGB
    result_srgb = oklab_to_srgb(result_lab).reshape(h, w, 3)

    if preserve_alpha and alpha is not None:
        out = np.zeros((h, w, 4), dtype=np.uint8)
        out[:, :, :3] = result_srgb
        out[:, :, 3] = alpha
        return Image.fromarray(out, "RGBA")
    else:
        return Image.fromarray(result_srgb, "RGB")


def _nearest_palette(pixels_lab: np.ndarray, palette_lab: np.ndarray) -> np.ndarray:
    """Find nearest palette color for each pixel in OKLAB space."""
    # Compute distances: [N_pixels, N_palette]
    diff = pixels_lab[:, np.newaxis, :] - palette_lab[np.newaxis, :, :]
    dists = np.sum(diff ** 2, axis=2)
    indices = np.argmin(dists, axis=1)
    return palette_lab[indices]


def _floyd_steinberg_oklab(
    image_lab: np.ndarray, palette_lab: np.ndarray
) -> np.ndarray:
    """Floyd-Steinberg dithering in OKLAB space."""
    h, w, _ = image_lab.shape
    buf = image_lab.astype(np.float64).copy()

    for y in range(h):
        for x in range(w):
            old = buf[y, x].copy()
            # Find nearest palette color
            dists = np.sum((palette_lab - old) ** 2, axis=1)
            idx = np.argmin(dists)
            new = palette_lab[idx]
            buf[y, x] = new
            err = old - new

            # Distribute error
            if x + 1 < w:
                buf[y, x + 1] += err * 7 / 16
            if y + 1 < h:
                if x - 1 >= 0:
                    buf[y + 1, x - 1] += err * 3 / 16
                buf[y + 1, x] += err * 5 / 16
                if x + 1 < w:
                    buf[y + 1, x + 1] += err * 1 / 16

    return buf
