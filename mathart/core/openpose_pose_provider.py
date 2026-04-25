"""SESSION-194/195: OpenPose Pose Sequence Provider — IoC Payload Hook.

SESSION-194 established this module as the Provider half of the IoC contract
that connects the previously orphan ``openpose_skeleton_renderer`` to the
live anti-flicker render pipeline.

SESSION-195 UPGRADE — Gait Registry Expansion (P0):
  Refactored from a single hardcoded ``derive_industrial_walk_cycle`` to a
  **data-driven OpenPose Gait Registry** following the UE5 AnimGraph /
  Chooser-Table pattern (see docs/RESEARCH_NOTES_SESSION_195.md §1).

  Each gait template (walk, run, jump, idle, dash) is an independent
  **strategy class** registered into ``OpenPoseGaitRegistry``. The public
  ``bake_openpose_pose_sequence`` resolves the correct gait generator at
  runtime via the registry, never through if/elif chains (OCP red line).

  This mirrors the existing ``MotionStateLaneRegistry`` in
  ``mathart.animation.unified_gait_blender`` and honours the project's
  IoC / Registry Pattern architecture discipline.

Architecture Discipline:
  - NEVER uses if/elif to dispatch gait actions (anti-spaghetti red line).
  - Each gait is a self-contained strategy with its own Catmull-Rom keyframes.
  - New gaits are added by subclassing ``OpenPoseGaitStrategy`` and calling
    ``register_gait_strategy(instance)`` — zero modification to this file.
  - All node addressing uses ``class_type + _meta.title`` semantic selectors.
"""
from __future__ import annotations

import abc
import logging
import sys
from dataclasses import dataclass, field
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
_NOSE_OFFSETS_FOR_FACE: dict[int, tuple[float, float]] = {
    14: (-0.020, -0.020),  # r_eye
    15: (+0.020, -0.020),  # l_eye
    16: (-0.040, -0.010),  # r_ear
    17: (+0.040, -0.010),  # l_ear
}


def _project_joints_to_coco18_array(
    joint_positions: dict[str, tuple[float, float]],
) -> np.ndarray:
    """Convert a ``{joint_name: (x, y)}`` dict to a ``(18, 3)`` array."""
    arr = np.zeros((18, 3), dtype=np.float64)
    nose_xy: tuple[float, float] | None = None
    for joint_name, coco_idx in _PROJECT_TO_COCO18.items():
        if joint_name not in joint_positions:
            continue
        x, y = joint_positions[joint_name]
        img_x = 0.5 + float(x)
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
#  Catmull-Rom spline helpers — deterministic, CPU-only
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


# ═══════════════════════════════════════════════════════════════════════════
#  SESSION-195: OpenPose Gait Strategy (Abstract Base) + Registry
#  UE5 AnimGraph / Chooser-Table pattern — data-driven, zero if/elif.
# ═══════════════════════════════════════════════════════════════════════════

class OpenPoseGaitStrategy(abc.ABC):
    """Abstract base for a COCO-18 gait template generator.

    Each concrete strategy encapsulates one motion type (walk, run, jump,
    idle, dash, etc.) as an independent data-driven asset — mirroring
    UE5's Pose Search Database pattern where each animation state is a
    separate queryable asset, not a branch in a switch statement.
    """

    @property
    @abc.abstractmethod
    def action_name(self) -> str:
        """Canonical action name (e.g., 'walk', 'run', 'jump')."""
        ...

    @abc.abstractmethod
    def generate(self, frame_count: int) -> list[dict[str, tuple[float, float]]]:
        """Produce ``frame_count`` pose frames in project-skeleton space."""
        ...


class OpenPoseGaitRegistry:
    """Central registry for OpenPose gait strategies (Chooser Table).

    Mirrors ``MotionStateLaneRegistry`` from ``unified_gait_blender.py``.
    Strategies register themselves; the bake API resolves by action name.
    """

    def __init__(self) -> None:
        self._strategies: dict[str, OpenPoseGaitStrategy] = {}

    def register(self, strategy: OpenPoseGaitStrategy) -> None:
        self._strategies[strategy.action_name] = strategy

    def get(self, action_name: str) -> OpenPoseGaitStrategy | None:
        return self._strategies.get(action_name)

    def names(self) -> list[str]:
        return sorted(self._strategies.keys())

    def __contains__(self, action_name: str) -> bool:
        return action_name in self._strategies

    def __len__(self) -> int:
        return len(self._strategies)


# Module-level singleton registry
_GAIT_REGISTRY = OpenPoseGaitRegistry()


def register_gait_strategy(strategy: OpenPoseGaitStrategy) -> None:
    """Register a gait strategy into the global OpenPose gait registry."""
    _GAIT_REGISTRY.register(strategy)


def get_gait_registry() -> OpenPoseGaitRegistry:
    """Return the global OpenPose gait registry singleton."""
    return _GAIT_REGISTRY


# ═══════════════════════════════════════════════════════════════════════════
#  Built-in Gait Strategies — COCO-18 industrial keyframe templates
#  Each is a self-contained Strategy class with hand-authored Catmull-Rom
#  keyframes. No if/elif dispatch anywhere.
# ═══════════════════════════════════════════════════════════════════════════

class _WalkGaitStrategy(OpenPoseGaitStrategy):
    """Industrial walk cycle — heel strike / mid-stance / toe-off / mid-swing."""

    @property
    def action_name(self) -> str:
        return "walk"

    def generate(self, frame_count: int) -> list[dict[str, tuple[float, float]]]:
        l_hand_y_keys = [1.30, 1.20, 1.30, 1.40]
        r_hand_y_keys = [1.40, 1.30, 1.20, 1.30]
        l_foot_y_keys = [0.00, 0.05, 0.00, 0.10]
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


class _RunGaitStrategy(OpenPoseGaitStrategy):
    """Industrial run cycle — faster cadence, higher knee lift, arm pump."""

    @property
    def action_name(self) -> str:
        return "run"

    def generate(self, frame_count: int) -> list[dict[str, tuple[float, float]]]:
        # Run: wider arm swing, higher knee lift, forward lean
        l_hand_y_keys = [1.50, 1.10, 1.50, 1.60]
        r_hand_y_keys = [1.60, 1.50, 1.10, 1.50]
        l_foot_y_keys = [0.00, 0.15, 0.00, 0.20]
        r_foot_y_keys = [0.15, 0.00, 0.20, 0.00]
        l_knee_y_keys = [0.45, 0.35, 0.60, 0.70]
        r_knee_y_keys = [0.60, 0.70, 0.45, 0.35]
        head_y_keys   = [2.75, 2.82, 2.75, 2.70]
        spine_x_keys  = [0.03, 0.05, 0.03, 0.00]  # forward lean

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
                "l_shoulder": (sx - 0.55 * hu, hu * 2.3),
                "r_shoulder": (sx + 0.55 * hu, hu * 2.3),
                "l_elbow":    (sx - 0.9 * hu, hu * 1.9),
                "r_elbow":    (sx + 0.9 * hu, hu * 1.9),
                "l_hand":     (sx - 1.1 * hu, l_hand_y[i] * hu),
                "r_hand":     (sx + 1.1 * hu, r_hand_y[i] * hu),
                "l_hip":      (sx - 0.3 * hu, hu * 1.0),
                "r_hip":      (sx + 0.3 * hu, hu * 1.0),
                "l_knee":     (sx - 0.3 * hu, l_knee_y[i] * hu),
                "r_knee":     (sx + 0.3 * hu, r_knee_y[i] * hu),
                "l_foot":     (sx - 0.3 * hu, l_foot_y[i] * hu),
                "r_foot":     (sx + 0.3 * hu, r_foot_y[i] * hu),
            })
        return frames


class _JumpGaitStrategy(OpenPoseGaitStrategy):
    """Industrial jump cycle — anticipation / launch / apex / landing."""

    @property
    def action_name(self) -> str:
        return "jump"

    def generate(self, frame_count: int) -> list[dict[str, tuple[float, float]]]:
        # Jump: crouch → launch → apex (arms up) → landing
        head_y_keys   = [2.60, 2.50, 3.20, 3.30, 2.80, 2.60]
        l_hand_y_keys = [1.20, 1.00, 1.80, 2.00, 1.40, 1.20]
        r_hand_y_keys = [1.20, 1.00, 1.80, 2.00, 1.40, 1.20]
        l_foot_y_keys = [0.00, 0.00, 0.30, 0.35, 0.10, 0.00]
        r_foot_y_keys = [0.00, 0.00, 0.30, 0.35, 0.10, 0.00]
        l_knee_y_keys = [0.35, 0.30, 0.65, 0.70, 0.45, 0.35]
        r_knee_y_keys = [0.35, 0.30, 0.65, 0.70, 0.45, 0.35]
        spine_x_keys  = [0.00, 0.00, 0.00, 0.00, 0.00, 0.00]

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
                "l_elbow":    (sx - 0.8 * hu, hu * 2.0),
                "r_elbow":    (sx + 0.8 * hu, hu * 2.0),
                "l_hand":     (sx - 0.9 * hu, l_hand_y[i] * hu),
                "r_hand":     (sx + 0.9 * hu, r_hand_y[i] * hu),
                "l_hip":      (sx - 0.3 * hu, hu * 1.0),
                "r_hip":      (sx + 0.3 * hu, hu * 1.0),
                "l_knee":     (sx - 0.3 * hu, l_knee_y[i] * hu),
                "r_knee":     (sx + 0.3 * hu, r_knee_y[i] * hu),
                "l_foot":     (sx - 0.3 * hu, l_foot_y[i] * hu),
                "r_foot":     (sx + 0.3 * hu, r_foot_y[i] * hu),
            })
        return frames


class _IdleGaitStrategy(OpenPoseGaitStrategy):
    """Industrial idle / breathing cycle — subtle weight shift."""

    @property
    def action_name(self) -> str:
        return "idle"

    def generate(self, frame_count: int) -> list[dict[str, tuple[float, float]]]:
        # Idle: very subtle breathing sway, weight shift
        head_y_keys   = [2.80, 2.82, 2.80, 2.78]
        l_hand_y_keys = [1.25, 1.26, 1.25, 1.24]
        r_hand_y_keys = [1.25, 1.24, 1.25, 1.26]
        spine_x_keys  = [0.00, 0.005, 0.00, -0.005]

        hu = 1.0 / 3.0
        head_y   = _catmull_rom_along(head_y_keys,   frame_count)
        l_hand_y = _catmull_rom_along(l_hand_y_keys, frame_count)
        r_hand_y = _catmull_rom_along(r_hand_y_keys, frame_count)
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
                "l_knee":     (sx - 0.3 * hu, hu * 0.50),
                "r_knee":     (sx + 0.3 * hu, hu * 0.50),
                "l_foot":     (sx - 0.3 * hu, hu * 0.00),
                "r_foot":     (sx + 0.3 * hu, hu * 0.00),
            })
        return frames


class _DashGaitStrategy(OpenPoseGaitStrategy):
    """Industrial dash / sprint burst — extreme forward lean, explosive."""

    @property
    def action_name(self) -> str:
        return "dash"

    def generate(self, frame_count: int) -> list[dict[str, tuple[float, float]]]:
        # Dash: extreme forward lean, explosive arm pump, high knee drive
        l_hand_y_keys = [1.60, 1.00, 1.70, 1.80]
        r_hand_y_keys = [1.80, 1.60, 1.00, 1.70]
        l_foot_y_keys = [0.00, 0.20, 0.00, 0.25]
        r_foot_y_keys = [0.20, 0.00, 0.25, 0.00]
        l_knee_y_keys = [0.40, 0.30, 0.65, 0.75]
        r_knee_y_keys = [0.65, 0.75, 0.40, 0.30]
        head_y_keys   = [2.65, 2.72, 2.65, 2.60]
        spine_x_keys  = [0.06, 0.08, 0.06, 0.04]  # strong forward lean

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
                "l_shoulder": (sx - 0.5 * hu, hu * 2.3),
                "r_shoulder": (sx + 0.5 * hu, hu * 2.3),
                "l_elbow":    (sx - 0.85 * hu, hu * 1.95),
                "r_elbow":    (sx + 0.85 * hu, hu * 1.95),
                "l_hand":     (sx - 1.0 * hu, l_hand_y[i] * hu),
                "r_hand":     (sx + 1.0 * hu, r_hand_y[i] * hu),
                "l_hip":      (sx - 0.3 * hu, hu * 1.0),
                "r_hip":      (sx + 0.3 * hu, hu * 1.0),
                "l_knee":     (sx - 0.3 * hu, l_knee_y[i] * hu),
                "r_knee":     (sx + 0.3 * hu, r_knee_y[i] * hu),
                "l_foot":     (sx - 0.3 * hu, l_foot_y[i] * hu),
                "r_foot":     (sx + 0.3 * hu, r_foot_y[i] * hu),
            })
        return frames


# ── Auto-register built-in gait strategies at import time ──
register_gait_strategy(_WalkGaitStrategy())
register_gait_strategy(_RunGaitStrategy())
register_gait_strategy(_JumpGaitStrategy())
register_gait_strategy(_IdleGaitStrategy())
register_gait_strategy(_DashGaitStrategy())


# ═══════════════════════════════════════════════════════════════════════════
#  Legacy compatibility wrapper
# ═══════════════════════════════════════════════════════════════════════════
def derive_industrial_walk_cycle(frame_count: int) -> list[dict[str, tuple[float, float]]]:
    """Legacy compatibility: delegates to the walk strategy in the registry."""
    strategy = _GAIT_REGISTRY.get("walk")
    if strategy is None:
        raise RuntimeError("Walk gait strategy not registered")
    return strategy.generate(frame_count)


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
    action_name: str = "walk",
    sequence_name: str = "openpose_pose",
    emit_banner: bool = True,
) -> OpenPoseSequenceArtifact:
    """Bake a real OpenPose COCO-18 pose sequence to disk.

    SESSION-195 upgrade: now accepts ``action_name`` to resolve the correct
    gait strategy from the registry. Falls back to ``walk`` if the requested
    action is not registered (graceful degradation, not crash).
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    if frame_count <= 0:
        raise ValueError(f"frame_count must be positive, got {frame_count}")

    if emit_banner:
        try:
            from mathart.core.anti_flicker_runtime import emit_industrial_baking_banner
            emit_industrial_baking_banner(stream=sys.stdout)
        except Exception as exc:  # pragma: no cover
            logger.debug("[openpose_pose_provider] banner emit skipped: %s", exc)

    if pose_frames is None:
        # SESSION-195: resolve gait from registry by action_name
        strategy = _GAIT_REGISTRY.get(action_name)
        if strategy is None:
            logger.warning(
                "[openpose_pose_provider] action '%s' not in gait registry, "
                "falling back to 'walk'. Registered: %s",
                action_name, _GAIT_REGISTRY.names(),
            )
            strategy = _GAIT_REGISTRY.get("walk")
        if strategy is not None:
            pose_frames = strategy.generate(frame_count)
        else:
            # Ultimate fallback: should never happen with built-in registration
            pose_frames = _WalkGaitStrategy().generate(frame_count)
    elif len(pose_frames) != frame_count:
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
        "[openpose_pose_provider] SESSION-195 baked %d OpenPose frames (%s) at %s",
        artifact.frame_count, action_name, artifact.sequence_directory,
    )
    return artifact


__all__ = [
    "ARTIFACT_FAMILY",
    "BACKEND_TYPE",
    "OpenPoseSequenceArtifact",
    "OpenPoseGaitStrategy",
    "OpenPoseGaitRegistry",
    "register_gait_strategy",
    "get_gait_registry",
    "derive_industrial_walk_cycle",
    "bake_openpose_pose_sequence",
]
