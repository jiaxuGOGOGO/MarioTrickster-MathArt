"""Gap B2 — Scene-Aware Distance Sensor via SDF Terrain Queries.

SESSION-048: Research-grounded implementation of terrain-aware distance
sensing that replaces flat-ground assumptions with SDF environment rays.

Research foundations:
  1. **Simon Clavet — Motion Matching (GDC 2016)**:
     Trajectory prediction as a spring-damper on desired velocity.  The entity
     (character centre-of-mass) is clamped to ≤15 cm around the simulated
     point.  Obstacle prediction causes the character to *react* to terrain
     before contact — the key insight we adopt for SDF-based ground sensing.

  2. **UE5 Distance Matching (Laurent Delayen / Paragon)**:
     Animation sequences driven by a *distance variable* rather than linear
     time.  A Distance Curve baked per clip maps normalised playback position
     to accumulated root-motion distance.  For landing, the engine traces a
     ray downward, obtains distance-to-ground D, and advances the fall
     animation to the frame whose Distance Curve value matches D — ensuring
     the feet touch down at exactly the right pose.

  3. **Time-to-Contact (TTC) — Perceptual Science**:
     TTC = D / |v|  where D is distance-to-surface and v is approach velocity.
     In our system, D comes from ``Terrain_SDF(foot_x, foot_y)`` and v from
     the character's current downward velocity.  The Transient Phase progress
     is then bound directly to TTC so that phase reaches 1.0 at the exact
     frame of ground contact, regardless of terrain shape.

  4. **Environment-aware Motion Matching (Pontón et al., SIGGRAPH 2025)**:
     2D ellipse collision proxies for body shape, environment features
     integrated into the Motion Matching cost function.  We adapt the concept
     of *environment features as penalisation factors* to our SDF terrain
     representation.

  5. **Falling and Landing Motion Control (Ha et al., SIGGRAPH Asia 2012)**:
     Airborne phase + landing phase decomposition.  The airborne phase
     optimises moment of inertia to meet ideal landing angle; the landing
     phase distributes impact over multiple body parts.  We mirror this
     two-phase structure in our TTC-driven transient phase.

Architecture:
    ┌──────────────────────────────────────────────────────────────────────┐
    │  TerrainSDF                                                          │
    │  ├─ Composable terrain from SDF primitives (flat, slope, steps, …)  │
    │  ├─ query(x, y) → signed distance to terrain surface                │
    │  └─ gradient(x, y) → surface normal at query point                  │
    ├──────────────────────────────────────────────────────────────────────┤
    │  TerrainRaySensor                                                    │
    │  ├─ cast_down(foot_x, foot_y) → absolute distance to ground         │
    │  ├─ cast_ray(origin, direction) → hit distance along ray            │
    │  └─ surface_normal(x, y) → terrain normal at closest surface point  │
    ├──────────────────────────────────────────────────────────────────────┤
    │  TTCPredictor                                                        │
    │  ├─ compute_ttc(distance, velocity) → time-to-contact               │
    │  ├─ ttc_to_phase(ttc, reference_ttc) → normalised [0, 1] phase     │
    │  └─ phase_schedule(ttc) → brace/landing preparation signals         │
    ├──────────────────────────────────────────────────────────────────────┤
    │  SceneAwareDistancePhase (replaces flat-ground fall_distance_phase)  │
    │  ├─ Terrain_SDF query at foot position                               │
    │  ├─ TTC computation from velocity + SDF distance                     │
    │  ├─ Transient phase bound to TTC (phase=1.0 at contact)             │
    │  └─ Full UMR metadata emission                                       │
    ├──────────────────────────────────────────────────────────────────────┤
    │  SceneAwareFallFrame (UMR-native frame generator)                    │
    │  ├─ Replaces phase_driven_fall_frame when terrain is available       │
    │  ├─ Pose driven by TTC-bound phase                                   │
    │  └─ Emits terrain_sensor metadata in UMR frame                       │
    └──────────────────────────────────────────────────────────────────────┘

Usage::

    from mathart.animation.terrain_sensor import (
        TerrainSDF, TerrainRaySensor, TTCPredictor,
        scene_aware_distance_phase, scene_aware_fall_frame,
        create_flat_terrain, create_step_terrain, create_slope_terrain,
    )

    # Build a terrain from SDF primitives
    terrain = create_step_terrain(step_x=0.5, step_height=0.15)

    # Query distance at a foot position
    sensor = TerrainRaySensor(terrain)
    distance = sensor.cast_down(foot_x=0.3, foot_y=0.4)

    # Compute TTC and phase
    predictor = TTCPredictor()
    ttc = predictor.compute_ttc(distance=distance, velocity_y=-2.0)
    phase_info = scene_aware_distance_phase(
        root_x=0.3, root_y=0.4, velocity_y=-2.0, terrain=terrain,
    )
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Optional, Any

import numpy as np

from .curves import ease_in_out
from .unified_motion import (
    PhaseState,
    MotionContactState,
    MotionRootTransform,
    UnifiedMotionFrame,
    pose_to_umr,
)

# ── Type aliases ──────────────────────────────────────────────────────────────

SDFFunc = Callable[[np.ndarray, np.ndarray], np.ndarray]


# ── Utility ───────────────────────────────────────────────────────────────────

def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, float(v)))


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if abs(b) > 1e-12 else default


# ══════════════════════════════════════════════════════════════════════════════
# 1. TerrainSDF — composable SDF terrain descriptions
# ══════════════════════════════════════════════════════════════════════════════


class TerrainSDF:
    """Composable SDF terrain that can be queried at any (x, y) point.

    The terrain SDF follows the standard convention:
      - distance < 0 → inside terrain (underground)
      - distance = 0 → on the surface
      - distance > 0 → above terrain (in air)

    For a 2D side-scroller, x is horizontal position and y is vertical
    (height).  The terrain surface is the zero-level set of the SDF.
    """

    def __init__(self, sdf_func: SDFFunc, name: str = "terrain"):
        self._sdf = sdf_func
        self.name = name

    def query(self, x: float, y: float) -> float:
        """Return signed distance from point (x, y) to terrain surface.

        Positive = above ground, negative = underground, zero = on surface.
        """
        xa = np.array([x], dtype=np.float64)
        ya = np.array([y], dtype=np.float64)
        return float(self._sdf(xa, ya)[0])

    def query_batch(self, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        """Batch query for multiple points."""
        return self._sdf(np.asarray(x, dtype=np.float64),
                         np.asarray(y, dtype=np.float64))

    def gradient(self, x: float, y: float, eps: float = 1e-4) -> tuple[float, float]:
        """Compute the SDF gradient (≈ surface normal) via central differences.

        Returns (gx, gy) — the unnormalised gradient vector.
        """
        xa = np.array([x], dtype=np.float64)
        ya = np.array([y], dtype=np.float64)
        dx_pos = float(self._sdf(xa + eps, ya)[0])
        dx_neg = float(self._sdf(xa - eps, ya)[0])
        dy_pos = float(self._sdf(xa, ya + eps)[0])
        dy_neg = float(self._sdf(xa, ya - eps)[0])
        gx = (dx_pos - dx_neg) / (2.0 * eps)
        gy = (dy_pos - dy_neg) / (2.0 * eps)
        return (gx, gy)

    def surface_normal(self, x: float, y: float, eps: float = 1e-4) -> tuple[float, float]:
        """Normalised surface normal at (x, y)."""
        gx, gy = self.gradient(x, y, eps)
        length = math.sqrt(gx * gx + gy * gy)
        if length < 1e-12:
            return (0.0, 1.0)  # default: upward normal
        return (gx / length, gy / length)

    def compose_union(self, other: "TerrainSDF", name: str | None = None) -> "TerrainSDF":
        """Union of two terrains (closest surface wins)."""
        a, b = self._sdf, other._sdf
        def combined(x: np.ndarray, y: np.ndarray) -> np.ndarray:
            return np.minimum(a(x, y), b(x, y))
        return TerrainSDF(combined, name=name or f"{self.name}+{other.name}")

    def compose_smooth_union(self, other: "TerrainSDF", k: float = 0.05,
                              name: str | None = None) -> "TerrainSDF":
        """Smooth union of two terrains for organic blending."""
        a, b = self._sdf, other._sdf
        def combined(x: np.ndarray, y: np.ndarray) -> np.ndarray:
            da, db = a(x, y), b(x, y)
            h = np.clip(0.5 + 0.5 * (db - da) / (k + 1e-10), 0, 1)
            return da * h + db * (1 - h) - k * h * (1 - h)
        return TerrainSDF(combined, name=name or f"smooth({self.name}+{other.name})")


# ── Terrain factory functions ─────────────────────────────────────────────────

def create_flat_terrain(ground_y: float = 0.0) -> TerrainSDF:
    """Flat horizontal ground at height ground_y.

    SDF(x, y) = y - ground_y  (positive above, negative below).
    """
    def sdf(x: np.ndarray, y: np.ndarray) -> np.ndarray:
        return y - ground_y
    return TerrainSDF(sdf, name=f"flat@{ground_y:.2f}")


def create_slope_terrain(
    start_x: float = 0.0,
    start_y: float = 0.0,
    end_x: float = 1.0,
    end_y: float = 0.3,
    base_y: float = 0.0,
) -> TerrainSDF:
    """Sloped terrain ramp between two points with flat extensions.

    Before start_x: flat at start_y.  After end_x: flat at end_y.
    Between: linear interpolation.
    """
    dx = end_x - start_x
    dy = end_y - start_y

    def sdf(x: np.ndarray, y: np.ndarray) -> np.ndarray:
        t = np.clip((x - start_x) / (dx if abs(dx) > 1e-10 else 1.0), 0.0, 1.0)
        surface_y = start_y + dy * t
        return y - surface_y

    return TerrainSDF(sdf, name=f"slope({start_x:.1f},{start_y:.1f}→{end_x:.1f},{end_y:.1f})")


def create_step_terrain(
    step_x: float = 0.5,
    step_height: float = 0.15,
    base_y: float = 0.0,
    transition_width: float = 0.02,
) -> TerrainSDF:
    """Step terrain: flat at base_y before step_x, flat at base_y+step_height after.

    Uses a smooth transition of width ``transition_width`` to keep the SDF
    well-behaved at the step edge.
    """
    top_y = base_y + step_height

    def sdf(x: np.ndarray, y: np.ndarray) -> np.ndarray:
        # Smooth step function via sigmoid
        t = np.clip((x - step_x) / max(transition_width, 1e-6), -6.0, 6.0)
        blend = 1.0 / (1.0 + np.exp(-t))
        surface_y = base_y * (1.0 - blend) + top_y * blend
        return y - surface_y

    return TerrainSDF(sdf, name=f"step@x={step_x:.2f},h={step_height:.2f}")


def create_sine_terrain(
    amplitude: float = 0.1,
    frequency: float = 2.0,
    base_y: float = 0.0,
    phase_offset: float = 0.0,
) -> TerrainSDF:
    """Sinusoidal terrain for testing on wavy ground."""
    def sdf(x: np.ndarray, y: np.ndarray) -> np.ndarray:
        surface_y = base_y + amplitude * np.sin(2.0 * np.pi * frequency * x + phase_offset)
        return y - surface_y
    return TerrainSDF(sdf, name=f"sine(A={amplitude:.2f},f={frequency:.1f})")


def create_platform_terrain(
    platforms: list[tuple[float, float, float, float]],
    base_y: float = -10.0,
) -> TerrainSDF:
    """Multi-platform terrain from a list of (x_start, x_end, y_top, thickness).

    Each platform is a box SDF.  The union of all platforms forms the terrain.
    ``base_y`` is the default "void" level far below.
    """
    if not platforms:
        return create_flat_terrain(0.0)

    def make_platform_sdf(x0: float, x1: float, y_top: float, thickness: float) -> SDFFunc:
        cx = (x0 + x1) / 2.0
        hw = (x1 - x0) / 2.0
        cy = y_top - thickness / 2.0
        hh = thickness / 2.0
        def sdf(x: np.ndarray, y: np.ndarray) -> np.ndarray:
            dx = np.abs(x - cx) - hw
            dy = np.abs(y - cy) - hh
            outside = np.sqrt(np.maximum(dx, 0.0) ** 2 + np.maximum(dy, 0.0) ** 2)
            inside = np.minimum(np.maximum(dx, dy), 0.0)
            return outside + inside
        return sdf

    platform_sdfs = [make_platform_sdf(*p) for p in platforms]

    def terrain_sdf(x: np.ndarray, y: np.ndarray) -> np.ndarray:
        result = platform_sdfs[0](x, y)
        for psdf in platform_sdfs[1:]:
            result = np.minimum(result, psdf(x, y))
        return result

    return TerrainSDF(terrain_sdf, name=f"platforms({len(platforms)})")


# ══════════════════════════════════════════════════════════════════════════════
# 2. TerrainRaySensor — SDF-based ray casting for distance queries
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class RayHit:
    """Result of a terrain ray cast."""
    distance: float = float("inf")
    hit_point: tuple[float, float] = (0.0, 0.0)
    surface_normal: tuple[float, float] = (0.0, 1.0)
    hit: bool = False
    steps: int = 0


class TerrainRaySensor:
    """SDF-based ray sensor for terrain distance queries.

    Uses sphere tracing (ray marching) along the SDF to find the closest
    surface intersection.  This is the core mechanism that replaces flat-
    ground ``root_y - ground_height`` with true SDF terrain queries.

    Parameters
    ----------
    terrain : TerrainSDF
        The terrain to sense against.
    max_steps : int
        Maximum ray marching steps (default 64).
    min_distance : float
        Surface hit threshold (default 1e-4).
    max_distance : float
        Maximum ray travel distance (default 10.0).
    """

    def __init__(
        self,
        terrain: TerrainSDF,
        max_steps: int = 64,
        min_distance: float = 1e-4,
        max_distance: float = 10.0,
    ):
        self.terrain = terrain
        self.max_steps = max_steps
        self.min_distance = min_distance
        self.max_distance = max_distance

    def cast_ray(
        self,
        origin_x: float,
        origin_y: float,
        dir_x: float = 0.0,
        dir_y: float = -1.0,
    ) -> RayHit:
        """Cast a ray from origin in direction (dir_x, dir_y).

        Uses sphere tracing: at each step, advance by the SDF distance at
        the current position (guaranteed safe step in SDF theory).

        Returns a RayHit with distance, hit point, and surface normal.
        """
        # Normalise direction
        length = math.sqrt(dir_x * dir_x + dir_y * dir_y)
        if length < 1e-12:
            return RayHit()
        ndx, ndy = dir_x / length, dir_y / length

        t = 0.0
        for step in range(self.max_steps):
            px = origin_x + t * ndx
            py = origin_y + t * ndy
            d = self.terrain.query(px, py)

            if d < self.min_distance:
                nx, ny = self.terrain.surface_normal(px, py)
                return RayHit(
                    distance=t,
                    hit_point=(px, py),
                    surface_normal=(nx, ny),
                    hit=True,
                    steps=step + 1,
                )

            t += max(d, self.min_distance * 0.5)
            if t > self.max_distance:
                break

        return RayHit(distance=t, hit=False, steps=self.max_steps)

    def cast_down(self, foot_x: float, foot_y: float) -> float:
        """Cast a ray straight down from (foot_x, foot_y).

        Returns the absolute distance to the terrain surface below.
        This is the primary query for Gap B2: ``Terrain_SDF(foot_x, foot_y)``
        gives the signed distance, but for a downward ray we want the
        positive distance to the surface below.
        """
        # First try the direct SDF query — if positive, we're above ground
        direct_d = self.terrain.query(foot_x, foot_y)
        if direct_d >= 0.0:
            # For simple terrains (flat, slope), the SDF value IS the distance
            # For complex terrains, use ray marching for accuracy
            ray = self.cast_ray(foot_x, foot_y, 0.0, -1.0)
            if ray.hit:
                return ray.distance
            return max(direct_d, 0.0)
        # Below surface — return 0 (already in contact)
        return 0.0

    def cast_down_with_normal(
        self, foot_x: float, foot_y: float
    ) -> tuple[float, tuple[float, float]]:
        """Cast down and also return the surface normal at the hit point."""
        ray = self.cast_ray(foot_x, foot_y, 0.0, -1.0)
        if ray.hit:
            return (ray.distance, ray.surface_normal)
        # Fallback: use direct SDF query
        d = max(self.terrain.query(foot_x, foot_y), 0.0)
        return (d, self.terrain.surface_normal(foot_x, foot_y))

    def multi_point_query(
        self,
        points: list[tuple[float, float]],
    ) -> list[float]:
        """Query distance-to-ground for multiple foot/body points.

        Useful for querying both left and right foot positions simultaneously.
        """
        return [self.cast_down(px, py) for px, py in points]


# ══════════════════════════════════════════════════════════════════════════════
# 3. TTCPredictor — Time-to-Contact computation
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class TTCResult:
    """Time-to-Contact prediction result."""
    ttc: float = float("inf")
    distance: float = 0.0
    velocity: float = 0.0
    phase: float = 0.0
    is_approaching: bool = False
    is_contact: bool = False
    brace_signal: float = 0.0
    landing_preparation: float = 0.0
    confidence: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ttc": self.ttc,
            "distance": self.distance,
            "velocity": self.velocity,
            "phase": self.phase,
            "is_approaching": self.is_approaching,
            "is_contact": self.is_contact,
            "brace_signal": self.brace_signal,
            "landing_preparation": self.landing_preparation,
            "confidence": self.confidence,
        }


class TTCPredictor:
    """Time-to-Contact predictor for landing phase control.

    Computes TTC = D / |v| where D is the SDF distance to terrain and v is
    the approach velocity.  The predictor also generates phase signals that
    bind the Transient Phase progress directly to TTC.

    Parameters
    ----------
    gravity : float
        Gravitational acceleration (default 9.81 m/s²).  Used to refine
        TTC when the character is in free-fall (v increases over time).
    contact_threshold : float
        Distance below which contact is considered established (default 0.01).
    max_ttc : float
        Maximum TTC value to prevent division-by-zero artifacts (default 5.0s).
    """

    def __init__(
        self,
        gravity: float = 9.81,
        contact_threshold: float = 0.01,
        max_ttc: float = 5.0,
    ):
        self.gravity = gravity
        self.contact_threshold = contact_threshold
        self.max_ttc = max_ttc

    def compute_ttc(
        self,
        distance: float,
        velocity_y: float,
        *,
        use_gravity: bool = True,
    ) -> TTCResult:
        """Compute time-to-contact from distance and vertical velocity.

        Parameters
        ----------
        distance : float
            Absolute distance to terrain surface (positive = above ground).
        velocity_y : float
            Vertical velocity (negative = falling downward).
        use_gravity : bool
            If True, solve the quadratic equation for free-fall:
            D = |v₀|·t + ½g·t²  →  t = (-|v₀| + √(v₀² + 2gD)) / g
            This gives a more accurate TTC than the simple D/|v| formula.

        Returns
        -------
        TTCResult
            Full TTC prediction with phase signals.
        """
        d = max(float(distance), 0.0)
        vy = float(velocity_y)

        # Contact check
        if d <= self.contact_threshold:
            return TTCResult(
                ttc=0.0,
                distance=d,
                velocity=vy,
                phase=1.0,
                is_approaching=False,
                is_contact=True,
                brace_signal=1.0,
                landing_preparation=1.0,
                confidence=1.0,
            )

        # Not approaching if moving upward
        is_approaching = vy < -1e-6

        if not is_approaching:
            return TTCResult(
                ttc=self.max_ttc,
                distance=d,
                velocity=vy,
                phase=0.0,
                is_approaching=False,
                is_contact=False,
                brace_signal=0.0,
                landing_preparation=0.0,
                confidence=0.5,
            )

        speed = abs(vy)

        if use_gravity and self.gravity > 0:
            # Quadratic solution: D = v₀·t + ½g·t²
            # ½g·t² + v₀·t - D = 0
            # t = (-v₀ + √(v₀² + 2gD)) / g
            discriminant = speed * speed + 2.0 * self.gravity * d
            if discriminant >= 0:
                ttc = (-speed + math.sqrt(discriminant)) / self.gravity
                # The negative root gives the physical solution for falling
                # Actually for falling: v₀ is the current downward speed,
                # distance decreases as: D(t) = D₀ - v₀·t - ½g·t²
                # Setting D(t) = 0: ½g·t² + v₀·t - D₀ = 0
                # t = (-v₀ + √(v₀² + 2gD₀)) / g
                ttc = (-speed + math.sqrt(discriminant)) / self.gravity
                ttc = max(ttc, 0.0)
            else:
                ttc = d / speed  # fallback
        else:
            # Simple linear TTC
            ttc = d / speed

        ttc = min(ttc, self.max_ttc)

        # Phase: maps TTC to [0, 1] where 1.0 = contact
        # We use a reference TTC (the initial TTC at fall start) to normalise.
        # Since we don't have the reference here, we use a heuristic:
        # phase = 1 - (ttc / max_ttc) but clamped and eased.
        # The caller (scene_aware_distance_phase) provides the reference.
        raw_phase = _clamp01(1.0 - (ttc / self.max_ttc))

        # Brace signal: ramps up in the last ~0.3s before contact
        brace_ttc_threshold = 0.3
        brace_signal = _clamp01(1.0 - (ttc / brace_ttc_threshold)) if ttc < brace_ttc_threshold else 0.0

        # Landing preparation: smooth ramp starting at ~0.5s
        prep_ttc_threshold = 0.5
        landing_prep = ease_in_out(_clamp01(1.0 - (ttc / prep_ttc_threshold))) if ttc < prep_ttc_threshold else 0.0

        # Confidence: higher when velocity is stable and distance is moderate
        confidence = _clamp01(min(speed / 0.5, 1.0) * min(d / 0.05, 1.0))

        return TTCResult(
            ttc=ttc,
            distance=d,
            velocity=vy,
            phase=raw_phase,
            is_approaching=True,
            is_contact=False,
            brace_signal=brace_signal,
            landing_preparation=landing_prep,
            confidence=confidence,
        )

    def ttc_to_phase(
        self,
        ttc: float,
        reference_ttc: float,
    ) -> float:
        """Convert TTC to normalised phase [0, 1] given a reference TTC.

        phase = 1.0 - (current_ttc / reference_ttc)

        This ensures phase reaches exactly 1.0 when TTC reaches 0 (contact),
        regardless of the terrain shape or fall height.
        """
        if reference_ttc <= 0:
            return 1.0
        return _clamp01(1.0 - (ttc / reference_ttc))


# ══════════════════════════════════════════════════════════════════════════════
# 4. Scene-Aware Distance Phase — replaces flat-ground fall_distance_phase
# ══════════════════════════════════════════════════════════════════════════════


def scene_aware_distance_phase(
    *,
    root_x: float,
    root_y: float,
    velocity_y: float = 0.0,
    terrain: TerrainSDF | None = None,
    ground_height: float = 0.0,
    fall_reference_height: float | None = None,
    fall_reference_ttc: float | None = None,
    foot_offset_y: float = 0.0,
    gravity: float = 9.81,
) -> dict[str, float | bool | str]:
    """Scene-aware distance phase that uses SDF terrain queries + TTC.

    This is the Gap B2 upgrade to ``fall_distance_phase``.  When a TerrainSDF
    is provided, it queries the terrain at the foot position to get the true
    distance-to-ground, then computes TTC to bind the transient phase.

    When no terrain is provided, falls back to the flat-ground calculation
    (backward compatible with existing code).

    Parameters
    ----------
    root_x, root_y : float
        Character root position.
    velocity_y : float
        Vertical velocity (negative = falling).
    terrain : TerrainSDF or None
        SDF terrain to query.  If None, uses flat ground at ``ground_height``.
    ground_height : float
        Flat ground height (used when terrain is None).
    fall_reference_height : float or None
        Reference height for phase normalisation (legacy mode).
    fall_reference_ttc : float or None
        Reference TTC for phase normalisation (TTC mode).
    foot_offset_y : float
        Vertical offset from root to foot (default 0.0).
    gravity : float
        Gravitational acceleration.

    Returns
    -------
    dict
        Phase metrics compatible with the existing fall_distance_phase contract,
        plus additional terrain_sensor metadata.
    """
    foot_x = float(root_x)
    foot_y = float(root_y) - float(foot_offset_y)

    # ── Distance query ──
    if terrain is not None:
        sensor = TerrainRaySensor(terrain)
        distance_to_ground, surface_normal = sensor.cast_down_with_normal(foot_x, foot_y)
        terrain_name = terrain.name
        query_mode = "sdf_terrain"
    else:
        distance_to_ground = max(foot_y - float(ground_height), 0.0)
        surface_normal = (0.0, 1.0)
        terrain_name = f"flat@{ground_height:.2f}"
        query_mode = "flat_ground_fallback"

    # ── TTC computation ──
    predictor = TTCPredictor(gravity=gravity)
    ttc_result = predictor.compute_ttc(distance_to_ground, velocity_y)

    # ── Phase computation ──
    # If we have a reference TTC, use TTC-based phase (preferred)
    if fall_reference_ttc is not None and fall_reference_ttc > 0:
        phase = predictor.ttc_to_phase(ttc_result.ttc, fall_reference_ttc)
        phase_source = "ttc_bound"
    elif ttc_result.is_contact:
        phase = 1.0
        phase_source = "contact_detected"
    elif ttc_result.is_approaching and ttc_result.ttc < predictor.max_ttc:
        # Auto-estimate reference TTC from current state
        # Use the current TTC as reference if no explicit reference given
        ref_height = float(fall_reference_height) if fall_reference_height is not None else max(distance_to_ground, 0.22)
        ref_height = max(ref_height, 1e-4)
        phase = _clamp01(1.0 - (distance_to_ground / ref_height))
        phase_source = "distance_matching_with_ttc"
    else:
        # Not falling — phase stays at 0
        ref_height = float(fall_reference_height) if fall_reference_height is not None else max(distance_to_ground, 0.22)
        ref_height = max(ref_height, 1e-4)
        phase = _clamp01(1.0 - (distance_to_ground / ref_height))
        phase_source = "distance_matching"

    # ── Landing window ──
    landing_window_threshold = max(0.03, distance_to_ground * 0.15) if distance_to_ground > 0 else 0.03
    landing_window = bool(distance_to_ground <= landing_window_threshold or phase >= 0.82)

    # ── Landing preparation (eased) ──
    landing_preparation = ease_in_out(_clamp01((phase - 0.42) / 0.58))

    return {
        # ── Core phase contract (backward compatible) ──
        "phase": float(phase),
        "phase_kind": "scene_aware_distance",
        "phase_source": phase_source,
        "distance_to_ground": float(distance_to_ground),
        "distance_window": float(landing_window_threshold),
        "target_distance": 0.0,
        "ground_height": float(ground_height),
        "fall_reference_height": float(fall_reference_height or max(distance_to_ground, 0.22)),
        "landing_preparation": float(landing_preparation),
        "target_state": "ground_contact",
        "contact_expectation": "landing_window" if landing_window else "airborne",
        "desired_contact_state": "ground_contact",
        "is_landing_window": landing_window,
        "window_signal": landing_window,
        # ── Gap B2 terrain sensor metadata ──
        "terrain_sensor_active": True,
        "terrain_name": terrain_name,
        "terrain_query_mode": query_mode,
        "surface_normal_x": float(surface_normal[0]),
        "surface_normal_y": float(surface_normal[1]),
        # ── Gap B2 TTC metadata ──
        "ttc": float(ttc_result.ttc),
        "ttc_velocity": float(ttc_result.velocity),
        "ttc_is_approaching": bool(ttc_result.is_approaching),
        "ttc_is_contact": bool(ttc_result.is_contact),
        "ttc_brace_signal": float(ttc_result.brace_signal),
        "ttc_landing_preparation": float(ttc_result.landing_preparation),
        "ttc_confidence": float(ttc_result.confidence),
        "ttc_reference": float(fall_reference_ttc) if fall_reference_ttc is not None else float(ttc_result.ttc),
    }


# ══════════════════════════════════════════════════════════════════════════════
# 5. Scene-Aware Fall Pose & Frame — TTC-driven pose generation
# ══════════════════════════════════════════════════════════════════════════════


def scene_aware_fall_pose(
    t: float,
    *,
    root_x: float = 0.0,
    root_y: float = 0.0,
    velocity_y: float = 0.0,
    terrain: TerrainSDF | None = None,
    ground_height: float = 0.0,
    fall_reference_height: float | None = None,
    fall_reference_ttc: float | None = None,
    foot_offset_y: float = 0.0,
    gravity: float = 9.81,
) -> dict[str, float]:
    """Generate a fall pose driven by TTC-bound phase from SDF terrain.

    The pose blends three stages based on the TTC-derived phase:
    1. **Stretch** (phase 0.0–0.4): arms spread, legs extended — airborne
    2. **Brace** (phase 0.4–0.8): knees bend, arms pull in — preparing
    3. **Landing** (phase 0.8–1.0): deep crouch, arms forward — absorbing

    The TTC binding ensures that regardless of terrain shape (flat, slope,
    step, sine wave), the phase reaches 1.0 at the exact moment of contact.
    """
    metrics = scene_aware_distance_phase(
        root_x=root_x,
        root_y=root_y,
        velocity_y=velocity_y,
        terrain=terrain,
        ground_height=ground_height,
        fall_reference_height=fall_reference_height,
        fall_reference_ttc=fall_reference_ttc,
        foot_offset_y=foot_offset_y,
        gravity=gravity,
    )
    phase = float(metrics["phase"])

    # Blend weights
    stretch = 1.0 - phase
    brace = ease_in_out(_clamp01((phase - 0.40) / 0.60))
    landing = ease_in_out(_clamp01((phase - 0.78) / 0.22))

    # TTC-driven brace enhancement: when TTC < 0.3s, intensify brace
    ttc_brace = float(metrics.get("ttc_brace_signal", 0.0))
    brace_boost = ttc_brace * 0.15

    # Surface normal tilt: adjust pose based on terrain slope
    nx = float(metrics.get("surface_normal_x", 0.0))
    slope_lean = nx * 0.08  # lean into the slope

    return {
        "spine": -0.05 * stretch - 0.18 * landing + slope_lean,
        "chest": -0.02 * stretch + 0.05 * landing,
        "head": 0.03 * stretch - 0.06 * landing,
        "l_shoulder": -0.60 * stretch + 0.22 * landing,
        "r_shoulder": -0.55 * stretch + 0.26 * landing,
        "l_elbow": 0.20 + 0.10 * landing + 0.05 * ttc_brace,
        "r_elbow": 0.22 + 0.12 * landing + 0.05 * ttc_brace,
        "l_hip": 0.10 * stretch - 0.22 * landing - brace_boost,
        "r_hip": -0.10 * stretch - 0.22 * landing - brace_boost,
        "l_knee": -0.16 * stretch - 0.54 * brace - 0.08 * ttc_brace,
        "r_knee": -0.12 * stretch - 0.54 * brace - 0.08 * ttc_brace,
        "l_foot": -0.05 * stretch + 0.06 * landing + 0.04 * ttc_brace,
        "r_foot": -0.03 * stretch + 0.06 * landing + 0.04 * ttc_brace,
    }


def scene_aware_fall_frame(
    t: float,
    **kwargs: Any,
) -> UnifiedMotionFrame:
    """UMR-native fall frame driven by SDF terrain + TTC.

    Drop-in replacement for ``phase_driven_fall_frame`` when terrain sensing
    is available.  Falls back gracefully to flat-ground when no terrain is
    provided.
    """
    time = float(kwargs.pop("time", 0.0))
    frame_index = int(kwargs.pop("frame_index", 0))
    source_state = str(kwargs.pop("source_state", "fall"))
    root_x = float(kwargs.pop("root_x", 0.0))
    root_y = float(kwargs.pop("root_y", 0.0))
    root_rotation = float(kwargs.pop("root_rotation", 0.0))
    root_velocity_x = float(kwargs.pop("root_velocity_x", 0.0))
    root_velocity_y = float(kwargs.pop("root_velocity_y", 0.0))
    ground_height = float(kwargs.pop("ground_height", 0.0))
    fall_reference_height = kwargs.pop("fall_reference_height", None)
    fall_reference_ttc = kwargs.pop("fall_reference_ttc", None)
    terrain = kwargs.pop("terrain", None)
    foot_offset_y = float(kwargs.pop("foot_offset_y", 0.0))
    gravity = float(kwargs.pop("gravity", 9.81))

    metrics = scene_aware_distance_phase(
        root_x=root_x,
        root_y=root_y,
        velocity_y=root_velocity_y,
        terrain=terrain,
        ground_height=ground_height,
        fall_reference_height=fall_reference_height,
        fall_reference_ttc=fall_reference_ttc,
        foot_offset_y=foot_offset_y,
        gravity=gravity,
    )

    pose = scene_aware_fall_pose(
        t,
        root_x=root_x,
        root_y=root_y,
        velocity_y=root_velocity_y,
        terrain=terrain,
        ground_height=ground_height,
        fall_reference_height=fall_reference_height,
        fall_reference_ttc=fall_reference_ttc,
        foot_offset_y=foot_offset_y,
        gravity=gravity,
    )

    landing_contact = bool(float(metrics["distance_to_ground"]) <= 1e-3)

    return pose_to_umr(
        pose,
        time=time,
        phase=float(metrics["phase"]),
        frame_index=frame_index,
        source_state=source_state,
        root_transform=MotionRootTransform(
            x=root_x,
            y=root_y,
            rotation=root_rotation,
            velocity_x=root_velocity_x,
            velocity_y=root_velocity_y,
            angular_velocity=0.0,
        ),
        contact_tags=MotionContactState(left_foot=landing_contact, right_foot=landing_contact),
        metadata={
            "generator": "scene_aware_fall_ttc_distance_matching",
            **{k: v for k, v in metrics.items() if not isinstance(v, (dict, list))},
        },
    )


# ══════════════════════════════════════════════════════════════════════════════
# 6. Scene-Aware Jump Phase — TTC for ascending + terrain-aware apex
# ══════════════════════════════════════════════════════════════════════════════


def scene_aware_jump_distance_phase(
    *,
    root_x: float,
    root_y: float,
    root_velocity_y: float = 0.0,
    apex_height: float | None = None,
    terrain: TerrainSDF | None = None,
    ground_height: float = 0.0,
    gravity: float = 9.81,
) -> dict[str, float | bool | str]:
    """Scene-aware jump phase with terrain-aware apex and landing prediction.

    During ascent: standard distance-to-apex phase.
    At/past apex: switches to scene_aware_distance_phase for descent.
    """
    from .phase_driven import jump_distance_phase, _resolve_apex_height

    # Determine if ascending or descending
    is_ascending = root_velocity_y > 0.01

    if is_ascending:
        # Use standard jump distance phase for ascent
        result = jump_distance_phase(
            root_y=root_y,
            root_velocity_y=root_velocity_y,
            apex_height=apex_height,
        )
        result["terrain_sensor_active"] = terrain is not None
        result["terrain_name"] = terrain.name if terrain else f"flat@{ground_height:.2f}"
        return result
    else:
        # Descending — use scene-aware distance phase
        fall_metrics = scene_aware_distance_phase(
            root_x=root_x,
            root_y=root_y,
            velocity_y=root_velocity_y,
            terrain=terrain,
            ground_height=ground_height,
        )
        # Remap: jump phase during descent starts at 0.5 (apex) and goes to 1.0
        fall_phase = float(fall_metrics["phase"])
        combined_phase = 0.5 + 0.5 * fall_phase
        fall_metrics["phase"] = combined_phase
        fall_metrics["phase_kind"] = "scene_aware_jump_descent"
        return fall_metrics


# ══════════════════════════════════════════════════════════════════════════════
# 7. Terrain Sensor Diagnostics — for evolution bridge integration
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class TerrainSensorDiagnostics:
    """Diagnostics from a terrain sensor evaluation cycle."""
    cycle_id: int = 0
    frame_count: int = 0
    terrain_name: str = ""
    mean_distance_error: float = 0.0
    max_distance_error: float = 0.0
    mean_ttc_error: float = 0.0
    phase_at_contact: float = 0.0
    contact_frame_index: int = -1
    phase_monotonic: bool = True
    ttc_decreasing: bool = True
    pass_gate: bool = False
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "frame_count": self.frame_count,
            "terrain_name": self.terrain_name,
            "mean_distance_error": self.mean_distance_error,
            "max_distance_error": self.max_distance_error,
            "mean_ttc_error": self.mean_ttc_error,
            "phase_at_contact": self.phase_at_contact,
            "contact_frame_index": self.contact_frame_index,
            "phase_monotonic": self.phase_monotonic,
            "ttc_decreasing": self.ttc_decreasing,
            "pass_gate": self.pass_gate,
            "timestamp": self.timestamp,
        }


def evaluate_terrain_sensor_accuracy(
    terrain: TerrainSDF,
    fall_trajectory: list[dict[str, float]],
    *,
    gravity: float = 9.81,
    phase_at_contact_threshold: float = 0.95,
) -> TerrainSensorDiagnostics:
    """Evaluate terrain sensor accuracy over a simulated fall trajectory.

    Parameters
    ----------
    terrain : TerrainSDF
        The terrain to evaluate against.
    fall_trajectory : list[dict]
        List of frames with keys: root_x, root_y, velocity_y, expected_distance.
    gravity : float
        Gravitational acceleration.
    phase_at_contact_threshold : float
        Minimum phase value at contact frame for pass gate.

    Returns
    -------
    TerrainSensorDiagnostics
        Evaluation results.
    """
    from datetime import datetime, timezone

    diag = TerrainSensorDiagnostics(
        frame_count=len(fall_trajectory),
        terrain_name=terrain.name,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    if not fall_trajectory:
        return diag

    sensor = TerrainRaySensor(terrain)
    predictor = TTCPredictor(gravity=gravity)

    distance_errors: list[float] = []
    ttc_errors: list[float] = []
    phases: list[float] = []
    ttcs: list[float] = []

    # Compute reference TTC from first frame
    first = fall_trajectory[0]
    first_d = sensor.cast_down(first["root_x"], first["root_y"])
    first_ttc = predictor.compute_ttc(first_d, first.get("velocity_y", 0.0))
    reference_ttc = first_ttc.ttc

    for i, frame in enumerate(fall_trajectory):
        rx, ry = frame["root_x"], frame["root_y"]
        vy = frame.get("velocity_y", 0.0)
        expected_d = frame.get("expected_distance", None)

        actual_d = sensor.cast_down(rx, ry)
        ttc_result = predictor.compute_ttc(actual_d, vy)
        phase = predictor.ttc_to_phase(ttc_result.ttc, reference_ttc) if reference_ttc > 0 else 0.0

        if expected_d is not None:
            distance_errors.append(abs(actual_d - expected_d))

        phases.append(phase)
        ttcs.append(ttc_result.ttc)

        # Check for contact
        if actual_d <= 0.01 and diag.contact_frame_index < 0:
            diag.contact_frame_index = i
            diag.phase_at_contact = phase

    if distance_errors:
        diag.mean_distance_error = float(np.mean(distance_errors))
        diag.max_distance_error = float(np.max(distance_errors))

    # Check phase monotonicity (should increase during fall)
    if len(phases) >= 2:
        diffs = [phases[i + 1] - phases[i] for i in range(len(phases) - 1)]
        diag.phase_monotonic = all(d >= -0.01 for d in diffs)

    # Check TTC decreasing (should decrease during fall)
    if len(ttcs) >= 2:
        ttc_diffs = [ttcs[i + 1] - ttcs[i] for i in range(len(ttcs) - 1)]
        diag.ttc_decreasing = all(d <= 0.01 for d in ttc_diffs)

    # Pass gate
    diag.pass_gate = (
        diag.phase_monotonic
        and diag.ttc_decreasing
        and (diag.phase_at_contact >= phase_at_contact_threshold or diag.contact_frame_index < 0)
        and diag.mean_distance_error <= 0.05
    )

    return diag


# ══════════════════════════════════════════════════════════════════════════════
# Exports
# ══════════════════════════════════════════════════════════════════════════════

__all__ = [
    # Terrain SDF
    "TerrainSDF",
    "create_flat_terrain",
    "create_slope_terrain",
    "create_step_terrain",
    "create_sine_terrain",
    "create_platform_terrain",
    # Ray sensor
    "TerrainRaySensor",
    "RayHit",
    # TTC predictor
    "TTCPredictor",
    "TTCResult",
    # Scene-aware phase
    "scene_aware_distance_phase",
    "scene_aware_fall_pose",
    "scene_aware_fall_frame",
    "scene_aware_jump_distance_phase",
    # Diagnostics
    "TerrainSensorDiagnostics",
    "evaluate_terrain_sensor_accuracy",
]
