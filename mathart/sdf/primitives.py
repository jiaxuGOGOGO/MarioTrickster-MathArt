"""
2D SDF primitive functions.

Each function returns a callable SDF(x, y) → distance, where:
  - distance < 0: inside the shape
  - distance = 0: on the boundary
  - distance > 0: outside the shape

All functions operate on numpy arrays for batch evaluation.
Coordinates are in normalized space [-1, 1] × [-1, 1].
"""
from __future__ import annotations
from typing import Callable
import numpy as np

SDFFunc = Callable[[np.ndarray, np.ndarray], np.ndarray]


def circle(cx: float = 0, cy: float = 0, r: float = 0.5) -> SDFFunc:
    """Circle SDF centered at (cx, cy) with radius r."""
    def sdf(x: np.ndarray, y: np.ndarray) -> np.ndarray:
        return np.sqrt((x - cx)**2 + (y - cy)**2) - r
    return sdf


def box(cx: float = 0, cy: float = 0, hw: float = 0.5, hh: float = 0.5) -> SDFFunc:
    """Axis-aligned box SDF centered at (cx, cy) with half-widths (hw, hh)."""
    def sdf(x: np.ndarray, y: np.ndarray) -> np.ndarray:
        dx = np.abs(x - cx) - hw
        dy = np.abs(y - cy) - hh
        outside = np.sqrt(np.maximum(dx, 0)**2 + np.maximum(dy, 0)**2)
        inside = np.minimum(np.maximum(dx, dy), 0)
        return outside + inside
    return sdf


def segment(ax: float, ay: float, bx: float, by: float, r: float = 0.05) -> SDFFunc:
    """Line segment SDF from (ax, ay) to (bx, by) with thickness r."""
    def sdf(x: np.ndarray, y: np.ndarray) -> np.ndarray:
        pax = x - ax
        pay = y - ay
        bax = bx - ax
        bay = by - ay
        h = np.clip((pax * bax + pay * bay) / (bax**2 + bay**2 + 1e-10), 0, 1)
        dx = pax - bax * h
        dy = pay - bay * h
        return np.sqrt(dx**2 + dy**2) - r
    return sdf


def triangle(
    x0: float = 0, y0: float = -0.5,
    x1: float = -0.5, y1: float = 0.5,
    x2: float = 0.5, y2: float = 0.5,
) -> SDFFunc:
    """Triangle SDF with vertices (x0,y0), (x1,y1), (x2,y2)."""
    def sdf(x: np.ndarray, y: np.ndarray) -> np.ndarray:
        # Use winding number approach for sign, distance to edges for magnitude
        def edge_dist(ex0, ey0, ex1, ey1):
            dx, dy = ex1 - ex0, ey1 - ey0
            px, py = x - ex0, y - ey0
            t = np.clip((px * dx + py * dy) / (dx**2 + dy**2 + 1e-10), 0, 1)
            return np.sqrt((px - t * dx)**2 + (py - t * dy)**2)

        d0 = edge_dist(x0, y0, x1, y1)
        d1 = edge_dist(x1, y1, x2, y2)
        d2 = edge_dist(x2, y2, x0, y0)
        dist = np.minimum(np.minimum(d0, d1), d2)

        # Sign: negative inside
        def cross2d(ax, ay, bx, by):
            return ax * by - ay * bx

        s0 = np.sign(cross2d(x1 - x0, y1 - y0, x - x0, y - y0))
        s1 = np.sign(cross2d(x2 - x1, y2 - y1, x - x1, y - y1))
        s2 = np.sign(cross2d(x0 - x2, y0 - y2, x - x2, y - y2))
        inside = (s0 > 0) & (s1 > 0) & (s2 > 0) | (s0 < 0) & (s1 < 0) & (s2 < 0)
        return np.where(inside, -dist, dist)

    return sdf


def star(cx: float = 0, cy: float = 0, r_outer: float = 0.5,
         r_inner: float = 0.2, n_points: int = 5) -> SDFFunc:
    """Star polygon SDF with n points."""
    def sdf(x: np.ndarray, y: np.ndarray) -> np.ndarray:
        dx = x - cx
        dy = y - cy
        angle = np.arctan2(dy, dx)
        dist = np.sqrt(dx**2 + dy**2)
        # Sector angle
        sector = 2 * np.pi / n_points
        # Map to first sector
        a = np.mod(angle + sector / 2, sector) - sector / 2
        # Interpolate between inner and outer radius
        t = np.abs(a) / (sector / 2)
        r = r_inner + (r_outer - r_inner) * (1 - t)
        return dist - r
    return sdf


def ring(cx: float = 0, cy: float = 0, r: float = 0.4, thickness: float = 0.1) -> SDFFunc:
    """Ring (annulus) SDF."""
    def sdf(x: np.ndarray, y: np.ndarray) -> np.ndarray:
        d = np.sqrt((x - cx)**2 + (y - cy)**2)
        return np.abs(d - r) - thickness
    return sdf
