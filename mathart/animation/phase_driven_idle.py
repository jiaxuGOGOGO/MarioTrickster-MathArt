"""Phase-Driven Idle Animation — SESSION-040 UMR Contract Enforcement.

This module provides a phase-driven idle animation generator that produces
``UnifiedMotionFrame`` objects natively, eliminating the need for the
``legacy_pose_adapter`` path in the pipeline trunk.

The idle animation is a subtle breathing cycle with:
- Chest expansion/contraction (primary action)
- Head bob with phase offset (secondary action)
- Arm sway at half frequency (overlapping action)

These are the same motion curves as the original ``idle_animation()`` in
``presets.py``, but wrapped in the UMR contract from the start. The original
function used ``sine_wave()`` from ``curves.py``; this module reimplements
the same math inline to avoid numpy dependency in the frame generator and
ensure bit-level determinism.

Design rationale (Mike Acton DOD):
    The idle state was the last remaining path that fell through to the
    ``legacy_pose_adapter`` in ``_build_umr_clip_for_state()``. By providing
    a native phase-driven generator, we close the last bypass and allow the
    ``PipelineContractGuard`` to reject any ``legacy_pose_adapter`` invocation.

References
----------
[1] Richard Williams, "The Animator's Survival Kit", 2009 — idle breathing cycles.
[2] Mike Acton, "Data-Oriented Design and C++", CppCon 2014.
"""
from __future__ import annotations

import math
from typing import Any

from .unified_motion import (
    MotionContactState,
    MotionRootTransform,
    UnifiedMotionFrame,
    pose_to_umr,
    infer_contact_tags,
)


def _sine(t: float, frequency: float = 1.0, amplitude: float = 1.0, phase: float = 0.0) -> float:
    """Pure-Python sine wave matching curves.sine_wave for determinism."""
    return amplitude * math.sin(2.0 * math.pi * frequency * t + phase)


def phase_driven_idle(t: float) -> dict[str, float]:
    """Phase-driven idle pose — drop-in replacement for idle_animation().

    Produces the same joint angles as the original ``idle_animation()``
    but uses pure-Python math for determinism.

    Parameters
    ----------
    t : float
        Normalized cycle time [0, 1). Full breathing cycle.

    Returns
    -------
    dict[str, float]
        Joint angle dictionary.
    """
    breath = _sine(t, frequency=1.0, amplitude=0.03)
    head_bob = _sine(t, frequency=1.0, amplitude=0.02, phase=0.5)
    arm_sway = _sine(t, frequency=0.5, amplitude=0.05)

    return {
        "spine": breath,
        "chest": breath * 0.5,
        "neck": -breath * 0.3,
        "head": head_bob,
        "l_shoulder": arm_sway,
        "r_shoulder": -arm_sway,
        "l_elbow": 0.1 + arm_sway * 0.3,
        "r_elbow": 0.1 - arm_sway * 0.3,
    }


def phase_driven_idle_frame(
    t: float,
    *,
    time: float = 0.0,
    frame_index: int = 0,
    source_state: str = "idle",
    root_x: float = 0.0,
    root_y: float = 0.0,
    root_velocity_x: float = 0.0,
    root_velocity_y: float = 0.0,
    **_extra: Any,
) -> UnifiedMotionFrame:
    """UMR-native idle frame generator.

    This is the SESSION-040 replacement for the legacy idle path. It produces
    a ``UnifiedMotionFrame`` directly, with proper contact tags (both feet
    grounded for idle) and metadata marking it as phase-driven.

    Parameters
    ----------
    t : float
        Normalized cycle time [0, 1).
    time : float
        Absolute time in seconds.
    frame_index : int
        Frame index within the clip.
    source_state : str
        Animation state name (default ``"idle"``).
    root_x, root_y : float
        Root position (typically 0 for idle).
    root_velocity_x, root_velocity_y : float
        Root velocities (typically 0 for idle).

    Returns
    -------
    UnifiedMotionFrame
        UMR-compliant frame with all required fields.
    """
    p = float(t) % 1.0
    pose = phase_driven_idle(p)

    return pose_to_umr(
        pose,
        time=float(time),
        phase=p,
        frame_index=int(frame_index),
        source_state=source_state,
        root_transform=MotionRootTransform(
            x=float(root_x),
            y=float(root_y),
            rotation=0.0,
            velocity_x=float(root_velocity_x),
            velocity_y=float(root_velocity_y),
            angular_velocity=0.0,
        ),
        contact_tags=infer_contact_tags(p, source_state),
        metadata={
            "generator": "phase_driven_idle",
            "phase_kind": "cyclic",
            "session": "SESSION-040",
        },
    )


__all__ = [
    "phase_driven_idle",
    "phase_driven_idle_frame",
]
