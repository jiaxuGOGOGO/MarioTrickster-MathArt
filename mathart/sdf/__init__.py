"""2D Signed Distance Field module for procedural pixel art effects."""
from .primitives import circle, box, segment, triangle, star, ring
from .operations import union, intersection, subtraction, smooth_union, smooth_subtraction
from .renderer import render_sdf, render_spritesheet
from .effects import flame_sdf, electric_arc_sdf, glow_sdf, spike_sdf, saw_blade_sdf

__all__ = [
    "circle", "box", "segment", "triangle", "star", "ring",
    "union", "intersection", "subtraction", "smooth_union", "smooth_subtraction",
    "render_sdf", "render_spritesheet",
    "flame_sdf", "electric_arc_sdf", "glow_sdf", "spike_sdf", "saw_blade_sdf",
]
