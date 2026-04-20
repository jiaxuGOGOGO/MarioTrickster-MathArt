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
from typing import Optional, Callable, Any, Mapping

import numpy as np

from .curves import ease_in_out
from .unified_motion import (
    PhaseState,
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
        phase: float | PhaseVariable | PhaseState,
        gait: GaitMode = GaitMode.WALK,
        speed: float = 1.0,
    ) -> dict[str, float]:
        """Generate a full-body pose at the given phase.

        Supports PhaseState for unified cyclic/transient handling.
        """
        if isinstance(phase, PhaseState) and not phase.is_cyclic:
            pose, _root_y = self._generate_transient_pose(phase, speed=speed)
            return pose
        pose, _root_y = self._generate_pose_and_root(phase, gait=gait, speed=speed)
        return pose

    def generate_frame(
        self,
        phase: float | PhaseVariable | PhaseState,
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
        """Generate a UMR frame — the ABSOLUTE UNIFIED ENTRY POINT.

        This method implements the Generalized Phase State gate mechanism
        inspired by Local Motion Phases (SIGGRAPH 2020) and DeepPhase
        (SIGGRAPH 2022). It accepts PhaseState, PhaseVariable, or bare
        float and routes through the appropriate interpolation path:

          - Cyclic (is_cyclic=True):  sin/cos trig mapping → Catmull-Rom.
          - Transient (is_cyclic=False): direct scalar [0,1] → Bezier/spline.

        This replaces the old adapter-bypass pattern where transient motions
        had to go through a separate code path.
        """
        # --- Phase State Resolution (Multiplexer Input) ---
        if isinstance(phase, PhaseState):
            ps = phase
            p = ps.to_float()
        elif isinstance(phase, PhaseVariable):
            p = phase.phase
            ps = PhaseState.cyclic(p)
        else:
            p = float(phase) % 1.0
            ps = PhaseState.cyclic(p)

        # --- Gate Mechanism: Cyclic vs Transient ---
        if ps.is_cyclic:
            # Cyclic path: standard trig-mapped Catmull-Rom interpolation
            pose, root_y = self._generate_pose_and_root(p, gait=gait, speed=speed)
        else:
            # Transient path: direct scalar as Bezier/spline time parameter
            # The transient pose generators (jump/fall/hit) use p directly
            # as a [0,1] progress value, bypassing trig mapping.
            pose, root_y = self._generate_transient_pose(ps, speed=speed)

        state_name = source_state or (ps.phase_kind if not ps.is_cyclic else gait.value)
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
                "gait": gait.value if ps.is_cyclic else ps.phase_kind,
                "root_y_source": "pelvis_height_curve" if ps.is_cyclic else "transient_spline",
                "phase_gate": "cyclic" if ps.is_cyclic else "transient",
            },
            phase_state=ps,
        )

    def _generate_transient_pose(
        self,
        ps: PhaseState,
        speed: float = 1.0,
    ) -> tuple[dict[str, float], float]:
        """Generate pose for transient (non-cyclic) motions via direct spline mapping.

        The PhaseState.value is used directly as the Bezier/spline time parameter t,
        bypassing the sin/cos trig mapping used for cyclic locomotion.
        This is the key architectural change from Gap 1 research:
        transient motions are no longer forced into a cyclic phase topology.
        """
        t = ps.to_float()  # Direct [0,1] scalar, no trig wrapping
        amplitude = ps.amplitude
        kind = ps.phase_kind

        if kind == "distance_to_apex":
            pose = phase_driven_jump(t)
            root_y = 0.18 * math.sin(t * math.pi)  # Parabolic arc approximation
            return pose, root_y * amplitude
        elif kind == "distance_to_ground":
            pose = phase_driven_fall(t)
            root_y = 0.22 * (1.0 - t)  # Descending
            return pose, root_y * amplitude
        elif kind == "hit_recovery":
            pose = phase_driven_hit(t)
            root_y = 0.0  # Grounded during hit
            return pose, root_y
        else:
            # Generic transient: use ease_in_out as default spline
            pose = phase_driven_jump(t)  # Fallback to jump-like pose
            root_y = 0.0
            return pose, root_y

    def _generate_pose_and_root(
        self,
        phase: float | PhaseVariable | PhaseState,
        gait: GaitMode = GaitMode.WALK,
        speed: float = 1.0,
    ) -> tuple[dict[str, float], float]:
        if isinstance(phase, PhaseState):
            p = phase.to_float()
        elif isinstance(phase, PhaseVariable):
            p = phase.phase
        else:
            p = float(phase) % 1.0
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


_PHASE_DRIVEN_STATE_REGISTRY: dict[str, dict[str, object]] = {
    "idle": {
        "is_cyclic": True,
        "phase_kind": "idle",
        "gait": None,
        "default_steps_per_second": 1.0,
    },
    "walk": {
        "is_cyclic": True,
        "phase_kind": "walk",
        "gait": GaitMode.WALK,
        "default_steps_per_second": 2.0,
    },
    "run": {
        "is_cyclic": True,
        "phase_kind": "run",
        "gait": GaitMode.RUN,
        "default_steps_per_second": 3.0,
    },
    "sneak": {
        "is_cyclic": True,
        "phase_kind": "sneak",
        "gait": GaitMode.SNEAK,
        "default_steps_per_second": 1.5,
    },
    "sprint": {
        "is_cyclic": True,
        "phase_kind": "run",
        "gait": GaitMode.RUN,
        "default_steps_per_second": 3.4,
    },
    "jump": {
        "is_cyclic": False,
        "phase_kind": "distance_to_apex",
        "gait": None,
        "default_steps_per_second": 1.0,
    },
    "apex": {
        "is_cyclic": False,
        "phase_kind": "distance_to_apex",
        "gait": None,
        "default_steps_per_second": 1.0,
    },
    "fall": {
        "is_cyclic": False,
        "phase_kind": "distance_to_ground",
        "gait": None,
        "default_steps_per_second": 1.0,
    },
    "ground_contact": {
        "is_cyclic": True,
        "phase_kind": "idle",
        "gait": None,
        "default_steps_per_second": 1.0,
    },
    "hit": {
        "is_cyclic": False,
        "phase_kind": "hit_recovery",
        "gait": None,
        "default_steps_per_second": 1.0,
    },
    "stable_balance": {
        "is_cyclic": True,
        "phase_kind": "idle",
        "gait": None,
        "default_steps_per_second": 1.0,
    },
    "dead": {
        "is_cyclic": False,
        "phase_kind": "dead",
        "gait": None,
        "default_steps_per_second": 0.0,
    },
}

_PHASE_DRIVEN_TRANSITION_TEMPLATE: dict[str, tuple[str, ...]] = {
    "idle": ("walk", "run", "sneak", "jump", "fall", "hit", "dead"),
    "walk": ("idle", "run", "sneak", "jump", "fall", "hit", "dead"),
    "run": ("idle", "walk", "sneak", "sprint", "jump", "fall", "hit", "dead"),
    "sneak": ("idle", "walk", "run", "jump", "fall", "hit", "dead"),
    "sprint": ("run", "jump", "fall", "hit", "dead"),
    "jump": ("apex", "fall", "hit", "dead"),
    "apex": ("fall", "hit", "dead"),
    "fall": ("ground_contact", "hit", "dead"),
    "ground_contact": ("idle", "walk", "run", "sneak", "stable_balance", "dead"),
    "hit": ("stable_balance", "dead"),
    "stable_balance": ("idle", "walk", "run", "sneak", "jump", "fall", "hit", "dead"),
    "dead": (),
}

PHASE_DRIVEN_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    state: frozenset({state, *targets})
    for state, targets in _PHASE_DRIVEN_TRANSITION_TEMPLATE.items()
}


class IllegalStateTransitionError(ValueError):
    """Safe, typed guard failure for rejected phase-driven transitions."""

    def __init__(self, current_state: str, target_state: str, allowed_targets: tuple[str, ...]):
        self.current_state = str(current_state)
        self.target_state = str(target_state)
        self.allowed_targets = tuple(allowed_targets)
        allowed_text = ", ".join(self.allowed_targets) if self.allowed_targets else "<none>"
        super().__init__(
            f"Illegal phase-driven transition {self.current_state!r} -> {self.target_state!r}; "
            f"allowed targets: {allowed_text}"
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "error": "illegal_state_transition",
            "current_state": self.current_state,
            "target_state": self.target_state,
            "allowed_targets": list(self.allowed_targets),
        }


def _normalize_phase_driven_state_name(state: str) -> str:
    return str(state).strip().lower()


class PhaseDrivenStateMachine:
    """Data-driven guarded controller for phase-driven animation states.

    The machine keeps the legal transition graph as a first-class contract.
    Transition checks are O(1) set lookups, and rejected requests leave all
    mutable runtime variables unchanged.
    """

    def __init__(
        self,
        current_state: str = "idle",
        *,
        phase_clock: float = 0.0,
        transition_blend_weight: float = 1.0,
        allowed_transitions: Mapping[str, frozenset[str]] | None = None,
        animator: PhaseDrivenAnimator | None = None,
    ) -> None:
        self.allowed_transitions = {
            _normalize_phase_driven_state_name(state): frozenset(
                _normalize_phase_driven_state_name(target) for target in targets
            )
            for state, targets in dict(allowed_transitions or PHASE_DRIVEN_ALLOWED_TRANSITIONS).items()
        }
        normalized_state = _normalize_phase_driven_state_name(current_state)
        if normalized_state not in self.allowed_transitions:
            raise ValueError(f"Unknown phase-driven state: {current_state!r}")
        self.current_state = normalized_state
        self.phase_clock = self._sanitize_phase_clock(normalized_state, phase_clock)
        self.transition_blend_weight = _clamp01(transition_blend_weight)
        self.cycle_count = 0
        self.last_transition_error: IllegalStateTransitionError | None = None
        self._animator = animator or _get_animator()

    def _state_descriptor(self, state: str | None = None) -> dict[str, object]:
        normalized_state = _normalize_phase_driven_state_name(state or self.current_state)
        return dict(_PHASE_DRIVEN_STATE_REGISTRY.get(normalized_state, _PHASE_DRIVEN_STATE_REGISTRY["idle"]))

    def _sanitize_phase_clock(self, state: str, value: float) -> float:
        descriptor = self._state_descriptor(state)
        if bool(descriptor["is_cyclic"]):
            return float(value) % 1.0
        return _clamp01(value)

    def snapshot(self) -> dict[str, float | int | str]:
        descriptor = self._state_descriptor()
        return {
            "current_state": self.current_state,
            "phase_clock": float(self.phase_clock),
            "transition_blend_weight": float(self.transition_blend_weight),
            "cycle_count": int(self.cycle_count),
            "phase_kind": str(descriptor["phase_kind"]),
        }

    def allowed_targets_for(self, state: str | None = None) -> frozenset[str]:
        normalized_state = _normalize_phase_driven_state_name(state or self.current_state)
        return self.allowed_transitions.get(normalized_state, frozenset())

    def can_transition_to(self, target_state: str) -> bool:
        normalized_target = _normalize_phase_driven_state_name(target_state)
        return normalized_target in self.allowed_targets_for(self.current_state)

    def transition_to(
        self,
        target_state: str,
        *,
        strict: bool = False,
        phase_clock: float = 0.0,
        blend_weight: float | None = None,
    ) -> bool:
        """Attempt to move to ``target_state`` under an explicit graph guard.

        When the edge is not declared, the machine returns ``False`` by default
        and preserves ``current_state``, ``phase_clock``, and
        ``transition_blend_weight`` exactly. Callers that prefer typed failures
        can set ``strict=True`` to receive ``IllegalStateTransitionError``.
        """
        normalized_target = _normalize_phase_driven_state_name(target_state)
        allowed_targets = self.allowed_targets_for(self.current_state)
        if normalized_target not in allowed_targets:
            error = IllegalStateTransitionError(
                self.current_state,
                normalized_target,
                tuple(sorted(allowed_targets)),
            )
            self.last_transition_error = error
            if strict:
                raise error
            return False

        if normalized_target != self.current_state:
            self.current_state = normalized_target
            self.phase_clock = self._sanitize_phase_clock(normalized_target, phase_clock)
            self.transition_blend_weight = _clamp01(blend_weight) if blend_weight is not None else 0.0
        elif blend_weight is not None:
            self.transition_blend_weight = _clamp01(blend_weight)

        self.last_transition_error = None
        return True

    def set_phase_clock(self, value: float) -> float:
        self.phase_clock = self._sanitize_phase_clock(self.current_state, value)
        return self.phase_clock

    def set_transition_blend_weight(self, value: float) -> float:
        self.transition_blend_weight = _clamp01(value)
        return self.transition_blend_weight

    def advance(
        self,
        dt: float,
        *,
        speed: float = 1.0,
        steps_per_second: float | None = None,
        progress_scale: float = 1.0,
    ) -> float:
        """Advance the internal phase clock without bypassing state guards."""
        descriptor = self._state_descriptor()
        if bool(descriptor["is_cyclic"]):
            effective_sps = float(steps_per_second) if steps_per_second is not None else float(descriptor["default_steps_per_second"])
            previous_phase = self.phase_clock
            delta = max(float(dt), 0.0) * max(float(speed), 0.0) * effective_sps / 2.0
            self.phase_clock = (self.phase_clock + delta) % 1.0
            if self.phase_clock < previous_phase and delta > 0.0:
                self.cycle_count += 1
            return self.phase_clock

        delta = max(float(dt), 0.0) * max(float(speed), 0.0) * max(float(progress_scale), 0.0)
        self.phase_clock = _clamp01(self.phase_clock + delta)
        return self.phase_clock

    def current_phase_state(self) -> PhaseState:
        descriptor = self._state_descriptor()
        phase_kind = str(descriptor["phase_kind"])
        if bool(descriptor["is_cyclic"]):
            return PhaseState.cyclic(self.phase_clock, phase_kind=phase_kind)
        return PhaseState.transient(self.phase_clock, phase_kind=phase_kind, amplitude=1.0)

    def generate_frame(
        self,
        *,
        time: float = 0.0,
        frame_index: int = 0,
        root_x: float = 0.0,
        root_y: float = 0.0,
        root_rotation: float = 0.0,
        root_velocity_x: float = 0.0,
        root_velocity_y: float = 0.0,
    ) -> UnifiedMotionFrame:
        """Generate a frame for the machine's current legal state."""
        descriptor = self._state_descriptor()
        gait = descriptor.get("gait")
        state_name = self.current_state

        if state_name in {"idle", "stable_balance", "ground_contact"}:
            from .phase_driven_idle import phase_driven_idle_frame

            return phase_driven_idle_frame(
                self.phase_clock,
                time=time,
                frame_index=frame_index,
                source_state=state_name,
                root_x=root_x,
                root_y=root_y,
                root_velocity_x=root_velocity_x,
                root_velocity_y=root_velocity_y,
            )

        if state_name == "dead":
            pose = phase_driven_hit(1.0, impact_energy=1.0)
            return pose_to_umr(
                pose,
                time=float(time),
                phase=float(_clamp01(self.phase_clock)),
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
                contact_tags=MotionContactState(left_foot=True, right_foot=True),
                metadata={
                    "generator": "phase_driven_state_machine_dead_pose",
                    "phase_gate": "terminal",
                    "phase_kind": "dead",
                },
                phase_state=PhaseState.transient(self.phase_clock, phase_kind="dead", amplitude=1.0),
            )

        resolved_gait = gait if isinstance(gait, GaitMode) else GaitMode.RUN
        return self._animator.generate_frame(
            self.current_phase_state(),
            gait=resolved_gait,
            time=time,
            frame_index=frame_index,
            source_state=state_name,
            root_x=root_x,
            root_rotation=root_rotation,
            root_velocity_x=root_velocity_x,
            root_velocity_y=root_velocity_y,
        )


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


@dataclass(frozen=True)
class TransientPhaseVariable:
    """Explicit non-cyclic transient phase state carried through the UMR bus.

    The contract keeps both the deficit-like state and its velocity so that
    recovery can be audited and distilled later instead of being reduced to a
    single scalar too early.
    """

    deficit: float
    velocity: float
    progress: float
    target_state: str
    contact_expectation: str
    window_signal: bool
    phase_source: str

    def to_metadata(self) -> dict[str, float | bool | str]:
        recovery_velocity = max(-float(self.velocity), 0.0)
        return {
            "phase": float(_clamp01(self.deficit)),
            "impact_deficit": float(_clamp01(self.deficit)),
            "deficit_velocity": float(self.velocity),
            "recovery_velocity": float(recovery_velocity),
            "recovery_progress": float(_clamp01(self.progress)),
            "target_state": str(self.target_state),
            "contact_expectation": str(self.contact_expectation),
            "window_signal": bool(self.window_signal),
            "is_recovery_complete": bool(self.window_signal),
            "phase_source": str(self.phase_source),
        }


def _halflife_to_damping(half_life: float, eps: float = 1e-5) -> float:
    return (4.0 * math.log(2.0)) / (max(float(half_life), eps) + eps)


def _critical_decay_step(value: float, velocity: float, half_life: float, dt: float) -> tuple[float, float]:
    y = _halflife_to_damping(half_life) / 2.0
    j1 = float(velocity) + float(value) * y
    eydt = math.exp(-y * max(float(dt), 0.0))
    next_value = eydt * (float(value) + j1 * max(float(dt), 0.0))
    next_velocity = eydt * (float(velocity) - j1 * y * max(float(dt), 0.0))
    return next_value, next_velocity


def critically_damped_hit_phase(
    elapsed: float,
    *,
    impact_energy: float = 1.0,
    half_life: float = 0.18,
    recovery_velocity: float = 0.0,
) -> TransientPhaseVariable:
    """Construct a critically damped hit recovery variable.

    ``deficit`` is the user-facing hit phase: 1.0 means peak stun, 0.0 means
    stable balance restored. The underlying velocity is preserved so Layer 3
    can distinguish between near-rest and still-recovering states.
    """
    deficit0 = _clamp01(impact_energy)
    velocity0 = -abs(float(recovery_velocity))
    deficit, deficit_velocity = _critical_decay_step(
        deficit0,
        velocity0,
        half_life=max(float(half_life), 1e-4),
        dt=max(float(elapsed), 0.0),
    )
    deficit = _clamp01(deficit)
    is_complete = bool(deficit <= 0.02 and abs(deficit_velocity) <= 0.05)
    if is_complete:
        deficit = 0.0
        deficit_velocity = 0.0
    return TransientPhaseVariable(
        deficit=deficit,
        velocity=deficit_velocity,
        progress=_clamp01(1.0 - deficit),
        target_state="stable_balance",
        contact_expectation="planted_recovery",
        window_signal=is_complete,
        phase_source="critical_damped_recovery",
    )


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
    apex_window_threshold = max(0.02, resolved_apex * 0.12)
    apex_window = bool(distance_to_apex <= apex_window_threshold or phase >= 0.88)
    return {
        "phase": float(phase),
        "phase_kind": "distance_to_apex",
        "phase_source": "distance_matching",
        "distance_to_apex": float(distance_to_apex),
        "distance_window": float(apex_window_threshold),
        "target_distance": 0.0,
        "apex_height": float(resolved_apex),
        "vertical_velocity": float(root_velocity_y),
        "target_state": "apex",
        "contact_expectation": "apex_window" if apex_window else "airborne",
        "desired_contact_state": "airborne",
        "is_apex_window": apex_window,
        "window_signal": apex_window,
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
    landing_window_threshold = max(0.03, reference_height * 0.15)
    landing_window = bool(distance_to_ground <= landing_window_threshold or phase >= 0.82)
    landing_preparation = ease_in_out(_clamp01((phase - 0.42) / 0.58))
    return {
        "phase": float(phase),
        "phase_kind": "distance_to_ground",
        "phase_source": "distance_matching",
        "distance_to_ground": float(distance_to_ground),
        "distance_window": float(landing_window_threshold),
        "target_distance": 0.0,
        "ground_height": float(ground_height),
        "fall_reference_height": float(reference_height),
        "landing_preparation": float(landing_preparation),
        "target_state": "ground_contact",
        "contact_expectation": "landing_window" if landing_window else "airborne",
        "desired_contact_state": "ground_contact",
        "is_landing_window": landing_window,
        "window_signal": landing_window,
    }


def hit_recovery_phase(
    progress_driver: float = 0.0,
    *,
    damping: float = 4.0,
    impact_energy: float = 1.0,
    stability_score: float | None = None,
    half_life: float | None = None,
    recovery_velocity: float = 0.0,
) -> dict[str, float | bool | str]:
    """Map a hit reaction to a one-way critically damped recovery phase.

    The returned ``phase`` follows the user's requested semantics:
    1.0 = peak stun / rigidity, 0.0 = recovered equilibrium.
    """
    energy = _clamp01(impact_energy)
    if stability_score is not None:
        phase = _clamp01((1.0 - _clamp01(stability_score)) * max(energy, 1.0))
        recovery_progress = _clamp01(1.0 - phase)
        is_complete = bool(phase <= 0.02)
        return {
            "phase": float(phase),
            "phase_kind": "hit_recovery",
            "phase_source": "action_goal_progress_stability",
            "impact_energy": float(energy),
            "impact_deficit": float(phase),
            "deficit_velocity": 0.0,
            "recovery_velocity": 0.0,
            "recovery_progress": float(recovery_progress),
            "stability_score": float(1.0 - phase),
            "target_state": "stable_balance",
            "contact_expectation": "planted_recovery",
            "window_signal": is_complete,
            "is_recovery_complete": is_complete,
        }

    elapsed = max(float(progress_driver), 0.0)
    resolved_half_life = float(half_life) if half_life is not None else max(0.12, (math.log(2.0) / max(float(damping), 1e-4)))
    transient = critically_damped_hit_phase(
        elapsed,
        impact_energy=energy,
        half_life=resolved_half_life,
        recovery_velocity=recovery_velocity,
    )
    metadata = transient.to_metadata()
    metadata.update(
        {
            "phase_kind": "hit_recovery",
            "impact_energy": float(energy),
            "stability_score": float(1.0 - transient.deficit),
            "half_life": float(resolved_half_life),
            "damping": float(damping),
        }
    )
    return metadata




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
    half_life: float | None = None,
    recovery_velocity: float = 0.0,
) -> dict[str, float]:
    """Critically damped hit reaction driven by recovery deficit, not time slices."""
    metrics = hit_recovery_phase(
        t,
        damping=damping,
        impact_energy=impact_energy,
        stability_score=stability_score,
        half_life=half_life,
        recovery_velocity=recovery_velocity,
    )
    phase = float(metrics["phase"])
    recovery = float(metrics["recovery_progress"])
    recovery_speed = float(metrics.get("recovery_velocity", 0.0) or 0.0)
    settle = ease_in_out(_clamp01(recovery * 1.15))

    return {
        "spine": -0.30 * phase + 0.06 * recovery + 0.03 * settle,
        "chest": 0.22 * phase - 0.05 * recovery - 0.02 * settle,
        "head": -0.38 * phase + 0.04 * recovery + 0.02 * settle,
        "l_shoulder": 0.42 * phase - 0.10 * recovery - 0.04 * settle,
        "r_shoulder": 0.46 * phase - 0.12 * recovery - 0.05 * settle,
        "l_elbow": 0.22 + 0.12 * phase + 0.03 * recovery_speed,
        "r_elbow": 0.22 + 0.12 * phase + 0.03 * recovery_speed,
        "l_hip": -0.12 * phase + 0.03 * recovery + 0.02 * settle,
        "r_hip": -0.12 * phase + 0.03 * recovery + 0.02 * settle,
        "l_knee": -0.14 * phase - 0.02 * recovery_speed,
        "r_knee": -0.14 * phase - 0.02 * recovery_speed,
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
    """UMR-native hit frame driven by critically damped recovery deficit."""
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
    half_life = kwargs.pop("half_life", None)
    recovery_velocity = float(kwargs.pop("recovery_velocity", 0.0))
    metrics = hit_recovery_phase(
        t,
        damping=damping,
        impact_energy=impact_energy,
        stability_score=stability_score,
        half_life=half_life,
        recovery_velocity=recovery_velocity,
    )
    pose = phase_driven_hit(
        t,
        damping=damping,
        impact_energy=impact_energy,
        stability_score=stability_score,
        half_life=half_life,
        recovery_velocity=recovery_velocity,
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
            "half_life": float(metrics.get("half_life", half_life or 0.0) or 0.0),
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

