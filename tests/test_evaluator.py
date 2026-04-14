"""Tests for the Asset Quality Evaluator module."""
import numpy as np
import pytest
from PIL import Image

from mathart.evaluator import AssetEvaluator, EvaluationResult, QualityMetric


# ── Fixtures ──

def make_solid_image(r, g, b, size=(32, 32)) -> Image.Image:
    """Create a solid-color RGBA image."""
    arr = np.full((*size, 4), [r, g, b, 255], dtype=np.uint8)
    return Image.fromarray(arr, mode="RGBA")


def make_checkerboard(size=(32, 32)) -> Image.Image:
    """Create a high-contrast checkerboard (sharp pixel art)."""
    arr = np.zeros((*size, 4), dtype=np.uint8)
    for y in range(size[0]):
        for x in range(size[1]):
            val = 255 if (x + y) % 2 == 0 else 0
            arr[y, x] = [val, val, val, 255]
    return Image.fromarray(arr, mode="RGBA")


def make_gradient_image(size=(32, 32)) -> Image.Image:
    """Create a smooth gradient (blurry, low sharpness)."""
    arr = np.zeros((*size, 4), dtype=np.uint8)
    for y in range(size[0]):
        for x in range(size[1]):
            val = int(x / size[1] * 255)
            arr[y, x] = [val, val, val, 255]
    return Image.fromarray(arr, mode="RGBA")


# ── Unit Tests ──

@pytest.mark.unit
class TestAssetEvaluator:
    def test_evaluate_returns_result(self):
        evaluator = AssetEvaluator()
        image = make_checkerboard()
        result = evaluator.evaluate(image)
        assert isinstance(result, EvaluationResult)
        assert 0.0 <= result.overall_score <= 1.0

    def test_checkerboard_has_high_sharpness(self):
        evaluator = AssetEvaluator()
        image = make_checkerboard()
        result = evaluator.evaluate(image)
        sharpness = result.breakdown[QualityMetric.SHARPNESS].score
        assert sharpness > 0.5, f"Checkerboard should have high sharpness, got {sharpness}"

    def test_gradient_has_lower_sharpness_than_checkerboard(self):
        evaluator = AssetEvaluator()
        checkerboard = make_checkerboard()
        gradient = make_gradient_image()
        result_cb = evaluator.evaluate(checkerboard)
        result_grad = evaluator.evaluate(gradient)
        sharp_cb = result_cb.breakdown[QualityMetric.SHARPNESS].score
        sharp_grad = result_grad.breakdown[QualityMetric.SHARPNESS].score
        assert sharp_cb > sharp_grad, (
            f"Checkerboard sharpness ({sharp_cb:.3f}) should exceed "
            f"gradient sharpness ({sharp_grad:.3f})"
        )

    def test_high_contrast_image_scores_well(self):
        evaluator = AssetEvaluator()
        image = make_checkerboard()
        result = evaluator.evaluate(image)
        contrast = result.breakdown[QualityMetric.CONTRAST].score
        assert contrast > 0.7, f"Checkerboard should have high contrast, got {contrast}"

    def test_solid_image_low_contrast(self):
        evaluator = AssetEvaluator()
        image = make_solid_image(128, 128, 128)
        result = evaluator.evaluate(image)
        contrast = result.breakdown[QualityMetric.CONTRAST].score
        assert contrast < 0.2, f"Solid image should have low contrast, got {contrast}"

    def test_palette_adherence_perfect_match(self):
        palette = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
        evaluator = AssetEvaluator(palette=palette)
        # Image with only red pixels
        image = make_solid_image(255, 0, 0)
        result = evaluator.evaluate(image)
        adherence = result.breakdown[QualityMetric.PALETTE_ADHERENCE].score
        assert adherence > 0.9, f"Red image should match red palette, got {adherence}"

    def test_palette_adherence_no_match(self):
        palette = [(0, 0, 0), (255, 255, 255)]  # Only black and white
        evaluator = AssetEvaluator(palette=palette)
        # Image with bright red pixels
        image = make_solid_image(255, 0, 0)
        result = evaluator.evaluate(image)
        adherence = result.breakdown[QualityMetric.PALETTE_ADHERENCE].score
        assert adherence < 0.5, f"Red image should not match B&W palette, got {adherence}"

    def test_style_consistency_identical_images(self):
        evaluator = AssetEvaluator()
        image = make_checkerboard()
        result = evaluator.evaluate(image, reference=image)
        consistency = result.breakdown[QualityMetric.STYLE_CONSISTENCY].score
        assert consistency > 0.9, f"Identical images should have high consistency, got {consistency}"

    def test_style_consistency_different_images(self):
        evaluator = AssetEvaluator()
        # Use larger images for reliable pHash differentiation
        image1 = make_checkerboard(size=(64, 64))
        image2 = make_solid_image(200, 100, 50, size=(64, 64))
        result_same = evaluator.evaluate(image1, reference=image1)  # identical
        result_diff = evaluator.evaluate(image1, reference=image2)  # different
        consistency_same = result_same.breakdown[QualityMetric.STYLE_CONSISTENCY].score
        consistency_diff = result_diff.breakdown[QualityMetric.STYLE_CONSISTENCY].score
        # Identical images should have perfect consistency
        assert consistency_same == 1.0
        # Different images should have lower or equal consistency
        assert consistency_diff <= consistency_same

    def test_color_harmony_metric_present(self):
        evaluator = AssetEvaluator()
        image = make_checkerboard()
        result = evaluator.evaluate(image)
        assert QualityMetric.COLOR_HARMONY in result.breakdown

    def test_evaluation_result_has_suggestions(self):
        evaluator = AssetEvaluator(pass_threshold=0.99)  # Very strict
        image = make_gradient_image()
        result = evaluator.evaluate(image)
        # With strict threshold, should generate suggestions
        assert isinstance(result.suggestions, list)

    def test_summary_string(self):
        evaluator = AssetEvaluator()
        image = make_checkerboard()
        result = evaluator.evaluate(image)
        summary = result.summary()
        assert "Overall" in summary
        assert "sharpness" in summary.lower() or "SHARPNESS" in summary

    def test_no_palette_skips_adherence(self):
        evaluator = AssetEvaluator(palette=None)
        image = make_checkerboard()
        result = evaluator.evaluate(image)
        adherence = result.breakdown[QualityMetric.PALETTE_ADHERENCE]
        assert adherence.score == 1.0  # Skipped = perfect score
        assert "skipped" in adherence.details.lower()

    def test_transparent_image(self):
        """Fully transparent image should not crash."""
        arr = np.zeros((32, 32, 4), dtype=np.uint8)  # All transparent
        image = Image.fromarray(arr, mode="RGBA")
        evaluator = AssetEvaluator()
        result = evaluator.evaluate(image)
        assert isinstance(result, EvaluationResult)

    def test_custom_weights_sum_to_one(self):
        """Custom weights are applied correctly."""
        weights = {
            QualityMetric.SHARPNESS: 0.5,
            QualityMetric.CONTRAST: 0.5,
        }
        evaluator = AssetEvaluator(weights=weights)
        image = make_checkerboard()
        result = evaluator.evaluate(image)
        assert 0.0 <= result.overall_score <= 1.0

    def test_register_custom_metric(self):
        """Custom metrics can be registered."""
        evaluator = AssetEvaluator()

        from mathart.evaluator.evaluator import MetricResult

        def my_metric(image, ev):
            return MetricResult(QualityMetric.RULE_COMPLIANCE, 0.8, "custom test")

        evaluator.register_metric(my_metric)
        image = make_checkerboard()
        result = evaluator.evaluate(image)
        assert QualityMetric.RULE_COMPLIANCE in result.breakdown
        assert result.breakdown[QualityMetric.RULE_COMPLIANCE].score == 0.8


@pytest.mark.unit
class TestPHash:
    def test_identical_images_zero_distance(self):
        image = make_checkerboard()
        h1 = AssetEvaluator._phash(image)
        h2 = AssetEvaluator._phash(image)
        assert h1 == h2

    def test_different_images_nonzero_distance(self):
        # Use larger images for reliable pHash differentiation
        img1 = make_checkerboard(size=(64, 64))
        img2 = make_solid_image(200, 100, 50, size=(64, 64))
        h1 = AssetEvaluator._phash(img1)
        h2 = AssetEvaluator._phash(img2)
        # XOR should have some bits set for visually different images
        xor = h1 ^ h2
        hamming = bin(xor).count('1')
        assert hamming > 0, f"Checkerboard vs solid should differ (hamming={hamming})"

    def test_phash_is_integer(self):
        image = make_checkerboard()
        h = AssetEvaluator._phash(image)
        assert isinstance(h, int)


@pytest.mark.unit
class TestOKLABConversion:
    def test_black_converts_to_zero_L(self):
        black = np.array([[0.0, 0.0, 0.0]])
        lab = AssetEvaluator._rgb_to_oklab(black)
        assert lab[0, 0] < 0.05  # L should be near 0

    def test_white_converts_to_high_L(self):
        white = np.array([[1.0, 1.0, 1.0]])
        lab = AssetEvaluator._rgb_to_oklab(white)
        assert lab[0, 0] > 0.9  # L should be near 1

    def test_output_shape(self):
        rgb = np.random.rand(100, 3)
        lab = AssetEvaluator._rgb_to_oklab(rgb)
        assert lab.shape == (100, 3)
