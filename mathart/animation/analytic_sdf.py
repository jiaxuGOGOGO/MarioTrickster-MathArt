"""Analytical 2D SDF distance+gradient helpers for industrial material baking.

This module packages exact or piecewise-analytic gradients for the canonical
2D primitives that dominate the repository's character silhouettes. The goal is
not to solve every arbitrary composite field analytically, but to establish a
high-confidence fast path for core industrial rendering primitives while keeping
finite differences as a fallback for unsupported shapes.

The returned gradient is expressed in the primitive's **local** coordinate space.
Callers that transform the query point into local space should transform the
resulting gradient back to world space with the transpose Jacobian of that local
mapping.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np


@dataclass(frozen=True)
class DistanceGradient2D:
    """Distance and local-space gradient for a 2D SDF query."""

    distance: np.ndarray
    gradient_x: np.ndarray
    gradient_y: np.ndarray


SDFGradientFunc2D = Callable[[np.ndarray, np.ndarray], DistanceGradient2D]


def _safe_normalize(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    length = np.sqrt(x * x + y * y)
    safe = np.maximum(length, 1e-12)
    return x / safe, y / safe


def circle_distance_gradient(radius: float) -> SDFGradientFunc2D:
    """Exact distance and gradient for a centered circle."""

    def evaluate(x: np.ndarray, y: np.ndarray) -> DistanceGradient2D:
        dist_to_center = np.sqrt(x * x + y * y)
        gx, gy = _safe_normalize(x, y)
        return DistanceGradient2D(distance=dist_to_center - radius, gradient_x=gx, gradient_y=gy)

    return evaluate


def capsule_distance_gradient(radius: float, length: float) -> SDFGradientFunc2D:
    """Exact distance and gradient for a vertical capsule from (0,0) to (0,-length)."""

    denom = float(length) + 1e-12

    def evaluate(x: np.ndarray, y: np.ndarray) -> DistanceGradient2D:
        t = np.clip(-y / denom, 0.0, 1.0)
        qx = x
        qy = y + t * length
        q_len = np.sqrt(qx * qx + qy * qy)
        gx, gy = _safe_normalize(qx, qy)
        return DistanceGradient2D(distance=q_len - radius, gradient_x=gx, gradient_y=gy)

    return evaluate


def rounded_box_distance_gradient(half_width: float, half_height: float, rounding: float) -> SDFGradientFunc2D:
    """Piecewise-analytic distance and gradient for a centered rounded box.

    The implementation follows the standard SDF decomposition into an outside
    Euclidean corner region and an inside axis-dominant region. The gradient is
    exact away from measure-zero seams and stable enough for lighting and
    material-map baking.
    """

    bx = float(half_width - rounding)
    by = float(half_height - rounding)

    def evaluate(x: np.ndarray, y: np.ndarray) -> DistanceGradient2D:
        ax = np.abs(x)
        ay = np.abs(y)
        sx = np.where(x < 0.0, -1.0, 1.0)
        sy = np.where(y < 0.0, -1.0, 1.0)

        wx = ax - bx
        wy = ay - by
        outside_x = np.maximum(wx, 0.0)
        outside_y = np.maximum(wy, 0.0)
        outside_len = np.sqrt(outside_x * outside_x + outside_y * outside_y)
        outside_distance = outside_len - rounding

        inside_axis_x = wx > wy
        inside_grad_x = np.where(inside_axis_x, sx, 0.0)
        inside_grad_y = np.where(inside_axis_x, 0.0, sy)
        inside_distance = np.minimum(np.maximum(wx, wy), 0.0) - rounding

        gx_out_local, gy_out_local = _safe_normalize(outside_x, outside_y)
        gx_out = sx * gx_out_local
        gy_out = sy * gy_out_local

        use_outside = (wx > 0.0) | (wy > 0.0)
        distance = np.where(use_outside, outside_distance, inside_distance)
        gradient_x = np.where(use_outside, gx_out, inside_grad_x)
        gradient_y = np.where(use_outside, gy_out, inside_grad_y)
        return DistanceGradient2D(distance=distance, gradient_x=gradient_x, gradient_y=gradient_y)

    return evaluate


__all__ = [
    "DistanceGradient2D",
    "SDFGradientFunc2D",
    "circle_distance_gradient",
    "capsule_distance_gradient",
    "rounded_box_distance_gradient",
]
