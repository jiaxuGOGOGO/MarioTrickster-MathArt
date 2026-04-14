"""Asset Quality Evaluator — automated pixel art quality scoring.

Implements a multi-metric quality evaluation pipeline for the Inner Loop
of the self-evolution system. All metrics return values in [0.0, 1.0]
where 1.0 is best.

Design principles:
  - Zero external ML dependencies (pure numpy + PIL)
  - Fast enough for evolutionary optimization (< 50ms per image)
  - Interpretable: every score has a human-readable explanation
  - Extensible: new metrics can be added via register_metric()

Distilled knowledge applied:
  - Pixel art sharpness: high-frequency edges should be crisp, not blurry
  - OKLAB color adherence: colors should stay within the project palette
  - Contrast: foreground/background should have sufficient luminance delta
  - Perceptual hash: style consistency across asset batches
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

import numpy as np
from PIL import Image


class QualityMetric(str, Enum):
    """Available quality metrics."""
    SHARPNESS = "sharpness"           # Pixel-perfect edge crispness
    PALETTE_ADHERENCE = "palette_adherence"  # Color stays in palette
    CONTRAST = "contrast"             # Luminance contrast ratio
    STYLE_CONSISTENCY = "style_consistency"  # pHash distance from reference
    COLOR_HARMONY = "color_harmony"   # OKLAB color distribution quality
    RULE_COMPLIANCE = "rule_compliance"  # Hard constraint satisfaction
    OVERALL = "overall"               # Weighted aggregate


@dataclass
class MetricResult:
    """Result of a single quality metric evaluation."""
    metric: QualityMetric
    score: float          # 0.0 (worst) to 1.0 (best)
    details: str = ""     # Human-readable explanation
    passed: bool = True   # Whether it meets minimum threshold

    def __post_init__(self):
        self.score = float(np.clip(self.score, 0.0, 1.0))


@dataclass
class EvaluationResult:
    """Complete quality evaluation result for one asset.

    Attributes
    ----------
    overall_score : float
        Weighted aggregate of all metric scores (0.0 - 1.0).
    breakdown : dict[QualityMetric, MetricResult]
        Per-metric results.
    passed : bool
        True if overall_score >= pass_threshold.
    suggestions : list[str]
        Actionable improvement suggestions based on failed metrics.
    """
    overall_score: float
    breakdown: dict[QualityMetric, MetricResult] = field(default_factory=dict)
    passed: bool = True
    suggestions: list[str] = field(default_factory=list)

    def __post_init__(self):
        self.overall_score = float(np.clip(self.overall_score, 0.0, 1.0))

    def summary(self) -> str:
        lines = [f"Overall: {self.overall_score:.3f} ({'PASS' if self.passed else 'FAIL'})"]
        for metric, result in self.breakdown.items():
            status = "✓" if result.passed else "✗"
            lines.append(f"  {status} {metric.value}: {result.score:.3f}  {result.details}")
        if self.suggestions:
            lines.append("Suggestions:")
            for s in self.suggestions:
                lines.append(f"  → {s}")
        return "\n".join(lines)


# ── Metric weights (must sum to 1.0) ──
_DEFAULT_WEIGHTS: dict[QualityMetric, float] = {
    QualityMetric.SHARPNESS: 0.30,
    QualityMetric.PALETTE_ADHERENCE: 0.25,
    QualityMetric.CONTRAST: 0.20,
    QualityMetric.STYLE_CONSISTENCY: 0.15,
    QualityMetric.COLOR_HARMONY: 0.10,
}

# ── Minimum passing thresholds ──
_DEFAULT_THRESHOLDS: dict[QualityMetric, float] = {
    QualityMetric.SHARPNESS: 0.50,
    QualityMetric.PALETTE_ADHERENCE: 0.60,
    QualityMetric.CONTRAST: 0.40,
    QualityMetric.STYLE_CONSISTENCY: 0.30,
    QualityMetric.COLOR_HARMONY: 0.30,
    QualityMetric.OVERALL: 0.55,
}


class AssetEvaluator:
    """Multi-metric quality evaluator for pixel art assets.

    Parameters
    ----------
    palette : list of (R, G, B) tuples, optional
        Target color palette. If provided, palette adherence is evaluated.
    reference : PIL.Image, optional
        Style reference image for consistency checks.
    weights : dict, optional
        Custom metric weights (must sum to 1.0).
    thresholds : dict, optional
        Custom minimum passing thresholds per metric.
    pass_threshold : float
        Minimum overall score to be considered passing (default 0.55).
    """

    def __init__(
        self,
        palette: Optional[list[tuple[int, int, int]]] = None,
        reference: Optional[Image.Image] = None,
        weights: Optional[dict[QualityMetric, float]] = None,
        thresholds: Optional[dict[QualityMetric, float]] = None,
        pass_threshold: float = 0.55,
    ):
        self.palette = palette
        self.reference = reference
        self.weights = weights or dict(_DEFAULT_WEIGHTS)
        self.thresholds = thresholds or dict(_DEFAULT_THRESHOLDS)
        self.pass_threshold = pass_threshold
        self._custom_metrics: list[Callable] = []

    def register_metric(self, fn: Callable) -> None:
        """Register a custom metric function.

        The function signature must be:
            fn(image: PIL.Image, evaluator: AssetEvaluator) -> MetricResult
        """
        self._custom_metrics.append(fn)

    def evaluate(
        self,
        image: Image.Image,
        reference: Optional[Image.Image] = None,
        palette: Optional[list[tuple[int, int, int]]] = None,
    ) -> EvaluationResult:
        """Evaluate a pixel art image across all quality metrics.

        Parameters
        ----------
        image : PIL.Image
            The image to evaluate. Should be RGBA or RGB.
        reference : PIL.Image, optional
            Override the evaluator-level reference image.
        palette : list of (R, G, B), optional
            Override the evaluator-level palette.

        Returns
        -------
        EvaluationResult
            Complete evaluation with per-metric scores and suggestions.
        """
        ref = reference or self.reference
        pal = palette or self.palette

        # Convert to numpy for processing
        img_arr = np.array(image.convert("RGBA"), dtype=np.float32) / 255.0

        breakdown: dict[QualityMetric, MetricResult] = {}

        # ── Run each metric ──
        breakdown[QualityMetric.SHARPNESS] = self._eval_sharpness(img_arr)
        breakdown[QualityMetric.CONTRAST] = self._eval_contrast(img_arr)
        breakdown[QualityMetric.COLOR_HARMONY] = self._eval_color_harmony(img_arr)

        if pal is not None:
            breakdown[QualityMetric.PALETTE_ADHERENCE] = self._eval_palette_adherence(img_arr, pal)
        else:
            breakdown[QualityMetric.PALETTE_ADHERENCE] = MetricResult(
                QualityMetric.PALETTE_ADHERENCE, 1.0, "No palette specified (skipped)", True
            )

        if ref is not None:
            breakdown[QualityMetric.STYLE_CONSISTENCY] = self._eval_style_consistency(image, ref)
        else:
            breakdown[QualityMetric.STYLE_CONSISTENCY] = MetricResult(
                QualityMetric.STYLE_CONSISTENCY, 1.0, "No reference specified (skipped)", True
            )

        # ── Run custom metrics ──
        for fn in self._custom_metrics:
            result = fn(image, self)
            breakdown[result.metric] = result

        # ── Apply thresholds ──
        for metric, result in breakdown.items():
            threshold = self.thresholds.get(metric, 0.0)
            result.passed = result.score >= threshold

        # ── Compute weighted overall score ──
        total_weight = 0.0
        weighted_sum = 0.0
        for metric, result in breakdown.items():
            w = self.weights.get(metric, 0.0)
            weighted_sum += result.score * w
            total_weight += w

        overall = weighted_sum / total_weight if total_weight > 0 else 0.0
        passed = overall >= self.pass_threshold

        # ── Generate suggestions ──
        suggestions = self._generate_suggestions(breakdown, overall)

        return EvaluationResult(
            overall_score=overall,
            breakdown=breakdown,
            passed=passed,
            suggestions=suggestions,
        )

    # ── Individual Metric Implementations ──

    def _eval_sharpness(self, img_arr: np.ndarray) -> MetricResult:
        """Evaluate pixel-perfect sharpness using Laplacian variance.

        Pixel art should have crisp, high-contrast edges. Blurry images
        (e.g., from bilinear scaling) will have low Laplacian variance.

        Score interpretation:
          > 0.7 : Crisp pixel art edges
          0.4-0.7 : Acceptable, some softness
          < 0.4 : Blurry, likely needs re-generation or sharpening
        """
        # Use luminance channel
        rgb = img_arr[:, :, :3]
        lum = 0.2126 * rgb[:, :, 0] + 0.7152 * rgb[:, :, 1] + 0.0722 * rgb[:, :, 2]

        # Laplacian kernel
        kernel = np.array([[0, -1, 0], [-1, 4, -1], [0, -1, 0]], dtype=np.float32)
        h, w = lum.shape
        if h < 3 or w < 3:
            return MetricResult(QualityMetric.SHARPNESS, 0.5, "Image too small for sharpness check")

        # Manual 2D convolution (avoid scipy dependency)
        padded = np.pad(lum, 1, mode='edge')
        lap = np.zeros_like(lum)
        for i in range(3):
            for j in range(3):
                lap += kernel[i, j] * padded[i:i+h, j:j+w]

        variance = float(np.var(lap))
        # Normalize: typical pixel art has variance 0.01-0.5
        # Score saturates at variance >= 0.05
        score = min(1.0, variance / 0.05)

        detail = f"Laplacian variance={variance:.4f}"
        return MetricResult(QualityMetric.SHARPNESS, score, detail)

    def _eval_contrast(self, img_arr: np.ndarray) -> MetricResult:
        """Evaluate luminance contrast ratio.

        Good pixel art should use the full luminance range and have
        sufficient contrast between foreground and background.

        Uses the standard WCAG contrast ratio formula adapted for images.
        """
        rgb = img_arr[:, :, :3]
        alpha = img_arr[:, :, 3]

        # Only consider non-transparent pixels
        mask = alpha > 0.1
        if mask.sum() < 10:
            return MetricResult(QualityMetric.CONTRAST, 0.5, "Too few opaque pixels")

        lum = 0.2126 * rgb[:, :, 0] + 0.7152 * rgb[:, :, 1] + 0.0722 * rgb[:, :, 2]
        visible_lum = lum[mask]

        l_max = float(np.percentile(visible_lum, 95))
        l_min = float(np.percentile(visible_lum, 5))

        # Michelson contrast
        if l_max + l_min < 1e-6:
            contrast = 0.0
        else:
            contrast = (l_max - l_min) / (l_max + l_min)

        detail = f"Michelson contrast={contrast:.3f} (L_max={l_max:.3f}, L_min={l_min:.3f})"
        return MetricResult(QualityMetric.CONTRAST, contrast, detail)

    def _eval_palette_adherence(
        self,
        img_arr: np.ndarray,
        palette: list[tuple[int, int, int]],
    ) -> MetricResult:
        """Evaluate how well the image colors match the target palette.

        For each pixel, find the nearest palette color (in RGB space).
        Score = fraction of pixels within tolerance distance.

        Tolerance is set to 15 RGB units (≈ 6% of 255), which accounts
        for minor anti-aliasing or dithering effects.
        """
        rgb = img_arr[:, :, :3]
        alpha = img_arr[:, :, 3]
        mask = alpha > 0.1

        if mask.sum() == 0:
            return MetricResult(QualityMetric.PALETTE_ADHERENCE, 1.0, "No opaque pixels")

        pixels = rgb[mask]  # (N, 3) in [0, 1]
        pal_arr = np.array(palette, dtype=np.float32) / 255.0  # (P, 3)

        # Compute distances to all palette colors
        # pixels: (N, 1, 3), pal_arr: (1, P, 3)
        diffs = pixels[:, np.newaxis, :] - pal_arr[np.newaxis, :, :]  # (N, P, 3)
        dists = np.sqrt(np.sum(diffs ** 2, axis=-1))  # (N, P)
        min_dists = np.min(dists, axis=-1)  # (N,)

        # Tolerance in normalized RGB: 15/255 ≈ 0.059
        tolerance = 15.0 / 255.0
        adherent = float(np.mean(min_dists <= tolerance))
        avg_dist = float(np.mean(min_dists))

        detail = (
            f"Palette adherence={adherent:.3f} "
            f"(avg_dist={avg_dist*255:.1f}/255, palette_size={len(palette)})"
        )
        return MetricResult(QualityMetric.PALETTE_ADHERENCE, adherent, detail)

    def _eval_style_consistency(
        self,
        image: Image.Image,
        reference: Image.Image,
    ) -> MetricResult:
        """Evaluate style consistency using perceptual hash (pHash).

        pHash computes a DCT-based fingerprint of the image. Similar images
        have small Hamming distances. We normalize to [0, 1] where 1 = identical.

        Note: pHash is rotation/scale invariant but sensitive to style changes,
        making it suitable for detecting style drift across asset batches.
        """
        hash1 = self._phash(image)
        hash2 = self._phash(reference)

        # Hamming distance
        xor = hash1 ^ hash2
        hamming = bin(xor).count('1')
        max_bits = 64  # 8x8 pHash

        # Convert to similarity score (0 = completely different, 1 = identical)
        similarity = 1.0 - (hamming / max_bits)

        detail = f"pHash Hamming distance={hamming}/{max_bits} (similarity={similarity:.3f})"
        return MetricResult(QualityMetric.STYLE_CONSISTENCY, similarity, detail)

    def _eval_color_harmony(self, img_arr: np.ndarray) -> MetricResult:
        """Evaluate color harmony using OKLAB color distribution analysis.

        Converts pixels to OKLAB space and measures:
        1. Chroma spread (too uniform = boring, too chaotic = harsh)
        2. Hue clustering (harmonious palettes have clustered hues)
        3. Luminance range (good pixel art uses full luminance range)

        Returns a composite harmony score.
        """
        rgb = img_arr[:, :, :3]
        alpha = img_arr[:, :, 3]
        mask = alpha > 0.1

        if mask.sum() < 5:
            return MetricResult(QualityMetric.COLOR_HARMONY, 0.5, "Too few pixels")

        pixels = rgb[mask]

        # Convert to OKLAB (simplified linear sRGB → OKLAB)
        lab = self._rgb_to_oklab(pixels)
        L = lab[:, 0]
        a = lab[:, 1]
        b = lab[:, 2]

        # Chroma
        chroma = np.sqrt(a**2 + b**2)
        chroma_mean = float(np.mean(chroma))
        chroma_std = float(np.std(chroma))

        # Luminance range
        l_range = float(np.max(L) - np.min(L))
        l_range_score = min(1.0, l_range / 0.6)  # Good pixel art uses 60%+ of range

        # Hue clustering: compute hue angles and measure concentration
        hue = np.arctan2(b, a)
        # Circular variance (0 = all same hue, 1 = uniform distribution)
        circ_mean_x = float(np.mean(np.cos(hue)))
        circ_mean_y = float(np.mean(np.sin(hue)))
        circ_r = math.sqrt(circ_mean_x**2 + circ_mean_y**2)  # 0=dispersed, 1=clustered
        # Moderate clustering is good (not all same, not completely random)
        hue_score = 1.0 - abs(circ_r - 0.5) * 2  # Peak at circ_r=0.5

        # Chroma score: moderate chroma is good
        chroma_score = min(1.0, chroma_mean / 0.15) * (1.0 - min(1.0, chroma_std / 0.2))

        harmony = (l_range_score * 0.4 + hue_score * 0.35 + chroma_score * 0.25)

        detail = (
            f"L_range={l_range:.3f}, hue_cluster={circ_r:.3f}, "
            f"chroma={chroma_mean:.3f}±{chroma_std:.3f}"
        )
        return MetricResult(QualityMetric.COLOR_HARMONY, harmony, detail)

    # ── Utility Methods ──

    @staticmethod
    def _phash(image: Image.Image, hash_size: int = 8) -> int:
        """Compute perceptual hash (pHash) of an image.

        Algorithm:
        1. Resize to (hash_size*4) x (hash_size*4) grayscale
        2. Apply DCT (approximated via row/column averaging)
        3. Threshold at median to get binary hash
        """
        # Resize and convert to grayscale
        size = hash_size * 4
        img = image.convert("L").resize((size, size), Image.LANCZOS)
        pixels = np.array(img, dtype=np.float32)

        # Approximate DCT via mean of 4x4 blocks
        blocks = pixels.reshape(hash_size, 4, hash_size, 4)
        dct_approx = blocks.mean(axis=(1, 3))

        # Threshold at median
        median = np.median(dct_approx)
        bits = (dct_approx > median).flatten()

        # Pack into integer
        result = 0
        for bit in bits:
            result = (result << 1) | int(bit)
        return result

    @staticmethod
    def _rgb_to_oklab(rgb: np.ndarray) -> np.ndarray:
        """Convert linear RGB [0,1] to OKLAB.

        Based on Björn Ottosson's OKLAB specification.
        Input: (N, 3) array in [0, 1]
        Output: (N, 3) array [L, a, b]
        """
        r, g, b = rgb[:, 0], rgb[:, 1], rgb[:, 2]

        # Linearize sRGB (approximate)
        def linearize(c):
            return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)

        r_lin = linearize(r)
        g_lin = linearize(g)
        b_lin = linearize(b)

        # Linear sRGB to LMS
        l = 0.4122214708 * r_lin + 0.5363325363 * g_lin + 0.0514459929 * b_lin
        m = 0.2119034982 * r_lin + 0.6806995451 * g_lin + 0.1073969566 * b_lin
        s = 0.0883024619 * r_lin + 0.2817188376 * g_lin + 0.6299787005 * b_lin

        # Cube root
        l_ = np.cbrt(np.maximum(l, 0))
        m_ = np.cbrt(np.maximum(m, 0))
        s_ = np.cbrt(np.maximum(s, 0))

        # LMS to OKLAB
        L = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_
        a = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
        b_out = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_

        return np.stack([L, a, b_out], axis=-1)

    def _generate_suggestions(
        self,
        breakdown: dict[QualityMetric, MetricResult],
        overall: float,
    ) -> list[str]:
        """Generate actionable improvement suggestions based on failed metrics."""
        suggestions = []

        sharpness = breakdown.get(QualityMetric.SHARPNESS)
        if sharpness and not sharpness.passed:
            suggestions.append(
                "Sharpness too low: check for bilinear scaling or blurry source. "
                "Use nearest-neighbor resampling for pixel art."
            )

        palette = breakdown.get(QualityMetric.PALETTE_ADHERENCE)
        if palette and not palette.passed:
            suggestions.append(
                "Colors drifting from palette: run OKLAB quantizer to snap pixels "
                "to nearest palette color, or apply Floyd-Steinberg dithering."
            )

        contrast = breakdown.get(QualityMetric.CONTRAST)
        if contrast and not contrast.passed:
            suggestions.append(
                "Low contrast: increase luminance range. "
                "Ensure darkest shadow and brightest highlight differ by ≥ 40% luminance."
            )

        style = breakdown.get(QualityMetric.STYLE_CONSISTENCY)
        if style and not style.passed:
            suggestions.append(
                "Style inconsistency detected: this asset may not match the reference style. "
                "Check proportions, line weight, and color palette against the style guide."
            )

        harmony = breakdown.get(QualityMetric.COLOR_HARMONY)
        if harmony and not harmony.passed:
            suggestions.append(
                "Color harmony could be improved: use OKLAB palette generator to create "
                "a harmonious palette with warm highlights and cool shadows."
            )

        if overall < 0.4:
            suggestions.append(
                "Overall quality is low. Consider regenerating with different parameters "
                "or increasing the evolutionary optimizer's generation count."
            )

        return suggestions
