"""SESSION-194: OpenPose Pose Sequence Provider — IoC Payload Hook.

This module is the **Provider** half of the SESSION-194 IoC contract that
finally connects the previously orphan ``openpose_skeleton_renderer``
module to the live anti-flicker render pipeline. Following Spring's IoC
pattern, this module exposes a single high-level entry point —
:func:`bake_openpose_pose_sequence` — which:

1. Maps the project's internal humanoid skeleton joints onto the COCO-18
   keypoint layout consumed by the OpenPose ControlNet.
2. Optionally derives a per-frame walk/jump cycle (a deterministic
   Catmull-Rom-spline-driven gait) so the on-disk sequence carries a
   *real* industrial-quality motion signal, never a static T-pose.
3. Uses the existing :func:`render_openpose_sequence` to produce
   COCO-18 PNG frames.
4. **Physically writes** the PNG sequence to a stable on-disk directory
   under the chunk's runtime tree.
5. Returns a *strongly-typed contract* dict declaring its
   ``artifact_family="openpose_pose_sequence"`` and ``backend_type``,
   so the trunk pipeline picks it up via duck-typed iteration without
   any ``if enable_openpose:`` branching.

UX zero-degradation: the helper emits the official
``[⚙️ 工业烘焙网关]`` Catmull-Rom banner before producing pose frames,
honouring the SESSION-192 UX contract and the SESSION-194 ¶1
``强制附加条款`` requirement.
"""
from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from mathart.core.openpose_skeleton_renderer import (
    COCO_18_KEYPOINT_NAMES,
    render_openpose_sequence,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
#  Contract types — SESSION-194 strongly-typed payload
# ═══════════════════════════════════════════════════════════════════════════
ARTIFACT_FAMILY: str = "openpose_pose_sequence"
BACKEND_TYPE: str = "openpose_skeleton_render"


@dataclass(frozen=True)
class OpenPoseSequenceArtifact:
    """Strongly-typed contract for a baked OpenPose pose sequence.

    Mirrors UE5's ``FCompactPose`` / pose-buffer pattern: the renderer
    side never needs to know which provider produced this artifact, only
    that it carries an absolute on-disk path to a directory of PNG
    skeleton frames.
    """

    artifact_family: str
    backend_type: str
    sequence_directory: str
    frame_count: int
    width: int
    height: int
    keypoint_layout: str = "coco_18"

    def to_payload_dict(self) -> dict[str, Any]:
        return {
            "artifact_family": self.artifact_family,
            "backend_type": self.backend_type,
            "sequence_directory": self.sequence_directory,
            "frame_count": self.frame_count,
            "width": self.width,
            "height": self.height,
            "keypoint_layout": self.keypoint_layout,
        }


# ═══════════════════════════════════════════════════════════════════════════
#  Project skeleton ↔ COCO-18 mapping
# ═══════════════════════════════════════════════════════════════════════════
# Project joint names (see Skeleton.create_humanoid) → COCO-18 indices.
# Joints not present in the project skeleton (eyes / ears) get mapped to
# the nose with a small horizontal offset for visual fidelity.
_PROJECT_TO_COCO18: dict[str, int] = {
    "head": 0,         # nose
    "neck": 1,         # neck
    "r_shoulder": 2,
    "r_elbow": 3,
    "r_hand": 4,       # r_wrist
    "l_shoulder": 5,
    "l_elbow": 6,
    "l_hand": 7,       # l_wrist
    "r_hip": 8,
    "r_knee": 9,
    "r_foot": 10,      # r_ankle
    "l_hip": 11,
    "l_knee": 12,
    "l_foot": 13,      # l_ankle
}
# Pseudo positions for missing eyes/ears (offsets relative to nose, in
# normalised-pose-space units).
_NOSE_OFFSETS_FOR_FACE: dict[int, tuple[float, float]] = {
    14: (-0.020, -0.020),  # r_eye
    15: (+0.020, -0.020),  # l_eye
    16: (-0.040, -0.010),  # r_ear
    17: (+0.040, -0.010),  # l_ear
}


def _project_joints_to_coco18_array(
    joint_positions: dict[str, tuple[float, float]],
) -> np.ndarray:
    """Convert a ``{joint_name: (x, y)}`` dict to a ``(18, 3)`` array.

    Coordinates are normalised so feet ≈ 0.95, head ≈ 0.10 (image space:
    Y grows downward). The OpenPose renderer expects ``(x, y, conf)``.
    """
    arr = np.zeros((18, 3), dtype=np.float64)
    nose_xy: tuple[float, float] | None = None
    for joint_name, coco_idx in _PROJECT_TO_COCO18.items():
        if joint_name not in joint_positions:
            continue
        x, y = joint_positions[joint_name]
        # Project: feet at y≈0, head at y≈1; image: y grows downward.
        # Map [0, 1] world-y → normalised image-y in [0.10, 0.95] inverted.
        img_x = 0.5 + float(x)  # centre at 0.5
        img_y = 0.95 - float(y) * 0.85
        arr[coco_idx, 0] = img_x
        arr[coco_idx, 1] = img_y
        arr[coco_idx, 2] = 1.0
        if joint_name == "head":
            nose_xy = (img_x, img_y)
    if nose_xy is not None:
        nx, ny = nose_xy
        for coco_idx, (dx, dy) in _NOSE_OFFSETS_FOR_FACE.items():
            arr[coco_idx, 0] = nx + dx
            arr[coco_idx, 1] = ny + dy
            arr[coco_idx, 2] = 0.6
    return arr


# ═══════════════════════════════════════════════════════════════════════════
#  Catmull-Rom spline gait — deterministic, CPU-only
# ═══════════════════════════════════════════════════════════════════════════
def _catmull_rom_segment(p0: float, p1: float, p2: float, p3: float, t: float) -> float:
    """Standard Catmull-Rom interpolation (alpha = 0.5, centripetal-flavoured)."""
    t2 = t * t
    t3 = t2 * t
    return 0.5 * (
        (2.0 * p1)
        + (-p0 + p2) * t
        + (2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3) * t2
        + (-p0 + 3.0 * p1 - 3.0 * p2 + p3) * t3
    )


def _catmull_rom_along(values: list[float], samples: int) -> list[float]:
    """Densify a small list of control points with Catmull-Rom splines."""
    if len(values) < 2:
        return [values[0] if values else 0.0] * samples
    pts = [values[0], *values, values[-1]]
    out: list[float] = []
    for i in range(samples):
        u = i / max(1, samples - 1) * (len(values) - 1)
        seg = int(u)
        if seg >= len(values) - 1:
            seg = len(values) - 2
        t = u - seg
        out.append(_catmull_rom_segment(pts[seg], pts[seg + 1], pts[seg + 2], pts[seg + 3], t))
    return out


def derive_industrial_walk_cycle(frame_count: int) -> list[dict[str, tuple[float, float]]]:
    """Deterministic industrial walk-cycle pose sequence in project-skeleton space.

    Uses Catmull-Rom interpolation between hand-authored keyframes (heel
    strike, mid-stance, toe-off, mid-swing) so the resulting motion is
    professional rather than the dreaded "扭动果冻" T-pose-jiggle.
    """
    # 4 keyframes per limb cycle; values are y-offsets and x-offsets
    # relative to the rest pose. Authored from typical biped gait curves.
    l_hand_y_keys = [1.30, 1.20, 1.30, 1.40]  # swing
    r_hand_y_keys = [1.40, 1.30, 1.20, 1.30]
    l_foot_y_keys = [0.00, 0.05, 0.00, 0.10]  # heel-strike → mid → toe-off → swing
    r_foot_y_keys = [0.05, 0.00, 0.10, 0.00]
    l_knee_y_keys = [0.50, 0.45, 0.55, 0.60]
    r_knee_y_keys = [0.55, 0.60, 0.50, 0.45]
    head_y_keys   = [2.80, 2.85, 2.80, 2.78]
    spine_x_keys  = [0.00, 0.02, 0.00, -0.02]

    hu = 1.0 / 3.0
    l_hand_y = _catmull_rom_along(l_hand_y_keys, frame_count)
    r_hand_y = _catmull_rom_along(r_hand_y_keys, frame_count)
    l_foot_y = _catmull_rom_along(l_foot_y_keys, frame_count)
    r_foot_y = _catmull_rom_along(r_foot_y_keys, frame_count)
    l_knee_y = _catmull_rom_along(l_knee_y_keys, frame_count)
    r_knee_y = _catmull_rom_along(r_knee_y_keys, frame_count)
    head_y   = _catmull_rom_along(head_y_keys,   frame_count)
    spine_x  = _catmull_rom_along(spine_x_keys,  frame_count)

    frames: list[dict[str, tuple[float, float]]] = []
    for i in range(frame_count):
        sx = spine_x[i]
        frames.append({
            "head":       (sx + 0.0, head_y[i] * hu),
            "neck":       (sx + 0.0, hu * 2.5),
            "chest":      (sx + 0.0, hu * 2.0),
            "spine":      (sx + 0.0, hu * 1.5),
            "hip":        (sx + 0.0, hu * 1.0),
            "l_shoulder": (sx - 0.6 * hu, hu * 2.3),
            "r_shoulder": (sx + 0.6 * hu, hu * 2.3),
            "l_elbow":    (sx - 1.0 * hu, hu * 1.8),
            "r_elbow":    (sx + 1.0 * hu, hu * 1.8),
            "l_hand":     (sx - 1.2 * hu, l_hand_y[i] * hu),
            "r_hand":     (sx + 1.2 * hu, r_hand_y[i] * hu),
            "l_hip":      (sx - 0.3 * hu, hu * 1.0),
            "r_hip":      (sx + 0.3 * hu, hu * 1.0),
            "l_knee":     (sx - 0.3 * hu, l_knee_y[i] * hu),
            "r_knee":     (sx + 0.3 * hu, r_knee_y[i] * hu),
            "l_foot":     (sx - 0.3 * hu, l_foot_y[i] * hu),
            "r_foot":     (sx + 0.3 * hu, r_foot_y[i] * hu),
        })
    return frames


# ═══════════════════════════════════════════════════════════════════════════
#  Public bake API
# ═══════════════════════════════════════════════════════════════════════════
def bake_openpose_pose_sequence(
    *,
    output_dir: str | Path,
    frame_count: int,
    width: int = 512,
    height: int = 512,
    pose_frames: list[dict[str, tuple[float, float]]] | None = None,
    sequence_name: str = "openpose_pose",
    emit_banner: bool = True,
) -> OpenPoseSequenceArtifact:
    """Bake a real OpenPose COCO-18 pose sequence to disk.

    This is the SESSION-194 hook the trunk pipeline calls instead of
    leaving the on-disk pose directory empty (the previous "假集成"
    failure mode). It always writes ``frame_count`` PNGs and returns an
    immutable artifact contract.
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    if frame_count <= 0:
        raise ValueError(f"frame_count must be positive, got {frame_count}")

    if emit_banner:
        # SESSION-194 § ¶1 (UX zero-degradation): industrial-render banner.
        try:
            from mathart.core.anti_flicker_runtime import emit_industrial_baking_banner
            emit_industrial_baking_banner(stream=sys.stdout)
        except Exception as exc:  # pragma: no cover — log only, never block bake
            logger.debug("[openpose_pose_provider] banner emit skipped: %s", exc)

    if pose_frames is None:
        pose_frames = derive_industrial_walk_cycle(frame_count)
    elif len(pose_frames) != frame_count:
        # Resample / clip to match the requested frame count.
        if len(pose_frames) > frame_count:
            pose_frames = pose_frames[:frame_count]
        else:
            last = pose_frames[-1] if pose_frames else {}
            pose_frames = list(pose_frames) + [last] * (frame_count - len(pose_frames))

    # Convert to the (N, 18, 3) array the renderer expects.
    arr = np.stack(
        [_project_joints_to_coco18_array(pose) for pose in pose_frames],
        axis=0,
    )
    images = render_openpose_sequence(arr, width=width, height=height)

    written_paths: list[Path] = []
    for idx, img in enumerate(images):
        target = output_path / f"{sequence_name}_{idx:04d}.png"
        img.save(target, format="PNG")
        written_paths.append(target.resolve())

    if not written_paths:
        # Fail fast — see SESSION-194 ¶2 (反静默降级红线).
        raise RuntimeError(
            f"bake_openpose_pose_sequence wrote zero frames to {output_path}; "
            "downstream OpenPose ControlNet would silently degrade."
        )

    artifact = OpenPoseSequenceArtifact(
        artifact_family=ARTIFACT_FAMILY,
        backend_type=BACKEND_TYPE,
        sequence_directory=str(output_path.resolve()),
        frame_count=len(written_paths),
        width=width,
        height=height,
    )
    logger.info(
        "[openpose_pose_provider] SESSION-194 baked %d OpenPose frames at %s",
        artifact.frame_count, artifact.sequence_directory,
    )
    return artifact


__all__ = [
    "ARTIFACT_FAMILY",
    "BACKEND_TYPE",
    "OpenPoseSequenceArtifact",
    "derive_industrial_walk_cycle",
    "bake_openpose_pose_sequence",
]
