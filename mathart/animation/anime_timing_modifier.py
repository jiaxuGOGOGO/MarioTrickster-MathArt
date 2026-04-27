"""Anime timing export modifier for V6 knowledge-driven pacing.

This modifier translates distilled Japanese animation timing knowledge into
export-layer timeline resampling. It consumes solved physical frames and emits
held/duplicated frames for hit-stop and animating on 2s/3s without touching
XPBD, IK, or pose solvers.
"""
from __future__ import annotations

import math
from dataclasses import replace
from typing import Any, Mapping, Sequence

from mathart.core.knowledge_interpreter import TimingParams, interpret_knowledge


_EPS = 1e-8


def _vec_len(v: tuple[float, float]) -> float:
    return math.sqrt(v[0] * v[0] + v[1] * v[1])


def _sub(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
    return (a[0] - b[0], a[1] - b[1])


def _scale(v: tuple[float, float], s: float) -> tuple[float, float]:
    return (v[0] * s, v[1] * s)


def _position_from_umr_frame(frame: Any, target: str = "root") -> tuple[float, float]:
    if target == "root":
        root = getattr(frame, "root_transform", None)
        return (float(getattr(root, "x", 0.0)), float(getattr(root, "y", 0.0)))
    joints = getattr(frame, "metadata", {}).get("joint_positions") if isinstance(getattr(frame, "metadata", {}), Mapping) else None
    if isinstance(joints, Mapping) and target in joints:
        raw = joints[target]
        if isinstance(raw, Sequence) and len(raw) >= 2:
            return (float(raw[0]), float(raw[1]))
    root = getattr(frame, "root_transform", None)
    return (float(getattr(root, "x", 0.0)), float(getattr(root, "y", 0.0)))


def _position_from_pose2d(frame: Any, target: str = "root") -> tuple[float, float]:
    if target == "root" or not getattr(frame, "bone_transforms", None):
        return (float(getattr(frame, "root_x", 0.0)), float(getattr(frame, "root_y", 0.0)))
    xform = frame.bone_transforms.get(target)
    if isinstance(xform, Mapping):
        return (float(xform.get("tx", 0.0)), float(xform.get("ty", 0.0)))
    return (float(getattr(frame, "root_x", 0.0)), float(getattr(frame, "root_y", 0.0)))


def _kinematics(positions: Sequence[tuple[float, float]], fps: float) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    if not positions:
        return [], []
    dt = 1.0 / max(float(fps), _EPS)
    velocities: list[tuple[float, float]] = []
    for idx, pos in enumerate(positions):
        if len(positions) == 1:
            velocities.append((0.0, 0.0))
        elif idx == 0:
            velocities.append(_scale(_sub(positions[1], pos), 1.0 / dt))
        elif idx == len(positions) - 1:
            velocities.append(_scale(_sub(pos, positions[idx - 1]), 1.0 / dt))
        else:
            velocities.append(_scale(_sub(positions[idx + 1], positions[idx - 1]), 0.5 / dt))

    accelerations: list[tuple[float, float]] = []
    for idx, vel in enumerate(velocities):
        if len(velocities) == 1:
            accelerations.append((0.0, 0.0))
        elif idx == 0:
            accelerations.append(_scale(_sub(velocities[1], vel), 1.0 / dt))
        elif idx == len(velocities) - 1:
            accelerations.append(_scale(_sub(vel, velocities[idx - 1]), 1.0 / dt))
        else:
            accelerations.append(_scale(_sub(velocities[idx + 1], velocities[idx - 1]), 0.5 / dt))
    return velocities, accelerations


def _detect_hit_stop_indices(
    accelerations: Sequence[tuple[float, float]],
    *,
    timing: TimingParams,
) -> set[int]:
    hits: set[int] = set()
    if timing.hit_stop_frames <= 0:
        return hits
    mags = [_vec_len(a) for a in accelerations]
    for idx in range(1, len(mags)):
        drop = mags[idx - 1] - mags[idx]
        if mags[idx - 1] >= timing.hit_stop_min_previous_acceleration and drop >= timing.hit_stop_acceleration_drop:
            hits.add(idx)
    return hits


def _clone_frame(frame: Any, *, frame_index: int, time_value: float, metadata_update: Mapping[str, Any]) -> Any:
    metadata = dict(getattr(frame, "metadata", {}) or {})
    metadata.update(metadata_update)
    updates: dict[str, Any] = {"metadata": metadata}
    if hasattr(frame, "frame_index"):
        updates["frame_index"] = int(frame_index)
    if hasattr(frame, "time"):
        updates["time"] = float(time_value)
    return replace(frame, **updates)


def _resample_frames(
    frames: Sequence[Any],
    *,
    fps: float,
    timing: TimingParams,
    position_getter: Any,
    target: str,
) -> list[Any]:
    if not timing.enabled or not frames:
        return list(frames)

    positions = [position_getter(frame, target) for frame in frames]
    velocities, accelerations = _kinematics(positions, fps=fps)
    hit_indices = _detect_hit_stop_indices(accelerations, timing=timing)
    step_rate = max(1, int(timing.step_rate))
    dt = 1.0 / max(float(fps), _EPS)

    output: list[Any] = []
    held_frame: Any | None = None
    for src_idx, frame in enumerate(frames):
        speed = _vec_len(velocities[src_idx]) if src_idx < len(velocities) else 0.0
        smooth_hold = step_rate > 1 and speed <= timing.smooth_motion_velocity_threshold
        if smooth_hold and held_frame is not None and src_idx % step_rate != 0:
            chosen = held_frame
            mode = f"on_{step_rate}s_hold"
        else:
            chosen = frame
            held_frame = frame
            mode = "source_update"

        out_idx = len(output)
        output.append(_clone_frame(
            chosen,
            frame_index=out_idx,
            time_value=out_idx * dt,
            metadata_update={
                "anime_timing": {
                    "mode": mode,
                    "source_frame_index": int(src_idx),
                    "step_rate": int(step_rate),
                    "hit_stop": False,
                }
            },
        ))

        if src_idx in hit_indices:
            for hold_idx in range(timing.hit_stop_frames):
                out_idx = len(output)
                output.append(_clone_frame(
                    frame,
                    frame_index=out_idx,
                    time_value=out_idx * dt,
                    metadata_update={
                        "anime_timing": {
                            "mode": "hit_stop_hold",
                            "source_frame_index": int(src_idx),
                            "hold_index": int(hold_idx),
                            "hit_stop": True,
                            "hit_stop_frames": int(timing.hit_stop_frames),
                        }
                    },
                ))
    return output


def timing_params_from_knowledge(knowledge_path: str | None = None) -> TimingParams:
    """Read TimingParams through the V6 knowledge interpreter."""

    return interpret_knowledge(knowledge_path).timing


def apply_to_unified_motion_clip(
    clip: Any,
    timing: TimingParams | None = None,
    *,
    target: str = "root",
    knowledge_path: str | None = None,
) -> Any:
    """Return a UMR clip resampled with anime timing holds."""

    params = timing or timing_params_from_knowledge(knowledge_path)
    frames = _resample_frames(
        clip.frames,
        fps=float(clip.fps),
        timing=params,
        position_getter=_position_from_umr_frame,
        target=target,
    )
    metadata = dict(getattr(clip, "metadata", {}) or {})
    metadata["anime_timing_modifier"] = {
        "enabled": bool(params.enabled),
        "target": target,
        "hit_stop_frames": int(params.hit_stop_frames),
        "step_rate": int(params.step_rate),
        "source_frame_count": len(clip.frames),
        "output_frame_count": len(frames),
    }
    return replace(clip, frames=frames, metadata=metadata)


def apply_to_clip2d(
    clip: Any,
    timing: TimingParams | None = None,
    *,
    target: str = "root",
    knowledge_path: str | None = None,
) -> Any:
    """Return a Clip2D resampled with hit-stop and animating-on-2s/3s."""

    params = timing or timing_params_from_knowledge(knowledge_path)
    frames = _resample_frames(
        clip.frames,
        fps=float(clip.fps),
        timing=params,
        position_getter=_position_from_pose2d,
        target=target,
    )
    clip_copy = replace(clip, frames=frames)
    metadata = dict(getattr(clip, "metadata", {}) or {})
    metadata["anime_timing_modifier"] = {
        "enabled": bool(params.enabled),
        "target": target,
        "hit_stop_frames": int(params.hit_stop_frames),
        "step_rate": int(params.step_rate),
        "source_frame_count": len(clip.frames),
        "output_frame_count": len(frames),
    }
    clip_copy.metadata = metadata
    return clip_copy


__all__ = [
    "timing_params_from_knowledge",
    "apply_to_unified_motion_clip",
    "apply_to_clip2d",
]
