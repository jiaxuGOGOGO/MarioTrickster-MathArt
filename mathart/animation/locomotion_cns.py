"""SESSION-053 — The Central Nervous System for locomotion transitions.

This module fuses three previously separate capabilities into one runtime-ready
locomotion stack:

1. **Phase-aligned gait sampling** from ``gait_blend.py``
2. **Inertialized transition landing** from ``transition_synthesizer.py``
3. **Dense runtime rule evaluation** through ``RuntimeDistillationBus``

The goal is explicit: do not crossfade locomotion states in pose space.
Instead, align the target gait phase first, switch to the target immediately,
and let inertialization decay the residual offset.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Iterable, Optional, Sequence

import numpy as np

from ..distill.runtime_bus import RuntimeConstraintEvaluation, RuntimeDistillationBus
from .skeleton import Skeleton
from .unified_gait_blender import (
    GaitMode,
    RUN_SYNC_PROFILE,
    SNEAK_SYNC_PROFILE,
    WALK_SYNC_PROFILE,
    TransitionStrategy,
    UnifiedGaitBlender,
    phase_warp,
)
from .unified_motion import (
    MotionRootTransform,
    UnifiedMotionClip,
    UnifiedMotionFrame,
    infer_contact_tags,
    pose_to_umr,
)


_GAIT_PROFILES: dict[GaitMode, Any] = {
    GaitMode.WALK: WALK_SYNC_PROFILE,
    GaitMode.RUN: RUN_SYNC_PROFILE,
    GaitMode.SNEAK: SNEAK_SYNC_PROFILE,
}


@dataclass(frozen=True)
class GaitTransitionRequest:
    """A single locomotion transition to synthesize and evaluate."""

    source_gait: GaitMode
    target_gait: GaitMode
    source_phase: float = 0.0
    source_speed: float = 1.0
    target_speed: float = 1.6
    duration_s: float = 0.25
    inertial_blend_time: float = 0.2
    case_id: str = ""

    def resolved_case_id(self) -> str:
        if self.case_id:
            return self.case_id
        return f"{self.source_gait.value}_to_{self.target_gait.value}"


@dataclass
class GaitTransitionMetrics:
    """Measured quality metrics for one locomotion transition."""

    case_id: str
    source_gait: str
    target_gait: str
    frame_count: int = 0
    aligned_phase_delta: float = 0.0
    mean_phase_step_error: float = 0.0
    max_phase_step_error: float = 0.0
    mean_sliding_error: float = 0.0
    max_sliding_error: float = 0.0
    contact_mismatch: float = 0.0
    foot_lock: float = 0.0
    transition_cost: float = 0.0
    runtime_score: float = 0.0
    runtime_penalty: float = 0.0
    accepted: bool = False
    runtime_mask: int = 0

    def to_feature_dict(self) -> dict[str, float]:
        return {
            "phase_jump": float(self.max_phase_step_error),
            "sliding_error": float(self.mean_sliding_error),
            "contact_mismatch": float(self.contact_mismatch),
            "foot_lock": float(self.foot_lock),
            "transition_cost": float(self.transition_cost),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "source_gait": self.source_gait,
            "target_gait": self.target_gait,
            "frame_count": self.frame_count,
            "aligned_phase_delta": self.aligned_phase_delta,
            "mean_phase_step_error": self.mean_phase_step_error,
            "max_phase_step_error": self.max_phase_step_error,
            "mean_sliding_error": self.mean_sliding_error,
            "max_sliding_error": self.max_sliding_error,
            "contact_mismatch": self.contact_mismatch,
            "foot_lock": self.foot_lock,
            "transition_cost": self.transition_cost,
            "runtime_score": self.runtime_score,
            "runtime_penalty": self.runtime_penalty,
            "accepted": self.accepted,
            "runtime_mask": self.runtime_mask,
        }


@dataclass
class GaitTransitionBatchResult:
    """Batch summary used by Layer 1 / Layer 3 evolution loops."""

    metrics: list[GaitTransitionMetrics] = field(default_factory=list)
    accepted_ratio: float = 0.0
    mean_runtime_score: float = 0.0
    mean_sliding_error: float = 0.0
    worst_phase_jump: float = 0.0
    mean_contact_mismatch: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "metrics": [m.to_dict() for m in self.metrics],
            "accepted_ratio": self.accepted_ratio,
            "mean_runtime_score": self.mean_runtime_score,
            "mean_sliding_error": self.mean_sliding_error,
            "worst_phase_jump": self.worst_phase_jump,
            "mean_contact_mismatch": self.mean_contact_mismatch,
        }


def _wrap_phase_delta(value: float) -> float:
    delta = float(value)
    while delta > 0.5:
        delta -= 1.0
    while delta < -0.5:
        delta += 1.0
    return delta


def _phase_step(profile: Any, speed: float, dt: float) -> float:
    stride = max(float(profile.stride_length), 1e-6)
    return float(speed) * float(dt) / stride


_UNIFIED_MOTION_HUB = UnifiedGaitBlender()


def _sample_pose(gait: GaitMode, phase: float, speed: float) -> tuple[dict[str, float], float]:
    return _UNIFIED_MOTION_HUB.sample_pose_at_phase(
        phase % 1.0,
        {gait: 1.0},
        speed=max(float(speed), 1e-4),
    )


def sample_gait_umr_frame(
    gait: GaitMode,
    *,
    phase: float,
    speed: float,
    time: float,
    frame_index: int,
    root_x: float,
    metadata: Optional[dict[str, Any]] = None,
) -> UnifiedMotionFrame:
    """Sample a pure gait pose through the unified motion hub."""

    return _UNIFIED_MOTION_HUB.sample_gait_umr_frame(
        gait,
        phase=phase,
        speed=speed,
        time=time,
        frame_index=frame_index,
        root_x=root_x,
        metadata=dict(metadata or {}),
    )


def build_phase_aligned_transition_clip(
    request: GaitTransitionRequest,
    *,
    fps: int = 24,
) -> UnifiedMotionClip:
    """Create a phase-aligned, inertialized locomotion transition clip.

    The source gait is sampled only at transition start to capture residuals.
    Every subsequent frame evaluates the **target gait only**, which is then
    corrected by inertialization residuals. This mirrors the intended runtime
    architecture for P1-B3-1.
    """

    dt = 1.0 / max(int(fps), 1)
    frame_count = max(3, int(round(max(request.duration_s, dt) * max(int(fps), 1))))
    source_profile = _GAIT_PROFILES[request.source_gait]
    target_profile = _GAIT_PROFILES[request.target_gait]

    source_phase = float(request.source_phase) % 1.0
    aligned_target_phase = phase_warp(
        source_phase,
        source_profile.markers,
        target_profile.markers,
    )

    source_step = _phase_step(source_profile, request.source_speed, dt)
    prev_source_phase = (source_phase - source_step) % 1.0

    source_prev = sample_gait_umr_frame(
        request.source_gait,
        phase=prev_source_phase,
        speed=request.source_speed,
        time=-dt,
        frame_index=-1,
        root_x=-float(request.source_speed) * dt,
        metadata={"generator": "locomotion_cns", "sampling": "source_prev"},
    )
    source_start = sample_gait_umr_frame(
        request.source_gait,
        phase=source_phase,
        speed=request.source_speed,
        time=0.0,
        frame_index=0,
        root_x=0.0,
        metadata={"generator": "locomotion_cns", "sampling": "source_start"},
    )

    motion_hub = UnifiedGaitBlender(
        transition_strategy=TransitionStrategy.INERTIALIZATION,
        blend_time=float(request.inertial_blend_time),
    )
    target_zero = sample_gait_umr_frame(
        request.target_gait,
        phase=aligned_target_phase,
        speed=request.target_speed,
        time=0.0,
        frame_index=0,
        root_x=0.0,
        metadata={"generator": "locomotion_cns", "sampling": "target_zero", "motion_hub": "UnifiedGaitBlender"},
    )
    motion_hub.request_transition(
        source_start,
        target_zero,
        prev_source_frame=source_prev,
        strategy=TransitionStrategy.INERTIALIZATION,
        dt=dt,
    )

    frames: list[UnifiedMotionFrame] = []
    root_x = 0.0
    for i in range(frame_count):
        elapsed = i * dt
        if i > 0:
            root_x += float(request.target_speed) * dt
        target_phase = (aligned_target_phase + _phase_step(target_profile, request.target_speed, elapsed)) % 1.0
        target_frame = sample_gait_umr_frame(
            request.target_gait,
            phase=target_phase,
            speed=request.target_speed,
            time=elapsed,
            frame_index=i,
            root_x=root_x,
            metadata={
                "generator": "locomotion_cns",
                "mode": "phase_aligned_inertialization",
                "source_gait": request.source_gait.value,
                "target_gait": request.target_gait.value,
                "aligned_target_phase": aligned_target_phase,
                "source_phase": source_phase,
                "case_id": request.resolved_case_id(),
                "motion_hub": "UnifiedGaitBlender",
            },
        )
        output = motion_hub.apply_transition(target_frame, dt=0.0 if i == 0 else dt)
        residual = 0.0
        keys = set(target_frame.joint_local_rotations) | set(output.joint_local_rotations)
        if keys:
            residual = float(np.mean([
                abs(output.joint_local_rotations.get(k, 0.0) - target_frame.joint_local_rotations.get(k, 0.0))
                for k in keys
            ]))
        quality = motion_hub.get_transition_quality()
        meta = dict(output.metadata)
        meta.update(
            {
                "aligned_target_phase": aligned_target_phase,
                "source_phase": source_phase,
                "phase_alignment_delta": _wrap_phase_delta(aligned_target_phase - source_phase),
                "transition_residual_l1": residual,
                "transition_peak_offset": quality.peak_offset,
                "generator_mode": "gait_cns",
            }
        )
        frames.append(
            UnifiedMotionFrame(
                time=output.time,
                phase=output.phase,
                root_transform=output.root_transform,
                joint_local_rotations=dict(output.joint_local_rotations),
                contact_tags=output.contact_tags,
                frame_index=output.frame_index,
                source_state=output.source_state,
                metadata=meta,
                phase_state=output.phase_state,
            )
        )

    return UnifiedMotionClip(
        clip_id=request.resolved_case_id(),
        state=request.target_gait.value,
        fps=int(fps),
        frames=frames,
        metadata={
            "generator": "locomotion_cns",
            "generator_mode": "gait_cns",
            "transition_type": "phase_aligned_inertialization",
            "case_id": request.resolved_case_id(),
            "source_gait": request.source_gait.value,
            "target_gait": request.target_gait.value,
            "fps": int(fps),
            "frame_count": frame_count,
            "aligned_target_phase": aligned_target_phase,
            "source_phase": source_phase,
        },
    )


def _world_foot_position(frame: UnifiedMotionFrame, joint_name: str, skeleton: Skeleton) -> tuple[float, float]:
    pose = dict(frame.joint_local_rotations)
    skeleton.apply_pose(pose)
    positions = skeleton.get_joint_positions()
    x, y = positions.get(joint_name, positions.get("root", (0.0, 0.0)))
    return (
        float(x) + float(frame.root_transform.x),
        float(y) + float(frame.root_transform.y),
    )


def compute_clip_sliding_metrics(clip: UnifiedMotionClip, *, skeleton: Optional[Skeleton] = None) -> tuple[float, float]:
    """Measure foot sliding on contact frames using FK world positions."""

    if len(clip.frames) < 2:
        return 0.0, 0.0
    skel = skeleton or Skeleton.create_humanoid(head_units=3.0)
    samples: list[float] = []
    prev_frame = clip.frames[0]
    prev_left = _world_foot_position(prev_frame, "l_foot", skel)
    prev_right = _world_foot_position(prev_frame, "r_foot", skel)
    for frame in clip.frames[1:]:
        left = _world_foot_position(frame, "l_foot", skel)
        right = _world_foot_position(frame, "r_foot", skel)
        if prev_frame.contact_tags.left_foot and frame.contact_tags.left_foot:
            samples.append(math.dist(prev_left, left))
        if prev_frame.contact_tags.right_foot and frame.contact_tags.right_foot:
            samples.append(math.dist(prev_right, right))
        prev_frame = frame
        prev_left = left
        prev_right = right
    if not samples:
        return 0.0, 0.0
    return float(np.mean(samples)), float(np.max(samples))


def _contact_mismatch(a: UnifiedMotionFrame, b: UnifiedMotionFrame) -> float:
    left = 0.0 if bool(a.contact_tags.left_foot) == bool(b.contact_tags.left_foot) else 0.5
    right = 0.0 if bool(a.contact_tags.right_foot) == bool(b.contact_tags.right_foot) else 0.5
    return left + right


def evaluate_transition_case(
    request: GaitTransitionRequest,
    *,
    fps: int = 24,
    runtime_bus: Optional[RuntimeDistillationBus] = None,
) -> tuple[UnifiedMotionClip, GaitTransitionMetrics, RuntimeConstraintEvaluation]:
    """Build one transition clip and evaluate it with runtime rules."""

    clip = build_phase_aligned_transition_clip(request, fps=fps)
    dt = 1.0 / max(int(fps), 1)
    target_profile = _GAIT_PROFILES[request.target_gait]
    source_profile = _GAIT_PROFILES[request.source_gait]
    aligned_delta = _wrap_phase_delta(float(clip.metadata.get("aligned_target_phase", 0.0)) - float(clip.metadata.get("source_phase", 0.0)))

    expected_step = _phase_step(target_profile, request.target_speed, dt)
    phase_errors: list[float] = []
    residuals: list[float] = []
    for prev, cur in zip(clip.frames[:-1], clip.frames[1:]):
        actual_step = (cur.phase - prev.phase) % 1.0
        phase_errors.append(abs(_wrap_phase_delta(actual_step - expected_step)))
        residuals.append(float(cur.metadata.get("transition_residual_l1", 0.0)))

    mean_slide, max_slide = compute_clip_sliding_metrics(clip)
    source_start = sample_gait_umr_frame(
        request.source_gait,
        phase=request.source_phase,
        speed=request.source_speed,
        time=0.0,
        frame_index=0,
        root_x=0.0,
        metadata={"generator": "locomotion_cns", "sampling": "metric_source"},
    )
    target_aligned = sample_gait_umr_frame(
        request.target_gait,
        phase=float(clip.metadata.get("aligned_target_phase", 0.0)),
        speed=request.target_speed,
        time=0.0,
        frame_index=0,
        root_x=0.0,
        metadata={"generator": "locomotion_cns", "sampling": "metric_target"},
    )
    transition_cost = 0.0
    joints = set(source_start.joint_local_rotations) | set(target_aligned.joint_local_rotations)
    if joints:
        transition_cost = float(np.mean([
            abs(source_start.joint_local_rotations.get(k, 0.0) - target_aligned.joint_local_rotations.get(k, 0.0))
            for k in joints
        ]))

    foot_lock = max(0.0, 1.0 - (mean_slide / 0.08))
    metrics = GaitTransitionMetrics(
        case_id=request.resolved_case_id(),
        source_gait=request.source_gait.value,
        target_gait=request.target_gait.value,
        frame_count=len(clip.frames),
        aligned_phase_delta=float(abs(aligned_delta)),
        mean_phase_step_error=float(np.mean(phase_errors) if phase_errors else 0.0),
        max_phase_step_error=float(np.max(phase_errors) if phase_errors else 0.0),
        mean_sliding_error=mean_slide,
        max_sliding_error=max_slide,
        contact_mismatch=_contact_mismatch(source_start, target_aligned),
        foot_lock=foot_lock,
        transition_cost=max(transition_cost, abs(aligned_delta) * source_profile.stride_length),
    )

    bus = runtime_bus or RuntimeDistillationBus()
    program = bus.build_gait_transition_program()
    evaluation = program.evaluate(metrics.to_feature_dict())
    metrics.runtime_score = float(evaluation.score)
    metrics.runtime_penalty = float(evaluation.penalty)
    metrics.runtime_mask = int(evaluation.satisfied_mask)
    metrics.accepted = bool(evaluation.accepted)
    return clip, metrics, evaluation


def evaluate_transition_batch(
    requests: Sequence[GaitTransitionRequest],
    *,
    fps: int = 24,
    runtime_bus: Optional[RuntimeDistillationBus] = None,
) -> GaitTransitionBatchResult:
    """Batch-evaluate multiple hard transitions through the same compiled rules."""

    if not requests:
        return GaitTransitionBatchResult()
    bus = runtime_bus or RuntimeDistillationBus()
    program = bus.build_gait_transition_program()
    metrics_list: list[GaitTransitionMetrics] = []
    feature_rows: list[dict[str, float]] = []

    for request in requests:
        clip, metrics, _ = evaluate_transition_case(request, fps=fps, runtime_bus=bus)
        feature_rows.append(metrics.to_feature_dict())
        metrics_list.append(metrics)

    batch = program.evaluate_feature_rows(feature_rows)
    for metrics, row in zip(metrics_list, batch["rows"]):
        metrics.runtime_score = float(row["score"])
        metrics.runtime_penalty = float(row["penalty"])
        metrics.runtime_mask = int(row["mask"])
        metrics.accepted = bool(row["accepted"])

    return GaitTransitionBatchResult(
        metrics=metrics_list,
        accepted_ratio=float(np.mean([1.0 if m.accepted else 0.0 for m in metrics_list])),
        mean_runtime_score=float(np.mean([m.runtime_score for m in metrics_list])),
        mean_sliding_error=float(np.mean([m.mean_sliding_error for m in metrics_list])),
        worst_phase_jump=float(np.max([m.max_phase_step_error for m in metrics_list])),
        mean_contact_mismatch=float(np.mean([m.contact_mismatch for m in metrics_list])),
    )


def default_cns_transition_requests() -> list[GaitTransitionRequest]:
    """Repository-standard batch for Layer 1 audits."""

    return [
        GaitTransitionRequest(GaitMode.WALK, GaitMode.RUN, source_phase=0.00, source_speed=0.80, target_speed=1.80, case_id="walk_to_run_contact_left"),
        GaitTransitionRequest(GaitMode.RUN, GaitMode.WALK, source_phase=0.50, source_speed=1.90, target_speed=0.90, case_id="run_to_walk_contact_right"),
        GaitTransitionRequest(GaitMode.WALK, GaitMode.SNEAK, source_phase=0.00, source_speed=0.80, target_speed=0.45, case_id="walk_to_sneak"),
        GaitTransitionRequest(GaitMode.SNEAK, GaitMode.RUN, source_phase=0.50, source_speed=0.45, target_speed=1.80, case_id="sneak_to_run"),
        GaitTransitionRequest(GaitMode.RUN, GaitMode.RUN, source_phase=0.18, source_speed=1.60, target_speed=2.30, case_id="run_accelerate"),
    ]


__all__ = [
    "GaitTransitionRequest",
    "GaitTransitionMetrics",
    "GaitTransitionBatchResult",
    "sample_gait_umr_frame",
    "build_phase_aligned_transition_clip",
    "compute_clip_sliding_metrics",
    "evaluate_transition_case",
    "evaluate_transition_batch",
    "default_cns_transition_requests",
]
