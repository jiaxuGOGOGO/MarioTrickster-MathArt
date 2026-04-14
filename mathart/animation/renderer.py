"""
Skeleton → Sprite Sheet renderer.

Renders skeletal animations as pixel art sprite sheets compatible with
AI_SpriteSlicer (horizontal strip, colCount = frame count).

Output: RGBA PNG with transparent background, PPU-compatible sizing.
"""
from __future__ import annotations
from typing import Callable
import numpy as np
from PIL import Image, ImageDraw

from .skeleton import Skeleton
from ..oklab.palette import Palette
from ..oklab.color_space import oklab_to_srgb


def render_skeleton_frame(
    skeleton: Skeleton,
    pose: dict[str, float],
    width: int = 32,
    height: int = 32,
    palette: Palette | None = None,
    bone_width_px: int = 3,
    joint_radius_px: int = 2,
) -> Image.Image:
    """Render a single skeleton pose to a pixel art frame.

    Args:
        skeleton: The skeleton to render.
        pose: Joint angles for this frame.
        width, height: Frame size in pixels.
        palette: Color palette (uses first few colors for body parts).
        bone_width_px: Bone rendering width in pixels.
        joint_radius_px: Joint dot radius in pixels.

    Returns:
        RGBA PIL Image.
    """
    skeleton.apply_pose(pose)

    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Map skeleton coords to pixel coords
    # Skeleton: x ∈ [-0.5, 0.5], y ∈ [0, 1] (feet at bottom)
    def to_px(sx: float, sy: float) -> tuple[int, int]:
        px = int((sx + 0.5) * (width - 1))
        py = int((1.0 - sy) * (height - 1))  # Flip Y
        return (np.clip(px, 0, width - 1), np.clip(py, 0, height - 1))

    # Determine colors
    if palette is not None and palette.count >= 3:
        srgb = palette.colors_srgb
        bone_color = tuple(int(c) for c in srgb[0]) + (255,)
        joint_color = tuple(int(c) for c in srgb[1]) + (255,)
        head_color = tuple(int(c) for c in srgb[2]) + (255,)
    else:
        bone_color = (180, 120, 80, 255)
        joint_color = (220, 180, 140, 255)
        head_color = (240, 200, 160, 255)

    positions = skeleton.get_joint_positions()

    # Draw bones
    for bone in skeleton.bones:
        if bone.joint_a in positions and bone.joint_b in positions:
            p1 = to_px(*positions[bone.joint_a])
            p2 = to_px(*positions[bone.joint_b])
            draw.line([p1, p2], fill=bone_color, width=bone_width_px)

    # Draw joints
    for name, (jx, jy) in positions.items():
        px, py = to_px(jx, jy)
        r = joint_radius_px
        draw.ellipse([px - r, py - r, px + r, py + r], fill=joint_color)

    # Draw head (larger circle)
    if "head" in positions:
        hx, hy = positions["head"]
        px, py = to_px(hx, hy)
        hr = int(width * 0.12)
        draw.ellipse([px - hr, py - hr, px + hr, py + hr], fill=head_color)

    return img


def render_skeleton_sheet(
    skeleton: Skeleton,
    animation_func: Callable[[float], dict[str, float]],
    frames: int = 8,
    frame_width: int = 32,
    frame_height: int = 32,
    palette: Palette | None = None,
) -> Image.Image:
    """Render a full animation cycle as a horizontal sprite sheet.

    Args:
        skeleton: The skeleton to animate.
        animation_func: Function mapping t ∈ [0, 1] → pose dict.
        frames: Number of frames.
        frame_width, frame_height: Size of each frame.
        palette: Color palette.

    Returns:
        RGBA PIL Image (width = frame_width * frames, height = frame_height).
        Compatible with AI_SpriteSlicer (colCount = frames).
    """
    sheet = Image.new("RGBA", (frame_width * frames, frame_height), (0, 0, 0, 0))

    for i in range(frames):
        t = i / frames
        pose = animation_func(t)
        frame = render_skeleton_frame(
            skeleton, pose, frame_width, frame_height, palette
        )
        sheet.paste(frame, (i * frame_width, 0))

    return sheet
