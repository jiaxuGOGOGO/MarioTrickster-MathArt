"""SESSION-039 — Transition Synthesizer: Inertialization & Dead Blending.

Industrial-grade animation transition system distilled from:

  - David Bollo (The Coalition / Microsoft), GDC 2018:
    "Inertialization: High-Performance Animation Transitions in Gears of War"
  - Daniel Holden (Epic Games), 2023:
    "Dead Blending" — extrapolation-based inertialization for Unreal Engine 5.3
  - Simon Clavet (Ubisoft), GDC 2016:
    "Motion Matching and The Road to Next-Gen Animation"

Design Philosophy
-----------------
Traditional crossfade blending evaluates BOTH source and target animations during
the transition window, doubling cost and — critically — destroying foot contact
tags because the blended pose is a weighted average of two unrelated contact states.
This causes the universally hated "foot skating" artifact.

Inertialization solves this by giving the TARGET animation **100% rendering weight
immediately** at the moment of transition. The source animation's residual momentum
is captured as a per-joint offset (position + velocity) and decayed to zero over a
short window (typically 4-8 frames / 0.1-0.3 seconds). The result:

  1. Contact tags from the target are always authoritative — no blended contacts.
  2. Only one animation is evaluated per frame — half the cost of crossfade.
  3. The decay preserves physical inertia — the transition looks momentum-preserving.

This module implements two complementary strategies:

  **Strategy A — Bollo Quintic Inertialization** (Gears of War)
    Uses a quintic polynomial with boundary conditions x(t1)=0, v(t1)=0, a(t1)=0
    to smoothly decay the offset. Mathematically optimal for jerk minimization.

  **Strategy B — Dead Blending** (Daniel Holden / Unreal 5.3)
    Extrapolates the source animation forward using recorded velocity with
    exponential decay, then cross-fades with the target. Simpler, more robust,
    and only requires the current pose + velocity at transition time.

Both strategies operate on ``UnifiedMotionFrame`` data through the UMR bus,
preserving phase, root transform, and contact tag semantics throughout.

Architecture
------------
::

    ┌──────────────────────────────────────────────────────────────────────┐
    │  InertializationChannel                                              │
    │  ├─ capture(source_frame, prev_source_frame)  → store offsets        │
    │  └─ apply(target_frame, elapsed_dt)           → inertialized frame   │
    ├──────────────────────────────────────────────────────────────────────┤
    │  DeadBlendingChannel                                                 │
    │  ├─ capture(source_frame, prev_source_frame)  → store extrapolation  │
    │  └─ apply(target_frame, elapsed_dt)           → blended frame        │
    ├──────────────────────────────────────────────────────────────────────┤
    │  TransitionSynthesizer                                               │
    │  ├─ request_transition(source, target, strategy)                     │
    │  ├─ update(target_frame, dt) → output frame                          │
    │  ├─ is_active → bool                                                 │
    │  └─ get_transition_quality() → quality metrics dict                  │
    └──────────────────────────────────────────────────────────────────────┘

Integration with UMR:
    - Input/output are ``UnifiedMotionFrame`` instances.
    - Contact tags always come from the TARGET frame (never blended).
    - Root transform offsets are inertialized in world space.
    - Joint rotations are inertialized as angular offsets (radians).

Integration with Layer 3:
    - ``get_transition_quality()`` returns metrics consumable by
      ``PhysicsTestBattery`` and ``MotionMatchingEvaluator``.
    - Transition cost (total displacement) feeds into evolution fitness.

References:
    [1] D. Bollo, "Inertialization: High-Performance Animation Transitions
        in Gears of War", GDC 2018.
    [2] D. Holden, "Dead Blending", theorangeduck.com, Feb 2023.
    [3] D. Holden, "Dead Blending Node in Unreal Engine", Aug 2023.
    [4] T. Flash and N. Hogan, "The Coordination of Arm Movements",
        Journal of Neuroscience 5(7), 1985.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

import numpy as np

from .unified_motion import (
    MotionContactState,
    MotionRootTransform,
    UnifiedMotionFrame,
)


# ── Enums ──────────────────────────────────────────────────────────────────


class TransitionStrategy(str, Enum):
    """Available transition blending strategies."""
    INERTIALIZATION = "inertialization"  # Bollo quintic polynomial (GDC 2018)
    DEAD_BLENDING = "dead_blending"      # Holden extrapolation + crossfade (2023)


# ── Quintic Inertialization (Bollo GDC 2018) ──────────────────────────────


def _quintic_decay(
    x0: float,
    v0: float,
    t: float,
    t1: float,
) -> float:
    """Evaluate the Bollo quintic decay polynomial at time *t*.

    Boundary conditions: x(0)=x0, v(0)=v0, x(t1)=0, v(t1)=0, a(t1)=0.
    The initial acceleration a0 is chosen to yield zero jerk at t=t1,
    following Flash & Hogan (1985) minimum-jerk trajectory theory.

    Parameters
    ----------
    x0 : float
        Initial offset (source - target) at transition start.
    v0 : float
        Initial velocity of the offset (source velocity via finite diff).
    t : float
        Elapsed time since transition start.
    t1 : float
        Total transition duration (seconds).

    Returns
    -------
    float
        Decayed offset value at time *t*. Returns 0.0 when t >= t1.
    """
    if t >= t1 or t1 <= 1e-8:
        return 0.0

    # Overshoot clamp (Bollo Idea #4): shorten t1 if velocity opposes offset
    if abs(v0) > 1e-8 and x0 * v0 < 0.0:
        t1_clamped = min(t1, abs(-5.0 * x0 / v0))
        t1 = max(t1_clamped, 1e-4)

    # Initial acceleration for zero-jerk at t1
    a0 = (-8.0 * v0 * t1 - 20.0 * x0) / (t1 * t1) if abs(t1) > 1e-8 else 0.0

    # Quintic coefficients
    t1_2 = t1 * t1
    t1_3 = t1_2 * t1
    t1_4 = t1_3 * t1
    t1_5 = t1_4 * t1

    A = -(a0 * t1_2 + 6.0 * v0 * t1 + 12.0 * x0) / (2.0 * t1_5) if abs(t1_5) > 1e-20 else 0.0
    B = (3.0 * a0 * t1_2 + 16.0 * v0 * t1 + 30.0 * x0) / (2.0 * t1_4) if abs(t1_4) > 1e-16 else 0.0
    C = -(3.0 * a0 * t1_2 + 12.0 * v0 * t1 + 20.0 * x0) / (2.0 * t1_3) if abs(t1_3) > 1e-12 else 0.0

    t2 = t * t
    t3 = t2 * t
    t4 = t3 * t
    t5 = t4 * t

    return A * t5 + B * t4 + C * t3 + 0.5 * a0 * t2 + v0 * t + x0


def _quintic_velocity(
    x0: float,
    v0: float,
    t: float,
    t1: float,
) -> float:
    """First derivative of the Bollo quintic at time *t*."""
    if t >= t1 or t1 <= 1e-8:
        return 0.0

    if abs(v0) > 1e-8 and x0 * v0 < 0.0:
        t1_clamped = min(t1, abs(-5.0 * x0 / v0))
        t1 = max(t1_clamped, 1e-4)

    a0 = (-8.0 * v0 * t1 - 20.0 * x0) / (t1 * t1) if abs(t1) > 1e-8 else 0.0

    t1_2 = t1 * t1
    t1_3 = t1_2 * t1
    t1_4 = t1_3 * t1
    t1_5 = t1_4 * t1

    A = -(a0 * t1_2 + 6.0 * v0 * t1 + 12.0 * x0) / (2.0 * t1_5) if abs(t1_5) > 1e-20 else 0.0
    B = (3.0 * a0 * t1_2 + 16.0 * v0 * t1 + 30.0 * x0) / (2.0 * t1_4) if abs(t1_4) > 1e-16 else 0.0
    C = -(3.0 * a0 * t1_2 + 12.0 * v0 * t1 + 20.0 * x0) / (2.0 * t1_3) if abs(t1_3) > 1e-12 else 0.0

    t2 = t * t
    t3 = t2 * t
    t4 = t3 * t

    return 5.0 * A * t4 + 4.0 * B * t3 + 3.0 * C * t2 + a0 * t + v0


# ── Exponential Decay Helper (Dead Blending) ──────────────────────────────


def _damper_decay_exact(value: float, halflife: float, dt: float) -> float:
    """Exponential decay with configurable half-life (Holden spring-roll-call).

    Parameters
    ----------
    value : float
        Current value to decay.
    halflife : float
        Time (seconds) for the value to halve. Smaller = faster decay.
    dt : float
        Time step.

    Returns
    -------
    float
        Decayed value.
    """
    if halflife <= 1e-8:
        return 0.0
    return value * math.exp(-0.69314718056 * dt / halflife)


def _smoothstep(t: float) -> float:
    """Hermite smoothstep for blend alpha: 3t^2 - 2t^3."""
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


# ── Per-Joint Inertialization Channel ─────────────────────────────────────


@dataclass
class _JointInertState:
    """Per-joint inertialization state for the quintic strategy."""
    x0: float = 0.0
    v0: float = 0.0


@dataclass
class _JointDeadBlendState:
    """Per-joint dead-blending extrapolation state."""
    ext_x: float = 0.0
    ext_v: float = 0.0


# ── Inertialization Channel ───────────────────────────────────────────────


@dataclass
class InertializationChannel:
    """Bollo quintic inertialization for a full skeleton.

    On ``capture()``, records the per-joint offset (source - target) and
    velocity at the moment of transition. On ``apply()``, evaluates the
    quintic decay and adds the residual offset to the target frame.

    The target frame's **contact tags are always preserved** — this is the
    fundamental guarantee that eliminates foot skating during transitions.
    """

    blend_time: float = 0.2  # seconds (4-6 frames at 24fps)
    _elapsed: float = 0.0
    _joint_states: dict[str, _JointInertState] = field(default_factory=dict)
    _root_x: _JointInertState = field(default_factory=_JointInertState)
    _root_y: _JointInertState = field(default_factory=_JointInertState)
    _root_rot: _JointInertState = field(default_factory=_JointInertState)
    _active: bool = False

    def capture(
        self,
        source_frame: UnifiedMotionFrame,
        target_frame: UnifiedMotionFrame,
        prev_source_frame: Optional[UnifiedMotionFrame] = None,
        dt: float = 1.0 / 24.0,
    ) -> None:
        """Capture the offset between source and target at transition time.

        This is the "fire" step of the "fire and forget" pattern from
        Bollo's GDC talk. After this call, the source animation is never
        evaluated again.
        """
        self._elapsed = 0.0
        self._active = True

        # Root transform offsets
        src_rt = source_frame.root_transform
        tgt_rt = target_frame.root_transform

        self._root_x = _JointInertState(
            x0=src_rt.x - tgt_rt.x,
            v0=((src_rt.x - prev_source_frame.root_transform.x) / max(dt, 1e-6)
                if prev_source_frame else src_rt.velocity_x),
        )
        self._root_y = _JointInertState(
            x0=src_rt.y - tgt_rt.y,
            v0=((src_rt.y - prev_source_frame.root_transform.y) / max(dt, 1e-6)
                if prev_source_frame else src_rt.velocity_y),
        )
        self._root_rot = _JointInertState(
            x0=self._wrap_angle(src_rt.rotation - tgt_rt.rotation),
            v0=((self._wrap_angle(src_rt.rotation - prev_source_frame.root_transform.rotation)) / max(dt, 1e-6)
                if prev_source_frame else src_rt.angular_velocity),
        )

        # Per-joint rotation offsets
        self._joint_states = {}
        src_joints = source_frame.joint_local_rotations
        tgt_joints = target_frame.joint_local_rotations
        prev_joints = prev_source_frame.joint_local_rotations if prev_source_frame else {}

        all_joints = set(src_joints.keys()) | set(tgt_joints.keys())
        for joint in all_joints:
            src_val = src_joints.get(joint, 0.0)
            tgt_val = tgt_joints.get(joint, 0.0)
            offset = self._wrap_angle(src_val - tgt_val)

            if prev_source_frame and joint in prev_joints:
                prev_val = prev_joints[joint]
                vel = self._wrap_angle(src_val - prev_val) / max(dt, 1e-6)
            else:
                vel = 0.0

            self._joint_states[joint] = _JointInertState(x0=offset, v0=vel)

    def apply(
        self,
        target_frame: UnifiedMotionFrame,
        dt: float = 1.0 / 24.0,
    ) -> UnifiedMotionFrame:
        """Apply decaying inertialization offset to the target frame.

        The target frame's contact tags, phase, and metadata are **always
        preserved unchanged**. Only root transform and joint rotations
        receive the decaying offset.
        """
        if not self._active:
            return target_frame

        self._elapsed += dt

        if self._elapsed >= self.blend_time:
            self._active = False
            return target_frame

        t = self._elapsed
        t1 = self.blend_time

        # Inertialized root transform
        rx_offset = _quintic_decay(self._root_x.x0, self._root_x.v0, t, t1)
        ry_offset = _quintic_decay(self._root_y.x0, self._root_y.v0, t, t1)
        rrot_offset = _quintic_decay(self._root_rot.x0, self._root_rot.v0, t, t1)

        rx_vel = _quintic_velocity(self._root_x.x0, self._root_x.v0, t, t1)
        ry_vel = _quintic_velocity(self._root_y.x0, self._root_y.v0, t, t1)

        new_root = MotionRootTransform(
            x=target_frame.root_transform.x + rx_offset,
            y=target_frame.root_transform.y + ry_offset,
            rotation=target_frame.root_transform.rotation + rrot_offset,
            velocity_x=target_frame.root_transform.velocity_x + rx_vel,
            velocity_y=target_frame.root_transform.velocity_y + ry_vel,
            angular_velocity=target_frame.root_transform.angular_velocity,
        )

        # Inertialized joint rotations
        new_joints = dict(target_frame.joint_local_rotations)
        for joint, state in self._joint_states.items():
            offset = _quintic_decay(state.x0, state.v0, t, t1)
            base_val = new_joints.get(joint, 0.0)
            new_joints[joint] = base_val + offset

        # Build output frame — contact tags from TARGET (never blended!)
        return UnifiedMotionFrame(
            time=target_frame.time,
            phase=target_frame.phase,
            root_transform=new_root,
            joint_local_rotations=new_joints,
            contact_tags=target_frame.contact_tags,  # CRITICAL: target contacts only
            frame_index=target_frame.frame_index,
            source_state=target_frame.source_state,
            metadata={
                **target_frame.metadata,
                "transition_active": True,
                "transition_strategy": "inertialization",
                "transition_progress": min(self._elapsed / max(self.blend_time, 1e-8), 1.0),
            },
        )

    @property
    def is_active(self) -> bool:
        return self._active

    @staticmethod
    def _wrap_angle(angle: float) -> float:
        """Wrap angle to [-pi, pi]."""
        while angle > math.pi:
            angle -= 2.0 * math.pi
        while angle < -math.pi:
            angle += 2.0 * math.pi
        return angle


# ── Dead Blending Channel ─────────────────────────────────────────────────


@dataclass
class DeadBlendingChannel:
    """Holden dead-blending: extrapolate source + crossfade with target.

    Simpler than quintic inertialization. At transition time, records the
    source pose and its velocity. Each frame, the source is extrapolated
    forward with exponentially decaying velocity, and a smoothstep crossfade
    blends from extrapolated source to target.

    Advantages over quintic inertialization:
      - Only needs current pose + velocity (no need to evaluate source clip)
      - Cross-fade guarantees output is always between source and target
      - Exponential velocity decay prevents crazy extrapolated poses

    The target frame's **contact tags are always preserved** — same guarantee
    as inertialization.
    """

    blend_time: float = 0.2        # seconds
    decay_halflife: float = 0.05   # velocity decay half-life (seconds)
    _elapsed: float = 0.0
    _joint_states: dict[str, _JointDeadBlendState] = field(default_factory=dict)
    _root_x: _JointDeadBlendState = field(default_factory=_JointDeadBlendState)
    _root_y: _JointDeadBlendState = field(default_factory=_JointDeadBlendState)
    _root_rot: _JointDeadBlendState = field(default_factory=_JointDeadBlendState)
    _active: bool = False

    def capture(
        self,
        source_frame: UnifiedMotionFrame,
        prev_source_frame: Optional[UnifiedMotionFrame] = None,
        dt: float = 1.0 / 24.0,
    ) -> None:
        """Record source pose and velocity at transition time."""
        self._elapsed = 0.0
        self._active = True

        src_rt = source_frame.root_transform

        self._root_x = _JointDeadBlendState(
            ext_x=src_rt.x,
            ext_v=((src_rt.x - prev_source_frame.root_transform.x) / max(dt, 1e-6)
                   if prev_source_frame else src_rt.velocity_x),
        )
        self._root_y = _JointDeadBlendState(
            ext_x=src_rt.y,
            ext_v=((src_rt.y - prev_source_frame.root_transform.y) / max(dt, 1e-6)
                   if prev_source_frame else src_rt.velocity_y),
        )
        self._root_rot = _JointDeadBlendState(
            ext_x=src_rt.rotation,
            ext_v=((InertializationChannel._wrap_angle(
                src_rt.rotation - prev_source_frame.root_transform.rotation
            )) / max(dt, 1e-6) if prev_source_frame else src_rt.angular_velocity),
        )

        # Per-joint states
        self._joint_states = {}
        src_joints = source_frame.joint_local_rotations
        prev_joints = prev_source_frame.joint_local_rotations if prev_source_frame else {}

        for joint, val in src_joints.items():
            if prev_source_frame and joint in prev_joints:
                vel = InertializationChannel._wrap_angle(val - prev_joints[joint]) / max(dt, 1e-6)
            else:
                vel = 0.0
            self._joint_states[joint] = _JointDeadBlendState(ext_x=val, ext_v=vel)

    def apply(
        self,
        target_frame: UnifiedMotionFrame,
        dt: float = 1.0 / 24.0,
    ) -> UnifiedMotionFrame:
        """Extrapolate source and crossfade with target.

        Contact tags always come from the target frame.
        """
        if not self._active:
            return target_frame

        self._elapsed += dt

        if self._elapsed >= self.blend_time:
            self._active = False
            return target_frame

        alpha = _smoothstep(self._elapsed / max(self.blend_time, 1e-8))
        hl = self.decay_halflife

        # Extrapolate and decay root
        self._root_x.ext_v = _damper_decay_exact(self._root_x.ext_v, hl, dt)
        self._root_x.ext_x += self._root_x.ext_v * dt
        self._root_y.ext_v = _damper_decay_exact(self._root_y.ext_v, hl, dt)
        self._root_y.ext_x += self._root_y.ext_v * dt
        self._root_rot.ext_v = _damper_decay_exact(self._root_rot.ext_v, hl, dt)
        self._root_rot.ext_x += self._root_rot.ext_v * dt

        def _lerp(a: float, b: float, t: float) -> float:
            return a + (b - a) * t

        tgt_rt = target_frame.root_transform
        new_root = MotionRootTransform(
            x=_lerp(self._root_x.ext_x, tgt_rt.x, alpha),
            y=_lerp(self._root_y.ext_x, tgt_rt.y, alpha),
            rotation=_lerp(self._root_rot.ext_x, tgt_rt.rotation, alpha),
            velocity_x=_lerp(self._root_x.ext_v, tgt_rt.velocity_x, alpha),
            velocity_y=_lerp(self._root_y.ext_v, tgt_rt.velocity_y, alpha),
            angular_velocity=tgt_rt.angular_velocity,
        )

        # Extrapolate and blend joints
        new_joints = dict(target_frame.joint_local_rotations)
        for joint, state in self._joint_states.items():
            state.ext_v = _damper_decay_exact(state.ext_v, hl, dt)
            state.ext_x += state.ext_v * dt
            tgt_val = new_joints.get(joint, 0.0)
            new_joints[joint] = _lerp(state.ext_x, tgt_val, alpha)

        return UnifiedMotionFrame(
            time=target_frame.time,
            phase=target_frame.phase,
            root_transform=new_root,
            joint_local_rotations=new_joints,
            contact_tags=target_frame.contact_tags,  # CRITICAL: target contacts only
            frame_index=target_frame.frame_index,
            source_state=target_frame.source_state,
            metadata={
                **target_frame.metadata,
                "transition_active": True,
                "transition_strategy": "dead_blending",
                "transition_progress": min(self._elapsed / max(self.blend_time, 1e-8), 1.0),
                "blend_alpha": alpha,
            },
        )

    @property
    def is_active(self) -> bool:
        return self._active


# ── Transition Quality Metrics ────────────────────────────────────────────


@dataclass
class TransitionQualityMetrics:
    """Quality metrics for a completed or in-progress transition.

    These metrics are designed to integrate with Layer 3 evolution fitness
    and the ``MotionMatchingEvaluator`` diagnostic system.
    """

    strategy: str = "none"
    total_displacement: float = 0.0      # Total offset magnitude over transition
    peak_offset: float = 0.0             # Maximum instantaneous offset
    contact_preservation: float = 1.0    # 1.0 = contacts always from target
    smoothness: float = 1.0              # 1.0 = no jerk/discontinuity
    blend_time_used: float = 0.0
    frames_processed: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "total_displacement": float(self.total_displacement),
            "peak_offset": float(self.peak_offset),
            "contact_preservation": float(self.contact_preservation),
            "smoothness": float(self.smoothness),
            "blend_time_used": float(self.blend_time_used),
            "frames_processed": int(self.frames_processed),
        }


# ── Main Transition Synthesizer ───────────────────────────────────────────


class TransitionSynthesizer:
    """Unified transition synthesis engine for the animation pipeline.

    This is the top-level API that the runtime animation system and Layer 3
    evaluation use to handle state transitions (e.g., Run → Jump, Jump → Fall).

    Usage::

        synth = TransitionSynthesizer(strategy=TransitionStrategy.DEAD_BLENDING)

        # When state changes:
        synth.request_transition(
            source_frame=last_run_frame,
            target_frame=first_jump_frame,
            prev_source_frame=second_to_last_run_frame,
        )

        # Each frame during transition:
        output = synth.update(current_target_frame, dt=1/24)

    The synthesizer automatically manages the transition lifecycle and
    provides quality metrics for Layer 3 integration.
    """

    def __init__(
        self,
        strategy: TransitionStrategy = TransitionStrategy.DEAD_BLENDING,
        blend_time: float = 0.2,
        decay_halflife: float = 0.05,
    ):
        self.strategy = strategy
        self.blend_time = blend_time
        self.decay_halflife = decay_halflife

        # Active channel
        self._inert_channel: Optional[InertializationChannel] = None
        self._dead_channel: Optional[DeadBlendingChannel] = None

        # Quality tracking
        self._metrics = TransitionQualityMetrics()
        self._offset_history: list[float] = []

    def request_transition(
        self,
        source_frame: UnifiedMotionFrame,
        target_frame: UnifiedMotionFrame,
        prev_source_frame: Optional[UnifiedMotionFrame] = None,
        dt: float = 1.0 / 24.0,
    ) -> None:
        """Initiate a new transition.

        This is the "fire" step. After this call, the source animation
        is never evaluated again — only the target animation is needed.

        Parameters
        ----------
        source_frame : UnifiedMotionFrame
            The last frame of the outgoing animation (e.g., last Run frame).
        target_frame : UnifiedMotionFrame
            The first frame of the incoming animation (e.g., first Jump frame).
        prev_source_frame : UnifiedMotionFrame, optional
            The frame before source_frame, used for velocity estimation.
            If not provided, velocities from source_frame's root transform
            are used instead.
        dt : float
            Time step for velocity computation.
        """
        self._offset_history = []
        self._metrics = TransitionQualityMetrics(
            strategy=self.strategy.value,
            blend_time_used=self.blend_time,
        )

        if self.strategy == TransitionStrategy.INERTIALIZATION:
            channel = InertializationChannel(blend_time=self.blend_time)
            channel.capture(source_frame, target_frame, prev_source_frame, dt)
            self._inert_channel = channel
            self._dead_channel = None
        else:
            channel = DeadBlendingChannel(
                blend_time=self.blend_time,
                decay_halflife=self.decay_halflife,
            )
            channel.capture(source_frame, prev_source_frame, dt)
            self._dead_channel = channel
            self._inert_channel = None

    def update(
        self,
        target_frame: UnifiedMotionFrame,
        dt: float = 1.0 / 24.0,
    ) -> UnifiedMotionFrame:
        """Process one frame through the transition synthesizer.

        If a transition is active, applies the inertialization/dead-blending
        offset. If no transition is active, passes through the target frame
        unchanged.

        Parameters
        ----------
        target_frame : UnifiedMotionFrame
            The current frame from the target (new) animation.
        dt : float
            Time step.

        Returns
        -------
        UnifiedMotionFrame
            The output frame with transition offset applied.
        """
        if self._inert_channel and self._inert_channel.is_active:
            result = self._inert_channel.apply(target_frame, dt)
            self._track_offset(result, target_frame)
            return result
        elif self._dead_channel and self._dead_channel.is_active:
            result = self._dead_channel.apply(target_frame, dt)
            self._track_offset(result, target_frame)
            return result
        else:
            return target_frame

    @property
    def is_active(self) -> bool:
        """Whether a transition is currently in progress."""
        if self._inert_channel and self._inert_channel.is_active:
            return True
        if self._dead_channel and self._dead_channel.is_active:
            return True
        return False

    def get_transition_quality(self) -> TransitionQualityMetrics:
        """Return quality metrics for the current/last transition.

        These metrics integrate with Layer 3's ``PhysicsTestBattery`` and
        ``MotionMatchingEvaluator`` for evolution fitness scoring.
        """
        if self._offset_history:
            self._metrics.total_displacement = sum(self._offset_history)
            self._metrics.peak_offset = max(self._offset_history)
            self._metrics.frames_processed = len(self._offset_history)

            # Smoothness: penalize large frame-to-frame offset changes
            if len(self._offset_history) >= 2:
                diffs = [
                    abs(self._offset_history[i] - self._offset_history[i - 1])
                    for i in range(1, len(self._offset_history))
                ]
                max_diff = max(diffs) if diffs else 0.0
                self._metrics.smoothness = float(
                    np.clip(1.0 - max_diff / max(self._metrics.peak_offset, 1e-6), 0.0, 1.0)
                )

        return self._metrics

    def _track_offset(
        self,
        output_frame: UnifiedMotionFrame,
        target_frame: UnifiedMotionFrame,
    ) -> None:
        """Track the magnitude of the applied offset for quality metrics."""
        dx = output_frame.root_transform.x - target_frame.root_transform.x
        dy = output_frame.root_transform.y - target_frame.root_transform.y

        joint_offset = 0.0
        for joint in output_frame.joint_local_rotations:
            out_val = output_frame.joint_local_rotations.get(joint, 0.0)
            tgt_val = target_frame.joint_local_rotations.get(joint, 0.0)
            joint_offset += abs(out_val - tgt_val)

        total = math.hypot(dx, dy) + joint_offset * 0.01
        self._offset_history.append(total)


# ── UMR Pipeline Node Integration ─────────────────────────────────────────


class TransitionPipelineNode:
    """UMR pipeline node that applies transition synthesis.

    This node can be inserted into the ``run_motion_pipeline()`` chain
    to automatically handle transitions between animation states.

    The node monitors ``source_state`` changes between consecutive frames
    and triggers inertialization when a state change is detected.
    """

    def __init__(
        self,
        strategy: TransitionStrategy = TransitionStrategy.DEAD_BLENDING,
        blend_time: float = 0.2,
        decay_halflife: float = 0.05,
    ):
        self.synthesizer = TransitionSynthesizer(
            strategy=strategy,
            blend_time=blend_time,
            decay_halflife=decay_halflife,
        )
        self._prev_frame: Optional[UnifiedMotionFrame] = None
        self._prev_prev_frame: Optional[UnifiedMotionFrame] = None
        self._last_state: str = ""

    @property
    def name(self) -> str:
        return "transition_synthesizer"

    @property
    def stage(self) -> str:
        return "transition"

    def process_frame(self, frame: UnifiedMotionFrame) -> UnifiedMotionFrame:
        """Process a single frame, detecting state changes and applying transitions."""
        current_state = frame.source_state

        # Detect state change → trigger transition
        if self._last_state and current_state != self._last_state and self._prev_frame is not None:
            self.synthesizer.request_transition(
                source_frame=self._prev_frame,
                target_frame=frame,
                prev_source_frame=self._prev_prev_frame,
            )

        # Apply active transition
        result = self.synthesizer.update(frame)

        # Update history
        self._prev_prev_frame = self._prev_frame
        self._prev_frame = result
        self._last_state = current_state

        return result

    def get_quality(self) -> TransitionQualityMetrics:
        """Return transition quality metrics for Layer 3 integration."""
        return self.synthesizer.get_transition_quality()


# ── Public API ─────────────────────────────────────────────────────────────


def create_transition_synthesizer(
    strategy: str = "dead_blending",
    blend_time: float = 0.2,
    decay_halflife: float = 0.05,
) -> TransitionSynthesizer:
    """Factory function for creating a configured TransitionSynthesizer.

    Parameters
    ----------
    strategy : str
        "inertialization" (Bollo quintic) or "dead_blending" (Holden).
    blend_time : float
        Duration of the transition window in seconds.
    decay_halflife : float
        Velocity decay half-life for dead blending (ignored for inertialization).

    Returns
    -------
    TransitionSynthesizer
    """
    strat = TransitionStrategy(strategy)
    return TransitionSynthesizer(
        strategy=strat,
        blend_time=blend_time,
        decay_halflife=decay_halflife,
    )


def inertialize_transition(
    source_frames: list[UnifiedMotionFrame],
    target_frames: list[UnifiedMotionFrame],
    strategy: str = "dead_blending",
    blend_time: float = 0.2,
    decay_halflife: float = 0.05,
    dt: float = 1.0 / 24.0,
) -> list[UnifiedMotionFrame]:
    """One-shot utility: inertialize a transition between two frame sequences.

    Given the tail of a source clip and the head of a target clip, produces
    a seamless output sequence where the transition is inertialized.

    Parameters
    ----------
    source_frames : list[UnifiedMotionFrame]
        Last 2+ frames of the outgoing animation.
    target_frames : list[UnifiedMotionFrame]
        Frames of the incoming animation to process.
    strategy : str
        "inertialization" or "dead_blending".
    blend_time : float
        Transition duration in seconds.
    decay_halflife : float
        Velocity decay half-life (dead blending only).
    dt : float
        Time step between frames.

    Returns
    -------
    list[UnifiedMotionFrame]
        Inertialized output frames (same length as target_frames).
    """
    if not source_frames or not target_frames:
        return list(target_frames)

    synth = create_transition_synthesizer(strategy, blend_time, decay_halflife)

    prev_source = source_frames[-2] if len(source_frames) >= 2 else None
    source = source_frames[-1]

    synth.request_transition(
        source_frame=source,
        target_frame=target_frames[0],
        prev_source_frame=prev_source,
        dt=dt,
    )

    output = []
    for frame in target_frames:
        out = synth.update(frame, dt)
        output.append(out)

    return output


__all__ = [
    "TransitionStrategy",
    "InertializationChannel",
    "DeadBlendingChannel",
    "TransitionQualityMetrics",
    "TransitionSynthesizer",
    "TransitionPipelineNode",
    "create_transition_synthesizer",
    "inertialize_transition",
]
