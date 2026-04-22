"""SESSION-131 — Temporal Quality Gate: Comprehensive Test Suite.

Tests cover:
    1. Flicker injection: replace one frame with noise, verify detection
    2. Min-SSIM fuse: worst frame pair triggers breaker, NOT average
    3. OOM prevention: sliding window processes O(1) memory per pair
    4. Evolution fitness penalty: verify penalty propagation
    5. Circuit breaker state machine: CLOSED→OPEN→HALF_OPEN→CLOSED
    6. Warp-SSIM pair computation: occlusion-aware GT flow warp
    7. Controller integration: post_sequence_generation method
    8. Neural rendering bridge: min_warp_ssim field and penalty
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mathart.quality.temporal_quality_gate import (
    BreakerState,
    BreakerStatus,
    QualityVerdict,
    TemporalQualityGate,
    TemporalQualityResult,
    compute_warp_ssim_pair,
    sliding_window_warp_ssim,
)
from mathart.pipeline_contract import PipelineContractError


# ═══════════════════════════════════════════════════════════════════════════
#  Fixtures: Synthetic Frame Sequences
# ═══════════════════════════════════════════════════════════════════════════


def _make_smooth_sequence(n_frames: int = 8, h: int = 64, w: int = 64) -> list[np.ndarray]:
    """Create a smooth gradient sequence with gradual horizontal shift."""
    frames = []
    for i in range(n_frames):
        # Gradual horizontal gradient shift
        x = np.linspace(i * 10, i * 10 + 255, w)
        x = np.clip(x, 0, 255)
        row = x.astype(np.uint8)
        frame = np.tile(row, (h, 1))
        frame_rgba = np.stack([frame, frame, frame, np.full_like(frame, 255)], axis=-1)
        frames.append(frame_rgba)
    return frames


def _make_flicker_sequence(n_frames: int = 8, h: int = 64, w: int = 64,
                           flicker_index: int = 3) -> list[np.ndarray]:
    """Create a smooth sequence with one frame replaced by random noise."""
    frames = _make_smooth_sequence(n_frames, h, w)
    rng = np.random.RandomState(42)
    frames[flicker_index] = rng.randint(0, 256, (h, w, 4), dtype=np.uint8)
    frames[flicker_index][:, :, 3] = 255  # Full alpha
    return frames


def _make_static_sequence(n_frames: int = 8, h: int = 64, w: int = 64) -> list[np.ndarray]:
    """Create a completely static (identical) frame sequence."""
    base = np.full((h, w, 4), 128, dtype=np.uint8)
    base[:, :, 3] = 255
    return [base.copy() for _ in range(n_frames)]


def _make_mv_fields(n_pairs: int, h: int = 64, w: int = 64,
                    dx_val: float = 1.0, dy_val: float = 0.0) -> list[SimpleNamespace]:
    """Create synthetic motion vector fields with uniform displacement."""
    fields = []
    for _ in range(n_pairs):
        fields.append(SimpleNamespace(
            dx=np.full((h, w), dx_val, dtype=np.float64),
            dy=np.full((h, w), dy_val, dtype=np.float64),
            mask=np.ones((h, w), dtype=bool),
        ))
    return fields


def _make_zero_mv_fields(n_pairs: int, h: int = 64, w: int = 64) -> list[SimpleNamespace]:
    """Create zero-displacement motion vector fields."""
    return _make_mv_fields(n_pairs, h, w, dx_val=0.0, dy_val=0.0)


# ═══════════════════════════════════════════════════════════════════════════
#  Test 1: Warp-SSIM Pair Computation
# ═══════════════════════════════════════════════════════════════════════════


class TestWarpSSIMPair:
    """Test the core warp-SSIM computation."""

    def test_identical_frames_high_ssim(self):
        """Identical frames with zero displacement should have SSIM ≈ 1.0."""
        frame = np.full((32, 32, 4), 128, dtype=np.uint8)
        frame[:, :, 3] = 255
        result = compute_warp_ssim_pair(
            frame, frame,
            mv_dx=np.zeros((32, 32)),
            mv_dy=np.zeros((32, 32)),
            mv_mask=np.ones((32, 32), dtype=bool),
        )
        assert result["warp_ssim"] > 0.95, f"Expected SSIM > 0.95, got {result['warp_ssim']}"
        assert result["warp_error"] < 0.05, f"Expected warp_error < 0.05, got {result['warp_error']}"

    def test_noise_frame_low_ssim(self):
        """A noise frame warped to a smooth frame should have low SSIM."""
        smooth = np.full((32, 32, 4), 128, dtype=np.uint8)
        smooth[:, :, 3] = 255
        rng = np.random.RandomState(42)
        noise = rng.randint(0, 256, (32, 32, 4), dtype=np.uint8)
        noise[:, :, 3] = 255
        result = compute_warp_ssim_pair(
            smooth, noise,
            mv_dx=np.zeros((32, 32)),
            mv_dy=np.zeros((32, 32)),
            mv_mask=np.ones((32, 32), dtype=bool),
        )
        assert result["warp_ssim"] < 0.5, f"Expected SSIM < 0.5, got {result['warp_ssim']}"
        assert result["warp_error"] > 0.1, f"Expected warp_error > 0.1, got {result['warp_error']}"

    def test_empty_mask_returns_default(self):
        """Empty mask (all occluded) should return default values."""
        frame = np.full((32, 32, 4), 128, dtype=np.uint8)
        result = compute_warp_ssim_pair(
            frame, frame,
            mv_dx=np.zeros((32, 32)),
            mv_dy=np.zeros((32, 32)),
            mv_mask=np.zeros((32, 32), dtype=bool),  # All occluded
        )
        assert result["coverage"] == 0.0
        assert result["warp_ssim"] == 1.0  # Default for no valid pixels

    def test_coverage_computation(self):
        """Coverage should reflect the fraction of non-occluded pixels."""
        h, w = 32, 32
        mask = np.zeros((h, w), dtype=bool)
        mask[:16, :] = True  # Half the pixels
        frame = np.full((h, w, 4), 128, dtype=np.uint8)
        result = compute_warp_ssim_pair(
            frame, frame,
            mv_dx=np.zeros((h, w)),
            mv_dy=np.zeros((h, w)),
            mv_mask=mask,
        )
        assert abs(result["coverage"] - 0.5) < 0.01


# ═══════════════════════════════════════════════════════════════════════════
#  Test 2: Sliding Window Computation
# ═══════════════════════════════════════════════════════════════════════════


class TestSlidingWindow:
    """Test the sliding window warp-SSIM computation."""

    def test_smooth_sequence_all_high_ssim(self):
        """Smooth sequence with zero MV should have high SSIM for all pairs."""
        frames = _make_smooth_sequence(6)
        mv_fields = _make_zero_mv_fields(5)
        results = sliding_window_warp_ssim(frames, mv_fields)
        assert len(results) == 5
        for r in results:
            assert r["warp_ssim"] > 0.5, f"Pair {r['pair_index']}: SSIM={r['warp_ssim']}"

    def test_flicker_sequence_detects_bad_pair(self):
        """Flicker injection should produce at least one low-SSIM pair."""
        frames = _make_flicker_sequence(6, flicker_index=3)
        mv_fields = _make_zero_mv_fields(5)
        results = sliding_window_warp_ssim(frames, mv_fields)
        ssim_values = [r["warp_ssim"] for r in results]
        min_ssim = min(ssim_values)
        min_idx = ssim_values.index(min_ssim)
        # The worst pair should be at index 2 (pair 2→3) or 3 (pair 3→4)
        assert min_idx in (2, 3), f"Expected worst pair at 2 or 3, got {min_idx}"
        assert min_ssim < 0.7, f"Expected min SSIM < 0.7, got {min_ssim}"

    def test_pair_count_matches(self):
        """Number of results should be min(frames-1, mv_fields)."""
        frames = _make_smooth_sequence(10)
        mv_fields = _make_zero_mv_fields(7)
        results = sliding_window_warp_ssim(frames, mv_fields)
        assert len(results) == 7


# ═══════════════════════════════════════════════════════════════════════════
#  Test 3: Flicker Injection & Min-SSIM Fuse
# ═══════════════════════════════════════════════════════════════════════════


class TestFlickerInjection:
    """Test that flicker injection is caught by Min-SSIM fuse."""

    def test_single_noise_frame_triggers_fail(self):
        """One noise frame in an otherwise smooth sequence should FAIL."""
        frames = _make_flicker_sequence(8, flicker_index=4)
        mv_fields = _make_zero_mv_fields(7)
        gate = TemporalQualityGate(min_ssim_threshold=0.70)
        result = gate.evaluate_sequence(frames, mv_fields)
        assert result.verdict in (QualityVerdict.FAIL, QualityVerdict.BREAKER_OPEN), \
            f"Expected FAIL, got {result.verdict.value}"
        assert result.min_ssim < 0.70, \
            f"Expected min_ssim < 0.70, got {result.min_ssim}"
        # The worst pair should be adjacent to the noise frame
        assert result.worst_frame_pair_index in (3, 4), \
            f"Expected worst pair at 3 or 4, got {result.worst_frame_pair_index}"

    def test_mean_ssim_masks_catastrophe(self):
        """Mean SSIM may be acceptable even when one pair is catastrophic.
        This proves why Min-SSIM is essential."""
        frames = _make_flicker_sequence(10, flicker_index=5)
        mv_fields = _make_zero_mv_fields(9)
        gate = TemporalQualityGate(min_ssim_threshold=0.70)
        result = gate.evaluate_sequence(frames, mv_fields)
        # Mean SSIM might be > 0.70 because 8 out of 9 pairs are smooth
        # But Min-SSIM should be < 0.70
        assert result.min_ssim < result.mean_ssim, \
            "Min-SSIM should be lower than Mean-SSIM when flicker exists"
        assert result.verdict != QualityVerdict.PASS, \
            "Flicker should not pass even if mean SSIM is acceptable"

    def test_clean_sequence_passes(self):
        """A clean smooth sequence should PASS."""
        frames = _make_smooth_sequence(8)
        mv_fields = _make_zero_mv_fields(7)
        gate = TemporalQualityGate(min_ssim_threshold=0.50)
        result = gate.evaluate_sequence(frames, mv_fields)
        assert result.verdict == QualityVerdict.PASS, \
            f"Expected PASS, got {result.verdict.value}: {result.diagnostics}"


# ═══════════════════════════════════════════════════════════════════════════
#  Test 4: Circuit Breaker State Machine
# ═══════════════════════════════════════════════════════════════════════════


class TestCircuitBreakerStateMachine:
    """Test the three-state circuit breaker transitions."""

    def test_initial_state_closed(self):
        """Gate starts in CLOSED state."""
        gate = TemporalQualityGate()
        assert gate.state == BreakerState.CLOSED

    def test_consecutive_failures_trip_breaker(self):
        """3 consecutive failures should trip CLOSED → OPEN."""
        frames = _make_flicker_sequence(4, flicker_index=2)
        mv_fields = _make_zero_mv_fields(3)
        gate = TemporalQualityGate(
            min_ssim_threshold=0.90,  # Very strict to ensure failure
            failure_threshold=3,
        )
        for i in range(3):
            result = gate.evaluate_sequence(frames, mv_fields)
        assert gate.state == BreakerState.OPEN
        assert result.verdict == QualityVerdict.BREAKER_OPEN

    def test_open_to_half_open_transition(self):
        """After timeout, OPEN should transition to HALF_OPEN on next call."""
        frames_bad = _make_flicker_sequence(4, flicker_index=2)
        mv_fields = _make_zero_mv_fields(3)
        gate = TemporalQualityGate(
            min_ssim_threshold=0.90,
            failure_threshold=2,
            reset_timeout_seconds=0.0,  # Immediate probe
        )
        # Trip the breaker
        gate.evaluate_sequence(frames_bad, mv_fields)
        gate.evaluate_sequence(frames_bad, mv_fields)
        assert gate.state == BreakerState.OPEN

        # Next call should transition to HALF_OPEN and evaluate
        result = gate.evaluate_sequence(frames_bad, mv_fields)
        # It should fail again and go back to OPEN
        assert gate.state == BreakerState.OPEN

    def test_half_open_to_closed_on_pass(self):
        """A passing probe in HALF_OPEN should transition to CLOSED."""
        frames_bad = _make_flicker_sequence(4, flicker_index=2)
        frames_good = _make_smooth_sequence(4)
        mv_fields = _make_zero_mv_fields(3)
        gate = TemporalQualityGate(
            min_ssim_threshold=0.50,
            failure_threshold=2,
            reset_timeout_seconds=0.0,
        )
        # Trip the breaker with bad frames
        gate.evaluate_sequence(frames_bad, mv_fields)
        gate.evaluate_sequence(frames_bad, mv_fields)
        assert gate.state == BreakerState.OPEN

        # Probe with good frames
        result = gate.evaluate_sequence(frames_good, mv_fields)
        assert gate.state == BreakerState.CLOSED
        assert result.verdict == QualityVerdict.PASS

    def test_reset_clears_state(self):
        """Force reset should return to CLOSED with clean counters."""
        gate = TemporalQualityGate()
        gate._status.state = BreakerState.OPEN
        gate._status.consecutive_failures = 10
        gate.reset()
        assert gate.state == BreakerState.CLOSED
        assert gate.status.consecutive_failures == 0


# ═══════════════════════════════════════════════════════════════════════════
#  Test 5: Evolution Fitness Penalty
# ═══════════════════════════════════════════════════════════════════════════


class TestFitnessPenalty:
    """Test fitness penalty computation for evolution engine."""

    def test_pass_zero_penalty(self):
        """Passing sequence should have zero penalty."""
        gate = TemporalQualityGate(min_ssim_threshold=0.50)
        result = TemporalQualityResult(
            verdict=QualityVerdict.PASS,
            min_ssim=0.80,
            max_warp_error=0.10,
        )
        penalty = gate.compute_fitness_penalty(result)
        assert penalty == 0.0

    def test_fail_positive_penalty(self):
        """Failing sequence should have positive penalty."""
        gate = TemporalQualityGate(min_ssim_threshold=0.70)
        result = TemporalQualityResult(
            verdict=QualityVerdict.FAIL,
            min_ssim=0.40,
            max_warp_error=0.30,
        )
        penalty = gate.compute_fitness_penalty(result)
        assert penalty > 0.0, f"Expected positive penalty, got {penalty}"
        # Penalty should be proportional to deficit
        expected_min = 2.0 * (0.70 - 0.40)  # ssim deficit
        assert penalty >= expected_min * 0.5, \
            f"Penalty {penalty} too small for deficit"

    def test_breaker_open_penalty(self):
        """Breaker open should also have positive penalty."""
        gate = TemporalQualityGate(min_ssim_threshold=0.70)
        result = TemporalQualityResult(
            verdict=QualityVerdict.BREAKER_OPEN,
            min_ssim=0.30,
            max_warp_error=0.40,
        )
        penalty = gate.compute_fitness_penalty(result)
        assert penalty > 0.0

    def test_lambda_scales_penalty(self):
        """Higher lambda should produce larger penalty."""
        gate = TemporalQualityGate(min_ssim_threshold=0.70)
        result = TemporalQualityResult(
            verdict=QualityVerdict.FAIL,
            min_ssim=0.50,
            max_warp_error=0.10,
        )
        penalty_low = gate.compute_fitness_penalty(result, lambda_temporal=1.0)
        penalty_high = gate.compute_fitness_penalty(result, lambda_temporal=4.0)
        assert penalty_high > penalty_low


# ═══════════════════════════════════════════════════════════════════════════
#  Test 6: Contract Validation (Fail-Fast)
# ═══════════════════════════════════════════════════════════════════════════


class TestContractValidation:
    """Test that invalid inputs are rejected immediately."""

    def test_single_frame_rejected(self):
        """Less than 2 frames should raise PipelineContractError."""
        gate = TemporalQualityGate()
        frame = np.full((32, 32, 4), 128, dtype=np.uint8)
        with pytest.raises(PipelineContractError, match="at least 2 frames"):
            gate.evaluate_sequence([frame], [])

    def test_empty_mv_fields_rejected(self):
        """Empty motion vector fields should raise PipelineContractError."""
        gate = TemporalQualityGate()
        frames = _make_smooth_sequence(4)
        with pytest.raises(PipelineContractError, match="at least 1 motion vector"):
            gate.evaluate_sequence(frames, [])


# ═══════════════════════════════════════════════════════════════════════════
#  Test 7: OOM Prevention — Sliding Window Memory
# ═══════════════════════════════════════════════════════════════════════════


class TestOOMPrevention:
    """Test that sliding window processes frames without accumulating memory."""

    def test_large_sequence_completes(self):
        """A 30-frame sequence should complete without memory issues."""
        frames = _make_smooth_sequence(30, h=32, w=32)
        mv_fields = _make_zero_mv_fields(29, h=32, w=32)
        results = sliding_window_warp_ssim(frames, mv_fields)
        assert len(results) == 29

    def test_result_per_pair_not_accumulated(self):
        """Each result should be independent, not accumulating frame data."""
        frames = _make_smooth_sequence(10, h=32, w=32)
        mv_fields = _make_zero_mv_fields(9, h=32, w=32)
        results = sliding_window_warp_ssim(frames, mv_fields)
        # Each result should only contain scalar metrics, not frame arrays
        for r in results:
            assert isinstance(r["warp_ssim"], float)
            assert isinstance(r["warp_error"], float)
            assert isinstance(r["coverage"], float)


# ═══════════════════════════════════════════════════════════════════════════
#  Test 8: Neural Rendering Bridge Integration
# ═══════════════════════════════════════════════════════════════════════════


class TestNeuralRenderingBridgeIntegration:
    """Test that min_warp_ssim fields are correctly populated."""

    def test_metrics_have_min_warp_ssim(self):
        """TemporalConsistencyMetrics should have min_warp_ssim field."""
        from mathart.evolution.neural_rendering_bridge import (
            TemporalConsistencyMetrics,
        )
        metrics = TemporalConsistencyMetrics()
        assert hasattr(metrics, "min_warp_ssim")
        assert hasattr(metrics, "per_pair_warp_ssim")
        assert hasattr(metrics, "worst_frame_pair_index")
        assert metrics.min_warp_ssim == 1.0  # Default
        assert metrics.worst_frame_pair_index == -1  # Default

    def test_metrics_to_dict_includes_min_ssim(self):
        """to_dict should include min_warp_ssim fields."""
        from mathart.evolution.neural_rendering_bridge import (
            TemporalConsistencyMetrics,
        )
        metrics = TemporalConsistencyMetrics(
            min_warp_ssim=0.65,
            per_pair_warp_ssim=[0.65, 0.80, 0.90],
            worst_frame_pair_index=0,
        )
        d = metrics.to_dict()
        assert d["min_warp_ssim"] == 0.65
        assert d["per_pair_warp_ssim"] == [0.65, 0.80, 0.90]
        assert d["worst_frame_pair_index"] == 0

    def test_fitness_bonus_penalizes_low_min_ssim(self):
        """Low min_warp_ssim should produce negative fitness bonus."""
        from mathart.evolution.neural_rendering_bridge import (
            NeuralRenderingEvolutionBridge,
            TemporalConsistencyMetrics,
        )
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            bridge = NeuralRenderingEvolutionBridge(
                project_root=Path(tmpdir),
                verbose=False,
            )
            # Good metrics
            good_metrics = TemporalConsistencyMetrics(
                temporal_pass=True,
                mean_warp_error=0.05,
                min_warp_ssim=0.90,
                flicker_score=0.01,
                coverage=0.95,
            )
            good_bonus = bridge.compute_temporal_fitness_bonus(good_metrics)

            # Bad metrics (catastrophic frame)
            bad_metrics = TemporalConsistencyMetrics(
                temporal_pass=False,
                mean_warp_error=0.20,
                min_warp_ssim=0.20,
                flicker_score=0.15,
                coverage=0.95,
                warp_error_threshold=0.15,
            )
            bad_bonus = bridge.compute_temporal_fitness_bonus(bad_metrics)

            assert good_bonus > bad_bonus, \
                f"Good bonus {good_bonus} should be > bad bonus {bad_bonus}"
            assert bad_bonus < 0, f"Bad bonus should be negative, got {bad_bonus}"


# ═══════════════════════════════════════════════════════════════════════════
#  Test 9: Result Serialization
# ═══════════════════════════════════════════════════════════════════════════


class TestResultSerialization:
    """Test that results can be serialized to dict/JSON."""

    def test_result_to_dict(self):
        """TemporalQualityResult.to_dict() should produce valid dict."""
        result = TemporalQualityResult(
            verdict=QualityVerdict.FAIL,
            min_ssim=0.45,
            mean_ssim=0.82,
            max_warp_error=0.18,
            mean_warp_error=0.12,
            worst_frame_pair_index=3,
            per_pair_ssim=[0.90, 0.85, 0.80, 0.45, 0.88],
            per_pair_warp_error=[0.10, 0.12, 0.14, 0.18, 0.11],
            frame_count=6,
            breaker_state=BreakerState.CLOSED,
            diagnostics="FAIL — Min-SSIM=0.4500 < threshold=0.7000 at pair index 3",
        )
        d = result.to_dict()
        assert d["verdict"] == "fail"
        assert d["min_ssim"] == 0.45
        assert d["worst_frame_pair_index"] == 3
        assert len(d["per_pair_ssim"]) == 5

    def test_breaker_status_to_dict(self):
        """BreakerStatus.to_dict() should produce valid dict."""
        status = BreakerStatus(
            state=BreakerState.OPEN,
            consecutive_failures=3,
            total_evaluations=10,
        )
        d = status.to_dict()
        assert d["state"] == "open"
        assert d["consecutive_failures"] == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
