"""ArtMathQualityController — unified quality control across the full pipeline.

This module is the central nervous system of the self-evolution loop.
It connects:
  - Knowledge base (distilled art rules + math models)
  - Sprite library (reference fingerprints)
  - Asset evaluator (per-image scoring)
  - Stagnation guard (invalid iteration detection)
  - Inner loop (parameter optimization)

The controller runs at FOUR checkpoints in the pipeline:
  1. PRE-GENERATION: Inject knowledge constraints into parameter space
  2. MID-GENERATION: Monitor partial results during multi-step generation
  3. POST-GENERATION: Score final asset, compare to references
  4. ITERATION-END: Decide continue/escalate/stop based on trend

Knowledge and math models are ACTIVE participants at every checkpoint,
not just passive filters at the end.
"""
from mathart.quality.controller import ArtMathQualityController
from mathart.quality.checkpoint import QualityCheckpoint, CheckpointResult
# SESSION-055: Multi-modal visual fitness scoring
from mathart.quality.visual_fitness import (
    VisualFitnessConfig,
    VisualFitnessResult,
    compute_laplacian_sharpness,
    compute_laplacian_quality,
    compute_frame_ssim,
    compute_temporal_consistency,
    compute_channel_dynamic_range,
    compute_depth_smoothness,
    compute_visual_fitness,
)

__all__ = [
    "ArtMathQualityController", "QualityCheckpoint", "CheckpointResult",
    # SESSION-055: Multi-modal visual fitness scoring
    "VisualFitnessConfig",
    "VisualFitnessResult",
    "compute_laplacian_sharpness",
    "compute_laplacian_quality",
    "compute_frame_ssim",
    "compute_temporal_consistency",
    "compute_channel_dynamic_range",
    "compute_depth_smoothness",
    "compute_visual_fitness",
]
