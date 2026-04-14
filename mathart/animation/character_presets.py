"""
Character preset templates for MarioTrickster.

Each preset defines a CharacterStyle + a dedicated palette that matches
the character's visual identity from the game design.

Presets:
  - Mario: red cap, red shirt, blue pants, brown shoes, mustache
  - Trickster: top hat, dark suit, purple accents, no mustache
  - SimpleEnemy (Goomba-like): round body, angry eyes, brown palette
  - FlyingEnemy: winged variant, lighter colors
  - BouncingEnemy: spring-loaded, green palette

Palette layout (6 colors minimum):
  [0] skin, [1] hair/hat, [2] shirt, [3] pants, [4] shoes, [5] outline
"""
from __future__ import annotations
from ..oklab.palette import Palette
from ..oklab.color_space import srgb_to_oklab
from .parts import CharacterStyle
import numpy as np


def _make_palette(name: str, colors_srgb: np.ndarray) -> Palette:
    """Helper: create a Palette from sRGB colors."""
    colors_oklab = srgb_to_oklab(colors_srgb)
    roles = ["skin", "hair_hat", "shirt", "pants", "shoes", "outline"]
    return Palette(
        name=name,
        colors_oklab=colors_oklab,
        roles=roles[:len(colors_oklab)],
    )


def mario_style() -> CharacterStyle:
    """Mario character style — red cap, mustache, chibi proportions."""
    return CharacterStyle(
        head_radius=0.38,
        torso_width=0.26,
        torso_height=0.20,
        arm_thickness=0.07,
        leg_thickness=0.08,
        hand_radius=0.055,
        foot_width=0.09,
        foot_height=0.045,
        skin_color_idx=0,
        hair_color_idx=1,
        shirt_color_idx=2,
        pants_color_idx=3,
        shoe_color_idx=4,
        outline_color_idx=5,
        has_hat=True,
        hat_style="cap",
        has_mustache=True,
        eye_style="dot",
        outline_width=0.04,
        light_angle=-0.7,
    )


def mario_palette(seed: int = 42) -> Palette:
    """Mario's signature color palette."""
    colors_srgb = np.array([
        [240, 195, 155],   # skin (warm peach)
        [210, 45, 35],     # hat/hair (mario red)
        [210, 45, 35],     # shirt (mario red)
        [65, 75, 195],     # pants (mario blue)
        [110, 65, 30],     # shoes (brown)
        [28, 18, 12],      # outline (near-black warm)
    ], dtype=np.float64)
    return _make_palette("mario", colors_srgb)


def trickster_style() -> CharacterStyle:
    """Trickster character style — top hat, dark suit, mysterious."""
    return CharacterStyle(
        head_radius=0.36,
        torso_width=0.24,
        torso_height=0.22,
        arm_thickness=0.065,
        leg_thickness=0.075,
        hand_radius=0.05,
        foot_width=0.085,
        foot_height=0.04,
        skin_color_idx=0,
        hair_color_idx=1,
        shirt_color_idx=2,
        pants_color_idx=3,
        shoe_color_idx=4,
        outline_color_idx=5,
        has_hat=True,
        hat_style="top",
        has_mustache=False,
        eye_style="oval",
        outline_width=0.04,
        light_angle=-0.7,
    )


def trickster_palette(seed: int = 42) -> Palette:
    """Trickster's dark mysterious palette."""
    colors_srgb = np.array([
        [200, 180, 165],   # skin (pale)
        [55, 35, 75],      # hat (dark purple)
        [55, 35, 75],      # suit (dark purple)
        [35, 30, 50],      # pants (darker purple)
        [25, 20, 20],      # shoes (near-black)
        [15, 10, 18],      # outline (deep dark)
    ], dtype=np.float64)
    return _make_palette("trickster", colors_srgb)


def simple_enemy_style() -> CharacterStyle:
    """Simple enemy (Goomba-like) — round, stubby, angry eyes."""
    return CharacterStyle(
        head_radius=0.42,
        torso_width=0.30,
        torso_height=0.15,
        arm_thickness=0.06,
        leg_thickness=0.08,
        hand_radius=0.04,
        foot_width=0.10,
        foot_height=0.05,
        skin_color_idx=0,
        hair_color_idx=1,
        shirt_color_idx=2,
        pants_color_idx=3,
        shoe_color_idx=4,
        outline_color_idx=5,
        has_hat=False,
        has_mustache=False,
        eye_style="wide",
        outline_width=0.045,
        light_angle=-0.7,
    )


def simple_enemy_palette(seed: int = 42) -> Palette:
    """Brown enemy palette."""
    colors_srgb = np.array([
        [180, 130, 80],    # body (tan)
        [120, 75, 40],     # dark accent
        [180, 130, 80],    # same as body
        [140, 95, 55],     # legs (darker tan)
        [90, 55, 25],      # feet (dark brown)
        [35, 22, 10],      # outline
    ], dtype=np.float64)
    return _make_palette("simple_enemy", colors_srgb)


def flying_enemy_style() -> CharacterStyle:
    """Flying enemy — lighter, with wide eyes."""
    return CharacterStyle(
        head_radius=0.35,
        torso_width=0.22,
        torso_height=0.18,
        arm_thickness=0.06,
        leg_thickness=0.06,
        hand_radius=0.04,
        foot_width=0.07,
        foot_height=0.035,
        skin_color_idx=0,
        hair_color_idx=1,
        shirt_color_idx=2,
        pants_color_idx=3,
        shoe_color_idx=4,
        outline_color_idx=5,
        has_hat=False,
        has_mustache=False,
        eye_style="wide",
        outline_width=0.04,
        light_angle=-0.7,
    )


def flying_enemy_palette(seed: int = 42) -> Palette:
    """Light blue/gray flying enemy palette."""
    colors_srgb = np.array([
        [170, 190, 210],   # body (light blue-gray)
        [120, 140, 165],   # accent
        [170, 190, 210],   # same
        [140, 160, 180],   # lower body
        [100, 115, 135],   # feet
        [30, 35, 45],      # outline
    ], dtype=np.float64)
    return _make_palette("flying_enemy", colors_srgb)


def bouncing_enemy_style() -> CharacterStyle:
    """Bouncing enemy — spring-loaded, green."""
    return CharacterStyle(
        head_radius=0.36,
        torso_width=0.28,
        torso_height=0.20,
        arm_thickness=0.065,
        leg_thickness=0.09,
        hand_radius=0.045,
        foot_width=0.11,
        foot_height=0.05,
        skin_color_idx=0,
        hair_color_idx=1,
        shirt_color_idx=2,
        pants_color_idx=3,
        shoe_color_idx=4,
        outline_color_idx=5,
        has_hat=False,
        has_mustache=False,
        eye_style="dot",
        outline_width=0.04,
        light_angle=-0.7,
    )


def bouncing_enemy_palette(seed: int = 42) -> Palette:
    """Green bouncing enemy palette."""
    colors_srgb = np.array([
        [120, 185, 90],    # body (green)
        [75, 130, 55],     # dark green
        [120, 185, 90],    # same
        [95, 155, 70],     # legs
        [60, 100, 40],     # feet
        [20, 35, 12],      # outline
    ], dtype=np.float64)
    return _make_palette("bouncing_enemy", colors_srgb)


# ── Registry ──

CHARACTER_PRESETS = {
    "mario": (mario_style, mario_palette),
    "trickster": (trickster_style, trickster_palette),
    "simple_enemy": (simple_enemy_style, simple_enemy_palette),
    "flying_enemy": (flying_enemy_style, flying_enemy_palette),
    "bouncing_enemy": (bouncing_enemy_style, bouncing_enemy_palette),
}


def get_preset(name: str) -> tuple[CharacterStyle, Palette]:
    """Get a character preset by name."""
    if name not in CHARACTER_PRESETS:
        available = ", ".join(CHARACTER_PRESETS.keys())
        raise ValueError(f"Unknown preset '{name}'. Available: {available}")
    style_fn, palette_fn = CHARACTER_PRESETS[name]
    return style_fn(), palette_fn()
