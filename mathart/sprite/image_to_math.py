"""Image-to-Math parameter inference (TASK-014).

This module bridges the gap between reference sprite images and the
mathematical parameter spaces used by the evolution engine. Given a
reference image, it:

1. Analyzes the image using SpriteAnalyzer to extract a StyleFingerprint
2. Converts the fingerprint into ParameterSpace constraints
3. Optionally generates an initial population seeded from the fingerprint

This enables the "reverse engineering" workflow:
  User provides reference sprite → system infers math parameters →
  evolution engine optimizes from that starting point.

The approach is based on deterministic image analysis (no AI/ML required):
  - Color analysis → palette_size, saturation, contrast, warmth constraints
  - Shape analysis → edge_density, fill_ratio, outline_width constraints
  - Anatomy analysis → head_ratio, proportions constraints
  - Animation analysis → frame_count, motion constraints

References:
  - Reinhard et al., "Color Transfer between Images", 2001
  - Chang et al., "Palette-based Photo Recoloring", SIGGRAPH 2015
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

from ..distill.compiler import Constraint, ParameterSpace
from ..distill.optimizer import Individual
from .analyzer import SpriteAnalyzer, StyleFingerprint


@dataclass
class InferenceResult:
    """Result of Image-to-Math parameter inference."""
    fingerprint: StyleFingerprint
    parameter_space: ParameterSpace
    seed_individual: Individual
    confidence: float  # 0-1: how confident the inference is
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"Image-to-Math Inference Result",
            f"  Source: {self.fingerprint.source_name} ({self.fingerprint.sprite_type})",
            f"  Parameters: {len(self.parameter_space.constraints)}",
            f"  Confidence: {self.confidence:.2f}",
            f"  Seed fitness: {self.seed_individual.fitness:.3f}",
        ]
        if self.notes:
            lines.append("  Notes:")
            for note in self.notes:
                lines.append(f"    - {note}")
        return "\n".join(lines)


class ImageToMathInference:
    """Infer mathematical parameters from reference sprite images.

    This class provides the core Image-to-Math pipeline that converts
    visual properties of reference sprites into parameter space constraints
    suitable for the evolution engine.

    Parameters
    ----------
    analyzer : SpriteAnalyzer, optional
        Custom analyzer instance. Uses default if not provided.
    constraint_margin : float
        How much to expand inferred constraints beyond the measured value.
        Default 0.15 (15% margin on each side).
    """

    def __init__(
        self,
        analyzer: Optional[SpriteAnalyzer] = None,
        constraint_margin: float = 0.15,
    ):
        self.analyzer = analyzer or SpriteAnalyzer()
        self.margin = constraint_margin

    def infer_from_image(
        self,
        image: Image.Image,
        source_name: str = "reference",
        sprite_type: str = "unknown",
        include_texture_params: bool = True,
        include_animation_params: bool = False,
    ) -> InferenceResult:
        """Infer parameter space from a single reference image.

        Parameters
        ----------
        image : PIL.Image
            The reference sprite image.
        source_name : str
            Name for logging and provenance.
        sprite_type : str
            Hint for the analyzer.
        include_texture_params : bool
            Whether to include noise texture parameters.
        include_animation_params : bool
            Whether to include animation parameters.

        Returns
        -------
        InferenceResult
        """
        fp = self.analyzer.analyze(
            image,
            source_name=source_name,
            sprite_type=sprite_type,
        )
        return self._build_result(
            fp,
            include_texture_params=include_texture_params,
            include_animation_params=include_animation_params,
        )

    def infer_from_frames(
        self,
        frames: list[Image.Image],
        source_name: str = "animation_ref",
        sprite_type: str = "character",
    ) -> InferenceResult:
        """Infer parameter space from animation frames.

        Parameters
        ----------
        frames : list of PIL.Image
            The animation frames.
        source_name : str
            Name for logging.
        sprite_type : str
            Sprite type hint.

        Returns
        -------
        InferenceResult
        """
        fp = self.analyzer.analyze_frames(
            frames,
            source_name=source_name,
            sprite_type=sprite_type,
        )
        return self._build_result(
            fp,
            include_texture_params=False,
            include_animation_params=True,
        )

    def _build_result(
        self,
        fp: StyleFingerprint,
        include_texture_params: bool,
        include_animation_params: bool,
    ) -> InferenceResult:
        """Build InferenceResult from a StyleFingerprint."""
        space = ParameterSpace(name=f"inferred_{fp.source_name}")
        seed_params = {}
        notes = []
        confidence_factors = []

        # ── Color parameters ──────────────────────────────────────────
        if fp.color.color_count > 0:
            self._add_param(space, seed_params, "palette_size",
                            fp.color.color_count, 2, 32, is_int=True)
            self._add_param(space, seed_params, "saturation",
                            fp.color.saturation_mean, 0.0, 1.0)
            self._add_param(space, seed_params, "contrast",
                            fp.color.contrast, 0.0, 1.0)
            self._add_param(space, seed_params, "warm_ratio",
                            fp.color.warm_ratio, 0.0, 1.0)
            confidence_factors.append(0.8)
        else:
            notes.append("No visible pixels detected — color inference skipped")
            confidence_factors.append(0.1)

        # ── Shape parameters ──────────────────────────────────────────
        self._add_param(space, seed_params, "edge_density",
                        fp.shape.edge_density, 0.0, 1.0)
        self._add_param(space, seed_params, "fill_ratio",
                        fp.shape.fill_ratio, 0.0, 1.0)
        self._add_param(space, seed_params, "outline_width",
                        fp.shape.outline_width * 0.03, 0.0, 0.12)
        self._add_param(space, seed_params, "symmetry_target",
                        fp.shape.symmetry_score, 0.0, 1.0)
        confidence_factors.append(0.7)

        # ── Anatomy parameters (character sprites) ────────────────────
        if fp.anatomy and fp.anatomy.is_character:
            self._add_param(space, seed_params, "head_ratio",
                            fp.anatomy.head_ratio, 0.05, 0.5)
            self._add_param(space, seed_params, "body_aspect",
                            fp.anatomy.width_to_height, 0.3, 2.0)
            notes.append("Character anatomy detected — proportions constrained")
            confidence_factors.append(0.75)

        # ── Texture parameters (optional) ─────────────────────────────
        if include_texture_params:
            # Infer texture parameters from edge density and fill ratio
            # Higher edge density → higher octaves, lower scale
            inferred_octaves = 3 + fp.shape.edge_density * 5
            inferred_scale = 4.0 + (1.0 - fp.shape.edge_density) * 8.0
            self._add_param(space, seed_params, "octaves",
                            inferred_octaves, 3.0, 8.0, is_int=True)
            self._add_param(space, seed_params, "scale",
                            inferred_scale, 2.0, 16.0)
            self._add_param(space, seed_params, "brightness",
                            0.75 + fp.color.contrast * 0.5, 0.5, 1.5)
            notes.append("Texture parameters inferred from edge/color analysis")
            confidence_factors.append(0.5)

        # ── Animation parameters (optional) ───────────────────────────
        if include_animation_params and fp.animation:
            self._add_param(space, seed_params, "frame_count",
                            float(fp.animation.frame_count), 4.0, 24.0, is_int=True)
            self._add_param(space, seed_params, "motion_magnitude",
                            fp.animation.motion_magnitude, 0.0, 20.0)
            notes.append(f"Animation: {fp.animation.frame_count} frames detected")
            confidence_factors.append(0.6)

        # ── Compute overall confidence ────────────────────────────────
        confidence = sum(confidence_factors) / len(confidence_factors) if confidence_factors else 0.0
        # Adjust confidence based on image quality
        confidence *= min(1.0, fp.quality_score + 0.3)

        seed = Individual(params=seed_params, fitness=0.0)

        return InferenceResult(
            fingerprint=fp,
            parameter_space=space,
            seed_individual=seed,
            confidence=float(np.clip(confidence, 0.0, 1.0)),
            notes=notes,
        )

    def _add_param(
        self,
        space: ParameterSpace,
        seed_params: dict,
        name: str,
        value: float,
        global_min: float,
        global_max: float,
        is_int: bool = False,
    ) -> None:
        """Add a parameter with margin-based constraints."""
        span = global_max - global_min
        margin = span * self.margin

        lo = max(global_min, value - margin)
        hi = min(global_max, value + margin)
        default = float(np.clip(value, lo, hi))

        if is_int:
            lo = float(max(global_min, round(value - margin)))
            hi = float(min(global_max, round(value + margin)))
            default = float(round(default))

        space.add_constraint(Constraint(
            param_name=name,
            min_value=lo,
            max_value=hi,
            default_value=default,
        ))
        seed_params[name] = default


def infer_and_evolve_params(
    image: Image.Image,
    source_name: str = "reference",
    sprite_type: str = "unknown",
) -> InferenceResult:
    """Convenience function: infer parameters from a reference image.

    This is the main entry point for the Image-to-Math pipeline.
    """
    inferrer = ImageToMathInference()
    return inferrer.infer_from_image(
        image,
        source_name=source_name,
        sprite_type=sprite_type,
    )
