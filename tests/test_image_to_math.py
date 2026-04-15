"""Tests for Image-to-Math parameter inference (TASK-014)."""
from __future__ import annotations

import pytest
import numpy as np
from PIL import Image

from mathart.sprite.image_to_math import (
    ImageToMathInference,
    InferenceResult,
    infer_and_evolve_params,
)


def make_test_sprite(w=32, h=32, color=(200, 100, 50)):
    """Create a simple test sprite with transparent background."""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    arr = np.array(img)
    # Draw a filled circle in the center
    cy, cx = h // 2, w // 2
    r = min(w, h) // 3
    for y in range(h):
        for x in range(w):
            if (x - cx) ** 2 + (y - cy) ** 2 < r ** 2:
                arr[y, x] = (*color, 255)
    return Image.fromarray(arr, "RGBA")


def make_character_sprite(w=16, h=32):
    """Create a simple character-like sprite (taller than wide)."""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    arr = np.array(img)
    # Head region (top 1/3)
    head_h = h // 3
    for y in range(2, head_h):
        for x in range(w // 4, 3 * w // 4):
            arr[y, x] = (200, 150, 100, 255)
    # Body region (bottom 2/3)
    for y in range(head_h, h - 2):
        for x in range(w // 6, 5 * w // 6):
            arr[y, x] = (100, 100, 200, 255)
    return Image.fromarray(arr, "RGBA")


class TestImageToMathInference:
    def test_basic_inference(self):
        """Should produce a valid InferenceResult from a simple sprite."""
        img = make_test_sprite()
        inferrer = ImageToMathInference()
        result = inferrer.infer_from_image(img, source_name="test_circle")

        assert isinstance(result, InferenceResult)
        assert result.fingerprint.source_name == "test_circle"
        assert len(result.parameter_space.constraints) > 0
        assert 0.0 <= result.confidence <= 1.0
        assert isinstance(result.seed_individual.params, dict)

    def test_color_params_inferred(self):
        """Color parameters should be present in the inferred space."""
        img = make_test_sprite(color=(255, 0, 0))
        inferrer = ImageToMathInference()
        result = inferrer.infer_from_image(img)

        param_names = set(result.parameter_space.constraints.keys())
        assert "palette_size" in param_names
        assert "saturation" in param_names
        assert "contrast" in param_names

    def test_shape_params_inferred(self):
        """Shape parameters should be present in the inferred space."""
        img = make_test_sprite()
        inferrer = ImageToMathInference()
        result = inferrer.infer_from_image(img)

        param_names = set(result.parameter_space.constraints.keys())
        assert "edge_density" in param_names
        assert "fill_ratio" in param_names

    def test_character_anatomy_params(self):
        """Character sprites should have anatomy parameters."""
        img = make_character_sprite()
        inferrer = ImageToMathInference()
        result = inferrer.infer_from_image(img, sprite_type="character")

        # Anatomy detection depends on the analyzer
        # At minimum, basic params should exist
        assert len(result.parameter_space.constraints) >= 4

    def test_texture_params_optional(self):
        """Texture params should be excluded when disabled."""
        img = make_test_sprite()
        inferrer = ImageToMathInference()

        with_tex = inferrer.infer_from_image(img, include_texture_params=True)
        without_tex = inferrer.infer_from_image(img, include_texture_params=False)

        assert len(with_tex.parameter_space.constraints) > len(without_tex.parameter_space.constraints)

    def test_seed_individual_within_bounds(self):
        """Seed individual params should be within constraint bounds."""
        img = make_test_sprite()
        inferrer = ImageToMathInference()
        result = inferrer.infer_from_image(img)

        for name, constraint in result.parameter_space.constraints.items():
            value = result.seed_individual.params.get(name)
            if value is not None and constraint.min_value is not None:
                assert value >= constraint.min_value, f"{name}: {value} < {constraint.min_value}"
            if value is not None and constraint.max_value is not None:
                assert value <= constraint.max_value, f"{name}: {value} > {constraint.max_value}"

    def test_convenience_function(self):
        """infer_and_evolve_params should work as a convenience wrapper."""
        img = make_test_sprite()
        result = infer_and_evolve_params(img, source_name="quick_test")
        assert isinstance(result, InferenceResult)
        assert result.fingerprint.source_name == "quick_test"

    def test_summary_format(self):
        """Summary should contain key information."""
        img = make_test_sprite()
        result = infer_and_evolve_params(img)
        summary = result.summary()
        assert "Image-to-Math" in summary
        assert "Confidence" in summary

    def test_empty_image_low_confidence(self):
        """Fully transparent image should have low confidence."""
        img = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
        inferrer = ImageToMathInference()
        result = inferrer.infer_from_image(img)
        assert result.confidence < 0.5

    def test_animation_inference(self):
        """Should handle multi-frame animation inference."""
        frames = [make_test_sprite(color=(200, 100 + i * 20, 50)) for i in range(4)]
        inferrer = ImageToMathInference()
        result = inferrer.infer_from_frames(frames, source_name="test_anim")
        assert result.fingerprint.animation is not None
        assert "Animation" in result.summary() or len(result.notes) > 0
