"""
SDF boolean and blending operations.

These combine multiple SDF shapes into complex forms.
Smooth variants use polynomial smooth-min for organic blending.
"""
from __future__ import annotations
from typing import Callable
import numpy as np

SDFFunc = Callable[[np.ndarray, np.ndarray], np.ndarray]


def union(a: SDFFunc, b: SDFFunc) -> SDFFunc:
    """Boolean union (OR) of two SDFs."""
    def sdf(x, y):
        return np.minimum(a(x, y), b(x, y))
    return sdf


def intersection(a: SDFFunc, b: SDFFunc) -> SDFFunc:
    """Boolean intersection (AND) of two SDFs."""
    def sdf(x, y):
        return np.maximum(a(x, y), b(x, y))
    return sdf


def subtraction(a: SDFFunc, b: SDFFunc) -> SDFFunc:
    """Boolean subtraction (A minus B)."""
    def sdf(x, y):
        return np.maximum(a(x, y), -b(x, y))
    return sdf


def smooth_union(a: SDFFunc, b: SDFFunc, k: float = 0.1) -> SDFFunc:
    """Smooth union with blending factor k."""
    def sdf(x, y):
        da = a(x, y)
        db = b(x, y)
        h = np.clip(0.5 + 0.5 * (db - da) / (k + 1e-10), 0, 1)
        return da * h + db * (1 - h) - k * h * (1 - h)
    return sdf


def smooth_subtraction(a: SDFFunc, b: SDFFunc, k: float = 0.1) -> SDFFunc:
    """Smooth subtraction with blending factor k."""
    def sdf(x, y):
        da = a(x, y)
        db = -b(x, y)
        h = np.clip(0.5 - 0.5 * (db + da) / (k + 1e-10), 0, 1)
        return da * (1 - h) + db * h + k * h * (1 - h)
    return sdf


def translate(f: SDFFunc, tx: float, ty: float) -> SDFFunc:
    """Translate an SDF."""
    def sdf(x, y):
        return f(x - tx, y - ty)
    return sdf


def rotate(f: SDFFunc, angle: float) -> SDFFunc:
    """Rotate an SDF by angle (radians) around origin."""
    c, s = np.cos(angle), np.sin(angle)
    def sdf(x, y):
        rx = c * x + s * y
        ry = -s * x + c * y
        return f(rx, ry)
    return sdf


def scale(f: SDFFunc, sx: float, sy: float | None = None) -> SDFFunc:
    """Scale an SDF (uniform if sy is None)."""
    if sy is None:
        sy = sx
    def sdf(x, y):
        return f(x / sx, y / sy) * min(sx, sy)
    return sdf


def repeat(f: SDFFunc, period_x: float, period_y: float) -> SDFFunc:
    """Tile an SDF with the given period."""
    def sdf(x, y):
        rx = np.mod(x + period_x / 2, period_x) - period_x / 2
        ry = np.mod(y + period_y / 2, period_y) - period_y / 2
        return f(rx, ry)
    return sdf
