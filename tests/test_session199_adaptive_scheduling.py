"""SESSION-199 — Adaptive Variance-Based ControlNet Strength Scheduler Tests.

Tests for the ``compute_adaptive_controlnet_strength`` function introduced
in SESSION-199 to implement PID-inspired adaptive gain scheduling for
ControlNet injection strength based on pixel-level variance.

Design Rationale
----------------
The adaptive scheduler addresses the "反臆想模型注入红线" by dynamically
scaling ControlNet strength proportionally to the normalised pixel variance
of the conditioning frame, then clamping to the safe operating window
``[min_strength, max_strength]``.

Formula::

    normalised = pixel_variance / 255.0
    raw = base_strength + variance_scale * normalised
    strength = clamp(raw, min_strength, max_strength)

References
----------
- SESSION-199 safe model mapping research notes
- PID adaptive gain scheduling (Åström & Hägglund 1995)
- Woodham 1980 photometric stereo (fluid→normalbae mapping rationale)
"""
from __future__ import annotations

import pytest

from mathart.core.vfx_topology_hydrator import compute_adaptive_controlnet_strength


class TestComputeAdaptiveControlnetStrength:
    """Unit tests for compute_adaptive_controlnet_strength."""

    # ── Boundary: zero variance ─────────────────────────────────────────

    def test_zero_variance_returns_base_strength(self) -> None:
        """Flat frame (zero variance) must return exactly the base_strength."""
        result = compute_adaptive_controlnet_strength(0.0)
        assert result == pytest.approx(0.35, abs=1e-9)

    def test_zero_variance_custom_base(self) -> None:
        """Custom base_strength must be respected when variance is zero."""
        result = compute_adaptive_controlnet_strength(0.0, base_strength=0.20)
        assert result == pytest.approx(0.20, abs=1e-9)

    # ── Boundary: maximum variance ──────────────────────────────────────

    def test_max_variance_clamped_to_max_strength(self) -> None:
        """Maximum uint8 variance (255²=65025) must be clamped to max_strength=0.90."""
        result = compute_adaptive_controlnet_strength(65025.0)
        # raw = 0.35 + 0.5 * (65025 / 255) = 0.35 + 0.5 * 255 = 0.35 + 127.5 = 127.85
        # clamped to 0.90
        assert result == pytest.approx(0.90, abs=1e-9)

    def test_max_variance_custom_max_strength(self) -> None:
        """Custom max_strength ceiling must be respected."""
        result = compute_adaptive_controlnet_strength(65025.0, max_strength=0.75)
        assert result == pytest.approx(0.75, abs=1e-9)

    # ── Boundary: minimum clamp ─────────────────────────────────────────

    def test_negative_base_clamped_to_min_strength(self) -> None:
        """Negative base_strength must be clamped to min_strength floor."""
        result = compute_adaptive_controlnet_strength(0.0, base_strength=-1.0)
        assert result == pytest.approx(0.10, abs=1e-9)

    def test_custom_min_strength_floor(self) -> None:
        """Custom min_strength floor must be respected."""
        result = compute_adaptive_controlnet_strength(0.0, base_strength=0.0, min_strength=0.05)
        assert result == pytest.approx(0.05, abs=1e-9)

    # ── Mid-range values ────────────────────────────────────────────────

    def test_10_percent_variance(self) -> None:
        """10% of max variance should yield base + 0.5 * 25.5 = 0.35 + 12.75 = 13.1 → clamped 0.90."""
        # 10% of 65025 = 6502.5
        # normalised = 6502.5 / 255 = 25.5
        # raw = 0.35 + 0.5 * 25.5 = 0.35 + 12.75 = 13.10 → clamped to 0.90
        result = compute_adaptive_controlnet_strength(6502.5)
        assert result == pytest.approx(0.90, abs=1e-9)

    def test_small_variance_no_clamp(self) -> None:
        """Small variance that stays within bounds should not be clamped."""
        # variance = 1.0, normalised = 1/255 ≈ 0.003922
        # raw = 0.35 + 0.5 * 0.003922 ≈ 0.351961
        result = compute_adaptive_controlnet_strength(1.0)
        expected = 0.35 + 0.5 * (1.0 / 255.0)
        assert result == pytest.approx(expected, abs=1e-6)

    def test_variance_scale_zero(self) -> None:
        """variance_scale=0 must always return base_strength (no scaling)."""
        for var in [0.0, 100.0, 65025.0]:
            result = compute_adaptive_controlnet_strength(var, variance_scale=0.0)
            assert result == pytest.approx(0.35, abs=1e-9), (
                f"variance={var}: expected 0.35, got {result}"
            )

    def test_custom_variance_scale(self) -> None:
        """Custom variance_scale must be applied correctly."""
        # variance = 255.0, normalised = 1.0
        # raw = 0.35 + 0.1 * 1.0 = 0.45
        result = compute_adaptive_controlnet_strength(255.0, variance_scale=0.1)
        assert result == pytest.approx(0.45, abs=1e-6)

    # ── Return type ─────────────────────────────────────────────────────

    def test_return_type_is_float(self) -> None:
        """Return type must be float, not int or other numeric type."""
        result = compute_adaptive_controlnet_strength(0.0)
        assert isinstance(result, float)

    def test_return_type_with_integer_variance(self) -> None:
        """Integer input must still produce float output."""
        result = compute_adaptive_controlnet_strength(100)
        assert isinstance(result, float)

    # ── Clamp invariant ─────────────────────────────────────────────────

    def test_result_always_within_bounds(self) -> None:
        """Result must always be within [min_strength, max_strength] for any input."""
        test_cases = [
            (0.0, 0.35, 0.5, 0.10, 0.90),
            (65025.0, 0.35, 0.5, 0.10, 0.90),
            (-1000.0, 0.35, 0.5, 0.10, 0.90),
            (1e9, 0.35, 0.5, 0.10, 0.90),
            (0.0, 0.0, 0.0, 0.20, 0.80),
        ]
        for var, base, scale, lo, hi in test_cases:
            result = compute_adaptive_controlnet_strength(
                var, base_strength=base, variance_scale=scale,
                min_strength=lo, max_strength=hi,
            )
            assert lo <= result <= hi, (
                f"variance={var}: result {result} outside [{lo}, {hi}]"
            )

    # ── SESSION-199 model mapping regression guard ──────────────────────

    def test_fluid_model_default_is_normalbae(self) -> None:
        """SESSION-199: FLUID_CONTROLNET_MODEL_DEFAULT must be normalbae (not depth)."""
        from mathart.core.vfx_topology_hydrator import FLUID_CONTROLNET_MODEL_DEFAULT
        assert FLUID_CONTROLNET_MODEL_DEFAULT == "control_v11p_sd15_normalbae.pth", (
            "SESSION-199 safe model mapping: fluid flowmap must use normalbae model "
            "(photometric stereo: fluid momentum ≈ surface normal perturbation). "
            f"Got: {FLUID_CONTROLNET_MODEL_DEFAULT}"
        )

    def test_physics_model_default_is_depth(self) -> None:
        """SESSION-199: PHYSICS_CONTROLNET_MODEL_DEFAULT must be depth (not normalbae)."""
        from mathart.core.vfx_topology_hydrator import PHYSICS_CONTROLNET_MODEL_DEFAULT
        assert PHYSICS_CONTROLNET_MODEL_DEFAULT == "control_v11f1p_sd15_depth.pth", (
            "SESSION-199 safe model mapping: physics 3D must use depth model "
            "(Z-axis deformation ≈ depth map gradient). "
            f"Got: {PHYSICS_CONTROLNET_MODEL_DEFAULT}"
        )

    def test_model_defaults_are_not_swapped(self) -> None:
        """SESSION-199: fluid and physics model defaults must not be identical."""
        from mathart.core.vfx_topology_hydrator import (
            FLUID_CONTROLNET_MODEL_DEFAULT,
            PHYSICS_CONTROLNET_MODEL_DEFAULT,
        )
        assert FLUID_CONTROLNET_MODEL_DEFAULT != PHYSICS_CONTROLNET_MODEL_DEFAULT, (
            "FLUID and PHYSICS ControlNet model defaults must differ. "
            "Both are currently set to the same model, which indicates "
            "the SESSION-199 safe model mapping was not applied correctly."
        )
