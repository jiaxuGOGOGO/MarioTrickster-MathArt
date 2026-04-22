"""
SESSION-125 (P2-SPINE-PREVIEW-1): Tensorized Spine FK preview pipeline.

This module implements a pure-Python, engine-independent preview path for
Spine JSON skeletal animation. It deliberately follows the same Ports and
Adapters discipline used elsewhere in the repository:

1. ``SpineJSONTensorSolver`` is a pure math module that reads a Spine JSON file,
   interpolates translate / rotate / scale timelines, and produces a
   ``[frames, bones, 3, 3]`` local/world affine matrix tensor.
2. ``HeadlessSpineRenderer`` consumes the solver output and renders a headless
   MP4 / GIF preview using NumPy + OpenCV only. No GUI calls are allowed.
3. The registry-native backend wrapper lives in
   ``mathart.core.spine_preview_backend`` and returns a strongly-typed
   ``ArtifactManifest``.

Research foundations
--------------------
1. Spine Runtime Skeletons (Esoteric Software): world transforms must be built
   from local transforms in parent-before-child order, and the world matrix is
   the canonical representation used for rendering and downstream effects.
2. CSE169 Skeletons chapter: forward kinematics is split into local joint
   matrix construction and world matrix concatenation.
3. NumPy ``matmul`` documentation: stacked matrices broadcast over leading
   dimensions, allowing ``parent_world @ local`` to solve all frames at once.
4. OpenCV ``VideoWriter`` documentation: video export should stream headlessly
   through a writer API rather than any GUI display functions.
5. Matplotlib backend documentation: Agg is a non-interactive file backend.
   We therefore prohibit any interactive display dependency here.

Red-line guards
---------------
- Anti-GUI-Blocking: NEVER call ``cv2.imshow()``, ``cv2.waitKey()``, or any
  interactive Matplotlib backend.
- Anti-Scalar-Frame-Loop: the FK hot path never uses nested Python loops over
  frames and bones for matrix propagation.
- Anti-Coordinate-Inversion: render projection flips Y so the visual output
  stays upright even though image coordinates grow downward.
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

import numpy as np

# SESSION-145: cv2 and PIL are lazy-imported inside the methods that
# actually need them (_draw_frames, _write_mp4, _write_gif) to preserve
# the sub-second cold-start guarantee of the top-level `mathart` wizard.
# See also: mathart/quality/interactive_gate.py for the same discipline.


@dataclass(frozen=True)
class SpineBoneRecord:
    """Normalized setup-pose bone record parsed from Spine JSON."""

    name: str
    parent: Optional[str]
    length: float
    x: float
    y: float
    rotation: float
    scale_x: float
    scale_y: float
    shear_x: float
    shear_y: float


@dataclass
class SpinePreviewClip:
    """Fully solved preview clip produced from a Spine JSON animation."""

    source_path: str
    animation_name: str
    fps: float
    frame_times: np.ndarray
    bone_names: tuple[str, ...]
    parent_indices: np.ndarray
    topology_order: tuple[int, ...]
    depth_levels: tuple[tuple[int, ...], ...]
    bone_lengths: np.ndarray
    local_matrices: np.ndarray
    world_matrices: np.ndarray
    world_origins: np.ndarray
    world_tips: np.ndarray
    global_rotations_deg: np.ndarray
    bounds_xy: np.ndarray

    @property
    def frame_count(self) -> int:
        return int(self.frame_times.shape[0])

    @property
    def bone_count(self) -> int:
        return int(len(self.bone_names))


@dataclass
class SpinePreviewRenderResult:
    """Output bundle from the headless preview renderer."""

    mp4_path: str
    gif_path: str
    diagnostics_path: str
    frame_count: int
    bone_count: int
    fps: float
    render_time_ms: float
    canvas_size: tuple[int, int]
    bounds_xy: list[float]


class SpineJSONTensorSolver:
    """Parse Spine JSON and solve local/world transform tensors.

    The solver keeps the FK hot path tensorized across frames. Only metadata
    preparation (timeline extraction, topology analysis) iterates in Python.
    World-matrix propagation is executed depth-by-depth with batched
    ``np.matmul`` across all frames at once.
    """

    def __init__(self, fps: float | None = None) -> None:
        self.default_fps = float(fps) if fps is not None else None

    def solve(
        self,
        spine_json_path: str | Path,
        *,
        animation_name: str | None = None,
        fps: float | None = None,
    ) -> SpinePreviewClip:
        path = Path(spine_json_path)
        data = json.loads(path.read_text(encoding="utf-8"))
        bones = self._parse_bones(data)
        if not bones:
            raise ValueError("Spine JSON contains no bones; cannot build preview")

        animation_name, animation_block = self._select_animation(data, animation_name)
        resolved_fps = self._resolve_fps(data, fps)
        frame_times = self._build_frame_times(animation_block, resolved_fps)
        parent_indices, topology_order, depth_levels = self._build_topology(bones)
        local = self._build_local_matrix_tensor(
            bones=bones,
            animation_block=animation_block,
            frame_times=frame_times,
        )
        world = self._propagate_world_matrices(
            local_matrices=local,
            parent_indices=parent_indices,
            depth_levels=depth_levels,
        )
        lengths = np.asarray([b.length for b in bones], dtype=np.float64)
        origins, tips = self._extract_world_points(world, lengths)
        global_rot = np.degrees(np.arctan2(world[:, :, 1, 0], world[:, :, 0, 0]))
        bounds = self._compute_bounds(origins, tips)
        return SpinePreviewClip(
            source_path=str(path),
            animation_name=animation_name,
            fps=resolved_fps,
            frame_times=frame_times,
            bone_names=tuple(b.name for b in bones),
            parent_indices=parent_indices,
            topology_order=topology_order,
            depth_levels=depth_levels,
            bone_lengths=lengths,
            local_matrices=local,
            world_matrices=world,
            world_origins=origins,
            world_tips=tips,
            global_rotations_deg=global_rot,
            bounds_xy=bounds,
        )

    def _parse_bones(self, data: dict[str, Any]) -> list[SpineBoneRecord]:
        result: list[SpineBoneRecord] = []
        for raw in data.get("bones", []) or []:
            result.append(
                SpineBoneRecord(
                    name=str(raw.get("name", "")),
                    parent=(None if raw.get("parent") in (None, "") else str(raw.get("parent"))),
                    length=float(raw.get("length", 0.0) or 0.0),
                    x=float(raw.get("x", 0.0) or 0.0),
                    y=float(raw.get("y", 0.0) or 0.0),
                    rotation=float(raw.get("rotation", 0.0) or 0.0),
                    scale_x=float(raw.get("scaleX", 1.0) or 1.0),
                    scale_y=float(raw.get("scaleY", 1.0) or 1.0),
                    shear_x=float(raw.get("shearX", 0.0) or 0.0),
                    shear_y=float(raw.get("shearY", 0.0) or 0.0),
                )
            )
        return result

    def _select_animation(
        self,
        data: dict[str, Any],
        requested_name: str | None,
    ) -> tuple[str, dict[str, Any]]:
        animations = data.get("animations", {}) or {}
        if not animations:
            return "setup_pose", {}
        if requested_name:
            if requested_name not in animations:
                available = ", ".join(sorted(animations))
                raise KeyError(
                    f"Animation {requested_name!r} not found in Spine JSON. Available: {available}"
                )
            return requested_name, dict(animations[requested_name] or {})
        name = next(iter(animations))
        return str(name), dict(animations[name] or {})

    def _resolve_fps(
        self,
        data: dict[str, Any],
        fps: float | None,
    ) -> float:
        if fps is not None:
            return max(float(fps), 1.0)
        if self.default_fps is not None:
            return max(float(self.default_fps), 1.0)
        skeleton = data.get("skeleton", {}) or {}
        return max(float(skeleton.get("fps", 30.0) or 30.0), 1.0)

    def _build_frame_times(
        self,
        animation_block: dict[str, Any],
        fps: float,
    ) -> np.ndarray:
        max_time = 0.0
        bones_block = animation_block.get("bones", {}) or {}
        for timelines in bones_block.values():
            for key in ("rotate", "translate", "scale"):
                for frame in timelines.get(key, []) or []:
                    max_time = max(max_time, float(frame.get("time", 0.0) or 0.0))
        frame_count = max(1, int(round(max_time * fps)) + 1)
        return np.arange(frame_count, dtype=np.float64) / float(fps)

    def _build_topology(
        self,
        bones: list[SpineBoneRecord],
    ) -> tuple[np.ndarray, tuple[int, ...], tuple[tuple[int, ...], ...]]:
        name_to_index = {bone.name: idx for idx, bone in enumerate(bones)}
        n = len(bones)
        parent_indices = np.full(n, -1, dtype=np.int32)
        children: list[list[int]] = [[] for _ in range(n)]
        indegree = np.zeros(n, dtype=np.int32)

        for idx, bone in enumerate(bones):
            if bone.parent is None:
                continue
            if bone.parent not in name_to_index:
                raise KeyError(f"Bone {bone.name!r} references missing parent {bone.parent!r}")
            parent_idx = name_to_index[bone.parent]
            parent_indices[idx] = parent_idx
            children[parent_idx].append(idx)
            indegree[idx] += 1

        queue = [idx for idx, deg in enumerate(indegree.tolist()) if deg == 0]
        order: list[int] = []
        while queue:
            current = queue.pop(0)
            order.append(current)
            for child in children[current]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    queue.append(child)

        if len(order) != n:
            raise ValueError("Bone hierarchy is cyclic; Spine preview requires a DAG / tree")

        depths = np.zeros(n, dtype=np.int32)
        for idx in order:
            parent_idx = parent_indices[idx]
            if parent_idx >= 0:
                depths[idx] = depths[parent_idx] + 1
        levels: list[tuple[int, ...]] = []
        for depth in range(int(depths.max()) + 1):
            members = tuple(int(i) for i in np.nonzero(depths == depth)[0].tolist())
            if members:
                levels.append(members)
        return parent_indices, tuple(order), tuple(levels)

    def _build_local_matrix_tensor(
        self,
        *,
        bones: list[SpineBoneRecord],
        animation_block: dict[str, Any],
        frame_times: np.ndarray,
    ) -> np.ndarray:
        frame_count = int(frame_times.shape[0])
        bone_count = len(bones)
        tx = np.empty((frame_count, bone_count), dtype=np.float64)
        ty = np.empty((frame_count, bone_count), dtype=np.float64)
        rot = np.empty((frame_count, bone_count), dtype=np.float64)
        sx = np.empty((frame_count, bone_count), dtype=np.float64)
        sy = np.empty((frame_count, bone_count), dtype=np.float64)

        bones_block = animation_block.get("bones", {}) or {}
        for bone_idx, bone in enumerate(bones):
            timelines = dict(bones_block.get(bone.name, {}) or {})
            translate = timelines.get("translate", []) or []
            rotate = timelines.get("rotate", []) or []
            scale = timelines.get("scale", []) or []

            tx[:, bone_idx] = self._sample_xy_channel(
                keyframes=translate,
                field="x",
                default=bone.x,
                frame_times=frame_times,
                unwrap_angles=False,
            )
            ty[:, bone_idx] = self._sample_xy_channel(
                keyframes=translate,
                field="y",
                default=bone.y,
                frame_times=frame_times,
                unwrap_angles=False,
            )
            rot[:, bone_idx] = self._sample_scalar_channel(
                keyframes=rotate,
                field="angle",
                default=bone.rotation,
                frame_times=frame_times,
                unwrap_angles=True,
            )
            sx[:, bone_idx] = self._sample_xy_channel(
                keyframes=scale,
                field="x",
                default=bone.scale_x,
                frame_times=frame_times,
                unwrap_angles=False,
            )
            sy[:, bone_idx] = self._sample_xy_channel(
                keyframes=scale,
                field="y",
                default=bone.scale_y,
                frame_times=frame_times,
                unwrap_angles=False,
            )

        return self._compose_affine_matrices(tx, ty, rot, sx, sy)

    def _sample_scalar_channel(
        self,
        *,
        keyframes: list[dict[str, Any]],
        field: str,
        default: float,
        frame_times: np.ndarray,
        unwrap_angles: bool,
    ) -> np.ndarray:
        if not keyframes:
            return np.full(frame_times.shape, float(default), dtype=np.float64)
        times = np.asarray([float(item.get("time", 0.0) or 0.0) for item in keyframes], dtype=np.float64)
        values = np.asarray([float(item.get(field, default) or default) for item in keyframes], dtype=np.float64)
        order = np.argsort(times)
        times = times[order]
        values = values[order]
        if unwrap_angles:
            values = np.degrees(np.unwrap(np.radians(values)))
        return np.interp(frame_times, times, values, left=values[0], right=values[-1])

    def _sample_xy_channel(
        self,
        *,
        keyframes: list[dict[str, Any]],
        field: str,
        default: float,
        frame_times: np.ndarray,
        unwrap_angles: bool,
    ) -> np.ndarray:
        return self._sample_scalar_channel(
            keyframes=keyframes,
            field=field,
            default=default,
            frame_times=frame_times,
            unwrap_angles=unwrap_angles,
        )

    def _compose_affine_matrices(
        self,
        tx: np.ndarray,
        ty: np.ndarray,
        rotation_deg: np.ndarray,
        scale_x: np.ndarray,
        scale_y: np.ndarray,
    ) -> np.ndarray:
        radians = np.radians(rotation_deg)
        cos_v = np.cos(radians)
        sin_v = np.sin(radians)
        matrices = np.zeros(tx.shape + (3, 3), dtype=np.float64)
        matrices[..., 0, 0] = cos_v * scale_x
        matrices[..., 0, 1] = -sin_v * scale_y
        matrices[..., 1, 0] = sin_v * scale_x
        matrices[..., 1, 1] = cos_v * scale_y
        matrices[..., 0, 2] = tx
        matrices[..., 1, 2] = ty
        matrices[..., 2, 2] = 1.0
        return matrices

    def _propagate_world_matrices(
        self,
        *,
        local_matrices: np.ndarray,
        parent_indices: np.ndarray,
        depth_levels: tuple[tuple[int, ...], ...],
    ) -> np.ndarray:
        world = np.zeros_like(local_matrices)
        for level in depth_levels:
            level_idx = np.asarray(level, dtype=np.int64)
            parent_idx = parent_indices[level_idx]
            roots = parent_idx < 0
            if np.any(roots):
                root_level_idx = level_idx[roots]
                world[:, root_level_idx, :, :] = local_matrices[:, root_level_idx, :, :]
            if np.any(~roots):
                bone_idx = level_idx[~roots]
                parent_level_idx = parent_idx[~roots]
                world[:, bone_idx, :, :] = np.matmul(
                    world[:, parent_level_idx, :, :],
                    local_matrices[:, bone_idx, :, :],
                )
        return world

    def _extract_world_points(
        self,
        world_matrices: np.ndarray,
        lengths: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        origins = world_matrices[..., :2, 2]
        tip_local = np.stack(
            [lengths, np.zeros_like(lengths), np.ones_like(lengths)],
            axis=-1,
        )
        tips = np.matmul(world_matrices, tip_local[None, :, :, None]).squeeze(-1)[..., :2]
        return origins, tips

    def _compute_bounds(self, origins: np.ndarray, tips: np.ndarray) -> np.ndarray:
        points = np.concatenate([origins, tips], axis=1)
        min_xy = points.reshape(-1, 2).min(axis=0)
        max_xy = points.reshape(-1, 2).max(axis=0)
        return np.concatenate([min_xy, max_xy], axis=0)


class HeadlessSpineRenderer:
    """Render a ``SpinePreviewClip`` to MP4 / GIF without any GUI dependency."""

    def __init__(
        self,
        *,
        canvas_size: tuple[int, int] = (512, 512),
        margin: int = 32,
        background_color: tuple[int, int, int] = (8, 8, 8),
        bone_color: tuple[int, int, int] = (0, 215, 255),
        joint_color: tuple[int, int, int] = (255, 255, 255),
        root_color: tuple[int, int, int] = (80, 255, 80),
        bone_thickness: int = 3,
        joint_radius: int = 4,
    ) -> None:
        self.canvas_size = (int(canvas_size[0]), int(canvas_size[1]))
        self.margin = int(margin)
        self.background_color = tuple(int(v) for v in background_color)
        self.bone_color = tuple(int(v) for v in bone_color)
        self.joint_color = tuple(int(v) for v in joint_color)
        self.root_color = tuple(int(v) for v in root_color)
        self.bone_thickness = int(bone_thickness)
        self.joint_radius = int(joint_radius)

    def render(
        self,
        clip: SpinePreviewClip,
        *,
        output_mp4_path: str | Path,
        output_gif_path: str | Path | None = None,
        diagnostics_path: str | Path | None = None,
    ) -> SpinePreviewRenderResult:
        start = time.perf_counter()
        mp4_path = Path(output_mp4_path)
        mp4_path.parent.mkdir(parents=True, exist_ok=True)
        gif_path = Path(output_gif_path) if output_gif_path is not None else mp4_path.with_suffix(".gif")
        diagnostics = Path(diagnostics_path) if diagnostics_path is not None else mp4_path.with_suffix(".diagnostics.json")
        gif_path.parent.mkdir(parents=True, exist_ok=True)
        diagnostics.parent.mkdir(parents=True, exist_ok=True)

        screen_origins, screen_tips, projection_meta = self._project_to_screen(
            clip.world_origins,
            clip.world_tips,
        )
        frames = self._draw_frames(clip, screen_origins, screen_tips)
        self._write_mp4(mp4_path, frames, fps=clip.fps)
        self._write_gif(gif_path, frames, fps=clip.fps)

        render_time_ms = (time.perf_counter() - start) * 1000.0
        payload = {
            "animation_name": clip.animation_name,
            "frame_count": clip.frame_count,
            "bone_count": clip.bone_count,
            "fps": clip.fps,
            "canvas_size": list(self.canvas_size),
            "bounds_xy": [float(v) for v in clip.bounds_xy.tolist()],
            "projection": projection_meta,
            "source_path": clip.source_path,
            "render_time_ms": render_time_ms,
        }
        diagnostics.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return SpinePreviewRenderResult(
            mp4_path=str(mp4_path),
            gif_path=str(gif_path),
            diagnostics_path=str(diagnostics),
            frame_count=clip.frame_count,
            bone_count=clip.bone_count,
            fps=clip.fps,
            render_time_ms=render_time_ms,
            canvas_size=self.canvas_size,
            bounds_xy=[float(v) for v in clip.bounds_xy.tolist()],
        )

    def _project_to_screen(
        self,
        origins: np.ndarray,
        tips: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray, dict[str, float]]:
        width, height = self.canvas_size
        all_points = np.concatenate([origins, tips], axis=1)
        flattened = all_points.reshape(-1, 2)
        min_xy = flattened.min(axis=0)
        max_xy = flattened.max(axis=0)
        center_xy = (min_xy + max_xy) * 0.5
        extent = np.maximum(max_xy - min_xy, 1e-6)
        drawable_w = max(width - 2 * self.margin, 1)
        drawable_h = max(height - 2 * self.margin, 1)
        scale = min(drawable_w / extent[0], drawable_h / extent[1])
        normalized = (all_points - center_xy[None, None, :]) * scale
        screen = np.empty_like(normalized)
        screen[..., 0] = normalized[..., 0] + width * 0.5
        screen[..., 1] = (height - 1) - (normalized[..., 1] + height * 0.5)
        screen = np.rint(screen).astype(np.int32)
        bone_count = origins.shape[1]
        return (
            screen[:, :bone_count, :],
            screen[:, bone_count:, :],
            {
                "scale": float(scale),
                "center_x": float(center_xy[0]),
                "center_y": float(center_xy[1]),
                "min_x": float(min_xy[0]),
                "min_y": float(min_xy[1]),
                "max_x": float(max_xy[0]),
                "max_y": float(max_xy[1]),
            },
        )

    def _draw_frames(
        self,
        clip: SpinePreviewClip,
        screen_origins: np.ndarray,
        screen_tips: np.ndarray,
    ) -> list[np.ndarray]:
        import cv2  # SESSION-145: lazy import — heavy C++ extension

        width, height = self.canvas_size
        frames: list[np.ndarray] = []
        for frame_idx in range(clip.frame_count):
            canvas = np.zeros((height, width, 3), dtype=np.uint8)
            canvas[:, :] = self.background_color
            for bone_idx in range(clip.bone_count):
                start_xy = tuple(int(v) for v in screen_origins[frame_idx, bone_idx])
                end_xy = tuple(int(v) for v in screen_tips[frame_idx, bone_idx])
                if start_xy != end_xy:
                    cv2.line(canvas, start_xy, end_xy, self.bone_color, self.bone_thickness, lineType=cv2.LINE_AA)
                color = self.root_color if clip.parent_indices[bone_idx] < 0 else self.joint_color
                cv2.circle(canvas, start_xy, self.joint_radius, color, thickness=-1, lineType=cv2.LINE_AA)
                if clip.bone_lengths[bone_idx] > 1e-9:
                    cv2.circle(canvas, end_xy, max(1, self.joint_radius - 1), self.joint_color, thickness=-1, lineType=cv2.LINE_AA)
            frames.append(canvas)
        return frames

    def _write_mp4(self, output_path: Path, frames: list[np.ndarray], *, fps: float) -> None:
        import cv2  # SESSION-145: lazy import — heavy C++ extension

        if not frames:
            raise ValueError("No frames to write")
        height, width = frames[0].shape[:2]
        writer = cv2.VideoWriter(
            str(output_path),
            cv2.VideoWriter_fourcc(*"mp4v"),
            float(fps),
            (width, height),
            True,
        )
        if not writer.isOpened():
            raise RuntimeError(f"Failed to open VideoWriter for {output_path}")
        try:
            for frame in frames:
                writer.write(frame)
        finally:
            writer.release()

    def _write_gif(self, output_path: Path, frames: list[np.ndarray], *, fps: float) -> None:
        import cv2  # SESSION-145: lazy import — heavy C++ extension
        from PIL import Image  # SESSION-145: lazy import — keep top-level clean

        if not frames:
            raise ValueError("No frames to write")
        duration_ms = max(int(round(1000.0 / max(float(fps), 1.0))), 1)
        pil_frames = [Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)) for frame in frames]
        pil_frames[0].save(
            output_path,
            save_all=True,
            append_images=pil_frames[1:],
            duration=duration_ms,
            loop=0,
            optimize=False,
        )


def create_demo_spine_json(
    output_path: str | Path,
    *,
    name: str = "session125_demo",
    fps: float = 30.0,
    frame_count: int = 60,
) -> Path:
    """Write a deterministic synthetic Spine JSON clip for CI and tests."""
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    t = np.arange(frame_count, dtype=np.float64) / max(float(fps), 1.0)
    phase = np.linspace(0.0, 2.0 * math.pi, frame_count, endpoint=False)

    def rotate_frames(amplitude: float, offset: float = 0.0) -> list[dict[str, float]]:
        values = amplitude * np.sin(phase + offset)
        return [
            {"time": round(float(tt), 4), "angle": round(float(vv), 3)}
            for tt, vv in zip(t.tolist(), values.tolist())
        ]

    def translate_frames(x_values: np.ndarray, y_values: np.ndarray) -> list[dict[str, float]]:
        return [
            {
                "time": round(float(tt), 4),
                "x": round(float(xx), 3),
                "y": round(float(yy), 3),
            }
            for tt, xx, yy in zip(t.tolist(), x_values.tolist(), y_values.tolist())
        ]

    root_x = 0.8 * np.linspace(0.0, 1.0, frame_count)
    root_y = 0.12 * np.sin(phase)
    data = {
        "skeleton": {
            "hash": "",
            "spine": "4.2",
            "x": 0,
            "y": 0,
            "width": 256,
            "height": 256,
            "fps": fps,
            "images": "",
            "audio": "",
        },
        "bones": [
            {"name": "root", "x": 0.0, "y": 0.0},
            {"name": "hip", "parent": "root", "x": 0.0, "y": 0.0, "length": 0.18},
            {"name": "spine", "parent": "hip", "x": 0.0, "y": 0.22, "length": 0.22},
            {"name": "head", "parent": "spine", "x": 0.0, "y": 0.20, "length": 0.14},
            {"name": "l_arm", "parent": "spine", "x": -0.12, "y": 0.12, "length": 0.18},
            {"name": "r_arm", "parent": "spine", "x": 0.12, "y": 0.12, "length": 0.18},
        ],
        "slots": [
            {"name": "slot_root", "bone": "root", "attachment": "root"},
            {"name": "slot_hip", "bone": "hip", "attachment": "hip"},
            {"name": "slot_spine", "bone": "spine", "attachment": "spine"},
            {"name": "slot_head", "bone": "head", "attachment": "head"},
            {"name": "slot_l_arm", "bone": "l_arm", "attachment": "l_arm"},
            {"name": "slot_r_arm", "bone": "r_arm", "attachment": "r_arm"},
        ],
        "animations": {
            name: {
                "bones": {
                    "root": {
                        "translate": translate_frames(root_x, root_y),
                    },
                    "hip": {
                        "rotate": rotate_frames(8.0),
                    },
                    "spine": {
                        "rotate": rotate_frames(14.0, offset=0.2),
                    },
                    "head": {
                        "rotate": rotate_frames(10.0, offset=0.4),
                    },
                    "l_arm": {
                        "rotate": rotate_frames(26.0, offset=math.pi * 0.5),
                    },
                    "r_arm": {
                        "rotate": rotate_frames(26.0, offset=-math.pi * 0.5),
                    },
                }
            }
        },
    }
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output


__all__ = [
    "HeadlessSpineRenderer",
    "SpineBoneRecord",
    "SpineJSONTensorSolver",
    "SpinePreviewClip",
    "SpinePreviewRenderResult",
    "create_demo_spine_json",
]
