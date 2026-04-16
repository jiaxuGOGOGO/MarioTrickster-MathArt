"""Unified Motion Representation (UMR) for animation pipeline closure.

This module introduces a strict shared contract for motion data, inspired by:

- Pixar OpenUSD `UsdSkel`: stable skeletal animation interchange schema
- SideFX Houdini KineFX: animation as point/attribute streams through filters
- Unreal Engine AnimGraph: intent -> base pose -> root motion -> localized correction

The design goal is deliberately narrow and practical for this repository:
all motion-producing and motion-correcting subsystems should be able to
communicate through the same frame structure instead of ad-hoc pose dicts.

Backward compatibility is preserved through `pose_to_umr()` and `umr_to_pose()`.
The renderer and legacy APIs can keep using raw `dict[str, float]` while the
internal trunk progressively converges on UMR.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Mapping, Optional


@dataclass(frozen=True)
class MotionRootTransform:
    """Minimal root motion payload for a single frame.

    The repository is still largely 2D and pose-centric, so the transform stays
    lightweight: planar translation, rotation, and first-order velocities.
    """

    x: float = 0.0
    y: float = 0.0
    rotation: float = 0.0
    velocity_x: float = 0.0
    velocity_y: float = 0.0
    angular_velocity: float = 0.0

    def to_dict(self) -> dict[str, float]:
        return {
            "x": float(self.x),
            "y": float(self.y),
            "rotation": float(self.rotation),
            "velocity_x": float(self.velocity_x),
            "velocity_y": float(self.velocity_y),
            "angular_velocity": float(self.angular_velocity),
        }


@dataclass(frozen=True)
class MotionContactState:
    """Canonical contact tags for a motion frame.

    At minimum, left/right foot contact must be explicit because the current
    repository repeatedly needs this information in phase logic, skating
    cleanup, motion matching, and rendering heuristics.
    """

    left_foot: bool = False
    right_foot: bool = False
    left_hand: bool = False
    right_hand: bool = False

    def to_dict(self) -> dict[str, bool]:
        return {
            "left_foot": bool(self.left_foot),
            "right_foot": bool(self.right_foot),
            "left_hand": bool(self.left_hand),
            "right_hand": bool(self.right_hand),
        }


@dataclass(frozen=True)
class UnifiedMotionFrame:
    """Strict cross-module motion frame contract.

    Required fields intentionally mirror the architecture request from the user:
    time, normalized phase, root transform, local joint rotations, and explicit
    contact tags.
    """

    time: float
    phase: float
    root_transform: MotionRootTransform
    joint_local_rotations: dict[str, float]
    contact_tags: MotionContactState = field(default_factory=MotionContactState)
    frame_index: int = 0
    source_state: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    format_version: str = "umr_motion_frame_v1"

    def __post_init__(self) -> None:
        metadata = dict(self.metadata)
        phase_kind = str(metadata.get("phase_kind", "cyclic"))
        phase_value = float(self.phase)
        if phase_kind in {"distance_to_apex", "distance_to_ground", "hit_recovery"}:
            phase_value = max(0.0, min(1.0, phase_value))
        else:
            phase_value = phase_value % 1.0
        object.__setattr__(self, "phase", phase_value)
        object.__setattr__(
            self,
            "joint_local_rotations",
            {k: float(v) for k, v in dict(self.joint_local_rotations).items()},
        )
        object.__setattr__(self, "metadata", metadata)

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format_version,
            "time": float(self.time),
            "phase": float(self.phase),
            "frame_index": int(self.frame_index),
            "source_state": self.source_state,
            "root_transform": self.root_transform.to_dict(),
            "joint_local_rotations": dict(self.joint_local_rotations),
            "contact_tags": self.contact_tags.to_dict(),
            "metadata": dict(self.metadata),
        }

    def with_pose(self, pose: Mapping[str, float], **metadata_updates: Any) -> "UnifiedMotionFrame":
        merged = dict(self.metadata)
        merged.update(metadata_updates)
        return replace(
            self,
            joint_local_rotations={k: float(v) for k, v in dict(pose).items()},
            metadata=merged,
        )

    def with_root(self, root_transform: MotionRootTransform, **metadata_updates: Any) -> "UnifiedMotionFrame":
        merged = dict(self.metadata)
        merged.update(metadata_updates)
        return replace(self, root_transform=root_transform, metadata=merged)

    def with_contacts(self, contact_tags: MotionContactState, **metadata_updates: Any) -> "UnifiedMotionFrame":
        merged = dict(self.metadata)
        merged.update(metadata_updates)
        return replace(self, contact_tags=contact_tags, metadata=merged)


@dataclass
class UnifiedMotionClip:
    """A sequence of UMR frames plus clip-level metadata."""

    clip_id: str
    state: str
    fps: int
    frames: list[UnifiedMotionFrame]
    metadata: dict[str, Any] = field(default_factory=dict)
    format_version: str = "umr_motion_clip_v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format_version,
            "clip_id": self.clip_id,
            "state": self.state,
            "fps": int(self.fps),
            "frame_count": len(self.frames),
            "metadata": dict(self.metadata),
            "frames": [frame.to_dict() for frame in self.frames],
        }

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        return path


@dataclass(frozen=True)
class MotionPipelineAuditEntry:
    """One audit record for a pipeline node processing one frame."""

    node_name: str
    stage: str
    frame_index: int
    time: float
    phase: float
    source_state: str
    metadata_snapshot: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_name": self.node_name,
            "stage": self.stage,
            "frame_index": int(self.frame_index),
            "time": float(self.time),
            "phase": float(self.phase),
            "source_state": self.source_state,
            "metadata_snapshot": dict(self.metadata_snapshot),
        }


@dataclass
class MotionPipelineResult:
    """Clip plus structured audit trail from the filter chain."""

    clip: UnifiedMotionClip
    audit_log: list[MotionPipelineAuditEntry] = field(default_factory=list)

    def audit_summary(self) -> dict[str, Any]:
        stage_counts: dict[str, int] = {}
        node_counts: dict[str, int] = {}
        for entry in self.audit_log:
            stage_counts[entry.stage] = stage_counts.get(entry.stage, 0) + 1
            node_counts[entry.node_name] = node_counts.get(entry.node_name, 0) + 1
        return {
            "entry_count": len(self.audit_log),
            "stage_counts": stage_counts,
            "node_counts": node_counts,
        }


@dataclass
class MotionPipelineNode:
    """A lightweight KineFX/AnimGraph-style frame filter node."""

    name: str
    stage: str
    processor: Callable[[UnifiedMotionFrame, float], UnifiedMotionFrame]

    def process(self, frame: UnifiedMotionFrame, dt: float) -> UnifiedMotionFrame:
        return self.processor(frame, dt)


def umr_to_pose(frame: UnifiedMotionFrame | Mapping[str, float]) -> dict[str, float]:
    """Convert a UMR frame back into the legacy pose-dict contract."""

    if isinstance(frame, UnifiedMotionFrame):
        return dict(frame.joint_local_rotations)
    return {k: float(v) for k, v in dict(frame).items()}


def pose_to_umr(
    pose: Mapping[str, float],
    *,
    time: float = 0.0,
    phase: float = 0.0,
    root_transform: Optional[MotionRootTransform] = None,
    contact_tags: Optional[MotionContactState] = None,
    frame_index: int = 0,
    source_state: str = "",
    metadata: Optional[Mapping[str, Any]] = None,
) -> UnifiedMotionFrame:
    """Wrap a legacy pose dict in the UMR contract."""

    metadata_dict = dict(metadata or {})
    phase_kind = str(metadata_dict.get("phase_kind", "cyclic"))
    normalized_phase = float(phase)
    if phase_kind in {"distance_to_apex", "distance_to_ground", "hit_recovery"}:
        normalized_phase = max(0.0, min(1.0, normalized_phase))
    else:
        normalized_phase = normalized_phase % 1.0

    return UnifiedMotionFrame(
        time=float(time),
        phase=normalized_phase,
        root_transform=root_transform or MotionRootTransform(),
        joint_local_rotations={k: float(v) for k, v in dict(pose).items()},
        contact_tags=contact_tags or MotionContactState(),
        frame_index=int(frame_index),
        source_state=source_state,
        metadata=metadata_dict,
    )


def infer_contact_tags(phase: float, state: str) -> MotionContactState:
    """Infer conservative foot contacts from state + phase.

    This is intentionally deterministic and lightweight. It avoids the current
    situation where multiple subsystems repeatedly re-guess contact timing from
    raw poses. For non-locomotion states we choose conservative defaults.
    """

    p = float(phase) % 1.0
    state_key = str(state).lower()

    if state_key == "run":
        left = (p >= 0.0 and p < 0.18) or (p >= 0.92 and p < 1.0)
        right = p >= 0.42 and p < 0.68
        return MotionContactState(left_foot=left, right_foot=right)
    if state_key in {"walk", "idle"}:
        left = (p >= 0.0 and p < 0.32) or (p >= 0.90 and p < 1.0)
        right = p >= 0.50 and p < 0.82
        if state_key == "idle":
            left = True
            right = True
        return MotionContactState(left_foot=left, right_foot=right)
    if state_key == "jump":
        return MotionContactState(left_foot=False, right_foot=False)
    if state_key == "fall":
        return MotionContactState(left_foot=False, right_foot=False)
    if state_key == "hit":
        return MotionContactState(left_foot=True, right_foot=True)
    return MotionContactState()


def run_motion_pipeline(
    clip: UnifiedMotionClip,
    nodes: list[MotionPipelineNode],
    *,
    dt: Optional[float] = None,
) -> MotionPipelineResult:
    """Run a sequence of frame filters over a clip and collect audit traces."""

    if not nodes:
        return MotionPipelineResult(clip=clip, audit_log=[])

    frame_dt = float(dt) if dt is not None else (1.0 / max(1, int(clip.fps)))
    frames = list(clip.frames)
    audit_log: list[MotionPipelineAuditEntry] = []

    for node in nodes:
        next_frames: list[UnifiedMotionFrame] = []
        for frame in frames:
            processed = node.process(frame, frame_dt)
            next_frames.append(processed)
            audit_log.append(
                MotionPipelineAuditEntry(
                    node_name=node.name,
                    stage=node.stage,
                    frame_index=processed.frame_index,
                    time=processed.time,
                    phase=processed.phase,
                    source_state=processed.source_state,
                    metadata_snapshot=dict(processed.metadata),
                )
            )
        frames = next_frames

    merged_meta = dict(clip.metadata)
    merged_meta.setdefault("motion_pipeline", {})
    merged_meta["motion_pipeline"].update(
        {
            "node_order": [node.name for node in nodes],
            "stage_order": [node.stage for node in nodes],
            "audit_entry_count": len(audit_log),
        }
    )

    out_clip = UnifiedMotionClip(
        clip_id=clip.clip_id,
        state=clip.state,
        fps=clip.fps,
        frames=frames,
        metadata=merged_meta,
        format_version=clip.format_version,
    )
    return MotionPipelineResult(clip=out_clip, audit_log=audit_log)


__all__ = [
    "MotionRootTransform",
    "MotionContactState",
    "UnifiedMotionFrame",
    "UnifiedMotionClip",
    "MotionPipelineAuditEntry",
    "MotionPipelineResult",
    "MotionPipelineNode",
    "pose_to_umr",
    "umr_to_pose",
    "infer_contact_tags",
    "run_motion_pipeline",
]
