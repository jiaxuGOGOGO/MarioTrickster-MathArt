"""Gap B3: Phase-Preserving Gait Transition Blending.

This module implements **Marker-based Dynamic Time Warping (DTW)** for
seamless walk/run/sneak transitions without foot sliding.

Architecture
============

The system is grounded in six research pillars:

1. **David Rosen (GDC 2014)** — Stride Wheel + Synchronized Blend:
   Animation playback is driven by actual distance traveled; walk/run
   keyframes and stride sizes are blended at any intermediate speed.

2. **UE Sync Groups / Sync Markers** — Leader-Follower architecture:
   The dominant gait (by weight) becomes Leader; Followers warp their
   playback to align sync markers (foot contacts) with the Leader.

3. **Bruderlin & Williams (1995)** — Motion Signal Processing / DTW:
   Motions are treated as time-series signals; DTW finds optimal
   alignment before interpolation.

4. **Kovar & Gleicher (2003)** — Registration Curves:
   Automatic timewarp curve construction via constraint matching
   (foot contact events).

5. **Ménardais et al. (2004)** — Support-Phase Synchronization:
   Gait cycles decomposed into support phases; phase boundaries
   establish correspondence for linear time warping.

6. **Rune Skovbo Johansen (2009)** — Semi-Procedural Locomotion:
   Cycle alignment for walk/run synchronization before blending.

Key Concepts
============

- **SyncMarker**: Named phase events (e.g., left_foot_down=0.0,
  right_foot_down=0.5) that must align across gaits during blending.

- **PhaseWarper**: Given a Leader phase and marker map, computes the
  corresponding Follower phase so markers are temporally aligned.

- **GaitBlendState**: Tracks per-gait phase, stride, and channels
  for continuous blending without discontinuities.

- **GaitBlender**: The main orchestrator that produces blended poses
  from multiple gaits at any interpolation weight.

Integration
===========

``GaitBlender`` consumes ``PhaseInterpolator`` instances from
``phase_driven.py`` and produces standard ``dict[str, float]`` poses
compatible with the UMR pipeline.

References
----------
.. [1] Rosen, D. "Animation Bootcamp: An Indie Approach to Procedural
       Animation." GDC 2014.
.. [2] Epic Games. "Sync Groups." Unreal Engine Documentation.
.. [3] Bruderlin, A. & Williams, L. "Motion Signal Processing."
       SIGGRAPH 1995.
.. [4] Kovar, L. & Gleicher, M. "Flexible Automatic Motion Blending
       with Registration Curves." SCA 2003.
.. [5] Ménardais, S. et al. "Synchronization for Dynamic Blending of
       Motions." SCA 2004.
.. [6] Johansen, R.S. "Automated Semi-Procedural Animation for
       Character Locomotion." 2009.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .phase_driven import (
    GaitMode,
    KeyPose,
    PhaseChannel,
    PhaseInterpolator,
    PhaseVariable,
    PhaseDrivenAnimator,
    WALK_KEY_POSES,
    RUN_KEY_POSES,
    WALK_CHANNELS,
    RUN_CHANNELS,
    _catmull_rom,
)


# ── Sync Marker System ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class SyncMarker:
    """A named synchronization point within a gait cycle.

    Sync markers define phase positions where specific biomechanical
    events occur (foot contacts). During blending, the system warps
    playback rates to align these markers across gaits.

    Attributes
    ----------
    name : str
        Human-readable marker name (e.g., "left_foot_down").
    phase : float
        Phase position [0, 1) within the gait cycle.
    """
    name: str
    phase: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "phase", float(self.phase) % 1.0)


# Standard bipedal sync markers (Rosen GDC 2014 + UE Sync Groups)
BIPEDAL_SYNC_MARKERS: tuple[SyncMarker, ...] = (
    SyncMarker(name="left_foot_down", phase=0.0),
    SyncMarker(name="right_foot_down", phase=0.5),
)


@dataclass(frozen=True)
class GaitSyncProfile:
    """Complete synchronization profile for a gait mode.

    Encapsulates the sync markers, stride parameters, and stepping
    frequency for a specific gait, enabling the PhaseWarper to
    compute aligned phases across different gaits.

    Attributes
    ----------
    gait : GaitMode
        The gait this profile describes.
    markers : tuple[SyncMarker, ...]
        Ordered sync markers for this gait cycle.
    stride_length : float
        Distance covered per full gait cycle (two steps), in world units.
    steps_per_second : float
        Base stepping frequency (steps/sec). Walk≈2.0, Run≈3.0.
    bounce_amplitude : float
        Vertical bounce amplitude at reference speed.
    """
    gait: GaitMode
    markers: tuple[SyncMarker, ...] = BIPEDAL_SYNC_MARKERS
    stride_length: float = 1.0
    steps_per_second: float = 2.0
    bounce_amplitude: float = 0.015

    @property
    def cycle_duration(self) -> float:
        """Duration of one full gait cycle in seconds."""
        return 2.0 / max(self.steps_per_second, 0.01)

    @property
    def cycle_velocity(self) -> float:
        """Ground velocity during this gait at reference speed."""
        return self.stride_length / max(self.cycle_duration, 0.001)


# Predefined profiles (Animator's Survival Kit parameters)
WALK_SYNC_PROFILE = GaitSyncProfile(
    gait=GaitMode.WALK,
    markers=BIPEDAL_SYNC_MARKERS,
    stride_length=0.8,        # ~0.8m per cycle (two steps)
    steps_per_second=2.0,     # 12 frames @ 24fps per step
    bounce_amplitude=0.015,
)

RUN_SYNC_PROFILE = GaitSyncProfile(
    gait=GaitMode.RUN,
    markers=BIPEDAL_SYNC_MARKERS,
    stride_length=2.0,        # ~2.0m per cycle (two steps)
    steps_per_second=3.0,     # 8 frames @ 24fps per step
    bounce_amplitude=0.025,
)

SNEAK_SYNC_PROFILE = GaitSyncProfile(
    gait=GaitMode.SNEAK,
    markers=BIPEDAL_SYNC_MARKERS,
    stride_length=0.5,        # Shorter strides
    steps_per_second=1.5,     # Slower cadence
    bounce_amplitude=0.008,
)


# ── Phase Warper ─────────────────────────────────────────────────────────────


def _marker_segment(markers: tuple[SyncMarker, ...], phase: float) -> tuple[int, float]:
    """Find which marker segment a phase falls in and the local t within it.

    Returns
    -------
    tuple[int, float]
        (segment_index, local_t) where segment_index is the index of the
        marker at the start of the segment, and local_t ∈ [0, 1) is the
        fractional position within that segment.
    """
    n = len(markers)
    if n == 0:
        return (0, 0.0)
    if n == 1:
        return (0, phase)

    phase = phase % 1.0
    sorted_markers = sorted(markers, key=lambda m: m.phase)

    for i in range(n):
        start = sorted_markers[i].phase
        end = sorted_markers[(i + 1) % n].phase
        if end <= start:
            end += 1.0  # Wrap around

        p = phase
        if p < start and i == n - 1:
            p += 1.0  # Handle wrap for last segment

        if start <= p < end:
            span = end - start
            local_t = (p - start) / max(span, 1e-9)
            return (i, local_t)

    # Fallback: last segment
    return (n - 1, 0.0)


def phase_warp(
    leader_phase: float,
    leader_markers: tuple[SyncMarker, ...],
    follower_markers: tuple[SyncMarker, ...],
) -> float:
    """Warp a follower's phase to align with the leader's sync markers.

    This is the core of the Marker-based DTW approach:
    1. Determine which marker segment the leader is in
    2. Find the local position within that segment
    3. Map to the corresponding follower segment at the same local position

    This ensures that when the leader's left foot hits the ground,
    the follower's left foot also hits the ground — regardless of
    different cycle lengths or stepping frequencies.

    Parameters
    ----------
    leader_phase : float
        Current phase of the leader gait [0, 1).
    leader_markers : tuple[SyncMarker, ...]
        Sync markers for the leader gait.
    follower_markers : tuple[SyncMarker, ...]
        Sync markers for the follower gait.

    Returns
    -------
    float
        Warped phase for the follower [0, 1).

    Examples
    --------
    >>> phase_warp(0.25, BIPEDAL_SYNC_MARKERS, BIPEDAL_SYNC_MARKERS)
    0.25
    >>> # With identical markers, phase passes through unchanged
    """
    if not leader_markers or not follower_markers:
        return leader_phase % 1.0

    seg_idx, local_t = _marker_segment(leader_markers, leader_phase)

    # Map to follower's corresponding segment
    f_sorted = sorted(follower_markers, key=lambda m: m.phase)
    n_f = len(f_sorted)

    # Use modular index to handle different marker counts
    f_idx = seg_idx % n_f
    f_start = f_sorted[f_idx].phase
    f_end = f_sorted[(f_idx + 1) % n_f].phase
    if f_end <= f_start:
        f_end += 1.0

    warped = f_start + local_t * (f_end - f_start)
    return warped % 1.0


# ── Stride Wheel ─────────────────────────────────────────────────────────────


@dataclass
class StrideWheel:
    """David Rosen's Stride Wheel: ties animation phase to distance traveled.

    The wheel circumference equals the stride length. As the character
    moves, the wheel "rolls" and the angular position directly drives
    the animation phase. This guarantees foot-ground synchronization.

    Attributes
    ----------
    circumference : float
        Stride length (distance per full gait cycle).
    _distance : float
        Accumulated distance traveled.
    """
    circumference: float = 1.0
    _distance: float = field(default=0.0, repr=False)

    @property
    def phase(self) -> float:
        """Current phase derived from accumulated distance."""
        if self.circumference <= 0:
            return 0.0
        return (self._distance / self.circumference) % 1.0

    def advance(self, distance_delta: float) -> float:
        """Advance the wheel by a distance increment.

        Parameters
        ----------
        distance_delta : float
            Distance moved this frame (can be derived from velocity * dt).

        Returns
        -------
        float
            New phase value [0, 1).
        """
        self._distance += abs(float(distance_delta))
        return self.phase

    def set_circumference(self, new_circumference: float) -> None:
        """Update stride length while preserving current phase.

        When circumference changes (e.g., walk→run transition), the
        accumulated distance is rescaled so that the current phase
        remains unchanged. This prevents phase discontinuities that
        would cause visible foot sliding.

        This is the critical detail from Rosen's Stride Wheel:
        the wheel "re-calibrates" its accumulated rotation when
        the stride length changes, rather than letting the phase
        jump to a new value.
        """
        new_c = max(float(new_circumference), 0.001)
        if self.circumference > 0 and abs(new_c - self.circumference) > 1e-9:
            # Preserve current phase by rescaling distance
            current_phase = self.phase
            self.circumference = new_c
            self._distance = current_phase * new_c
        else:
            self.circumference = new_c

    def reset(self) -> None:
        """Reset accumulated distance."""
        self._distance = 0.0


# ── Gait Blend State ─────────────────────────────────────────────────────────


@dataclass
class GaitBlendLayer:
    """State for a single gait within the blending system.

    Tracks the gait's own phase, weight, and interpolator reference
    so the blender can evaluate and mix multiple gaits simultaneously.

    Attributes
    ----------
    profile : GaitSyncProfile
        Sync profile for this gait.
    weight : float
        Blend weight [0, 1]. Sum of all layer weights should be 1.0.
    phase : float
        Current phase of this gait layer [0, 1).
    interpolator : PhaseInterpolator
        Pose interpolator for this gait.
    channels : dict[str, PhaseChannel]
        Secondary motion channels for this gait.
    """
    profile: GaitSyncProfile
    weight: float = 0.0
    phase: float = 0.0
    interpolator: PhaseInterpolator = field(default=None, repr=False)  # type: ignore[assignment]
    channels: dict[str, PhaseChannel] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.interpolator is None:
            if self.profile.gait == GaitMode.RUN:
                self.interpolator = PhaseInterpolator(RUN_KEY_POSES)
                if not self.channels:
                    self.channels = dict(RUN_CHANNELS)
            else:
                self.interpolator = PhaseInterpolator(WALK_KEY_POSES)
                if not self.channels:
                    self.channels = dict(WALK_CHANNELS)


# ── Bounce Gravity (Rosen) ───────────────────────────────────────────────────


def adaptive_bounce(
    phase: float,
    speed: float,
    base_amplitude: float = 0.015,
    reference_speed: float = 1.0,
) -> float:
    """Compute speed-adaptive vertical bounce (David Rosen's insight).

    Gravity is constant, so faster movement = less time per stride =
    shallower bounce trajectory. The bounce height scales inversely
    with speed.

    Parameters
    ----------
    phase : float
        Current gait phase [0, 1).
    speed : float
        Current movement speed (1.0 = walk reference).
    base_amplitude : float
        Bounce amplitude at reference speed.
    reference_speed : float
        Speed at which base_amplitude applies.

    Returns
    -------
    float
        Vertical offset for this phase and speed.
    """
    # Bounce is 2x frequency (once per step, two steps per cycle)
    bounce_signal = math.sin(4.0 * math.pi * phase)
    # Amplitude decreases with speed (Rosen: "flatter the faster he goes")
    speed_ratio = max(reference_speed, 0.01) / max(speed, 0.01)
    amplitude = base_amplitude * min(speed_ratio, 2.0)
    return amplitude * bounce_signal


# ── GaitBlender: Main Orchestrator ───────────────────────────────────────────


class GaitBlender:
    """Phase-preserving gait transition blender.

    Implements the full Gap B3 pipeline:
    1. Stride Wheel drives phase from distance (Rosen)
    2. Leader-Follower selection by weight (UE Sync Groups)
    3. Phase Warping aligns sync markers (Kovar Registration Curves)
    4. Pose interpolation in aligned phase space (Bruderlin DTW)
    5. Stride length blending (Rosen Synchronized Blend)
    6. Adaptive bounce (Rosen Bounce Gravity)

    Parameters
    ----------
    walk_profile : GaitSyncProfile, optional
        Walk gait profile. Defaults to WALK_SYNC_PROFILE.
    run_profile : GaitSyncProfile, optional
        Run gait profile. Defaults to RUN_SYNC_PROFILE.
    sneak_profile : GaitSyncProfile, optional
        Sneak gait profile. Defaults to SNEAK_SYNC_PROFILE.
    """

    def __init__(
        self,
        walk_profile: GaitSyncProfile | None = None,
        run_profile: GaitSyncProfile | None = None,
        sneak_profile: GaitSyncProfile | None = None,
    ):
        self._layers: dict[GaitMode, GaitBlendLayer] = {
            GaitMode.WALK: GaitBlendLayer(
                profile=walk_profile or WALK_SYNC_PROFILE,
                weight=1.0,
            ),
            GaitMode.RUN: GaitBlendLayer(
                profile=run_profile or RUN_SYNC_PROFILE,
                weight=0.0,
            ),
            GaitMode.SNEAK: GaitBlendLayer(
                profile=sneak_profile or SNEAK_SYNC_PROFILE,
                weight=0.0,
            ),
        }
        self._stride_wheel = StrideWheel(
            circumference=WALK_SYNC_PROFILE.stride_length
        )
        self._blend_speed: float = 4.0  # Weight transition speed (1/s)
        self._current_speed: float = 1.0

    @property
    def layers(self) -> dict[GaitMode, GaitBlendLayer]:
        """Access blend layers (read-only view)."""
        return dict(self._layers)

    @property
    def leader(self) -> GaitMode:
        """The gait with the highest blend weight (Leader)."""
        return max(self._layers, key=lambda g: self._layers[g].weight)

    @property
    def active_gaits(self) -> list[GaitMode]:
        """Gaits with non-zero blend weight."""
        return [g for g, layer in self._layers.items() if layer.weight > 1e-6]

    @property
    def blended_stride_length(self) -> float:
        """Current blended stride length from all active gaits."""
        total = 0.0
        for layer in self._layers.values():
            total += layer.weight * layer.profile.stride_length
        return max(total, 0.001)

    @property
    def blended_steps_per_second(self) -> float:
        """Current blended stepping frequency."""
        total = 0.0
        for layer in self._layers.values():
            total += layer.weight * layer.profile.steps_per_second
        return max(total, 0.01)

    @property
    def blended_bounce_amplitude(self) -> float:
        """Current blended bounce amplitude."""
        total = 0.0
        for layer in self._layers.values():
            total += layer.weight * layer.profile.bounce_amplitude
        return total

    def set_target_gait(self, gait: GaitMode) -> None:
        """Set the target gait for blending transition.

        Weights will smoothly transition toward the target gait
        during subsequent ``update()`` calls.

        Parameters
        ----------
        gait : GaitMode
            Target gait to transition to.
        """
        self._target_gait = gait

    def set_weights(self, weights: dict[GaitMode, float]) -> None:
        """Directly set blend weights (must sum to ~1.0).

        Parameters
        ----------
        weights : dict[GaitMode, float]
            Weight per gait mode.
        """
        total = sum(weights.values())
        if total < 1e-9:
            return
        for gait, w in weights.items():
            if gait in self._layers:
                self._layers[gait].weight = max(0.0, w / total)

    def set_blend_speed(self, speed: float) -> None:
        """Set the weight transition speed.

        Parameters
        ----------
        speed : float
            Transition speed in weight-units per second.
            Higher = faster transitions.
        """
        self._blend_speed = max(0.1, float(speed))

    def update(
        self,
        dt: float,
        velocity: float = 1.0,
        target_gait: GaitMode | None = None,
    ) -> dict[str, float]:
        """Advance the blender by one time step and produce a blended pose.

        This is the main entry point for the gait blending pipeline:

        1. Update blend weights toward target gait
        2. Compute blended stride length
        3. Advance stride wheel by velocity * dt
        4. Determine leader phase from stride wheel
        5. Warp follower phases to align with leader
        6. Evaluate each gait's pose at its warped phase
        7. Blend poses by weight
        8. Add adaptive bounce

        Parameters
        ----------
        dt : float
            Time step in seconds.
        velocity : float
            Current movement speed (world units/second).
        target_gait : GaitMode, optional
            If provided, sets the target gait for weight transition.

        Returns
        -------
        dict[str, float]
            Blended pose (joint angles + special keys).
        """
        if target_gait is not None:
            self._target_gait = target_gait
        self._current_speed = max(abs(float(velocity)), 0.001)

        # Step 1: Update blend weights
        self._update_weights(dt)

        # Step 2: Update stride wheel circumference
        self._stride_wheel.set_circumference(self.blended_stride_length)

        # Step 3: Advance stride wheel
        distance = self._current_speed * dt
        leader_phase = self._stride_wheel.advance(distance)

        # Step 4: Determine leader
        leader_gait = self.leader
        leader_layer = self._layers[leader_gait]
        leader_layer.phase = leader_phase

        # Step 5: Warp follower phases
        for gait, layer in self._layers.items():
            if gait == leader_gait:
                continue
            if layer.weight < 1e-6:
                layer.phase = leader_phase  # Keep in sync even when inactive
                continue
            layer.phase = phase_warp(
                leader_phase,
                leader_layer.profile.markers,
                layer.profile.markers,
            )

        # Step 6 & 7: Evaluate and blend poses
        blended_pose = self._blend_poses()

        # Step 8: Add adaptive bounce
        bounce = adaptive_bounce(
            leader_phase,
            self._current_speed,
            self.blended_bounce_amplitude,
        )
        blended_pose["_root_y"] = blended_pose.get("_root_y", 0.0) + bounce
        blended_pose["_phase"] = leader_phase
        blended_pose["_leader"] = float(list(GaitMode).index(leader_gait))
        blended_pose["_stride_length"] = self.blended_stride_length
        blended_pose["_bounce"] = bounce

        return blended_pose

    def _update_weights(self, dt: float) -> None:
        """Smoothly transition weights toward target gait."""
        target = getattr(self, "_target_gait", GaitMode.WALK)
        rate = self._blend_speed * dt

        for gait, layer in self._layers.items():
            target_w = 1.0 if gait == target else 0.0
            diff = target_w - layer.weight
            if abs(diff) < 1e-6:
                layer.weight = target_w
            else:
                layer.weight += diff * min(rate, 1.0)
                layer.weight = max(0.0, min(1.0, layer.weight))

        # Normalize weights
        total = sum(l.weight for l in self._layers.values())
        if total > 1e-9:
            for layer in self._layers.values():
                layer.weight /= total

    def _blend_poses(self) -> dict[str, float]:
        """Evaluate each active gait and blend by weight.

        The key insight from Rosen and Kovar: blending happens in
        phase-aligned space, so feet are synchronized before
        interpolation occurs.
        """
        blended: dict[str, float] = {}
        total_weight = 0.0
        blended_pelvis = 0.0

        for gait, layer in self._layers.items():
            if layer.weight < 1e-6:
                continue

            # Evaluate pose at warped phase
            pose, pelvis_h = layer.interpolator.evaluate(layer.phase)

            # Apply secondary channels
            for ch_name, channel in layer.channels.items():
                ch_val = channel.evaluate(layer.phase)
                if ch_name == "torso_bob":
                    pose["spine"] = pose.get("spine", 0.0) + ch_val + pelvis_h * 2.0
                elif ch_name == "torso_twist":
                    pose["chest"] = pose.get("chest", 0.0) + ch_val
                elif ch_name == "head_stabilize":
                    pose["head"] = pose.get("head", 0.0) + ch_val
                elif ch_name == "arm_pump":
                    pose["l_elbow"] = pose.get("l_elbow", 0.0) + ch_val * 0.5
                    pose["r_elbow"] = pose.get("r_elbow", 0.0) - ch_val * 0.5

            # Weighted accumulation
            w = layer.weight
            for joint, value in pose.items():
                blended[joint] = blended.get(joint, 0.0) + value * w
            blended_pelvis += pelvis_h * w
            total_weight += w

        blended["_root_y"] = blended_pelvis
        return blended

    def get_blend_state(self) -> dict[str, Any]:
        """Return current blend state for debugging/serialization."""
        return {
            "leader": self.leader.value,
            "active_gaits": [g.value for g in self.active_gaits],
            "weights": {g.value: l.weight for g, l in self._layers.items()},
            "phases": {g.value: l.phase for g, l in self._layers.items()},
            "stride_length": self.blended_stride_length,
            "steps_per_second": self.blended_steps_per_second,
            "bounce_amplitude": self.blended_bounce_amplitude,
            "speed": self._current_speed,
        }

    def generate_frame(
        self,
        dt: float,
        velocity: float = 1.0,
        target_gait: GaitMode | None = None,
        *,
        time: float = 0.0,
        frame_index: int = 0,
    ) -> dict[str, Any]:
        """Generate a complete frame with metadata for UMR integration.

        Parameters
        ----------
        dt : float
            Time step.
        velocity : float
            Movement speed.
        target_gait : GaitMode, optional
            Target gait for transition.
        time : float
            Absolute time for the frame.
        frame_index : int
            Frame sequence number.

        Returns
        -------
        dict[str, Any]
            Pose + metadata dict compatible with UMR pipeline.
        """
        pose = self.update(dt, velocity, target_gait)
        state = self.get_blend_state()

        return {
            "pose": {k: v for k, v in pose.items() if not k.startswith("_")},
            "root_y": pose.get("_root_y", 0.0),
            "phase": pose.get("_phase", 0.0),
            "leader": state["leader"],
            "weights": state["weights"],
            "stride_length": state["stride_length"],
            "bounce": pose.get("_bounce", 0.0),
            "time": time,
            "frame_index": frame_index,
            "metadata": {
                "generator": "gait_blender",
                "gap": "B3",
                "research_refs": [
                    "Rosen_GDC2014",
                    "UE_SyncGroups",
                    "Bruderlin1995",
                    "Kovar2003",
                    "Menardais2004",
                    "Johansen2009",
                ],
            },
        }


# ── Convenience Functions ────────────────────────────────────────────────────


def blend_walk_run(
    phase: float,
    alpha: float,
    speed: float = 1.0,
) -> tuple[dict[str, float], float]:
    """Quick walk-run blend at a given phase and blend factor.

    This is a stateless convenience function for simple use cases
    where the full GaitBlender state machine is not needed.

    Parameters
    ----------
    phase : float
        Current gait phase [0, 1).
    alpha : float
        Blend factor: 0.0 = pure walk, 1.0 = pure run.
    speed : float
        Movement speed for adaptive bounce.

    Returns
    -------
    tuple[dict[str, float], float]
        (blended_pose, root_y_offset)
    """
    alpha = max(0.0, min(1.0, float(alpha)))

    walk_interp = PhaseInterpolator(WALK_KEY_POSES)
    run_interp = PhaseInterpolator(RUN_KEY_POSES)

    # Both use same markers, so phase_warp is identity
    walk_pose, walk_h = walk_interp.evaluate(phase)
    run_pose, run_h = run_interp.evaluate(phase)

    # Blend poses
    blended: dict[str, float] = {}
    all_joints = set(walk_pose.keys()) | set(run_pose.keys())
    for joint in all_joints:
        w_val = walk_pose.get(joint, 0.0)
        r_val = run_pose.get(joint, 0.0)
        blended[joint] = w_val * (1.0 - alpha) + r_val * alpha

    # Blend pelvis height
    root_y = walk_h * (1.0 - alpha) + run_h * alpha

    # Blend stride parameters for bounce
    bounce_amp = WALK_SYNC_PROFILE.bounce_amplitude * (1.0 - alpha) + \
                 RUN_SYNC_PROFILE.bounce_amplitude * alpha
    bounce = adaptive_bounce(phase, speed, bounce_amp)
    root_y += bounce

    return blended, root_y


def blend_gaits_at_phase(
    phase: float,
    weights: dict[GaitMode, float],
    speed: float = 1.0,
) -> tuple[dict[str, float], float]:
    """Multi-gait blend at a given phase with arbitrary weights.

    Parameters
    ----------
    phase : float
        Current gait phase [0, 1).
    weights : dict[GaitMode, float]
        Per-gait blend weights (will be normalized).
    speed : float
        Movement speed for adaptive bounce.

    Returns
    -------
    tuple[dict[str, float], float]
        (blended_pose, root_y_offset)
    """
    # Normalize weights
    total = sum(weights.values())
    if total < 1e-9:
        weights = {GaitMode.WALK: 1.0}
        total = 1.0
    norm_weights = {g: w / total for g, w in weights.items()}

    # Determine leader (highest weight)
    leader_gait = max(norm_weights, key=lambda g: norm_weights[g])

    # Profile lookup
    profiles = {
        GaitMode.WALK: WALK_SYNC_PROFILE,
        GaitMode.RUN: RUN_SYNC_PROFILE,
        GaitMode.SNEAK: SNEAK_SYNC_PROFILE,
    }
    leader_profile = profiles.get(leader_gait, WALK_SYNC_PROFILE)

    # Interpolator lookup
    interpolators = {
        GaitMode.WALK: PhaseInterpolator(WALK_KEY_POSES),
        GaitMode.RUN: PhaseInterpolator(RUN_KEY_POSES),
        GaitMode.SNEAK: PhaseInterpolator(WALK_KEY_POSES),  # Sneak uses walk base
    }

    blended: dict[str, float] = {}
    root_y = 0.0
    bounce_amp = 0.0

    for gait, w in norm_weights.items():
        if w < 1e-6:
            continue

        profile = profiles.get(gait, WALK_SYNC_PROFILE)
        interp = interpolators.get(gait, interpolators[GaitMode.WALK])

        # Warp phase for followers
        if gait == leader_gait:
            warped_phase = phase
        else:
            warped_phase = phase_warp(
                phase,
                leader_profile.markers,
                profile.markers,
            )

        pose, pelvis_h = interp.evaluate(warped_phase)

        # Apply sneak modifications
        if gait == GaitMode.SNEAK:
            pose["spine"] = pose.get("spine", 0.0) - 0.10
            pose["l_knee"] = pose.get("l_knee", 0.0) - 0.15
            pose["r_knee"] = pose.get("r_knee", 0.0) - 0.15
            for key in ["l_shoulder", "r_shoulder"]:
                if key in pose:
                    pose[key] *= 0.4
            pose["head"] = pose.get("head", 0.0) - 0.05
            pelvis_h -= 0.02

        # Accumulate
        for joint, value in pose.items():
            blended[joint] = blended.get(joint, 0.0) + value * w
        root_y += pelvis_h * w
        bounce_amp += profile.bounce_amplitude * w

    bounce = adaptive_bounce(phase, speed, bounce_amp)
    root_y += bounce

    return blended, root_y
