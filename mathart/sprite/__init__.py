"""Sprite Analysis Engine — learn from reference sprites and spritesheets.

This module analyzes uploaded sprite assets and extracts:
  1. Color palette (OKLAB-quantized, up to 32 colors)
  2. Pixel art style fingerprint (line weight, outline presence, shading style)
  3. Animation metrics (frame count, motion vectors, timing rhythm)
  4. Anatomy proportions (if character sprite: head/body/limb ratios)
  5. Mathematical style parameters (contrast, edge density, color harmony)

Extracted knowledge is stored in knowledge/sprite_library.json and
automatically fed into:
  - AssetEvaluator (as reference palette + style fingerprint)
  - RuleCompiler (as soft constraints for parameter space)
  - InnerLoop (as quality benchmark targets)
  - ArtMathQualityController (as living style guide)
"""
from mathart.sprite.analyzer import SpriteAnalyzer
from mathart.sprite.library import SpriteLibrary
from mathart.sprite.sheet_parser import SpriteSheetParser

__all__ = ["SpriteAnalyzer", "SpriteLibrary", "SpriteSheetParser"]
