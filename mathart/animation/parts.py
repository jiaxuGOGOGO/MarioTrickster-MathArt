"""
Character part system — SDF-based body part definitions.

Each body part is a small SDF composition that gets positioned and rotated
by the skeletal system. This replaces the old line+circle renderer with
actual shaped, textured, lit body parts.

Design philosophy:
  - Each part is a function returning an SDF in local space [-1,1]²
  - Parts are parameterized by a CharacterStyle (colors, proportions)
  - The renderer transforms each part to world space using joint positions
  - Multiple layers: fill → highlight → shadow → outline

Distilled knowledge applied:
  - 3 head-unit chibi proportions (head ≈ 1/3 body height)
  - 5 Core Shapes (Peter Han): head=sphere, torso=box, limbs=cylinder
  - 暖光冷影: highlight warm-shifted, shadow cool-shifted in OKLAB
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable
import numpy as np

SDFFunc = Callable[[np.ndarray, np.ndarray], np.ndarray]


@dataclass
class CharacterStyle:
    """Visual style parameters for a character.

    Colors are indices into the palette. The renderer maps these
    to actual OKLAB colors at render time.
    """
    # Body proportions (relative to head size)
    head_radius: float = 0.38        # Head circle radius in normalized coords
    torso_width: float = 0.28        # Torso half-width
    torso_height: float = 0.22       # Torso half-height
    arm_thickness: float = 0.08      # Arm capsule radius
    leg_thickness: float = 0.09      # Leg capsule radius
    hand_radius: float = 0.06        # Hand circle radius
    foot_width: float = 0.10         # Foot box half-width
    foot_height: float = 0.05        # Foot box half-height

    # Color indices into palette
    skin_color_idx: int = 0          # Skin/face
    hair_color_idx: int = 1          # Hair/hat
    shirt_color_idx: int = 2         # Shirt/torso
    pants_color_idx: int = 3         # Pants/legs
    shoe_color_idx: int = 4          # Shoes
    outline_color_idx: int = 5       # Outline (darkest)

    # Feature flags
    has_hat: bool = False
    hat_style: str = "cap"           # "cap", "top", "none"
    has_mustache: bool = False
    eye_style: str = "dot"           # "dot", "oval", "wide"

    # Outline
    outline_width: float = 0.04      # Outline thickness in normalized coords

    # Light direction (for highlight/shadow)
    light_angle: float = -0.7        # Radians, -0.7 ≈ upper-left


# ── Body Part SDF Factories ──

def head_sdf(style: CharacterStyle) -> SDFFunc:
    """Head: circle + optional hat + eyes + optional mustache."""
    r = style.head_radius

    def sdf(x: np.ndarray, y: np.ndarray) -> np.ndarray:
        return np.sqrt(x**2 + y**2) - r
    return sdf


def hat_sdf(style: CharacterStyle) -> SDFFunc | None:
    """Hat on top of head."""
    if not style.has_hat:
        return None

    r = style.head_radius

    if style.hat_style == "cap":
        # Baseball cap: wide brim + dome
        def sdf(x: np.ndarray, y: np.ndarray) -> np.ndarray:
            # Dome (upper half of head)
            dome = np.sqrt(x**2 + (y - r * 0.3)**2) - r * 1.05
            # Brim (thin rectangle extending right)
            brim_dx = np.abs(x - r * 0.2) - r * 0.8
            brim_dy = np.abs(y - r * 0.6) - r * 0.12
            brim = np.sqrt(np.maximum(brim_dx, 0)**2 + np.maximum(brim_dy, 0)**2) + \
                   np.minimum(np.maximum(brim_dx, brim_dy), 0)
            # Only top half
            dome = np.where(y > r * 0.1, dome, 1.0)
            return np.minimum(dome, brim)
        return sdf

    elif style.hat_style == "top":
        # Top hat: tall rectangle
        def sdf(x: np.ndarray, y: np.ndarray) -> np.ndarray:
            hat_dx = np.abs(x) - r * 0.6
            hat_dy = np.abs(y - r * 0.9) - r * 0.5
            hat = np.sqrt(np.maximum(hat_dx, 0)**2 + np.maximum(hat_dy, 0)**2) + \
                  np.minimum(np.maximum(hat_dx, hat_dy), 0)
            brim_dx = np.abs(x) - r * 0.9
            brim_dy = np.abs(y - r * 0.4) - r * 0.08
            brim = np.sqrt(np.maximum(brim_dx, 0)**2 + np.maximum(brim_dy, 0)**2) + \
                   np.minimum(np.maximum(brim_dx, brim_dy), 0)
            return np.minimum(hat, brim)
        return sdf

    return None


def eye_sdf(style: CharacterStyle, side: float = 1.0) -> SDFFunc:
    """Eye: small shape offset from head center.

    side: 1.0 for right eye, -1.0 for left eye (from character's perspective).
    """
    r = style.head_radius
    ex = side * r * 0.25  # Horizontal offset
    ey = r * 0.05         # Slightly above center

    if style.eye_style == "dot":
        dot_r = r * 0.08
        def sdf(x: np.ndarray, y: np.ndarray) -> np.ndarray:
            return np.sqrt((x - ex)**2 + (y - ey)**2) - dot_r
    elif style.eye_style == "oval":
        ow, oh = r * 0.10, r * 0.14
        def sdf(x: np.ndarray, y: np.ndarray) -> np.ndarray:
            dx = (x - ex) / ow
            dy = (y - ey) / oh
            return np.sqrt(dx**2 + dy**2) - 1.0
    else:  # "wide"
        ow, oh = r * 0.14, r * 0.12
        def sdf(x: np.ndarray, y: np.ndarray) -> np.ndarray:
            dx = (x - ex) / ow
            dy = (y - ey) / oh
            return np.sqrt(dx**2 + dy**2) - 1.0
    return sdf


def mustache_sdf(style: CharacterStyle) -> SDFFunc | None:
    """Mustache below nose."""
    if not style.has_mustache:
        return None
    r = style.head_radius
    my = -r * 0.15  # Below center

    def sdf(x: np.ndarray, y: np.ndarray) -> np.ndarray:
        # Two overlapping circles for mustache shape
        d1 = np.sqrt((x - r * 0.12)**2 + (y - my)**2) - r * 0.12
        d2 = np.sqrt((x + r * 0.12)**2 + (y - my)**2) - r * 0.12
        return np.minimum(d1, d2)
    return sdf


def torso_sdf(style: CharacterStyle) -> SDFFunc:
    """Torso: rounded rectangle."""
    hw = style.torso_width
    hh = style.torso_height
    rounding = 0.04  # Corner rounding

    def sdf(x: np.ndarray, y: np.ndarray) -> np.ndarray:
        dx = np.abs(x) - (hw - rounding)
        dy = np.abs(y) - (hh - rounding)
        outside = np.sqrt(np.maximum(dx, 0)**2 + np.maximum(dy, 0)**2) - rounding
        inside = np.minimum(np.maximum(dx, dy), 0) - rounding
        return np.where((dx > 0) | (dy > 0), outside, inside)
    return sdf


def limb_sdf(thickness: float, length: float = 1.0) -> SDFFunc:
    """Limb: capsule (line segment with rounded ends).

    In local space, the limb goes from (0, 0) to (0, -length).
    """
    r = thickness

    def sdf(x: np.ndarray, y: np.ndarray) -> np.ndarray:
        # Capsule from (0,0) to (0,-length)
        t = np.clip(-y / (length + 1e-10), 0, 1)
        dx = x
        dy = y + t * length
        return np.sqrt(dx**2 + dy**2) - r
    return sdf


def hand_sdf(style: CharacterStyle) -> SDFFunc:
    """Hand: small circle."""
    r = style.hand_radius

    def sdf(x: np.ndarray, y: np.ndarray) -> np.ndarray:
        return np.sqrt(x**2 + y**2) - r
    return sdf


def foot_sdf(style: CharacterStyle) -> SDFFunc:
    """Foot: small rounded rectangle, slightly wider than tall."""
    hw = style.foot_width
    hh = style.foot_height
    rounding = 0.02

    def sdf(x: np.ndarray, y: np.ndarray) -> np.ndarray:
        dx = np.abs(x) - (hw - rounding)
        dy = np.abs(y) - (hh - rounding)
        outside = np.sqrt(np.maximum(dx, 0)**2 + np.maximum(dy, 0)**2) - rounding
        inside = np.minimum(np.maximum(dx, dy), 0) - rounding
        return np.where((dx > 0) | (dy > 0), outside, inside)
    return sdf


# ── Part Assembly ──

@dataclass
class BodyPart:
    """A positioned body part with its SDF and rendering info."""
    name: str
    sdf: SDFFunc
    color_idx: int           # Palette index for fill
    joint_name: str          # Which joint positions this part
    offset_x: float = 0.0   # Local offset from joint
    offset_y: float = 0.0
    rotation: float = 0.0   # Additional local rotation
    z_order: int = 0         # Drawing order (higher = on top)
    is_outline_only: bool = False  # For eyes, mustache


def assemble_character(style: CharacterStyle) -> list[BodyPart]:
    """Assemble a complete character from body parts.

    Returns parts sorted by z_order for correct layering.
    """
    parts = []
    hu = 1.0 / 3.0  # Head unit for 3-head chibi

    # Back arm (drawn first, behind body)
    parts.append(BodyPart(
        "r_upper_arm", limb_sdf(style.arm_thickness, hu * 0.5),
        style.shirt_color_idx, "r_shoulder", z_order=-2))
    parts.append(BodyPart(
        "r_forearm", limb_sdf(style.arm_thickness * 0.85, hu * 0.45),
        style.skin_color_idx, "r_elbow", z_order=-2))
    parts.append(BodyPart(
        "r_hand", hand_sdf(style),
        style.skin_color_idx, "r_hand", z_order=-2))

    # Back leg
    parts.append(BodyPart(
        "r_thigh", limb_sdf(style.leg_thickness, hu * 0.5),
        style.pants_color_idx, "r_hip", z_order=-1))
    parts.append(BodyPart(
        "r_shin", limb_sdf(style.leg_thickness * 0.85, hu * 0.45),
        style.pants_color_idx, "r_knee", z_order=-1))
    parts.append(BodyPart(
        "r_foot", foot_sdf(style),
        style.shoe_color_idx, "r_foot", offset_y=-0.01, z_order=-1))

    # Torso (middle layer)
    parts.append(BodyPart(
        "torso", torso_sdf(style),
        style.shirt_color_idx, "spine", z_order=0))

    # Front leg
    parts.append(BodyPart(
        "l_thigh", limb_sdf(style.leg_thickness, hu * 0.5),
        style.pants_color_idx, "l_hip", z_order=1))
    parts.append(BodyPart(
        "l_shin", limb_sdf(style.leg_thickness * 0.85, hu * 0.45),
        style.pants_color_idx, "l_knee", z_order=1))
    parts.append(BodyPart(
        "l_foot", foot_sdf(style),
        style.shoe_color_idx, "l_foot", offset_y=-0.01, z_order=1))

    # Front arm
    parts.append(BodyPart(
        "l_upper_arm", limb_sdf(style.arm_thickness, hu * 0.5),
        style.shirt_color_idx, "l_shoulder", z_order=2))
    parts.append(BodyPart(
        "l_forearm", limb_sdf(style.arm_thickness * 0.85, hu * 0.45),
        style.skin_color_idx, "l_elbow", z_order=2))
    parts.append(BodyPart(
        "l_hand", hand_sdf(style),
        style.skin_color_idx, "l_hand", z_order=2))

    # Head (on top)
    parts.append(BodyPart(
        "head", head_sdf(style),
        style.skin_color_idx, "head", z_order=3))

    # Hat (on top of head)
    hat = hat_sdf(style)
    if hat is not None:
        parts.append(BodyPart(
            "hat", hat,
            style.hair_color_idx, "head", z_order=4))

    # Eyes (on top of head, outline only)
    parts.append(BodyPart(
        "l_eye", eye_sdf(style, side=-1.0),
        style.outline_color_idx, "head", z_order=5, is_outline_only=True))
    parts.append(BodyPart(
        "r_eye", eye_sdf(style, side=1.0),
        style.outline_color_idx, "head", z_order=5, is_outline_only=True))

    # Mustache
    stache = mustache_sdf(style)
    if stache is not None:
        parts.append(BodyPart(
            "mustache", stache,
            style.hair_color_idx, "head", z_order=5, is_outline_only=True))

    # Sort by z_order
    parts.sort(key=lambda p: p.z_order)

    return parts
