"""
SESSION-033: Phase-Driven Animation Control System

Research-grounded implementation of phase-based animation that replaces
rigid sin(t) formulas with a principled, phase-first architecture.

Research foundations:
  1. **PFNN** (Holden et al., SIGGRAPH 2017):
     Phase variable p ∈ [0, 2π) as first-class citizen, linearly interpolated
     between foot-contact events. Left contact → p=0, Right contact → p=π.
     Catmull-Rom spline interpolation of control parameters over phase.

  2. **DeepPhase / Periodic Autoencoder** (Starke et al., SIGGRAPH 2022):
     Multi-channel phase decomposition: Γ(x) = A·sin(2π(Fx - S)) + B.
     Amplitude (A), Frequency (F), Offset (B), Phase-shift (S) extracted
     via FFT. Enables layered phase channels for different body parts.

  3. **The Animator's Survival Kit** (Richard Williams, 2009):
     Four canonical key poses — Contact, Down, Passing, Up — with precise
     pelvis height trajectory and timing ratios. Walk on 12s:
     Contact(1)→Down(4)→Pass(7)→Up(10)→Contact(13).
     Run: flight phase, 1/2-1/3 head UP raise, forward lean scales with speed.

Architecture:
    ┌─────────────────────────────────────────────────────────────────┐
    │  PhaseVariable                                                  │
    │  ├─ p ∈ [0, 1)  (normalized cycle phase)                       │
    │  ├─ foot_contacts: left_contact @ p=0, right_contact @ p=0.5   │
    │  └─ advance(dt, speed) → monotonic phase increment             │
    ├─────────────────────────────────────────────────────────────────┤
    │  KeyPoseLibrary                                                 │
    │  ├─ WALK: Contact(0.0), Down(0.125), Pass(0.375), Up(0.5)     │
    │  ├─ RUN:  Contact(0.0), Down(0.15), Pass(0.35), Up(0.5)       │
    │  └─ Each pose: full joint angle dict + pelvis_height            │
    ├─────────────────────────────────────────────────────────────────┤
    │  PhaseInterpolator                                              │
    │  ├─ Catmull-Rom spline over key poses (PFNN-style)             │
    │  ├─ Per-joint smooth interpolation                              │
    │  └─ Pelvis height curve: Contact→Down(low)→Pass(high)→Up(high) │
    ├─────────────────────────────────────────────────────────────────┤
    │  PhaseChannel (DeepPhase-inspired)                              │
    │  ├─ Multi-channel overlay: primary gait + arm swing + torso     │
    │  ├─ Each channel: Γ(p) = A·sin(2π(F·p - S)) + B               │
    │  └─ Amplitude modulation for speed/style transitions            │
    ├─────────────────────────────────────────────────────────────────┤
    │  PhaseDrivenAnimator (Integration)                              │
    │  ├─ walk_pose(phase) → dict[str, float]                        │
    │  ├─ run_pose(phase) → dict[str, float]                         │
    │  └─ Replaces old sin()-based presets                            │
    └─────────────────────────────────────────────────────────────────┘

Usage::

    from mathart.animation.phase_driven import (
        PhaseDrivenAnimator, PhaseVariable, GaitMode,
        phase_driven_walk, phase_driven_run,
    )

    # Simple functional API (drop-in replacement for presets)
    pose = phase_driven_walk(t=0.3)
    pose = phase_driven_run(t=0.7)

    # Object-oriented API with phase tracking
    animator = PhaseDrivenAnimator()
    phase = PhaseVariable()
    phase.advance(dt=1/60, speed=1.0)
    pose = animator.generate(phase, gait=GaitMode.RUN)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Callable

import numpy as np

from .curves import ease_in_out
from .unified_motion import (
    MotionContactState,
    MotionRootTransform,
    UnifiedMotionFrame,
    infer_contact_tags,
    pose_to_umr,
)


# ── Phase Variable (PFNN-inspired) ──────────────────────────────────────────


class PhaseVariable:
    """Cyclic phase variable for locomotion control.

    Implements the PFNN phase concept: a monotonically increasing variable
    p ∈ [0, 1) that wraps around, with foot-contact events at defined points.

    PFNN mapping: left_contact @ p=0.0, right_contact @ p=0.5.
    Phase advances linearly between contacts, speed-modulated.

    Parameters
    ----------
    initial_phase : float
        Starting phase value [0, 1). Default 0.0.
    """

    def __init__(self, initial_phase: float = 0.0):
        self._phase = float(initial_phase) % 1.0
        self._prev_phase = self._phase
        self._cycle_count = 0

    @property
    def phase(self) -> float:
        """Current phase value [0, 1)."""
        return self._phase

    @property
    def phase_2pi(self) -> float:
        """Current phase in [0, 2π) (PFNN convention)."""
        return self._phase * 2.0 * math.pi

    @property
    def cycle_count(self) -> int:
        """Number of completed full cycles."""
        return self._cycle_count

    @property
    def left_contact(self) -> bool:
        """True if phase just crossed a left-foot contact event (p ≈ 0)."""
        return self._prev_phase > 0.9 and self._phase < 0.1

    @property
    def right_contact(self) -> bool:
        """True if phase just crossed a right-foot contact event (p ≈ 0.5)."""
        return self._prev_phase < 0.5 and self._phase >= 0.5

    def advance(self, dt: float, speed: float = 1.0, steps_per_second: float = 2.0) -> float:
        """Advance phase by dt seconds at given speed.

        Parameters
        ----------
        dt : float
            Time step in seconds.
        speed : float
            Speed multiplier (1.0 = normal walk tempo).
        steps_per_second : float
            Base stepping frequency. Walk=2.0 (12 frames @ 24fps),
            Run=3.0 (8 frames @ 24fps). From Animator's Survival Kit.

        Returns
        -------
        float : New phase value.
        """
        self._prev_phase = self._phase
        # Phase increment: one full cycle = two steps
        # steps_per_second=2 means 1 full cycle per second
        delta = dt * speed * steps_per_second / 2.0
        self._phase = (self._phase + delta) % 1.0
        if self._phase < self._prev_phase and delta > 0:
            self._cycle_count += 1
        return self._phase

    def set_phase(self, p: float) -> None:
        """Directly set phase value."""
        self._prev_phase = self._phase
        self._phase = float(p) % 1.0

    def reset(self) -> None:
        """Reset to initial state."""
        self._phase = 0.0
        self._prev_phase = 0.0
        self._cycle_count = 0


# ── Key Pose Definitions (Animator's Survival Kit) ──────────────────────────


class GaitMode(str, Enum):
    """Gait modes with distinct phase-pose mappings."""
    WALK = "walk"
    RUN = "run"
    SNEAK = "sneak"


@dataclass(frozen=True)
class KeyPose:
    """A canonical key pose at a specific phase point.

    Based on Richard Williams' four key poses:
    Contact, Down, Passing, Up.

    Attributes
    ----------
    name : str
        Pose name (e.g., "contact", "down", "passing", "up").
    phase : float
        Phase position [0, 1) within a half-cycle (one step).
    pelvis_height : float
        Relative pelvis height offset from neutral.
        Negative = lower, positive = higher.
    joints : dict[str, float]
        Joint angle values for this pose.
    """
    name: str
    phase: float
    pelvis_height: float
    joints: dict[str, float]


# ── Walk Cycle Key Poses ────────────────────────────────────────────────────
# Derived from Animator's Survival Kit p.107-111:
# On 12s: Contact(1)→Down(4)→Pass(7)→Up(10)→Contact(13)
# Normalized to half-cycle [0, 0.5]: Contact(0)→Down(0.125)→Pass(0.25)→Up(0.375)→Contact(0.5)
# Full cycle: left step [0, 0.5), right step [0.5, 1.0)

WALK_KEY_POSES: list[KeyPose] = [
    KeyPose(
        name="contact",
        phase=0.0,
        pelvis_height=0.0,  # Neutral — "middle position"
        joints={
            # Stance leg (left) extended forward, heel strike
            "l_hip": 0.35,      # Forward reach
            "l_knee": -0.05,    # Nearly straight (heel strike)
            "l_foot": 0.15,     # Dorsiflexed for heel contact
            # Swing leg (right) extended back
            "r_hip": -0.35,     # Trailing behind
            "r_knee": -0.05,    # Nearly straight
            "r_foot": -0.10,    # Plantarflexed (pushing off)
            # Arms counter-rotate to legs (Williams: "arms always opposite to legs")
            "l_shoulder": -0.30,  # Back (opposite to left leg forward)
            "r_shoulder": 0.30,   # Forward (opposite to right leg back)
            "l_elbow": 0.15,
            "r_elbow": 0.15,
            # Torso: slight forward lean, neutral rotation
            "spine": 0.06,
            "chest": 0.0,
            "head": -0.03,
            "neck": 0.0,
        },
    ),
    KeyPose(
        name="down",
        phase=0.125,  # ~25% of half-cycle (frame 4 of 12)
        pelvis_height=-0.025,  # LOWEST point — "the leg bends absorbing the force"
        joints={
            # Front leg absorbs weight — knee bends significantly
            "l_hip": 0.25,
            "l_knee": -0.35,    # Deep bend — weight absorption
            "l_foot": 0.05,     # Foot flat on ground
            # Back leg lifting off
            "r_hip": -0.25,
            "r_knee": -0.15,
            "r_foot": -0.15,    # Toe pushing off
            # Arms at WIDEST (Williams: "arm swing is at its widest on the DOWN")
            "l_shoulder": -0.40,
            "r_shoulder": 0.40,
            "l_elbow": 0.20,
            "r_elbow": 0.20,
            # Torso dips with pelvis
            "spine": 0.08,
            "chest": -0.02,
            "head": -0.04,
            "neck": 0.0,
        },
    ),
    KeyPose(
        name="passing",
        phase=0.25,  # 50% of half-cycle (frame 7 of 12)
        pelvis_height=0.015,  # HIGH — "slightly higher than mid-point" (leg straight)
        joints={
            # Stance leg straight (lifts pelvis)
            "l_hip": 0.0,
            "l_knee": -0.05,    # Nearly straight — this lifts the pelvis
            "l_foot": 0.0,
            # Swing leg passes through — knee bent, foot tucked
            "r_hip": 0.0,
            "r_knee": -0.55,    # High knee bend — foot clearance
            "r_foot": -0.20,    # Foot tucked under
            # Arms passing through center
            "l_shoulder": 0.0,
            "r_shoulder": 0.0,
            "l_elbow": 0.10,
            "r_elbow": 0.10,
            # Torso upright
            "spine": 0.05,
            "chest": 0.0,
            "head": -0.02,
            "neck": 0.0,
        },
    ),
    KeyPose(
        name="up",
        phase=0.375,  # 75% of half-cycle (frame 10 of 12)
        pelvis_height=0.020,  # HIGHEST — "foot pushing off lifts pelvis to highest"
        joints={
            # Stance leg pushing off — on toe
            "l_hip": -0.15,
            "l_knee": -0.10,
            "l_foot": -0.20,    # Plantarflexed — toe push
            # Swing leg reaching forward
            "r_hip": 0.25,
            "r_knee": -0.20,    # Extending forward
            "r_foot": 0.10,     # Dorsiflexing for heel strike prep
            # Arms swinging through
            "l_shoulder": 0.25,
            "r_shoulder": -0.25,
            "l_elbow": 0.15,
            "r_elbow": 0.15,
            # Torso slightly lifted
            "spine": 0.04,
            "chest": 0.01,
            "head": -0.02,
            "neck": 0.0,
        },
    ),
]


# ── Run Cycle Key Poses ─────────────────────────────────────────────────────
# Derived from Animator's Survival Kit p.176-182:
# Run on 6s: Extreme/Contact(1)→Down(2)→PassPos(4)→High(5)→Extreme(7)
# Key differences from walk:
#   - Flight phase (both feet off ground)
#   - Greater forward lean (scales with speed)
#   - UP raise only 1/2 to 1/3 head height
#   - Reduced arm action for realistic run
#   - Shoulders oppose hips

RUN_KEY_POSES: list[KeyPose] = [
    KeyPose(
        name="contact",
        phase=0.0,
        pelvis_height=-0.010,  # Slight dip at contact (impact absorption)
        joints={
            # Front leg reaches with straight leg
            "l_hip": 0.50,
            "l_knee": -0.10,    # Nearly straight — reach
            "l_foot": 0.20,     # Dorsiflexed
            # Back leg extended behind — push-off
            "r_hip": -0.50,
            "r_knee": -0.10,
            "r_foot": -0.25,    # Strong plantarflexion
            # Arms: counter-swing, more compact than walk
            "l_shoulder": -0.40,
            "r_shoulder": 0.40,
            "l_elbow": 0.60,    # More bent (running form)
            "r_elbow": 0.60,
            # Greater forward lean
            "spine": 0.12,
            "chest": -0.03,
            "head": -0.06,
            "neck": 0.0,
        },
    ),
    KeyPose(
        name="down",
        phase=0.10,  # Faster transition to down in run
        pelvis_height=-0.035,  # Deeper dip — more impact force
        joints={
            "l_hip": 0.35,
            "l_knee": -0.50,    # Deep bend — absorbing impact
            "l_foot": 0.0,
            "r_hip": -0.35,
            "r_knee": -0.20,
            "r_foot": -0.20,
            "l_shoulder": -0.45,
            "r_shoulder": 0.45,
            "l_elbow": 0.65,
            "r_elbow": 0.65,
            "spine": 0.14,
            "chest": -0.04,
            "head": -0.06,
            "neck": 0.0,
        },
    ),
    KeyPose(
        name="passing",
        phase=0.25,
        pelvis_height=0.010,  # Rising — straight stance leg
        joints={
            "l_hip": 0.0,
            "l_knee": -0.08,    # Straight stance leg
            "l_foot": -0.05,
            "r_hip": 0.10,
            "r_knee": -0.70,    # High knee drive
            "r_foot": -0.25,    # Foot tucked
            "l_shoulder": 0.0,
            "r_shoulder": 0.0,
            "l_elbow": 0.50,
            "r_elbow": 0.50,
            "spine": 0.10,
            "chest": 0.0,
            "head": -0.05,
            "neck": 0.0,
        },
    ),
    KeyPose(
        name="up",
        phase=0.375,
        pelvis_height=0.020,  # Highest — but only 1/2 to 1/3 head height
        joints={
            # Push-off leg extending
            "l_hip": -0.25,
            "l_knee": -0.05,    # Extending for push-off
            "l_foot": -0.30,    # Strong plantarflexion — toe push
            # Swing leg driving forward
            "r_hip": 0.40,
            "r_knee": -0.30,
            "r_foot": 0.15,
            "l_shoulder": 0.35,
            "r_shoulder": -0.35,
            "l_elbow": 0.55,
            "r_elbow": 0.55,
            "spine": 0.10,
            "chest": 0.02,
            "head": -0.04,
            "neck": 0.0,
        },
    ),
    # Flight phase — unique to running (both feet off ground)
    KeyPose(
        name="flight",
        phase=0.45,  # Brief aerial phase before next contact
        pelvis_height=0.015,  # Still elevated
        joints={
            "l_hip": -0.35,     # Trailing back
            "l_knee": -0.15,
            "l_foot": -0.20,
            "r_hip": 0.45,      # Reaching forward
            "r_knee": -0.15,
            "r_foot": 0.15,
            "l_shoulder": 0.40,
            "r_shoulder": -0.40,
            "l_elbow": 0.55,
            "r_elbow": 0.55,
            "spine": 0.10,
            "chest": 0.01,
            "head": -0.05,
            "neck": 0.0,
        },
    ),
]


# ── Catmull-Rom Spline Interpolation (PFNN-style) ──────────────────────────


def _catmull_rom(p0: float, p1: float, p2: float, p3: float, t: float) -> float:
    """Evaluate Catmull-Rom spline at parameter t ∈ [0, 1].

    This is the same interpolation scheme used in PFNN for smooth
    phase-indexed parameter blending.

    The Catmull-Rom spline passes through p1 at t=0 and p2 at t=1,
    using p0 and p3 as tangent guides.
    """
    t2 = t * t
    t3 = t2 * t
    return 0.5 * (
        (2.0 * p1)
        + (-p0 + p2) * t
        + (2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3) * t2
        + (-p0 + 3.0 * p1 - 3.0 * p2 + p3) * t3
    )


def _catmull_rom_array(values: list[float], t: float) -> float:
    """Evaluate Catmull-Rom through a cyclic array of values at parameter t.

    Parameters
    ----------
    values : list[float]
        Control point values (assumed cyclic).
    t : float
        Parameter [0, 1) mapping to the full array cycle.

    Returns
    -------
    float : Interpolated value.
    """
    n = len(values)
    scaled = t * n
    i = int(scaled) % n
    frac = scaled - int(scaled)

    p0 = values[(i - 1) % n]
    p1 = values[i % n]
    p2 = values[(i + 1) % n]
    p3 = values[(i + 2) % n]

    return _catmull_rom(p0, p1, p2, p3, frac)


# ── Phase Interpolator ──────────────────────────────────────────────────────


class PhaseInterpolator:
    """Interpolates between key poses using Catmull-Rom splines.

    Given a set of key poses at specific phase points within a half-cycle,
    this interpolator produces smooth joint angle trajectories by:
    1. Building per-joint Catmull-Rom spline control points
    2. Mirroring left/right for the second half-cycle
    3. Evaluating at any phase value [0, 1)

    The mirroring implements the fundamental gait symmetry:
    what the left side does in [0, 0.5), the right side does in [0.5, 1.0).

    Parameters
    ----------
    key_poses : list[KeyPose]
        Ordered key poses for one half-cycle (one step).
    """

    # Joint pairs for left-right mirroring
    _MIRROR_PAIRS: list[tuple[str, str]] = [
        ("l_hip", "r_hip"),
        ("l_knee", "r_knee"),
        ("l_foot", "r_foot"),
        ("l_shoulder", "r_shoulder"),
        ("l_elbow", "r_elbow"),
        ("l_hand", "r_hand"),
    ]
    _SYMMETRIC_JOINTS: list[str] = [
        "spine", "chest", "head", "neck", "root",
    ]

    def __init__(self, key_poses: list[KeyPose]):
        self._key_poses = sorted(key_poses, key=lambda kp: kp.phase)
        self._build_spline_data()

    def _build_spline_data(self) -> None:
        """Pre-compute spline control points for each joint.

        To ensure C1 continuity at the half-cycle boundary (p=0.5),
        we append a virtual "mirrored contact" pose at p=0.5 that
        represents the Contact pose with left/right swapped. This
        gives the Catmull-Rom spline a proper target to interpolate
        toward at the end of each half-cycle.
        """
        # Collect all joint names from key poses
        all_joints: set[str] = set()
        for kp in self._key_poses:
            all_joints.update(kp.joints.keys())

        # Create the mirrored contact pose at p=0.5
        # This is what the pose looks like at the start of the mirrored half
        contact_pose = self._key_poses[0]  # First pose is Contact at p=0
        mirrored_joints: dict[str, float] = {}
        swapped_keys: set[str] = set()
        for l_j, r_j in self._MIRROR_PAIRS:
            l_val = contact_pose.joints.get(l_j, 0.0)
            r_val = contact_pose.joints.get(r_j, 0.0)
            if l_j in contact_pose.joints or r_j in contact_pose.joints:
                mirrored_joints[l_j] = r_val
                mirrored_joints[r_j] = l_val
                swapped_keys.add(l_j)
                swapped_keys.add(r_j)
        for j in contact_pose.joints:
            if j not in swapped_keys:
                mirrored_joints[j] = contact_pose.joints[j]
        all_joints.update(mirrored_joints.keys())

        # Extended key poses: original + mirrored contact at 0.5
        extended_poses = list(self._key_poses) + [
            KeyPose(
                name="contact_mirror",
                phase=0.5,
                pelvis_height=contact_pose.pelvis_height,
                joints=mirrored_joints,
            )
        ]
        extended_poses.sort(key=lambda kp: kp.phase)

        # Build phase→value arrays for each joint
        self._joint_phases: dict[str, list[float]] = {}
        self._joint_values: dict[str, list[float]] = {}
        self._pelvis_phases: list[float] = []
        self._pelvis_values: list[float] = []

        for joint in all_joints:
            phases = []
            values = []
            for kp in extended_poses:
                phases.append(kp.phase)
                values.append(kp.joints.get(joint, 0.0))
            self._joint_phases[joint] = phases
            self._joint_values[joint] = values

        # Pelvis height spline
        for kp in extended_poses:
            self._pelvis_phases.append(kp.phase)
            self._pelvis_values.append(kp.pelvis_height)

    def _interpolate_at_phase(
        self, phases: list[float], values: list[float], p: float
    ) -> float:
        """Interpolate value at phase p using Catmull-Rom over non-uniform knots.

        Finds the segment containing p and evaluates the spline.
        """
        n = len(phases)
        if n == 0:
            return 0.0
        if n == 1:
            return values[0]

        # Find segment: phases[i] <= p < phases[i+1]
        # Handle wrap-around for cyclic phases
        seg_idx = 0
        for i in range(n - 1):
            if phases[i] <= p < phases[i + 1]:
                seg_idx = i
                break
        else:
            # p is past last phase or before first — wrap
            seg_idx = n - 1

        if seg_idx < n - 1:
            span = phases[seg_idx + 1] - phases[seg_idx]
            if span > 0:
                local_t = (p - phases[seg_idx]) / span
            else:
                local_t = 0.0
        else:
            # Wrap segment: from last phase to 0.5 (half-cycle end)
            span = 0.5 - phases[seg_idx]
            if span > 0:
                local_t = (p - phases[seg_idx]) / span
            else:
                local_t = 0.0

        local_t = max(0.0, min(1.0, local_t))

        # Get four control points for Catmull-Rom
        p0 = values[(seg_idx - 1) % n]
        p1 = values[seg_idx % n]
        p2 = values[(seg_idx + 1) % n]
        p3 = values[(seg_idx + 2) % n]

        return _catmull_rom(p0, p1, p2, p3, local_t)

    def evaluate(self, phase: float) -> tuple[dict[str, float], float]:
        """Evaluate pose at given phase [0, 1).

        Handles left-right mirroring for the second half-cycle.

        Parameters
        ----------
        phase : float
            Gait cycle phase [0, 1).

        Returns
        -------
        tuple[dict[str, float], float]
            (joint_angles, pelvis_height_offset)
        """
        phase = phase % 1.0

        # Determine if we're in the mirrored half
        if phase < 0.5:
            half_phase = phase
            mirror = False
        else:
            half_phase = phase - 0.5
            mirror = True

        # Interpolate each joint at half_phase
        raw_pose: dict[str, float] = {}
        for joint in self._joint_phases:
            raw_pose[joint] = self._interpolate_at_phase(
                self._joint_phases[joint],
                self._joint_values[joint],
                half_phase,
            )

        # Interpolate pelvis height
        pelvis_h = self._interpolate_at_phase(
            self._pelvis_phases, self._pelvis_values, half_phase
        )

        # Apply mirroring if in second half-cycle
        if mirror:
            mirrored: dict[str, float] = {}
            # Swap left/right pairs
            swapped = set()
            for l_joint, r_joint in self._MIRROR_PAIRS:
                if l_joint in raw_pose and r_joint in raw_pose:
                    mirrored[l_joint] = raw_pose[r_joint]
                    mirrored[r_joint] = raw_pose[l_joint]
                    swapped.add(l_joint)
                    swapped.add(r_joint)
                elif l_joint in raw_pose:
                    mirrored[r_joint] = raw_pose[l_joint]
                    swapped.add(l_joint)
                elif r_joint in raw_pose:
                    mirrored[l_joint] = raw_pose[r_joint]
                    swapped.add(r_joint)

            # Symmetric joints stay the same
            for joint in raw_pose:
                if joint not in swapped:
                    mirrored[joint] = raw_pose[joint]

            return mirrored, pelvis_h
        else:
            return raw_pose, pelvis_h


# ── Phase Channel (DeepPhase-inspired) ──────────────────────────────────────


@dataclass
class PhaseChannel:
    """A single periodic phase channel inspired by DeepPhase PAE.

    Models one aspect of motion as: Γ(p) = A·sin(2π(F·p - S)) + B

    This allows layered, multi-channel animation where different body
    aspects (primary gait, arm swing, torso twist, head bob) each have
    their own amplitude, frequency, and phase offset.

    Parameters
    ----------
    amplitude : float
        Signal amplitude A. Controls motion magnitude.
    frequency : float
        Frequency F relative to the gait cycle. F=1.0 means one full
        oscillation per gait cycle; F=2.0 means two (e.g., torso bob).
    phase_shift : float
        Phase offset S ∈ [0, 1). Shifts the signal in time.
    offset : float
        DC offset B. Shifts the signal baseline.
    """
    amplitude: float = 1.0
    frequency: float = 1.0
    phase_shift: float = 0.0
    offset: float = 0.0

    def evaluate(self, phase: float) -> float:
        """Evaluate channel at given phase.

        Parameters
        ----------
        phase : float
            Current gait phase [0, 1).

        Returns
        -------
        float : Channel value Γ(phase).
        """
        return self.amplitude * math.sin(
            2.0 * math.pi * (self.frequency * phase - self.phase_shift)
        ) + self.offset

    def evaluate_2d(self, phase: float) -> tuple[float, float]:
        """Evaluate as 2D phase representation (DeepPhase-style).

        Returns (cos, sin) components for continuous phase tracking
        that avoids discontinuities at wrap-around.

        Returns
        -------
        tuple[float, float] : (phase_x, phase_y) on unit circle.
        """
        angle = 2.0 * math.pi * (self.frequency * phase - self.phase_shift)
        return (
            self.amplitude * math.cos(angle),
            self.amplitude * math.sin(angle),
        )


# ── Predefined Phase Channel Sets ──────────────────────────────────────────

# Walk secondary motion channels
WALK_CHANNELS: dict[str, PhaseChannel] = {
    # Torso bob: 2x frequency (bounces twice per cycle — once per step)
    "torso_bob": PhaseChannel(amplitude=0.015, frequency=2.0, phase_shift=0.125, offset=0.0),
    # Torso twist: 1x frequency (counter-rotates with legs)
    "torso_twist": PhaseChannel(amplitude=0.04, frequency=1.0, phase_shift=0.0, offset=0.0),
    # Head stabilization: opposes torso bob
    "head_stabilize": PhaseChannel(amplitude=0.008, frequency=2.0, phase_shift=0.375, offset=0.0),
    # Lateral sway: 1x frequency (shift weight side to side)
    "lateral_sway": PhaseChannel(amplitude=0.02, frequency=1.0, phase_shift=0.25, offset=0.0),
}

# Run secondary motion channels
RUN_CHANNELS: dict[str, PhaseChannel] = {
    "torso_bob": PhaseChannel(amplitude=0.025, frequency=2.0, phase_shift=0.10, offset=0.0),
    "torso_twist": PhaseChannel(amplitude=0.06, frequency=1.0, phase_shift=0.0, offset=0.0),
    "head_stabilize": PhaseChannel(amplitude=0.012, frequency=2.0, phase_shift=0.35, offset=0.0),
    "lateral_sway": PhaseChannel(amplitude=0.015, frequency=1.0, phase_shift=0.25, offset=0.0),
    # Arm pump: additional arm motion for running
    "arm_pump": PhaseChannel(amplitude=0.10, frequency=2.0, phase_shift=0.0, offset=0.0),
}


# ── Phase-Driven Animator (Integration) ─────────────────────────────────────


class PhaseDrivenAnimator:
    """Main phase-driven animation controller.

    Integrates key-pose interpolation (Animator's Survival Kit),
    Catmull-Rom spline blending (PFNN), and multi-channel phase
    overlays (DeepPhase) into a unified animation generator.

    This replaces the old sin()-based presets with a principled,
    research-grounded system where Phase is the first-class citizen.

    Parameters
    ----------
    walk_poses : list[KeyPose], optional
        Custom walk key poses. Defaults to WALK_KEY_POSES.
    run_poses : list[KeyPose], optional
        Custom run key poses. Defaults to RUN_KEY_POSES.
    walk_channels : dict[str, PhaseChannel], optional
        Walk secondary motion channels. Defaults to WALK_CHANNELS.
    run_channels : dict[str, PhaseChannel], optional
        Run secondary motion channels. Defaults to RUN_CHANNELS.
    """

    def __init__(
        self,
        walk_poses: list[KeyPose] | None = None,
        run_poses: list[KeyPose] | None = None,
        walk_channels: dict[str, PhaseChannel] | None = None,
        run_channels: dict[str, PhaseChannel] | None = None,
    ):
        self._walk_interp = PhaseInterpolator(walk_poses or WALK_KEY_POSES)
        self._run_interp = PhaseInterpolator(run_poses or RUN_KEY_POSES)
        self._walk_channels = walk_channels or WALK_CHANNELS
        self._run_channels = run_channels or RUN_CHANNELS

    def generate(
        self,
        phase: float | PhaseVariable,
        gait: GaitMode = GaitMode.WALK,
        speed: float = 1.0,
    ) -> dict[str, float]:
        """Generate a full-body pose at the given phase."""
        pose, _root_y = self._generate_pose_and_root(phase, gait=gait, speed=speed)
        return pose

    def generate_frame(
        self,
        phase: float | PhaseVariable,
        gait: GaitMode = GaitMode.WALK,
        speed: float = 1.0,
        *,
        time: float = 0.0,
        frame_index: int = 0,
        source_state: str | None = None,
        root_x: float = 0.0,
        root_rotation: float = 0.0,
        root_velocity_x: float = 0.0,
        root_velocity_y: float = 0.0,
    ) -> UnifiedMotionFrame:
        """Generate a UMR frame instead of a legacy pose dict.

        This is the new strict internal contract for the motion trunk.
        """
        p = phase.phase if isinstance(phase, PhaseVariable) else float(phase) % 1.0
        pose, root_y = self._generate_pose_and_root(phase, gait=gait, speed=speed)
        state_name = source_state or gait.value
        return pose_to_umr(
            pose,
            time=float(time),
            phase=p,
            frame_index=int(frame_index),
            source_state=state_name,
            root_transform=MotionRootTransform(
                x=float(root_x),
                y=float(root_y),
                rotation=float(root_rotation),
                velocity_x=float(root_velocity_x),
                velocity_y=float(root_velocity_y),
                angular_velocity=0.0,
            ),
            contact_tags=infer_contact_tags(p, state_name),
            metadata={
                "generator": "phase_driven_animator",
                "gait": gait.value,
                "root_y_source": "pelvis_height_curve",
            },
        )

    def _generate_pose_and_root(
        self,
        phase: float | PhaseVariable,
        gait: GaitMode = GaitMode.WALK,
        speed: float = 1.0,
    ) -> tuple[dict[str, float], float]:
        p = phase.phase if isinstance(phase, PhaseVariable) else float(phase) % 1.0
        if gait == GaitMode.RUN:
            return self._generate_run(p, speed)
        if gait == GaitMode.SNEAK:
            return self._generate_sneak(p, speed)
        return self._generate_walk(p, speed)

    def _generate_walk(self, p: float, speed: float = 1.0) -> tuple[dict[str, float], float]:
        """Generate walk pose with phase channels overlay."""
        pose, pelvis_h = self._walk_interp.evaluate(p)

        channels = self._walk_channels
        bob = channels["torso_bob"].evaluate(p)
        twist = channels["torso_twist"].evaluate(p)
        head_comp = channels["head_stabilize"].evaluate(p)

        pose["spine"] = pose.get("spine", 0.0) + bob + pelvis_h * 2.0
        pose["chest"] = pose.get("chest", 0.0) + twist
        pose["head"] = pose.get("head", 0.0) + head_comp

        speed_factor = max(0.5, min(2.0, speed))
        if speed_factor != 1.0:
            lean_extra = (speed_factor - 1.0) * 0.04
            pose["spine"] = pose.get("spine", 0.0) + lean_extra

        return pose, pelvis_h

    def _generate_run(self, p: float, speed: float = 1.0) -> tuple[dict[str, float], float]:
        """Generate run pose with phase channels overlay."""
        pose, pelvis_h = self._run_interp.evaluate(p)

        channels = self._run_channels
        bob = channels["torso_bob"].evaluate(p)
        twist = channels["torso_twist"].evaluate(p)
        head_comp = channels["head_stabilize"].evaluate(p)
        arm_pump = channels["arm_pump"].evaluate(p)

        pose["spine"] = pose.get("spine", 0.0) + bob + pelvis_h * 3.0
        pose["chest"] = pose.get("chest", 0.0) + twist
        pose["head"] = pose.get("head", 0.0) + head_comp

        pose["l_elbow"] = pose.get("l_elbow", 0.0) + arm_pump * 0.5
        pose["r_elbow"] = pose.get("r_elbow", 0.0) - arm_pump * 0.5

        speed_factor = max(0.5, min(3.0, speed))
        lean_extra = (speed_factor - 1.0) * 0.06
        pose["spine"] = pose.get("spine", 0.0) + lean_extra

        return pose, pelvis_h

    def _generate_sneak(self, p: float, speed: float = 1.0) -> tuple[dict[str, float], float]:
        """Generate sneak pose (Animator's Survival Kit p.167-175)."""
        pose, pelvis_h = self._walk_interp.evaluate(p)

        pose["spine"] = pose.get("spine", 0.0) - 0.10
        pose["l_knee"] = pose.get("l_knee", 0.0) - 0.15
        pose["r_knee"] = pose.get("r_knee", 0.0) - 0.15
        for key in ["l_shoulder", "r_shoulder"]:
            if key in pose:
                pose[key] *= 0.4
        pose["head"] = pose.get("head", 0.0) - 0.05

        return pose, pelvis_h - 0.02

    def walk_pose(self, t: float) -> dict[str, float]:
        """Convenience: generate walk pose at normalized time t ∈ [0, 1)."""
        return self.generate(t, GaitMode.WALK)

    def walk_frame(self, t: float, **kwargs: float) -> UnifiedMotionFrame:
        """Convenience: generate walk UMR frame at normalized time t ∈ [0, 1)."""
        return self.generate_frame(t, GaitMode.WALK, **kwargs)

    def run_pose(self, t: float) -> dict[str, float]:
        """Convenience: generate run pose at normalized time t ∈ [0, 1)."""
        return self.generate(t, GaitMode.RUN)

    def run_frame(self, t: float, **kwargs: float) -> UnifiedMotionFrame:
        """Convenience: generate run UMR frame at normalized time t ∈ [0, 1)."""
        return self.generate_frame(t, GaitMode.RUN, **kwargs)


# ── Drop-in Replacement Functions ───────────────────────────────────────────
# These are functional API replacements for the old presets.py functions.
# They use a module-level PhaseDrivenAnimator singleton.

_DEFAULT_ANIMATOR: PhaseDrivenAnimator | None = None


def _get_animator() -> PhaseDrivenAnimator:
    """Get or create the module-level animator singleton."""
    global _DEFAULT_ANIMATOR
    if _DEFAULT_ANIMATOR is None:
        _DEFAULT_ANIMATOR = PhaseDrivenAnimator()
    return _DEFAULT_ANIMATOR


def phase_driven_walk(t: float) -> dict[str, float]:
    """Phase-driven walk cycle — drop-in replacement for run_animation().

    Uses key-pose interpolation (Contact→Down→Pass→Up) with
    Catmull-Rom splines and DeepPhase-style secondary channels.

    Parameters
    ----------
    t : float
        Normalized time [0, 1). Full gait cycle.

    Returns
    -------
    dict[str, float] : Joint angles.
    """
    return _get_animator().walk_pose(t)


def phase_driven_run(t: float) -> dict[str, float]:
    """Phase-driven run cycle — drop-in replacement for run_animation().

    Includes flight phase, greater forward lean, and reduced arm action
    per Animator's Survival Kit guidelines.
    """
    return _get_animator().run_pose(t)


def phase_driven_walk_frame(t: float, **kwargs: float) -> UnifiedMotionFrame:
    """UMR-native walk frame generator."""
    return _get_animator().walk_frame(t, **kwargs)


def phase_driven_run_frame(t: float, **kwargs: float) -> UnifiedMotionFrame:
    """UMR-native run frame generator."""
    return _get_animator().run_frame(t, **kwargs)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _resolve_apex_height(root_y: float, root_velocity_y: float, apex_height: float | None) -> float:
    if apex_height is not None and float(apex_height) > 1e-6:
        return float(apex_height)
    ballistic_extra = max(float(root_velocity_y), 0.0) ** 2 / (2.0 * 9.81) if root_velocity_y > 0.0 else 0.0
    return max(float(root_y) + ballistic_extra, float(root_y), 0.18)


def jump_distance_phase(
    *,
    root_y: float,
    root_velocity_y: float = 0.0,
    apex_height: float | None = None,
) -> dict[str, float | bool | str]:
    """Map jump ascent state to a distance-to-apex transient phase."""
    resolved_apex = max(_resolve_apex_height(root_y, root_velocity_y, apex_height), 1e-4)
    current_height = max(float(root_y), 0.0)
    distance_to_apex = max(resolved_apex - current_height, 0.0)
    phase = _clamp01(1.0 - (distance_to_apex / resolved_apex))
    apex_window = bool(distance_to_apex <= max(0.02, resolved_apex * 0.12) or phase >= 0.88)
    return {
        "phase": float(phase),
        "phase_kind": "distance_to_apex",
        "phase_source": "distance_matching",
        "distance_to_apex": float(distance_to_apex),
        "apex_height": float(resolved_apex),
        "vertical_velocity": float(root_velocity_y),
        "is_apex_window": apex_window,
    }


def fall_distance_phase(
    *,
    root_y: float,
    ground_height: float = 0.0,
    fall_reference_height: float | None = None,
) -> dict[str, float | bool | str]:
    """Map descent state to a distance-to-ground transient phase."""
    current_height = max(float(root_y) - float(ground_height), 0.0)
    reference_height = float(fall_reference_height) if fall_reference_height is not None else max(current_height, 0.22)
    reference_height = max(reference_height, 1e-4)
    distance_to_ground = max(current_height, 0.0)
    phase = _clamp01(1.0 - (distance_to_ground / reference_height))
    landing_window = bool(distance_to_ground <= max(0.03, reference_height * 0.15) or phase >= 0.82)
    return {
        "phase": float(phase),
        "phase_kind": "distance_to_ground",
        "phase_source": "distance_matching",
        "distance_to_ground": float(distance_to_ground),
        "ground_height": float(ground_height),
        "fall_reference_height": float(reference_height),
        "is_landing_window": landing_window,
    }


def hit_recovery_phase(
    progress_driver: float = 0.0,
    *,
    damping: float = 4.0,
    impact_energy: float = 1.0,
    stability_score: float | None = None,
) -> dict[str, float | str]:
    """Map a hit reaction to a one-way recovery deficit phase.

    The returned ``phase`` follows the user's requested semantics:
    1.0 = peak stun / rigidity, 0.0 = recovered equilibrium.
    """
    energy = max(float(impact_energy), 0.0)
    if stability_score is not None:
        phase = _clamp01((1.0 - _clamp01(stability_score)) * max(energy, 1.0))
        phase_source = "action_goal_progress_stability"
    else:
        driver = max(float(progress_driver), 0.0)
        phase = _clamp01(energy * math.exp(-max(float(damping), 0.0) * driver))
        phase_source = "action_goal_progress_proxy"
    recovery_progress = _clamp01(1.0 - phase)
    return {
        "phase": float(phase),
        "phase_kind": "hit_recovery",
        "phase_source": phase_source,
        "impact_energy": float(energy),
        "impact_deficit": float(phase),
        "recovery_progress": float(recovery_progress),
        "stability_score": float(1.0 - phase),
    }


def phase_driven_jump(
    t: float,
    *,
    root_y: float | None = None,
    root_velocity_y: float = 0.0,
    apex_height: float | None = None,
) -> dict[str, float]:
    """Distance-matched jump pose driven by vertical distance to apex."""
    resolved_apex = float(apex_height) if apex_height is not None else 0.18
    current_height = float(root_y) if root_y is not None else resolved_apex * math.sin((_clamp01(t) * math.pi) / 2.0)
    metrics = jump_distance_phase(
        root_y=current_height,
        root_velocity_y=root_velocity_y,
        apex_height=apex_height,
    )
    phase = float(metrics["phase"])
    crouch = ease_in_out(_clamp01((0.20 - phase) / 0.20))
    launch = ease_in_out(_clamp01(phase / 0.70))
    apex = ease_in_out(_clamp01((phase - 0.62) / 0.38))

    return {
        "spine": -0.18 * crouch + 0.12 * launch + 0.04 * apex,
        "chest": -0.05 * crouch + 0.06 * launch - 0.03 * apex,
        "head": -0.04 * crouch + 0.03 * apex,
        "l_hip": -0.26 * crouch + 0.16 * launch + 0.12 * apex,
        "r_hip": -0.26 * crouch + 0.16 * launch - 0.12 * apex,
        "l_knee": -0.52 * crouch - 0.04 * launch - 0.20 * apex,
        "r_knee": -0.52 * crouch - 0.04 * launch - 0.08 * apex,
        "l_foot": 0.08 * (1.0 - apex),
        "r_foot": 0.08 * (1.0 - apex),
        "l_shoulder": 0.22 * crouch - 0.58 * launch - 0.28 * apex,
        "r_shoulder": 0.22 * crouch - 0.58 * launch - 0.18 * apex,
        "l_elbow": 0.12 + 0.12 * crouch + 0.18 * apex,
        "r_elbow": 0.12 + 0.12 * crouch + 0.18 * apex,
    }


def phase_driven_fall(
    t: float,
    *,
    root_y: float | None = None,
    ground_height: float = 0.0,
    fall_reference_height: float | None = None,
) -> dict[str, float]:
    """Distance-matched falling pose driven by distance to ground."""
    reference_height = float(fall_reference_height) if fall_reference_height is not None else 0.22
    current_height = float(root_y) if root_y is not None else reference_height * (1.0 - _clamp01(t))
    metrics = fall_distance_phase(
        root_y=current_height,
        ground_height=ground_height,
        fall_reference_height=fall_reference_height,
    )
    phase = float(metrics["phase"])
    stretch = 1.0 - phase
    brace = ease_in_out(_clamp01((phase - 0.40) / 0.60))
    landing = ease_in_out(_clamp01((phase - 0.78) / 0.22))

    return {
        "spine": -0.05 * stretch - 0.18 * landing,
        "chest": -0.02 * stretch + 0.05 * landing,
        "head": 0.03 * stretch - 0.06 * landing,
        "l_shoulder": -0.60 * stretch + 0.22 * landing,
        "r_shoulder": -0.55 * stretch + 0.26 * landing,
        "l_elbow": 0.20 + 0.10 * landing,
        "r_elbow": 0.22 + 0.12 * landing,
        "l_hip": 0.10 * stretch - 0.22 * landing,
        "r_hip": -0.10 * stretch - 0.22 * landing,
        "l_knee": -0.16 * stretch - 0.54 * brace,
        "r_knee": -0.12 * stretch - 0.54 * brace,
        "l_foot": -0.05 * stretch + 0.06 * landing,
        "r_foot": -0.03 * stretch + 0.06 * landing,
    }


def phase_driven_hit(
    t: float,
    *,
    damping: float = 4.0,
    impact_energy: float = 1.0,
    stability_score: float | None = None,
) -> dict[str, float]:
    """Goal-progress hit reaction driven by recovery deficit rather than time slices."""
    metrics = hit_recovery_phase(
        t,
        damping=damping,
        impact_energy=impact_energy,
        stability_score=stability_score,
    )
    phase = float(metrics["phase"])
    recovery = float(metrics["recovery_progress"])

    return {
        "spine": -0.30 * phase + 0.06 * recovery,
        "chest": 0.22 * phase - 0.05 * recovery,
        "head": -0.38 * phase + 0.04 * recovery,
        "l_shoulder": 0.42 * phase - 0.10 * recovery,
        "r_shoulder": 0.46 * phase - 0.12 * recovery,
        "l_elbow": 0.22 + 0.12 * phase,
        "r_elbow": 0.22 + 0.12 * phase,
        "l_hip": -0.12 * phase + 0.03 * recovery,
        "r_hip": -0.12 * phase + 0.03 * recovery,
        "l_knee": -0.14 * phase,
        "r_knee": -0.14 * phase,
    }


def phase_driven_jump_frame(
    t: float,
    **kwargs: float,
) -> UnifiedMotionFrame:
    """UMR-native jump frame driven by distance to apex."""
    time = float(kwargs.pop("time", 0.0))
    frame_index = int(kwargs.pop("frame_index", 0))
    source_state = str(kwargs.pop("source_state", "jump"))
    root_x = float(kwargs.pop("root_x", 0.0))
    root_y = float(kwargs.pop("root_y", 0.0))
    root_rotation = float(kwargs.pop("root_rotation", 0.0))
    root_velocity_x = float(kwargs.pop("root_velocity_x", 0.0))
    root_velocity_y = float(kwargs.pop("root_velocity_y", 0.0))
    apex_height = kwargs.pop("apex_height", None)
    metrics = jump_distance_phase(root_y=root_y, root_velocity_y=root_velocity_y, apex_height=apex_height)
    pose = phase_driven_jump(t, root_y=root_y, root_velocity_y=root_velocity_y, apex_height=apex_height)
    contact = bool(root_y <= 1e-3 and float(metrics["phase"]) <= 0.08)
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
        contact_tags=MotionContactState(left_foot=contact, right_foot=contact),
        metadata={
            "generator": "phase_driven_jump_distance_matching",
            **metrics,
        },
    )


def phase_driven_fall_frame(
    t: float,
    **kwargs: float,
) -> UnifiedMotionFrame:
    """UMR-native fall frame driven by distance to ground."""
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
    metrics = fall_distance_phase(
        root_y=root_y,
        ground_height=ground_height,
        fall_reference_height=fall_reference_height,
    )
    pose = phase_driven_fall(
        t,
        root_y=root_y,
        ground_height=ground_height,
        fall_reference_height=fall_reference_height,
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
            "generator": "phase_driven_fall_distance_matching",
            **metrics,
        },
    )


def phase_driven_hit_frame(
    t: float,
    **kwargs: float,
) -> UnifiedMotionFrame:
    """UMR-native hit frame driven by recovery deficit progress."""
    time = float(kwargs.pop("time", 0.0))
    frame_index = int(kwargs.pop("frame_index", 0))
    source_state = str(kwargs.pop("source_state", "hit"))
    root_x = float(kwargs.pop("root_x", 0.0))
    root_y = float(kwargs.pop("root_y", 0.0))
    root_rotation = float(kwargs.pop("root_rotation", 0.0))
    root_velocity_x = float(kwargs.pop("root_velocity_x", 0.0))
    root_velocity_y = float(kwargs.pop("root_velocity_y", 0.0))
    damping = float(kwargs.pop("damping", 4.0))
    impact_energy = float(kwargs.pop("impact_energy", 1.0))
    stability_score = kwargs.pop("stability_score", None)
    metrics = hit_recovery_phase(
        t,
        damping=damping,
        impact_energy=impact_energy,
        stability_score=stability_score,
    )
    pose = phase_driven_hit(
        t,
        damping=damping,
        impact_energy=impact_energy,
        stability_score=stability_score,
    )
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
        contact_tags=MotionContactState(left_foot=True, right_foot=True),
        metadata={
            "generator": "phase_driven_hit_recovery",
            **metrics,
            "damping": damping,
        },
    )


# ── Phase Analysis Utilities (DeepPhase-inspired) ───────────────────────────


def extract_phase_parameters(
    signal: np.ndarray,
    sample_rate: float = 30.0,
) -> dict[str, float]:
    """Extract sinusoidal approximation parameters from a motion signal.

    Implements the DeepPhase/PAE parameter extraction via FFT:
    Γ(x) = A·sin(2π(Fx - S)) + B

    Parameters
    ----------
    signal : np.ndarray
        1D temporal signal (e.g., joint angle over time).
    sample_rate : float
        Samples per second.

    Returns
    -------
    dict with keys: amplitude, frequency, phase_shift, offset
    """
    N = len(signal)
    if N < 4:
        return {"amplitude": 0.0, "frequency": 0.0, "phase_shift": 0.0, "offset": 0.0}

    T = N / sample_rate  # Total duration in seconds

    # FFT
    coeffs = np.fft.rfft(signal)
    K = len(coeffs) - 1  # Number of positive frequency bins

    # Offset B = c₀/N (DC component)
    B = np.real(coeffs[0]) / N

    # Magnitudes and power spectrum
    magnitudes = np.abs(coeffs)
    freqs = np.fft.rfftfreq(N, d=1.0 / sample_rate)

    # Power spectrum (single-sided, doubled except DC)
    power = np.zeros(K + 1)
    power[0] = magnitudes[0] ** 2 / (2 * N)
    power[1:] = 2 * magnitudes[1:] ** 2 / N

    # Mean frequency F (power-weighted, excluding DC)
    total_power = np.sum(power[1:])
    if total_power > 1e-12:
        F = np.sum(freqs[1:] * power[1:]) / total_power
    else:
        F = 0.0

    # Amplitude A (preserves average power)
    if total_power > 1e-12:
        A = math.sqrt(2.0 / N * np.sum(power[1:]))
    else:
        A = 0.0

    # Phase shift S via 2D representation
    if F > 1e-12:
        t_vals = np.arange(N) / sample_rate
        centered = signal - B
        sx = np.sum(centered * np.cos(2 * math.pi * F * t_vals))
        sy = np.sum(centered * np.sin(2 * math.pi * F * t_vals))
        S = math.atan2(sy, sx) / (2 * math.pi)
    else:
        S = 0.0

    return {
        "amplitude": float(A),
        "frequency": float(F),
        "phase_shift": float(S),
        "offset": float(B),
    }


def create_phase_channel_from_signal(
    signal: np.ndarray,
    sample_rate: float = 30.0,
) -> PhaseChannel:
    """Create a PhaseChannel from an observed motion signal.

    This is the bridge between DeepPhase analysis and our
    phase-driven animation system: observe a motion, extract
    its periodic structure, and create a reusable channel.

    Parameters
    ----------
    signal : np.ndarray
        1D temporal signal.
    sample_rate : float
        Samples per second.

    Returns
    -------
    PhaseChannel : Fitted phase channel.
    """
    params = extract_phase_parameters(signal, sample_rate)
    return PhaseChannel(
        amplitude=params["amplitude"],
        frequency=params["frequency"],
        phase_shift=params["phase_shift"],
        offset=params["offset"],
    )

