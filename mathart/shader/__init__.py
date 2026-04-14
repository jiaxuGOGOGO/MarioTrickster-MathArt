"""Unity Shader Knowledge Module and Pseudo-3D Extension Scaffold.

This module provides:
  1. ShaderKnowledgeBase — structured knowledge about Unity URP/HDRP shaders
     relevant to 2D pixel art (sprite lit, outline, palette swap, rim light).
  2. ShaderParamOptimizer — parameter optimizer that uses the inner loop's
     quality evaluator to tune shader parameters for pixel art aesthetics.
  3. Pseudo3DExtension — reserved scaffold for future pseudo-3D rendering
     (billboarded sprites, depth-based parallax, normal-mapped 2D sprites).
  4. ShaderCodeGenerator — generates Unity HLSL shader code fragments from
     distilled knowledge rules.

Upgrade path
------------
Current state (v0.3.x): CPU-side shader parameter recommendation only.
  - Reads shader knowledge from knowledge/unity_shader.md
  - Generates HLSL code fragments and .shadergraph JSON snippets
  - Validates parameters against art quality metrics

Next upgrade (requires Unity + GPU):
  - User runs generated shader in Unity and captures screenshots
  - Screenshots are fed back via `mathart-evolve eval` for quality scoring
  - System closes the loop: shader params → Unity render → quality score → optimize

Pseudo-3D upgrade path (future):
  - Normal map generation from 2D sprites (SpriteIlluminator-style math)
  - Depth buffer simulation via painter's algorithm
  - Parallax scrolling layers with depth-based scale
  - Billboard sprite rotation math for isometric view
"""
from mathart.shader.knowledge import ShaderKnowledgeBase
from mathart.shader.generator import ShaderCodeGenerator
from mathart.shader.pseudo3d import Pseudo3DExtension

__all__ = [
    "ShaderKnowledgeBase",
    "ShaderCodeGenerator",
    "Pseudo3DExtension",
]
