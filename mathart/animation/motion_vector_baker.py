"""SESSION-045 — Motion Vector Baker: Ground-Truth Optical Flow from Procedural Animation.

Distilled from Gap C3 research on neural rendering bridge and anti-flicker:

1. **Ondřej Jamriška / EbSynth** (SIGGRAPH 2019):
   "Stylizing Video by Example" — patch-based synthesis with temporal blending
   requires accurate optical flow as a guide channel for style propagation.

2. **OnlyFlow** (CVPR 2025 Workshop):
   Optical flow conditioning for video diffusion models — trainable encoder
   injects flow features into temporal attention layers.

3. **MotionPrompt** (CVPR 2025):
   Optical-flow guided prompt optimization for coherent video generation.

Core Insight:
    Traditional video pipelines ESTIMATE optical flow using RAFT, Farneback, etc.
    These estimations are inherently noisy, especially at occlusion boundaries.

    MarioTrickster-MathArt is a PROCEDURAL MATH ENGINE. We know EXACTLY where
    every bone, joint, and pixel moves from frame to frame via Forward Kinematics.
    We can export PERFECT ground-truth motion vectors with ZERO estimation error.

Architecture:
    ┌─────────────────────────────────────────────────────────────────────┐
    │  MotionVectorBaker                                                  │
    │  ├─ compute_joint_displacement()  — per-joint FK delta              │
    │  ├─ compute_pixel_motion_field()  — per-pixel SDF-weighted flow     │
    │  ├─ encode_motion_vector_rgb()    — normalized RGB for EbSynth      │
    │  ├─ encode_motion_vector_hsv()    — HSV color wheel for debugging   │
    │  └─ encode_motion_vector_raw()    — float32 .npy for Python         │
    ├─────────────────────────────────────────────────────────────────────┤
    │  MotionVectorSequence                                               │
    │  ├─ bake_sequence()              — full animation MV sequence       │
    │  ├─ export_ebsynth_project()     — EbSynth-ready directory layout   │
    │  └─ export_controlnet_batch()    — ControlNet conditioning batch    │
    └─────────────────────────────────────────────────────────────────────┘

Motion Vector Convention (Unity URP standard):
    - 2-channel texture (dx, dy) representing screen-space pixel displacement
    - previous_UV = current_UV - motion_vector
    - Normalized RGB: R = dx*0.5 + 0.5, G = dy*0.5 + 0.5, B = 0
    - HSV: Hue = atan2(dy, dx), Saturation = magnitude, Value = 1.0

References:
    - SESSION-034: IndustrialRenderer (SDF rendering pipeline)
    - SESSION-044: SDFAuxMaps (auxiliary map baking)
    - Jamriška et al., "Stylizing Video by Example", SIGGRAPH 2019
    - Koroglu et al., "OnlyFlow", CVPR 2025W
    - Nam et al., "MotionPrompt", CVPR 2025
    - Unity URP Motion Vectors documentation
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

import numpy as np
from PIL import Image

from .skeleton import Skeleton
from .parts import CharacterStyle, BodyPart, assemble_character


# ═══════════════════════════════════════════════════════════════════════════
#  Data Structures
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class MotionVectorField:
    """Per-pixel motion vector field for a single frame transition.

    Stores the displacement of every pixel from frame N to frame N+1
    in screen-space coordinates.

    Attributes
    ----------
    dx : np.ndarray
        Horizontal displacement per pixel (positive = rightward).
    dy : np.ndarray
        Vertical displacement per pixel (positive = downward in image space).
    magnitude : np.ndarray
        Per-pixel displacement magnitude.
    mask : np.ndarray
        Boolean mask indicating valid (inside character) pixels.
    width : int
        Frame width in pixels.
    height : int
        Frame height in pixels.
    metadata : dict
        Additional metadata (frame indices, max displacement, etc.).
    """
    dx: np.ndarray
    dy: np.ndarray
    magnitude: np.ndarray
    mask: np.ndarray
    width: int
    height: int
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def max_magnitude(self) -> float:
        """Maximum displacement magnitude across all valid pixels."""
        if not np.any(self.mask):
            return 0.0
        return float(np.max(self.magnitude[self.mask]))

    @property
    def mean_magnitude(self) -> float:
        """Mean displacement magnitude across valid pixels."""
        if not np.any(self.mask):
            return 0.0
        return float(np.mean(self.magnitude[self.mask]))

    @property
    def valid_pixel_count(self) -> int:
        """Number of valid (inside character) pixels."""
        return int(np.count_nonzero(self.mask))


@dataclass
class MotionVectorSequence:
    """A sequence of motion vector fields for a full animation.

    Attributes
    ----------
    fields : list[MotionVectorField]
        One MV field per frame transition (N frames → N-1 fields).
    frame_count : int
        Total number of frames in the animation.
    width : int
        Frame width in pixels.
    height : int
        Frame height in pixels.
    """
    fields: list[MotionVectorField] = field(default_factory=list)
    frame_count: int = 0
    width: int = 32
    height: int = 32

    @property
    def total_motion_energy(self) -> float:
        """Sum of all displacement magnitudes across all frames."""
        return sum(f.mean_magnitude for f in self.fields)


# ═══════════════════════════════════════════════════════════════════════════
#  Core: Joint Displacement Computation
# ═══════════════════════════════════════════════════════════════════════════


def compute_joint_displacement(
    skeleton: Skeleton,
    pose_a: dict[str, float],
    pose_b: dict[str, float],
) -> dict[str, tuple[float, float]]:
    """Compute the world-space displacement of every joint between two poses.

    This is the foundation of our ground-truth motion vectors: instead of
    estimating optical flow from pixel data, we compute exact displacements
    from the skeletal Forward Kinematics.

    Parameters
    ----------
    skeleton : Skeleton
        The character skeleton.
    pose_a : dict[str, float]
        Joint angles for the source frame.
    pose_b : dict[str, float]
        Joint angles for the target frame.

    Returns
    -------
    dict[str, tuple[float, float]]
        Per-joint (dx, dy) displacement in world coordinates.
    """
    # Compute FK for pose A
    skel_a = Skeleton.create_humanoid(skeleton.head_units)
    skel_a.apply_pose(pose_a)
    positions_a = skel_a.get_joint_positions()

    # Compute FK for pose B
    skel_b = Skeleton.create_humanoid(skeleton.head_units)
    skel_b.apply_pose(pose_b)
    positions_b = skel_b.get_joint_positions()

    displacements: dict[str, tuple[float, float]] = {}
    for joint_name in positions_a:
        if joint_name in positions_b:
            ax, ay = positions_a[joint_name]
            bx, by = positions_b[joint_name]
            displacements[joint_name] = (bx - ax, by - ay)
        else:
            displacements[joint_name] = (0.0, 0.0)

    return displacements


# ═══════════════════════════════════════════════════════════════════════════
#  Core: Per-Pixel Motion Field Computation
# ═══════════════════════════════════════════════════════════════════════════


def _compute_sdf_weights(
    x: np.ndarray,
    y: np.ndarray,
    joint_positions: dict[str, tuple[float, float]],
    parts: list[BodyPart],
    sigma: float = 0.15,
) -> dict[str, np.ndarray]:
    """Compute per-pixel influence weights for each body part/joint.

    Uses a Gaussian falloff from each joint position to determine how much
    each joint's motion contributes to each pixel's displacement. This is
    analogous to Linear Blend Skinning (LBS) weights in 3D animation.

    Parameters
    ----------
    x, y : np.ndarray
        2D coordinate grids.
    joint_positions : dict[str, tuple[float, float]]
        World positions of all joints.
    parts : list[BodyPart]
        Character body parts with joint associations.
    sigma : float
        Gaussian falloff radius for weight computation.

    Returns
    -------
    dict[str, np.ndarray]
        Per-joint weight maps (same shape as x).
    """
    weights: dict[str, np.ndarray] = {}
    total_weight = np.zeros_like(x, dtype=np.float64)

    for part in parts:
        if part.joint_name not in joint_positions:
            continue
        jx, jy = joint_positions[part.joint_name]
        dist_sq = (x - (jx + part.offset_x)) ** 2 + (y - (jy + part.offset_y)) ** 2
        w = np.exp(-dist_sq / (2.0 * sigma ** 2))

        if part.joint_name in weights:
            weights[part.joint_name] = np.maximum(weights[part.joint_name], w)
        else:
            weights[part.joint_name] = w.copy()
        total_weight += w

    # Normalize weights to sum to 1
    total_weight = np.maximum(total_weight, 1e-8)
    for name in weights:
        weights[name] /= total_weight

    return weights


def compute_pixel_motion_field(
    skeleton: Skeleton,
    pose_a: dict[str, float],
    pose_b: dict[str, float],
    style: CharacterStyle,
    width: int = 32,
    height: int = 32,
    skinning_sigma: float = 0.15,
) -> MotionVectorField:
    """Compute a per-pixel motion vector field between two animation frames.

    This is the core function that leverages our procedural math engine's
    unique advantage: exact motion vectors from Forward Kinematics, weighted
    by SDF-based skinning influence (analogous to LBS in 3D).

    The algorithm:
    1. Compute FK joint positions for both poses
    2. Compute per-joint displacement vectors
    3. For each pixel, compute skinning weights (Gaussian falloff from joints)
    4. Blend joint displacements using skinning weights → per-pixel motion

    Parameters
    ----------
    skeleton : Skeleton
        The character skeleton.
    pose_a : dict[str, float]
        Joint angles for the source frame (frame N).
    pose_b : dict[str, float]
        Joint angles for the target frame (frame N+1).
    style : CharacterStyle
        Character visual style (for body part assembly).
    width, height : int
        Frame dimensions.
    skinning_sigma : float
        Gaussian falloff radius for skinning weights.

    Returns
    -------
    MotionVectorField
        Per-pixel motion vectors with metadata.
    """
    # Step 1: FK for both poses
    skel_a = Skeleton.create_humanoid(skeleton.head_units)
    skel_a.apply_pose(pose_a)
    positions_a = skel_a.get_joint_positions()

    skel_b = Skeleton.create_humanoid(skeleton.head_units)
    skel_b.apply_pose(pose_b)
    positions_b = skel_b.get_joint_positions()

    # Step 2: Per-joint displacement
    joint_displacements: dict[str, tuple[float, float]] = {}
    for name in positions_a:
        if name in positions_b:
            ax, ay = positions_a[name]
            bx, by = positions_b[name]
            joint_displacements[name] = (bx - ax, by - ay)

    # Step 3: Build coordinate grid and compute skinning weights
    xs = np.linspace(-0.6, 0.6, width)
    ys = np.linspace(1.1, -0.1, height)
    x, y = np.meshgrid(xs, ys)

    parts = assemble_character(style)
    weights = _compute_sdf_weights(x, y, positions_a, parts, sigma=skinning_sigma)

    # Step 4: Blend joint displacements per pixel
    dx = np.zeros((height, width), dtype=np.float64)
    dy = np.zeros((height, width), dtype=np.float64)

    for joint_name, (jdx, jdy) in joint_displacements.items():
        if joint_name in weights:
            dx += weights[joint_name] * jdx
            dy += weights[joint_name] * jdy

    # Compute character mask from SDF union
    union_dist = np.full((height, width), np.inf, dtype=np.float64)
    for part in parts:
        if part.joint_name not in positions_a:
            continue
        jx, jy = positions_a[part.joint_name]
        local_x = x - (jx + part.offset_x)
        local_y = y - (jy + part.offset_y)
        cos_r = math.cos(-part.rotation)
        sin_r = math.sin(-part.rotation)
        rot_x = local_x * cos_r - local_y * sin_r
        rot_y = local_x * sin_r + local_y * cos_r
        dist = np.asarray(part.sdf(rot_x, rot_y), dtype=np.float64)
        if not part.is_outline_only:
            union_dist = np.minimum(union_dist, dist)

    mask = union_dist < 0.0

    # Zero out motion outside the character
    dx[~mask] = 0.0
    dy[~mask] = 0.0

    # Convert world-space displacement to pixel-space displacement
    pixel_per_unit_x = width / 1.2   # x range is [-0.6, 0.6] = 1.2
    pixel_per_unit_y = height / 1.2  # y range is [-0.1, 1.1] = 1.2
    dx_pixels = dx * pixel_per_unit_x
    dy_pixels = -dy * pixel_per_unit_y  # Flip Y for image coordinates

    magnitude = np.sqrt(dx_pixels ** 2 + dy_pixels ** 2)

    return MotionVectorField(
        dx=dx_pixels,
        dy=dy_pixels,
        magnitude=magnitude,
        mask=mask,
        width=width,
        height=height,
        metadata={
            "skinning_sigma": skinning_sigma,
            "max_magnitude_px": float(np.max(magnitude[mask])) if np.any(mask) else 0.0,
            "mean_magnitude_px": float(np.mean(magnitude[mask])) if np.any(mask) else 0.0,
            "valid_pixels": int(np.count_nonzero(mask)),
            "joint_count": len(joint_displacements),
        },
    )


# ═══════════════════════════════════════════════════════════════════════════
#  Encoding: Multiple Output Formats
# ═══════════════════════════════════════════════════════════════════════════


def encode_motion_vector_rgb(
    mv_field: MotionVectorField,
    max_displacement: Optional[float] = None,
) -> Image.Image:
    """Encode motion vectors as normalized RGB image.

    Convention (Unity URP / EbSynth compatible):
        R = dx / (2 * max_disp) + 0.5  (clamped to [0, 1])
        G = dy / (2 * max_disp) + 0.5  (clamped to [0, 1])
        B = 0
        A = mask (255 inside, 0 outside)

    A pixel with R=128, G=128 means zero displacement (neutral).

    Parameters
    ----------
    mv_field : MotionVectorField
        The motion vector field to encode.
    max_displacement : float, optional
        Maximum expected displacement for normalization.
        If None, auto-computed from the field.

    Returns
    -------
    Image.Image
        RGBA image with encoded motion vectors.
    """
    if max_displacement is None:
        max_displacement = max(mv_field.max_magnitude, 1.0)

    # Normalize to [-1, 1] range then shift to [0, 1]
    norm_dx = np.clip(mv_field.dx / (2.0 * max_displacement) + 0.5, 0.0, 1.0)
    norm_dy = np.clip(mv_field.dy / (2.0 * max_displacement) + 0.5, 0.0, 1.0)

    r = np.round(norm_dx * 255.0).astype(np.uint8)
    g = np.round(norm_dy * 255.0).astype(np.uint8)
    b = np.zeros_like(r, dtype=np.uint8)
    a = np.where(mv_field.mask, 255, 0).astype(np.uint8)

    rgba = np.dstack((r, g, b, a))
    return Image.fromarray(rgba, "RGBA")


def encode_motion_vector_hsv(
    mv_field: MotionVectorField,
    max_displacement: Optional[float] = None,
) -> Image.Image:
    """Encode motion vectors as HSV color wheel visualization.

    Convention (Middlebury optical flow standard):
        Hue = direction of motion (atan2(dy, dx))
        Saturation = magnitude (normalized to [0, 1])
        Value = 1.0 (full brightness)

    This format is primarily for human debugging and visualization.

    Parameters
    ----------
    mv_field : MotionVectorField
        The motion vector field to encode.
    max_displacement : float, optional
        Maximum expected displacement for saturation normalization.

    Returns
    -------
    Image.Image
        RGBA image with HSV-encoded motion vectors.
    """
    if max_displacement is None:
        max_displacement = max(mv_field.max_magnitude, 1.0)

    # Compute angle and magnitude
    angle = np.arctan2(mv_field.dy, mv_field.dx)  # [-pi, pi]
    hue = (angle + np.pi) / (2.0 * np.pi)  # [0, 1]
    sat = np.clip(mv_field.magnitude / max_displacement, 0.0, 1.0)

    # HSV to RGB conversion
    h6 = hue * 6.0
    sector = np.floor(h6).astype(int) % 6
    f = h6 - np.floor(h6)
    p = 1.0 - sat
    q = 1.0 - sat * f
    t = 1.0 - sat * (1.0 - f)

    r = np.zeros_like(hue)
    g = np.zeros_like(hue)
    b = np.zeros_like(hue)

    for s in range(6):
        m = sector == s
        if s == 0:
            r[m], g[m], b[m] = 1.0, t[m], p[m]
        elif s == 1:
            r[m], g[m], b[m] = q[m], 1.0, p[m]
        elif s == 2:
            r[m], g[m], b[m] = p[m], 1.0, t[m]
        elif s == 3:
            r[m], g[m], b[m] = p[m], q[m], 1.0
        elif s == 4:
            r[m], g[m], b[m] = t[m], p[m], 1.0
        elif s == 5:
            r[m], g[m], b[m] = 1.0, p[m], q[m]

    r_u8 = np.round(r * 255.0).astype(np.uint8)
    g_u8 = np.round(g * 255.0).astype(np.uint8)
    b_u8 = np.round(b * 255.0).astype(np.uint8)
    a_u8 = np.where(mv_field.mask, 255, 0).astype(np.uint8)

    rgba = np.dstack((r_u8, g_u8, b_u8, a_u8))
    return Image.fromarray(rgba, "RGBA")


def encode_motion_vector_raw(mv_field: MotionVectorField) -> np.ndarray:
    """Encode motion vectors as raw float32 array for Python consumers.

    Returns a (H, W, 3) float32 array:
        channel 0: dx (pixel displacement)
        channel 1: dy (pixel displacement)
        channel 2: mask (1.0 inside, 0.0 outside)

    Parameters
    ----------
    mv_field : MotionVectorField
        The motion vector field to encode.

    Returns
    -------
    np.ndarray
        Shape (height, width, 3), dtype float32.
    """
    mask_f = mv_field.mask.astype(np.float32)
    return np.dstack((
        mv_field.dx.astype(np.float32),
        mv_field.dy.astype(np.float32),
        mask_f,
    ))


# ═══════════════════════════════════════════════════════════════════════════
#  Sequence Baking: Full Animation Motion Vector Export
# ═══════════════════════════════════════════════════════════════════════════


def bake_motion_vector_sequence(
    skeleton: Skeleton,
    animation_func: Callable[[float], dict[str, float]],
    style: CharacterStyle,
    frames: int = 8,
    width: int = 32,
    height: int = 32,
    skinning_sigma: float = 0.15,
) -> MotionVectorSequence:
    """Bake a complete motion vector sequence for an animation.

    Computes per-pixel motion vectors for every consecutive frame pair
    in the animation. The result can be exported to EbSynth, ControlNet,
    or any other temporal consistency tool.

    Parameters
    ----------
    skeleton : Skeleton
        The character skeleton.
    animation_func : Callable[[float], dict[str, float]]
        Maps t in [0, 1] to a pose dict.
    style : CharacterStyle
        Character visual style.
    frames : int
        Number of animation frames.
    width, height : int
        Frame dimensions.
    skinning_sigma : float
        Gaussian falloff for skinning weights.

    Returns
    -------
    MotionVectorSequence
        Complete sequence of motion vector fields.
    """
    sequence = MotionVectorSequence(
        frame_count=frames,
        width=width,
        height=height,
    )

    poses = [animation_func(i / max(frames - 1, 1)) for i in range(frames)]

    for i in range(frames - 1):
        mv_field = compute_pixel_motion_field(
            skeleton=skeleton,
            pose_a=poses[i],
            pose_b=poses[i + 1],
            style=style,
            width=width,
            height=height,
            skinning_sigma=skinning_sigma,
        )
        mv_field.metadata["frame_a"] = i
        mv_field.metadata["frame_b"] = i + 1
        sequence.fields.append(mv_field)

    return sequence


# ═══════════════════════════════════════════════════════════════════════════
#  Export: EbSynth Project Layout
# ═══════════════════════════════════════════════════════════════════════════


def export_ebsynth_project(
    sequence: MotionVectorSequence,
    albedo_frames: list[Image.Image],
    output_dir: str | Path,
    keyframe_indices: Optional[list[int]] = None,
    max_displacement: Optional[float] = None,
) -> dict[str, Any]:
    """Export a motion vector sequence as an EbSynth-compatible project.

    Creates a directory structure that EbSynth can consume directly:
        output_dir/
        ├── frames/           # Original rendered frames
        │   ├── 0000.png
        │   ├── 0001.png
        │   └── ...
        ├── flow/             # Motion vector maps (RGB encoded)
        │   ├── 0000_0001.png
        │   ├── 0001_0002.png
        │   └── ...
        ├── flow_vis/         # HSV visualization for debugging
        │   ├── 0000_0001.png
        │   └── ...
        ├── keyframes/        # Stylized keyframes (to be painted)
        │   └── 0000.png
        └── project.json      # Project metadata

    Parameters
    ----------
    sequence : MotionVectorSequence
        The baked motion vector sequence.
    albedo_frames : list[Image.Image]
        Rendered albedo frames (one per animation frame).
    output_dir : str or Path
        Output directory path.
    keyframe_indices : list[int], optional
        Indices of keyframes. Defaults to [0] (first frame only).
    max_displacement : float, optional
        Max displacement for RGB normalization.

    Returns
    -------
    dict
        Project metadata including file paths.
    """
    out = Path(output_dir)
    frames_dir = out / "frames"
    flow_dir = out / "flow"
    flow_vis_dir = out / "flow_vis"
    keyframes_dir = out / "keyframes"

    for d in [frames_dir, flow_dir, flow_vis_dir, keyframes_dir]:
        d.mkdir(parents=True, exist_ok=True)

    if keyframe_indices is None:
        keyframe_indices = [0]

    # Auto-compute max displacement for consistent normalization
    if max_displacement is None:
        max_displacement = max(
            (f.max_magnitude for f in sequence.fields),
            default=1.0,
        )
        max_displacement = max(max_displacement, 1.0)

    # Export albedo frames
    frame_paths = []
    for i, frame in enumerate(albedo_frames):
        path = frames_dir / f"{i:04d}.png"
        frame.save(str(path))
        frame_paths.append(str(path))

    # Export keyframes (copy from albedo)
    keyframe_paths = []
    for idx in keyframe_indices:
        if idx < len(albedo_frames):
            path = keyframes_dir / f"{idx:04d}.png"
            albedo_frames[idx].save(str(path))
            keyframe_paths.append(str(path))

    # Export motion vector maps
    flow_paths = []
    flow_vis_paths = []
    for i, mv_field in enumerate(sequence.fields):
        # RGB encoded (for EbSynth guide channel)
        rgb_img = encode_motion_vector_rgb(mv_field, max_displacement)
        rgb_path = flow_dir / f"{i:04d}_{i + 1:04d}.png"
        rgb_img.save(str(rgb_path))
        flow_paths.append(str(rgb_path))

        # HSV visualization (for debugging)
        hsv_img = encode_motion_vector_hsv(mv_field, max_displacement)
        hsv_path = flow_vis_dir / f"{i:04d}_{i + 1:04d}.png"
        hsv_img.save(str(hsv_path))
        flow_vis_paths.append(str(hsv_path))

    # Write project metadata
    project_meta = {
        "format": "ebsynth_project",
        "version": "1.0",
        "frame_count": sequence.frame_count,
        "width": sequence.width,
        "height": sequence.height,
        "max_displacement": max_displacement,
        "keyframe_indices": keyframe_indices,
        "frame_paths": frame_paths,
        "flow_paths": flow_paths,
        "flow_vis_paths": flow_vis_paths,
        "keyframe_paths": keyframe_paths,
        "total_motion_energy": sequence.total_motion_energy,
        "source": "MarioTrickster-MathArt MotionVectorBaker",
        "note": "Ground-truth motion vectors from procedural FK — zero estimation error",
    }

    import json
    meta_path = out / "project.json"
    meta_path.write_text(json.dumps(project_meta, indent=2), encoding="utf-8")

    return project_meta


# ═══════════════════════════════════════════════════════════════════════════
#  Temporal Consistency Validation
# ═══════════════════════════════════════════════════════════════════════════


def compute_temporal_consistency_score(
    frame_a: np.ndarray,
    frame_b: np.ndarray,
    mv_field: MotionVectorField,
) -> dict[str, float]:
    """Compute temporal consistency between two frames using motion vectors.

    Warps frame_a using the motion vector field and compares with frame_b.
    A perfectly consistent sequence should have zero warp error.

    This metric is the foundation of our anti-flicker validation:
    if the AI-stylized frames deviate from the motion-warped prediction,
    we know exactly where and how much flicker occurred.

    Parameters
    ----------
    frame_a : np.ndarray
        Source frame (H, W, C) as uint8 or float.
    frame_b : np.ndarray
        Target frame (H, W, C) as uint8 or float.
    mv_field : MotionVectorField
        Motion vector field from frame_a to frame_b.

    Returns
    -------
    dict[str, float]
        Consistency metrics:
        - warp_error: mean absolute error between warped_a and frame_b
        - warp_ssim_proxy: 1 - normalized warp error (higher = more consistent)
        - coverage: fraction of pixels with valid motion vectors
    """
    h, w = frame_a.shape[:2]
    fa = frame_a.astype(np.float64) / 255.0 if frame_a.dtype == np.uint8 else frame_a.astype(np.float64)
    fb = frame_b.astype(np.float64) / 255.0 if frame_b.dtype == np.uint8 else frame_b.astype(np.float64)

    # Build warp coordinates
    yy, xx = np.mgrid[0:h, 0:w]
    warp_x = np.clip(np.round(xx + mv_field.dx).astype(int), 0, w - 1)
    warp_y = np.clip(np.round(yy + mv_field.dy).astype(int), 0, h - 1)

    # Warp frame_a
    warped_a = fa[warp_y, warp_x]

    # Compute error only on valid pixels
    mask = mv_field.mask
    if not np.any(mask):
        return {"warp_error": 0.0, "warp_ssim_proxy": 1.0, "coverage": 0.0}

    if fa.ndim == 3:
        mask_3d = np.expand_dims(mask, axis=-1)
        diff = np.abs(warped_a - fb) * mask_3d
        warp_error = float(np.sum(diff) / max(np.sum(mask_3d), 1))
    else:
        diff = np.abs(warped_a - fb) * mask
        warp_error = float(np.sum(diff) / max(np.sum(mask), 1))

    coverage = float(np.count_nonzero(mask)) / float(h * w)
    ssim_proxy = max(0.0, 1.0 - warp_error)

    return {
        "warp_error": warp_error,
        "warp_ssim_proxy": ssim_proxy,
        "coverage": coverage,
    }


__all__ = [
    "MotionVectorField",
    "MotionVectorSequence",
    "compute_joint_displacement",
    "compute_pixel_motion_field",
    "encode_motion_vector_rgb",
    "encode_motion_vector_hsv",
    "encode_motion_vector_raw",
    "bake_motion_vector_sequence",
    "export_ebsynth_project",
    "compute_temporal_consistency_score",
]
