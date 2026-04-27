"""E2E Test Suite — P1-AI-2E: Motion-Adaptive Keyframe Planning.

SESSION-091: Comprehensive test coverage for:
1. Nonlinearity score computation (Clavet feature vector)
2. Adaptive keyframe selection (anti-Extrema-Omission, anti-Void, anti-Cluster)
3. SparseCtrl end_percent mapping (Guo et al., ECCV 2024)
4. Full backend E2E (UMR frames → ArtifactManifest)
5. Orchestrator hot-reload coordination (Stale Cache Leak guard)
6. ComfyUI Client reload resilience
7. SafePointExecutionLock (Mid-Frame Reload Trap guard)
8. Three-layer evolution bridge
9. Configuration validation

Anti-Pattern Guards (Red Line Assertions):
- 🚫 Stale Cache Leak Trap: Assert old KEYFRAME_PLAN purged after reload
- 🚫 Extrema Omission & Void Trap: Assert no gap > max_gap, all contacts captured
- 🚫 Mid-Frame Reload Trap: Assert no AttributeError during concurrent reload
"""
from __future__ import annotations

import importlib
import math
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest


TEST_RNG_SEED = 42


def make_rng(seed: int = TEST_RNG_SEED) -> np.random.Generator:
    """Return an isolated RNG for a single test context."""
    return np.random.default_rng(seed)


def make_random_scores(length: int, seed: int = TEST_RNG_SEED) -> np.ndarray:
    """Create a deterministic high-variance score vector for a single test."""
    return make_rng(seed).random(length).astype(np.float64)


# ---------------------------------------------------------------------------
# Helpers: Generate synthetic UMR clips
# ---------------------------------------------------------------------------

def make_smooth_walk_clip(n_frames: int = 48, fps: int = 12) -> list[dict[str, Any]]:
    """Generate a smooth walking UMR clip with periodic contact events."""
    frames: list[dict] = []
    for i in range(n_frames):
        t = i / fps
        phase = (i / n_frames) % 1.0
        # Smooth sinusoidal walk
        vx = 1.0 + 0.2 * math.sin(2 * math.pi * phase)
        vy = 0.1 * math.cos(2 * math.pi * phase)
        aw = 0.05 * math.sin(4 * math.pi * phase)
        # Alternating foot contacts every ~6 frames
        left_foot = (i % 12) < 6
        right_foot = not left_foot
        frames.append({
            "time": t,
            "phase": phase,
            "frame_index": i,
            "root_transform": {
                "x": vx * t,
                "y": vy * t,
                "rotation": 0.0,
                "velocity_x": vx,
                "velocity_y": vy,
                "angular_velocity": aw,
            },
            "contact_tags": {
                "left_foot": left_foot,
                "right_foot": right_foot,
            },
            "joint_local_rotations": {"hip": 0.0, "knee": 0.0},
            "metadata": {"joint_channel_schema": "2d_scalar"},
        })
    return frames


def make_high_nonlinearity_clip(n_frames: int = 48, fps: int = 12) -> list[dict[str, Any]]:
    """Generate a high-nonlinearity clip with sudden acceleration and contact events.

    Simulates: walk → sudden jump → hitstop → landing → walk.
    This is the Guilty Gear Xrd discipline test case.
    """
    frames: list[dict] = []
    for i in range(n_frames):
        t = i / fps
        phase = (i / n_frames) % 1.0

        if i < 12:
            # Walk phase: smooth
            vx = 1.0
            vy = 0.0
            aw = 0.0
            left_foot = (i % 6) < 3
            right_foot = not left_foot
        elif i < 18:
            # Jump phase: sudden upward acceleration
            vx = 1.5 + (i - 12) * 0.5
            vy = 3.0 + (i - 12) * 1.0
            aw = 0.5 * (i - 12)
            left_foot = False
            right_foot = False
        elif i < 22:
            # Hitstop phase: sudden deceleration (Guilty Gear Xrd)
            vx = 0.1
            vy = -0.5
            aw = -2.0
            left_foot = False
            right_foot = False
        elif i < 28:
            # Landing phase: contact event
            vx = 0.5
            vy = -2.0 + (i - 22) * 0.5
            aw = 0.3
            left_foot = i >= 24
            right_foot = i >= 26
        else:
            # Recovery walk
            vx = 1.0
            vy = 0.0
            aw = 0.0
            left_foot = (i % 6) < 3
            right_foot = not left_foot

        frames.append({
            "time": t,
            "phase": phase,
            "frame_index": i,
            "root_transform": {
                "x": 0.0,
                "y": 0.0,
                "rotation": 0.0,
                "velocity_x": vx,
                "velocity_y": vy,
                "angular_velocity": aw,
            },
            "contact_tags": {
                "left_foot": left_foot,
                "right_foot": right_foot,
            },
            "joint_local_rotations": {"hip": 0.0},
            "metadata": {"joint_channel_schema": "2d_scalar"},
        })
    return frames


def make_constant_clip(n_frames: int = 30) -> list[dict[str, Any]]:
    """Generate a constant-velocity clip (zero nonlinearity)."""
    return [
        {
            "time": i / 12.0,
            "phase": 0.0,
            "frame_index": i,
            "root_transform": {
                "velocity_x": 1.0,
                "velocity_y": 0.0,
                "angular_velocity": 0.0,
            },
            "contact_tags": {"left_foot": True, "right_foot": True},
            "joint_local_rotations": {},
            "metadata": {},
        }
        for i in range(n_frames)
    ]


# ===========================================================================
# Test Group 1: Nonlinearity Score Computation
# ===========================================================================

class TestNonlinearityScores:
    """Test the core nonlinearity score computation algorithm."""

    def test_constant_velocity_zero_score(self):
        """Constant velocity → all scores should be zero."""
        from mathart.core.motion_adaptive_keyframe_backend import (
            compute_nonlinearity_scores,
            extract_signals_from_umr_frames,
        )
        frames = make_constant_clip(30)
        vx, vy, aw, cl, cr = extract_signals_from_umr_frames(frames)
        scores = compute_nonlinearity_scores(vx, vy, aw, cl, cr)
        assert scores.shape == (30,)
        assert np.allclose(scores, 0.0), f"Expected all zeros, got {scores}"

    def test_high_nonlinearity_has_peaks(self):
        """High-nonlinearity clip → scores should have peaks at transitions."""
        from mathart.core.motion_adaptive_keyframe_backend import (
            compute_nonlinearity_scores,
            extract_signals_from_umr_frames,
        )
        frames = make_high_nonlinearity_clip(48)
        vx, vy, aw, cl, cr = extract_signals_from_umr_frames(frames)
        scores = compute_nonlinearity_scores(vx, vy, aw, cl, cr)
        assert scores.shape == (48,)
        # Peak should be in the jump/hitstop region (frames 12-22)
        peak_idx = int(np.argmax(scores))
        assert 10 <= peak_idx <= 28, f"Peak at {peak_idx}, expected in [10, 28]"
        assert scores.max() > 0.5, f"Max score {scores.max()} too low"

    def test_scores_normalized_0_1(self):
        """All scores must be in [0, 1]."""
        from mathart.core.motion_adaptive_keyframe_backend import (
            compute_nonlinearity_scores,
            extract_signals_from_umr_frames,
        )
        frames = make_high_nonlinearity_clip(48)
        vx, vy, aw, cl, cr = extract_signals_from_umr_frames(frames)
        scores = compute_nonlinearity_scores(vx, vy, aw, cl, cr)
        assert np.all(scores >= 0.0), "Scores below 0"
        assert np.all(scores <= 1.0), "Scores above 1"

    def test_single_frame_clip(self):
        """Single-frame clip → score should be zero (no diff possible)."""
        from mathart.core.motion_adaptive_keyframe_backend import (
            compute_nonlinearity_scores,
        )
        scores = compute_nonlinearity_scores(
            np.array([1.0]), np.array([0.0]), np.array([0.0]),
            np.array([1.0]), np.array([0.0]),
        )
        assert scores.shape == (1,)
        assert scores[0] == 0.0

    def test_custom_weights(self):
        """Custom weights should change the score distribution."""
        from mathart.core.motion_adaptive_keyframe_backend import (
            compute_nonlinearity_scores,
            extract_signals_from_umr_frames,
        )
        frames = make_high_nonlinearity_clip(48)
        vx, vy, aw, cl, cr = extract_signals_from_umr_frames(frames)

        # Contact-only weighting
        scores_contact = compute_nonlinearity_scores(
            vx, vy, aw, cl, cr,
            weight_acc=0.0, weight_ang=0.0, weight_contact=1.0,
        )
        # Acceleration-only weighting
        scores_acc = compute_nonlinearity_scores(
            vx, vy, aw, cl, cr,
            weight_acc=1.0, weight_ang=0.0, weight_contact=0.0,
        )
        # They should differ
        assert not np.allclose(scores_contact, scores_acc)


# ===========================================================================
# Test Group 2: Adaptive Keyframe Selection
# ===========================================================================

class TestAdaptiveKeyframeSelection:
    """Test the adaptive keyframe selection algorithm."""

    def test_never_uses_static_sampling(self):
        """🚫 ANTI-PATTERN: NEVER use frame_idx % step == 0.

        Verify that keyframes are NOT uniformly spaced.
        """
        from mathart.core.motion_adaptive_keyframe_backend import (
            compute_nonlinearity_scores,
            extract_signals_from_umr_frames,
            select_adaptive_keyframes,
        )
        frames = make_high_nonlinearity_clip(48)
        vx, vy, aw, cl, cr = extract_signals_from_umr_frames(frames)
        scores = compute_nonlinearity_scores(vx, vy, aw, cl, cr)
        contact_events = np.zeros(48)
        contact_events[12] = 1.0  # Jump start
        contact_events[24] = 1.0  # Landing

        keyframes = select_adaptive_keyframes(scores, contact_events)

        # Check that gaps are NOT all equal (would indicate static sampling)
        gaps = [keyframes[i + 1] - keyframes[i] for i in range(len(keyframes) - 1)]
        assert len(set(gaps)) > 1, (
            f"All gaps are equal ({gaps}), indicating static sampling!"
        )

    def test_boundary_anchors_always_present(self):
        """First and last frame must always be keyframes."""
        from mathart.core.motion_adaptive_keyframe_backend import (
            select_adaptive_keyframes,
        )
        scores = make_random_scores(48)
        contact_events = np.zeros(48)
        keyframes = select_adaptive_keyframes(scores, contact_events)
        assert keyframes[0] == 0, "First frame not a keyframe"
        assert keyframes[-1] == 47, "Last frame not a keyframe"
        assert keyframes == [0, 2, 5, 7, 11, 13, 16, 18, 20, 22, 31, 39, 41, 44, 47]
        assert [keyframes[i + 1] - keyframes[i] for i in range(len(keyframes) - 1)] == [2, 3, 2, 4, 2, 3, 2, 2, 2, 9, 8, 2, 3, 3]

    def test_contact_events_always_captured(self):
        """🚫 ANTI-PATTERN: Contact events MUST be captured as keyframes.

        Guilty Gear Xrd discipline: hitstop/contact safe points are mandatory.
        """
        from mathart.core.motion_adaptive_keyframe_backend import (
            select_adaptive_keyframes,
        )
        scores = np.zeros(48)  # All zero scores
        contact_events = np.zeros(48)
        contact_events[10] = 1.0
        contact_events[20] = 1.0
        contact_events[35] = 1.0

        keyframes = select_adaptive_keyframes(
            scores, contact_events, min_gap=1,
        )

        for ce_frame in [10, 20, 35]:
            assert ce_frame in keyframes, (
                f"Contact event at frame {ce_frame} not captured! "
                f"Keyframes: {keyframes}"
            )

    def test_contact_absolute_override_on_min_gap_conflict(self):
        """Contact 帧必须在 min_gap 冲突中绝对存活。"""
        from mathart.core.motion_adaptive_keyframe_backend import (
            select_adaptive_keyframes,
        )

        scores = np.array([0.0, 0.2, 0.9, 0.1, 0.0], dtype=np.float64)
        contact_events = np.array([0.0, 1.0, 0.0, 0.0, 0.0], dtype=np.float64)

        keyframes = select_adaptive_keyframes(
            scores,
            contact_events,
            min_gap=2,
            max_gap=12,
            extrema_threshold=0.5,
        )

        assert 1 in keyframes, f"Contact frame 1 was dropped: {keyframes}"
        assert 2 not in keyframes, f"Non-contact peak 2 should lose to contact frame: {keyframes}"

    def test_extreme_contact_vs_peak_conflict_interception(self):
        """Extreme conflict interception: Contact frame MUST survive even when
        a much-higher-scored non-contact neighbour exists within min_gap.

        Scenario (CODE RED — Campaign 2):
            scores        = [0.0, 0.2, 0.9, 0.1, 0.0]
            contact_events= [0,   1,   0,   0,   0  ]
            min_gap = 2

        Frame 1 (score 0.2) is a Contact frame.
        Frame 2 (score 0.9) is a non-contact peak.
        Distance |2 - 1| = 1 < min_gap = 2.

        Strict assertions:
        - Index 1 MUST be in the returned keyframes (Contact absolute survival).
        - Index 2 MUST NOT be in the returned keyframes (unconditionally
          discarded because it conflicts with an immune Contact frame).
        """
        from mathart.core.motion_adaptive_keyframe_backend import (
            select_adaptive_keyframes,
        )

        scores = np.array([0.0, 0.2, 0.9, 0.1, 0.0], dtype=np.float64)
        contact_events = np.array([0.0, 1.0, 0.0, 0.0, 0.0], dtype=np.float64)

        keyframes = select_adaptive_keyframes(
            scores,
            contact_events,
            min_gap=2,
            max_gap=12,
            extrema_threshold=0.5,
        )

        # --- Strict assertion: Contact frame 1 MUST survive ---
        assert 1 in keyframes, (
            f"CODE RED VIOLATION: Contact frame at index 1 was murdered by "
            f"min_gap filter! Returned keyframes: {keyframes}"
        )
        # --- Strict assertion: Non-contact peak 2 MUST be discarded ---
        assert 2 not in keyframes, (
            f"CODE RED VIOLATION: Non-contact peak at index 2 (score=0.9) "
            f"survived despite distance conflict with Contact frame 1! "
            f"Returned keyframes: {keyframes}"
        )
        # --- Verify boundary anchors still present ---
        assert 0 in keyframes, f"Boundary anchor 0 missing: {keyframes}"
        assert 4 in keyframes, f"Boundary anchor 4 missing: {keyframes}"

    def test_multi_contact_immune_sandwich(self):
        """Multiple Contact frames sandwich a high-score non-contact peak.

        Scenario:
            scores        = [0.0, 0.3, 0.95, 0.3, 0.0]
            contact_events= [0,   1,   0,    1,   0  ]
            min_gap = 2

        Frame 1 and 3 are Contact (immune).  Frame 2 (score 0.95) is
        within min_gap of BOTH immune frames → must be discarded.
        Both Contact frames must survive despite distance = 2 >= min_gap.
        """
        from mathart.core.motion_adaptive_keyframe_backend import (
            select_adaptive_keyframes,
        )

        scores = np.array([0.0, 0.3, 0.95, 0.3, 0.0], dtype=np.float64)
        contact_events = np.array([0.0, 1.0, 0.0, 1.0, 0.0], dtype=np.float64)

        keyframes = select_adaptive_keyframes(
            scores,
            contact_events,
            min_gap=2,
            max_gap=12,
            extrema_threshold=0.5,
        )

        assert 1 in keyframes, f"Contact frame 1 dropped: {keyframes}"
        assert 3 in keyframes, f"Contact frame 3 dropped: {keyframes}"
        assert 2 not in keyframes, (
            f"Non-contact peak 2 survived sandwich between two Contact "
            f"frames: {keyframes}"
        )

    def test_max_gap_constraint_no_void(self):
        """🚫 ANTI-PATTERN: No gap between keyframes may exceed max_gap.

        This prevents the Void Trap — smooth segments must not starve.
        """
        from mathart.core.motion_adaptive_keyframe_backend import (
            select_adaptive_keyframes,
        )
        max_gap = 8
        scores = make_random_scores(60, seed=102)
        contact_events = np.zeros(60)

        keyframes = select_adaptive_keyframes(
            scores, contact_events, max_gap=max_gap, min_gap=2,
        )

        gaps = [keyframes[i + 1] - keyframes[i] for i in range(len(keyframes) - 1)]
        max_actual = max(gaps) if gaps else 0
        assert max_actual <= max_gap, (
            f"Max gap {max_actual} exceeds max_gap {max_gap}! "
            f"Gaps: {gaps}, Keyframes: {keyframes}"
        )
        assert keyframes == [0, 2, 4, 6, 8, 12, 15, 20, 22, 24, 28, 32, 36, 40, 47, 50, 54, 56, 59]
        assert gaps == [2, 2, 2, 2, 4, 3, 5, 2, 2, 4, 4, 4, 4, 7, 3, 4, 2, 3]

    def test_min_gap_constraint_no_cluster(self):
        """🚫 ANTI-PATTERN: No two keyframes may be closer than min_gap.

        This prevents the Cluster Trap — extrema packing.
        """
        from mathart.core.motion_adaptive_keyframe_backend import (
            select_adaptive_keyframes,
        )
        min_gap = 3
        scores = make_random_scores(48)
        contact_events = np.zeros(48)

        keyframes = select_adaptive_keyframes(
            scores, contact_events, min_gap=min_gap, max_gap=12,
        )

        for i in range(len(keyframes) - 1):
            gap = keyframes[i + 1] - keyframes[i]
            assert gap >= min_gap, (
                f"Gap {gap} at keyframes[{i}]={keyframes[i]} → "
                f"keyframes[{i+1}]={keyframes[i+1]} violates min_gap={min_gap}!"
            )

    def test_empty_clip(self):
        """Empty clip → empty keyframes."""
        from mathart.core.motion_adaptive_keyframe_backend import (
            select_adaptive_keyframes,
        )
        keyframes = select_adaptive_keyframes(np.array([]), np.array([]))
        assert keyframes == []

    def test_single_frame(self):
        """Single-frame clip → only frame 0."""
        from mathart.core.motion_adaptive_keyframe_backend import (
            select_adaptive_keyframes,
        )
        keyframes = select_adaptive_keyframes(np.array([0.5]), np.array([0.0]))
        assert keyframes == [0]

    def test_two_frames(self):
        """Two-frame clip → both frames."""
        from mathart.core.motion_adaptive_keyframe_backend import (
            select_adaptive_keyframes,
        )
        keyframes = select_adaptive_keyframes(
            np.array([0.5, 0.8]), np.array([0.0, 0.0]),
        )
        assert 0 in keyframes
        assert 1 in keyframes

    def test_extrema_captured(self):
        """Local maxima above threshold must be captured."""
        from mathart.core.motion_adaptive_keyframe_backend import (
            select_adaptive_keyframes,
        )
        # Create a score array with clear peaks
        scores = np.zeros(30)
        scores[10] = 0.9  # Peak
        scores[20] = 0.8  # Peak
        contact_events = np.zeros(30)

        keyframes = select_adaptive_keyframes(
            scores, contact_events, extrema_threshold=0.5,
        )
        assert 10 in keyframes, f"Peak at 10 not captured: {keyframes}"
        assert 20 in keyframes, f"Peak at 20 not captured: {keyframes}"


# ===========================================================================
# Test Group 3: SparseCtrl end_percent Mapping
# ===========================================================================

class TestEndPercentMapping:
    """Test the SparseCtrl end_percent mapping."""

    def test_high_score_gets_high_end_percent(self):
        """High-score keyframes → end_percent close to max."""
        from mathart.core.motion_adaptive_keyframe_backend import map_end_percent
        scores = np.array([0.0, 0.5, 1.0])
        keyframes = [0, 1, 2]
        eps = map_end_percent(scores, keyframes, base_end_percent=0.4, max_end_percent=1.0)
        assert eps[0] == 0.4  # Zero score → base
        assert eps[1] == pytest.approx(0.7, abs=0.01)  # Mid score
        assert eps[2] == 1.0  # Max score → max

    def test_all_same_score(self):
        """All same scores → all same end_percent."""
        from mathart.core.motion_adaptive_keyframe_backend import map_end_percent
        scores = np.array([0.5, 0.5, 0.5])
        keyframes = [0, 1, 2]
        eps = map_end_percent(scores, keyframes)
        assert len(set(eps)) == 1

    def test_end_percent_bounds(self):
        """end_percent must be in [base, max]."""
        from mathart.core.motion_adaptive_keyframe_backend import map_end_percent
        scores = make_random_scores(20, seed=104)
        keyframes = list(range(20))
        eps = map_end_percent(scores, keyframes, base_end_percent=0.3, max_end_percent=0.9)
        for ep in eps:
            assert 0.3 <= ep <= 0.9, f"end_percent {ep} out of bounds"
        np.testing.assert_allclose(
            eps,
            [0.8031, 0.7153, 0.4297, 0.3752, 0.5250, 0.7523, 0.7411, 0.5477, 0.6870, 0.7945, 0.3398, 0.6230, 0.7535, 0.7952, 0.4315, 0.5904, 0.5197, 0.4592, 0.7790, 0.6361],
            atol=1e-4,
        )
        assert np.mean(eps) == pytest.approx(0.614915, abs=1e-6)


# ===========================================================================
# Test Group 4: Full Backend E2E
# ===========================================================================

class TestMotionAdaptiveKeyframeBackendE2E:
    """End-to-end tests for the full backend pipeline."""

    def test_smooth_walk_produces_valid_manifest(self):
        """Smooth walk clip → valid ArtifactManifest with KEYFRAME_PLAN family."""
        from mathart.core.motion_adaptive_keyframe_backend import (
            MotionAdaptiveKeyframeBackend,
        )
        from mathart.core.artifact_schema import validate_artifact

        with tempfile.TemporaryDirectory(prefix="kf_test_") as tmpdir:
            backend = MotionAdaptiveKeyframeBackend()
            ctx = backend.validate_config({
                "umr_frames": make_smooth_walk_clip(48),
                "fps": 12,
                "output_dir": tmpdir,
            })
            manifest = backend.execute(ctx)

            assert manifest.artifact_family == "keyframe_plan"
            assert manifest.backend_type == "motion_adaptive_keyframe"
            assert manifest.metadata["frame_count"] == 48
            assert manifest.metadata["keyframe_count"] > 0
            errors = validate_artifact(manifest)
            assert not errors, f"Validation errors: {errors}"

    def test_high_nonlinearity_captures_all_contacts(self):
        """🚫 ANTI-PATTERN: All contact events MUST be captured."""
        from mathart.core.motion_adaptive_keyframe_backend import (
            MotionAdaptiveKeyframeBackend,
        )
        import json

        with tempfile.TemporaryDirectory(prefix="kf_test_") as tmpdir:
            backend = MotionAdaptiveKeyframeBackend()
            ctx = backend.validate_config({
                "umr_frames": make_high_nonlinearity_clip(48),
                "fps": 12,
                "output_dir": tmpdir,
            })
            manifest = backend.execute(ctx)

            # Read the plan
            plan_path = Path(tmpdir) / "keyframe_plan.json"
            plan = json.loads(plan_path.read_text())

            contact_frames = plan["contact_event_frames"]
            keyframes = plan["keyframe_indices"]

            # Every contact event frame must be in keyframes
            for cf in contact_frames:
                assert cf in keyframes, (
                    f"Contact event at frame {cf} NOT captured! "
                    f"Keyframes: {keyframes}"
                )

    def test_no_gap_exceeds_max_gap(self):
        """🚫 ANTI-PATTERN: No gap may exceed max_gap."""
        from mathart.core.motion_adaptive_keyframe_backend import (
            MotionAdaptiveKeyframeBackend,
        )
        import json

        with tempfile.TemporaryDirectory(prefix="kf_test_") as tmpdir:
            backend = MotionAdaptiveKeyframeBackend()
            ctx = backend.validate_config({
                "umr_frames": make_smooth_walk_clip(60),
                "fps": 12,
                "output_dir": tmpdir,
                "config_overrides": {"max_gap": 10},
            })
            manifest = backend.execute(ctx)

            plan_path = Path(tmpdir) / "keyframe_plan.json"
            plan = json.loads(plan_path.read_text())
            assert plan["max_actual_gap"] <= 10, (
                f"Max actual gap {plan['max_actual_gap']} exceeds max_gap=10"
            )

    def test_manifest_has_required_metadata(self):
        """ArtifactManifest must have all required metadata keys."""
        from mathart.core.motion_adaptive_keyframe_backend import (
            MotionAdaptiveKeyframeBackend,
        )
        from mathart.core.artifact_schema import ArtifactFamily

        required = ArtifactFamily.required_metadata_keys("keyframe_plan")

        with tempfile.TemporaryDirectory(prefix="kf_test_") as tmpdir:
            backend = MotionAdaptiveKeyframeBackend()
            ctx = backend.validate_config({
                "umr_frames": make_smooth_walk_clip(24),
                "fps": 12,
                "output_dir": tmpdir,
            })
            manifest = backend.execute(ctx)

            for key in required:
                assert key in manifest.metadata, (
                    f"Required metadata key '{key}' missing from manifest"
                )

    def test_empty_frames_raises(self):
        """Empty UMR frames → ValueError."""
        from mathart.core.motion_adaptive_keyframe_backend import (
            MotionAdaptiveKeyframeBackend,
        )
        backend = MotionAdaptiveKeyframeBackend()
        ctx = backend.validate_config({
            "umr_frames": [],
            "output_dir": "/tmp",
        })
        with pytest.raises(ValueError, match="No UMR frames"):
            backend.execute(ctx)


# ===========================================================================
# Test Group 5: Orchestrator Hot-Reload Coordination
# ===========================================================================

class TestOrchestratorHotReloadCoordination:
    """Test the Orchestrator's hot-reload cache invalidation."""

    def test_stale_cache_purged_on_reload(self):
        """🚫 ANTI-PATTERN: Stale Cache Leak Trap.

        After reload, old KEYFRAME_PLAN must be completely purged.
        """
        from mathart.core.microkernel_orchestrator import MicrokernelOrchestrator

        with tempfile.TemporaryDirectory(prefix="orch_test_") as tmpdir:
            orch = MicrokernelOrchestrator(project_root=tmpdir)

            # Cache a result
            old_plan = {"keyframe_indices": [0, 10, 20], "version": "old"}
            orch.cache_result("motion_adaptive_keyframe", old_plan)
            assert orch.get_cached_result("motion_adaptive_keyframe") is old_plan

            # Set iteration counter
            for _ in range(5):
                orch.increment_iteration("motion_adaptive_keyframe")
            assert orch.get_iteration_count("motion_adaptive_keyframe") == 5

            # Simulate reload
            orch.on_backend_reload("motion_adaptive_keyframe")

            # Assert cache is purged
            assert orch.get_cached_result("motion_adaptive_keyframe") is None, (
                "Stale cache NOT purged after reload!"
            )
            # Assert iteration counter is reset
            assert orch.get_iteration_count("motion_adaptive_keyframe") == 0, (
                "Iteration counter NOT reset after reload!"
            )

    def test_targeted_invalidation_not_global_clear(self):
        """Reload of one backend must NOT clear other backends' caches."""
        from mathart.core.microkernel_orchestrator import MicrokernelOrchestrator

        with tempfile.TemporaryDirectory(prefix="orch_test_") as tmpdir:
            orch = MicrokernelOrchestrator(project_root=tmpdir)

            orch.cache_result("backend_a", {"data": "a"})
            orch.cache_result("backend_b", {"data": "b"})

            orch.on_backend_reload("backend_a")

            assert orch.get_cached_result("backend_a") is None
            assert orch.get_cached_result("backend_b") is not None, (
                "Global clear detected! backend_b cache was destroyed!"
            )

    def test_reload_callback_chain(self):
        """Reload callbacks must be invoked with correct backend_name."""
        from mathart.core.microkernel_orchestrator import MicrokernelOrchestrator

        with tempfile.TemporaryDirectory(prefix="orch_test_") as tmpdir:
            orch = MicrokernelOrchestrator(project_root=tmpdir)

            callback_log: list[str] = []
            orch.register_reload_callback(
                "test_cb", lambda name: callback_log.append(name)
            )

            orch.on_backend_reload("motion_adaptive_keyframe")
            assert callback_log == ["motion_adaptive_keyframe"]

    def test_safe_point_lock_accessible(self):
        """Orchestrator exposes SafePointExecutionLock."""
        from mathart.core.microkernel_orchestrator import MicrokernelOrchestrator
        from mathart.core.safe_point_execution import SafePointExecutionLock

        with tempfile.TemporaryDirectory(prefix="orch_test_") as tmpdir:
            orch = MicrokernelOrchestrator(project_root=tmpdir)
            assert isinstance(orch.safe_point_lock, SafePointExecutionLock)


# ===========================================================================
# Test Group 6: ComfyUI Client Reload Resilience
# ===========================================================================

class TestComfyUIClientReloadResilience:
    """Test the ComfyUI Client's workflow cache invalidation."""

    def test_workflow_cache_purged_on_reload(self):
        """🚫 ANTI-PATTERN: Stale workflow cache must be purged."""
        from mathart.comfy_client.comfyui_ws_client import ComfyUIClient

        client = ComfyUIClient()
        client.cache_workflow("motion_adaptive_keyframe_plan_v1", {"prompt": {}})
        assert client.get_cached_workflow("motion_adaptive_keyframe_plan_v1") is not None

        client.on_backend_reload("motion_adaptive_keyframe")
        assert client.get_cached_workflow("motion_adaptive_keyframe_plan_v1") is None, (
            "Stale workflow cache NOT purged after backend reload!"
        )

    def test_unrelated_cache_preserved(self):
        """Reload of one backend must NOT purge unrelated caches."""
        from mathart.comfy_client.comfyui_ws_client import ComfyUIClient

        client = ComfyUIClient()
        client.cache_workflow("anti_flicker_workflow", {"prompt": {}})
        client.cache_workflow("motion_adaptive_keyframe_plan", {"prompt": {}})

        client.on_backend_reload("motion_adaptive_keyframe")

        assert client.get_cached_workflow("anti_flicker_workflow") is not None, (
            "Unrelated cache was destroyed!"
        )
        assert client.get_cached_workflow("motion_adaptive_keyframe_plan") is None

    def test_safe_point_lock_injection(self):
        """Client accepts SafePointExecutionLock injection."""
        from mathart.comfy_client.comfyui_ws_client import ComfyUIClient
        from mathart.core.safe_point_execution import SafePointExecutionLock

        client = ComfyUIClient()
        lock = SafePointExecutionLock()
        client.set_safe_point_lock(lock)
        assert client._safe_point_lock is lock


# ===========================================================================
# Test Group 7: SafePointExecutionLock
# ===========================================================================

class TestSafePointExecutionLock:
    """Test the frame-boundary safe-point lock."""

    def test_execution_fence_basic(self):
        """execution_fence increments/decrements counter."""
        from mathart.core.safe_point_execution import SafePointExecutionLock

        lock = SafePointExecutionLock()
        assert not lock.is_executing("test")

        with lock.execution_fence("test"):
            assert lock.is_executing("test")

        assert not lock.is_executing("test")

    def test_reload_gate_basic(self):
        """reload_gate sets/clears reloading flag."""
        from mathart.core.safe_point_execution import SafePointExecutionLock

        lock = SafePointExecutionLock()
        assert not lock.is_reloading("test")

        with lock.reload_gate("test"):
            assert lock.is_reloading("test")

        assert not lock.is_reloading("test")

    def test_mid_frame_reload_blocked(self):
        """🚫 ANTI-PATTERN: Mid-Frame Reload Trap.

        Manual reload must wait for the current frame-sized execution section
        to complete before entering.
        """
        from mathart.core.safe_point_execution import SafePointExecutionLock

        lock = SafePointExecutionLock(reload_timeout=5.0)
        execution_started = threading.Event()
        reload_entered = threading.Event()
        execution_done = threading.Event()

        errors: list[str] = []

        def executor():
            with lock.frame_execution("test_backend"):
                execution_started.set()
                time.sleep(0.5)
            execution_done.set()

        def reloader():
            execution_started.wait(timeout=2.0)
            time.sleep(0.1)
            with lock.reload_gate("test_backend"):
                reload_entered.set()
                if not execution_done.is_set():
                    errors.append("Reload entered while a frame was still executing!")

        t1 = threading.Thread(target=executor, daemon=True)
        t2 = threading.Thread(target=reloader, daemon=True)
        t1.start()
        t2.start()
        t1.join(timeout=5.0)
        t2.join(timeout=5.0)

        assert not errors, f"Mid-Frame Reload Trap: {errors}"
        assert execution_done.is_set()
        assert reload_entered.is_set()

    def test_watcher_reload_occurs_at_frame_boundary(self):
        """Watcher 请求必须在两帧间隙由主渲染线程消费。"""
        from mathart.core.backend_file_watcher import BackendFileWatcher
        from mathart.core.backend_registry import BackendRegistry
        from mathart.core.safe_point_execution import SafePointExecutionLock

        BackendRegistry.reset()
        BackendRegistry._builtins_loaded = False
        BackendRegistry._backend_module_map = {}

        with tempfile.TemporaryDirectory(prefix="safe_point_watch_") as tmpdir:
            tmp_path = Path(tmpdir)
            pkg_dir = tmp_path / "dynamic_backends"
            pkg_dir.mkdir()
            (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
            backend_file = pkg_dir / "dummy_hot_backend.py"
            backend_file.write_text(
                """
from mathart.core.backend_registry import BackendMeta, register_backend

@register_backend(
    \"dummy_hot_backend\",
    display_name=\"Dummy Hot Backend v1\",
    version=\"1.0.0\",
    artifact_families=(\"test_hot_output\",),
    session_origin=\"SESSION-092\",
)
class DummyHotBackend:
    HOT_RELOAD_VERSION = \"v1_result\"

    @property
    def meta(self) -> BackendMeta:
        return self._backend_meta

    def execute(self, context: dict) -> dict:
        return {\"status\": \"ok\", \"version\": \"v1_result\"}
""".strip(),
                encoding="utf-8",
            )

            sys.path.insert(0, str(tmp_path))
            try:
                importlib.import_module("dynamic_backends.dummy_hot_backend")
                reg = BackendRegistry()
                _, v1_class = reg.get_or_raise("dummy_hot_backend")
                assert v1_class.HOT_RELOAD_VERSION == "v1_result"

                lock = SafePointExecutionLock(reload_timeout=5.0)
                try:
                    watcher = BackendFileWatcher(
                        reg,
                        extra_watch_paths=[str(pkg_dir)],
                        debounce_seconds=0.2,
                        safe_point_lock=lock,
                    )
                except ImportError as exc:
                    pytest.skip(str(exc))
                watcher.start()

                frame_log: list[tuple[str, int, float]] = []
                reload_records: list[dict[str, Any]] = []
                frame_one_started = threading.Event()

                def render_loop() -> None:
                    for frame_idx in range(4):
                        with lock.frame_execution("dummy_hot_backend"):
                            frame_log.append(("start", frame_idx, time.time()))
                            if frame_idx == 1:
                                frame_one_started.set()
                            time.sleep(0.20)
                            frame_log.append(("end", frame_idx, time.time()))
                        reload_records.extend(
                            watcher.process_pending_reloads(
                                backend_name="dummy_hot_backend",
                                frame_index=frame_idx,
                            )
                        )
                        time.sleep(0.05)

                render_thread = threading.Thread(target=render_loop, daemon=True)
                render_thread.start()

                assert frame_one_started.wait(timeout=3.0), "Render loop never reached frame 1"
                backend_file.write_text(
                    """
from mathart.core.backend_registry import BackendMeta, register_backend

@register_backend(
    \"dummy_hot_backend\",
    display_name=\"Dummy Hot Backend v2\",
    version=\"2.0.0\",
    artifact_families=(\"test_hot_output\",),
    session_origin=\"SESSION-092\",
)
class DummyHotBackend:
    HOT_RELOAD_VERSION = \"v2_result\"

    @property
    def meta(self) -> BackendMeta:
        return self._backend_meta

    def execute(self, context: dict) -> dict:
        return {\"status\": \"ok\", \"version\": \"v2_result\"}
""".strip(),
                    encoding="utf-8",
                )

                assert watcher.reload_requested_event.wait(timeout=5.0), (
                    "Watcher never queued a reload request"
                )

                _, still_v1_class = reg.get_or_raise("dummy_hot_backend")
                assert still_v1_class.HOT_RELOAD_VERSION == "v1_result", (
                    "Backend reloaded before frame-boundary handoff"
                )

                render_thread.join(timeout=8.0)
                assert not render_thread.is_alive(), "Render loop deadlocked"
                assert reload_records, "No reload was consumed at any frame boundary"

                reload_record = reload_records[0]
                assert reload_record["success"] is True
                boundary_frame = int(reload_record["frame_boundary_after"])
                assert boundary_frame >= 1

                frame_end = next(t for tag, idx, t in frame_log if tag == "end" and idx == boundary_frame)
                frame_next_start = next(
                    t for tag, idx, t in frame_log if tag == "start" and idx == boundary_frame + 1
                )
                assert frame_end <= reload_record["timestamp"] <= frame_next_start, (
                    f"Reload did not occur inside frame boundary gap: {reload_record}, frame_log={frame_log}"
                )

                _, v2_class = reg.get_or_raise("dummy_hot_backend")
                assert v2_class.HOT_RELOAD_VERSION == "v2_result"
            finally:
                if 'watcher' in locals():
                    watcher.stop()
                for mod_name in [m for m in list(sys.modules) if m.startswith("dynamic_backends")]:
                    del sys.modules[mod_name]
                if str(tmp_path) in sys.path:
                    sys.path.remove(str(tmp_path))

                # SESSION-098 (HIGH-2.6): Use the canonical restore helper
                # instead of hand-rolled partial reload that misses modules.
                from tests.conftest import restore_builtin_backends
                restore_builtin_backends()
                # Also reload the motion adaptive keyframe backend
                import mathart.core.motion_adaptive_keyframe_backend as motion_backend_module
                importlib.reload(motion_backend_module)

    def test_concurrent_executions_allowed(self):
        """Multiple concurrent executions of the same backend are OK."""
        from mathart.core.safe_point_execution import SafePointExecutionLock

        lock = SafePointExecutionLock()
        count = threading.atomic = 0  # Use a list for thread-safe counter
        counter = [0]
        counter_lock = threading.Lock()

        def executor():
            with lock.execution_fence("test"):
                with counter_lock:
                    counter[0] += 1
                time.sleep(0.1)
                with counter_lock:
                    counter[0] -= 1

        threads = [threading.Thread(target=executor, daemon=True) for _ in range(5)]
        for t in threads:
            t.start()
        time.sleep(0.05)

        # Multiple should be executing simultaneously
        assert lock.is_executing("test")

        for t in threads:
            t.join(timeout=5.0)

    def test_different_backends_independent(self):
        """Different backends can execute and reload independently."""
        from mathart.core.safe_point_execution import SafePointExecutionLock

        lock = SafePointExecutionLock()

        with lock.execution_fence("backend_a"):
            # Should NOT block reload of backend_b
            with lock.reload_gate("backend_b"):
                assert lock.is_executing("backend_a")
                assert lock.is_reloading("backend_b")

    def test_status_snapshot(self):
        """status() returns correct snapshot."""
        from mathart.core.safe_point_execution import SafePointExecutionLock

        lock = SafePointExecutionLock()
        with lock.frame_execution("test"):
            status = lock.status()
            assert status["test"]["active_frames"] == 1
            assert status["test"]["reloading"] is False
            assert status["test"]["reload_requested"] is False

    def test_singleton_access(self):
        """get_safe_point_lock returns consistent singleton."""
        from mathart.core.safe_point_execution import get_safe_point_lock

        lock1 = get_safe_point_lock()
        lock2 = get_safe_point_lock()
        assert lock1 is lock2


# ===========================================================================
# Test Group 8: Three-Layer Evolution Bridge
# ===========================================================================

class TestKeyframeEvolutionBridge:
    """Test the three-layer evolution bridge."""

    def test_evaluate_good_plan(self):
        """Good plan → high fitness scores."""
        from mathart.core.motion_adaptive_keyframe_backend import (
            KeyframeEvolutionBridge,
            KeyframePlannerConfig,
        )

        with tempfile.TemporaryDirectory(prefix="evo_test_") as tmpdir:
            bridge = KeyframeEvolutionBridge(project_root=tmpdir)
            config = KeyframePlannerConfig()

            plan = {
                "frame_count": 48,
                "keyframe_indices": [0, 6, 12, 18, 24, 30, 36, 42, 47],
                "nonlinearity_scores": [0.5] * 48,
                "contact_events_captured": 3,
                "contact_event_frames": [6, 18, 30],
            }

            fitness = bridge.evaluate(plan, config)
            assert fitness["coverage"] > 0.0
            assert fitness["no_void"] == 1.0
            assert fitness["no_cluster"] == 1.0
            assert fitness["contact_capture"] == 1.0

    def test_distill_persists_rules(self):
        """High-fitness config → knowledge rules persisted to disk."""
        from mathart.core.motion_adaptive_keyframe_backend import (
            KeyframeEvolutionBridge,
            KeyframePlannerConfig,
        )
        import json

        with tempfile.TemporaryDirectory(prefix="evo_test_") as tmpdir:
            bridge = KeyframeEvolutionBridge(project_root=tmpdir)
            config = KeyframePlannerConfig()

            fitness = {
                "coverage": 0.9,
                "no_void": 1.0,
                "no_cluster": 1.0,
                "contact_capture": 1.0,
                "temporal_coherence": 0.8,
            }

            rules = bridge.distill(config, fitness)
            assert len(rules) > 0

            # Check persistence
            assert bridge.knowledge_path.exists()
            stored = json.loads(bridge.knowledge_path.read_text())
            assert len(stored) > 0
            assert stored[0]["type"] == "keyframe_planner_config"


# ===========================================================================
# Test Group 9: Configuration Validation
# ===========================================================================

class TestKeyframePlannerConfig:
    """Test configuration validation."""

    def test_valid_config(self):
        """Default config should be valid."""
        from mathart.core.motion_adaptive_keyframe_backend import KeyframePlannerConfig
        config = KeyframePlannerConfig()
        errors = config.validate()
        assert not errors

    def test_zero_weights_rejected(self):
        """All-zero weights should be rejected."""
        from mathart.core.motion_adaptive_keyframe_backend import KeyframePlannerConfig
        config = KeyframePlannerConfig(
            weight_acceleration=0.0,
            weight_angular_acceleration=0.0,
            weight_contact_event=0.0,
        )
        errors = config.validate()
        assert any("zero" in e.lower() for e in errors)

    def test_invalid_min_gap(self):
        """min_gap < 1 should be rejected."""
        from mathart.core.motion_adaptive_keyframe_backend import KeyframePlannerConfig
        config = KeyframePlannerConfig(min_gap=0)
        errors = config.validate()
        assert any("min_gap" in e for e in errors)

    def test_max_gap_less_than_min_gap(self):
        """max_gap < min_gap should be rejected."""
        from mathart.core.motion_adaptive_keyframe_backend import KeyframePlannerConfig
        config = KeyframePlannerConfig(min_gap=10, max_gap=5)
        errors = config.validate()
        assert any("max_gap" in e for e in errors)

    def test_invalid_end_percent(self):
        """end_percent out of [0, 1] should be rejected."""
        from mathart.core.motion_adaptive_keyframe_backend import KeyframePlannerConfig
        config = KeyframePlannerConfig(base_end_percent=1.5)
        errors = config.validate()
        assert any("base_end_percent" in e for e in errors)


# ===========================================================================
# Test Group 10: Backend Registration Verification
# ===========================================================================

class TestBackendRegistration:
    """Verify the backend is properly registered in the global registry."""

    def test_registered_in_global_registry(self):
        """Backend must be discoverable via get_registry()."""
        from mathart.core.motion_adaptive_keyframe_backend import (
            MotionAdaptiveKeyframeBackend,
        )
        from mathart.core.backend_registry import get_registry

        reg = get_registry()
        entry = reg.get("motion_adaptive_keyframe")
        assert entry is not None, "Backend not found in registry!"
        meta, cls = entry
        assert cls is MotionAdaptiveKeyframeBackend

    def test_artifact_family_correct(self):
        """Backend must declare KEYFRAME_PLAN artifact family."""
        from mathart.core.motion_adaptive_keyframe_backend import (
            MotionAdaptiveKeyframeBackend,
        )
        from mathart.core.backend_registry import get_registry

        reg = get_registry()
        meta, _ = reg.get("motion_adaptive_keyframe")
        assert "keyframe_plan" in meta.artifact_families

    def test_backend_type_enum_exists(self):
        """BackendType.MOTION_ADAPTIVE_KEYFRAME must exist."""
        from mathart.core.backend_types import BackendType
        assert hasattr(BackendType, "MOTION_ADAPTIVE_KEYFRAME")
        assert BackendType.MOTION_ADAPTIVE_KEYFRAME.value == "motion_adaptive_keyframe"

    def test_artifact_family_enum_exists(self):
        """ArtifactFamily.KEYFRAME_PLAN must exist."""
        from mathart.core.artifact_schema import ArtifactFamily
        assert hasattr(ArtifactFamily, "KEYFRAME_PLAN")
        assert ArtifactFamily.KEYFRAME_PLAN.value == "keyframe_plan"

    def test_aliases_resolve(self):
        """Backend aliases must resolve correctly."""
        from mathart.core.backend_types import backend_type_value
        assert backend_type_value("adaptive_keyframe") == "motion_adaptive_keyframe"
        assert backend_type_value("keyframe_planner") == "motion_adaptive_keyframe"
        assert backend_type_value("motion_keyframe") == "motion_adaptive_keyframe"


# ===========================================================================
# Test Group 11: Signal Extraction
# ===========================================================================

class TestSignalExtraction:
    """Test UMR frame → NumPy signal extraction."""

    def test_extract_from_valid_frames(self):
        """Valid UMR frames → correct signal arrays."""
        from mathart.core.motion_adaptive_keyframe_backend import (
            extract_signals_from_umr_frames,
        )
        frames = make_smooth_walk_clip(12)
        vx, vy, aw, cl, cr = extract_signals_from_umr_frames(frames)
        assert vx.shape == (12,)
        assert vy.shape == (12,)
        assert aw.shape == (12,)
        assert cl.shape == (12,)
        assert cr.shape == (12,)
        # First frame should have left_foot=True (i=0, 0%12<6)
        assert cl[0] == 1.0
        assert cr[0] == 0.0

    def test_extract_from_empty_frames(self):
        """Empty frame list → empty arrays."""
        from mathart.core.motion_adaptive_keyframe_backend import (
            extract_signals_from_umr_frames,
        )
        vx, vy, aw, cl, cr = extract_signals_from_umr_frames([])
        assert vx.shape == (0,)

    def test_extract_from_minimal_frames(self):
        """Frames with missing fields → safe defaults (zero)."""
        from mathart.core.motion_adaptive_keyframe_backend import (
            extract_signals_from_umr_frames,
        )
        frames = [{"time": 0.0}, {"time": 0.1}]
        vx, vy, aw, cl, cr = extract_signals_from_umr_frames(frames)
        assert np.allclose(vx, 0.0)
        assert np.allclose(vy, 0.0)
