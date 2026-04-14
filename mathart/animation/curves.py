"""
Animation curve functions (easing, spring, oscillation).

Implements the 12 principles of animation mathematically:
  - Ease in/out: slow-in-slow-out for natural motion
  - Spring: follow-through and overlapping action
  - Sine wave: secondary action (breathing, swaying)
  - Bezier: arbitrary custom curves

All functions map t ∈ [0, 1] → value ∈ [0, 1] (or beyond for overshoot).
"""
from __future__ import annotations
import numpy as np


def ease_in_out(t: float | np.ndarray, power: float = 2.0) -> float | np.ndarray:
    """Smooth ease-in-out curve. Power controls sharpness."""
    t = np.asarray(t)
    return np.where(
        t < 0.5,
        0.5 * (2 * t) ** power,
        1 - 0.5 * (2 * (1 - t)) ** power,
    )


def ease_in(t: float | np.ndarray, power: float = 2.0) -> float | np.ndarray:
    """Ease-in curve (slow start)."""
    return np.asarray(t) ** power


def ease_out(t: float | np.ndarray, power: float = 2.0) -> float | np.ndarray:
    """Ease-out curve (slow end)."""
    return 1 - (1 - np.asarray(t)) ** power


def spring(
    t: float | np.ndarray,
    stiffness: float = 10.0,
    damping: float = 3.0,
) -> float | np.ndarray:
    """Damped spring curve for follow-through and overshoot.

    Simulates: x'' + 2*damping*x' + stiffness*x = 0
    with x(0) = 0, target = 1.
    """
    t = np.asarray(t, dtype=np.float64)
    omega = np.sqrt(max(stiffness - damping**2, 0.01))
    return 1 - np.exp(-damping * t) * (np.cos(omega * t) + (damping / omega) * np.sin(omega * t))


def sine_wave(
    t: float | np.ndarray,
    frequency: float = 1.0,
    amplitude: float = 1.0,
    phase: float = 0.0,
) -> float | np.ndarray:
    """Sinusoidal oscillation for breathing, swaying, bobbing."""
    return amplitude * np.sin(2 * np.pi * frequency * np.asarray(t) + phase)


def bezier_curve(
    t: float | np.ndarray,
    p0: float = 0.0,
    p1: float = 0.3,
    p2: float = 0.7,
    p3: float = 1.0,
) -> float | np.ndarray:
    """Cubic Bezier curve for custom easing."""
    t = np.asarray(t)
    u = 1 - t
    return u**3 * p0 + 3 * u**2 * t * p1 + 3 * u * t**2 * p2 + t**3 * p3


def bounce(t: float | np.ndarray, bounces: int = 3) -> float | np.ndarray:
    """Bounce easing for landing impacts."""
    t = np.asarray(t, dtype=np.float64)
    result = np.zeros_like(t)
    for i in range(bounces):
        start = 1 - 1 / (2 ** i)
        end = 1 - 1 / (2 ** (i + 1))
        mask = (t >= start) & (t < end)
        local_t = (t - start) / (end - start)
        height = 1 / (2 ** i)
        result = np.where(mask, height * (1 - (2 * local_t - 1) ** 2), result)
    result = np.where(t >= 1 - 1 / (2 ** bounces), 1.0, result)
    return result


def squash_stretch(
    t: float | np.ndarray,
    squash_amount: float = 0.3,
    stretch_amount: float = 0.2,
) -> tuple[float | np.ndarray, float | np.ndarray]:
    """Squash and stretch with volume conservation.

    Returns (scale_x, scale_y) where scale_x * scale_y ≈ 1.0.

    Distilled from animation principles:
    - Squash: wider + shorter (impact/landing)
    - Stretch: narrower + taller (anticipation/jump)
    """
    t = np.asarray(t)
    # t=0: neutral, t<0.5: stretch, t>0.5: squash
    stretch_y = 1.0 + stretch_amount * np.sin(np.pi * t)
    stretch_x = 1.0 / stretch_y  # Volume conservation
    return stretch_x, stretch_y
