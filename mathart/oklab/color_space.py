"""
OKLAB / OKLCH color space conversions.

References:
  - Björn Ottosson, "A perceptual color space for image processing"
    https://bottosson.github.io/posts/oklab/
  - Distilled knowledge: warm-light→cool-shadow, cool-light→warm-shadow
    (from MarioTrickster-Art PROMPT_RECIPES §光影)

All functions operate on numpy arrays for batch processing.
L ∈ [0, 1], a ∈ [-0.4, 0.4], b ∈ [-0.4, 0.4]
"""
from __future__ import annotations
import numpy as np


# ── Linear sRGB ↔ sRGB gamma ──────────────────────────────────

def _linear_to_srgb_channel(c: np.ndarray) -> np.ndarray:
    """Apply sRGB gamma curve (vectorized)."""
    out = np.where(c <= 0.0031308, 12.92 * c, 1.055 * np.power(np.clip(c, 0, None), 1.0 / 2.4) - 0.055)
    return np.clip(out, 0.0, 1.0)


def _srgb_to_linear_channel(c: np.ndarray) -> np.ndarray:
    """Remove sRGB gamma curve (vectorized)."""
    return np.where(c <= 0.04045, c / 12.92, np.power((c + 0.055) / 1.055, 2.4))


# ── OKLAB ↔ Linear sRGB ───────────────────────────────────────

# M1: linear sRGB → LMS (approximate cone response)
_M1 = np.array([
    [0.4122214708, 0.5363325363, 0.0514459929],
    [0.2119034982, 0.6806995451, 0.1073969566],
    [0.0883024619, 0.2817188376, 0.6299787005],
])

# M2: cube-root LMS → OKLAB
_M2 = np.array([
    [0.2104542553, 0.7936177850, -0.0040720468],
    [1.9779984951, -2.4285922050, 0.4505937099],
    [0.0259040371, 0.7827717662, -0.8086757660],
])

_M1_inv = np.linalg.inv(_M1)
_M2_inv = np.linalg.inv(_M2)


def linear_to_oklab(rgb_linear: np.ndarray) -> np.ndarray:
    """Convert linear sRGB [N, 3] to OKLAB [N, 3]."""
    rgb = np.asarray(rgb_linear, dtype=np.float64)
    was_1d = rgb.ndim == 1
    if was_1d:
        rgb = rgb[np.newaxis, :]
    lms = rgb @ _M1.T
    lms_cbrt = np.sign(lms) * np.abs(lms) ** (1.0 / 3.0)
    lab = lms_cbrt @ _M2.T
    return lab[0] if was_1d else lab


def oklab_to_linear(lab: np.ndarray) -> np.ndarray:
    """Convert OKLAB [N, 3] to linear sRGB [N, 3]."""
    lab = np.asarray(lab, dtype=np.float64)
    was_1d = lab.ndim == 1
    if was_1d:
        lab = lab[np.newaxis, :]
    lms_cbrt = lab @ _M2_inv.T
    lms = lms_cbrt ** 3
    rgb = lms @ _M1_inv.T
    return rgb[0] if was_1d else rgb


def srgb_to_oklab(srgb: np.ndarray) -> np.ndarray:
    """Convert sRGB [0-255 uint8 or 0-1 float] to OKLAB."""
    srgb = np.asarray(srgb, dtype=np.float64)
    if srgb.max() > 1.0:
        srgb = srgb / 255.0
    was_1d = srgb.ndim == 1
    if was_1d:
        srgb = srgb[np.newaxis, :]
    linear = np.stack([_srgb_to_linear_channel(srgb[:, i]) for i in range(3)], axis=-1)
    result = linear_to_oklab(linear)
    return result[0] if was_1d else result


def oklab_to_srgb(lab: np.ndarray, as_uint8: bool = True) -> np.ndarray:
    """Convert OKLAB to sRGB. Returns uint8 [0-255] by default."""
    linear = oklab_to_linear(lab)
    was_1d = linear.ndim == 1
    if was_1d:
        linear = linear[np.newaxis, :]
    srgb = np.stack([_linear_to_srgb_channel(linear[:, i]) for i in range(3)], axis=-1)
    if was_1d:
        srgb = srgb[0]
    if as_uint8:
        return np.round(srgb * 255).astype(np.uint8)
    return srgb


# ── OKLAB ↔ OKLCH ─────────────────────────────────────────────

def oklab_to_oklch(lab: np.ndarray) -> np.ndarray:
    """Convert OKLAB [L, a, b] to OKLCH [L, C, h(radians)]."""
    lab = np.asarray(lab, dtype=np.float64)
    was_1d = lab.ndim == 1
    if was_1d:
        lab = lab[np.newaxis, :]
    L = lab[:, 0]
    a = lab[:, 1]
    b = lab[:, 2]
    C = np.sqrt(a**2 + b**2)
    h = np.arctan2(b, a)
    lch = np.stack([L, C, h], axis=-1)
    return lch[0] if was_1d else lch


def oklch_to_oklab(lch: np.ndarray) -> np.ndarray:
    """Convert OKLCH [L, C, h(radians)] to OKLAB [L, a, b]."""
    lch = np.asarray(lch, dtype=np.float64)
    was_1d = lch.ndim == 1
    if was_1d:
        lch = lch[np.newaxis, :]
    L = lch[:, 0]
    C = lch[:, 1]
    h = lch[:, 2]
    a = C * np.cos(h)
    b = C * np.sin(h)
    lab = np.stack([L, a, b], axis=-1)
    return lab[0] if was_1d else lab
