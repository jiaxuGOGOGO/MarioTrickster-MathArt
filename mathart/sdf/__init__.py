"""2D Signed Distance Field module for procedural pixel art effects."""
from .primitives import circle, box, segment, triangle, star, ring
from .operations import union, intersection, subtraction, smooth_union, smooth_subtraction
from .renderer import (
    render_sdf, render_sdf_simple, render_spritesheet, render_textured_sdf,
    render_sdf_layered, composite_layers, LayeredRenderResult,
    render_textured_sdf_layered,
)
from .effects import flame_sdf, electric_arc_sdf, glow_sdf, spike_sdf, saw_blade_sdf
from .noise import (
    perlin_2d, simplex_2d, fbm, ridged_noise, turbulence, domain_warp,
    render_noise_texture, generate_texture, TEXTURE_PRESETS,
)

__all__ = [
    "circle", "box", "segment", "triangle", "star", "ring",
    "union", "intersection", "subtraction", "smooth_union", "smooth_subtraction",
    "render_sdf", "render_sdf_simple", "render_spritesheet", "render_textured_sdf",
    "render_sdf_layered", "composite_layers", "LayeredRenderResult",
    "render_textured_sdf_layered",
    "flame_sdf", "electric_arc_sdf", "glow_sdf", "spike_sdf", "saw_blade_sdf",
    "perlin_2d", "simplex_2d", "fbm", "ridged_noise", "turbulence", "domain_warp",
    "render_noise_texture", "generate_texture", "TEXTURE_PRESETS",
]
