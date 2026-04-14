"""Asset Quality Evaluator — Inner Loop quality scoring for the self-evolution system.

This module provides automated quality evaluation for generated pixel art assets.
It implements multiple complementary metrics:

1. **Pixel Art Quality Metrics**:
   - Frequency analysis (sharp pixel edges vs. blurry)
   - Palette adherence (how well colors match the target palette)
   - Contrast and readability score

2. **Style Consistency**:
   - Perceptual hash (pHash) distance from style reference
   - Color distribution similarity (OKLAB histogram comparison)

3. **Rule Compliance**:
   - Hard constraint validation from knowledge/ files
   - Anatomy ROM checks, color gamut checks, etc.

Usage::

    from mathart.evaluator import AssetEvaluator, EvaluationResult
    evaluator = AssetEvaluator()
    result = evaluator.evaluate(image, reference=style_ref)
    print(result.overall_score)  # 0.0 - 1.0
    print(result.breakdown)      # per-metric scores
"""
from .evaluator import AssetEvaluator, EvaluationResult, QualityMetric

__all__ = ["AssetEvaluator", "EvaluationResult", "QualityMetric"]
