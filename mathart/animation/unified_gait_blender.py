from __future__ import annotations

"""SESSION-069 — Unified phase-driven gait and transition motion hub.

This module folds the historical ``gait_blend`` and
``transition_synthesizer`` implementations into a single math core that
operates on continuous phase manifolds and decaying residual states.

Design goals
------------

The implementation follows three constraints.

First, **locomotion phase, gait blending, and transition residuals are
computed inside one numerical trunk**. There is no runtime branching that
hands off to a separate gait engine or transition engine. A single core
maintains contiguous joint layouts, phase-aligned gait evaluation, and
inertialized residual correction.

Second, **feature memory is data-oriented**. Runtime joint values are packed
into NumPy arrays according to a stable layout so phase blending and
transition residual updates operate on contiguous vectors instead of sparse
per-joint dictionaries.

Third, **transition continuity preserves target contacts while maintaining
source-side C0/C1 continuity** through residual capture at the switch frame
and smooth residual decay over time.
"""

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Optional, Sequence

import numpy as np

from .deepphase_fft import PhaseBlender, PhaseManifoldPoint, extract_deepphase_channels
from .phase_driven import (
    GaitMode,
    PhaseChannel,
    PhaseInterpolator,
    RUN_CHANNELS,
    RUN_KEY_POSES,
    WALK_CHANNELS,
    WALK_KEY_POSES,
)
from .presets import fall_animation, hit_animation, jump_animation
from .presets import idle_animation
from .skeleton import Skeleton
from .unified_motion import (
    MotionContactState,
    MotionRootTransform,
    UnifiedMotionFrame,
    pose_to_umr,
)


# ---------------------------------------------------------------------------
# Sync Markers and gait profiles
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SyncMarker:
    """A named sync event on the normalized gait cycle."""

    name: str
    phase: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "phase", float(self.phase) % 1.0)


BIPEDAL_SYNC_MARKERS: tuple[SyncMarker, ...] = (
    SyncMarker("left_foot_down", 0.0),
    SyncMarker("right_foot_down", 0.5),
)


@dataclass(frozen=True)
class GaitSyncProfile:
    """Continuous gait metadata used by the unified motion hub."""

    gait: GaitMode
    stride_length: float
    steps_per_second: float
    markers: tuple[SyncMarker, ...] = BIPEDAL_SYNC_MARKERS
    bounce_amplitude: float = 0.015

    @property
    def cycle_duration(self) -> float:
        return 2.0 / max(self.steps_per_second, 1e-6)

    @property
    def cycle_velocity(self) -> float:
        return self.stride_length / max(self.cycle_duration, 1e-6)


WALK_SYNC_PROFILE = GaitSyncProfile(
    gait=GaitMode.WALK,
    stride_length=0.8,
    steps_per_second=2.0,
    bounce_amplitude=0.018,
)
RUN_SYNC_PROFILE = GaitSyncProfile(
    gait=GaitMode.RUN,
    stride_length=2.0,
    steps_per_second=3.0,
    bounce_amplitude=0.010,
)
SNEAK_SYNC_PROFILE = GaitSyncProfile(
    gait=GaitMode.SNEAK,
    stride_length=0.5,
    steps_per_second=1.5,
    bounce_amplitude=0.022,
)


# ---------------------------------------------------------------------------
# Phase alignment helpers
# ---------------------------------------------------------------------------


@dataclass
class StrideWheel:
    """David Rosen's stride wheel with phase-preserving circumference updates."""

    circumference: float = 1.0
    _distance: float = field(default=0.0, repr=False)

    @property
    def phase(self) -> float:
        if self.circumference <= 0.0:
            return 0.0
        return (self._distance / self.circumference) % 1.0

    def advance(self, distance_delta: float) -> float:
        self._distance += abs(float(distance_delta))
        return self.phase

    def set_circumference(self, new_circumference: float) -> None:
        new_c = max(float(new_circumference), 1e-3)
        if abs(self.circumference - new_c) <= 1e-9:
            self.circumference = new_c
            return
        current_phase = self.phase
        self.circumference = new_c
        self._distance = current_phase * new_c

    def reset(self) -> None:
        self._distance = 0.0


@dataclass
class GaitBlendLayer:
    """Per-gait runtime state for unified locomotion blending."""

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


def _marker_segment(markers: Sequence[SyncMarker], phase: float) -> tuple[int, float]:
    if len(markers) < 2:
        return 0, float(phase) % 1.0
    p = float(phase) % 1.0
    ordered = sorted(markers, key=lambda m: m.phase)
    for idx, current in enumerate(ordered):
        if idx > 0 and abs(p - current.phase) <= 1e-9:
            return idx, 0.0
        nxt = ordered[(idx + 1) % len(ordered)]
        start = current.phase
        end = nxt.phase if idx + 1 < len(ordered) else nxt.phase + 1.0
        test_p = p if p >= start else p + 1.0
        is_last = idx == len(ordered) - 1
        in_segment = (start <= test_p < end) or (is_last and start <= test_p <= end)
        if in_segment:
            denom = max(end - start, 1e-8)
            return idx, (test_p - start) / denom
    return 0, 0.0


def phase_warp(
    leader_phase: float,
    leader_markers: Sequence[SyncMarker],
    follower_markers: Sequence[SyncMarker],
) -> float:
    """Piecewise-linear marker alignment on the normalized gait cycle."""

    if not leader_markers or not follower_markers:
        return float(leader_phase) % 1.0
    if len(leader_markers) != len(follower_markers):
        return float(leader_phase) % 1.0
    seg_idx, local_t = _marker_segment(leader_markers, leader_phase)
    follower_sorted = sorted(follower_markers, key=lambda m: m.phase)
    current = follower_sorted[seg_idx]
    nxt = follower_sorted[(seg_idx + 1) % len(follower_sorted)]
    start = current.phase
    end = nxt.phase if seg_idx + 1 < len(follower_sorted) else nxt.phase + 1.0
    return (start + (end - start) * local_t) % 1.0


def adaptive_bounce(
    phase: float,
    speed: float,
    base_amplitude: float = 0.015,
    reference_speed: float = 1.0,
) -> float:
    bounce_signal = math.sin(4.0 * math.pi * (float(phase) % 1.0))
    speed_ratio = max(reference_speed, 0.01) / max(abs(speed), 0.01)
    amplitude = base_amplitude * min(speed_ratio, 2.0)
    return amplitude * bounce_signal


# ---------------------------------------------------------------------------
# Transition residual helpers
# ---------------------------------------------------------------------------


class TransitionStrategy(str, Enum):
    INERTIALIZATION = "inertialization"
    DEAD_BLENDING = "dead_blending"


@dataclass
class TransitionQualityMetrics:
    strategy: str = "none"
    total_displacement: float = 0.0
    peak_offset: float = 0.0
    contact_preservation: float = 1.0
    velocity_continuity: float = 1.0
    smoothness: float = 1.0
    foot_sliding: float = 0.0
    frames_processed: int = 0
    joint_count: int = 0
    elapsed: float = 0.0

    def to_dict(self) -> dict[str, float | int | str]:
        return {
            "strategy": self.strategy,
            "total_displacement": float(self.total_displacement),
            "peak_offset": float(self.peak_offset),
            "contact_preservation": float(self.contact_preservation),
            "velocity_continuity": float(self.velocity_continuity),
            "smoothness": float(self.smoothness),
            "foot_sliding": float(self.foot_sliding),
            "frames_processed": int(self.frames_processed),
            "joint_count": int(self.joint_count),
            "elapsed": float(self.elapsed),
        }


@dataclass(frozen=True)
class UnifiedGaitRuntimeConfig:
    """Once-resolved scalar inputs for the unified gait hot path."""

    blend_time: float = 0.2
    phase_weight: float = 1.0
    parameter_source: str = "defaults"


def resolve_unified_gait_runtime_config(
    runtime_distillation_bus: Any | None = None,
    *,
    blend_time: float = 0.2,
    phase_weight: float = 1.0,
) -> UnifiedGaitRuntimeConfig:
    """Resolve gait parameters exactly once, outside the frame hot path."""

    resolved_blend_time = max(float(blend_time), 1e-3)
    resolved_phase_weight = float(np.clip(phase_weight, 0.0, 1.0))
    resolver = getattr(runtime_distillation_bus, "resolve_scalar", None)
    if callable(resolver):
        resolved_blend_time = max(float(resolver([
            "physics_gait.blend_time",
            "blend_time",
            "gait_blend_time",
            "transition_blend_time",
        ], resolved_blend_time)), 1e-3)
        resolved_phase_weight = float(np.clip(resolver([
            "physics_gait.phase_weight",
            "phase_weight",
            "gait_phase_weight",
            "phase_alignment_weight",
        ], resolved_phase_weight), 0.0, 1.0))
        return UnifiedGaitRuntimeConfig(
            blend_time=resolved_blend_time,
            phase_weight=resolved_phase_weight,
            parameter_source="runtime_distillation_bus",
        )
    return UnifiedGaitRuntimeConfig(
        blend_time=resolved_blend_time,
        phase_weight=resolved_phase_weight,
        parameter_source="defaults",
    )


@dataclass
class _TransitionMetricAccumulators:
    velocity_continuity_sum: float = 0.0
    contact_preservation_sum: float = 0.0
    smoothness_sum: float = 0.0
    foot_sliding_sum: float = 0.0
    prev_root_velocity: np.ndarray = field(default_factory=lambda: np.zeros(3, dtype=np.float64), repr=False)
    prev_displacement: float = 0.0


@dataclass
class _ResidualState:
    offset: np.ndarray
    velocity: np.ndarray


@dataclass
class _VectorLayout:
    joint_names: tuple[str, ...]
    joint_index: dict[str, int]

    @classmethod
    def from_frames(cls, frames: Sequence[UnifiedMotionFrame]) -> "_VectorLayout":
        names: list[str] = []
        seen: set[str] = set()
        for frame in frames:
            for joint in frame.joint_local_rotations.keys():
                if joint not in seen:
                    seen.add(joint)
                    names.append(joint)
        return cls(tuple(names), {name: i for i, name in enumerate(names)})

    @classmethod
    def from_pose_dicts(cls, poses: Sequence[Mapping[str, float]]) -> "_VectorLayout":
        names: list[str] = []
        seen: set[str] = set()
        for pose in poses:
            for joint in pose.keys():
                if joint.startswith("_"):
                    continue
                if joint not in seen:
                    seen.add(joint)
                    names.append(joint)
        return cls(tuple(names), {name: i for i, name in enumerate(names)})

    def to_array(self, pose: Mapping[str, float]) -> np.ndarray:
        values = np.zeros(len(self.joint_names), dtype=np.float64)
        for joint, idx in self.joint_index.items():
            values[idx] = float(pose.get(joint, 0.0))
        return values

    def to_pose(self, values: np.ndarray) -> dict[str, float]:
        return {joint: float(values[idx]) for joint, idx in self.joint_index.items()}


@dataclass(frozen=True)
class UnifiedMotionSample:
    pose: dict[str, float]
    root_y: float
    phase: float
    stride_length: float
    leader: GaitMode
    weights: dict[str, float]
    fft_phase: float


@dataclass
class _FFTSignature:
    point: PhaseManifoldPoint
    channels: dict[str, list[PhaseManifoldPoint]]


# ---------------------------------------------------------------------------
# Unified motion core
# ---------------------------------------------------------------------------


class UnifiedGaitBlender:
    """Single-source motion core for phase blending and transition residuals."""

    def __init__(
        self,
        walk_profile: GaitSyncProfile | None = None,
        run_profile: GaitSyncProfile | None = None,
        sneak_profile: GaitSyncProfile | None = None,
        *,
        transition_strategy: TransitionStrategy = TransitionStrategy.DEAD_BLENDING,
        blend_time: float = 0.2,
        decay_halflife: float = 0.05,
        fft_lock_strength: float = 0.35,
        phase_weight: float = 1.0,
    ):
        self._layers: dict[GaitMode, GaitBlendLayer] = {
            GaitMode.WALK: GaitBlendLayer(profile=walk_profile or WALK_SYNC_PROFILE, weight=1.0),
            GaitMode.RUN: GaitBlendLayer(profile=run_profile or RUN_SYNC_PROFILE, weight=0.0),
            GaitMode.SNEAK: GaitBlendLayer(profile=sneak_profile or SNEAK_SYNC_PROFILE, weight=0.0),
        }
        self._stride_wheel = StrideWheel(circumference=self._layers[GaitMode.WALK].profile.stride_length)
        self._blend_speed = 4.0
        self._current_speed = 1.0
        self._target_gait = GaitMode.WALK
        self.transition_strategy = transition_strategy
        self.blend_time = max(float(blend_time), 1e-3)
        self.decay_halflife = max(float(decay_halflife), 1e-4)
        self.fft_lock_strength = float(np.clip(fft_lock_strength, 0.0, 1.0))
        self.phase_weight = float(np.clip(phase_weight, 0.0, 1.0))
        self._layout = _VectorLayout.from_pose_dicts([
            WALK_KEY_POSES[0].joints,
            RUN_KEY_POSES[0].joints,
        ])
        self._residual_layout = self._layout
        zeros = np.zeros(len(self._residual_layout.joint_names), dtype=np.float64)
        self._joint_residual = _ResidualState(offset=zeros.copy(), velocity=zeros.copy())
        self._root_residual = _ResidualState(offset=np.zeros(3, dtype=np.float64), velocity=np.zeros(3, dtype=np.float64))
        self._transition_elapsed = 0.0
        self._transition_active = False
        self._transition_metrics = TransitionQualityMetrics(strategy=self.transition_strategy.value)
        self._transition_accumulators = _TransitionMetricAccumulators()
        self._fft_signatures = self._build_fft_signatures()

    # ---- gait state helpers -------------------------------------------------

    @property
    def layers(self) -> dict[GaitMode, GaitBlendLayer]:
        return dict(self._layers)

    @property
    def leader(self) -> GaitMode:
        return max(self._layers, key=lambda gait: self._layers[gait].weight)

    @property
    def active_gaits(self) -> list[GaitMode]:
        return [gait for gait, layer in self._layers.items() if layer.weight > 1e-6]

    @property
    def blended_stride_length(self) -> float:
        return max(sum(layer.profile.stride_length * layer.weight for layer in self._layers.values()), 1e-3)

    @property
    def blended_steps_per_second(self) -> float:
        return max(sum(layer.profile.steps_per_second * layer.weight for layer in self._layers.values()), 1e-3)

    @property
    def blended_bounce_amplitude(self) -> float:
        return sum(layer.profile.bounce_amplitude * layer.weight for layer in self._layers.values())

    def set_target_gait(self, gait: GaitMode) -> None:
        self._target_gait = gait

    def set_weights(self, weights: dict[GaitMode, float]) -> None:
        total = sum(max(float(value), 0.0) for value in weights.values())
        if total <= 1e-9:
            return
        for gait, layer in self._layers.items():
            layer.weight = max(float(weights.get(gait, 0.0)), 0.0) / total

    def set_blend_speed(self, speed: float) -> None:
        self._blend_speed = max(float(speed), 0.1)

    def update(
        self,
        dt: float,
        velocity: float = 1.0,
        target_gait: GaitMode | None = None,
    ) -> dict[str, float]:
        sample = self.sample_continuous_gait(dt=dt, velocity=velocity, target_gait=target_gait)
        pose = dict(sample.pose)
        pose["_root_y"] = sample.root_y
        pose["_phase"] = sample.phase
        pose["_leader"] = float(list(GaitMode).index(sample.leader))
        pose["_stride_length"] = sample.stride_length
        pose["_fft_phase"] = sample.fft_phase
        pose["_bounce"] = sample.root_y - self._compute_pelvis_height(sample.pose, sample.phase)
        return pose

    def sample_continuous_gait(
        self,
        *,
        dt: float,
        velocity: float,
        target_gait: GaitMode | None = None,
    ) -> UnifiedMotionSample:
        if target_gait is not None:
            self._target_gait = target_gait
        self._current_speed = max(abs(float(velocity)), 1e-3)
        self._update_weights(dt)
        self._stride_wheel.set_circumference(self.blended_stride_length)
        raw_phase = self._stride_wheel.advance(self._current_speed * max(float(dt), 0.0))
        leader_gait = self.leader
        leader_layer = self._layers[leader_gait]
        leader_layer.phase = raw_phase
        fft_phase = self._blend_fft_phase(raw_phase)
        leader_phase = self._phase_shortest_arc_mix(raw_phase, fft_phase, self._effective_phase_alignment_weight())
        leader_layer.phase = leader_phase
        for gait, layer in self._layers.items():
            if gait == leader_gait:
                continue
            if layer.weight <= 1e-6:
                layer.phase = leader_phase
                continue
            warped_phase = phase_warp(leader_phase, leader_layer.profile.markers, layer.profile.markers)
            layer.phase = self._phase_shortest_arc_mix(leader_phase, warped_phase, self.phase_weight)
        pose_vec = np.zeros(len(self._layout.joint_names), dtype=np.float64)
        pelvis_h = 0.0
        for gait, layer in self._layers.items():
            if layer.weight <= 1e-6:
                continue
            pose_dict, pelvis = self._evaluate_layer(layer)
            pose_vec += self._layout.to_array(pose_dict) * layer.weight
            pelvis_h += pelvis * layer.weight
        pose = self._layout.to_pose(pose_vec)
        bounce = adaptive_bounce(leader_phase, self._current_speed, self.blended_bounce_amplitude)
        root_y = pelvis_h + bounce
        return UnifiedMotionSample(
            pose=pose,
            root_y=float(root_y),
            phase=float(leader_phase),
            stride_length=float(self.blended_stride_length),
            leader=leader_gait,
            weights={gait.value: layer.weight for gait, layer in self._layers.items()},
            fft_phase=float(fft_phase),
        )

    def sample_pose_at_phase(
        self,
        phase: float,
        weights: Mapping[GaitMode, float],
        speed: float = 1.0,
    ) -> tuple[dict[str, float], float]:
        normalizer = sum(max(float(value), 0.0) for value in weights.values())
        if normalizer <= 1e-9:
            weights = {GaitMode.WALK: 1.0}
            normalizer = 1.0
        blended_pose = np.zeros(len(self._layout.joint_names), dtype=np.float64)
        pelvis_h = 0.0
        phase = float(phase) % 1.0
        fft_phase = self._blend_fft_phase(phase)
        phase = self._phase_shortest_arc_mix(phase, fft_phase, self._effective_phase_alignment_weight())
        leader_gait = max(weights, key=lambda gait: weights[gait])
        leader_markers = self._layers[leader_gait].profile.markers
        for gait, value in weights.items():
            weight = max(float(value), 0.0) / normalizer
            if weight <= 1e-6 or gait not in self._layers:
                continue
            layer = self._layers[gait]
            warped = phase if gait == leader_gait else phase_warp(phase, leader_markers, layer.profile.markers)
            layer.phase = self._phase_shortest_arc_mix(phase, warped, self.phase_weight)
            pose, pelvis = self._evaluate_layer(layer)
            blended_pose += self._layout.to_array(pose) * weight
            pelvis_h += pelvis * weight
        bounce = adaptive_bounce(phase, speed, self._blend_bounce_for_weights(weights, normalizer))
        return self._layout.to_pose(blended_pose), float(pelvis_h + bounce)

    def get_blend_state(self) -> dict[str, Any]:
        return {
            "leader": self.leader.value,
            "active_gaits": [g.value for g in self.active_gaits],
            "weights": {g.value: layer.weight for g, layer in self._layers.items()},
            "phases": {g.value: layer.phase for g, layer in self._layers.items()},
            "stride_length": self.blended_stride_length,
            "steps_per_second": self.blended_steps_per_second,
            "bounce_amplitude": self.blended_bounce_amplitude,
            "speed": self._current_speed,
            "fft_lock_strength": self.fft_lock_strength,
            "transition_strategy": self.transition_strategy.value,
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
        pose = self.update(dt=dt, velocity=velocity, target_gait=target_gait)
        state = self.get_blend_state()
        return {
            "pose": {key: value for key, value in pose.items() if not key.startswith("_")},
            "root_y": pose.get("_root_y", 0.0),
            "phase": pose.get("_phase", 0.0),
            "leader": state["leader"],
            "weights": state["weights"],
            "stride_length": state["stride_length"],
            "bounce": pose.get("_bounce", 0.0),
            "time": float(time),
            "frame_index": int(frame_index),
            "metadata": {
                "generator": "unified_gait_blender",
                "gap": "B3",
                "transition_strategy": self.transition_strategy.value,
                "research_refs": [
                    "Holden2017_PFNN",
                    "Starke2022_DeepPhase",
                    "Bollo2018_Inertialization",
                    "Holden2020_DeadBlending",
                    "Rosen2014_StrideWheel",
                ],
            },
        }

    # ---- transition residual trunk -----------------------------------------

    def request_transition(
        self,
        source_frame: UnifiedMotionFrame,
        target_frame: UnifiedMotionFrame,
        prev_source_frame: UnifiedMotionFrame | None = None,
        *,
        strategy: TransitionStrategy | None = None,
        dt: float = 1.0 / 24.0,
    ) -> None:
        active_strategy = strategy or self.transition_strategy
        self.transition_strategy = active_strategy
        layout = _VectorLayout.from_frames([source_frame, target_frame] + ([prev_source_frame] if prev_source_frame else []))
        self._residual_layout = layout
        source_joints = layout.to_array(source_frame.joint_local_rotations)
        target_joints = layout.to_array(target_frame.joint_local_rotations)
        target_root_velocity = np.array([
            target_frame.root_transform.velocity_x,
            target_frame.root_transform.velocity_y,
            target_frame.root_transform.angular_velocity,
        ], dtype=np.float64)
        if prev_source_frame is not None:
            prev_joints = layout.to_array(prev_source_frame.joint_local_rotations)
            joint_velocity = (source_joints - prev_joints) / max(dt, 1e-6)
        else:
            joint_velocity = np.zeros(len(layout.joint_names), dtype=np.float64)
        source_root_velocity = np.array([
            source_frame.root_transform.velocity_x,
            source_frame.root_transform.velocity_y,
            source_frame.root_transform.angular_velocity,
        ], dtype=np.float64)
        root_velocity = source_root_velocity - target_root_velocity
        joint_offset = source_joints - target_joints
        root_offset = np.array([
            source_frame.root_transform.x - target_frame.root_transform.x,
            source_frame.root_transform.y - target_frame.root_transform.y,
            self._wrap_angle(source_frame.root_transform.rotation - target_frame.root_transform.rotation),
        ], dtype=np.float64)
        self._joint_residual = _ResidualState(offset=joint_offset, velocity=joint_velocity)
        self._root_residual = _ResidualState(offset=root_offset, velocity=root_velocity)
        self._transition_elapsed = 0.0
        self._transition_active = True
        initial_velocity_gap = float(np.linalg.norm(root_velocity))
        initial_peak = float(max(np.max(np.abs(root_offset), initial=0.0), np.max(np.abs(joint_offset), initial=0.0)))
        initial_displacement = float(np.linalg.norm(root_offset[:2]) + np.linalg.norm(joint_offset))
        initial_velocity_continuity = math.exp(-initial_velocity_gap)
        self._transition_metrics = TransitionQualityMetrics(
            strategy=active_strategy.value,
            total_displacement=0.0,
            peak_offset=initial_peak,
            contact_preservation=1.0,
            velocity_continuity=initial_velocity_continuity,
            smoothness=initial_velocity_continuity,
            foot_sliding=0.0,
            frames_processed=0,
            joint_count=len(layout.joint_names),
            elapsed=0.0,
        )
        self._transition_accumulators = _TransitionMetricAccumulators(
            prev_root_velocity=source_root_velocity.copy(),
            prev_displacement=initial_displacement,
        )

    def apply_transition(
        self,
        target_frame: UnifiedMotionFrame,
        dt: float = 1.0 / 24.0,
    ) -> UnifiedMotionFrame:
        if not self._transition_active:
            return target_frame
        if self._transition_elapsed >= self.blend_time:
            self._transition_active = False
            return target_frame
        dt = max(float(dt), 0.0)
        self._transition_elapsed += dt
        progress = min(self._transition_elapsed / max(self.blend_time, 1e-8), 1.0)
        target_joints = self._residual_layout.to_array(target_frame.joint_local_rotations)
        target_root = np.array([
            target_frame.root_transform.x,
            target_frame.root_transform.y,
            target_frame.root_transform.rotation,
        ], dtype=np.float64)
        target_root_velocity = np.array([
            target_frame.root_transform.velocity_x,
            target_frame.root_transform.velocity_y,
            target_frame.root_transform.angular_velocity,
        ], dtype=np.float64)
        if self.transition_strategy == TransitionStrategy.INERTIALIZATION:
            joint_offset, joint_velocity = self._quintic_state(self._joint_residual, self._transition_elapsed, self.blend_time)
            root_offset, root_velocity = self._quintic_state(self._root_residual, self._transition_elapsed, self.blend_time)
            pose_vec = target_joints + joint_offset
            root_vec = target_root + root_offset
            root_vel = target_root_velocity + root_velocity
        else:
            alpha = self._smoothstep(progress)
            self._joint_residual.velocity = self._damper_decay_exact(self._joint_residual.velocity, self.decay_halflife, dt)
            self._joint_residual.offset = self._joint_residual.offset + self._joint_residual.velocity * dt
            self._root_residual.velocity = self._damper_decay_exact(self._root_residual.velocity, self.decay_halflife, dt)
            self._root_residual.offset = self._root_residual.offset + self._root_residual.velocity * dt
            pose_vec = self._lerp(self._joint_residual.offset + target_joints, target_joints, alpha)
            root_vec = self._lerp(self._root_residual.offset + target_root, target_root, alpha)
            root_vel = self._lerp(self._root_residual.velocity, target_root_velocity, alpha)
        remaining_peak = float(max(np.max(np.abs(root_vec - target_root), initial=0.0), np.max(np.abs(pose_vec - target_joints), initial=0.0)))
        self._transition_metrics.peak_offset = max(self._transition_metrics.peak_offset, remaining_peak)
        self._transition_metrics.elapsed = self._transition_elapsed
        self._update_transition_metrics(
            target_frame=target_frame,
            target_joints=target_joints,
            target_root=target_root,
            target_root_velocity=target_root_velocity,
            pose_vec=pose_vec,
            root_vec=root_vec,
            root_vel=root_vel,
        )
        if progress >= 1.0:
            self._transition_active = False
            return target_frame
        return UnifiedMotionFrame(
            time=target_frame.time,
            phase=target_frame.phase,
            root_transform=MotionRootTransform(
                x=float(root_vec[0]),
                y=float(root_vec[1]),
                rotation=float(root_vec[2]),
                velocity_x=float(root_vel[0]),
                velocity_y=float(root_vel[1]),
                angular_velocity=float(root_vel[2]),
            ),
            joint_local_rotations=self._residual_layout.to_pose(pose_vec),
            contact_tags=target_frame.contact_tags,
            frame_index=target_frame.frame_index,
            source_state=target_frame.source_state,
            metadata={
                **target_frame.metadata,
                "transition_active": True,
                "transition_strategy": self.transition_strategy.value,
                "transition_progress": progress,
                "motion_hub": "UnifiedGaitBlender",
            },
        )

    def update_transition(self, target_frame: UnifiedMotionFrame, dt: float = 1.0 / 24.0) -> UnifiedMotionFrame:
        return self.apply_transition(target_frame=target_frame, dt=dt)

    @property
    def is_active(self) -> bool:
        return self._transition_active

    def get_transition_quality(self) -> TransitionQualityMetrics:
        return TransitionQualityMetrics(
            strategy=self._transition_metrics.strategy,
            total_displacement=self._transition_metrics.total_displacement,
            peak_offset=self._transition_metrics.peak_offset,
            contact_preservation=self._transition_metrics.contact_preservation,
            velocity_continuity=self._transition_metrics.velocity_continuity,
            smoothness=self._transition_metrics.smoothness,
            foot_sliding=self._transition_metrics.foot_sliding,
            frames_processed=self._transition_metrics.frames_processed,
            joint_count=self._transition_metrics.joint_count,
            elapsed=self._transition_metrics.elapsed,
        )

    # ---- stateless frame helpers -------------------------------------------

    def sample_gait_umr_frame(
        self,
        gait: GaitMode,
        *,
        phase: float,
        speed: float,
        time: float,
        frame_index: int,
        root_x: float,
        metadata: Mapping[str, Any] | None = None,
    ) -> UnifiedMotionFrame:
        pose, root_y = self.sample_pose_at_phase(float(phase) % 1.0, {gait: 1.0}, speed=speed)
        gait_profile = self._layers[gait].profile
        step_frequency = gait_profile.steps_per_second / 2.0
        stride = gait_profile.stride_length
        left_contact = phase % 1.0 < 0.5
        right_contact = not left_contact
        return pose_to_umr(
            pose,
            time=time,
            phase=phase % 1.0,
            source_state=gait.value,
            root_transform=MotionRootTransform(
                x=root_x,
                y=root_y,
                velocity_x=float(speed),
                velocity_y=0.0,
                rotation=0.0,
                angular_velocity=0.0,
            ),
            contact_tags=MotionContactState(left_foot=left_contact, right_foot=right_contact),
            frame_index=frame_index,
            metadata={
                **dict(metadata or {}),
                "stride_length": stride,
                "step_frequency": step_frequency,
                "motion_hub": "UnifiedGaitBlender",
                "fft_phase": self._blend_fft_phase(float(phase) % 1.0),
            },
        )

    # ---- internal numerical methods ----------------------------------------

    def _effective_phase_alignment_weight(self) -> float:
        return float(np.clip(self.fft_lock_strength * self.phase_weight, 0.0, 1.0))

    def _update_transition_metrics(
        self,
        *,
        target_frame: UnifiedMotionFrame,
        target_joints: np.ndarray,
        target_root: np.ndarray,
        target_root_velocity: np.ndarray,
        pose_vec: np.ndarray,
        root_vec: np.ndarray,
        root_vel: np.ndarray,
    ) -> None:
        pose_gap = float(np.linalg.norm(pose_vec - target_joints))
        root_planar_gap = float(np.linalg.norm(root_vec[:2] - target_root[:2]))
        angular_gap = abs(self._wrap_angle(float(root_vec[2] - target_root[2])))
        displacement = pose_gap + root_planar_gap + 0.25 * angular_gap
        planted_contacts = int(bool(target_frame.contact_tags.left_foot)) + int(bool(target_frame.contact_tags.right_foot))
        foot_sliding = root_planar_gap * float(planted_contacts) if planted_contacts > 0 else 0.0
        contact_preservation = 1.0 if planted_contacts <= 0 else math.exp(-foot_sliding)
        velocity_error = float(np.linalg.norm(root_vel - target_root_velocity))
        velocity_continuity = math.exp(-velocity_error)
        jerk = float(np.linalg.norm(root_vel - self._transition_accumulators.prev_root_velocity))
        displacement_delta = abs(displacement - self._transition_accumulators.prev_displacement)
        smoothness = math.exp(-(0.5 * jerk + 0.25 * displacement_delta))

        self._transition_accumulators.velocity_continuity_sum += velocity_continuity
        self._transition_accumulators.contact_preservation_sum += contact_preservation
        self._transition_accumulators.smoothness_sum += smoothness
        self._transition_accumulators.foot_sliding_sum += foot_sliding
        self._transition_accumulators.prev_root_velocity = root_vel.copy()
        self._transition_accumulators.prev_displacement = displacement

        self._transition_metrics.frames_processed += 1
        frames = max(self._transition_metrics.frames_processed, 1)
        self._transition_metrics.total_displacement += displacement
        self._transition_metrics.contact_preservation = self._transition_accumulators.contact_preservation_sum / frames
        self._transition_metrics.velocity_continuity = self._transition_accumulators.velocity_continuity_sum / frames
        self._transition_metrics.smoothness = self._transition_accumulators.smoothness_sum / frames
        self._transition_metrics.foot_sliding = self._transition_accumulators.foot_sliding_sum / frames

    def _update_weights(self, dt: float) -> None:
        target = self._target_gait
        rate = max(float(dt), 0.0) * self._blend_speed
        for gait, layer in self._layers.items():
            target_weight = 1.0 if gait == target else 0.0
            diff = target_weight - layer.weight
            if abs(diff) < 1e-9:
                layer.weight = target_weight
            else:
                layer.weight = float(np.clip(layer.weight + diff * min(rate, 1.0), 0.0, 1.0))
        total = sum(layer.weight for layer in self._layers.values())
        if total <= 1e-9:
            self._layers[GaitMode.WALK].weight = 1.0
            total = 1.0
        for layer in self._layers.values():
            layer.weight /= total

    def _evaluate_layer(self, layer: GaitBlendLayer) -> tuple[dict[str, float], float]:
        pose, pelvis_h = layer.interpolator.evaluate(layer.phase)
        pose = dict(pose)
        for name, channel in layer.channels.items():
            channel_value = channel.evaluate(layer.phase)
            if name == "torso_bob":
                pose["spine"] = pose.get("spine", 0.0) + channel_value + pelvis_h * 2.0
            elif name == "torso_twist":
                pose["chest"] = pose.get("chest", 0.0) + channel_value
            elif name == "head_stabilize":
                pose["head"] = pose.get("head", 0.0) + channel_value
            elif name == "arm_pump":
                pose["l_elbow"] = pose.get("l_elbow", 0.0) + channel_value * 0.5
                pose["r_elbow"] = pose.get("r_elbow", 0.0) - channel_value * 0.5
        return pose, float(pelvis_h)

    def _build_fft_signatures(self) -> dict[GaitMode, _FFTSignature]:
        signatures: dict[GaitMode, _FFTSignature] = {}
        sample_grid = np.linspace(0.0, 1.0, 64, endpoint=False)
        sample_rate = 64.0
        for gait, layer in self._layers.items():
            left_signal = np.array([layer.interpolator.evaluate(float(phase))[0].get("l_hip", 0.0) for phase in sample_grid], dtype=np.float64)
            right_signal = np.array([layer.interpolator.evaluate(float(phase))[0].get("r_hip", 0.0) for phase in sample_grid], dtype=np.float64)
            spine_signal = np.array([layer.interpolator.evaluate(float(phase))[0].get("spine", 0.0) for phase in sample_grid], dtype=np.float64)
            channels = {
                "left_leg": extract_deepphase_channels(left_signal, sample_rate=sample_rate, max_channels=2),
                "right_leg": extract_deepphase_channels(right_signal, sample_rate=sample_rate, max_channels=2),
                "spine": extract_deepphase_channels(spine_signal, sample_rate=sample_rate, max_channels=2),
            }
            dominant = channels["left_leg"][0] if channels["left_leg"] else PhaseManifoldPoint(channel_name=f"{gait.value}_left")
            signatures[gait] = _FFTSignature(point=dominant, channels=channels)
        return signatures

    def _blend_fft_phase(self, raw_phase: float) -> float:
        points: list[PhaseManifoldPoint] = []
        weights: list[float] = []
        for gait, layer in self._layers.items():
            if layer.weight <= 1e-6:
                continue
            signature = self._fft_signatures[gait].point
            points.append(
                PhaseManifoldPoint(
                    amplitude=signature.amplitude,
                    frequency=signature.frequency,
                    phase_shift=(signature.phase_shift + raw_phase) % 1.0,
                    offset=signature.offset,
                    channel_name=signature.channel_name,
                )
            )
            weights.append(layer.weight)
        if not points:
            return raw_phase % 1.0
        blended = PhaseBlender.blend_multi(points, weights)
        return blended.phase_shift % 1.0

    def _blend_bounce_for_weights(self, weights: Mapping[GaitMode, float], normalizer: float) -> float:
        return sum(self._layers[gait].profile.bounce_amplitude * max(float(weight), 0.0) / normalizer for gait, weight in weights.items() if gait in self._layers)

    def _compute_pelvis_height(self, pose: Mapping[str, float], phase: float) -> float:
        weights = {g: layer.weight for g, layer in self._layers.items() if layer.weight > 1e-6}
        if not weights:
            return 0.0
        pelvis_h = 0.0
        leader_gait = max(weights, key=weights.get)
        leader_markers = self._layers[leader_gait].profile.markers
        total = sum(weights.values())
        for gait, weight in weights.items():
            layer = self._layers[gait]
            warped = phase if gait == leader_gait else phase_warp(phase, leader_markers, layer.profile.markers)
            _, pelvis = layer.interpolator.evaluate(warped)
            pelvis_h += pelvis * (weight / total)
        return float(pelvis_h)

    @staticmethod
    def _phase_shortest_arc_mix(source_phase: float, target_phase: float, alpha: float) -> float:
        a = float(source_phase) % 1.0
        b = float(target_phase) % 1.0
        delta = (b - a + 0.5) % 1.0 - 0.5
        return (a + delta * alpha) % 1.0

    @staticmethod
    def _wrap_angle(angle: float) -> float:
        wrapped = float(angle)
        while wrapped > math.pi:
            wrapped -= 2.0 * math.pi
        while wrapped < -math.pi:
            wrapped += 2.0 * math.pi
        return wrapped

    @staticmethod
    def _quintic_state(state: _ResidualState, t: float, duration: float) -> tuple[np.ndarray, np.ndarray]:
        t = max(float(t), 0.0)
        duration = max(float(duration), 1e-6)
        tau = np.clip(t / duration, 0.0, 1.0)
        tau2 = tau * tau
        tau3 = tau2 * tau
        tau4 = tau3 * tau
        tau5 = tau4 * tau
        x0 = state.offset
        v0 = state.velocity
        offset = x0 * (1.0 - 10.0 * tau3 + 15.0 * tau4 - 6.0 * tau5) + v0 * duration * (tau - 6.0 * tau3 + 8.0 * tau4 - 3.0 * tau5)
        velocity = (x0 * (-30.0 * tau2 + 60.0 * tau3 - 30.0 * tau4) + v0 * duration * (1.0 - 18.0 * tau2 + 32.0 * tau3 - 15.0 * tau4)) / duration
        return offset, velocity

    @staticmethod
    def _damper_decay_exact(value: np.ndarray, halflife: float, dt: float) -> np.ndarray:
        decay = math.exp(-math.log(2.0) * max(float(dt), 0.0) / max(float(halflife), 1e-8))
        return value * decay

    @staticmethod
    def _smoothstep(x: float) -> float:
        v = float(np.clip(x, 0.0, 1.0))
        return v * v * (3.0 - 2.0 * v)

    @staticmethod
    def _lerp(a: np.ndarray, b: np.ndarray, alpha: float) -> np.ndarray:
        return a + (b - a) * float(alpha)


# ---------------------------------------------------------------------------
# Compatibility façade types backed by the unified core
# ---------------------------------------------------------------------------


class GaitBlender(UnifiedGaitBlender):
    """Backward-compatible alias for the historical gait blender API."""


class InertializationChannel:
    """Compatibility channel that delegates to the unified residual solver."""

    def __init__(self, blend_time: float = 0.2):
        self._core = UnifiedGaitBlender(transition_strategy=TransitionStrategy.INERTIALIZATION, blend_time=blend_time)

    def capture(self, source_frame: UnifiedMotionFrame, prev_source_frame: UnifiedMotionFrame | None = None, dt: float = 1.0 / 24.0) -> None:
        self._source_frame = source_frame
        self._prev_source_frame = prev_source_frame
        self._dt = dt

    def apply(self, target_frame: UnifiedMotionFrame, dt: float = 1.0 / 24.0) -> UnifiedMotionFrame:
        if hasattr(self, "_source_frame"):
            self._core.request_transition(self._source_frame, target_frame, getattr(self, "_prev_source_frame", None), strategy=TransitionStrategy.INERTIALIZATION, dt=getattr(self, "_dt", dt))
            del self._source_frame
        return self._core.apply_transition(target_frame, dt)

    @property
    def is_active(self) -> bool:
        return self._core.is_active


class DeadBlendingChannel:
    """Compatibility channel that delegates to the unified residual solver."""

    def __init__(self, blend_time: float = 0.2, decay_halflife: float = 0.05):
        self._core = UnifiedGaitBlender(
            transition_strategy=TransitionStrategy.DEAD_BLENDING,
            blend_time=blend_time,
            decay_halflife=decay_halflife,
        )

    def capture(self, source_frame: UnifiedMotionFrame, prev_source_frame: UnifiedMotionFrame | None = None, dt: float = 1.0 / 24.0) -> None:
        self._source_frame = source_frame
        self._prev_source_frame = prev_source_frame
        self._dt = dt

    def apply(self, target_frame: UnifiedMotionFrame, dt: float = 1.0 / 24.0) -> UnifiedMotionFrame:
        if hasattr(self, "_source_frame"):
            self._core.request_transition(self._source_frame, target_frame, getattr(self, "_prev_source_frame", None), strategy=TransitionStrategy.DEAD_BLENDING, dt=getattr(self, "_dt", dt))
            del self._source_frame
        return self._core.apply_transition(target_frame, dt)

    @property
    def is_active(self) -> bool:
        return self._core.is_active


class TransitionSynthesizer:
    """Historical transition API mapped onto the unified motion core."""

    def __init__(
        self,
        strategy: TransitionStrategy = TransitionStrategy.DEAD_BLENDING,
        blend_time: float = 0.2,
        decay_halflife: float = 0.05,
        phase_weight: float = 1.0,
    ):
        self._core = UnifiedGaitBlender(
            transition_strategy=strategy,
            blend_time=blend_time,
            decay_halflife=decay_halflife,
            phase_weight=phase_weight,
        )
        self.strategy = strategy
        self.blend_time = blend_time
        self.decay_halflife = decay_halflife
        self.phase_weight = phase_weight
        self._source_frame: Optional[UnifiedMotionFrame] = None
        self._prev_source_frame: Optional[UnifiedMotionFrame] = None

    def request_transition(
        self,
        source_frame: UnifiedMotionFrame,
        target_frame: UnifiedMotionFrame,
        prev_source_frame: UnifiedMotionFrame | None = None,
        dt: float = 1.0 / 24.0,
    ) -> None:
        self._core.request_transition(source_frame, target_frame, prev_source_frame, strategy=self.strategy, dt=dt)

    def update(self, target_frame: UnifiedMotionFrame, dt: float = 1.0 / 24.0) -> UnifiedMotionFrame:
        return self._core.apply_transition(target_frame, dt)

    @property
    def is_active(self) -> bool:
        return self._core.is_active

    def get_transition_quality(self) -> TransitionQualityMetrics:
        return self._core.get_transition_quality()

    def get_quality_metrics(self) -> TransitionQualityMetrics:
        return self.get_transition_quality()


class TransitionPipelineNode:
    """Pipeline node that detects state changes and applies unified residual synthesis."""

    def __init__(
        self,
        strategy: TransitionStrategy = TransitionStrategy.DEAD_BLENDING,
        blend_time: float = 0.2,
        decay_halflife: float = 0.05,
    ):
        self.synth = TransitionSynthesizer(strategy=strategy, blend_time=blend_time, decay_halflife=decay_halflife)
        self._prev_frame: Optional[UnifiedMotionFrame] = None
        self._current_state: str = ""

    def __call__(self, frame: UnifiedMotionFrame, dt: float = 1.0 / 24.0) -> UnifiedMotionFrame:
        if self._prev_frame is not None and frame.source_state != self._current_state:
            self.synth.request_transition(self._prev_frame, frame, self._prev_frame, dt=dt)
        output = self.synth.update(frame, dt=dt)
        self._prev_frame = output
        self._current_state = frame.source_state
        return output


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------


def blend_walk_run(phase: float, alpha: float, speed: float = 1.0) -> tuple[dict[str, float], float]:
    alpha = float(np.clip(alpha, 0.0, 1.0))
    core = UnifiedGaitBlender()
    return core.sample_pose_at_phase(float(phase) % 1.0, {GaitMode.WALK: 1.0 - alpha, GaitMode.RUN: alpha}, speed=speed)


def blend_gaits_at_phase(phase: float, weights: dict[GaitMode, float], speed: float = 1.0) -> tuple[dict[str, float], float]:
    core = UnifiedGaitBlender()
    return core.sample_pose_at_phase(float(phase) % 1.0, weights, speed=speed)


def sample_gait_umr_frame(
    gait: GaitMode,
    *,
    phase: float,
    speed: float,
    time: float,
    frame_index: int,
    root_x: float,
    metadata: Mapping[str, Any] | None = None,
) -> UnifiedMotionFrame:
    core = UnifiedGaitBlender()
    return core.sample_gait_umr_frame(
        gait,
        phase=phase,
        speed=speed,
        time=time,
        frame_index=frame_index,
        root_x=root_x,
        metadata=metadata,
    )


def create_transition_synthesizer(
    strategy: str = "dead_blending",
    blend_time: float = 0.2,
    decay_halflife: float = 0.05,
) -> TransitionSynthesizer:
    return TransitionSynthesizer(
        strategy=TransitionStrategy(strategy),
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
    if not source_frames or not target_frames:
        return list(target_frames)
    synth = create_transition_synthesizer(strategy=strategy, blend_time=blend_time, decay_halflife=decay_halflife)
    source = source_frames[-1]
    prev_source = source_frames[-2] if len(source_frames) >= 2 else None
    synth.request_transition(source, target_frames[0], prev_source, dt=dt)
    outputs: list[UnifiedMotionFrame] = []
    for frame in target_frames:
        outputs.append(synth.update(frame, dt=dt))
    return outputs


def phase_driven_state_pose(state: str, phase: float) -> dict[str, float]:
    normalized = state.strip().lower()
    if normalized == "idle":
        return idle_animation(phase)
    if normalized == "jump":
        return jump_animation(phase)
    if normalized == "fall":
        return fall_animation(phase)
    if normalized == "hit":
        return hit_animation(phase)
    if normalized == "run":
        pose, _ = blend_gaits_at_phase(phase, {GaitMode.RUN: 1.0})
        return pose
    if normalized == "walk":
        pose, _ = blend_gaits_at_phase(phase, {GaitMode.WALK: 1.0})
        return pose
    return idle_animation(phase)


# ---------------------------------------------------------------------------
# Motion lane registry for pipeline.py
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MotionStateRequest:
    state: str
    phase: float
    time: float
    frame_index: int
    frame_count: int
    fps: int
    metadata: dict[str, Any] = field(default_factory=dict)
    root_x: float = 0.0


class MotionStateLane:
    state_name: str = ""

    def begin_clip(
        self,
        gait_runtime_config: UnifiedGaitRuntimeConfig | None = None,
    ) -> "MotionStateLane":
        return self

    def preview_pose(self, phase: float) -> dict[str, float]:
        raise NotImplementedError

    def infer_root_transform(
        self,
        *,
        progress: float,
        frame_index: int,
        frame_count: int,
        fps: int,
    ) -> MotionRootTransform:
        return MotionRootTransform(x=0.0, y=0.0, velocity_x=0.0, velocity_y=0.0)

    def build_frame(self, request: MotionStateRequest) -> UnifiedMotionFrame:
        raise NotImplementedError


class MotionStateLaneRegistry:
    def __init__(self) -> None:
        self._lanes: dict[str, MotionStateLane] = {}

    def register(self, lane: MotionStateLane) -> MotionStateLane:
        if not lane.state_name:
            raise ValueError("motion lane must declare a state_name")
        self._lanes[lane.state_name] = lane
        return lane

    def get(self, state: str) -> MotionStateLane:
        key = state.strip().lower()
        if key not in self._lanes:
            raise KeyError(f"motion lane {key!r} not registered")
        return self._lanes[key]

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._lanes.keys()))


_MOTION_LANE_REGISTRY = MotionStateLaneRegistry()


def register_motion_lane(lane: MotionStateLane) -> MotionStateLane:
    return _MOTION_LANE_REGISTRY.register(lane)


def get_motion_lane_registry() -> MotionStateLaneRegistry:
    return _MOTION_LANE_REGISTRY


class _LocomotionLane(MotionStateLane):
    def __init__(
        self,
        state_name: str,
        gait: GaitMode,
        speed: float,
        gait_runtime_config: UnifiedGaitRuntimeConfig | None = None,
    ) -> None:
        self.state_name = state_name
        self.gait = gait
        self.speed = speed
        self._gait_runtime_config = gait_runtime_config or UnifiedGaitRuntimeConfig()
        self._core = UnifiedGaitBlender(
            blend_time=self._gait_runtime_config.blend_time,
            phase_weight=self._gait_runtime_config.phase_weight,
        )

    def begin_clip(
        self,
        gait_runtime_config: UnifiedGaitRuntimeConfig | None = None,
    ) -> MotionStateLane:
        if gait_runtime_config is None:
            return self
        return _LocomotionLane(
            self.state_name,
            self.gait,
            self.speed,
            gait_runtime_config=gait_runtime_config,
        )

    def preview_pose(self, phase: float) -> dict[str, float]:
        pose, _ = self._core.sample_pose_at_phase(float(phase) % 1.0, {self.gait: 1.0}, speed=self.speed)
        return pose

    def infer_root_transform(self, *, progress: float, frame_index: int, frame_count: int, fps: int) -> MotionRootTransform:
        frame_duration = max(frame_count / max(fps, 1), 1e-6)
        velocity = self.speed
        return MotionRootTransform(
            x=float(progress) * frame_duration * velocity,
            y=0.0,
            velocity_x=velocity,
            velocity_y=0.0,
            rotation=0.0,
            angular_velocity=0.0,
        )

    def build_frame(self, request: MotionStateRequest) -> UnifiedMotionFrame:
        return self._core.sample_gait_umr_frame(
            self.gait,
            phase=request.phase,
            speed=self.speed,
            time=request.time,
            frame_index=request.frame_index,
            root_x=request.root_x,
            metadata={
                **request.metadata,
                "lane": self.state_name,
                "registry": "motion_state_lane",
                "gait_blend_time": self._gait_runtime_config.blend_time,
                "gait_phase_weight": self._gait_runtime_config.phase_weight,
                "gait_param_source": self._gait_runtime_config.parameter_source,
            },
        )


# ---------------------------------------------------------------------------
# Transient Phase Metadata Profiles (SESSION-070)
# ---------------------------------------------------------------------------
# These profiles inject the correct phase_kind, target_state, and phase_source
# metadata for transient (non-cyclic) motion states. This aligns with the
# DeepPhase / distance-matching discipline from phase_driven.py.

_TRANSIENT_PHASE_PROFILES: dict[str, dict[str, Any]] = {
    "jump": {
        "phase_kind": "distance_to_apex",
        "phase_source": "distance_matching",
        "target_state": "apex",
        "contact_expectation": "airborne",
        "desired_contact_state": "airborne",
    },
    "fall": {
        "phase_kind": "distance_to_ground",
        "phase_source": "distance_matching",
        "target_state": "ground_contact",
        "contact_expectation": "airborne",
        "desired_contact_state": "ground_contact",
    },
    "hit": {
        "phase_kind": "hit_recovery",
        "phase_source": "critical_damped_recovery",
        "target_state": "stable_balance",
        "contact_expectation": "planted_recovery",
        "recovery_velocity": 0.0,
    },
}


class _ProceduralStateLane(MotionStateLane):
    def __init__(self, state_name: str, pose_fn, vx: float = 0.0, vy: float = 0.0) -> None:
        self.state_name = state_name
        self._pose_fn = pose_fn
        self._vx = vx
        self._vy = vy

    def preview_pose(self, phase: float) -> dict[str, float]:
        return dict(self._pose_fn(float(phase) % 1.0))

    def infer_root_transform(self, *, progress: float, frame_index: int, frame_count: int, fps: int) -> MotionRootTransform:
        frame_duration = max(frame_count / max(fps, 1), 1e-6)
        return MotionRootTransform(
            x=float(progress) * frame_duration * self._vx,
            y=float(progress) * frame_duration * self._vy,
            velocity_x=self._vx,
            velocity_y=self._vy,
            rotation=0.0,
            angular_velocity=0.0,
        )

    def build_frame(self, request: MotionStateRequest) -> UnifiedMotionFrame:
        pose = self.preview_pose(request.phase)
        root = self.infer_root_transform(progress=request.phase, frame_index=request.frame_index, frame_count=request.frame_count, fps=request.fps)
        root = MotionRootTransform(
            x=request.root_x if self._vx != 0.0 else root.x,
            y=root.y,
            velocity_x=root.velocity_x,
            velocity_y=root.velocity_y,
            rotation=root.rotation,
            angular_velocity=root.angular_velocity,
        )
        # SESSION-070: Inject transient phase metadata for jump/fall/hit
        # This aligns the lane-generated frames with the DeepPhase /
        # distance-matching discipline expected by downstream consumers.
        merged_meta = {**request.metadata, "lane": self.state_name, "registry": "motion_state_lane"}
        transient_profile = _TRANSIENT_PHASE_PROFILES.get(self.state_name)
        if transient_profile is not None:
            for k, v in transient_profile.items():
                merged_meta.setdefault(k, v)

        # Determine phase_state for transient vs cyclic
        from .unified_motion import PhaseState
        phase_state = None
        if transient_profile is not None:
            phase_state = PhaseState.transient(
                value=request.phase,
                phase_kind=str(transient_profile.get("phase_kind", "transient")),
            )

        return pose_to_umr(
            pose,
            time=request.time,
            phase=request.phase,
            source_state=self.state_name,
            root_transform=root,
            contact_tags=MotionContactState(left_foot=False, right_foot=False),
            frame_index=request.frame_index,
            metadata=merged_meta,
            phase_state=phase_state,
        )


register_motion_lane(_ProceduralStateLane("idle", idle_animation, vx=0.0, vy=0.0))
register_motion_lane(_LocomotionLane("walk", GaitMode.WALK, speed=0.8))
register_motion_lane(_LocomotionLane("run", GaitMode.RUN, speed=1.8))
register_motion_lane(_ProceduralStateLane("jump", jump_animation, vx=0.6, vy=1.8))
register_motion_lane(_ProceduralStateLane("fall", fall_animation, vx=0.4, vy=-1.5))
register_motion_lane(_ProceduralStateLane("hit", hit_animation, vx=0.0, vy=0.0))


def get_registered_motion_preview_map() -> dict[str, Any]:
    registry = get_motion_lane_registry()
    return {name: registry.get(name).preview_pose for name in registry.names()}


__all__ = [
    "GaitMode",
    "SyncMarker",
    "GaitSyncProfile",
    "GaitBlendLayer",
    "GaitBlender",
    "UnifiedGaitBlender",
    "StrideWheel",
    "BIPEDAL_SYNC_MARKERS",
    "WALK_SYNC_PROFILE",
    "RUN_SYNC_PROFILE",
    "SNEAK_SYNC_PROFILE",
    "phase_warp",
    "adaptive_bounce",
    "blend_walk_run",
    "blend_gaits_at_phase",
    "sample_gait_umr_frame",
    "TransitionStrategy",
    "TransitionQualityMetrics",
    "UnifiedGaitRuntimeConfig",
    "resolve_unified_gait_runtime_config",
    "TransitionSynthesizer",
    "TransitionPipelineNode",
    "InertializationChannel",
    "DeadBlendingChannel",
    "create_transition_synthesizer",
    "inertialize_transition",
    "MotionStateLane",
    "MotionStateRequest",
    "MotionStateLaneRegistry",
    "register_motion_lane",
    "get_motion_lane_registry",
    "get_registered_motion_preview_map",
    "_marker_segment",
]
