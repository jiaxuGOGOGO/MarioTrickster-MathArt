"""Multi-Modal Visual Fitness Scoring for Genetic Evolution.

SESSION-055: Battle 4 — NR-IQA for Genetic Evolution

This module upgrades the Layer 3 (Optuna) evaluation function from a
physics-only score into a **multi-modal visual fitness** score that combines
physics quality with perceptual image quality metrics.  The design follows
the SESSION-055 research direction:

- **Laplacian Variance** penalises high-frequency noise in normal maps and
  rewards sharp, clean edges — the sweet-spot approach avoids both blurry
  and noisy outputs.
- **SSIM** (Wang et al. 2004) measures frame-to-frame temporal consistency,
  penalising geometric deformation between adjacent animation frames.
- **Depth/Thickness/Roughness channel quality** ensures industrial material
  maps maintain meaningful dynamic range.

The combined fitness function can be plugged into any Optuna objective or
evolution loop to let the engine "self-evolve the most beautiful polygon
topology".

Design references:

- Wang et al., "Image quality assessment: From error visibility to
  structural similarity," IEEE TIP, 2004.
- BRISQUE NR-IQA (Mittal et al., 2012) — spatial-domain NSS features.
- Laplacian Variance for blur/noise detection — OpenCV standard practice.
- Dead Cells industrial sprite pipeline — material bundle quality gates.

Usage::

    from mathart.quality.visual_fitness import (
        compute_visual_fitness,
        compute_laplacian_sharpness,
        compute_frame_ssim,
    )

    score = compute_visual_fitness(frames, aux_maps)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

import numpy as np


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class VisualFitnessConfig:
    """Weights and thresholds for multi-modal visual fitness scoring."""

    # Laplacian variance sweet-spot for normal maps
    laplacian_low: float = 50.0       # Below this = too blurry
    laplacian_high: float = 5000.0    # Above this = too noisy
    laplacian_optimal: float = 500.0  # Sweet spot

    # SSIM temporal consistency
    ssim_weight: float = 0.25
    ssim_min_acceptable: float = 0.85  # Below this = too much deformation

    # Physics score weight
    physics_weight: float = 0.30

    # Laplacian quality weight
    laplacian_weight: float = 0.20

    # Depth map quality weight
    depth_weight: float = 0.10

    # Thickness/roughness channel weight
    channel_weight: float = 0.15


@dataclass
class VisualFitnessResult:
    """Complete visual fitness evaluation result."""

    overall_score: float = 0.0
    physics_score: float = 0.0
    laplacian_score: float = 0.0
    ssim_temporal_score: float = 0.0
    depth_quality_score: float = 0.0
    channel_quality_score: float = 0.0
    laplacian_variance: float = 0.0
    mean_ssim: float = 0.0
    depth_range: float = 0.0
    thickness_range: float = 0.0
    roughness_range: float = 0.0
    frame_count: int = 0
    accepted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_score": round(self.overall_score, 4),
            "physics_score": round(self.physics_score, 4),
            "laplacian_score": round(self.laplacian_score, 4),
            "ssim_temporal_score": round(self.ssim_temporal_score, 4),
            "depth_quality_score": round(self.depth_quality_score, 4),
            "channel_quality_score": round(self.channel_quality_score, 4),
            "laplacian_variance": round(self.laplacian_variance, 4),
            "mean_ssim": round(self.mean_ssim, 4),
            "depth_range": round(self.depth_range, 4),
            "thickness_range": round(self.thickness_range, 4),
            "roughness_range": round(self.roughness_range, 4),
            "frame_count": self.frame_count,
            "accepted": self.accepted,
        }


# ---------------------------------------------------------------------------
# Core Metrics
# ---------------------------------------------------------------------------


def compute_laplacian_sharpness(image: np.ndarray) -> float:
    """Compute Laplacian variance as a sharpness/noise metric.

    The Laplacian operator highlights regions of rapid intensity change.
    Its variance across the image gives a single scalar:

    - **Low variance** → blurry image (no edges)
    - **Moderate variance** → sharp, clean edges (optimal)
    - **Very high variance** → high-frequency noise

    Parameters
    ----------
    image : np.ndarray
        Input image, shape (H, W) or (H, W, C).  If multi-channel,
        converted to grayscale via luminance weights.

    Returns
    -------
    float
        Laplacian variance (higher = sharper, but very high = noisy).
    """
    if image.ndim == 3:
        if image.shape[2] == 4:  # RGBA
            gray = np.dot(image[:, :, :3].astype(np.float64),
                          [0.2989, 0.5870, 0.1140])
        else:  # RGB
            gray = np.dot(image.astype(np.float64),
                          [0.2989, 0.5870, 0.1140])
    else:
        gray = image.astype(np.float64)

    # Laplacian kernel (discrete approximation)
    # [0  1  0]
    # [1 -4  1]
    # [0  1  0]
    h, w = gray.shape
    if h < 3 or w < 3:
        return 0.0

    # Pad for convolution
    padded = np.pad(gray, 1, mode="edge")
    laplacian = (
        padded[:-2, 1:-1]   # top
        + padded[2:, 1:-1]  # bottom
        + padded[1:-1, :-2] # left
        + padded[1:-1, 2:]  # right
        - 4.0 * padded[1:-1, 1:-1]
    )

    return float(np.var(laplacian))


def compute_laplacian_quality(
    variance: float,
    *,
    low: float = 50.0,
    high: float = 5000.0,
    optimal: float = 500.0,
) -> float:
    """Convert Laplacian variance into a 0-1 quality score.

    Uses a Gaussian-like penalty centered on the optimal variance.
    Too low (blurry) or too high (noisy) both reduce the score.

    Parameters
    ----------
    variance : float
        Laplacian variance from ``compute_laplacian_sharpness()``.
    low : float
        Below this, the image is considered too blurry.
    high : float
        Above this, the image is considered too noisy.
    optimal : float
        The sweet-spot variance for maximum quality.

    Returns
    -------
    float
        Quality score in [0, 1].
    """
    if variance <= 0:
        return 0.0

    # Log-space Gaussian penalty
    log_var = np.log1p(variance)
    log_opt = np.log1p(optimal)
    log_low = np.log1p(low)
    log_high = np.log1p(high)

    # Sigma: distance from optimal to boundary (whichever is farther)
    sigma = max(abs(log_opt - log_low), abs(log_high - log_opt), 1e-8)
    score = np.exp(-0.5 * ((log_var - log_opt) / sigma) ** 2)

    return float(np.clip(score, 0.0, 1.0))


def compute_frame_ssim(
    frame_a: np.ndarray,
    frame_b: np.ndarray,
) -> float:
    """Compute SSIM between two frames for temporal consistency.

    Uses a simplified SSIM implementation that works without scikit-image.
    For production use, scikit-image's ``structural_similarity`` is preferred.

    Parameters
    ----------
    frame_a, frame_b : np.ndarray
        Two frames of shape (H, W) or (H, W, C).

    Returns
    -------
    float
        SSIM score in [0, 1].  Higher = more similar.
    """
    # Convert to grayscale if needed
    def _to_gray(img: np.ndarray) -> np.ndarray:
        if img.ndim == 3:
            if img.shape[2] == 4:
                return np.dot(img[:, :, :3].astype(np.float64),
                              [0.2989, 0.5870, 0.1140])
            return np.dot(img.astype(np.float64),
                          [0.2989, 0.5870, 0.1140])
        return img.astype(np.float64)

    try:
        from skimage.metrics import structural_similarity as ssim
        g1 = _to_gray(frame_a)
        g2 = _to_gray(frame_b)
        if g1.shape != g2.shape:
            return 0.0
        win_size = min(7, min(g1.shape[0], g1.shape[1]))
        if win_size % 2 == 0:
            win_size = max(win_size - 1, 3)
        if win_size < 3:
            win_size = 3
        if g1.shape[0] < win_size or g1.shape[1] < win_size:
            # Image too small for SSIM window
            return float(1.0 - np.mean(np.abs(g1 - g2)) / 255.0)
        return float(ssim(g1, g2, data_range=255.0, win_size=win_size))
    except ImportError:
        pass

    # Fallback: simplified SSIM
    g1 = _to_gray(frame_a)
    g2 = _to_gray(frame_b)
    if g1.shape != g2.shape:
        return 0.0

    C1 = (0.01 * 255) ** 2
    C2 = (0.03 * 255) ** 2

    mu1 = np.mean(g1)
    mu2 = np.mean(g2)
    sigma1_sq = np.var(g1)
    sigma2_sq = np.var(g2)
    sigma12 = np.mean((g1 - mu1) * (g2 - mu2))

    ssim_val = ((2 * mu1 * mu2 + C1) * (2 * sigma12 + C2)) / (
        (mu1 ** 2 + mu2 ** 2 + C1) * (sigma1_sq + sigma2_sq + C2)
    )
    return float(np.clip(ssim_val, 0.0, 1.0))


def compute_temporal_consistency(
    frames: Sequence[np.ndarray],
) -> float:
    """Compute mean SSIM across consecutive frame pairs.

    Parameters
    ----------
    frames : Sequence[np.ndarray]
        List of animation frames.

    Returns
    -------
    float
        Mean SSIM across all consecutive pairs.  1.0 if fewer than 2 frames.
    """
    if len(frames) < 2:
        return 1.0

    ssim_scores = []
    for i in range(len(frames) - 1):
        score = compute_frame_ssim(frames[i], frames[i + 1])
        ssim_scores.append(score)

    return float(np.mean(ssim_scores))


def compute_channel_dynamic_range(channel: np.ndarray) -> float:
    """Compute the dynamic range of a material channel (0-1 normalized).

    A channel with zero dynamic range (flat) is useless for downstream
    rendering.  This metric ensures thickness, roughness, and depth maps
    contain meaningful variation.

    Parameters
    ----------
    channel : np.ndarray
        2D array representing a material channel.

    Returns
    -------
    float
        Dynamic range in [0, 1].  0 = flat, 1 = full range.
    """
    if channel.size == 0:
        return 0.0

    ch = channel.astype(np.float64)
    if ch.max() > 1.0:
        ch = ch / 255.0

    val_range = float(ch.max() - ch.min())
    return min(val_range, 1.0)


def compute_depth_smoothness(depth_map: np.ndarray) -> float:
    """Compute depth map smoothness via gradient magnitude.

    A good depth map should have smooth gradients without sudden jumps
    (which indicate rendering artifacts).

    Parameters
    ----------
    depth_map : np.ndarray
        2D depth map.

    Returns
    -------
    float
        Smoothness score in [0, 1].  Higher = smoother.
    """
    if depth_map.size == 0 or depth_map.ndim < 2:
        return 0.0

    d = depth_map.astype(np.float64)
    if d.max() > 1.0:
        d = d / 255.0

    h, w = d.shape[:2]
    if h < 2 or w < 2:
        return 1.0

    # Compute gradient magnitude
    if d.ndim == 3:
        d = d[:, :, 0]

    grad_x = np.diff(d, axis=1)
    grad_y = np.diff(d, axis=0)

    # Trim to same size
    min_h = min(grad_x.shape[0], grad_y.shape[0])
    min_w = min(grad_x.shape[1], grad_y.shape[1])
    grad_x = grad_x[:min_h, :min_w]
    grad_y = grad_y[:min_h, :min_w]

    grad_mag = np.sqrt(grad_x ** 2 + grad_y ** 2)

    # Smoothness: inverse of mean gradient magnitude
    # Normalize so that perfectly flat = 1.0
    mean_grad = float(np.mean(grad_mag))
    smoothness = 1.0 / (1.0 + 10.0 * mean_grad)

    return float(np.clip(smoothness, 0.0, 1.0))


# ---------------------------------------------------------------------------
# Combined Visual Fitness
# ---------------------------------------------------------------------------


def compute_visual_fitness(
    frames: Optional[Sequence[np.ndarray]] = None,
    normal_maps: Optional[Sequence[np.ndarray]] = None,
    depth_maps: Optional[Sequence[np.ndarray]] = None,
    thickness_maps: Optional[Sequence[np.ndarray]] = None,
    roughness_maps: Optional[Sequence[np.ndarray]] = None,
    physics_score: float = 0.0,
    config: Optional[VisualFitnessConfig] = None,
) -> VisualFitnessResult:
    """Compute the combined multi-modal visual fitness score.

    This is the main entry point for the NR-IQA genetic evolution fitness
    function.  It combines:

    1. **Physics score** (from existing Optuna/evolution loop)
    2. **Laplacian quality** (normal map sharpness sweet-spot)
    3. **SSIM temporal consistency** (frame-to-frame stability)
    4. **Depth map quality** (smoothness + dynamic range)
    5. **Channel quality** (thickness + roughness dynamic range)

    Parameters
    ----------
    frames : Sequence[np.ndarray], optional
        Animation frames for temporal consistency.
    normal_maps : Sequence[np.ndarray], optional
        Normal maps for Laplacian quality.
    depth_maps : Sequence[np.ndarray], optional
        Depth maps for smoothness scoring.
    thickness_maps : Sequence[np.ndarray], optional
        Thickness channel maps.
    roughness_maps : Sequence[np.ndarray], optional
        Roughness channel maps.
    physics_score : float
        Physics quality score from the existing pipeline.
    config : VisualFitnessConfig, optional
        Scoring configuration.

    Returns
    -------
    VisualFitnessResult
        Complete fitness evaluation with per-component scores.
    """
    cfg = config or VisualFitnessConfig()
    result = VisualFitnessResult()
    result.physics_score = float(np.clip(physics_score, 0.0, 1.0))

    # --- Laplacian quality (normal maps) ---
    if normal_maps and len(normal_maps) > 0:
        variances = [compute_laplacian_sharpness(nm) for nm in normal_maps]
        mean_var = float(np.mean(variances))
        result.laplacian_variance = mean_var
        result.laplacian_score = compute_laplacian_quality(
            mean_var,
            low=cfg.laplacian_low,
            high=cfg.laplacian_high,
            optimal=cfg.laplacian_optimal,
        )
    else:
        result.laplacian_score = 0.5  # Neutral if no normal maps

    # --- SSIM temporal consistency ---
    if frames and len(frames) >= 2:
        result.mean_ssim = compute_temporal_consistency(frames)
        # Scale: below min_acceptable gets penalized
        if result.mean_ssim >= cfg.ssim_min_acceptable:
            result.ssim_temporal_score = result.mean_ssim
        else:
            result.ssim_temporal_score = result.mean_ssim * 0.5
    else:
        result.ssim_temporal_score = 0.5
        result.mean_ssim = 1.0

    # --- Depth map quality ---
    if depth_maps and len(depth_maps) > 0:
        smoothness_scores = [compute_depth_smoothness(dm) for dm in depth_maps]
        range_scores = [compute_channel_dynamic_range(dm) for dm in depth_maps]
        result.depth_range = float(np.mean(range_scores))
        result.depth_quality_score = float(
            0.5 * np.mean(smoothness_scores) + 0.5 * np.mean(range_scores)
        )
    else:
        result.depth_quality_score = 0.5

    # --- Channel quality (thickness + roughness) ---
    channel_scores = []
    if thickness_maps and len(thickness_maps) > 0:
        t_ranges = [compute_channel_dynamic_range(tm) for tm in thickness_maps]
        result.thickness_range = float(np.mean(t_ranges))
        channel_scores.extend(t_ranges)
    if roughness_maps and len(roughness_maps) > 0:
        r_ranges = [compute_channel_dynamic_range(rm) for rm in roughness_maps]
        result.roughness_range = float(np.mean(r_ranges))
        channel_scores.extend(r_ranges)
    if channel_scores:
        result.channel_quality_score = float(np.mean(channel_scores))
    else:
        result.channel_quality_score = 0.5

    # --- Frame count ---
    result.frame_count = len(frames) if frames else 0

    # --- Combined score ---
    result.overall_score = float(np.clip(
        cfg.physics_weight * result.physics_score
        + cfg.laplacian_weight * result.laplacian_score
        + cfg.ssim_weight * result.ssim_temporal_score
        + cfg.depth_weight * result.depth_quality_score
        + cfg.channel_weight * result.channel_quality_score,
        0.0, 1.0,
    ))

    # Acceptance: overall score above 0.5 and no critical failures
    result.accepted = (
        result.overall_score >= 0.5
        and result.laplacian_score >= 0.3
        and result.ssim_temporal_score >= 0.3
    )

    return result


# ---------------------------------------------------------------------------
# Pytest Integration
# ---------------------------------------------------------------------------


def test_laplacian_sharpness_basic():
    """Laplacian variance is positive for non-flat images."""
    _rng = np.random.default_rng(77)
    img = _rng.integers(0, 255, (32, 32), dtype=np.uint8)
    var = compute_laplacian_sharpness(img)
    assert var > 0, f"Expected positive variance, got {var}"


def test_laplacian_sharpness_flat():
    """Laplacian variance is zero for flat images."""
    img = np.full((32, 32), 128, dtype=np.uint8)
    var = compute_laplacian_sharpness(img)
    assert var < 1.0, f"Expected near-zero variance for flat image, got {var}"


def test_laplacian_quality_sweet_spot():
    """Quality score is highest at the optimal variance."""
    low_score = compute_laplacian_quality(10.0)
    opt_score = compute_laplacian_quality(500.0)
    high_score = compute_laplacian_quality(50000.0)
    assert opt_score > low_score, "Optimal should beat low"
    assert opt_score > high_score, "Optimal should beat high"
    assert opt_score > 0.8, f"Optimal score should be high, got {opt_score}"


def test_frame_ssim_identical():
    """SSIM of identical frames should be ~1.0."""
    _rng = np.random.default_rng(77)
    frame = _rng.integers(0, 255, (32, 32, 4), dtype=np.uint8)
    score = compute_frame_ssim(frame, frame)
    assert score > 0.99, f"Self-SSIM should be ~1.0, got {score}"


def test_frame_ssim_different():
    """SSIM of very different frames should be low."""
    f1 = np.zeros((32, 32, 4), dtype=np.uint8)
    f2 = np.full((32, 32, 4), 255, dtype=np.uint8)
    score = compute_frame_ssim(f1, f2)
    assert score < 0.5, f"SSIM of black vs white should be low, got {score}"


def test_temporal_consistency():
    """Temporal consistency of similar frames should be high."""
    _rng = np.random.default_rng(77)
    base = _rng.integers(50, 200, (32, 32, 3), dtype=np.uint8)
    frames = [base + _rng.integers(-5, 5, base.shape).astype(np.uint8)
              for _ in range(5)]
    score = compute_temporal_consistency(frames)
    assert score > 0.8, f"Similar frames should have high consistency, got {score}"


def test_channel_dynamic_range():
    """Dynamic range of a gradient should be ~1.0."""
    channel = np.linspace(0, 255, 32 * 32).reshape(32, 32).astype(np.uint8)
    dr = compute_channel_dynamic_range(channel)
    assert dr > 0.9, f"Full gradient should have high dynamic range, got {dr}"


def test_channel_dynamic_range_flat():
    """Dynamic range of a flat channel should be 0."""
    channel = np.full((32, 32), 128, dtype=np.uint8)
    dr = compute_channel_dynamic_range(channel)
    assert dr < 0.01, f"Flat channel should have zero dynamic range, got {dr}"


def test_depth_smoothness():
    """Smooth depth map should score high."""
    # Create a smooth gradient
    depth = np.tile(np.linspace(0, 1, 32), (32, 1))
    score = compute_depth_smoothness(depth)
    assert score > 0.5, f"Smooth gradient should score high, got {score}"


def test_visual_fitness_combined():
    """Combined visual fitness produces a valid score."""
    _rng = np.random.default_rng(77)
    frames = [_rng.integers(50, 200, (32, 32, 4), dtype=np.uint8)
              for _ in range(5)]
    normals = [_rng.integers(0, 255, (32, 32, 3), dtype=np.uint8)
               for _ in range(5)]
    depths = [np.tile(np.linspace(0, 255, 32), (32, 1)).astype(np.uint8)
              for _ in range(5)]

    result = compute_visual_fitness(
        frames=frames,
        normal_maps=normals,
        depth_maps=depths,
        physics_score=0.85,
    )
    assert 0.0 <= result.overall_score <= 1.0
    assert result.frame_count == 5
    assert result.physics_score == 0.85


def test_visual_fitness_no_inputs():
    """Visual fitness with no inputs returns neutral scores."""
    result = compute_visual_fitness(physics_score=0.5)
    assert 0.0 <= result.overall_score <= 1.0
    assert result.physics_score == 0.5


def test_visual_fitness_acceptance():
    """Visual fitness acceptance gate works correctly."""
    # Good inputs: similar consecutive frames (small perturbation)
    rng = np.random.default_rng(42)
    base = rng.integers(50, 200, (32, 32, 4), dtype=np.uint8)
    frames = [
        np.clip(base.astype(np.int16) + rng.integers(-3, 4, base.shape), 0, 255).astype(np.uint8)
        for _ in range(5)
    ]
    # Create smooth normal maps (gradient-based, not random noise)
    # This produces moderate Laplacian variance in the sweet spot
    gx = np.tile(np.linspace(100, 200, 32), (32, 1)).astype(np.uint8)
    gy = np.tile(np.linspace(100, 200, 32).reshape(-1, 1), (1, 32)).astype(np.uint8)
    flat_z = np.full((32, 32), 200, dtype=np.uint8)
    normal_base = np.stack([gx, gy, flat_z], axis=-1)
    normals = [
        np.clip(normal_base.astype(np.int16) + rng.integers(-5, 6, normal_base.shape), 0, 255).astype(np.uint8)
        for _ in range(5)
    ]
    result = compute_visual_fitness(
        frames=frames, normal_maps=normals, physics_score=0.9,
    )
    assert result.accepted, f"Good inputs should be accepted: {result.to_dict()}"
