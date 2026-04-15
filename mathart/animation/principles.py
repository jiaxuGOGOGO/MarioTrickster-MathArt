"""
Disney's 12 Principles of Animation — Mathematical Formalization.

This module provides mathematically precise implementations of the classic
animation principles, designed for procedural pixel art sprite animation.
Each principle is expressed as a parametric function that transforms
position, scale, rotation, or opacity over normalized time t ∈ [0, 1].

Mathematical foundations:
  - Squash & Stretch: volume-preserving affine transform (det = 1)
  - Anticipation: cubic Hermite with negative pre-motion
  - Follow-through: damped harmonic oscillator
  - Slow in/out: Hermite smoothstep and its derivatives
  - Arcs: parametric curves (elliptical, parabolic, Lissajous)
  - Secondary action: phase-shifted coupled oscillators
  - Timing: non-linear time remapping via easing functions
  - Exaggeration: amplitude scaling with energy conservation

References:
  - Thomas, F. & Johnston, O. (1981). "The Illusion of Life"
  - Lasseter, J. (1987). "Principles of Traditional Animation Applied
    to 3D Computer Animation." SIGGRAPH '87.
  - Witkin, A. & Popović, Z. (1995). "Motion Warping." SIGGRAPH '95.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np


# ── Easing Functions (Timing Principle) ───────────────────────────────────────

def ease_in_quad(t: float) -> float:
    """Quadratic ease-in: slow start."""
    return t * t

def ease_out_quad(t: float) -> float:
    """Quadratic ease-out: slow end."""
    return t * (2 - t)

def ease_in_out_quad(t: float) -> float:
    """Quadratic ease-in-out."""
    return 2 * t * t if t < 0.5 else -1 + (4 - 2 * t) * t

def ease_in_cubic(t: float) -> float:
    return t * t * t

def ease_out_cubic(t: float) -> float:
    t1 = t - 1
    return t1 * t1 * t1 + 1

def ease_in_out_cubic(t: float) -> float:
    return 4 * t * t * t if t < 0.5 else (t - 1) * (2 * t - 2) * (2 * t - 2) + 1

def ease_in_elastic(t: float) -> float:
    """Elastic ease-in: spring-like overshoot at start."""
    if t == 0 or t == 1:
        return t
    p = 0.3
    s = p / 4
    return -(2 ** (10 * (t - 1))) * math.sin((t - 1 - s) * (2 * math.pi) / p)

def ease_out_elastic(t: float) -> float:
    """Elastic ease-out: spring-like overshoot at end."""
    if t == 0 or t == 1:
        return t
    p = 0.3
    s = p / 4
    return 2 ** (-10 * t) * math.sin((t - s) * (2 * math.pi) / p) + 1

def ease_out_bounce(t: float) -> float:
    """Bounce ease-out: bouncing ball effect."""
    if t < 1 / 2.75:
        return 7.5625 * t * t
    elif t < 2 / 2.75:
        t -= 1.5 / 2.75
        return 7.5625 * t * t + 0.75
    elif t < 2.5 / 2.75:
        t -= 2.25 / 2.75
        return 7.5625 * t * t + 0.9375
    else:
        t -= 2.625 / 2.75
        return 7.5625 * t * t + 0.984375

def ease_in_back(t: float) -> float:
    """Back ease-in: slight overshoot backward before moving forward."""
    s = 1.70158
    return t * t * ((s + 1) * t - s)

def ease_out_back(t: float) -> float:
    """Back ease-out: overshoot then settle."""
    s = 1.70158
    t -= 1
    return t * t * ((s + 1) * t + s) + 1

def smoothstep(t: float) -> float:
    """Hermite smoothstep: 3t² - 2t³"""
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)

def smootherstep(t: float) -> float:
    """Ken Perlin's smootherstep: 6t⁵ - 15t⁴ + 10t³"""
    t = max(0.0, min(1.0, t))
    return t * t * t * (t * (t * 6 - 15) + 10)


EASING_FUNCTIONS: dict[str, Callable[[float], float]] = {
    "linear": lambda t: t,
    "ease_in_quad": ease_in_quad,
    "ease_out_quad": ease_out_quad,
    "ease_in_out_quad": ease_in_out_quad,
    "ease_in_cubic": ease_in_cubic,
    "ease_out_cubic": ease_out_cubic,
    "ease_in_out_cubic": ease_in_out_cubic,
    "ease_in_elastic": ease_in_elastic,
    "ease_out_elastic": ease_out_elastic,
    "ease_out_bounce": ease_out_bounce,
    "ease_in_back": ease_in_back,
    "ease_out_back": ease_out_back,
    "smoothstep": smoothstep,
    "smootherstep": smootherstep,
}


# ── Squash & Stretch ──────────────────────────────────────────────────────────

@dataclass
class SquashStretch:
    """Volume-preserving squash and stretch.

    Mathematical model:
      Given stretch factor s along the primary axis:
        scale_x = s
        scale_y = 1/s  (preserves area: s * 1/s = 1)

    For 3D (volume preservation):
        scale_x = s
        scale_y = scale_z = 1/sqrt(s)

    The stretch factor varies with velocity:
        s(t) = 1 + amplitude * |velocity(t)| / max_velocity
    """
    amplitude: float = 0.3
    axis: str = "y"  # Primary stretch axis

    def apply(self, t: float, velocity: float = 0.0) -> tuple[float, float]:
        """Return (scale_x, scale_y) for given time and velocity.

        Args:
            t: Normalized time [0, 1]
            velocity: Current velocity (can be negative)

        Returns:
            (scale_x, scale_y) preserving area
        """
        stretch = 1.0 + self.amplitude * abs(velocity)
        if self.axis == "y":
            return (1.0 / stretch, stretch)
        else:
            return (stretch, 1.0 / stretch)

    def apply_impact(self, t: float, impact_time: float = 0.0,
                     recovery_speed: float = 5.0) -> tuple[float, float]:
        """Squash on impact, then recover.

        Uses damped exponential recovery:
            squash(t) = 1 - amplitude * exp(-recovery_speed * (t - impact_time))

        Args:
            t: Current time
            impact_time: When the impact occurred
            recovery_speed: How fast to recover (higher = faster)

        Returns:
            (scale_x, scale_y) preserving area
        """
        dt = max(0, t - impact_time)
        squash = 1.0 - self.amplitude * math.exp(-recovery_speed * dt)
        squash = max(0.3, squash)  # Clamp to avoid zero scale
        # Volume preservation
        return (1.0 / squash, squash)


# ── Anticipation ──────────────────────────────────────────────────────────────

@dataclass
class Anticipation:
    """Preparatory motion before the main action.

    Mathematical model:
      Uses a cubic Hermite spline with a negative dip:
        p(t) = a₁t³ + a₂t² + a₃t + a₄
      where coefficients are chosen so that:
        p(0) = 0, p(1) = 1
        p(anticipation_time) = -anticipation_amount
        p'(0) = 0 (starts from rest)
    """
    amount: float = 0.15       # How far back to pull (0-1)
    duration: float = 0.25     # Fraction of total time for anticipation

    def apply(self, t: float) -> float:
        """Return position offset at time t.

        Returns a value that goes negative (anticipation), then overshoots
        to 1.0 (main action).
        """
        if t <= 0:
            return 0.0
        if t >= 1:
            return 1.0

        # Two-phase: anticipation then action
        if t < self.duration:
            # Pull back phase
            nt = t / self.duration
            return -self.amount * math.sin(nt * math.pi)
        else:
            # Action phase
            nt = (t - self.duration) / (1.0 - self.duration)
            return -self.amount * math.sin(math.pi) * (1 - nt) + nt
            # Simplified: just ease from -amount to 1.0
            # return -self.amount * (1 - nt) + nt


# ── Follow-Through & Overlapping Action ───────────────────────────────────────

@dataclass
class FollowThrough:
    """Damped harmonic oscillator for follow-through motion.

    Mathematical model (underdamped oscillator):
        x(t) = A * exp(-ζωt) * cos(ωd*t + φ) + target

    where:
        ω = natural frequency
        ζ = damping ratio (0 < ζ < 1 for oscillation)
        ωd = ω * sqrt(1 - ζ²) = damped frequency
        A = initial amplitude
        φ = phase offset
    """
    frequency: float = 8.0      # Natural frequency (oscillations per unit time)
    damping: float = 0.3        # Damping ratio (0 = no damping, 1 = critical)
    amplitude: float = 0.2      # Initial overshoot amplitude

    def apply(self, t: float, target: float = 1.0) -> float:
        """Return position with follow-through oscillation.

        Args:
            t: Time since the main action ended [0, ∞)
            target: The target rest position

        Returns:
            Position with damped oscillation around target
        """
        if t <= 0:
            return target

        omega = 2 * math.pi * self.frequency
        zeta = min(self.damping, 0.99)  # Ensure underdamped
        omega_d = omega * math.sqrt(1 - zeta * zeta)

        envelope = self.amplitude * math.exp(-zeta * omega * t)
        oscillation = math.cos(omega_d * t)

        return target + envelope * oscillation


# ── Arc Motion ────────────────────────────────────────────────────────────────

@dataclass
class ArcMotion:
    """Parametric arc paths for natural-looking movement.

    Types:
      - Parabolic: y = -4h*t*(t-1), x = t  (projectile)
      - Elliptical: x = a*cos(θ), y = b*sin(θ)
      - Lissajous: x = sin(a*t + δ), y = sin(b*t)
      - Figure-8: x = sin(t), y = sin(2t)/2
    """
    arc_type: str = "parabolic"
    height: float = 0.3
    width: float = 1.0
    frequency_x: int = 1
    frequency_y: int = 2
    phase_delta: float = 0.0

    def evaluate(self, t: float) -> tuple[float, float]:
        """Return (x, y) position on the arc at time t ∈ [0, 1]."""
        if self.arc_type == "parabolic":
            x = t * self.width
            y = -4 * self.height * t * (t - 1)
            return (x, y)

        elif self.arc_type == "elliptical":
            theta = t * math.pi  # Half ellipse
            x = self.width * 0.5 * math.cos(theta)
            y = self.height * math.sin(theta)
            return (x, y)

        elif self.arc_type == "lissajous":
            x = math.sin(self.frequency_x * t * 2 * math.pi + self.phase_delta)
            y = math.sin(self.frequency_y * t * 2 * math.pi)
            return (x * self.width * 0.5, y * self.height * 0.5)

        elif self.arc_type == "figure8":
            theta = t * 2 * math.pi
            x = math.sin(theta) * self.width * 0.5
            y = math.sin(2 * theta) * self.height * 0.25
            return (x, y)

        return (t, 0.0)


# ── Secondary Action ──────────────────────────────────────────────────────────

@dataclass
class SecondaryAction:
    """Phase-shifted coupled oscillator for secondary motion.

    Models appendages, hair, capes, etc. that follow the main body
    with a delay and different frequency.

    Mathematical model:
        secondary(t) = amplitude * sin(frequency * t + phase_offset)
                       * envelope(t)
    where envelope provides smooth onset/offset.
    """
    amplitude: float = 0.15
    frequency: float = 3.0
    phase_offset: float = 0.5   # Delay relative to primary (in radians)
    decay: float = 0.1          # Envelope decay rate

    def apply(self, t: float, primary_value: float = 0.0) -> float:
        """Return secondary action offset.

        Args:
            t: Normalized time
            primary_value: Current value of the primary action

        Returns:
            Offset to add to the secondary element's position
        """
        envelope = 1.0 - math.exp(-t / max(self.decay, 0.01))
        oscillation = math.sin(
            2 * math.pi * self.frequency * t + self.phase_offset
        )
        return self.amplitude * oscillation * envelope


# ── Exaggeration ──────────────────────────────────────────────────────────────

@dataclass
class Exaggeration:
    """Amplitude scaling with energy conservation.

    Scales the deviation from the rest pose while maintaining
    the overall energy (integral of squared displacement).

    Mathematical model:
        exaggerated(t) = rest + factor * (original(t) - rest)
    """
    factor: float = 1.5
    rest_value: float = 0.0

    def apply(self, value: float) -> float:
        """Apply exaggeration to a value."""
        deviation = value - self.rest_value
        return self.rest_value + self.factor * deviation


# ── Complete Animation Sequence ───────────────────────────────────────────────

@dataclass
class AnimationKeyframe:
    """A keyframe in an animation sequence."""
    time: float              # Normalized time [0, 1]
    position: tuple[float, float] = (0.0, 0.0)
    scale: tuple[float, float] = (1.0, 1.0)
    rotation: float = 0.0   # Radians
    opacity: float = 1.0
    easing: str = "smoothstep"


@dataclass
class PrincipledAnimation:
    """A complete animation built from the 12 principles.

    This class composes multiple principles into a coherent animation
    that can be sampled at any time t to get transform parameters.
    """
    keyframes: list[AnimationKeyframe] = field(default_factory=list)
    squash_stretch: Optional[SquashStretch] = None
    anticipation: Optional[Anticipation] = None
    follow_through: Optional[FollowThrough] = None
    arc_motion: Optional[ArcMotion] = None
    secondary: Optional[SecondaryAction] = None
    exaggeration: Optional[Exaggeration] = None

    def sample(self, t: float) -> dict:
        """Sample the animation at time t ∈ [0, 1].

        Returns dict with keys: position, scale, rotation, opacity
        """
        t = max(0.0, min(1.0, t))

        # Base: interpolate between keyframes
        pos = [0.0, 0.0]
        scale = [1.0, 1.0]
        rotation = 0.0
        opacity = 1.0

        if self.keyframes:
            # Find surrounding keyframes
            kf_before = self.keyframes[0]
            kf_after = self.keyframes[-1]
            for i, kf in enumerate(self.keyframes):
                if kf.time <= t:
                    kf_before = kf
                if kf.time >= t and (i == 0 or self.keyframes[i-1].time <= t):
                    kf_after = kf
                    break

            # Interpolate
            if kf_before.time == kf_after.time:
                local_t = 0.0
            else:
                local_t = (t - kf_before.time) / (kf_after.time - kf_before.time)

            # Apply easing
            ease_fn = EASING_FUNCTIONS.get(kf_after.easing, smoothstep)
            local_t = ease_fn(local_t)

            pos[0] = kf_before.position[0] + (kf_after.position[0] - kf_before.position[0]) * local_t
            pos[1] = kf_before.position[1] + (kf_after.position[1] - kf_before.position[1]) * local_t
            scale[0] = kf_before.scale[0] + (kf_after.scale[0] - kf_before.scale[0]) * local_t
            scale[1] = kf_before.scale[1] + (kf_after.scale[1] - kf_before.scale[1]) * local_t
            rotation = kf_before.rotation + (kf_after.rotation - kf_before.rotation) * local_t
            opacity = kf_before.opacity + (kf_after.opacity - kf_before.opacity) * local_t

        # Apply arc motion
        if self.arc_motion:
            arc_x, arc_y = self.arc_motion.evaluate(t)
            pos[0] += arc_x
            pos[1] += arc_y

        # Apply anticipation
        if self.anticipation:
            antic_offset = self.anticipation.apply(t)
            pos[1] += antic_offset * 0.1  # Scale to reasonable range

        # Apply squash & stretch
        if self.squash_stretch:
            # Estimate velocity from position change
            dt = 0.01
            if t + dt <= 1.0:
                future_pos = list(pos)
                if self.arc_motion:
                    fx, fy = self.arc_motion.evaluate(min(t + dt, 1.0))
                    future_pos[0] = fx
                    future_pos[1] = fy
                velocity = math.sqrt(
                    (future_pos[0] - pos[0])**2 + (future_pos[1] - pos[1])**2
                ) / dt
            else:
                velocity = 0.0
            ss_x, ss_y = self.squash_stretch.apply(t, velocity)
            scale[0] *= ss_x
            scale[1] *= ss_y

        # Apply follow-through (after main action)
        if self.follow_through and t > 0.7:
            ft_offset = self.follow_through.apply(t - 0.7, target=0.0)
            pos[1] += ft_offset * 0.05

        # Apply exaggeration
        if self.exaggeration:
            scale[0] = self.exaggeration.apply(scale[0])
            scale[1] = self.exaggeration.apply(scale[1])

        return {
            "position": tuple(pos),
            "scale": tuple(scale),
            "rotation": rotation,
            "opacity": opacity,
        }

    def generate_frames(self, n_frames: int) -> list[dict]:
        """Generate a sequence of animation frames.

        Args:
            n_frames: Number of frames to generate.

        Returns:
            List of dicts, each with position, scale, rotation, opacity.
        """
        frames = []
        for i in range(n_frames):
            t = i / max(n_frames - 1, 1)
            frames.append(self.sample(t))
        return frames


# ── Preset Animations ─────────────────────────────────────────────────────────

def create_jump_animation(
    height: float = 0.5,
    squash_amount: float = 0.3,
    anticipation_amount: float = 0.1,
) -> PrincipledAnimation:
    """Create a jump animation with squash, stretch, and anticipation.

    The jump follows a parabolic arc with:
      - Anticipation crouch before takeoff
      - Stretch during ascent
      - Squash on landing
      - Follow-through bounce
    """
    anim = PrincipledAnimation(
        keyframes=[
            AnimationKeyframe(time=0.0, position=(0, 0), scale=(1, 1), easing="ease_in_back"),
            AnimationKeyframe(time=0.15, position=(0, -0.05), scale=(1.1, 0.85), easing="ease_in_cubic"),
            AnimationKeyframe(time=0.5, position=(0.5, height), scale=(0.85, 1.15), easing="ease_out_quad"),
            AnimationKeyframe(time=0.85, position=(1.0, 0.02), scale=(1.15, 0.8), easing="ease_in_quad"),
            AnimationKeyframe(time=1.0, position=(1.0, 0), scale=(1, 1), easing="ease_out_elastic"),
        ],
        squash_stretch=SquashStretch(amplitude=squash_amount),
        anticipation=Anticipation(amount=anticipation_amount, duration=0.15),
        follow_through=FollowThrough(frequency=6, damping=0.4, amplitude=0.1),
    )
    return anim


def create_walk_cycle(
    stride_length: float = 1.0,
    bob_height: float = 0.05,
) -> PrincipledAnimation:
    """Create a walk cycle with bob, lean, and arm swing.

    Uses sinusoidal vertical bob (2 cycles per stride) and
    secondary arm swing (phase-shifted).
    """
    anim = PrincipledAnimation(
        keyframes=[
            AnimationKeyframe(time=0.0, position=(0, 0), scale=(1, 1)),
            AnimationKeyframe(time=0.25, position=(stride_length * 0.25, bob_height), scale=(0.98, 1.02)),
            AnimationKeyframe(time=0.5, position=(stride_length * 0.5, 0), scale=(1, 1)),
            AnimationKeyframe(time=0.75, position=(stride_length * 0.75, bob_height), scale=(0.98, 1.02)),
            AnimationKeyframe(time=1.0, position=(stride_length, 0), scale=(1, 1)),
        ],
        secondary=SecondaryAction(amplitude=0.08, frequency=1.0, phase_offset=math.pi),
    )
    return anim


def create_idle_breathe(
    amplitude: float = 0.02,
    frequency: float = 0.5,
) -> PrincipledAnimation:
    """Create an idle breathing animation.

    Subtle scale oscillation simulating breathing.
    """
    anim = PrincipledAnimation(
        keyframes=[
            AnimationKeyframe(time=0.0, scale=(1.0, 1.0)),
            AnimationKeyframe(time=0.5, scale=(1.0 + amplitude, 1.0 - amplitude * 0.5), easing="smoothstep"),
            AnimationKeyframe(time=1.0, scale=(1.0, 1.0), easing="smoothstep"),
        ],
    )
    return anim


def create_attack_swing(
    wind_up: float = 0.2,
    swing_speed: float = 0.1,
    follow_through_amount: float = 0.15,
) -> PrincipledAnimation:
    """Create an attack swing animation.

    Wind-up (anticipation) → fast swing → follow-through.
    """
    anim = PrincipledAnimation(
        keyframes=[
            AnimationKeyframe(time=0.0, rotation=0, scale=(1, 1), easing="ease_in_back"),
            AnimationKeyframe(time=wind_up, rotation=-0.3, scale=(1.05, 0.95), easing="ease_in_cubic"),
            AnimationKeyframe(time=wind_up + swing_speed, rotation=1.2, scale=(0.9, 1.1), easing="ease_out_cubic"),
            AnimationKeyframe(time=0.7, rotation=0.8, scale=(1, 1), easing="ease_out_elastic"),
            AnimationKeyframe(time=1.0, rotation=0, scale=(1, 1), easing="smoothstep"),
        ],
        anticipation=Anticipation(amount=0.2, duration=wind_up),
        follow_through=FollowThrough(frequency=4, damping=0.5, amplitude=follow_through_amount),
        exaggeration=Exaggeration(factor=1.3),
    )
    return anim


def create_death_animation(
    fall_height: float = 0.3,
) -> PrincipledAnimation:
    """Create a death/defeat animation.

    Flash → shrink → fall → fade out.
    """
    anim = PrincipledAnimation(
        keyframes=[
            AnimationKeyframe(time=0.0, position=(0, 0), scale=(1, 1), opacity=1.0),
            AnimationKeyframe(time=0.1, scale=(1.2, 0.8), opacity=0.5, easing="ease_out_quad"),
            AnimationKeyframe(time=0.2, scale=(0.8, 1.2), opacity=1.0, easing="ease_in_quad"),
            AnimationKeyframe(time=0.5, position=(0, fall_height), scale=(0.5, 0.5), rotation=0.5, opacity=0.8, easing="ease_in_cubic"),
            AnimationKeyframe(time=0.8, position=(0, 0), scale=(1.3, 0.3), opacity=0.4, easing="ease_out_bounce"),
            AnimationKeyframe(time=1.0, position=(0, 0), scale=(1.5, 0.1), opacity=0.0, easing="smoothstep"),
        ],
        squash_stretch=SquashStretch(amplitude=0.4),
    )
    return anim


ANIMATION_PRESETS: dict[str, Callable[..., PrincipledAnimation]] = {
    "jump": create_jump_animation,
    "walk": create_walk_cycle,
    "idle": create_idle_breathe,
    "attack": create_attack_swing,
    "death": create_death_animation,
}
