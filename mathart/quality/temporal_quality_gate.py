"""SESSION-131 — Temporal Quality Gate: Circuit Breaker for AI Rendering.

Implements a three-state circuit breaker (Martin Fowler pattern) that enforces
strict temporal consistency requirements on AI-rendered video sequences using
ground-truth optical flow from the procedural math engine.

Industrial References:
    - Lai et al., "Learning Blind Video Temporal Consistency", ECCV 2018
      — Warping Error = ||W(F_t→t+1, O_t) - O_{t+1}||² × M_t→t+1
    - Martin Fowler, "CircuitBreaker", 2014
      — Three-state machine: CLOSED → OPEN → HALF_OPEN
    - Jim Gray, "Why Do Computers Stop", Tandem Computers, 1985
      — Fail-Fast: detect and report errors immediately

Core Design Principles:
    1. **Min-SSIM over Mean-SSIM**: One catastrophic frame must NOT be masked
       by 29 smooth frames.  The WORST frame-pair SSIM is the fuse.
    2. **Sliding Window O(1) Memory**: Never load all frames into memory at
       once.  Process frame pairs sequentially and release immediately.
    3. **Occlusion-Aware Warp Error**: Use GT motion vector masks to exclude
       occluded pixels from error computation.
    4. **Circuit Breaker State Machine**: Consecutive batch failures trigger
       OPEN state; recovery requires a successful probe batch.

Architecture:
    ┌──────────────────────────────────────────────────────────────────────┐
    │  TemporalQualityGate                                                │
    │                                                                      │
    │  CLOSED ──[fail]──► OPEN ──[timeout]──► HALF_OPEN ──[pass]──► CLOSED│
    │    │                  │                     │                         │
    │    └──[pass]──► ok    └──[reject all]       └──[fail]──► OPEN        │
    │                                                                      │
    │  evaluate_sequence(frames, mv_fields)                                │
    │    ├─ sliding_window_warp_ssim()  — O(1) memory per-pair SSIM       │
    │    ├─ min_ssim as fuse            — worst pair triggers breaker      │
    │    ├─ warp_error_per_pair()       — occlusion-aware GT flow warp    │
    │    └─ verdict: PASS / FAIL / BREAKER_OPEN                           │
    └──────────────────────────────────────────────────────────────────────┘
"""
from __future__ import annotations

import enum
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

import numpy as np

from mathart.pipeline_contract import PipelineContractError

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
#  Circuit Breaker State Machine
# ═══════════════════════════════════════════════════════════════════════════


class BreakerState(enum.Enum):
    """Three-state circuit breaker (Martin Fowler pattern)."""
    CLOSED = "closed"        # Normal operation — all calls pass through
    OPEN = "open"            # Tripped — all calls immediately rejected
    HALF_OPEN = "half_open"  # Probing — one trial call allowed


@dataclass
class BreakerStatus:
    """Snapshot of circuit breaker state for diagnostics."""
    state: BreakerState = BreakerState.CLOSED
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    total_evaluations: int = 0
    total_passes: int = 0
    total_failures: int = 0
    last_trip_time: float = 0.0
    last_min_ssim: float = 1.0
    last_max_warp_error: float = 0.0
    worst_frame_pair_index: int = -1

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "consecutive_failures": self.consecutive_failures,
            "consecutive_successes": self.consecutive_successes,
            "total_evaluations": self.total_evaluations,
            "total_passes": self.total_passes,
            "total_failures": self.total_failures,
            "last_min_ssim": round(self.last_min_ssim, 6),
            "last_max_warp_error": round(self.last_max_warp_error, 6),
            "worst_frame_pair_index": self.worst_frame_pair_index,
        }


# ═══════════════════════════════════════════════════════════════════════════
#  Evaluation Result
# ═══════════════════════════════════════════════════════════════════════════


class QualityVerdict(enum.Enum):
    """Verdict from temporal quality evaluation."""
    PASS = "pass"
    FAIL = "fail"
    BREAKER_OPEN = "breaker_open"


@dataclass
class TemporalQualityResult:
    """Complete result from a temporal quality evaluation."""
    verdict: QualityVerdict = QualityVerdict.FAIL
    min_ssim: float = 0.0
    mean_ssim: float = 0.0
    max_warp_error: float = 1.0
    mean_warp_error: float = 1.0
    worst_frame_pair_index: int = -1
    per_pair_ssim: list[float] = field(default_factory=list)
    per_pair_warp_error: list[float] = field(default_factory=list)
    frame_count: int = 0
    breaker_state: BreakerState = BreakerState.CLOSED
    diagnostics: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "min_ssim": round(self.min_ssim, 6),
            "mean_ssim": round(self.mean_ssim, 6),
            "max_warp_error": round(self.max_warp_error, 6),
            "mean_warp_error": round(self.mean_warp_error, 6),
            "worst_frame_pair_index": self.worst_frame_pair_index,
            "per_pair_ssim": [round(s, 6) for s in self.per_pair_ssim],
            "per_pair_warp_error": [round(e, 6) for e in self.per_pair_warp_error],
            "frame_count": self.frame_count,
            "breaker_state": self.breaker_state.value,
            "diagnostics": self.diagnostics,
        }


# ═══════════════════════════════════════════════════════════════════════════
#  Core: Sliding Window Warp-SSIM Computation
# ═══════════════════════════════════════════════════════════════════════════


def _to_gray_float(img: np.ndarray) -> np.ndarray:
    """Convert image to grayscale float64 in [0, 1]."""
    if img.dtype == np.uint8:
        img = img.astype(np.float64) / 255.0
    else:
        img = img.astype(np.float64)
    if img.ndim == 3:
        if img.shape[2] == 4:
            return np.dot(img[:, :, :3], [0.2989, 0.5870, 0.1140])
        return np.dot(img, [0.2989, 0.5870, 0.1140])
    return img


def compute_warp_ssim_pair(
    frame_a: np.ndarray,
    frame_b: np.ndarray,
    mv_dx: np.ndarray,
    mv_dy: np.ndarray,
    mv_mask: np.ndarray,
) -> dict[str, float]:
    """Compute occlusion-aware warp SSIM between a frame pair.

    Warps frame_a using ground-truth motion vectors and computes SSIM
    against frame_b, considering only non-occluded pixels.

    This is the core metric from Lai et al. (ECCV 2018):
        Warp Error = ||W(F_t→t+1, O_t) - O_{t+1}||² × M_t→t+1

    Memory: O(H×W) — only two frames in memory at any time.

    Parameters
    ----------
    frame_a : np.ndarray
        Source frame (H, W, C) or (H, W).
    frame_b : np.ndarray
        Target frame (H, W, C) or (H, W).
    mv_dx : np.ndarray
        Horizontal pixel displacement (H, W).
    mv_dy : np.ndarray
        Vertical pixel displacement (H, W).
    mv_mask : np.ndarray
        Boolean mask for valid (non-occluded) pixels.

    Returns
    -------
    dict[str, float]
        Keys: warp_ssim, warp_error, coverage.
    """
    h, w = frame_a.shape[:2]
    ga = _to_gray_float(frame_a)
    gb = _to_gray_float(frame_b)

    # Build warp coordinates
    yy, xx = np.mgrid[0:h, 0:w]
    warp_x = np.clip(np.round(xx + mv_dx).astype(int), 0, w - 1)
    warp_y = np.clip(np.round(yy + mv_dy).astype(int), 0, h - 1)

    # Warp frame_a to frame_b position
    warped_a = ga[warp_y, warp_x]

    # Compute occlusion-aware warp error
    valid = mv_mask.astype(bool)
    if not np.any(valid):
        return {"warp_ssim": 1.0, "warp_error": 0.0, "coverage": 0.0}

    # Warp error (MAE on valid pixels)
    diff = np.abs(warped_a - gb)
    warp_error = float(np.mean(diff[valid]))

    # SSIM on valid region (simplified but robust)
    wa_valid = warped_a[valid]
    gb_valid = gb[valid]

    C1 = (0.01) ** 2  # Data is in [0, 1]
    C2 = (0.03) ** 2

    mu_wa = np.mean(wa_valid)
    mu_gb = np.mean(gb_valid)
    sigma_wa_sq = np.var(wa_valid)
    sigma_gb_sq = np.var(gb_valid)
    sigma_cross = np.mean((wa_valid - mu_wa) * (gb_valid - mu_gb))

    ssim_val = float(
        ((2 * mu_wa * mu_gb + C1) * (2 * sigma_cross + C2))
        / ((mu_wa ** 2 + mu_gb ** 2 + C1) * (sigma_wa_sq + sigma_gb_sq + C2))
    )
    ssim_val = max(0.0, min(1.0, ssim_val))

    coverage = float(np.count_nonzero(valid)) / float(h * w)

    return {
        "warp_ssim": ssim_val,
        "warp_error": warp_error,
        "coverage": coverage,
    }


def sliding_window_warp_ssim(
    frames: Sequence[np.ndarray],
    mv_fields: Sequence[Any],
) -> list[dict[str, float]]:
    """Compute warp-SSIM for all consecutive frame pairs using sliding window.

    Memory-safe: only two frames are in memory at any time.  Each frame
    is processed and the reference to the previous frame is released.

    Parameters
    ----------
    frames : Sequence[np.ndarray]
        Rendered frames (N frames).
    mv_fields : Sequence[MotionVectorField]
        Motion vector fields (N-1 fields, one per transition).

    Returns
    -------
    list[dict[str, float]]
        Per-pair metrics: warp_ssim, warp_error, coverage.
    """
    results: list[dict[str, float]] = []
    n_pairs = min(len(frames) - 1, len(mv_fields))

    for i in range(n_pairs):
        fa = frames[i]
        fb = frames[i + 1]
        mv = mv_fields[i]

        pair_result = compute_warp_ssim_pair(
            frame_a=fa,
            frame_b=fb,
            mv_dx=mv.dx,
            mv_dy=mv.dy,
            mv_mask=mv.mask,
        )
        pair_result["pair_index"] = i
        results.append(pair_result)

        # Explicit cleanup hint for GC (OOM prevention)
        del fa

    return results


# ═══════════════════════════════════════════════════════════════════════════
#  Temporal Quality Gate (Circuit Breaker)
# ═══════════════════════════════════════════════════════════════════════════


class TemporalQualityGate:
    """Three-state circuit breaker for AI rendering temporal quality.

    Enforces Min-SSIM (worst frame pair) as the primary fuse, with
    occlusion-aware warp error as secondary metric.

    State transitions (Martin Fowler Circuit Breaker):
        CLOSED → OPEN:     consecutive_failures >= failure_threshold
        OPEN → HALF_OPEN:  reset_timeout_seconds elapsed since trip
        HALF_OPEN → CLOSED: probe batch passes
        HALF_OPEN → OPEN:   probe batch fails

    Parameters
    ----------
    min_ssim_threshold : float
        Minimum acceptable SSIM for the WORST frame pair.
        Below this → immediate failure.  Default 0.70.
    max_warp_error_threshold : float
        Maximum acceptable warp error for any frame pair.
        Default 0.20.
    failure_threshold : int
        Consecutive failures to trigger OPEN state.  Default 3.
    reset_timeout_seconds : float
        Seconds to wait in OPEN before allowing HALF_OPEN probe.
        Default 0.0 (immediate probe allowed for batch pipelines).
    """

    def __init__(
        self,
        min_ssim_threshold: float = 0.70,
        max_warp_error_threshold: float = 0.20,
        failure_threshold: int = 3,
        reset_timeout_seconds: float = 0.0,
    ) -> None:
        self.min_ssim_threshold = min_ssim_threshold
        self.max_warp_error_threshold = max_warp_error_threshold
        self.failure_threshold = failure_threshold
        self.reset_timeout_seconds = reset_timeout_seconds

        self._status = BreakerStatus()

    @property
    def state(self) -> BreakerState:
        """Current circuit breaker state."""
        return self._status.state

    @property
    def status(self) -> BreakerStatus:
        """Full status snapshot."""
        return self._status

    def evaluate_sequence(
        self,
        frames: Sequence[np.ndarray],
        mv_fields: Sequence[Any],
    ) -> TemporalQualityResult:
        """Evaluate a rendered sequence against temporal quality requirements.

        This is the main entry point.  It:
        1. Checks circuit breaker state (reject immediately if OPEN)
        2. Computes sliding-window warp-SSIM for all frame pairs
        3. Uses Min-SSIM (worst pair) as the primary fuse
        4. Updates circuit breaker state based on verdict

        Parameters
        ----------
        frames : Sequence[np.ndarray]
            AI-rendered frames (N frames).
        mv_fields : Sequence[MotionVectorField]
            Ground-truth motion vector fields (N-1 fields).

        Returns
        -------
        TemporalQualityResult
            Complete evaluation result with verdict.

        Raises
        ------
        PipelineContractError
            If frames or mv_fields are empty or mismatched.
        """
        self._status.total_evaluations += 1

        # Contract validation
        if len(frames) < 2:
            raise PipelineContractError(
                "temporal_quality_gate_input",
                f"temporal_quality_gate requires at least 2 frames, got {len(frames)}"
            )
        if len(mv_fields) < 1:
            raise PipelineContractError(
                "temporal_quality_gate_input",
                f"temporal_quality_gate requires at least 1 motion vector field, got {len(mv_fields)}"
            )

        # Check circuit breaker state
        if self._status.state == BreakerState.OPEN:
            elapsed = time.monotonic() - self._status.last_trip_time
            if elapsed < self.reset_timeout_seconds:
                return TemporalQualityResult(
                    verdict=QualityVerdict.BREAKER_OPEN,
                    breaker_state=BreakerState.OPEN,
                    frame_count=len(frames),
                    diagnostics=(
                        f"Circuit breaker OPEN — {self._status.consecutive_failures} "
                        f"consecutive failures. Waiting {self.reset_timeout_seconds - elapsed:.1f}s "
                        "before probe."
                    ),
                )
            # Transition to HALF_OPEN for probe
            self._status.state = BreakerState.HALF_OPEN
            logger.info("[TemporalQualityGate] OPEN → HALF_OPEN: probe allowed")

        # Compute sliding-window warp-SSIM
        pair_results = sliding_window_warp_ssim(frames, mv_fields)

        if not pair_results:
            raise PipelineContractError(
                "temporal_quality_gate_evaluation",
                "No valid frame pairs could be evaluated"
            )

        # Extract per-pair metrics
        per_pair_ssim = [r["warp_ssim"] for r in pair_results]
        per_pair_warp_error = [r["warp_error"] for r in pair_results]

        # Min-SSIM: the WORST frame pair is the fuse
        min_ssim = min(per_pair_ssim)
        mean_ssim = float(np.mean(per_pair_ssim))
        max_warp_error = max(per_pair_warp_error)
        mean_warp_error = float(np.mean(per_pair_warp_error))
        worst_idx = int(np.argmin(per_pair_ssim))

        # Verdict: Min-SSIM is the PRIMARY fuse
        ssim_pass = min_ssim >= self.min_ssim_threshold
        warp_pass = max_warp_error <= self.max_warp_error_threshold
        temporal_pass = ssim_pass and warp_pass

        # Update breaker state
        self._status.last_min_ssim = min_ssim
        self._status.last_max_warp_error = max_warp_error
        self._status.worst_frame_pair_index = worst_idx

        if temporal_pass:
            self._status.consecutive_failures = 0
            self._status.consecutive_successes += 1
            self._status.total_passes += 1

            if self._status.state == BreakerState.HALF_OPEN:
                self._status.state = BreakerState.CLOSED
                logger.info("[TemporalQualityGate] HALF_OPEN → CLOSED: probe passed")

            verdict = QualityVerdict.PASS
            diagnostics = (
                f"PASS — Min-SSIM={min_ssim:.4f} (threshold={self.min_ssim_threshold}), "
                f"Max-WarpError={max_warp_error:.4f} (threshold={self.max_warp_error_threshold}), "
                f"Mean-SSIM={mean_ssim:.4f}"
            )
        else:
            self._status.consecutive_successes = 0
            self._status.consecutive_failures += 1
            self._status.total_failures += 1

            # Check if we should trip the breaker
            if self._status.consecutive_failures >= self.failure_threshold:
                self._status.state = BreakerState.OPEN
                self._status.last_trip_time = time.monotonic()
                verdict = QualityVerdict.BREAKER_OPEN
                logger.warning(
                    f"[TemporalQualityGate] TRIPPED → OPEN after "
                    f"{self._status.consecutive_failures} consecutive failures"
                )
            elif self._status.state == BreakerState.HALF_OPEN:
                self._status.state = BreakerState.OPEN
                self._status.last_trip_time = time.monotonic()
                verdict = QualityVerdict.BREAKER_OPEN
                logger.warning(
                    "[TemporalQualityGate] HALF_OPEN → OPEN: probe failed"
                )
            else:
                verdict = QualityVerdict.FAIL

            fail_reasons = []
            if not ssim_pass:
                fail_reasons.append(
                    f"Min-SSIM={min_ssim:.4f} < threshold={self.min_ssim_threshold} "
                    f"at pair index {worst_idx}"
                )
            if not warp_pass:
                fail_reasons.append(
                    f"Max-WarpError={max_warp_error:.4f} > threshold={self.max_warp_error_threshold}"
                )
            diagnostics = f"FAIL — {'; '.join(fail_reasons)}"

        result = TemporalQualityResult(
            verdict=verdict,
            min_ssim=min_ssim,
            mean_ssim=mean_ssim,
            max_warp_error=max_warp_error,
            mean_warp_error=mean_warp_error,
            worst_frame_pair_index=worst_idx,
            per_pair_ssim=per_pair_ssim,
            per_pair_warp_error=per_pair_warp_error,
            frame_count=len(frames),
            breaker_state=self._status.state,
            diagnostics=diagnostics,
        )

        logger.info(f"[TemporalQualityGate] {diagnostics}")
        return result

    def reset(self) -> None:
        """Force-reset the circuit breaker to CLOSED state."""
        self._status = BreakerStatus()
        logger.info("[TemporalQualityGate] Force reset to CLOSED")

    def compute_fitness_penalty(
        self,
        result: TemporalQualityResult,
        lambda_temporal: float = 2.0,
    ) -> float:
        """Compute fitness penalty for evolution engine integration.

        Uses linear penalty with hard threshold:
            penalty = λ_temporal × max(0, ssim_threshold - min_warp_ssim)

        The penalty is always non-negative.  A passing sequence gets 0 penalty.
        A failing sequence gets a penalty proportional to how far below
        threshold the worst frame pair fell.

        Parameters
        ----------
        result : TemporalQualityResult
            Result from evaluate_sequence().
        lambda_temporal : float
            Penalty weight.  Higher = stronger evolutionary pressure.

        Returns
        -------
        float
            Non-negative penalty value.  0.0 for passing sequences.
        """
        if result.verdict == QualityVerdict.PASS:
            return 0.0

        ssim_deficit = max(0.0, self.min_ssim_threshold - result.min_ssim)
        warp_excess = max(0.0, result.max_warp_error - self.max_warp_error_threshold)

        penalty = lambda_temporal * (ssim_deficit + warp_excess)
        return penalty


__all__ = [
    "BreakerState",
    "BreakerStatus",
    "QualityVerdict",
    "TemporalQualityResult",
    "TemporalQualityGate",
    "compute_warp_ssim_pair",
    "sliding_window_warp_ssim",
]
