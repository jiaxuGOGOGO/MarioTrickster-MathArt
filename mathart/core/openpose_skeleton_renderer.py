"""SESSION-193: OpenPose Skeleton Renderer — 18-Keypoint Pose Sequence Generator.

Industrial References:
  - OpenPose (Cao et al., 2019): Real-time multi-person 2D pose estimation.
    The COCO-18 keypoint format is the de-facto standard for ControlNet
    OpenPose conditioning.
  - ControlNet OpenPose (Zhang et al., 2023): Uses skeleton images (black
    background, coloured limb segments) as spatial conditioning for
    diffusion models.
  - SESSION-193 ControlNet Arbitration: When upstream physics degrades to
    a Dummy Cylinder mesh, OpenPose skeleton guidance at strength 1.0
    takes over motion control, while Depth/Normal are softened to 0.40–0.45
    to break geometric lock.

Architecture Discipline:
  This module is a **standalone renderer** — it does NOT modify the trunk
  pipeline, the preset manager, or the orchestrator. It exposes pure
  functions that convert 2D math skeleton coordinates into OpenPose-format
  images suitable for ControlNet conditioning.

  All rendering uses PIL/numpy — **ZERO cv2 dependency**.

Keypoint Layout (COCO-18):
  0: Nose, 1: Neck, 2: R-Shoulder, 3: R-Elbow, 4: R-Wrist,
  5: L-Shoulder, 6: L-Elbow, 7: L-Wrist, 8: R-Hip, 9: R-Knee,
  10: R-Ankle, 11: L-Hip, 12: L-Knee, 13: L-Ankle, 14: R-Eye,
  15: L-Eye, 16: R-Ear, 17: L-Ear

Hard Red Lines:
  - ZERO cv2 dependency — uses ONLY PIL + numpy.
  - NEVER touches proxy environment variables.
  - NEVER modifies SESSION-189 anchors.
  - NEVER modifies the anime_rhythmic_subsample or latent_healing logic.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Sequence

import numpy as np
from PIL import Image, ImageDraw

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
#  COCO-18 Keypoint Definitions
# ═══════════════════════════════════════════════════════════════════════════

COCO_18_KEYPOINT_NAMES: tuple[str, ...] = (
    "nose", "neck",
    "r_shoulder", "r_elbow", "r_wrist",
    "l_shoulder", "l_elbow", "l_wrist",
    "r_hip", "r_knee", "r_ankle",
    "l_hip", "l_knee", "l_ankle",
    "r_eye", "l_eye", "r_ear", "l_ear",
)

# Limb connections: pairs of keypoint indices to draw as bones.
COCO_18_LIMB_PAIRS: tuple[tuple[int, int], ...] = (
    (0, 1),    # nose -> neck
    (1, 2),    # neck -> r_shoulder
    (2, 3),    # r_shoulder -> r_elbow
    (3, 4),    # r_elbow -> r_wrist
    (1, 5),    # neck -> l_shoulder
    (5, 6),    # l_shoulder -> l_elbow
    (6, 7),    # l_elbow -> l_wrist
    (1, 8),    # neck -> r_hip
    (8, 9),    # r_hip -> r_knee
    (9, 10),   # r_knee -> r_ankle
    (1, 11),   # neck -> l_hip
    (11, 12),  # l_hip -> l_knee
    (12, 13),  # l_knee -> l_ankle
    (0, 14),   # nose -> r_eye
    (0, 15),   # nose -> l_eye
    (14, 16),  # r_eye -> r_ear
    (15, 17),  # l_eye -> l_ear
)

# Canonical OpenPose limb colours (RGB) — matches the official visualisation.
COCO_18_LIMB_COLORS: tuple[tuple[int, int, int], ...] = (
    (255, 0, 0),       # nose-neck: red
    (255, 85, 0),      # neck-r_shoulder: orange
    (255, 170, 0),     # r_shoulder-r_elbow: yellow-orange
    (255, 255, 0),     # r_elbow-r_wrist: yellow
    (170, 255, 0),     # neck-l_shoulder: yellow-green
    (85, 255, 0),      # l_shoulder-l_elbow: green
    (0, 255, 0),       # l_elbow-l_wrist: bright green
    (0, 255, 85),      # neck-r_hip: cyan-green
    (0, 255, 170),     # r_hip-r_knee: teal
    (0, 255, 255),     # r_knee-r_ankle: cyan
    (0, 170, 255),     # neck-l_hip: sky blue
    (0, 85, 255),      # l_hip-l_knee: blue
    (0, 0, 255),       # l_knee-l_ankle: deep blue
    (85, 0, 255),      # nose-r_eye: purple
    (170, 0, 255),     # nose-l_eye: violet
    (255, 0, 255),     # r_eye-r_ear: magenta
    (255, 0, 170),     # l_eye-l_ear: pink
)

# Keypoint circle colours (one per keypoint).
COCO_18_KEYPOINT_COLORS: tuple[tuple[int, int, int], ...] = (
    (255, 0, 0),       # 0: nose
    (255, 85, 0),      # 1: neck
    (255, 170, 0),     # 2: r_shoulder
    (255, 255, 0),     # 3: r_elbow
    (170, 255, 0),     # 4: r_wrist
    (85, 255, 0),      # 5: l_shoulder
    (0, 255, 0),       # 6: l_elbow
    (0, 255, 85),      # 7: l_wrist
    (0, 255, 170),     # 8: r_hip
    (0, 255, 255),     # 9: r_knee
    (0, 170, 255),     # 10: r_ankle
    (0, 85, 255),      # 11: l_hip
    (0, 0, 255),       # 12: l_knee
    (85, 0, 255),      # 13: l_ankle
    (170, 0, 255),     # 14: r_eye
    (255, 0, 255),     # 15: l_eye
    (255, 0, 170),     # 16: r_ear
    (255, 0, 85),      # 17: l_ear
)


# ═══════════════════════════════════════════════════════════════════════════
#  Skeleton-to-OpenPose Keypoint Mapping
# ═══════════════════════════════════════════════════════════════════════════

def skeleton_joints_to_coco18(
    joint_positions: np.ndarray,
    *,
    image_width: int,
    image_height: int,
) -> list[tuple[float, float, float]]:
    """Convert internal skeleton joint positions to COCO-18 keypoints.

    Parameters
    ----------
    joint_positions : np.ndarray
        Shape ``(J, 2)`` or ``(J, 3)`` array of joint (x, y[, confidence])
        positions in normalised [0, 1] coordinates.
    image_width, image_height : int
        Target image dimensions for pixel-space conversion.

    Returns
    -------
    list of (x_px, y_px, confidence)
        18 keypoints in pixel coordinates. Missing joints get confidence 0.
    """
    J = joint_positions.shape[0]
    # Build a default mapping: if we have fewer joints than 18, map what
    # we can and leave the rest at confidence=0.
    keypoints: list[tuple[float, float, float]] = []
    for i in range(18):
        if i < J:
            x = float(joint_positions[i, 0]) * image_width
            y = float(joint_positions[i, 1]) * image_height
            conf = float(joint_positions[i, 2]) if joint_positions.shape[1] > 2 else 1.0
            keypoints.append((x, y, conf))
        else:
            keypoints.append((0.0, 0.0, 0.0))
    return keypoints


# ═══════════════════════════════════════════════════════════════════════════
#  Render Single OpenPose Frame
# ═══════════════════════════════════════════════════════════════════════════

def render_openpose_frame(
    keypoints: list[tuple[float, float, float]],
    *,
    width: int = 512,
    height: int = 512,
    limb_thickness: int = 4,
    keypoint_radius: int = 4,
) -> Image.Image:
    """Render a single OpenPose skeleton frame (black background, coloured bones).

    Parameters
    ----------
    keypoints : list of (x, y, confidence)
        18 COCO keypoints in pixel coordinates.
    width, height : int
        Output image dimensions.
    limb_thickness : int
        Line width for bone segments.
    keypoint_radius : int
        Circle radius for keypoint dots.

    Returns
    -------
    PIL.Image.Image
        RGB image with black background and coloured skeleton overlay.
    """
    canvas = Image.new("RGB", (width, height), (0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    # Draw limbs
    for limb_idx, (kp_a, kp_b) in enumerate(COCO_18_LIMB_PAIRS):
        if kp_a >= len(keypoints) or kp_b >= len(keypoints):
            continue
        xa, ya, ca = keypoints[kp_a]
        xb, yb, cb = keypoints[kp_b]
        if ca < 0.1 or cb < 0.1:
            continue  # Skip invisible joints
        color = COCO_18_LIMB_COLORS[limb_idx % len(COCO_18_LIMB_COLORS)]
        draw.line([(xa, ya), (xb, yb)], fill=color, width=limb_thickness)

    # Draw keypoints
    for kp_idx, (x, y, c) in enumerate(keypoints):
        if c < 0.1:
            continue
        color = COCO_18_KEYPOINT_COLORS[kp_idx % len(COCO_18_KEYPOINT_COLORS)]
        r = keypoint_radius
        draw.ellipse([(x - r, y - r), (x + r, y + r)], fill=color)

    return canvas


# ═══════════════════════════════════════════════════════════════════════════
#  Render Full Pose Sequence from Math Skeleton
# ═══════════════════════════════════════════════════════════════════════════

def render_openpose_sequence(
    skeleton_frames: np.ndarray | list[np.ndarray],
    *,
    width: int = 512,
    height: int = 512,
    limb_thickness: int = 4,
    keypoint_radius: int = 4,
) -> list[Image.Image]:
    """Render a sequence of OpenPose skeleton frames from math bone data.

    Parameters
    ----------
    skeleton_frames : array-like
        Shape ``(N, J, 2)`` or ``(N, J, 3)`` — N frames, J joints,
        (x, y[, confidence]) in normalised [0, 1] coordinates.
    width, height : int
        Output image dimensions.

    Returns
    -------
    list of PIL.Image.Image
        N OpenPose skeleton images (black background, coloured bones).
    """
    if isinstance(skeleton_frames, np.ndarray):
        frames_list = [skeleton_frames[i] for i in range(skeleton_frames.shape[0])]
    else:
        frames_list = list(skeleton_frames)

    pose_images: list[Image.Image] = []
    for frame_joints in frames_list:
        arr = np.asarray(frame_joints)
        if arr.ndim == 1:
            # Flat array — reshape to (J, 2)
            arr = arr.reshape(-1, 2)
        kps = skeleton_joints_to_coco18(
            arr, image_width=width, image_height=height,
        )
        img = render_openpose_frame(
            kps,
            width=width,
            height=height,
            limb_thickness=limb_thickness,
            keypoint_radius=keypoint_radius,
        )
        pose_images.append(img)

    return pose_images


def render_openpose_from_2d_bones(
    bone_positions_sequence: list[list[tuple[float, float]]],
    *,
    width: int = 512,
    height: int = 512,
) -> list[Image.Image]:
    """Convenience wrapper: render OpenPose from raw 2D bone coordinate lists.

    Parameters
    ----------
    bone_positions_sequence : list of list of (x, y)
        Each inner list has up to 18 (x, y) tuples in normalised [0, 1] space.

    Returns
    -------
    list of PIL.Image.Image
    """
    frames = []
    for bone_positions in bone_positions_sequence:
        arr = np.array([(x, y, 1.0) for x, y in bone_positions], dtype=np.float64)
        frames.append(arr)
    return render_openpose_sequence(
        frames, width=width, height=height,
    )


# ═══════════════════════════════════════════════════════════════════════════
#  ControlNet Arbitration: Strength Override for Dummy Mesh
# ═══════════════════════════════════════════════════════════════════════════

# SESSION-193: When Dummy Mesh is detected, these are the arbitrated strengths.
# OpenPose takes over motion control at full strength.
# Depth/Normal are softened to break geometric lock.
OPENPOSE_CONTROLNET_STRENGTH: float = 1.0
DUMMY_MESH_DEPTH_NORMAL_STRENGTH: float = 0.45

# The previous SESSION-192 value of 0.90 caused geometric overfitting
# when the upstream mesh is a featureless cylinder. SESSION-193 reverts
# Depth/Normal to the research-recommended 0.40–0.45 band and delegates
# motion authority to OpenPose at 1.0.


def arbitrate_controlnet_strengths(
    workflow: dict[str, Any],
    *,
    is_dummy_mesh: bool = False,
    openpose_strength: float = OPENPOSE_CONTROLNET_STRENGTH,
    depth_normal_strength: float = DUMMY_MESH_DEPTH_NORMAL_STRENGTH,
) -> dict[str, Any]:
    """SESSION-193 ControlNet Arbitration: adjust strengths based on mesh quality.

    When ``is_dummy_mesh`` is True:
      - Depth/Normal ControlNet strength → 0.40–0.45 (break geometric lock)
      - OpenPose ControlNet strength → 1.0 (math skeleton takes over)

    When ``is_dummy_mesh`` is False:
      - No changes are made (existing strengths are preserved).

    All node addressing uses ``class_type`` / ``_meta.title`` — NEVER
    hardcoded numeric IDs.

    Returns
    -------
    dict
        Arbitration report.
    """
    report: dict[str, Any] = {
        "session": "SESSION-193",
        "feature": "controlnet_arbitration",
        "is_dummy_mesh": is_dummy_mesh,
        "touched_nodes": [],
    }

    if not is_dummy_mesh:
        report["action"] = "no_change"
        return report

    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            continue
        class_type = node.get("class_type", "")
        inputs = node.get("inputs", {})
        meta = node.get("_meta", {})
        title = str(meta.get("title", "")).lower()

        # ── OpenPose ControlNet → strength 1.0 ──
        if class_type.startswith("ControlNetApply") and "openpose" in title:
            if "strength" in inputs:
                prev = inputs["strength"]
                inputs["strength"] = float(openpose_strength)
                report["touched_nodes"].append({
                    "node_id": node_id,
                    "class_type": class_type,
                    "operation": "openpose_strength_maximize",
                    "strength": [prev, float(openpose_strength)],
                })

        # ── Depth/Normal ControlNet → strength 0.45 ──
        elif class_type.startswith("ControlNetApply"):
            is_depth_normal = any(
                kw in title for kw in ("depth", "normal")
            )
            is_rgb = any(
                kw in title for kw in ("rgb", "color", "sparsectrl", "sparse")
            )
            if is_depth_normal and not is_rgb:
                if "strength" in inputs:
                    prev = inputs["strength"]
                    inputs["strength"] = float(depth_normal_strength)
                    report["touched_nodes"].append({
                        "node_id": node_id,
                        "class_type": class_type,
                        "operation": "depth_normal_strength_soften",
                        "strength": [prev, float(depth_normal_strength)],
                    })

    report["action"] = "arbitrated"
    report["openpose_strength"] = float(openpose_strength)
    report["depth_normal_strength"] = float(depth_normal_strength)
    return report


__all__ = [
    "COCO_18_KEYPOINT_NAMES",
    "COCO_18_LIMB_PAIRS",
    "COCO_18_LIMB_COLORS",
    "COCO_18_KEYPOINT_COLORS",
    "OPENPOSE_CONTROLNET_STRENGTH",
    "DUMMY_MESH_DEPTH_NORMAL_STRENGTH",
    "skeleton_joints_to_coco18",
    "render_openpose_frame",
    "render_openpose_sequence",
    "render_openpose_from_2d_bones",
    "arbitrate_controlnet_strengths",
]
