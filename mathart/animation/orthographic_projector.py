"""
SESSION-061: Orthographic Projection Pipeline — 3D NSM to 2D Animation

Research foundations:
  1. **Sebastian Starke — Mode-Adaptive Neural Networks for Quadruped Motion
     Control (SIGGRAPH 2018)**: The gating network dynamically blends expert
     weights for asymmetric quadruped gaits.  Our projector consumes the
     per-limb contact labels and target offsets produced by the distilled NSM
     runtime and maps them into a 2D bone hierarchy.

  2. **Daniel Holden — Phase-Functioned Neural Networks for Character Control
     (SIGGRAPH 2017)**: Terrain heightmap input drives phase-cycled weight
     interpolation.  We adopt the concept of terrain geometry as a first-class
     input by coupling the projector with ``TerrainSDF`` queries that feed
     the 2D IK solver.

  3. **Spine JSON Export Format (Esoteric Software)**: The industry standard
     for 2D skeletal animation interchange.  Our exporter emits a
     Spine-compatible JSON bundle (skeleton, bones, slots, IK constraints,
     and animation timelines) so that downstream engines (Unity, Godot,
     custom runtimes) can load the projected motion directly.

Architecture:
    ┌──────────────────────────────────────────────────────────────────────┐
    │  OrthographicProjector                                              │
    │  ├─ project_skeleton(3D bones) → 2D bone hierarchy                 │
    │  ├─ project_pose(3D joint angles) → 2D joint angles + depth order  │
    │  └─ depth_to_sorting_order(z_depth) → integer layer index          │
    ├──────────────────────────────────────────────────────────────────────┤
    │  SpineJSONExporter                                                  │
    │  ├─ build_skeleton_data(projected bones) → Spine skeleton dict      │
    │  ├─ build_animation_data(clip frames) → Spine animation dict        │
    │  ├─ build_ik_constraints(IK targets) → Spine IK constraint list     │
    │  └─ export(clip, path) → writes .json file                          │
    ├──────────────────────────────────────────────────────────────────────┤
    │  ProjectionQualityMetrics                                           │
    │  ├─ depth_ordering_consistency → % frames with stable sort order    │
    │  ├─ bone_length_preservation → mean ratio vs original 3D length     │
    │  └─ joint_angle_fidelity → mean angular error after projection      │
    └──────────────────────────────────────────────────────────────────────┘

Usage::

    from mathart.animation.orthographic_projector import (
        OrthographicProjector, SpineJSONExporter,
        ProjectionConfig, ProjectionQualityMetrics,
    )

    projector = OrthographicProjector(ProjectionConfig())
    bones_2d = projector.project_skeleton(bones_3d)
    clip_2d  = projector.project_clip(clip_3d)
    metrics  = projector.evaluate_quality(clip_3d, clip_2d)

    exporter = SpineJSONExporter()
    exporter.export(clip_2d, "output/mario_walk.json")
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional, Sequence

import numpy as np


# ═══════════════════════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ProjectionConfig:
    """Configuration for the orthographic projection pipeline.

    The projection plane is defined by *keep_axes* (default ``"xy"``),
    meaning X and Y displacements are preserved while Z is discarded
    from positional data but retained as a sorting-order signal.
    """

    keep_axes: str = "xy"
    depth_axis: str = "z"
    sorting_layers: int = 16
    depth_range: tuple[float, float] = (-2.0, 2.0)
    preserve_z_rotation: bool = True
    scale: float = 1.0
    flip_y: bool = False


# ═══════════════════════════════════════════════════════════════════════════════
# Data Structures
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Bone3D:
    """A bone in 3D space with position, rotation, and hierarchy."""

    name: str
    parent: Optional[str] = None
    position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0)
    length: float = 0.0
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0)


@dataclass
class Bone2D:
    """A bone projected into 2D with sorting order derived from depth."""

    name: str
    parent: Optional[str] = None
    x: float = 0.0
    y: float = 0.0
    rotation: float = 0.0
    length: float = 0.0
    scale_x: float = 1.0
    scale_y: float = 1.0
    sorting_order: int = 0
    original_depth: float = 0.0


@dataclass
class Pose3D:
    """A single-frame 3D pose: mapping from bone name to local transform."""

    bone_transforms: dict[str, dict[str, float]] = field(default_factory=dict)
    root_position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    root_rotation: tuple[float, float, float] = (0.0, 0.0, 0.0)
    contact_labels: dict[str, float] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Pose2D:
    """A single-frame 2D pose after orthographic projection."""

    bone_transforms: dict[str, dict[str, float]] = field(default_factory=dict)
    root_x: float = 0.0
    root_y: float = 0.0
    root_rotation: float = 0.0
    sorting_orders: dict[str, int] = field(default_factory=dict)
    contact_labels: dict[str, float] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Clip3D:
    """A sequence of 3D poses forming an animation clip."""

    name: str = "untitled"
    fps: float = 30.0
    frames: list[Pose3D] = field(default_factory=list)
    skeleton_bones: list[Bone3D] = field(default_factory=list)


@dataclass
class Clip2D:
    """A sequence of 2D poses after orthographic projection."""

    name: str = "untitled"
    fps: float = 30.0
    frames: list[Pose2D] = field(default_factory=list)
    skeleton_bones: list[Bone2D] = field(default_factory=list)
    ik_constraints: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProjectionQualityMetrics:
    """Quality metrics for evaluating the orthographic projection."""

    depth_ordering_consistency: float = 0.0
    bone_length_preservation: float = 0.0
    joint_angle_fidelity: float = 0.0
    sorting_order_stability: float = 0.0
    total_frames: int = 0
    total_bones: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ═══════════════════════════════════════════════════════════════════════════════
# Orthographic Projector
# ═══════════════════════════════════════════════════════════════════════════════

class OrthographicProjector:
    """Projects 3D skeletal data into 2D using orthographic projection.

    The projector implements the dimensionality reduction described in
    Phase 3 of the MarioTrickster-MathArt roadmap: 3D NSM bone data is
    projected onto the XY plane, Z-axis rotation is preserved, and
    Z-depth is converted into integer sorting orders for foreground /
    background layering.
    """

    def __init__(self, config: Optional[ProjectionConfig] = None) -> None:
        self.config = config or ProjectionConfig()

    def depth_to_sorting_order(self, z: float) -> int:
        """Map a Z-depth value to an integer sorting-order layer.

        Depth values are linearly mapped from ``config.depth_range`` into
        ``[0, config.sorting_layers - 1]``.  Bones closer to the camera
        (lower Z in a right-handed system) receive higher sorting orders
        so they render in front.
        """
        lo, hi = self.config.depth_range
        if hi <= lo:
            return 0
        t = max(0.0, min(1.0, (z - lo) / (hi - lo)))
        layer = int(round((1.0 - t) * (self.config.sorting_layers - 1)))
        return max(0, min(self.config.sorting_layers - 1, layer))

    def project_bone(self, bone: Bone3D) -> Bone2D:
        """Project a single 3D bone into 2D."""
        px, py, pz = bone.position
        rx, ry, rz = bone.rotation
        sx, sy, sz = bone.scale
        scale = self.config.scale
        y_mult = -1.0 if self.config.flip_y else 1.0

        return Bone2D(
            name=bone.name,
            parent=bone.parent,
            x=px * scale,
            y=py * scale * y_mult,
            rotation=rz if self.config.preserve_z_rotation else 0.0,
            length=bone.length * scale,
            scale_x=sx,
            scale_y=sy,
            sorting_order=self.depth_to_sorting_order(pz),
            original_depth=pz,
        )

    def project_skeleton(self, bones_3d: Sequence[Bone3D]) -> list[Bone2D]:
        """Project an entire 3D skeleton into 2D."""
        return [self.project_bone(b) for b in bones_3d]

    def project_pose(self, pose: Pose3D) -> Pose2D:
        """Project a single 3D pose frame into 2D.

        For each bone transform, X/Y translation and Z rotation are kept;
        Z translation is converted to a sorting order.
        """
        result = Pose2D()
        rx, ry, rz = pose.root_position
        scale = self.config.scale
        y_mult = -1.0 if self.config.flip_y else 1.0
        result.root_x = rx * scale
        result.root_y = ry * scale * y_mult
        rrx, rry, rrz = pose.root_rotation
        result.root_rotation = rrz

        for bone_name, xform in pose.bone_transforms.items():
            tx = float(xform.get("tx", 0.0)) * scale
            ty = float(xform.get("ty", 0.0)) * scale * y_mult
            tz = float(xform.get("tz", 0.0))
            rot_z = float(xform.get("rz", 0.0))
            sx = float(xform.get("sx", 1.0))
            sy = float(xform.get("sy", 1.0))

            result.bone_transforms[bone_name] = {
                "tx": tx,
                "ty": ty,
                "rotation": rot_z,
                "sx": sx,
                "sy": sy,
            }
            result.sorting_orders[bone_name] = self.depth_to_sorting_order(tz)

        result.contact_labels = dict(pose.contact_labels)
        result.metadata = dict(pose.metadata)
        result.metadata["projection"] = "orthographic"
        result.metadata["depth_axis"] = self.config.depth_axis
        return result

    def project_clip(self, clip: Clip3D) -> Clip2D:
        """Project an entire 3D animation clip into 2D."""
        bones_2d = self.project_skeleton(clip.skeleton_bones)
        frames_2d = [self.project_pose(f) for f in clip.frames]
        return Clip2D(
            name=clip.name,
            fps=clip.fps,
            frames=frames_2d,
            skeleton_bones=bones_2d,
            metadata=dict(clip.metadata),
        )

    def evaluate_quality(
        self,
        clip_3d: Clip3D,
        clip_2d: Clip2D,
    ) -> ProjectionQualityMetrics:
        """Evaluate projection quality by comparing 3D and 2D clips."""
        metrics = ProjectionQualityMetrics(
            total_frames=len(clip_2d.frames),
            total_bones=len(clip_2d.skeleton_bones),
        )
        if not clip_2d.frames or not clip_3d.frames:
            return metrics

        # Bone length preservation
        length_ratios: list[float] = []
        for b3, b2 in zip(clip_3d.skeleton_bones, clip_2d.skeleton_bones):
            if b3.length > 1e-6:
                ratio = b2.length / (b3.length * self.config.scale)
                length_ratios.append(ratio)
        metrics.bone_length_preservation = (
            float(np.mean(length_ratios)) if length_ratios else 1.0
        )

        # Joint angle fidelity (Z-rotation preservation)
        angle_errors: list[float] = []
        for f3, f2 in zip(clip_3d.frames, clip_2d.frames):
            for bname in f3.bone_transforms:
                rz_3d = float(f3.bone_transforms[bname].get("rz", 0.0))
                rz_2d = float(f2.bone_transforms.get(bname, {}).get("rotation", 0.0))
                err = abs(rz_3d - rz_2d)
                angle_errors.append(err)
        metrics.joint_angle_fidelity = (
            1.0 - min(float(np.mean(angle_errors)) / 180.0, 1.0)
            if angle_errors
            else 1.0
        )

        # Sorting order stability (frame-to-frame consistency)
        if len(clip_2d.frames) >= 2:
            stable = 0
            total = 0
            for i in range(1, len(clip_2d.frames)):
                prev = clip_2d.frames[i - 1].sorting_orders
                curr = clip_2d.frames[i].sorting_orders
                for bname in prev:
                    if bname in curr:
                        total += 1
                        if prev[bname] == curr[bname]:
                            stable += 1
            metrics.sorting_order_stability = (
                stable / total if total > 0 else 1.0
            )
            metrics.depth_ordering_consistency = metrics.sorting_order_stability
        else:
            metrics.sorting_order_stability = 1.0
            metrics.depth_ordering_consistency = 1.0

        return metrics


# ═══════════════════════════════════════════════════════════════════════════════
# Spine JSON Exporter
# ═══════════════════════════════════════════════════════════════════════════════

class SpineJSONExporter:
    """Export projected 2D clips to Spine-compatible JSON format.

    The exported JSON follows the Spine JSON export format specification
    (https://esotericsoftware.com/spine-json-format) and includes:
    - Skeleton metadata
    - Bone hierarchy with setup pose
    - Slot definitions with draw order (from sorting order)
    - IK constraint definitions
    - Animation timelines (bone rotate, translate, scale)
    """

    def __init__(self, spine_version: str = "4.2") -> None:
        self.spine_version = spine_version

    def build_skeleton_data(self, clip: Clip2D) -> dict[str, Any]:
        """Build the top-level skeleton metadata."""
        return {
            "skeleton": {
                "hash": "",
                "spine": self.spine_version,
                "x": 0,
                "y": 0,
                "width": 256,
                "height": 256,
                "fps": clip.fps,
                "images": "",
                "audio": "",
            }
        }

    def build_bones(self, bones: Sequence[Bone2D]) -> list[dict[str, Any]]:
        """Build the bones array for Spine JSON."""
        result: list[dict[str, Any]] = []
        for bone in bones:
            entry: dict[str, Any] = {"name": bone.name}
            if bone.parent:
                entry["parent"] = bone.parent
            if abs(bone.length) > 1e-6:
                entry["length"] = round(bone.length, 2)
            if abs(bone.rotation) > 1e-6:
                entry["rotation"] = round(bone.rotation, 2)
            if abs(bone.x) > 1e-6:
                entry["x"] = round(bone.x, 2)
            if abs(bone.y) > 1e-6:
                entry["y"] = round(bone.y, 2)
            if abs(bone.scale_x - 1.0) > 1e-6:
                entry["scaleX"] = round(bone.scale_x, 4)
            if abs(bone.scale_y - 1.0) > 1e-6:
                entry["scaleY"] = round(bone.scale_y, 4)
            result.append(entry)
        return result

    def build_slots(self, bones: Sequence[Bone2D]) -> list[dict[str, Any]]:
        """Build slot definitions sorted by sorting order (draw order)."""
        sorted_bones = sorted(bones, key=lambda b: b.sorting_order)
        return [
            {
                "name": f"slot_{bone.name}",
                "bone": bone.name,
                "attachment": bone.name,
            }
            for bone in sorted_bones
        ]

    def build_ik_constraints(
        self,
        constraints: Sequence[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Build IK constraint definitions for Spine JSON."""
        result: list[dict[str, Any]] = []
        for c in constraints:
            entry: dict[str, Any] = {
                "name": str(c.get("name", "ik")),
                "order": int(c.get("order", 0)),
                "bones": list(c.get("bones", [])),
                "target": str(c.get("target", "")),
            }
            if "mix" in c:
                entry["mix"] = float(c["mix"])
            if "bendPositive" in c:
                entry["bendPositive"] = bool(c["bendPositive"])
            if "softness" in c:
                entry["softness"] = float(c["softness"])
            result.append(entry)
        return result

    def build_animation(
        self,
        clip: Clip2D,
    ) -> dict[str, Any]:
        """Build animation timelines from projected clip frames."""
        if not clip.frames:
            return {}

        dt = 1.0 / max(clip.fps, 1.0)
        bone_timelines: dict[str, dict[str, list[dict[str, Any]]]] = {}

        for frame_idx, frame in enumerate(clip.frames):
            time_val = round(frame_idx * dt, 4)

            for bone_name, xform in frame.bone_transforms.items():
                if bone_name not in bone_timelines:
                    bone_timelines[bone_name] = {
                        "rotate": [],
                        "translate": [],
                        "scale": [],
                    }

                rotation = float(xform.get("rotation", 0.0))
                bone_timelines[bone_name]["rotate"].append({
                    "time": time_val,
                    "angle": round(rotation, 2),
                })

                tx = float(xform.get("tx", 0.0))
                ty = float(xform.get("ty", 0.0))
                if abs(tx) > 1e-6 or abs(ty) > 1e-6:
                    bone_timelines[bone_name]["translate"].append({
                        "time": time_val,
                        "x": round(tx, 2),
                        "y": round(ty, 2),
                    })

                sx = float(xform.get("sx", 1.0))
                sy = float(xform.get("sy", 1.0))
                if abs(sx - 1.0) > 1e-4 or abs(sy - 1.0) > 1e-4:
                    bone_timelines[bone_name]["scale"].append({
                        "time": time_val,
                        "x": round(sx, 4),
                        "y": round(sy, 4),
                    })

        # Clean up empty timeline types
        for bone_name in bone_timelines:
            bone_timelines[bone_name] = {
                k: v for k, v in bone_timelines[bone_name].items() if v
            }

        return {
            clip.name: {
                "bones": bone_timelines,
            }
        }

    def export(
        self,
        clip: Clip2D,
        output_path: str | Path,
        *,
        apply_anime_timing: bool = True,
        apply_squash_stretch: bool = True,
    ) -> Path:
        """Export a projected 2D clip to Spine JSON format."""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        export_clip = clip
        if apply_anime_timing:
            from mathart.animation.anime_timing_modifier import apply_to_clip2d as apply_anime_timing_to_clip2d
            export_clip = apply_anime_timing_to_clip2d(export_clip)
        if apply_squash_stretch:
            from mathart.animation.squash_stretch_modifier import apply_to_clip2d
            export_clip = apply_to_clip2d(export_clip)

        data: dict[str, Any] = {}
        data.update(self.build_skeleton_data(export_clip))
        data["bones"] = self.build_bones(export_clip.skeleton_bones)
        data["slots"] = self.build_slots(export_clip.skeleton_bones)

        if export_clip.ik_constraints:
            data["ik"] = self.build_ik_constraints(export_clip.ik_constraints)

        data["animations"] = self.build_animation(export_clip)

        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return path


# ═══════════════════════════════════════════════════════════════════════════════
# Convenience Factories
# ═══════════════════════════════════════════════════════════════════════════════

def create_biped_skeleton_3d() -> list[Bone3D]:
    """Create a standard biped skeleton in 3D for testing."""
    return [
        Bone3D("root", None, (0, 0, 0), (0, 0, 0), 0.0),
        Bone3D("hip", "root", (0, 0.8, 0), (0, 0, 0), 0.15),
        Bone3D("spine", "hip", (0, 0.2, 0), (0, 0, 0), 0.2),
        Bone3D("chest", "spine", (0, 0.2, 0), (0, 0, 0), 0.2),
        Bone3D("head", "chest", (0, 0.15, 0), (0, 0, 0), 0.15),
        Bone3D("l_thigh", "hip", (-0.1, 0, 0.01), (0, 0, 0), 0.3),
        Bone3D("l_shin", "l_thigh", (0, -0.3, 0), (0, 0, 0), 0.3),
        Bone3D("l_foot", "l_shin", (0, -0.3, 0), (0, 0, 0), 0.1),
        Bone3D("r_thigh", "hip", (0.1, 0, -0.01), (0, 0, 0), 0.3),
        Bone3D("r_shin", "r_thigh", (0, -0.3, 0), (0, 0, 0), 0.3),
        Bone3D("r_foot", "r_shin", (0, -0.3, 0), (0, 0, 0), 0.1),
        Bone3D("l_arm", "chest", (-0.15, 0.1, 0.02), (0, 0, 0), 0.2),
        Bone3D("l_forearm", "l_arm", (0, -0.2, 0), (0, 0, 0), 0.2),
        Bone3D("r_arm", "chest", (0.15, 0.1, -0.02), (0, 0, 0), 0.2),
        Bone3D("r_forearm", "r_arm", (0, -0.2, 0), (0, 0, 0), 0.2),
    ]


def create_quadruped_skeleton_3d() -> list[Bone3D]:
    """Create a standard quadruped skeleton in 3D for testing."""
    return [
        Bone3D("root", None, (0, 0, 0), (0, 0, 0), 0.0),
        Bone3D("spine_base", "root", (0, 0.5, 0), (0, 0, 0), 0.4),
        Bone3D("spine_mid", "spine_base", (0.2, 0, 0), (0, 0, 0), 0.3),
        Bone3D("spine_top", "spine_mid", (0.2, 0, 0), (0, 0, 0), 0.3),
        Bone3D("head", "spine_top", (0.15, 0.05, 0), (0, 0, 0), 0.15),
        Bone3D("tail", "spine_base", (-0.15, 0.05, 0), (0, 0, 0), 0.2),
        Bone3D("fl_upper", "spine_top", (-0.05, -0.05, 0.1), (0, 0, 0), 0.2),
        Bone3D("fl_lower", "fl_upper", (0, -0.2, 0), (0, 0, 0), 0.2),
        Bone3D("fl_paw", "fl_lower", (0, -0.2, 0), (0, 0, 0), 0.05),
        Bone3D("fr_upper", "spine_top", (-0.05, -0.05, -0.1), (0, 0, 0), 0.2),
        Bone3D("fr_lower", "fr_upper", (0, -0.2, 0), (0, 0, 0), 0.2),
        Bone3D("fr_paw", "fr_lower", (0, -0.2, 0), (0, 0, 0), 0.05),
        Bone3D("hl_upper", "spine_base", (0.05, -0.05, 0.1), (0, 0, 0), 0.2),
        Bone3D("hl_lower", "hl_upper", (0, -0.2, 0), (0, 0, 0), 0.2),
        Bone3D("hl_paw", "hl_lower", (0, -0.2, 0), (0, 0, 0), 0.05),
        Bone3D("hr_upper", "spine_base", (0.05, -0.05, -0.1), (0, 0, 0), 0.2),
        Bone3D("hr_lower", "hr_upper", (0, -0.2, 0), (0, 0, 0), 0.2),
        Bone3D("hr_paw", "hr_lower", (0, -0.2, 0), (0, 0, 0), 0.05),
    ]


def create_sample_walk_clip_3d(
    n_frames: int = 30,
    fps: float = 30.0,
) -> Clip3D:
    """Create a sample 3D walk clip for testing the projection pipeline."""
    bones = create_biped_skeleton_3d()
    frames: list[Pose3D] = []
    for i in range(n_frames):
        t = i / max(n_frames - 1, 1)
        phase = t * 2.0 * math.pi
        pose = Pose3D(
            root_position=(t * 2.0, 0.0, 0.0),
            root_rotation=(0.0, 0.0, 0.0),
        )
        swing = 25.0 * math.sin(phase)
        pose.bone_transforms = {
            "l_thigh": {"tx": 0.0, "ty": 0.0, "tz": 0.01, "rz": swing, "sx": 1.0, "sy": 1.0},
            "l_shin": {"tx": 0.0, "ty": 0.0, "tz": 0.01, "rz": max(0, swing) * 0.5, "sx": 1.0, "sy": 1.0},
            "r_thigh": {"tx": 0.0, "ty": 0.0, "tz": -0.01, "rz": -swing, "sx": 1.0, "sy": 1.0},
            "r_shin": {"tx": 0.0, "ty": 0.0, "tz": -0.01, "rz": max(0, -swing) * 0.5, "sx": 1.0, "sy": 1.0},
            "l_arm": {"tx": 0.0, "ty": 0.0, "tz": 0.02, "rz": -swing * 0.5, "sx": 1.0, "sy": 1.0},
            "r_arm": {"tx": 0.0, "ty": 0.0, "tz": -0.02, "rz": swing * 0.5, "sx": 1.0, "sy": 1.0},
            "spine": {"tx": 0.0, "ty": 0.0, "tz": 0.0, "rz": swing * 0.05, "sx": 1.0, "sy": 1.0},
        }
        l_contact = 1.0 if math.sin(phase) <= 0 else 0.0
        r_contact = 1.0 if math.sin(phase) >= 0 else 0.0
        pose.contact_labels = {"l_foot": l_contact, "r_foot": r_contact}
        frames.append(pose)

    return Clip3D(name="walk", fps=fps, frames=frames, skeleton_bones=bones)


__all__ = [
    "ProjectionConfig",
    "Bone3D",
    "Bone2D",
    "Pose3D",
    "Pose2D",
    "Clip3D",
    "Clip2D",
    "ProjectionQualityMetrics",
    "OrthographicProjector",
    "SpineJSONExporter",
    "create_biped_skeleton_3d",
    "create_quadruped_skeleton_3d",
    "create_sample_walk_clip_3d",
]
