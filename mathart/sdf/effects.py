"""
Game-specific SDF effects for MarioTrickster.

Maps to LevelThemeProfile elementSprites:
  - spike_sdf → SpikeTrap
  - flame_sdf → FireTrap
  - saw_blade_sdf → SawBlade
  - electric_arc_sdf → generic VFX
  - glow_sdf → Collectible / Checkpoint highlight

Each effect can be rendered as static sprite or animated sprite sheet.
"""
from __future__ import annotations
import numpy as np
from .primitives import SDFFunc, circle, triangle, star, ring, box
from .operations import union, smooth_union, rotate, translate, scale


def spike_sdf(n_spikes: int = 3, height: float = 0.7, base_width: float = 0.8) -> SDFFunc:
    """Ground spike trap SDF.

    Creates a row of triangular spikes on a base platform.
    Maps to: LevelThemeProfile.elementSprites["SpikeTrap"]
    """
    base = box(0, 0.35, base_width / 2, 0.15)
    spikes = []
    spacing = base_width / n_spikes
    for i in range(n_spikes):
        cx = -base_width / 2 + spacing / 2 + i * spacing
        spike = triangle(
            cx, -0.5,                          # tip (top)
            cx - spacing * 0.4, 0.2,           # bottom-left
            cx + spacing * 0.4, 0.2,           # bottom-right
        )
        spikes.append(spike)

    result = base
    for s in spikes:
        result = union(result, s)
    return result


def flame_sdf(t: float = 0.0) -> SDFFunc:
    """Animated flame SDF.

    Maps to: LevelThemeProfile.elementSprites["FireTrap"]
    Time parameter t ∈ [0, 1] for animation cycling.
    """
    def sdf(x: np.ndarray, y: np.ndarray) -> np.ndarray:
        # Base flame shape: elongated circle
        flicker = 0.05 * np.sin(2 * np.pi * t * 3 + x * 5)
        core = np.sqrt(x**2 + ((y + 0.1 - flicker) * 1.5)**2) - 0.25
        # Tip: narrowing upward
        tip_factor = 1.0 + np.maximum(-y - 0.1, 0) * 2.0
        shaped = core * tip_factor
        # Waviness
        wave = 0.04 * np.sin(y * 12 + t * 2 * np.pi * 2)
        return shaped + wave
    return sdf


def saw_blade_sdf(n_teeth: int = 8, t: float = 0.0) -> SDFFunc:
    """Rotating saw blade SDF.

    Maps to: LevelThemeProfile.elementSprites["SawBlade"]
    """
    angle = t * 2 * np.pi  # Full rotation per cycle
    outer = star(0, 0, 0.45, 0.3, n_teeth)
    inner = circle(0, 0, 0.12)
    blade = rotate(outer, angle)
    # Subtract center hole
    def sdf(x, y):
        d_blade = blade(x, y)
        d_hole = inner(x, y)
        return np.maximum(d_blade, -d_hole)
    return sdf


def electric_arc_sdf(t: float = 0.0, segments: int = 6) -> SDFFunc:
    """Electric arc VFX SDF.

    Procedural lightning bolt with time-varying randomness.
    """
    def sdf(x: np.ndarray, y: np.ndarray) -> np.ndarray:
        # Create a jagged path from left to right
        phase = t * 2 * np.pi
        # Base horizontal distance
        d = np.abs(y - 0.1 * np.sin(x * segments + phase)
                   - 0.05 * np.sin(x * segments * 2.7 + phase * 1.3)
                   - 0.03 * np.sin(x * segments * 5.1 + phase * 2.1))
        # Taper at ends
        taper = 1.0 - 0.5 * (np.abs(x) / 0.8) ** 2
        thickness = 0.04 * np.maximum(taper, 0.1)
        return d - thickness
    return sdf


def glow_sdf(t: float = 0.0, pulse_speed: float = 2.0) -> SDFFunc:
    """Pulsing glow effect for collectibles/checkpoints.

    Maps to: Collectible, Checkpoint, GoalZone highlights.
    """
    def sdf(x: np.ndarray, y: np.ndarray) -> np.ndarray:
        pulse = 0.05 * np.sin(2 * np.pi * t * pulse_speed)
        r = 0.25 + pulse
        # Star-like glow
        angle = np.arctan2(y, x)
        ray = 0.03 * np.sin(angle * 4 + t * 2 * np.pi)
        dist = np.sqrt(x**2 + y**2) - r + ray
        return dist
    return sdf
