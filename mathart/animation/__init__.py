"""Programmatic 2D skeletal animation module."""
from .skeleton import Skeleton, Bone, Joint
from .curves import ease_in_out, spring, sine_wave, bezier_curve
from .presets import idle_animation, run_animation, jump_animation, fall_animation, hit_animation
from .renderer import render_skeleton_sheet
from .parts import CharacterStyle, BodyPart, assemble_character
from .character_renderer import render_character_frame, render_character_sheet
from .character_presets import get_preset, CHARACTER_PRESETS
from .principles import (
    PrincipledAnimation, AnimationKeyframe,
    SquashStretch, Anticipation, FollowThrough, ArcMotion,
    SecondaryAction, Exaggeration,
    EASING_FUNCTIONS, ANIMATION_PRESETS,
    create_jump_animation, create_walk_cycle, create_idle_breathe,
    create_attack_swing, create_death_animation,
)

__all__ = [
    "Skeleton", "Bone", "Joint",
    "ease_in_out", "spring", "sine_wave", "bezier_curve",
    "idle_animation", "run_animation", "jump_animation",
    "fall_animation", "hit_animation",
    "render_skeleton_sheet",
    "CharacterStyle", "BodyPart", "assemble_character",
    "render_character_frame", "render_character_sheet",
    "get_preset", "CHARACTER_PRESETS",
    "PrincipledAnimation", "AnimationKeyframe",
    "SquashStretch", "Anticipation", "FollowThrough", "ArcMotion",
    "SecondaryAction", "Exaggeration",
    "EASING_FUNCTIONS", "ANIMATION_PRESETS",
    "create_jump_animation", "create_walk_cycle", "create_idle_breathe",
    "create_attack_swing", "create_death_animation",
]
