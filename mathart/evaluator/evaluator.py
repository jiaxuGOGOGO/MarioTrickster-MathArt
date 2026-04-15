"""Asset Quality Evaluator — pixel-art-specific multi-metric quality scoring.

SESSION-018 REWRITE: Added 7 pixel-art-specific metrics on top of the
original 5 generic metrics.  The evaluator is now strict enough to provide
real evolutionary selection pressure.

New metrics (pixel-art-specific):
  - OUTLINE_CLARITY:   Sobel edge ratio — are outlines crisp & continuous?
  - SHAPE_READABILITY: Foreground compactness — can you tell what it is?
  - FILL_RATIO:        Opaque pixel / canvas ratio — is the shape visible?
  - PALETTE_ECONOMY:   Unique-color-count penalty — fewer = better pixel art
  - DITHER_QUALITY:    Checkerboard-pattern detection — dithering regularity
  - OUTLINE_CONTINUITY: 8-connected boundary gap count
  - INTERNAL_DETAIL:   Variance inside the filled region (not flat fill)

All metrics are pure NumPy, < 50 ms per image.

Design principles:
  - Zero external ML dependencies (pure numpy + PIL)
  - Fast enough for evolutionary optimization (< 50ms per image)
  - Interpretable: every score has a human-readable explanation
  - Extensible: new metrics can be added via register_metric()
  - STRICT: pass_threshold raised to 0.65, individual thresholds raised
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
    # Original metrics
    SHARPNESS = "sharpness"
    PALETTE_ADHERENCE = "palette_adherence"
    CONTRAST = "contrast"
    STYLE_CONSISTENCY = "style_consistency"
    COLOR_HARMONY = "color_harmony"
    # New pixel-art-specific metrics (SESSION-018)
    OUTLINE_CLARITY = "outline_clarity"
    SHAPE_READABILITY = "shape_readability"
    FILL_RATIO = "fill_ratio"
    PALETTE_ECONOMY = "palette_economy"
    DITHER_QUALITY = "dither_quality"
    OUTLINE_CONTINUITY = "outline_continuity"
    INTERNAL_DETAIL = "internal_detail"
    # Aggregate
    RULE_COMPLIANCE = "rule_compliance"
    OVERALL = "overall"


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
    """Complete quality evaluation result for one asset."""
    overall_score: float
    breakdown: dict[QualityMetric, MetricResult] = field(default_factory=dict)
    passed: bool = True
    suggestions: list[str] = field(default_factory=list)

    def __post_init__(self):
        self.overall_score = float(np.clip(self.overall_score, 0.0, 1.0))

    def summary(self) -> str:
        lines = [f"Overall: {self.overall_score:.3f} ({'PASS' if self.passed else 'FAIL'})"]
        for metric, result in self.breakdown.items():
            status = "+" if result.passed else "X"
            lines.append(f"  {status} {metric.value}: {result.score:.3f}  {result.details}")
        if self.suggestions:
            lines.append("Suggestions:")
            for s in self.suggestions:
                lines.append(f"  -> {s}")
        return "\n".join(lines)


# ── Metric weights (must sum to 1.0) ──
# Rebalanced: pixel-art-specific metrics get 40% total weight
_DEFAULT_WEIGHTS: dict[QualityMetric, float] = {
    # Original (60%)
    QualityMetric.SHARPNESS: 0.12,
    QualityMetric.PALETTE_ADHERENCE: 0.10,
    QualityMetric.CONTRAST: 0.12,
    QualityMetric.STYLE_CONSISTENCY: 0.08,
    QualityMetric.COLOR_HARMONY: 0.08,
    # Pixel-art-specific (50%)
    QualityMetric.OUTLINE_CLARITY: 0.08,
    QualityMetric.SHAPE_READABILITY: 0.08,
    QualityMetric.FILL_RATIO: 0.10,
    QualityMetric.PALETTE_ECONOMY: 0.06,
    QualityMetric.DITHER_QUALITY: 0.04,
    QualityMetric.OUTLINE_CONTINUITY: 0.06,
    QualityMetric.INTERNAL_DETAIL: 0.08,
}

# ── Minimum passing thresholds (raised for strictness) ──
_DEFAULT_THRESHOLDS: dict[QualityMetric, float] = {
    QualityMetric.SHARPNESS: 0.45,
    QualityMetric.PALETTE_ADHERENCE: 0.50,
    QualityMetric.CONTRAST: 0.40,
    QualityMetric.STYLE_CONSISTENCY: 0.30,
    QualityMetric.COLOR_HARMONY: 0.30,
    QualityMetric.OUTLINE_CLARITY: 0.35,
    QualityMetric.SHAPE_READABILITY: 0.30,
    QualityMetric.FILL_RATIO: 0.15,
    QualityMetric.PALETTE_ECONOMY: 0.40,
    QualityMetric.DITHER_QUALITY: 0.30,
    QualityMetric.OUTLINE_CONTINUITY: 0.30,
    QualityMetric.INTERNAL_DETAIL: 0.20,
    QualityMetric.OVERALL: 0.65,
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
        Minimum overall score to be considered passing (default 0.65).
    """

    def __init__(
        self,
        palette: Optional[list[tuple[int, int, int]]] = None,
        reference: Optional[Image.Image] = None,
        weights: Optional[dict[QualityMetric, float]] = None,
        thresholds: Optional[dict[QualityMetric, float]] = None,
        pass_threshold: float = 0.65,
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
        """Evaluate a pixel art image across all quality metrics."""
        ref = reference or self.reference
        pal = palette or self.palette

        # Convert to numpy for processing
        img_arr = np.array(image.convert("RGBA"), dtype=np.float32) / 255.0

        breakdown: dict[QualityMetric, MetricResult] = {}

        # ── Original metrics ──
        breakdown[QualityMetric.SHARPNESS] = self._eval_sharpness(img_arr)
        breakdown[QualityMetric.CONTRAST] = self._eval_contrast(img_arr)
        breakdown[QualityMetric.COLOR_HARMONY] = self._eval_color_harmony(img_arr)

        if pal is not None:
            breakdown[QualityMetric.PALETTE_ADHERENCE] = self._eval_palette_adherence(img_arr, pal)
        else:
            # Penalize slightly when no palette — don't give free 1.0
            breakdown[QualityMetric.PALETTE_ADHERENCE] = MetricResult(
                QualityMetric.PALETTE_ADHERENCE, 0.6,
                "No palette specified (default penalty)", True
            )

        if ref is not None:
            breakdown[QualityMetric.STYLE_CONSISTENCY] = self._eval_style_consistency(image, ref)
        else:
            breakdown[QualityMetric.STYLE_CONSISTENCY] = MetricResult(
                QualityMetric.STYLE_CONSISTENCY, 0.5,
                "No reference specified (default penalty)", True
            )

        # ── New pixel-art-specific metrics (SESSION-018) ──
        breakdown[QualityMetric.OUTLINE_CLARITY] = self._eval_outline_clarity(img_arr)
        breakdown[QualityMetric.SHAPE_READABILITY] = self._eval_shape_readability(img_arr)
        breakdown[QualityMetric.FILL_RATIO] = self._eval_fill_ratio(img_arr)
        breakdown[QualityMetric.PALETTE_ECONOMY] = self._eval_palette_economy(img_arr)
        breakdown[QualityMetric.DITHER_QUALITY] = self._eval_dither_quality(img_arr)
        breakdown[QualityMetric.OUTLINE_CONTINUITY] = self._eval_outline_continuity(img_arr)
        breakdown[QualityMetric.INTERNAL_DETAIL] = self._eval_internal_detail(img_arr)

        # ── Run custom metrics ──
        for fn in self._custom_metrics:
            try:
                result = fn(image, self)
                breakdown[result.metric] = result
            except Exception:
                pass

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

    # ═══════════════════════════════════════════════════════════════════════
    # ORIGINAL METRICS
    # ═══════════════════════════════════════════════════════════════════════

    def _eval_sharpness(self, img_arr: np.ndarray) -> MetricResult:
        """Evaluate pixel-perfect sharpness using Laplacian variance."""
        rgb = img_arr[:, :, :3]
        lum = 0.2126 * rgb[:, :, 0] + 0.7152 * rgb[:, :, 1] + 0.0722 * rgb[:, :, 2]

        kernel = np.array([[0, -1, 0], [-1, 4, -1], [0, -1, 0]], dtype=np.float32)
        h, w = lum.shape
        if h < 3 or w < 3:
            return MetricResult(QualityMetric.SHARPNESS, 0.5, "Image too small")

        padded = np.pad(lum, 1, mode='edge')
        lap = np.zeros_like(lum)
        for i in range(3):
            for j in range(3):
                lap += kernel[i, j] * padded[i:i+h, j:j+w]

        variance = float(np.var(lap))
        score = min(1.0, variance / 0.05)

        detail = f"Laplacian variance={variance:.4f}"
        return MetricResult(QualityMetric.SHARPNESS, score, detail)

    def _eval_contrast(self, img_arr: np.ndarray) -> MetricResult:
        """Evaluate luminance contrast ratio (Michelson)."""
        rgb = img_arr[:, :, :3]
        alpha = img_arr[:, :, 3]
        mask = alpha > 0.1
        if mask.sum() < 10:
            return MetricResult(QualityMetric.CONTRAST, 0.3, "Too few opaque pixels")

        lum = 0.2126 * rgb[:, :, 0] + 0.7152 * rgb[:, :, 1] + 0.0722 * rgb[:, :, 2]
        visible_lum = lum[mask]

        l_max = float(np.percentile(visible_lum, 95))
        l_min = float(np.percentile(visible_lum, 5))

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
        """Evaluate how well the image colors match the target palette."""
        rgb = img_arr[:, :, :3]
        alpha = img_arr[:, :, 3]
        mask = alpha > 0.1

        if mask.sum() == 0:
            return MetricResult(QualityMetric.PALETTE_ADHERENCE, 0.5, "No opaque pixels")

        pixels = rgb[mask]
        if hasattr(palette, 'colors_srgb'):
            pal_arr = np.array(palette.colors_srgb, dtype=np.float32) / 255.0
        else:
            pal_arr = np.array(palette, dtype=np.float32) / 255.0

        diffs = pixels[:, np.newaxis, :] - pal_arr[np.newaxis, :, :]
        dists = np.sqrt(np.sum(diffs ** 2, axis=-1))
        min_dists = np.min(dists, axis=-1)

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
        """Evaluate style consistency using perceptual hash (pHash)."""
        hash1 = self._phash(image)
        hash2 = self._phash(reference)

        xor = hash1 ^ hash2
        hamming = bin(xor).count('1')
        max_bits = 64

        similarity = 1.0 - (hamming / max_bits)

        detail = f"pHash Hamming distance={hamming}/{max_bits} (similarity={similarity:.3f})"
        return MetricResult(QualityMetric.STYLE_CONSISTENCY, similarity, detail)

    def _eval_color_harmony(self, img_arr: np.ndarray) -> MetricResult:
        """Evaluate color harmony using OKLAB color distribution analysis."""
        rgb = img_arr[:, :, :3]
        alpha = img_arr[:, :, 3]
        mask = alpha > 0.1

        if mask.sum() < 5:
            return MetricResult(QualityMetric.COLOR_HARMONY, 0.3, "Too few pixels")

        pixels = rgb[mask]
        lab = self._rgb_to_oklab(pixels)
        L = lab[:, 0]
        a = lab[:, 1]
        b = lab[:, 2]

        chroma = np.sqrt(a**2 + b**2)
        chroma_mean = float(np.mean(chroma))
        chroma_std = float(np.std(chroma))

        l_range = float(np.max(L) - np.min(L))
        l_range_score = min(1.0, l_range / 0.6)

        hue = np.arctan2(b, a)
        circ_mean_x = float(np.mean(np.cos(hue)))
        circ_mean_y = float(np.mean(np.sin(hue)))
        circ_r = math.sqrt(circ_mean_x**2 + circ_mean_y**2)
        hue_score = 1.0 - abs(circ_r - 0.5) * 2

        chroma_score = min(1.0, chroma_mean / 0.15) * (1.0 - min(1.0, chroma_std / 0.2))

        harmony = (l_range_score * 0.4 + hue_score * 0.35 + chroma_score * 0.25)

        detail = (
            f"L_range={l_range:.3f}, hue_cluster={circ_r:.3f}, "
            f"chroma={chroma_mean:.3f}+/-{chroma_std:.3f}"
        )
        return MetricResult(QualityMetric.COLOR_HARMONY, harmony, detail)

    # ═══════════════════════════════════════════════════════════════════════
    # NEW PIXEL-ART-SPECIFIC METRICS (SESSION-018)
    # ═══════════════════════════════════════════════════════════════════════

    def _eval_outline_clarity(self, img_arr: np.ndarray) -> MetricResult:
        """Evaluate outline clarity using Sobel edge detection.

        Good pixel art has clear, strong outlines. We measure the ratio of
        strong edge pixels to total foreground pixels. High ratio = crisp outlines.
        """
        alpha = img_arr[:, :, 3]
        mask = alpha > 0.1
        fg_count = int(mask.sum())
        if fg_count < 10:
            return MetricResult(QualityMetric.OUTLINE_CLARITY, 0.2, "Too few pixels")

        lum = (0.2126 * img_arr[:, :, 0] + 0.7152 * img_arr[:, :, 1]
               + 0.0722 * img_arr[:, :, 2])
        h, w = lum.shape
        if h < 3 or w < 3:
            return MetricResult(QualityMetric.OUTLINE_CLARITY, 0.3, "Image too small")

        # Sobel X and Y
        sx = np.array([[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]], dtype=np.float32)
        sy = np.array([[-1, -2, -1], [0, 0, 0], [1, 2, 1]], dtype=np.float32)

        padded = np.pad(lum, 1, mode='edge')
        gx = np.zeros_like(lum)
        gy = np.zeros_like(lum)
        for i in range(3):
            for j in range(3):
                gx += sx[i, j] * padded[i:i+h, j:j+w]
                gy += sy[i, j] * padded[i:i+h, j:j+w]

        magnitude = np.sqrt(gx**2 + gy**2)

        # Also check alpha edges (outline at shape boundary)
        alpha_padded = np.pad(alpha, 1, mode='constant', constant_values=0)
        alpha_gx = np.zeros_like(alpha)
        alpha_gy = np.zeros_like(alpha)
        for i in range(3):
            for j in range(3):
                alpha_gx += sx[i, j] * alpha_padded[i:i+h, j:j+w]
                alpha_gy += sy[i, j] * alpha_padded[i:i+h, j:j+w]
        alpha_mag = np.sqrt(alpha_gx**2 + alpha_gy**2)

        # Combine: strong edges in either luminance or alpha
        combined_edge = np.maximum(magnitude, alpha_mag)
        strong_edges = combined_edge > 0.15
        edge_count = int(strong_edges.sum())

        # Ideal: 15-40% of foreground pixels are edges (outline-heavy)
        edge_ratio = edge_count / max(fg_count, 1)
        # Score peaks at ~25% edge ratio
        if edge_ratio < 0.05:
            score = edge_ratio / 0.05 * 0.4  # Too few edges
        elif edge_ratio < 0.15:
            score = 0.4 + (edge_ratio - 0.05) / 0.10 * 0.4
        elif edge_ratio <= 0.45:
            score = 0.8 + (1.0 - abs(edge_ratio - 0.25) / 0.20) * 0.2
        else:
            score = max(0.3, 1.0 - (edge_ratio - 0.45) * 2)  # Too many edges = noise

        detail = f"edge_ratio={edge_ratio:.3f}, edge_px={edge_count}, fg_px={fg_count}"
        return MetricResult(QualityMetric.OUTLINE_CLARITY, score, detail)

    def _eval_shape_readability(self, img_arr: np.ndarray) -> MetricResult:
        """Evaluate shape readability via compactness and solidity.

        Compactness = 4*pi*area / perimeter^2  (1.0 for circle, lower for complex)
        Good sprites have moderate compactness (not too simple, not too noisy).
        Also checks that the shape is roughly centered.
        """
        alpha = img_arr[:, :, 3]
        mask = (alpha > 0.1).astype(np.uint8)
        h, w = mask.shape
        area = int(mask.sum())

        if area < 5:
            return MetricResult(QualityMetric.SHAPE_READABILITY, 0.1, "Nearly empty")

        # Estimate perimeter: count boundary pixels (4-connected)
        padded = np.pad(mask, 1, mode='constant', constant_values=0)
        boundary = np.zeros_like(mask)
        for di, dj in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            neighbor = padded[1+di:h+1+di, 1+dj:w+1+dj]
            boundary |= (mask == 1) & (neighbor == 0)
        perimeter = int(boundary.sum())

        if perimeter < 4:
            return MetricResult(QualityMetric.SHAPE_READABILITY, 0.3, "Degenerate shape")

        compactness = 4 * math.pi * area / (perimeter ** 2)
        # Ideal compactness for game sprites: 0.2-0.8
        if compactness < 0.05:
            compact_score = compactness / 0.05 * 0.3
        elif compactness < 0.15:
            compact_score = 0.3 + (compactness - 0.05) / 0.10 * 0.4
        elif compactness <= 0.85:
            compact_score = 0.7 + 0.3 * (1.0 - abs(compactness - 0.5) / 0.35)
        else:
            compact_score = 0.7  # Very compact (circle-like), still OK

        # Centering score: center of mass should be near image center
        ys, xs = np.where(mask > 0)
        cx = float(np.mean(xs)) / w
        cy = float(np.mean(ys)) / h
        center_dist = math.sqrt((cx - 0.5)**2 + (cy - 0.5)**2)
        center_score = max(0.0, 1.0 - center_dist * 3)

        score = compact_score * 0.7 + center_score * 0.3

        detail = (f"compactness={compactness:.3f}, center=({cx:.2f},{cy:.2f}), "
                  f"area={area}, perimeter={perimeter}")
        return MetricResult(QualityMetric.SHAPE_READABILITY, score, detail)

    def _eval_fill_ratio(self, img_arr: np.ndarray) -> MetricResult:
        """Evaluate fill ratio: opaque pixels / total canvas pixels.

        Too low = shape is invisible or too small.
        Too high = no transparency, probably not a sprite.
        Ideal range: 15%-75% for game sprites.
        """
        alpha = img_arr[:, :, 3]
        total = alpha.size
        opaque = int((alpha > 0.1).sum())
        ratio = opaque / total

        if ratio < 0.05:
            score = ratio / 0.05 * 0.2  # Nearly invisible
        elif ratio < 0.15:
            score = 0.2 + (ratio - 0.05) / 0.10 * 0.5
        elif ratio <= 0.75:
            score = 0.7 + 0.3 * (1.0 - abs(ratio - 0.40) / 0.35)
        else:
            score = max(0.3, 0.7 - (ratio - 0.75) * 2)  # Too full

        detail = f"fill_ratio={ratio:.3f} ({opaque}/{total} pixels)"
        return MetricResult(QualityMetric.FILL_RATIO, score, detail)

    def _eval_palette_economy(self, img_arr: np.ndarray) -> MetricResult:
        """Evaluate palette economy: good pixel art uses few colors effectively.

        Quantize to 5-bit per channel, count unique colors in opaque region.
        Ideal: 4-16 colors for small sprites, up to 32 for larger ones.
        """
        alpha = img_arr[:, :, 3]
        mask = alpha > 0.1
        if mask.sum() < 5:
            return MetricResult(QualityMetric.PALETTE_ECONOMY, 0.3, "Too few pixels")

        rgb = img_arr[:, :, :3]
        pixels = rgb[mask]

        # Quantize to 5-bit (32 levels per channel)
        quantized = (pixels * 31).astype(np.int32)
        # Pack into single int for unique counting
        packed = quantized[:, 0] * 1024 + quantized[:, 1] * 32 + quantized[:, 2]
        n_colors = len(np.unique(packed))

        h, w = img_arr.shape[:2]
        size = max(h, w)

        # Ideal color count depends on sprite size
        if size <= 32:
            ideal_min, ideal_max = 3, 12
        elif size <= 64:
            ideal_min, ideal_max = 4, 20
        else:
            ideal_min, ideal_max = 6, 32

        if n_colors < ideal_min:
            score = 0.4 + 0.3 * (n_colors / ideal_min)  # Too few
        elif n_colors <= ideal_max:
            score = 0.7 + 0.3 * (1.0 - (n_colors - ideal_min) / max(1, ideal_max - ideal_min))
        else:
            # Penalty for too many colors (not pixel art)
            excess = n_colors - ideal_max
            score = max(0.1, 0.7 - excess * 0.02)

        detail = f"unique_colors={n_colors} (ideal={ideal_min}-{ideal_max} for {size}px)"
        return MetricResult(QualityMetric.PALETTE_ECONOMY, score, detail)

    def _eval_dither_quality(self, img_arr: np.ndarray) -> MetricResult:
        """Evaluate dither quality: detect and score dithering patterns.

        Good dithering has regular patterns (checkerboard, ordered).
        Bad dithering is random noise. No dithering in flat areas is fine.
        """
        alpha = img_arr[:, :, 3]
        mask = alpha > 0.1
        if mask.sum() < 16:
            return MetricResult(QualityMetric.DITHER_QUALITY, 0.5, "Too few pixels")

        lum = (0.2126 * img_arr[:, :, 0] + 0.7152 * img_arr[:, :, 1]
               + 0.0722 * img_arr[:, :, 2])
        h, w = lum.shape

        # Check for alternating pixel patterns (checkerboard)
        if h < 3 or w < 3:
            return MetricResult(QualityMetric.DITHER_QUALITY, 0.5, "Image too small")

        # Horizontal difference
        hdiff = np.abs(lum[:, 1:] - lum[:, :-1])
        # Vertical difference
        vdiff = np.abs(lum[1:, :] - lum[:-1, :])

        # In dithered areas, alternating pixels have high frequency
        # Measure ratio of high-frequency to low-frequency content
        h_high = (hdiff > 0.05).sum()
        v_high = (vdiff > 0.05).sum()
        total_transitions = max(1, hdiff.size + vdiff.size)
        transition_ratio = (h_high + v_high) / total_transitions

        # Check regularity: in good dithering, transitions are evenly spaced
        # For checkerboard: every other pixel differs
        if transition_ratio < 0.05:
            # Very smooth, no dithering needed — that's fine
            score = 0.7
        elif transition_ratio < 0.3:
            # Some texture/dithering — moderate
            score = 0.5 + transition_ratio * 1.0
        elif transition_ratio < 0.6:
            # Heavy dithering — check regularity
            # Regular dithering: horizontal and vertical are balanced
            balance = 1.0 - abs(h_high - v_high) / max(1, h_high + v_high)
            score = 0.4 + balance * 0.4
        else:
            # Too noisy
            score = max(0.2, 0.6 - (transition_ratio - 0.6) * 1.5)

        detail = f"transition_ratio={transition_ratio:.3f}, h_high={h_high}, v_high={v_high}"
        return MetricResult(QualityMetric.DITHER_QUALITY, score, detail)

    def _eval_outline_continuity(self, img_arr: np.ndarray) -> MetricResult:
        """Evaluate outline continuity: count gaps in the shape boundary.

        Good pixel art outlines are continuous (8-connected). Gaps indicate
        rendering errors or poor shape definition.
        """
        alpha = img_arr[:, :, 3]
        mask = (alpha > 0.1).astype(np.uint8)
        h, w = mask.shape

        if mask.sum() < 5:
            return MetricResult(QualityMetric.OUTLINE_CONTINUITY, 0.2, "Too few pixels")

        # Find boundary pixels (4-connected)
        padded = np.pad(mask, 1, mode='constant', constant_values=0)
        boundary = np.zeros_like(mask, dtype=np.uint8)
        for di, dj in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            neighbor = padded[1+di:h+1+di, 1+dj:w+1+dj]
            boundary |= (mask == 1) & (neighbor == 0)

        boundary_count = int(boundary.sum())
        if boundary_count < 4:
            return MetricResult(QualityMetric.OUTLINE_CONTINUITY, 0.5, "Too few boundary pixels")

        # Check 8-connectivity of boundary: each boundary pixel should have
        # at least one 8-connected boundary neighbor
        padded_b = np.pad(boundary, 1, mode='constant', constant_values=0)
        neighbor_count = np.zeros_like(boundary, dtype=np.int32)
        for di in [-1, 0, 1]:
            for dj in [-1, 0, 1]:
                if di == 0 and dj == 0:
                    continue
                neighbor_count += padded_b[1+di:h+1+di, 1+dj:w+1+dj]

        # Boundary pixels with 0 neighbors are isolated (gaps)
        isolated = boundary & (neighbor_count == 0)
        # Boundary pixels with 1 neighbor are endpoints (potential gaps)
        endpoints = boundary & (neighbor_count == 1)

        n_isolated = int(isolated.sum())
        n_endpoints = int(endpoints.sum())
        gap_score = (n_isolated * 2 + n_endpoints) / max(1, boundary_count)

        # Lower gap_score = better continuity
        score = max(0.0, 1.0 - gap_score * 3)

        detail = (f"boundary={boundary_count}px, isolated={n_isolated}, "
                  f"endpoints={n_endpoints}, gap_score={gap_score:.3f}")
        return MetricResult(QualityMetric.OUTLINE_CONTINUITY, score, detail)

    def _eval_internal_detail(self, img_arr: np.ndarray) -> MetricResult:
        """Evaluate internal detail: variance inside the filled region.

        Good pixel art has internal shading, highlights, and texture.
        Pure flat-fill sprites score low. Too much noise also scores low.
        """
        alpha = img_arr[:, :, 3]
        mask = alpha > 0.1
        fg_count = int(mask.sum())

        if fg_count < 10:
            return MetricResult(QualityMetric.INTERNAL_DETAIL, 0.2, "Too few pixels")

        rgb = img_arr[:, :, :3]
        lum = 0.2126 * rgb[:, :, 0] + 0.7152 * rgb[:, :, 1] + 0.0722 * rgb[:, :, 2]

        # Get interior pixels (not on boundary)
        h, w = mask.shape
        mask_uint = mask.astype(np.uint8)
        padded = np.pad(mask_uint, 1, mode='constant', constant_values=0)
        boundary = np.zeros_like(mask_uint)
        for di, dj in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            neighbor = padded[1+di:h+1+di, 1+dj:w+1+dj]
            boundary |= (mask_uint == 1) & (neighbor == 0)

        interior = mask & (boundary == 0)
        interior_count = int(interior.sum())

        if interior_count < 5:
            # Very small sprite, use all foreground pixels
            interior = mask
            interior_count = fg_count

        interior_lum = lum[interior]
        variance = float(np.var(interior_lum))

        # Also check color variance (not just luminance)
        interior_rgb = rgb[interior.astype(bool)]
        color_var = float(np.mean(np.var(interior_rgb, axis=0)))

        combined_var = variance * 0.6 + color_var * 0.4

        # Ideal: moderate variance (0.005 - 0.05)
        if combined_var < 0.001:
            score = combined_var / 0.001 * 0.3  # Flat fill
        elif combined_var < 0.005:
            score = 0.3 + (combined_var - 0.001) / 0.004 * 0.4
        elif combined_var <= 0.06:
            score = 0.7 + 0.3 * (1.0 - abs(combined_var - 0.02) / 0.04)
        else:
            score = max(0.3, 0.7 - (combined_var - 0.06) * 5)  # Too noisy

        detail = (f"lum_var={variance:.4f}, color_var={color_var:.4f}, "
                  f"combined={combined_var:.4f}, interior_px={interior_count}")
        return MetricResult(QualityMetric.INTERNAL_DETAIL, score, detail)

    # ═══════════════════════════════════════════════════════════════════════
    # UTILITY METHODS
    # ═══════════════════════════════════════════════════════════════════════

    @staticmethod
    def _phash(image: Image.Image, hash_size: int = 8) -> int:
        """Compute perceptual hash (pHash) of an image."""
        size = hash_size * 4
        img = image.convert("L").resize((size, size), Image.LANCZOS)
        pixels = np.array(img, dtype=np.float32)

        blocks = pixels.reshape(hash_size, 4, hash_size, 4)
        dct_approx = blocks.mean(axis=(1, 3))

        median = np.median(dct_approx)
        bits = (dct_approx > median).flatten()

        result = 0
        for bit in bits:
            result = (result << 1) | int(bit)
        return result

    @staticmethod
    def _rgb_to_oklab(rgb: np.ndarray) -> np.ndarray:
        """Convert linear RGB [0,1] to OKLAB."""
        r, g, b = rgb[:, 0], rgb[:, 1], rgb[:, 2]

        def linearize(c):
            return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)

        r_lin = linearize(r)
        g_lin = linearize(g)
        b_lin = linearize(b)

        l = 0.4122214708 * r_lin + 0.5363325363 * g_lin + 0.0514459929 * b_lin
        m = 0.2119034982 * r_lin + 0.6806995451 * g_lin + 0.1073969566 * b_lin
        s = 0.0883024619 * r_lin + 0.2817188376 * g_lin + 0.6299787005 * b_lin

        l_ = np.cbrt(np.maximum(l, 0))
        m_ = np.cbrt(np.maximum(m, 0))
        s_ = np.cbrt(np.maximum(s, 0))

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
                "Sharpness too low: use nearest-neighbor resampling for pixel art."
            )

        palette = breakdown.get(QualityMetric.PALETTE_ADHERENCE)
        if palette and not palette.passed:
            suggestions.append(
                "Colors drifting from palette: apply OKLAB quantizer or dithering."
            )

        contrast = breakdown.get(QualityMetric.CONTRAST)
        if contrast and not contrast.passed:
            suggestions.append(
                "Low contrast: increase luminance range between shadow and highlight."
            )

        style = breakdown.get(QualityMetric.STYLE_CONSISTENCY)
        if style and not style.passed:
            suggestions.append(
                "Style inconsistency: check proportions and line weight against reference."
            )

        harmony = breakdown.get(QualityMetric.COLOR_HARMONY)
        if harmony and not harmony.passed:
            suggestions.append(
                "Color harmony low: use OKLAB palette with warm highlights / cool shadows."
            )

        # New metric suggestions
        outline = breakdown.get(QualityMetric.OUTLINE_CLARITY)
        if outline and not outline.passed:
            suggestions.append(
                "Outline unclear: increase outline_width or add darker outline color."
            )

        readability = breakdown.get(QualityMetric.SHAPE_READABILITY)
        if readability and not readability.passed:
            suggestions.append(
                "Shape hard to read: simplify geometry or increase sprite size."
            )

        fill = breakdown.get(QualityMetric.FILL_RATIO)
        if fill and not fill.passed:
            suggestions.append(
                "Fill ratio too low: shape may be too small for canvas. "
                "Increase shape radius or decrease canvas size."
            )

        economy = breakdown.get(QualityMetric.PALETTE_ECONOMY)
        if economy and not economy.passed:
            suggestions.append(
                "Too many colors: quantize to fewer colors for cleaner pixel art."
            )

        continuity = breakdown.get(QualityMetric.OUTLINE_CONTINUITY)
        if continuity and not continuity.passed:
            suggestions.append(
                "Outline has gaps: check SDF rendering threshold or outline width."
            )

        detail = breakdown.get(QualityMetric.INTERNAL_DETAIL)
        if detail and not detail.passed:
            suggestions.append(
                "Internal detail lacking: add shading, highlights, or texture variation."
            )

        if overall < 0.4:
            suggestions.append(
                "Overall quality is low. Consider regenerating with different parameters."
            )

        return suggestions
